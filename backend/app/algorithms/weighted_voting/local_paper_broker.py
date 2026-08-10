"""Weighted Voting local paper broker and account/risk adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from backend.app.algorithms.weighted_voting.global_interface import (
    WeightedVotingGlobalRiskRequest,
    WeightedVotingGlobalRiskResponse,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import (
    WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
    WeightedVotingInventoryEventType,
    WeightedVotingInventoryRepository,
    WeightedVotingInventorySnapshot,
)
from backend.app.algorithms.weighted_voting.local_paper_logging import record_weighted_voting_local_paper_lifecycle_event
from backend.app.algorithms.weighted_voting.runtime_context import (
    WeightedVotingGlobalRiskState,
    WeightedVotingReadOnlyAccountObservation,
)
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill
from backend.app.risk.types import AccountSnapshot, PendingOrder, PortfolioPosition, PortfolioSnapshot


WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION = "weighted_voting_local_paper_broker_v1"
WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION = "weighted_voting_local_paper_fill_engine_v1"
WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE = "weighted_voting.local_paper"
SUPPORTED_WEIGHTED_VOTING_LOCAL_ORDER_TYPES = frozenset({"LIMIT", "STOP", "STOP_LIMIT"})
WEIGHTED_VOTING_LOCAL_ORDER_LIFECYCLE_STATUSES = (
    "PENDING",
    "ACCEPTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "REPLACED",
)


@dataclass(frozen=True)
class WeightedVotingLocalPaperAccount:
    algorithm_id: str
    capital_partition_id: str
    cash: float
    reserved_cash: float
    buying_power: float
    realised_pnl: float
    unrealised_pnl: float
    equity: float
    gross_exposure: float
    net_exposure: float
    daily_loss: float
    daily_trade_count: int
    snapshot_version: int
    observed_at: datetime

    @classmethod
    def from_inventory(cls, snapshot: WeightedVotingInventorySnapshot, *, observed_at: datetime) -> "WeightedVotingLocalPaperAccount":
        return cls(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            capital_partition_id=snapshot.capital_partition_id,
            cash=snapshot.cash,
            reserved_cash=snapshot.reserved_buying_power,
            buying_power=snapshot.remaining_capital_partition,
            realised_pnl=snapshot.realised_pnl,
            unrealised_pnl=snapshot.unrealised_pnl,
            equity=snapshot.equity,
            gross_exposure=snapshot.gross_exposure,
            net_exposure=snapshot.net_exposure,
            daily_loss=snapshot.daily_loss,
            daily_trade_count=snapshot.daily_trade_count,
            snapshot_version=snapshot.snapshot_version,
            observed_at=observed_at,
        )


class WeightedVotingLocalPaperBroker:
    """In-process paper broker backed only by Weighted Voting local inventory."""

    broker_kind = "weighted_voting_local_paper"
    paper_endpoint = True
    configured = True
    live_trading_enabled = False
    base_url = "local-paper://weighted_voting"

    def __init__(self, store: Any, inventory_repository: WeightedVotingInventoryRepository, market_data_provider: Any | None = None) -> None:
        self.store = store
        self.inventory_repository = inventory_repository
        self.market_data_provider = market_data_provider

    def verify_paper_endpoint(self) -> bool:
        return True

    def verify_paper_account(self) -> bool:
        snapshot = self.inventory_repository.current_snapshot(now=datetime.now(UTC))
        return snapshot.algorithm_id == WEIGHTED_VOTING_ALGORITHM_ID and snapshot.capital_partition_id.startswith("weighted_voting.")

    def account_observation(self, *, as_of: datetime) -> WeightedVotingReadOnlyAccountObservation:
        account = self.local_account(as_of=as_of)
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=account.equity,
            broker_buying_power=account.buying_power,
            observed_at=as_of,
            source_id="weighted_voting.local_paper.inventory_account",
            available=account.equity > 0,
            reason_codes=("weighted_voting.local_paper.account_from_dedicated_inventory",),
        )

    def local_account(self, *, as_of: datetime) -> WeightedVotingLocalPaperAccount:
        snapshot = self.inventory_repository.current_snapshot(now=as_of)
        return WeightedVotingLocalPaperAccount.from_inventory(snapshot, observed_at=as_of)

    def reset_local_paper_account(
        self,
        *,
        initial_capital: float | None = None,
        reset_at: datetime | None = None,
        reason: str = "weighted_voting.local_paper.reset_requested",
    ) -> dict[str, Any]:
        occurred_at = _utc(reset_at) or datetime.now(UTC)
        before = self.inventory_repository.current_snapshot(now=occurred_at)
        capital = float(initial_capital if initial_capital is not None else before.initial_capital or before.allocated_capital)
        archived = _archive_weighted_voting_local_paper_records(self.store, reset_at=occurred_at, reason=reason)
        after = self.inventory_repository.reset_local_paper_account(
            initial_capital=capital,
            occurred_at=occurred_at,
            expected_snapshot_version=before.snapshot_version,
            reason=reason,
            event_id=f"weighted-voting-local-paper-reset-{before.snapshot_version}-{occurred_at.isoformat()}",
        )
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "executionMode": "LOCAL_PAPER",
            "brokerKind": self.broker_kind,
            "resetAt": occurred_at.isoformat(),
            "reason": reason,
            "previousInventorySnapshotVersion": before.snapshot_version,
            "inventorySnapshotVersion": after.snapshot_version,
            "initialCapital": capital,
            "cash": after.cash,
            "positionCount": len(after.open_positions),
            "pendingOrderCount": len(after.pending_orders),
            "realizedPnl": after.realized_pnl,
            "unrealizedPnl": after.unrealized_pnl,
            "dailyLoss": after.daily_loss,
            "dailyTradeCount": after.daily_trade_count,
            "archivedWeightedVotingLocalPaperRecords": archived,
            "siblingAlgorithmMutationAllowed": False,
            "reasonCodes": ("weighted_voting.local_paper.reset_completed_weighted_voting_only", reason),
        }
        self.store.write_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.reset.latest", record)
        self.store.write_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.reset.{after.snapshot_version}", record)
        self._record_lifecycle(
            "weighted_voting.local_paper.account_reset",
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "decisionId": "weighted_voting.local_paper.reset",
                "orderIntentId": "weighted_voting.local_paper.reset",
                "clientOrderId": "weighted_voting.local_paper.reset",
                "status": "RESET",
                "reasonCodes": (reason,),
            },
            occurred_at=occurred_at,
            inventory_snapshot_version=after.snapshot_version,
            reason_codes=(reason, "weighted_voting.local_paper.reset_completed_weighted_voting_only"),
        )
        return record

    def gateway_account_snapshot(self, *, evaluated_at: datetime, **_: Any) -> AccountSnapshot:
        snapshot = self.inventory_repository.current_snapshot(now=evaluated_at)
        account = WeightedVotingLocalPaperAccount.from_inventory(snapshot, observed_at=evaluated_at)
        return AccountSnapshot(
            accountSnapshotId=f"weighted-voting-local-paper-{snapshot.snapshot_version}",
            accountId=account.capital_partition_id,
            equity=max(0.01, account.equity),
            highWaterEquity=max(0.01, snapshot.allocated_capital, account.equity),
            availableBuyingPower=max(0.0, account.buying_power),
            settledCash=max(0.0, account.cash),
            realizedDailyPnl=snapshot.daily_realised_pnl,
            unrealizedDailyPnl=snapshot.daily_unrealised_pnl,
            brokerConnected=True,
            brokerAccountActive=True,
            tradingPermission=True,
            clockSynchronized=True,
            accountSnapshotFresh=True,
            localBrokerOrdersReconciled=True,
            localBrokerPositionsReconciled=True,
            observedAt=evaluated_at,
        )

    def gateway_portfolio_snapshot(self, *, evaluated_at: datetime, **_: Any) -> PortfolioSnapshot:
        snapshot = self.inventory_repository.current_snapshot(now=evaluated_at)
        positions = tuple(
            PortfolioPosition(
                algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
                symbol=position.symbol,
                quantity=abs(position.quantity),
                marketValue=abs(position.quantity * (position.mark_price or position.average_entry_price)),
                openRiskDollars=0.0,
                side="short" if position.quantity < 0 else "long",
            )
            for position in snapshot.open_positions
        )
        pending = tuple(
            PendingOrder(
                algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
                symbol=order.symbol,
                side="Sell" if order.side.upper() == "SELL" else "Buy",
                quantity=max(0, order.quantity - order.filled_quantity),
                notional=order.reserved_buying_power,
                riskDollars=order.planned_risk_dollars,
                decisionId=order.decision_id,
                clientOrderId=order.client_order_id,
                intentKey=order.order_intent_id,
                submittedAt=order.created_at,
            )
            for order in snapshot.pending_orders
            if order.quantity > order.filled_quantity
        )
        return PortfolioSnapshot(
            positions=positions,
            pendingOrders=pending,
            tradesToday=snapshot.daily_trade_count,
            algorithmTradesToday={WEIGHTED_VOTING_ALGORITHM_ID: snapshot.daily_trade_count},
            ordersSubmittedInLastMinute=0,
        )

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        if str(getattr(intent, "algorithmId", "")) != WEIGHTED_VOTING_ALGORITHM_ID:
            return _rejected_ack(intent, "weighted_voting.local_paper.foreign_algorithm_rejected")
        if not str(getattr(intent, "capitalPartitionId", "")).startswith("weighted_voting."):
            return _rejected_ack(intent, "weighted_voting.local_paper.foreign_capital_partition_rejected")
        if bool(getattr(intent, "liveTradingEnabled", False)):
            return _rejected_ack(intent, "weighted_voting.local_paper.live_trading_rejected")
        if int(getattr(intent, "submittedQuantity", 0) or 0) <= 0:
            return _rejected_ack(intent, "weighted_voting.local_paper.zero_quantity_rejected")
        existing_ack = self._idempotent_existing_order_ack(intent)
        if existing_ack is not None:
            return existing_ack

        accepted_at = _utc(getattr(intent, "createdAt", None)) or datetime.now(UTC)
        inventory_snapshot = self.inventory_repository.current_snapshot(now=accepted_at)
        if _opens_unsupported_short(inventory_snapshot, intent):
            return self._reject_local_order(intent, reason="weighted_voting.local_paper.open_short_not_supported", rejected_at=accepted_at)

        order_type = _local_order_type(intent)
        if order_type == "MARKET":
            return self._reject_local_order(intent, reason="weighted_voting.local_paper.market_orders_disabled", rejected_at=accepted_at)
        if order_type not in SUPPORTED_WEIGHTED_VOTING_LOCAL_ORDER_TYPES:
            return self._reject_local_order(intent, reason="weighted_voting.local_paper.unsupported_order_type_rejected", rejected_at=accepted_at)

        fill_evaluation = _evaluate_local_paper_fill(
            intent,
            order_type=order_type,
            submitted_at=accepted_at,
            market_data=_market_data_for_intent(self.store, self.market_data_provider, intent, submitted_at=accepted_at),
        )
        status = fill_evaluation["status"]
        filled_quantity = int(fill_evaluation["filledQuantity"])
        order = _order_payload(
            intent,
            accepted_at=accepted_at,
            order_type=order_type,
            status=status,
            filled_quantity=filled_quantity,
            fill_evaluation=fill_evaluation,
        )
        self.store.write_snapshot(_order_key(intent.clientOrderId), order)
        self._record_lifecycle(
            "weighted_voting.local_paper.order_created",
            order,
            occurred_at=accepted_at,
            inventory_snapshot_version=inventory_snapshot.snapshot_version,
        )
        if status in {"OPEN", "PARTIALLY_FILLED", "FILLED"}:
            self._record_lifecycle(
                "weighted_voting.local_paper.order_open",
                order,
                occurred_at=accepted_at,
                inventory_snapshot_version=inventory_snapshot.snapshot_version,
            )
        self.store.write_snapshot(
            _order_index_key(intent.orderIntentId),
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "orderIntentId": intent.orderIntentId,
                "clientOrderId": intent.clientOrderId,
                "executionMode": "LOCAL_PAPER",
            },
        )
        fill = _fill_payload(intent, filled_at=accepted_at, status=status, filled_quantity=filled_quantity, fill_evaluation=fill_evaluation)
        if fill is not None:
            self.store.write_snapshot(_fill_key(intent.clientOrderId), fill)
            self._record_lifecycle(
                "weighted_voting.local_paper.fill_recorded",
                {**fill, "decisionId": intent.decisionId},
                occurred_at=accepted_at,
                inventory_snapshot_version=inventory_snapshot.snapshot_version,
                position_id=f"weighted_voting.position.{str(intent.symbol).upper()}.{intent.clientOrderId}",
            )
            for protective in _protective_order_payloads(intent, fill, created_at=accepted_at):
                self.store.write_snapshot(_protective_order_key(protective["clientOrderId"]), protective)
                self._record_lifecycle(
                    "weighted_voting.local_paper.order_created",
                    protective,
                    occurred_at=accepted_at,
                    inventory_snapshot_version=inventory_snapshot.snapshot_version,
                    position_id=str(protective.get("parentPositionId") or ""),
                )
                self._record_lifecycle(
                    "weighted_voting.local_paper.order_open",
                    protective,
                    occurred_at=accepted_at,
                    inventory_snapshot_version=inventory_snapshot.snapshot_version,
                    position_id=str(protective.get("parentPositionId") or ""),
                )
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"local-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=accepted_at,
            rejectedReason=None,
        )

    def _reject_local_order(self, intent: Any, *, reason: str, rejected_at: datetime) -> PaperGatewayBrokerAck:
        order = _order_payload(
            intent,
            accepted_at=rejected_at,
            order_type=_local_order_type(intent),
            status="REJECTED",
            filled_quantity=0,
            reason_codes=(reason,),
        )
        self.store.write_snapshot(_order_key(intent.clientOrderId), order)
        snapshot = self.inventory_repository.current_snapshot(now=rejected_at)
        self._record_lifecycle(
            "weighted_voting.local_paper.order_created",
            order,
            occurred_at=rejected_at,
            inventory_snapshot_version=snapshot.snapshot_version,
        )
        self._record_lifecycle(
            "weighted_voting.local_paper.reservation_released",
            order,
            occurred_at=rejected_at,
            inventory_snapshot_version=snapshot.snapshot_version,
            reason_codes=(reason,),
        )
        self.store.write_snapshot(
            _order_index_key(intent.orderIntentId),
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "orderIntentId": intent.orderIntentId,
                "clientOrderId": intent.clientOrderId,
                "executionMode": "LOCAL_PAPER",
                "status": "REJECTED",
                "reasonCodes": [reason],
            },
        )
        return _rejected_ack(intent, reason)

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            payload = self.store.read_snapshot(_fill_key(client_order_id))
        except KeyError:
            return None
        if str(payload.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            return None
        filled_quantity = int(payload.get("filledQuantity") or 0)
        average_price = payload.get("averageFillPrice")
        if filled_quantity <= 0 or average_price is None:
            return None
        return PaperGatewayFill(
            executionMode="LOCAL_PAPER",
            clientOrderId=client_order_id,
            algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
            capitalPartitionId=str(payload.get("capitalPartitionId") or WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID),
            accountId=WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
            orderIntentId=str(payload.get("orderIntentId") or client_order_id),
            symbol=str(payload.get("symbol") or "SPY").upper(),
            side=Signal.SELL if str(payload.get("side") or "").upper() == "SELL" else Signal.BUY,
            filledQuantity=filled_quantity,
            averageFillPrice=float(average_price),
            marketReferencePrice=_positive_float(payload.get("marketReferencePrice")),
            slippagePerShare=float(payload.get("slippagePerShare") or 0.0),
            spreadImpactPerShare=float(payload.get("spreadImpactPerShare") or 0.0),
            commission=float(payload.get("commission") or 0.0),
            regulatoryFees=float(payload.get("regulatoryFees") or 0.0),
            totalExecutionCost=float(payload.get("totalExecutionCost") or 0.0),
            executionCostBreakdown=dict(payload.get("executionCostBreakdown") or {}),
            status=str(payload.get("status") or "FILLED"),
            filledAt=_utc(payload.get("filledAt")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            order = dict(self.store.read_snapshot(_order_key(client_order_id)))
        except KeyError:
            return False
        if str(order.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            return False
        if str(order.get("status") or "").upper() in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"}:
            return False
        order["status"] = "CANCELED"
        order["lifecycleStatuses"] = _lifecycle_for_status("CANCELED")
        order["reasonCodes"] = [*list(order.get("reasonCodes") or ()), "weighted_voting.local_paper.order_canceled_locally"]
        order["updatedAt"] = datetime.now(UTC).isoformat()
        self.store.write_snapshot(_order_key(client_order_id), order)
        snapshot = self.inventory_repository.current_snapshot(now=_utc(order["updatedAt"]) or datetime.now(UTC))
        self._record_lifecycle(
            "weighted_voting.local_paper.reservation_released",
            order,
            occurred_at=_utc(order["updatedAt"]) or datetime.now(UTC),
            inventory_snapshot_version=snapshot.snapshot_version,
            reason_codes=("weighted_voting.local_paper.order_canceled_locally",),
        )
        return True

    def expire_order(self, client_order_id: str, *, expired_at: datetime | None = None) -> bool:
        return self._mark_terminal_order_status(
            client_order_id,
            status="EXPIRED",
            reason_code="weighted_voting.local_paper.order_expired_locally",
            occurred_at=expired_at or datetime.now(UTC),
        )

    def replace_order(self, client_order_id: str, *, replacement_client_order_id: str, replaced_at: datetime | None = None) -> bool:
        return self._mark_terminal_order_status(
            client_order_id,
            status="REPLACED",
            reason_code="weighted_voting.local_paper.order_replaced_locally",
            occurred_at=replaced_at or datetime.now(UTC),
            extra={"replacementClientOrderId": replacement_client_order_id},
        )

    def _mark_terminal_order_status(
        self,
        client_order_id: str,
        *,
        status: str,
        reason_code: str,
        occurred_at: datetime,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        try:
            order = dict(self.store.read_snapshot(_order_key(client_order_id)))
        except KeyError:
            return False
        if str(order.get("status") or "").upper() in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"}:
            return False
        order["status"] = status
        order["lifecycleStatuses"] = _lifecycle_for_status(status)
        order["reasonCodes"] = [*list(order.get("reasonCodes") or ()), reason_code]
        order["updatedAt"] = occurred_at.isoformat()
        if extra:
            order.update(extra)
        self.store.write_snapshot(_order_key(client_order_id), order)
        snapshot = self.inventory_repository.current_snapshot(now=occurred_at)
        self._record_lifecycle(
            "weighted_voting.local_paper.reservation_released",
            order,
            occurred_at=occurred_at,
            inventory_snapshot_version=snapshot.snapshot_version,
            reason_codes=(reason_code,),
        )
        return True

    def _idempotent_existing_order_ack(self, intent: Any) -> PaperGatewayBrokerAck | None:
        try:
            existing = self.store.read_snapshot(_order_key(str(intent.clientOrderId)))
        except KeyError:
            return None
        if str(existing.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            return _rejected_ack(intent, "weighted_voting.local_paper.foreign_existing_order_rejected")
        status = str(existing.get("status") or "ACCEPTED").upper()
        if status == "REJECTED":
            return PaperGatewayBrokerAck(
                clientOrderId=str(intent.clientOrderId),
                brokerOrderId=str(existing.get("brokerOrderId") or f"local-{intent.clientOrderId}"),
                status="REJECTED",
                acceptedAt=None,
                rejectedReason=str((existing.get("reasonCodes") or ["weighted_voting.local_paper.rejected"])[0]),
            )
        return PaperGatewayBrokerAck(
            clientOrderId=str(intent.clientOrderId),
            brokerOrderId=str(existing.get("brokerOrderId") or f"local-{intent.clientOrderId}"),
            status="ACCEPTED",
            acceptedAt=_utc(existing.get("submittedAt") or existing.get("updatedAt")),
            rejectedReason=None,
        )

    def refresh_orders(self) -> list[dict[str, Any]]:
        return [dict(payload) for key, payload in _store_items(self.store) if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") and isinstance(payload, dict)]

    def process_market_data(self, *, symbol: str, market_data: dict[str, Any], observed_at: datetime) -> tuple[PaperGatewayFill, ...]:
        normalized = _normalize_market_data(market_data, submitted_at=observed_at)
        if normalized is None:
            return ()
        fills: list[PaperGatewayFill] = []
        for key, protective in _active_protective_orders(self.store, symbol=symbol):
            fill_payload = self._maybe_fill_protective_order(key, protective, normalized, observed_at=observed_at)
            if fill_payload is None:
                continue
            fill = self.refresh_order(str(fill_payload["clientOrderId"]))
            if fill is not None:
                fills.append(fill)
        if fills:
            return tuple(fills)
        for key, order in _active_local_orders(self.store, symbol=symbol):
            fill_payload = self._maybe_fill_open_order(key, order, normalized, observed_at=observed_at)
            if fill_payload is None:
                continue
            fill = self.refresh_order(str(fill_payload["clientOrderId"]))
            if fill is not None:
                fills.append(fill)
        return tuple(fills)

    def refresh_positions(self) -> list[dict[str, Any]]:
        snapshot = self.inventory_repository.current_snapshot(now=datetime.now(UTC))
        return [
            {
                "executionMode": "LOCAL_PAPER",
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "capitalPartitionId": snapshot.capital_partition_id,
                "positionId": position.position_id,
                "clientOrderId": position.client_order_id,
                "symbol": position.symbol,
                "quantity": position.quantity,
                "averageEntryPrice": position.average_entry_price,
                "source": "weighted_voting.local_paper.inventory_positions",
            }
            for position in snapshot.open_positions
        ]

    def _maybe_fill_open_order(
        self,
        key: str,
        order: dict[str, Any],
        market_data: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> dict[str, Any] | None:
        try:
            current = dict(self.store.read_snapshot(key))
        except KeyError:
            return None
        if str(current.get("status") or "").upper() not in {"PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"}:
            return None
        if str(current.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            return None
        intent = _order_intent_like(current)
        order_type = str(current.get("orderType") or "LIMIT").upper()
        fill_evaluation = _evaluate_local_paper_fill(intent, order_type=order_type, submitted_at=observed_at, market_data=market_data)
        filled_quantity = int(fill_evaluation.get("filledQuantity") or 0)
        if filled_quantity <= 0:
            updated = {
                **current,
                "fillEvaluation": fill_evaluation,
                "updatedAt": observed_at.isoformat(),
                "reasonCodes": list(dict.fromkeys([*(current.get("reasonCodes") or ()), fill_evaluation.get("reasonCode")])),
            }
            self.store.write_snapshot(key, updated)
            return None
        previous_filled = int(current.get("filledQuantity") or 0)
        total_quantity = int(current.get("quantity") or previous_filled + int(current.get("remainingQuantity") or 0))
        total_filled = min(total_quantity, previous_filled + filled_quantity)
        remaining = max(0, total_quantity - total_filled)
        status = "FILLED" if remaining <= 0 else "PARTIALLY_FILLED"
        fill_evaluation = {**fill_evaluation, "status": status, "remainingQuantity": remaining}
        updated = {
            **current,
            "status": status,
            "filledQuantity": total_filled,
            "remainingQuantity": remaining,
            "averageFillPrice": fill_evaluation.get("fillPrice"),
            "fillEvaluation": fill_evaluation,
            "lifecycleStatuses": _lifecycle_for_status(status),
            "updatedAt": observed_at.isoformat(),
            "reasonCodes": list(dict.fromkeys([*(current.get("reasonCodes") or ()), "weighted_voting.local_paper.open_order_filled_from_market_data"])),
        }
        self.store.write_snapshot(key, updated)
        fill = _fill_payload(intent, filled_at=observed_at, status=status, filled_quantity=filled_quantity, fill_evaluation=fill_evaluation)
        if fill is None:
            return None
        self.store.write_snapshot(_fill_key(str(fill["clientOrderId"])), fill)
        for protective in _protective_order_payloads(intent, fill, created_at=observed_at):
            self.store.write_snapshot(_protective_order_key(protective["clientOrderId"]), protective)
        before_snapshot = self.inventory_repository.current_snapshot(now=observed_at)
        updated_snapshot = self._record_open_order_inventory_fill(updated, fill, observed_at=observed_at)
        lifecycle_version = updated_snapshot.snapshot_version if updated_snapshot is not None else before_snapshot.snapshot_version
        self._record_lifecycle(
            "weighted_voting.local_paper.fill_recorded",
            {**fill, "decisionId": updated.get("decisionId")},
            occurred_at=observed_at,
            inventory_snapshot_version=lifecycle_version,
            position_id=f"weighted_voting.position.{str(updated.get('symbol') or '').upper()}.{fill['clientOrderId']}",
        )
        if updated_snapshot is not None:
            self._record_lifecycle(
                "weighted_voting.local_paper.position_updated",
                updated,
                occurred_at=observed_at,
                inventory_snapshot_version=updated_snapshot.snapshot_version,
                position_id=f"weighted_voting.position.{str(updated.get('symbol') or '').upper()}.{fill['clientOrderId']}",
            )
        for protective in _protective_order_payloads(intent, fill, created_at=observed_at):
            self._record_lifecycle(
                "weighted_voting.local_paper.order_created",
                protective,
                occurred_at=observed_at,
                inventory_snapshot_version=lifecycle_version,
                position_id=str(protective.get("parentPositionId") or ""),
            )
            self._record_lifecycle(
                "weighted_voting.local_paper.order_open",
                protective,
                occurred_at=observed_at,
                inventory_snapshot_version=lifecycle_version,
                position_id=str(protective.get("parentPositionId") or ""),
            )
        return fill

    def _record_open_order_inventory_fill(self, order: dict[str, Any], fill: dict[str, Any], *, observed_at: datetime) -> WeightedVotingInventorySnapshot | None:
        snapshot = self.inventory_repository.current_snapshot(now=observed_at)
        side = str(order.get("side") or "").upper()
        signed_quantity = -int(fill.get("filledQuantity") or 0) if side == "SELL" else int(fill.get("filledQuantity") or 0)
        if signed_quantity == 0:
            return None
        client_order_id = str(fill.get("clientOrderId") or "")
        return self.inventory_repository.append_event(
            event_id=str(fill.get("fillId") or f"{client_order_id}.fill.{observed_at.isoformat()}"),
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload={
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "fill_id": str(fill.get("fillId") or ""),
                "position_id": f"weighted_voting.position.{str(order.get('symbol') or '').upper()}.{client_order_id}",
                "symbol": str(order.get("symbol") or "").upper(),
                "side": "SHORT" if signed_quantity < 0 else "LONG",
                "quantity": signed_quantity,
                "average_entry_price": float(fill.get("averageFillPrice") or 0.0),
                "mark_price": float(fill.get("averageFillPrice") or 0.0),
                "market_reference_price": fill.get("marketReferencePrice"),
                "slippage_per_share": fill.get("slippagePerShare"),
                "spread_impact_per_share": fill.get("spreadImpactPerShare"),
                "commission": fill.get("commission"),
                "regulatory_fees": fill.get("regulatoryFees"),
                "total_execution_cost": fill.get("totalExecutionCost"),
                "execution_costs": fill.get("executionCostBreakdown") or {},
                "opened_at": observed_at.isoformat(),
                "decision_id": str(order.get("decisionId") or ""),
                "order_intent_id": str(order.get("orderIntentId") or ""),
                "client_order_id": client_order_id,
                "source": "weighted_voting.local_paper.open_order_fill",
            },
            occurred_at=observed_at,
            expected_snapshot_version=snapshot.snapshot_version,
        )

    def _maybe_fill_protective_order(
        self,
        key: str,
        protective: dict[str, Any],
        market_data: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> dict[str, Any] | None:
        try:
            current = dict(self.store.read_snapshot(key))
        except KeyError:
            return None
        if str(current.get("status") or "").upper() not in {"PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"}:
            return None
        protective = current
        if str(protective.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            return None
        reference = _execution_reference(side="SELL", market_data=market_data)
        if reference is None:
            return None
        trigger_kind = _protective_trigger_kind(protective, reference)
        if trigger_kind is None:
            return None
        snapshot = self.inventory_repository.current_snapshot(now=observed_at)
        available_quantity = _long_quantity_available(snapshot, str(protective.get("symbol") or "").upper())
        quantity = min(max(0, int(protective.get("remainingQuantity") or protective.get("quantity") or 0)), available_quantity)
        if quantity <= 0:
            return None
        costs = _execution_costs(_protective_intent_like(protective), side="SELL", market_price=float(reference["price"]), quantity=quantity)
        fill_evaluation = _fill_evaluation(
            status="FILLED",
            filled_quantity=quantity,
            submitted_quantity=quantity,
            fill_price=costs["executionPrice"],
            market_data=market_data,
            executable=True,
            triggered=True,
            reason=f"weighted_voting.local_paper.{trigger_kind}_protective_exit_triggered",
            reference=reference,
            execution_costs=costs,
        )
        updated = {
            **protective,
            "status": "FILLED",
            "filledQuantity": quantity,
            "remainingQuantity": 0,
            "averageFillPrice": costs["executionPrice"],
            "triggeredBy": trigger_kind,
            "triggeredAt": observed_at.isoformat(),
            "updatedAt": observed_at.isoformat(),
            "lifecycleStatuses": _lifecycle_for_status("FILLED"),
            "fillEvaluation": fill_evaluation,
            "reasonCodes": list(
                dict.fromkeys(
                    [
                        *(protective.get("reasonCodes") or ()),
                        f"weighted_voting.local_paper.{trigger_kind}_protective_exit_triggered",
                        "weighted_voting.local_paper.protective_exit_filled_locally",
                    ]
                )
            ),
        }
        self.store.write_snapshot(key, updated)
        fill_payload = _protective_fill_payload(updated, filled_at=observed_at, fill_evaluation=fill_evaluation)
        self.store.write_snapshot(_fill_key(str(updated["clientOrderId"])), fill_payload)
        updated_snapshot = self._record_protective_inventory_fill(updated, fill_payload, observed_at=observed_at)
        lifecycle_version = updated_snapshot.snapshot_version if updated_snapshot is not None else snapshot.snapshot_version
        self._record_lifecycle(
            "weighted_voting.local_paper.fill_recorded",
            {**fill_payload, "decisionId": updated.get("decisionId")},
            occurred_at=observed_at,
            inventory_snapshot_version=lifecycle_version,
            position_id=str(updated.get("parentPositionId") or ""),
        )
        self._record_lifecycle(
            "weighted_voting.local_paper.exit_filled",
            updated,
            occurred_at=observed_at,
            inventory_snapshot_version=lifecycle_version,
            position_id=str(updated.get("parentPositionId") or ""),
        )
        if updated_snapshot is not None:
            self._record_lifecycle(
                "weighted_voting.local_paper.position_updated",
                updated,
                occurred_at=observed_at,
                inventory_snapshot_version=updated_snapshot.snapshot_version,
                position_id=str(updated.get("parentPositionId") or ""),
            )
        _cancel_sibling_protective_orders(self.store, updated, canceled_at=observed_at, inventory_snapshot_version=lifecycle_version)
        return fill_payload

    def _record_protective_inventory_fill(self, protective: dict[str, Any], fill: dict[str, Any], *, observed_at: datetime) -> WeightedVotingInventorySnapshot | None:
        snapshot = self.inventory_repository.current_snapshot(now=observed_at)
        client_order_id = str(protective.get("clientOrderId") or "")
        parent_client_order_id = str(protective.get("parentClientOrderId") or "")
        execution_costs = dict(fill.get("executionCostBreakdown") or {})
        if int(fill.get("filledQuantity") or 0) <= 0:
            return None
        return self.inventory_repository.append_event(
            event_id=f"{client_order_id}.protective_fill.FILLED",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload={
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "fill_id": f"{client_order_id}.protective_fill",
                "position_id": str(protective.get("parentPositionId") or f"weighted_voting.position.{protective.get('symbol')}.{parent_client_order_id}"),
                "symbol": str(protective.get("symbol") or "").upper(),
                "side": "SHORT",
                "quantity": -int(fill.get("filledQuantity") or 0),
                "average_entry_price": float(fill.get("averageFillPrice") or 0.0),
                "market_reference_price": fill.get("marketReferencePrice"),
                "slippage_per_share": fill.get("slippagePerShare"),
                "spread_impact_per_share": fill.get("spreadImpactPerShare"),
                "commission": fill.get("commission"),
                "regulatory_fees": fill.get("regulatoryFees"),
                "total_execution_cost": fill.get("totalExecutionCost"),
                "execution_costs": execution_costs,
                "opened_at": observed_at.isoformat(),
                "decision_id": str(protective.get("decisionId") or ""),
                "order_intent_id": str(protective.get("orderIntentId") or ""),
                "client_order_id": client_order_id,
                "parent_client_order_id": parent_client_order_id,
                "source": "weighted_voting.local_paper.protective_exit_fill",
            },
            occurred_at=observed_at,
            expected_snapshot_version=snapshot.snapshot_version,
        )

    def _record_lifecycle(
        self,
        event_name: str,
        source: Any,
        *,
        occurred_at: datetime,
        inventory_snapshot_version: int | None,
        position_id: str | None = None,
        reason_codes: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        return record_weighted_voting_local_paper_lifecycle_event(
            self.store,
            event_name=event_name,
            source=source,
            occurred_at=occurred_at,
            inventory_snapshot_version=inventory_snapshot_version,
            position_id=position_id,
            reason_codes=reason_codes,
        )


class WeightedVotingLocalPaperRiskPort:
    def __init__(self, inventory_repository: WeightedVotingInventoryRepository) -> None:
        self.inventory_repository = inventory_repository

    def global_risk_state(self, *, as_of: datetime) -> WeightedVotingGlobalRiskState:
        snapshot = self.inventory_repository.current_snapshot(now=as_of)
        return WeightedVotingGlobalRiskState(
            service_available=snapshot.allocated_capital > 0,
            global_available_risk=snapshot.remaining_daily_risk,
            global_max_shares=max(0, int(snapshot.remaining_capital_partition)),
            gate_response=None,
            observed_at=as_of,
            source_id="weighted_voting.local_paper.local_risk_state",
            reason_codes=("weighted_voting.local_paper.global_risk_capacity_from_inventory",),
        )


class WeightedVotingLocalPaperRiskService:
    def __init__(self, inventory_repository: WeightedVotingInventoryRepository) -> None:
        self.inventory_repository = inventory_repository

    def evaluate(self, request: WeightedVotingGlobalRiskRequest) -> WeightedVotingGlobalRiskResponse:
        snapshot = self.inventory_repository.current_snapshot(now=request.request_timestamp)
        if snapshot.allocated_capital <= 0:
            return _risk_response(request, action="REJECT", quantity=0, risk=0.0, reason="weighted_voting.local_paper.capital_unallocated")
        requested_quantity = max(0, int(request.proposed_quantity))
        risk_per_share = request.planned_risk / requested_quantity if requested_quantity > 0 else 0.0
        price = request.proposed_notional / requested_quantity if requested_quantity > 0 else 0.0
        max_by_risk = requested_quantity if risk_per_share <= 0 else int(snapshot.remaining_daily_risk // risk_per_share)
        max_by_capital = requested_quantity if price <= 0 else int(snapshot.remaining_capital_partition // price)
        allowed = max(0, min(requested_quantity, max_by_risk, max_by_capital))
        if allowed <= 0:
            return _risk_response(request, action="REJECT", quantity=0, risk=0.0, reason="weighted_voting.local_paper.local_risk_rejected")
        action = "ALLOW" if allowed == requested_quantity else "REDUCE"
        approved_risk = min(request.planned_risk, allowed * risk_per_share if risk_per_share > 0 else request.planned_risk)
        return _risk_response(request, action=action, quantity=allowed, risk=approved_risk, reason="weighted_voting.local_paper.local_risk_allowed")


def build_weighted_voting_local_paper_gateway_dependencies(
    store: Any,
    inventory_repository: WeightedVotingInventoryRepository,
    market_data_provider: Any | None = None,
) -> tuple[WeightedVotingLocalPaperBroker, WeightedVotingLocalPaperRiskPort, WeightedVotingLocalPaperRiskService]:
    broker = WeightedVotingLocalPaperBroker(store, inventory_repository, market_data_provider=market_data_provider)
    return broker, WeightedVotingLocalPaperRiskPort(inventory_repository), WeightedVotingLocalPaperRiskService(inventory_repository)


def _risk_response(
    request: WeightedVotingGlobalRiskRequest,
    *,
    action: str,
    quantity: int,
    risk: float,
    reason: str,
) -> WeightedVotingGlobalRiskResponse:
    response = WeightedVotingGlobalRiskResponse(
        request_id=request.request_id,
        proposal_id=request.proposal_id,
        action=action,  # type: ignore[arg-type]
        maximum_quantity=quantity,
        maximum_additional_risk=risk,
        reason_codes=(reason,),
        configuration_hash="weighted_voting.local_paper_risk",
        configuration_version=WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION,
        evaluated_timestamp=request.request_timestamp,
        expiry_timestamp=request.request_timestamp + timedelta(seconds=30),
    )
    return response.with_hash()


def _order_payload(
    intent: Any,
    *,
    accepted_at: datetime,
    order_type: str,
    status: str,
    filled_quantity: int,
    reason_codes: tuple[str, ...] | None = None,
    fill_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    submitted_quantity = int(getattr(intent, "submittedQuantity", 0) or 0)
    average_price = _fill_price(intent) if filled_quantity > 0 else None
    reasons = list(reason_codes or (_status_reason_code(status),))
    return {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION,
        "executionMode": "LOCAL_PAPER",
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "capitalPartitionId": intent.capitalPartitionId,
        "decisionId": intent.decisionId,
        "orderIntentId": intent.orderIntentId,
        "clientOrderId": intent.clientOrderId,
        "symbol": str(intent.symbol).upper(),
        "side": str(intent.side.value if hasattr(intent.side, "value") else intent.side).upper(),
        "orderType": order_type,
        "timeInForce": str(getattr(intent, "timeInForce", "DAY") or "DAY").upper(),
        "quantity": submitted_quantity,
        "filledQuantity": int(filled_quantity),
        "remainingQuantity": max(0, submitted_quantity - int(filled_quantity)),
        "averageFillPrice": average_price,
        "limitPrice": getattr(intent, "limitPrice", None),
        "stopPrice": getattr(intent, "stopPrice", None),
        "stopLimitPrice": getattr(intent, "stopLimitPrice", None),
        "targetPrice": getattr(intent, "targetPrice", None),
        "settingsSnapshot": dict(getattr(intent, "settingsSnapshot", None) or {}),
        "status": status,
        "lifecycleStatuses": _lifecycle_for_status(status),
        "brokerOrderId": f"local-{intent.clientOrderId}",
        "fillEngineVersion": WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION,
        "fillEvaluation": fill_evaluation or {},
        "submittedAt": accepted_at.isoformat(),
        "updatedAt": accepted_at.isoformat(),
        "reasonCodes": reasons,
    }


def _fill_payload(intent: Any, *, filled_at: datetime, status: str, filled_quantity: int, fill_evaluation: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if filled_quantity <= 0:
        return None
    side = str(intent.side.value if hasattr(intent.side, "value") else intent.side).upper()
    costs = dict((fill_evaluation or {}).get("executionCosts") or {})
    return {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION,
        "executionMode": "LOCAL_PAPER",
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "fillId": f"{intent.clientOrderId}.{status if status in {'PARTIALLY_FILLED', 'FILLED'} else 'FILLED'}.{int(filled_quantity)}.{filled_at.isoformat()}",
        "capitalPartitionId": intent.capitalPartitionId,
        "accountId": WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        "orderIntentId": intent.orderIntentId,
        "clientOrderId": intent.clientOrderId,
        "symbol": str(intent.symbol).upper(),
        "side": side,
        "filledQuantity": int(filled_quantity),
        "averageFillPrice": float((fill_evaluation or {}).get("fillPrice") or _fill_price(intent)),
        "marketReferencePrice": (fill_evaluation or {}).get("marketReferencePrice"),
        "slippagePerShare": float(costs.get("slippagePerShare") or 0.0),
        "spreadImpactPerShare": float(costs.get("spreadImpactPerShare") or 0.0),
        "commission": float(costs.get("commission") or 0.0),
        "regulatoryFees": float(costs.get("regulatoryFees") or 0.0),
        "totalExecutionCost": float(costs.get("totalExecutionCost") or 0.0),
        "executionCostBreakdown": costs,
        "status": status if status in {"PARTIALLY_FILLED", "FILLED"} else "FILLED",
        "filledAt": filled_at.isoformat(),
        "fillEngineVersion": WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION,
        "fillEvaluation": fill_evaluation or {},
        "reasonCodes": [_status_reason_code(status), "weighted_voting.local_paper.fill_simulated_locally"],
    }


def _protective_order_payloads(intent: Any, fill: dict[str, Any], *, created_at: datetime) -> tuple[dict[str, Any], ...]:
    if str(intent.side.value if hasattr(intent.side, "value") else intent.side).upper() != "BUY":
        return ()
    quantity = max(0, int(fill.get("filledQuantity") or 0))
    if quantity <= 0:
        return ()
    stop_price = _positive_float(getattr(intent, "stopPrice", None))
    target_price = _positive_float(getattr(intent, "targetPrice", None))
    if stop_price is None and target_price is None:
        return ()
    parent_client_order_id = str(intent.clientOrderId)
    parent_position_id = f"weighted_voting.position.{str(intent.symbol).upper()}.{parent_client_order_id}"
    common = {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION,
        "executionMode": "LOCAL_PAPER",
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "capitalPartitionId": intent.capitalPartitionId,
        "accountId": WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        "parentClientOrderId": parent_client_order_id,
        "parentOrderIntentId": intent.orderIntentId,
        "parentPositionId": parent_position_id,
        "decisionId": intent.decisionId,
        "orderIntentId": intent.orderIntentId,
        "symbol": str(intent.symbol).upper(),
        "side": "SELL",
        "quantity": quantity,
        "filledQuantity": 0,
        "remainingQuantity": quantity,
        "status": "OPEN",
        "lifecycleStatuses": _lifecycle_for_status("OPEN"),
        "createdAt": created_at.isoformat(),
        "updatedAt": created_at.isoformat(),
        "reasonCodes": ["weighted_voting.local_paper.protective_order_created_for_entry_fill"],
    }
    orders: list[dict[str, Any]] = []
    if stop_price is not None:
        orders.append(
            {
                **common,
                "clientOrderId": f"{parent_client_order_id}-stop",
                "protectiveKind": "stop_loss",
                "orderType": "STOP",
                "stopPrice": stop_price,
                "targetPrice": None,
                "limitPrice": None,
            }
        )
    if target_price is not None:
        orders.append(
            {
                **common,
                "clientOrderId": f"{parent_client_order_id}-target",
                "protectiveKind": "profit_target",
                "orderType": "LIMIT",
                "stopPrice": None,
                "targetPrice": target_price,
                "limitPrice": target_price,
            }
        )
    return tuple(orders)


def _active_local_orders(store: Any, *, symbol: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    active_statuses = {"PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"}
    result: list[tuple[str, dict[str, Any]]] = []
    for key, payload in _store_items(store):
        if not key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders."):
            continue
        if str(payload.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            continue
        if str(payload.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(payload.get("status") or "").upper() not in active_statuses:
            continue
        result.append((key, dict(payload)))
    return tuple(result)


def _order_intent_like(order: dict[str, Any]) -> SimpleNamespace:
    remaining = int(order.get("remainingQuantity") or 0)
    return SimpleNamespace(
        algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
        capitalPartitionId=order.get("capitalPartitionId") or WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        decisionId=order.get("decisionId") or "",
        orderIntentId=order.get("orderIntentId") or "",
        clientOrderId=order.get("clientOrderId") or "",
        symbol=str(order.get("symbol") or "SPY").upper(),
        side=str(order.get("side") or "BUY").upper(),
        submittedQuantity=remaining,
        orderType=str(order.get("orderType") or "LIMIT").upper(),
        timeInForce=str(order.get("timeInForce") or "DAY").upper(),
        limitPrice=order.get("limitPrice"),
        stopPrice=order.get("stopPrice"),
        stopLimitPrice=order.get("stopLimitPrice"),
        triggerPrice=order.get("stopPrice") or order.get("limitPrice"),
        targetPrice=order.get("targetPrice"),
        settingsSnapshot=dict(order.get("settingsSnapshot") or {}),
    )


def _active_protective_orders(store: Any, *, symbol: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    active_statuses = {"PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"}
    result: list[tuple[str, dict[str, Any]]] = []
    for key, payload in _store_items(store):
        if not key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders."):
            continue
        if str(payload.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            continue
        if str(payload.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(payload.get("status") or "").upper() not in active_statuses:
            continue
        result.append((key, dict(payload)))
    return tuple(result)


def _protective_trigger_kind(protective: dict[str, Any], reference: dict[str, Any]) -> str | None:
    price = float(reference["price"])
    kind = str(protective.get("protectiveKind") or "").lower()
    if kind == "stop_loss":
        stop_price = _positive_float(protective.get("stopPrice"))
        return "stop_loss" if stop_price is not None and price <= stop_price else None
    if kind == "profit_target":
        target_price = _positive_float(protective.get("targetPrice") or protective.get("limitPrice"))
        return "profit_target" if target_price is not None and price >= target_price else None
    return None


def _protective_intent_like(protective: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(settingsSnapshot=dict(protective.get("settingsSnapshot") or {}))


def _protective_fill_payload(protective: dict[str, Any], *, filled_at: datetime, fill_evaluation: dict[str, Any]) -> dict[str, Any]:
    costs = dict(fill_evaluation.get("executionCosts") or {})
    return {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION,
        "executionMode": "LOCAL_PAPER",
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "capitalPartitionId": protective.get("capitalPartitionId") or WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        "accountId": WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        "orderIntentId": protective["orderIntentId"],
        "clientOrderId": protective["clientOrderId"],
        "parentClientOrderId": protective.get("parentClientOrderId"),
        "symbol": str(protective.get("symbol") or "").upper(),
        "side": "SELL",
        "filledQuantity": int(protective.get("filledQuantity") or 0),
        "averageFillPrice": float(protective.get("averageFillPrice") or fill_evaluation.get("fillPrice") or 0.01),
        "marketReferencePrice": fill_evaluation.get("marketReferencePrice"),
        "slippagePerShare": float(costs.get("slippagePerShare") or 0.0),
        "spreadImpactPerShare": float(costs.get("spreadImpactPerShare") or 0.0),
        "commission": float(costs.get("commission") or 0.0),
        "regulatoryFees": float(costs.get("regulatoryFees") or 0.0),
        "totalExecutionCost": float(costs.get("totalExecutionCost") or 0.0),
        "executionCostBreakdown": costs,
        "status": "FILLED",
        "filledAt": filled_at.isoformat(),
        "fillEngineVersion": WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION,
        "fillEvaluation": fill_evaluation,
        "reasonCodes": [
            "weighted_voting.local_paper.protective_exit_filled_locally",
            "weighted_voting.local_paper.fill_simulated_locally",
        ],
    }


def _cancel_sibling_protective_orders(store: Any, filled: dict[str, Any], *, canceled_at: datetime, inventory_snapshot_version: int | None) -> None:
    parent_client_order_id = str(filled.get("parentClientOrderId") or "")
    filled_client_order_id = str(filled.get("clientOrderId") or "")
    if not parent_client_order_id:
        return
    for key, payload in _store_items(store):
        if not key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders."):
            continue
        if str(payload.get("algorithmId") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            continue
        if str(payload.get("parentClientOrderId") or "") != parent_client_order_id:
            continue
        if str(payload.get("clientOrderId") or "") == filled_client_order_id:
            continue
        if str(payload.get("status") or "").upper() not in {"PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"}:
            continue
        updated = {
            **payload,
            "status": "CANCELED",
            "remainingQuantity": int(payload.get("remainingQuantity") or payload.get("quantity") or 0),
            "lifecycleStatuses": _lifecycle_for_status("CANCELED"),
            "updatedAt": canceled_at.isoformat(),
            "reasonCodes": list(
                dict.fromkeys(
                    [
                        *(payload.get("reasonCodes") or ()),
                        "weighted_voting.local_paper.protective_sibling_canceled_after_exit_fill",
                    ]
                )
            ),
        }
        store.write_snapshot(key, updated)
        record_weighted_voting_local_paper_lifecycle_event(
            store,
            event_name="weighted_voting.local_paper.reservation_released",
            source=updated,
            occurred_at=canceled_at,
            inventory_snapshot_version=inventory_snapshot_version,
            position_id=str(updated.get("parentPositionId") or ""),
            reason_codes=("weighted_voting.local_paper.protective_sibling_canceled_after_exit_fill",),
        )


def _archive_weighted_voting_local_paper_records(store: Any, *, reset_at: datetime, reason: str) -> dict[str, int]:
    prefixes = {
        "orders": f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.",
        "fills": f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills.",
        "protectiveOrders": f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.",
        "executionGatewayLocalPaper": "weighted_voting.execution_gateway.local_paper.",
    }
    archived = {name: 0 for name in prefixes}
    for key, payload in _store_items(store):
        category = next((name for name, prefix in prefixes.items() if key.startswith(prefix)), None)
        if category is None:
            continue
        if str(payload.get("algorithmId") or payload.get("algorithm_id") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
            continue
        updated = {
            **payload,
            "status": "RESET_ARCHIVED",
            "resetArchived": True,
            "resetArchivedAt": reset_at.isoformat(),
            "resetReason": reason,
            "filledQuantity": 0 if category == "fills" else payload.get("filledQuantity", payload.get("filled_quantity", 0)),
            "remainingQuantity": 0 if category in {"orders", "protectiveOrders"} else payload.get("remainingQuantity", payload.get("remaining_quantity", 0)),
            "averageFillPrice": None if category == "fills" else payload.get("averageFillPrice"),
            "reasonCodes": list(
                dict.fromkeys(
                    [
                        *(payload.get("reasonCodes") or payload.get("reason_codes") or ()),
                        "weighted_voting.local_paper.reset_archived_weighted_voting_record",
                        reason,
                    ]
                )
            ),
        }
        store.write_snapshot(key, updated)
        archived[category] += 1
    return archived


def _fill_price(intent: Any) -> float:
    value = getattr(intent, "limitPrice", None) or getattr(intent, "triggerPrice", None) or getattr(intent, "stopPrice", None)
    return float(value or 0.01)


def _local_order_type(intent: Any) -> str:
    raw = str(getattr(intent, "orderType", "") or "LIMIT").strip().upper().replace("-", "_").replace(" ", "_")
    if raw in {"BRACKET_LIMIT", "ENTRY_LIMIT"} or raw.endswith("_LIMIT") and "STOP" not in raw:
        return "LIMIT"
    if "STOP" in raw and "LIMIT" in raw:
        return "STOP_LIMIT"
    if raw in {"STOP", "STOP_MARKET"}:
        return "STOP"
    if raw == "MARKET":
        return "MARKET"
    if "LIMIT" in raw:
        return "LIMIT"
    return raw


def _opens_unsupported_short(snapshot: WeightedVotingInventorySnapshot, intent: Any) -> bool:
    raw_side = getattr(intent, "side", "")
    side = str(raw_side.value if hasattr(raw_side, "value") else raw_side).upper()
    if side != "SELL":
        return False
    requested_quantity = max(0, int(getattr(intent, "submittedQuantity", 0) or 0))
    return requested_quantity > _long_quantity_available(snapshot, str(getattr(intent, "symbol", "") or "").upper())


def _long_quantity_available(snapshot: WeightedVotingInventorySnapshot, symbol: str) -> int:
    return sum(max(0, int(position.quantity)) for position in snapshot.open_positions if position.symbol.upper() == symbol.upper())


def _liquidity_capped_quantity(intent: Any, *, executable: bool) -> int:
    if not executable:
        return 0
    submitted_quantity = max(0, int(getattr(intent, "submittedQuantity", 0) or 0))
    settings = getattr(intent, "settingsSnapshot", None)
    override = None
    partial_fill_mode = "DETERMINISTIC_LIQUIDITY"
    if isinstance(settings, dict):
        partial_fill_mode = str(
            settings.get("weighted_voting.local_paper.partial_fill_mode")
            or settings.get("localPaperPartialFillMode")
            or ((settings.get("localPaper") or {}).get("partialFillMode") if isinstance(settings.get("localPaper"), dict) else "")
            or partial_fill_mode
        ).upper()
        override = (
            settings.get("localPaperAvailableQuantity")
            or settings.get("weightedVotingLocalPaperAvailableQuantity")
            or settings.get("localPaperFilledQuantity")
            or settings.get("weightedVotingLocalPaperFilledQuantity")
        )
    if override is not None:
        available = max(0, min(submitted_quantity, int(override)))
        if partial_fill_mode == "ALL_OR_NONE" and available < submitted_quantity:
            return 0
        return available
    return submitted_quantity


def _evaluate_local_paper_fill(intent: Any, *, order_type: str, submitted_at: datetime, market_data: dict[str, Any] | None) -> dict[str, Any]:
    side = str(intent.side.value if hasattr(intent.side, "value") else intent.side).upper()
    submitted_quantity = max(0, int(getattr(intent, "submittedQuantity", 0) or 0))
    reference = _execution_reference(side=side, market_data=market_data)
    if reference is None:
        return _fill_evaluation(
            status="OPEN",
            filled_quantity=0,
            submitted_quantity=submitted_quantity,
            fill_price=None,
            market_data=market_data,
            executable=False,
            triggered=False,
            reason="weighted_voting.local_paper.fill_waiting_for_point_in_time_market_data",
        )

    triggered = _stop_triggered(intent, side=side, reference=reference, order_type=order_type)
    executable = _order_executable(intent, side=side, reference=reference, order_type=order_type, triggered=triggered)
    filled_quantity = _liquidity_capped_quantity(intent, executable=executable)
    costs = _execution_costs(intent, side=side, market_price=float(reference["price"]), quantity=filled_quantity)
    status = "OPEN"
    if 0 < filled_quantity < submitted_quantity:
        status = "PARTIALLY_FILLED"
    elif filled_quantity >= submitted_quantity and submitted_quantity > 0:
        status = "FILLED"
    reason = "weighted_voting.local_paper.fill_simulated_from_market_data" if filled_quantity > 0 else _non_fill_reason(order_type=order_type, triggered=triggered)
    return _fill_evaluation(
        status=status,
        filled_quantity=filled_quantity,
        submitted_quantity=submitted_quantity,
        fill_price=costs["executionPrice"] if filled_quantity > 0 else None,
        market_data=market_data,
        executable=executable,
        triggered=triggered,
        reason=reason,
        reference=reference,
        execution_costs=costs if filled_quantity > 0 else None,
    )


def _fill_evaluation(
    *,
    status: str,
    filled_quantity: int,
    submitted_quantity: int,
    fill_price: float | None,
    market_data: dict[str, Any] | None,
    executable: bool,
    triggered: bool,
    reason: str,
    reference: dict[str, Any] | None = None,
    execution_costs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION,
        "status": status,
        "filledQuantity": int(filled_quantity),
        "remainingQuantity": max(0, int(submitted_quantity) - int(filled_quantity)),
        "fillPrice": fill_price,
        "marketReferencePrice": reference.get("price") if reference else None,
        "executable": bool(executable),
        "triggered": bool(triggered),
        "reference": reference,
        "executionCosts": execution_costs or _empty_execution_costs(),
        "marketData": market_data or {},
        "reasonCode": reason,
        "fallbackPolicy": "quote_nbbo_preferred_completed_bar_close_only_no_intrabar_lookahead",
    }


def _execution_costs(intent: Any, *, side: str, market_price: float, quantity: int) -> dict[str, Any]:
    settings = _execution_cost_settings(intent)
    slippage = float(settings["buySlippagePerShare"] if side == "BUY" else settings["sellSlippagePerShare"])
    spread_impact = float(settings["spreadImpactPerShare"]) + (market_price * float(settings["spreadImpactPercent"]))
    adverse_per_share = max(0.0, slippage + spread_impact)
    execution_price = market_price + adverse_per_share if side == "BUY" else max(0.01, market_price - adverse_per_share)
    commission = max(float(settings["minimumCommission"]), int(quantity) * float(settings["commissionPerShare"])) if quantity > 0 else 0.0
    regulatory = int(quantity) * float(settings["regulatoryFeePerShare"]) if quantity > 0 else 0.0
    total_cost = round((adverse_per_share * int(quantity)) + commission + regulatory, 10)
    return {
        "marketPrice": round(market_price, 10),
        "executionPrice": round(execution_price, 10),
        "slippagePerShare": round(slippage, 10),
        "spreadImpactPerShare": round(spread_impact, 10),
        "adversePriceAdjustmentPerShare": round(adverse_per_share, 10),
        "commission": round(commission, 10),
        "regulatoryFees": round(regulatory, 10),
        "totalExecutionCost": total_cost,
        "configuration": settings,
        "reasonCode": "weighted_voting.local_paper.execution_costs_applied",
    }


def _execution_cost_settings(intent: Any) -> dict[str, float]:
    settings = getattr(intent, "settingsSnapshot", None)
    sources: list[dict[str, Any]] = []
    if isinstance(settings, dict):
        local_paper = settings.get("localPaper")
        if isinstance(local_paper, dict):
            costs = local_paper.get("executionCosts")
            if isinstance(costs, dict):
                sources.append(costs)
            sources.append(local_paper)
        for key in ("localPaperExecutionCosts", "weightedVotingLocalPaperExecutionCosts", "executionCosts", "transactionCostAssumptions"):
            value = settings.get(key)
            if isinstance(value, dict):
                sources.append(value)
        sources.append(settings)
    raw: dict[str, Any] = {}
    for source in sources:
        raw.update(source)
    entry_slippage = _nonnegative_float(
        raw,
        "weighted_voting.local_paper.slippage",
        "weighted_voting.local_paper.execution_costs.buy_slippage_per_share",
        "entrySlippagePerShare",
        "slippagePerShare",
        "slippage",
        "local_paper_buy_slippage_per_share",
        "buySlippagePerShare",
        default=0.0,
    )
    return {
        "buySlippagePerShare": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.execution_costs.buy_slippage_per_share",
            "buySlippagePerShare",
            "local_paper_buy_slippage_per_share",
            default=entry_slippage,
        ),
        "sellSlippagePerShare": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.execution_costs.sell_slippage_per_share",
            "sellSlippagePerShare",
            "local_paper_sell_slippage_per_share",
            default=entry_slippage,
        ),
        "commissionPerShare": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.commission",
            "weighted_voting.local_paper.execution_costs.commission_per_share",
            "commission",
            "commissionPerShare",
            "feePerShare",
            "fee_per_share",
            "local_paper_commission_per_share",
            default=0.0,
        ),
        "minimumCommission": _nonnegative_float(raw, "minimumCommission", "minimum_commission", default=0.0),
        "regulatoryFeePerShare": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.execution_costs.regulatory_fee_per_share",
            "regulatoryFeePerShare",
            "regulatoryFeesPerShare",
            "local_paper_regulatory_fee_per_share",
            default=0.0,
        ),
        "spreadImpactPerShare": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.execution_costs.spread_impact_per_share",
            "spreadImpactPerShare",
            "local_paper_spread_impact_per_share",
            default=0.0,
        ),
        "spreadImpactPercent": _nonnegative_float(
            raw,
            "weighted_voting.local_paper.execution_costs.spread_impact_percent",
            "spreadImpactPercent",
            "costBufferPercent",
            "local_paper_spread_impact_percent",
            default=0.0,
        ),
    }


def _nonnegative_float(payload: dict[str, Any], *keys: str, default: float) -> float:
    for key in keys:
        if key in payload:
            value = _positive_or_zero_float(payload.get(key))
            if value is not None:
                return value
    return float(default)


def _positive_or_zero_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, number)


def _empty_execution_costs() -> dict[str, Any]:
    return {
        "marketPrice": None,
        "executionPrice": None,
        "slippagePerShare": 0.0,
        "spreadImpactPerShare": 0.0,
        "adversePriceAdjustmentPerShare": 0.0,
        "commission": 0.0,
        "regulatoryFees": 0.0,
        "totalExecutionCost": 0.0,
        "configuration": {},
    }


def _execution_reference(*, side: str, market_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not market_data:
        return None
    source = market_data.get("source")
    if source == "quote":
        field = "ask" if side == "BUY" else "bid"
        price = _positive_float(market_data.get(field))
        if price is None:
            return None
        return {"source": "quote", "field": field, "price": price, "timestamp": market_data.get("timestamp")}
    if source == "bar":
        price = _positive_float(market_data.get("close"))
        if price is None:
            return None
        return {"source": "completed_bar_close", "field": "close", "price": price, "timestamp": market_data.get("timestamp"), "barEndTimestamp": market_data.get("barEndTimestamp")}
    return None


def _order_executable(intent: Any, *, side: str, reference: dict[str, Any], order_type: str, triggered: bool) -> bool:
    price = float(reference["price"])
    if order_type == "LIMIT":
        return _limit_executable(intent, side=side, price=price)
    if order_type == "STOP":
        return triggered
    if order_type == "STOP_LIMIT":
        return triggered and _limit_executable(intent, side=side, price=price)
    return False


def _limit_executable(intent: Any, *, side: str, price: float) -> bool:
    limit_price = _positive_float(getattr(intent, "limitPrice", None) or getattr(intent, "stopLimitPrice", None))
    if limit_price is None:
        return False
    if side == "SELL":
        return price >= limit_price
    return price <= limit_price


def _stop_triggered(intent: Any, *, side: str, reference: dict[str, Any], order_type: str) -> bool:
    if order_type == "LIMIT":
        return True
    stop_price = _positive_float(getattr(intent, "stopPrice", None) or getattr(intent, "triggerPrice", None))
    if stop_price is None:
        return False
    price = float(reference["price"])
    if side == "SELL":
        return price <= stop_price
    return price >= stop_price


def _non_fill_reason(*, order_type: str, triggered: bool) -> str:
    if order_type in {"STOP", "STOP_LIMIT"} and not triggered:
        return "weighted_voting.local_paper.stop_not_triggered"
    return "weighted_voting.local_paper.order_not_executable_at_market_price"


def _market_data_for_intent(store: Any, provider: Any | None, intent: Any, *, submitted_at: datetime) -> dict[str, Any] | None:
    symbol = str(getattr(intent, "symbol", "SPY") or "SPY").upper()
    settings = getattr(intent, "settingsSnapshot", None)
    candidates = []
    if isinstance(settings, dict):
        candidates.extend(
            (
                settings.get("localPaperQuote"),
                settings.get("weightedVotingLocalPaperQuote"),
                settings.get("nbbo"),
                settings.get("quote"),
                settings.get("localPaperBar"),
                settings.get("weightedVotingLocalPaperBar"),
                settings.get("bar"),
                settings.get("candle"),
            )
        )
        market_data = settings.get("marketData")
        if isinstance(market_data, dict):
            candidates.extend((market_data.get("quote"), market_data.get("nbbo"), market_data.get("bar"), market_data.get("candle")))
    provided = _provider_market_data(provider, symbol=symbol, submitted_at=submitted_at, intent=intent)
    candidates.insert(0, provided)
    for key in _market_data_store_keys(symbol):
        try:
            candidates.append(store.read_snapshot(key))
        except (AttributeError, KeyError):
            pass
    for candidate in candidates:
        normalized = _normalize_market_data(candidate, submitted_at=submitted_at)
        if normalized is not None:
            return normalized
    return None


def _provider_market_data(provider: Any | None, *, symbol: str, submitted_at: datetime, intent: Any) -> Any | None:
    if provider is None:
        return None
    for kwargs in (
        {"symbol": symbol, "as_of": submitted_at},
        {"symbol": symbol, "submitted_at": submitted_at},
        {"intent": intent, "submitted_at": submitted_at},
    ):
        try:
            return provider(**kwargs) if callable(provider) else None
        except TypeError:
            continue
    return None


def _normalize_market_data(raw: Any, *, submitted_at: datetime) -> dict[str, Any] | None:
    if raw is None:
        return None
    payload = dict(raw) if isinstance(raw, dict) else _object_payload(raw)
    if not payload:
        return None
    quote = payload.get("quote") or payload.get("nbbo")
    if isinstance(quote, dict):
        normalized_quote = _normalize_quote(quote, submitted_at=submitted_at)
        if normalized_quote:
            return normalized_quote
    if any(key in payload for key in ("bid", "ask", "bidPrice", "askPrice")):
        normalized_quote = _normalize_quote(payload, submitted_at=submitted_at)
        if normalized_quote:
            return normalized_quote
    bar = payload.get("bar") or payload.get("candle")
    if isinstance(bar, dict):
        normalized_bar = _normalize_bar(bar, submitted_at=submitted_at)
        if normalized_bar:
            return normalized_bar
    if any(key in payload for key in ("open", "high", "low", "close")):
        normalized_bar = _normalize_bar(payload, submitted_at=submitted_at)
        if normalized_bar:
            return normalized_bar
    return None


def _normalize_quote(payload: dict[str, Any], *, submitted_at: datetime) -> dict[str, Any] | None:
    observed_at = _market_timestamp(payload, "timestamp", "quoteTimestamp", "observedAt", "receivedAt")
    if observed_at is not None and observed_at > submitted_at:
        return None
    bid = _positive_float(payload.get("bid") or payload.get("bidPrice"))
    ask = _positive_float(payload.get("ask") or payload.get("askPrice"))
    if bid is None or ask is None or ask < bid:
        return None
    return {
        "source": "quote",
        "bid": bid,
        "ask": ask,
        "timestamp": (observed_at or submitted_at).isoformat(),
        "reasonCode": "weighted_voting.local_paper.nbbo_quote_fill_reference",
    }


def _normalize_bar(payload: dict[str, Any], *, submitted_at: datetime) -> dict[str, Any] | None:
    observed_at = _market_timestamp(payload, "timestamp", "barTimestamp", "candleTimestamp", "start")
    bar_end = _market_timestamp(payload, "barEndTimestamp", "end", "closeTimestamp")
    if bar_end is None and observed_at is not None:
        bar_end = observed_at + _bar_duration(payload)
    if bar_end is None or bar_end > submitted_at:
        return None
    close = _positive_float(payload.get("close"))
    if close is None:
        return None
    return {
        "source": "bar",
        "close": close,
        "timestamp": (observed_at or bar_end).isoformat(),
        "barEndTimestamp": bar_end.isoformat(),
        "reasonCode": "weighted_voting.local_paper.completed_bar_close_conservative_fill_reference",
    }


def _bar_duration(payload: dict[str, Any]) -> timedelta:
    raw = str(payload.get("timeframe") or payload.get("barTimeframe") or "1Min").lower()
    if "5" in raw:
        return timedelta(minutes=5)
    if "15" in raw:
        return timedelta(minutes=15)
    if "hour" in raw or raw in {"1h", "60min"}:
        return timedelta(hours=1)
    return timedelta(minutes=1)


def _market_timestamp(payload: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = payload.get(key)
        parsed = _utc(value)
        if parsed is not None:
            return parsed
    return None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _object_payload(raw: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("bid", "ask", "bidPrice", "askPrice", "timestamp", "quoteTimestamp", "observedAt", "open", "high", "low", "close", "barEndTimestamp", "timeframe"):
        if hasattr(raw, key):
            result[key] = getattr(raw, key)
    return result


def _market_data_store_keys(symbol: str) -> tuple[str, ...]:
    return (
        f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.market_data.{symbol}.quote",
        f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.market_data.{symbol}.bar",
    )


def _lifecycle_for_status(status: str) -> list[str]:
    normalized = str(status or "").upper()
    if normalized == "PENDING":
        return ["PENDING"]
    if normalized == "ACCEPTED":
        return ["PENDING", "ACCEPTED"]
    if normalized == "OPEN":
        return ["PENDING", "ACCEPTED", "OPEN"]
    if normalized == "PARTIALLY_FILLED":
        return ["PENDING", "ACCEPTED", "OPEN", "PARTIALLY_FILLED"]
    if normalized == "FILLED":
        return ["PENDING", "ACCEPTED", "OPEN", "FILLED"]
    if normalized == "REJECTED":
        return ["PENDING", "REJECTED"]
    if normalized in {"CANCELED", "EXPIRED", "REPLACED"}:
        return ["PENDING", "ACCEPTED", "OPEN", normalized]
    return ["PENDING", "ACCEPTED", "OPEN"]


def _status_reason_code(status: str) -> str:
    normalized = str(status or "").upper()
    if normalized == "FILLED":
        return "weighted_voting.local_paper.order_filled_locally"
    if normalized == "PARTIALLY_FILLED":
        return "weighted_voting.local_paper.order_partially_filled_locally"
    if normalized == "OPEN":
        return "weighted_voting.local_paper.order_open_locally"
    if normalized == "ACCEPTED":
        return "weighted_voting.local_paper.order_accepted_locally"
    if normalized == "PENDING":
        return "weighted_voting.local_paper.order_pending_locally"
    if normalized == "CANCELED":
        return "weighted_voting.local_paper.order_canceled_locally"
    if normalized == "EXPIRED":
        return "weighted_voting.local_paper.order_expired_locally"
    if normalized == "REPLACED":
        return "weighted_voting.local_paper.order_replaced_locally"
    if normalized == "REJECTED":
        return "weighted_voting.local_paper.order_rejected_locally"
    return "weighted_voting.local_paper.order_open_locally"


def _rejected_ack(intent: Any, reason: str) -> PaperGatewayBrokerAck:
    return PaperGatewayBrokerAck(
        clientOrderId=str(getattr(intent, "clientOrderId", "weighted-voting-local-paper-rejected")),
        brokerOrderId=None,
        status="REJECTED",
        acceptedAt=None,
        rejectedReason=reason,
    )


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _store_items(store: Any) -> tuple[tuple[str, dict], ...]:
    snapshots = getattr(store, "snapshots", None)
    if not isinstance(snapshots, dict):
        return ()
    return tuple((str(key), value) for key, value in snapshots.items() if isinstance(value, dict))


def _order_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.{client_order_id}"


def _order_index_key(order_intent_id: str) -> str:
    return f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.order_index.{order_intent_id}"


def _fill_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills.{client_order_id}"


def _protective_order_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.{client_order_id}"


__all__ = [
    "WEIGHTED_VOTING_LOCAL_PAPER_BROKER_VERSION",
    "WEIGHTED_VOTING_LOCAL_PAPER_FILL_ENGINE_VERSION",
    "WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE",
    "SUPPORTED_WEIGHTED_VOTING_LOCAL_ORDER_TYPES",
    "WEIGHTED_VOTING_LOCAL_ORDER_LIFECYCLE_STATUSES",
    "WeightedVotingLocalPaperAccount",
    "WeightedVotingLocalPaperBroker",
    "WeightedVotingLocalPaperRiskPort",
    "WeightedVotingLocalPaperRiskService",
    "build_weighted_voting_local_paper_gateway_dependencies",
]
