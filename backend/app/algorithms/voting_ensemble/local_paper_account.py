from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Protocol

from backend.app.domain.models import Signal, _require_utc
from backend.app.execution import PaperGatewayFill
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, aggregate_global_account_risk


VOTING_ENSEMBLE_ALGORITHM_ID = "voting_ensemble"
VOTING_ENSEMBLE_CAPITAL_PARTITION_ID = "voting_ensemble.paper.default"


VOTING_ENSEMBLE_LONG_ONLY_BUYING_POWER_MODEL = "LOCAL_CASH_NO_MARGIN_LONG_ONLY"
VOTING_ENSEMBLE_LONG_SHORT_BUYING_POWER_MODEL = "LOCAL_CASH_NO_MARGIN_LONG_AND_SHORT"


def _holding_minutes_from_intent(intent: Any) -> int | None:
    """The maximum holding minutes an intent carries, if it carries one.

    Gateway intents keep it on their settings snapshot; engine-internal exit intents and
    tests may set it directly. None when neither says, so the caller can fall back to the
    algorithm default rather than to a made-up number.
    """
    direct = getattr(intent, "maximumHoldingMinutes", None)
    snapshot = getattr(intent, "settingsSnapshot", None)
    candidate = direct if direct is not None else (snapshot.get("maximumHoldingMinutes") if isinstance(snapshot, Mapping) else None)
    try:
        minutes = int(candidate) if candidate is not None else None
    except (TypeError, ValueError):
        return None
    return minutes if minutes is not None and minutes > 0 else None


def _buying_power_model(allow_shorts: bool) -> str:
    """Label the buying-power model actually in force.

    Short exposure consumes the same cash buying power as long exposure, one for one:
    the account still carries no margin and no leverage, so a short is sized against
    cash exactly as a long is.
    """
    return VOTING_ENSEMBLE_LONG_SHORT_BUYING_POWER_MODEL if allow_shorts else VOTING_ENSEMBLE_LONG_ONLY_BUYING_POWER_MODEL


VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID = "voting_ensemble.paper.default.account"
VOTING_ENSEMBLE_LOCAL_PAPER_ACCOUNT_VERSION = "voting_ensemble_local_paper_account_v2"
VOTING_ENSEMBLE_LOCAL_INVENTORY_MANIFEST_VERSION = "voting_ensemble_local_inventory_manifest_v1"
VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH = Decimal("100000")
VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY = "voting_ensemble_local_paper_account"
VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE = "voting_ensemble.paper_execution"
VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE = "voting_ensemble.paper_gateway"


class VotingEnsembleLedgerStore(Protocol):
    snapshots: dict[str, dict[str, Any]]

    def read_snapshot(self, key: str) -> dict[str, Any]: ...

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class VotingEnsemblePaperAccount:
    initialCash: Decimal
    cash: Decimal
    equity: Decimal
    buyingPower: Decimal
    realizedPnl: Decimal
    realizedPnlToday: Decimal
    unrealizedPnl: Decimal
    dailyNetPnl: Decimal
    intradayEquityHigh: Decimal
    drawdownDollars: Decimal
    drawdownPercent: Decimal
    openPositionNotional: Decimal
    grossExposure: Decimal
    netExposure: Decimal
    totalOpenRiskDollars: Decimal
    totalOpenRiskPercent: Decimal
    tradesToday: int
    sessionDate: date
    lastMarkPrice: Decimal | None
    lastMarkedAt: datetime | None
    observedAt: datetime
    appliedFillIds: tuple[str, ...] = ()
    usableEntryBuyingPower: Decimal = Decimal("0")
    allowLeverage: bool = False
    allowMargin: bool = False
    allowShorts: bool = True
    maxLeverage: Decimal = Decimal("1")
    version: str = VOTING_ENSEMBLE_LOCAL_PAPER_ACCOUNT_VERSION
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        return _owned_record(
            {
                "schemaVersion": self.version,
                "version": self.version,
                "algorithm_id": self.algorithmId,
                "initialCash": _money(self.initialCash),
                "cash": _money(self.cash),
                "cashBalance": _money(self.cash),
                "equity": _money(self.equity),
                "buyingPower": _money(self.buyingPower),
                "usableEntryBuyingPower": _money(self.usableEntryBuyingPower),
                "cashBuyingPower": _money(self.usableEntryBuyingPower),
                "marginBuyingPower": 0.0,
                "allowLeverage": bool(self.allowLeverage),
                "allowMargin": bool(self.allowMargin),
                "allowShorts": bool(self.allowShorts),
                "maxLeverage": _percent(self.maxLeverage),
                "buyingPowerModel": _buying_power_model(self.allowShorts),
                "equityModel": "cash_plus_local_owned_position_market_value",
                "realizedPnl": _money(self.realizedPnl),
                "realizedPnlToday": _money(self.realizedPnlToday),
                "unrealizedPnl": _money(self.unrealizedPnl),
                "unrealizedPnlToday": _money(self.unrealizedPnl),
                "dailyNetPnl": _money(self.dailyNetPnl),
                "dailyNetPnlAfterExitCosts": _money(self.dailyNetPnl),
                "intradayEquityHigh": _money(self.intradayEquityHigh),
                "drawdownDollars": _money(self.drawdownDollars),
                "drawdownPercent": _percent(self.drawdownPercent),
                "drawdownFromIntradayHighPercent": _percent(self.drawdownPercent),
                "openPositionNotional": _money(self.openPositionNotional),
                "grossExposure": _money(self.grossExposure),
                "netExposure": _money(self.netExposure),
                "totalOpenRiskDollars": _money(self.totalOpenRiskDollars),
                "totalOpenRiskPercent": _percent(self.totalOpenRiskPercent),
                "tradesToday": int(self.tradesToday),
                "sessionDate": self.sessionDate.isoformat(),
                "lastMarkPrice": _price(self.lastMarkPrice) if self.lastMarkPrice is not None else None,
                "lastMarkedAt": _iso(self.lastMarkedAt) if self.lastMarkedAt is not None else None,
                "appliedFillIds": list(self.appliedFillIds),
                "observedAt": _iso(self.observedAt),
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "paperOnly": True,
                "readOnlyExternalBrokerState": True,
                "reasonCodes": reason_codes,
            }
        )


@dataclass(frozen=True)
class VotingEnsemblePosition:
    symbol: str
    quantity: int
    averagePrice: float
    markPrice: float
    notional: float
    unrealizedPnl: float
    realizedPnl: float
    updatedAt: datetime
    openedAt: datetime | None = None
    stopPrice: float | None = None
    profitTargetPrice: float | None = None
    entryOrderId: str | None = None
    entryFillIds: tuple[str, ...] = ()
    lastFillId: str | None = None
    lastMarkedAt: datetime | None = None
    markPricePolicy: str | None = None
    marketDataFresh: bool = True
    quoteAgeSeconds: float | None = None
    marketDataReceiptAgeSeconds: float | None = None
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    positionOwner: str = VOTING_ENSEMBLE_ALGORITHM_ID
    exitOwner: str = VOTING_ENSEMBLE_ALGORITHM_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        status = "OPEN" if self.quantity else "FLAT"
        side = "LONG" if self.quantity > 0 else "SHORT" if self.quantity < 0 else "FLAT"
        return _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_position_v1",
                **asdict(self),
                "algorithm_id": self.algorithmId,
                "symbol": self.symbol.upper(),
                "quantity": int(self.quantity),
                "signedQuantity": int(self.quantity),
                "side": side,
                "averagePrice": round(float(self.averagePrice), 6),
                "averageEntryPrice": round(float(self.averagePrice), 6),
                "markPrice": round(float(self.markPrice), 6),
                "notional": round(float(self.notional), 6),
                "marketValue": round(float(self.notional), 6),
                "unrealizedPnl": round(float(self.unrealizedPnl), 6),
                "realizedPnl": round(float(self.realizedPnl), 6),
                "openedAt": _iso(self.openedAt) if self.openedAt is not None else None,
                "entryFillIds": list(self.entryFillIds),
                "lastMarkedAt": _iso(self.lastMarkedAt) if self.lastMarkedAt is not None else None,
                "markPricePolicy": self.markPricePolicy,
                "marketDataFresh": bool(self.marketDataFresh),
                "quoteAgeSeconds": round(float(self.quoteAgeSeconds), 6) if self.quoteAgeSeconds is not None else None,
                "marketDataReceiptAgeSeconds": round(float(self.marketDataReceiptAgeSeconds), 6) if self.marketDataReceiptAgeSeconds is not None else None,
                "status": status,
                "updatedAt": _iso(self.updatedAt),
                "source": "voting_ensemble.local_paper_account.positions",
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": reason_codes,
            }
        )


@dataclass(frozen=True)
class VotingEnsemblePaperOrder:
    clientOrderId: str
    orderIntentId: str
    decisionId: str
    symbol: str
    side: Signal
    orderType: str
    quantity: int
    filledQuantity: int
    entryPrice: float
    submittedAt: datetime
    status: str = "OPEN"
    triggerPrice: float | None = None
    stopPrice: float | None = None
    targetPrice: float | None = None
    averageFillPrice: float | None = None
    filledAt: datetime | None = None
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        payload = _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_order_v1",
                **asdict(self),
                "algorithm_id": self.algorithmId,
                "symbol": self.symbol.upper(),
                "side": self.side.value if isinstance(self.side, Signal) else str(self.side),
                "orderType": self.orderType.upper(),
                "submittedAt": _iso(self.submittedAt),
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": reason_codes,
            }
        )
        if self.filledAt is not None:
            payload["filledAt"] = _iso(self.filledAt)
        return payload


@dataclass(frozen=True)
class VotingEnsembleFill:
    clientOrderId: str
    orderIntentId: str
    symbol: str
    side: Signal
    filledQuantity: int
    averageFillPrice: float
    filledAt: datetime
    realizedPnl: float
    closedQuantity: int
    grossNotional: float
    feeAmount: float
    status: str = "FILLED"
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        return _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_fill_v1",
                **asdict(self),
                "algorithm_id": self.algorithmId,
                "symbol": self.symbol.upper(),
                "side": self.side.value if isinstance(self.side, Signal) else str(self.side),
                "averageFillPrice": round(float(self.averageFillPrice), 6),
                "grossNotional": _money(self.grossNotional),
                "feeAmount": _money(self.feeAmount),
                "realizedPnl": round(float(self.realizedPnl), 6),
                "filledAt": _iso(self.filledAt),
                "source": "voting_ensemble.local_paper_account.fill",
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": reason_codes,
            }
        )


@dataclass(frozen=True)
class VotingEnsembleClosedTrade:
    closedTradeId: str
    clientOrderId: str
    orderIntentId: str
    symbol: str
    side: Signal
    quantity: int
    averageEntryPrice: float
    exitPrice: float
    realizedPnl: float
    closedAt: datetime
    entryOrderId: str | None = None
    exitOrderId: str | None = None
    entryFillIds: tuple[str, ...] = ()
    exitFillId: str | None = None
    associatedOrderIds: tuple[str, ...] = ()
    associatedFillIds: tuple[str, ...] = ()
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        return _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_closed_trade_v1",
                **asdict(self),
                "algorithm_id": self.algorithmId,
                "symbol": self.symbol.upper(),
                "side": self.side.value if isinstance(self.side, Signal) else str(self.side),
                "averageEntryPrice": round(float(self.averageEntryPrice), 6),
                "exitPrice": round(float(self.exitPrice), 6),
                "realizedPnl": round(float(self.realizedPnl), 6),
                "closedAt": _iso(self.closedAt),
                "entryOrderId": self.entryOrderId,
                "exitOrderId": self.exitOrderId,
                "entryFillIds": list(self.entryFillIds),
                "exitFillId": self.exitFillId,
                "associatedOrderIds": list(self.associatedOrderIds),
                "associatedFillIds": list(self.associatedFillIds),
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": reason_codes,
            }
        )


@dataclass(frozen=True)
class VotingEnsembleAccountRiskSnapshot:
    accountRiskState: dict[str, Any]
    brokerState: dict[str, Any]
    riskState: dict[str, Any]
    observedAt: datetime
    sessionDate: date
    accountId: str = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    capitalPartitionId: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    executionMode: str = "LOCAL_PAPER"

    def to_record(self, *, reason_codes: list[str]) -> dict[str, Any]:
        return _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_risk_snapshot_v1",
                **asdict(self),
                "algorithm_id": self.algorithmId,
                "observedAt": _iso(self.observedAt),
                "sessionDate": self.sessionDate.isoformat(),
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": reason_codes,
            }
        )


class VotingEnsembleInventoryLedger:
    """Canonical mutable ledger for Voting Ensemble LOCAL_PAPER account state."""

    def __init__(self, store: VotingEnsembleLedgerStore) -> None:
        self.store = store

    def account_snapshot(self, *, observed_at: datetime | None = None) -> dict[str, Any]:
        observed = _require_utc(observed_at or datetime.now(UTC))
        existing = self.store.snapshots.get(_execution_key("local_account.latest"))
        if isinstance(existing, dict):
            enriched = self._enriched_account_payload(existing, observed_at=observed, reason_codes=["voting_ensemble.local_paper_account.enriched_from_existing_balance"])
            if _account_requires_upgrade(existing) and getattr(self.store, "lastPersistenceError", None) is None:
                self.store.write_snapshot("local_account.latest", enriched)
                self.persist_inventory_manifest(observed_at=observed)
            elif getattr(self.store, "lastPersistenceError", None) is None:
                self.persist_inventory_manifest(observed_at=observed)
            return enriched
        account = self._account_payload(
            initial_cash=_configured_initial_cash(),
            cash=_configured_initial_cash(),
            realized_pnl=0.0,
            observed_at=observed,
            reason_codes=["voting_ensemble.local_paper_account.initialized"],
        )
        if getattr(self.store, "lastPersistenceError", None) is not None:
            return account
        self.store.write_snapshot("local_account.latest", account)
        self.persist_inventory_manifest(observed_at=observed)
        return account

    def broker_account_snapshot(self, *, observed_at: datetime | None = None) -> BrokerAccountSnapshot:
        observed = _require_utc(observed_at or datetime.now(UTC))
        account = self.account_snapshot(observed_at=observed)
        return BrokerAccountSnapshot(
            accountId=str(account["accountId"]),
            equity=float(account.get("equity") or 0.0),
            buyingPower=float(account.get("buyingPower") or 0.0),
            realizedPnlToday=float(account.get("realizedPnlToday") or 0.0),
            intradayEquityHigh=float(account.get("intradayEquityHigh") or account.get("equity") or 0.0),
            positions=[self._broker_position(position, observed_at=observed) for position in self.positions(include_flat=False)],
            pendingOrders=[
                self._broker_order(order)
                for order in self.orders()
                if str(order.get("status") or "").upper() in {"PENDING", "ACCEPTED", "PARTIALLY_FILLED", "NEW"}
            ],
            partiallyFilledOrders=[],
            observedAt=observed,
            sessionDate=observed.date(),
            sourceAuthority="local_ui_history",
            positionsReconciled=True,
            openOrdersReconciled=True,
        )

    def create_order(self, intent: Any, *, observed_at: datetime) -> dict[str, Any]:
        side = Signal(getattr(intent, "side"))
        order = VotingEnsemblePaperOrder(
            clientOrderId=str(intent.clientOrderId),
            orderIntentId=str(intent.orderIntentId),
            decisionId=str(intent.decisionId),
            symbol=str(intent.symbol).upper(),
            side=side,
            orderType=str(intent.orderType).upper(),
            quantity=int(intent.submittedQuantity),
            filledQuantity=0,
            entryPrice=float(intent.limitPrice or intent.triggerPrice or 0.01),
            triggerPrice=getattr(intent, "triggerPrice", None),
            stopPrice=getattr(intent, "stopPrice", None),
            targetPrice=getattr(intent, "targetPrice", None),
            submittedAt=_require_utc(observed_at),
        )
        payload = order.to_record(reason_codes=["voting_ensemble.local_paper.order_accepted_locally"])
        exit_reason = getattr(intent, "exitReason", None)
        if exit_reason:
            payload["exitReason"] = str(exit_reason)
            payload["reasonCodes"] = [*list(payload.get("reasonCodes") or ()), "voting_ensemble.local_paper.risk_reducing_exit_order_accepted_locally"]
        # The holding limit travels on the gateway record's settings snapshot. Keeping it
        # on the local order is what lets the maintenance loop enforce a time stop for
        # the position this order opens, instead of a default it has to guess at.
        holding_minutes = _holding_minutes_from_intent(intent)
        if holding_minutes is not None:
            payload["maximumHoldingMinutes"] = holding_minutes
        self.store.write_snapshot(f"local_order.{order.clientOrderId}", payload)
        self.persist_inventory_manifest(observed_at=observed_at)
        return payload

    def mark_order_filled(self, client_order_id: str, fill: PaperGatewayFill) -> dict[str, Any]:
        order = self.store.read_snapshot(f"local_order.{client_order_id}")
        updated = {
            **order,
            "status": "FILLED" if min(int(order.get("quantity") or 0), int(order.get("filledQuantity") or 0) + int(fill.filledQuantity)) >= int(order.get("quantity") or 0) else "PARTIALLY_FILLED",
            "filledQuantity": min(int(order.get("quantity") or 0), int(order.get("filledQuantity") or 0) + int(fill.filledQuantity)),
            "averageFillPrice": fill.averageFillPrice,
            "filledAt": _iso(fill.filledAt),
            "reasonCodes": [*list(order.get("reasonCodes") or ()), "voting_ensemble.local_paper.order_filled_locally"],
        }
        self.store.write_snapshot(f"local_order.{client_order_id}", updated)
        self.persist_inventory_manifest(observed_at=fill.filledAt)
        return self.store.read_snapshot(f"local_order.{client_order_id}")

    def cancel_order(self, client_order_id: str, *, canceled_at: datetime) -> bool:
        try:
            order = self.store.read_snapshot(f"local_order.{client_order_id}")
        except KeyError:
            return False
        if int(order.get("filledQuantity") or 0) > 0:
            return False
        self.store.write_snapshot(
            f"local_order.{client_order_id}",
            {
                **order,
                "status": "CANCELED",
                "updatedAt": _iso(_require_utc(canceled_at)),
                "reasonCodes": [*list(order.get("reasonCodes") or ()), "voting_ensemble.local_paper.order_canceled_locally"],
            },
        )
        self.persist_inventory_manifest(observed_at=canceled_at)
        return True

    def mark_open_positions_from_market_data(
        self,
        *,
        symbol: str,
        nbbo: Mapping[str, Any] | None,
        observed_at: datetime,
    ) -> dict[str, Any]:
        """Mark open LOCAL_PAPER positions using conservative liquidation NBBO.

        Policy: a long position is marked at the current bid because that is
        the conservative liquidation side. A short position is marked at the
        current ask because buy-to-cover liquidation pays the offer. Stale,
        crossed, missing, or future-dated quotes are recorded but never used to
        update account equity or unrealized P&L.
        """
        observed = _require_utc(observed_at)
        normalized_symbol = symbol.upper()
        mark = _mark_input_from_nbbo(normalized_symbol, nbbo, observed)
        status_payload = self._market_data_status_payload(
            symbol=normalized_symbol,
            mark=mark,
            observed_at=observed,
        )
        transaction = getattr(self.store, "transaction", None)
        if callable(transaction):
            with transaction():
                self.store.write_snapshot(f"local_market_data.{normalized_symbol}.latest", status_payload)
                if mark["fresh"]:
                    self._apply_mark_to_positions(normalized_symbol, mark=mark, observed_at=observed)
        else:
            self.store.write_snapshot(f"local_market_data.{normalized_symbol}.latest", status_payload)
            if mark["fresh"]:
                self._apply_mark_to_positions(normalized_symbol, mark=mark, observed_at=observed)
        self.persist_inventory_manifest(observed_at=observed)
        return {
            **status_payload,
            "positionsMarked": len([position for position in self.positions() if str(position.get("symbol") or "").upper() == normalized_symbol]),
        }

    def latest_market_data_status(self, symbol: str) -> dict[str, Any]:
        try:
            return self.store.read_snapshot(f"local_market_data.{symbol.upper()}.latest")
        except KeyError:
            return {}

    def local_mark_is_fresh_for_entries(self, symbol: str, *, evaluated_at: datetime) -> bool:
        status = self.latest_market_data_status(symbol)
        if not status:
            return True
        if not bool(status.get("fresh")):
            return False
        quote_timestamp = _parse_time(status.get("quoteTimestamp"))
        max_age = _decimal(status.get("maxQuoteAgeSeconds") or _configured_max_quote_age_seconds())
        if quote_timestamp is None:
            return False
        return Decimal(max(0.0, (_require_utc(evaluated_at) - quote_timestamp).total_seconds())) <= max_age

    def apply_fill(
        self,
        *,
        client_order_id: str,
        applied_fill_id: str | None = None,
        order_intent_id: str,
        symbol: str,
        side: Signal | str,
        requested_quantity: int,
        fill_price: float,
        filled_at: datetime,
    ) -> PaperGatewayFill | None:
        filled_at = _require_utc(filled_at)
        resolved_fill_id = _applied_fill_id(
            applied_fill_id=applied_fill_id,
            client_order_id=client_order_id,
            order_intent_id=order_intent_id,
            symbol=symbol,
            side=side,
            requested_quantity=requested_quantity,
            fill_price=fill_price,
            filled_at=filled_at,
        )
        if self._applied_fill_exists(resolved_fill_id):
            return self._existing_fill(client_order_id)
        transaction = getattr(self.store, "transaction", None)
        if callable(transaction):
            with transaction():
                if self._applied_fill_exists(resolved_fill_id):
                    return self._existing_fill(client_order_id)
                return self._apply_fill_once(
                    client_order_id=client_order_id,
                    applied_fill_id=resolved_fill_id,
                    order_intent_id=order_intent_id,
                    symbol=symbol,
                    side=side,
                    requested_quantity=requested_quantity,
                    fill_price=fill_price,
                    filled_at=filled_at,
                )
        return self._apply_fill_once(
            client_order_id=client_order_id,
            applied_fill_id=resolved_fill_id,
            order_intent_id=order_intent_id,
            symbol=symbol,
            side=side,
            requested_quantity=requested_quantity,
            fill_price=fill_price,
            filled_at=filled_at,
        )

    def _apply_fill_once(
        self,
        *,
        client_order_id: str,
        applied_fill_id: str,
        order_intent_id: str,
        symbol: str,
        side: Signal | str,
        requested_quantity: int,
        fill_price: float,
        filled_at: datetime,
    ) -> PaperGatewayFill | None:
        normalized_side = Signal(side)
        normalized_symbol = symbol.upper()
        quantity = max(0, int(requested_quantity))
        fill_price_decimal = _decimal(fill_price)
        if quantity <= 0 or fill_price_decimal <= 0:
            return None
        current_position = self.position_for_symbol(normalized_symbol)
        signed_position = int(current_position.get("quantity") or 0)
        average_price = _decimal(current_position.get("averagePrice") or fill_price_decimal)
        if normalized_side == Signal.SELL and signed_position > 0:
            quantity = min(quantity, signed_position)
        elif normalized_side == Signal.BUY and signed_position < 0:
            quantity = min(quantity, abs(signed_position))
        if quantity <= 0:
            return None

        signed_fill = quantity if normalized_side == Signal.BUY else -quantity
        gross_notional = fill_price_decimal * Decimal(quantity)
        fee_amount = _fill_fee(quantity, fill_price_decimal)
        closing_quantity = 0
        realized_pnl = Decimal("0")
        if signed_position and (signed_position > 0) != (signed_fill > 0):
            closing_quantity = min(abs(signed_position), abs(signed_fill))
            realized_pnl = (
                (fill_price_decimal - average_price) * Decimal(closing_quantity)
                if signed_position > 0
                else (average_price - fill_price_decimal) * Decimal(closing_quantity)
            )

        fill = VotingEnsembleFill(
            clientOrderId=client_order_id,
            orderIntentId=order_intent_id,
            symbol=normalized_symbol,
            side=normalized_side,
            filledQuantity=quantity,
            averageFillPrice=float(fill_price_decimal),
            filledAt=filled_at,
            realizedPnl=float(realized_pnl),
            closedQuantity=closing_quantity,
            grossNotional=float(gross_notional),
            feeAmount=float(fee_amount),
        )
        fill_payload = fill.to_record(reason_codes=["voting_ensemble.local_paper.fill_simulated_locally"])
        fill_payload["appliedFillId"] = applied_fill_id
        self.store.write_snapshot(
            f"paper_order_gateway.fill.{client_order_id}",
            fill_payload,
        )
        self._apply_position_update(
            symbol=normalized_symbol,
            signed_position=signed_position,
            average_price=average_price,
            signed_fill=signed_fill,
            fill_price=fill_price_decimal,
            filled_at=filled_at,
            client_order_id=client_order_id,
            stop_price=self._order_price(client_order_id, "stopPrice"),
            profit_target_price=self._order_price(client_order_id, "targetPrice"),
            realized_pnl=realized_pnl,
        )
        self._record_closed_trade_if_needed(
            fill=fill,
            average_entry_price=average_price,
            closing_quantity=closing_quantity,
            prior_position=current_position,
        )
        self._update_account_from_fill(
            side=normalized_side,
            quantity=quantity,
            fill_price=fill_price_decimal,
            fee_amount=fee_amount,
            realized_pnl=realized_pnl,
            observed_at=filled_at,
        )
        self.store.write_snapshot("local_risk_snapshot.latest", self.risk_snapshot_payload(observed_at=filled_at))
        self._record_applied_fill(
            applied_fill_id=applied_fill_id,
            client_order_id=client_order_id,
            order_intent_id=order_intent_id,
            symbol=normalized_symbol,
            side=normalized_side,
            quantity=quantity,
            fill_price=fill_price_decimal,
            filled_at=filled_at,
        )
        self.persist_inventory_manifest(observed_at=filled_at)
        return PaperGatewayFill(
            executionMode="LOCAL_PAPER",
            clientOrderId=client_order_id,
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            accountId=VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            orderIntentId=order_intent_id,
            symbol=normalized_symbol,
            side=normalized_side,
            filledQuantity=quantity,
            averageFillPrice=float(fill_price_decimal),
            status="FILLED",
            filledAt=filled_at,
        )

    def position_for_symbol(self, symbol: str) -> dict[str, Any]:
        try:
            position = self.store.read_snapshot(f"local_position.{symbol.upper()}")
        except KeyError:
            return {}
        if not _is_voting_ensemble_owned_position(position):
            return {}
        return dict(position)

    def quantity_for_symbol(self, symbol: str) -> int:
        return int(self.position_for_symbol(symbol).get("quantity") or 0)

    def positions(self, *, include_flat: bool = False) -> list[dict[str, Any]]:
        positions = [position for position in self._records("local_position.") if _is_voting_ensemble_owned_position(position)]
        if include_flat:
            return positions
        return [position for position in positions if int(position.get("quantity") or 0) != 0]

    def orders(self) -> list[dict[str, Any]]:
        return self._records("local_order.")

    def fills(self) -> list[dict[str, Any]]:
        return self._records("paper_order_gateway.fill.", namespace=VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE)

    def accounts(self) -> list[dict[str, Any]]:
        return self._records("local_account.")

    def closed_trades(self) -> list[dict[str, Any]]:
        return self._records("local_closed_trade.")

    def realized_pnl_records(self) -> list[dict[str, Any]]:
        return self._records("local_realized_pnl.")

    def risk_snapshots(self) -> list[dict[str, Any]]:
        return self._records("local_risk_snapshot.")

    def market_data_statuses(self) -> list[dict[str, Any]]:
        return self._records("local_market_data.")

    def applied_fill_ids(self) -> list[str]:
        return [str(record.get("appliedFillId")) for record in self._records("applied_fill.") if record.get("appliedFillId")]

    def persist_inventory_manifest(self, *, observed_at: datetime | None = None) -> dict[str, Any]:
        observed = _require_utc(observed_at or datetime.now(UTC))
        snapshots = dict(self.store.snapshots)
        latest_account = snapshots.get(_execution_key("local_account.latest"), {})
        session_date = str(latest_account.get("sessionDate") or observed.date().isoformat()) if isinstance(latest_account, Mapping) else observed.date().isoformat()
        trades_today = int(latest_account.get("tradesToday") or 0) if isinstance(latest_account, Mapping) else 0
        payload = _owned_record(
            {
                "schemaVersion": VOTING_ENSEMBLE_LOCAL_INVENTORY_MANIFEST_VERSION,
                "version": VOTING_ENSEMBLE_LOCAL_INVENTORY_MANIFEST_VERSION,
                "inventoryAuthority": "canonical_mutable_voting_ensemble_local_paper_inventory",
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "accountKey": _execution_key("local_account.latest"),
                "positionKeyPrefix": _execution_key("local_position."),
                "orderKeyPrefix": _execution_key("local_order."),
                "fillKeyPrefix": f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.",
                "appliedFillKeyPrefix": _execution_key("applied_fill."),
                "closedTradeKeyPrefix": _execution_key("local_closed_trade."),
                "realizedPnlKeyPrefix": _execution_key("local_realized_pnl."),
                "riskSnapshotKey": _execution_key("local_risk_snapshot.latest"),
                "tradeCountersKey": _execution_key("local_account.latest"),
                "sessionDateKey": _execution_key("local_account.latest"),
                "conceptualStorageKeys": {
                    "voting_ensemble.local_account.latest": _execution_key("local_account.latest"),
                    "voting_ensemble.inventory.positions": _execution_key("local_position."),
                    "voting_ensemble.local_orders": _execution_key("local_order."),
                    "voting_ensemble.local_fills": f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.",
                    "voting_ensemble.closed_trades": _execution_key("local_closed_trade."),
                    "voting_ensemble.applied_fill_ids": _execution_key("applied_fill."),
                },
                "currentPositionKeys": _owned_keys(snapshots, _execution_key("local_position.")),
                "localOrderKeys": _owned_keys(snapshots, _execution_key("local_order.")),
                "localFillKeys": _owned_keys(snapshots, f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill."),
                "appliedFillKeys": _owned_keys(snapshots, _execution_key("applied_fill.")),
                "closedTradeKeys": _owned_keys(snapshots, _execution_key("local_closed_trade.")),
                "realizedPnlKeys": _owned_keys(snapshots, _execution_key("local_realized_pnl.")),
                "riskSnapshotKeys": _owned_keys(snapshots, _execution_key("local_risk_snapshot.")),
                "appliedFillIds": self.applied_fill_ids(),
                "tradeCounters": {"tradesToday": trades_today},
                "sessionDate": session_date,
                "observedAt": _iso(observed),
                "reasonCodes": ["voting_ensemble.local_paper.persistence_manifest_recorded"],
            }
        )
        self.store.write_snapshot("local_inventory_manifest.latest", payload)
        return payload

    def _applied_fill_exists(self, applied_fill_id: str) -> bool:
        try:
            self.store.read_snapshot(f"applied_fill.{_stable_key(applied_fill_id)}")
        except KeyError:
            return False
        return True

    def _existing_fill(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            payload = self.store.read_snapshot(f"paper_order_gateway.fill.{client_order_id}")
        except KeyError:
            return None
        try:
            return PaperGatewayFill(
                executionMode=str(payload.get("executionMode") or "LOCAL_PAPER"),  # type: ignore[arg-type]
                clientOrderId=str(payload.get("clientOrderId") or client_order_id),
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                accountId=VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                orderIntentId=str(payload.get("orderIntentId") or payload.get("clientOrderId") or client_order_id),
                symbol=str(payload.get("symbol") or "SPY").upper(),
                side=Signal(payload.get("side") or Signal.BUY),
                filledQuantity=int(payload.get("filledQuantity") or 0),
                averageFillPrice=float(payload.get("averageFillPrice") or 0.01),
                status=str(payload.get("status") or "FILLED"),  # type: ignore[arg-type]
                filledAt=_parse_time(payload.get("filledAt")) or datetime.now(UTC),
            )
        except Exception:
            return None

    def _record_applied_fill(
        self,
        *,
        applied_fill_id: str,
        client_order_id: str,
        order_intent_id: str,
        symbol: str,
        side: Signal,
        quantity: int,
        fill_price: Decimal,
        filled_at: datetime,
    ) -> None:
        observed = datetime.now(UTC)
        self.store.write_snapshot(
            f"applied_fill.{_stable_key(applied_fill_id)}",
            _owned_record(
                {
                    "schemaVersion": "voting_ensemble_applied_fill_v1",
                    "appliedFillId": applied_fill_id,
                    "clientOrderId": client_order_id,
                    "orderIntentId": order_intent_id,
                    "symbol": symbol,
                    "side": side.value,
                    "filledQuantity": int(quantity),
                    "averageFillPrice": _price(fill_price),
                    "filledAt": _iso(filled_at),
                    "appliedAt": _iso(observed),
                    "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                    "reasonCodes": ["voting_ensemble.local_paper.fill_applied_once"],
                }
            ),
        )
        try:
            account = self.store.read_snapshot("local_account.latest")
        except KeyError:
            return
        applied_ids = list(dict.fromkeys([*list(account.get("appliedFillIds") or []), applied_fill_id]))
        self.store.write_snapshot(
            "local_account.latest",
            {
                **account,
                "appliedFillIds": applied_ids,
                "reasonCodes": [*list(account.get("reasonCodes") or ()), "voting_ensemble.local_paper.applied_fill_id_recorded"],
            },
        )

    def risk_snapshot_payload(self, *, observed_at: datetime) -> dict[str, Any]:
        observed = _require_utc(observed_at)
        risk = aggregate_global_account_risk(self.broker_account_snapshot(observed_at=observed), candidateSymbol="SPY")
        return VotingEnsembleAccountRiskSnapshot(
            accountRiskState=risk.accountRiskState.model_dump(mode="json"),
            brokerState=risk.brokerState,
            riskState=risk.riskState,
            observedAt=observed,
            sessionDate=observed.date(),
        ).to_record(reason_codes=["voting_ensemble.local_paper.risk_snapshot_recorded"])

    def _account_payload(
        self,
        *,
        cash: float | Decimal,
        realized_pnl: float | Decimal,
        observed_at: datetime,
        reason_codes: list[str],
        initial_cash: float | Decimal | None = None,
        equity: float | Decimal | None = None,
        unrealized_pnl: float | Decimal = 0.0,
        intraday_equity_high: float | Decimal | None = None,
    ) -> dict[str, Any]:
        observed = _require_utc(observed_at)
        initial = _decimal(initial_cash if initial_cash is not None else _configured_initial_cash())
        cash_value = _decimal(cash)
        positions = self.positions(include_flat=False)
        open_notional = sum((_abs_decimal(position.get("notional")) for position in positions), Decimal("0"))
        net_exposure = sum((_decimal(position.get("notional")) for position in positions), Decimal("0"))
        owned_market_value = _owned_position_market_value(positions)
        unrealized = _decimal(unrealized_pnl)
        realized = _decimal(realized_pnl)
        resolved_equity = _max_decimal(Decimal("0"), cash_value + owned_market_value if equity is None else _decimal(equity))
        max_leverage = Decimal("1")
        max_gross_exposure = resolved_equity * max_leverage
        remaining_exposure = _max_decimal(Decimal("0"), max_gross_exposure - open_notional)
        usable_entry_buying_power = _min_decimal(_max_decimal(Decimal("0"), cash_value), remaining_exposure)
        previous_high = _decimal(intraday_equity_high) if intraday_equity_high is not None else resolved_equity
        high = _max_decimal(previous_high, resolved_equity)
        drawdown = _max_decimal(Decimal("0"), high - resolved_equity)
        drawdown_percent = (drawdown / high * Decimal("100")) if high > 0 else Decimal("0")
        open_risk = self._total_open_risk_dollars(positions)
        open_risk_percent = (open_risk / resolved_equity * Decimal("100")) if resolved_equity > 0 else Decimal("0")
        last_mark_price, last_marked_at = self._last_mark(positions)
        session_date = observed.date()
        account = VotingEnsemblePaperAccount(
            initialCash=initial,
            cash=cash_value,
            equity=resolved_equity,
            buyingPower=usable_entry_buying_power,
            usableEntryBuyingPower=usable_entry_buying_power,
            realizedPnl=realized,
            realizedPnlToday=realized,
            unrealizedPnl=unrealized,
            dailyNetPnl=realized + unrealized,
            intradayEquityHigh=high,
            drawdownDollars=drawdown,
            drawdownPercent=drawdown_percent,
            openPositionNotional=open_notional,
            grossExposure=open_notional,
            netExposure=net_exposure,
            totalOpenRiskDollars=open_risk,
            totalOpenRiskPercent=open_risk_percent,
            tradesToday=self._trades_today(session_date),
            sessionDate=session_date,
            lastMarkPrice=last_mark_price,
            lastMarkedAt=last_marked_at,
            appliedFillIds=tuple(self.applied_fill_ids()),
            observedAt=observed,
        )
        return account.to_record(reason_codes=reason_codes)

    def _enriched_account_payload(self, existing: Mapping[str, Any], *, observed_at: datetime, reason_codes: list[str]) -> dict[str, Any]:
        initial = _decimal(existing.get("initialCash") or existing.get("startingCash") or _configured_initial_cash())
        cash = _decimal(_first_present(existing, "cash", "cashBalance", "buyingPower", default=initial))
        realized = _decimal(existing.get("realizedPnl") or existing.get("realizedPnlToday") or 0)
        unrealized = _decimal(existing.get("unrealizedPnl") or existing.get("unrealizedPnlToday") or 0)
        intraday_high = _decimal(existing.get("intradayEquityHigh")) if existing.get("intradayEquityHigh") is not None else None
        payload = self._account_payload(
            initial_cash=initial,
            cash=cash,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            intraday_equity_high=intraday_high,
            observed_at=observed_at,
            reason_codes=reason_codes,
        )
        existing_session = str(existing.get("sessionDate") or "")
        if existing_session == str(payload.get("sessionDate") or ""):
            try:
                payload["tradesToday"] = max(int(payload.get("tradesToday") or 0), int(existing.get("tradesToday") or 0))
            except (TypeError, ValueError):
                pass
            if existing.get("dailyNetPnl") is not None:
                payload["dailyNetPnl"] = _money(existing.get("dailyNetPnl"))
            if existing.get("dailyNetPnlAfterExitCosts") is not None:
                payload["dailyNetPnlAfterExitCosts"] = _money(existing.get("dailyNetPnlAfterExitCosts"))
        return payload

    def _apply_position_update(
        self,
        *,
        symbol: str,
        signed_position: int,
        average_price: float | Decimal,
        signed_fill: int,
        fill_price: float | Decimal,
        filled_at: datetime,
        client_order_id: str,
        stop_price: float | None,
        profit_target_price: float | None,
        realized_pnl: float | Decimal,
    ) -> None:
        current_position = self.position_for_symbol(symbol)
        average = _decimal(average_price)
        price = _decimal(fill_price)
        prior_realized = _decimal(current_position.get("realizedPnl") or 0)
        entry_fill_ids = [str(item) for item in current_position.get("entryFillIds") or [] if item]
        if not entry_fill_ids and current_position.get("lastFillId") and signed_position != 0:
            entry_fill_ids.append(str(current_position["lastFillId"]))
        next_quantity = signed_position + signed_fill
        if next_quantity == 0:
            next_average = Decimal("0")
            market_value = Decimal("0")
            unrealized = Decimal("0")
        elif signed_position == 0 or (signed_position > 0) == (signed_fill > 0):
            next_notional = Decimal(signed_position) * average + Decimal(signed_fill) * price
            next_average = abs(next_notional / next_quantity)
            market_value = Decimal(next_quantity) * price
            unrealized = _unrealized_pnl(next_quantity, next_average, price)
            entry_fill_ids.append(client_order_id)
        else:
            next_average = average
            market_value = Decimal(next_quantity) * price
            unrealized = _unrealized_pnl(next_quantity, next_average, price)
        position = VotingEnsemblePosition(
            symbol=symbol,
            quantity=next_quantity,
            averagePrice=float(next_average),
            markPrice=float(price if next_quantity else Decimal("0")),
            notional=float(market_value),
            unrealizedPnl=float(unrealized),
            realizedPnl=float(prior_realized + _decimal(realized_pnl)),
            # A flat record keeps no opening metadata. It used to carry the previous
            # trade's openedAt and entry ids forward, so the next position on the symbol
            # inherited a clock that had already been running for hours and a time stop
            # would have fired on it at once.
            openedAt=((_parse_time(current_position.get("openedAt")) if signed_position != 0 else None) or filled_at) if next_quantity else None,
            updatedAt=filled_at,
            stopPrice=stop_price if next_quantity else None,
            profitTargetPrice=profit_target_price if next_quantity else None,
            entryOrderId=str(current_position.get("entryOrderId") or client_order_id) if next_quantity else None,
            entryFillIds=tuple(dict.fromkeys(entry_fill_ids)) if next_quantity else (),
            lastFillId=client_order_id,
            lastMarkedAt=filled_at if next_quantity else None,
            markPricePolicy="fill_price_until_fresh_nbbo_mark" if next_quantity else None,
            marketDataFresh=True,
            quoteAgeSeconds=0.0 if next_quantity else None,
            marketDataReceiptAgeSeconds=0.0 if next_quantity else None,
        )
        self.store.write_snapshot(
            f"local_position.{symbol}",
            position.to_record(reason_codes=["voting_ensemble.local_paper.position_updated_by_ledger_fill"]),
        )

    def _apply_mark_to_positions(self, symbol: str, *, mark: Mapping[str, Any], observed_at: datetime) -> None:
        changed = False
        for payload in self.positions(include_flat=False):
            if str(payload.get("symbol") or "").upper() != symbol:
                continue
            quantity = int(payload.get("quantity") or 0)
            if quantity == 0:
                continue
            mark_price = _decimal(mark["bid"] if quantity > 0 else mark["ask"])
            average = _decimal(payload.get("averagePrice") or payload.get("averageEntryPrice") or mark_price)
            market_value = Decimal(quantity) * mark_price
            unrealized = _unrealized_pnl(quantity, average, mark_price)
            position = VotingEnsemblePosition(
                symbol=symbol,
                quantity=quantity,
                averagePrice=float(average),
                markPrice=float(mark_price),
                notional=float(market_value),
                unrealizedPnl=float(unrealized),
                realizedPnl=float(_decimal(payload.get("realizedPnl") or 0)),
                openedAt=_parse_time(payload.get("openedAt")),
                updatedAt=observed_at,
                stopPrice=_positive_float(payload.get("stopPrice")),
                profitTargetPrice=_positive_float(payload.get("profitTargetPrice")),
                entryOrderId=str(payload.get("entryOrderId") or "") or None,
                entryFillIds=tuple(str(item) for item in payload.get("entryFillIds") or []),
                lastFillId=str(payload.get("lastFillId") or "") or None,
                lastMarkedAt=observed_at,
                markPricePolicy="conservative_liquidation_nbbo_bid_for_long_ask_for_short",
                marketDataFresh=True,
                quoteAgeSeconds=float(mark["quoteAgeSeconds"]),
                marketDataReceiptAgeSeconds=float(mark["marketDataReceiptAgeSeconds"]),
            )
            self.store.write_snapshot(
                f"local_position.{symbol}",
                position.to_record(reason_codes=["voting_ensemble.local_paper.position_marked_to_fresh_nbbo"]),
            )
            changed = True
        if not changed:
            return
        account = self.account_snapshot(observed_at=observed_at)
        positions = self.positions(include_flat=False)
        unrealized = sum((_decimal(position.get("unrealizedPnl")) for position in positions), Decimal("0"))
        equity = _max_decimal(
            Decimal("0"),
            _decimal(_first_present(account, "cash", "cashBalance", default=0))
            + _owned_position_market_value(positions),
        )
        self.store.write_snapshot(
            "local_account.latest",
            self._account_payload(
                initial_cash=_decimal(account.get("initialCash") or _configured_initial_cash()),
                cash=_decimal(_first_present(account, "cash", "cashBalance", default=0)),
                realized_pnl=_decimal(account.get("realizedPnl") or 0),
                unrealized_pnl=unrealized,
                equity=equity,
                intraday_equity_high=_decimal(account.get("intradayEquityHigh") or equity),
                observed_at=observed_at,
                reason_codes=["voting_ensemble.local_paper.account_marked_to_market_from_fresh_nbbo"],
            ),
        )
        self.store.write_snapshot("local_risk_snapshot.latest", self.risk_snapshot_payload(observed_at=observed_at))

    def _market_data_status_payload(self, *, symbol: str, mark: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
        return _owned_record(
            {
                "schemaVersion": "voting_ensemble_local_market_data_mark_v1",
                "symbol": symbol,
                "fresh": bool(mark.get("fresh")),
                "bid": _price(mark.get("bid")) if mark.get("bid") is not None else None,
                "ask": _price(mark.get("ask")) if mark.get("ask") is not None else None,
                "bidSize": float(mark.get("bidSize") or 0.0),
                "askSize": float(mark.get("askSize") or 0.0),
                "quoteTimestamp": _iso(mark["quoteTimestamp"]) if mark.get("quoteTimestamp") is not None else None,
                "marketDataReceiptTimestamp": _iso(mark["marketDataReceiptTimestamp"]) if mark.get("marketDataReceiptTimestamp") is not None else None,
                "quoteAgeSeconds": float(mark.get("quoteAgeSeconds") or 0.0),
                "marketDataReceiptAgeSeconds": float(mark.get("marketDataReceiptAgeSeconds") or 0.0),
                "maxQuoteAgeSeconds": float(mark.get("maxQuoteAgeSeconds") or _configured_max_quote_age_seconds()),
                "markPricePolicy": "conservative_liquidation_nbbo_bid_for_long_ask_for_short",
                "observedAt": _iso(observed_at),
                "sourceAuthority": VOTING_ENSEMBLE_LOCAL_SOURCE_AUTHORITY,
                "reasonCodes": list(mark.get("reasonCodes") or ()),
            }
        )

    def _order_price(self, client_order_id: str, key: str) -> float | None:
        try:
            order = self.store.read_snapshot(f"local_order.{client_order_id}")
        except KeyError:
            return None
        value = order.get(key)
        if value is None:
            return None
        parsed = _decimal(value)
        return float(parsed) if parsed > 0 else None

    def _record_closed_trade_if_needed(
        self,
        *,
        fill: VotingEnsembleFill,
        average_entry_price: float,
        closing_quantity: int,
        prior_position: Mapping[str, Any],
    ) -> None:
        if closing_quantity <= 0:
            return
        trade_id = f"ve-closed-{_hash({'clientOrderId': fill.clientOrderId, 'quantity': closing_quantity, 'at': fill.filledAt.isoformat()})[:20]}"
        closed = VotingEnsembleClosedTrade(
            closedTradeId=trade_id,
            clientOrderId=fill.clientOrderId,
            orderIntentId=fill.orderIntentId,
            symbol=fill.symbol,
            side=fill.side,
            quantity=closing_quantity,
            averageEntryPrice=average_entry_price,
            exitPrice=fill.averageFillPrice,
            realizedPnl=fill.realizedPnl,
            closedAt=fill.filledAt,
            entryOrderId=str(prior_position.get("entryOrderId") or "") or None,
            exitOrderId=fill.clientOrderId,
            entryFillIds=tuple(str(item) for item in prior_position.get("entryFillIds") or ([prior_position.get("lastFillId")] if prior_position.get("lastFillId") else [])),
            exitFillId=fill.clientOrderId,
            associatedOrderIds=tuple(
                dict.fromkeys(
                    [
                        *([str(prior_position.get("entryOrderId"))] if prior_position.get("entryOrderId") else []),
                        fill.clientOrderId,
                    ]
                )
            ),
            associatedFillIds=tuple(
                dict.fromkeys(
                    [
                        *[str(item) for item in prior_position.get("entryFillIds") or ([prior_position.get("lastFillId")] if prior_position.get("lastFillId") else [])],
                        fill.clientOrderId,
                    ]
                )
            ),
        )
        closed_payload = closed.to_record(reason_codes=["voting_ensemble.local_paper.closed_trade_recorded"])
        self.store.write_snapshot(f"local_closed_trade.{trade_id}", closed_payload)
        self.store.write_snapshot(
            f"local_realized_pnl.ve-pnl-{trade_id}",
            {
                **closed_payload,
                "schemaVersion": "voting_ensemble_local_realized_pnl_v1",
                "realizedPnlRecordId": f"ve-pnl-{trade_id}",
                "observedAt": _iso(fill.filledAt),
                "reasonCodes": ["voting_ensemble.local_paper.realized_pnl_recorded"],
            },
        )

    def _update_account_from_fill(
        self,
        *,
        side: Signal,
        quantity: int,
        fill_price: float,
        fee_amount: float | Decimal,
        realized_pnl: float,
        observed_at: datetime,
    ) -> None:
        try:
            account = self.store.read_snapshot("local_account.latest")
        except KeyError:
            initial = _configured_initial_cash()
            account = {
                "initialCash": _money(initial),
                "cash": _money(initial),
                "cashBalance": _money(initial),
                "realizedPnl": 0.0,
                "realizedPnlToday": 0.0,
                "intradayEquityHigh": _money(initial),
            }
        cash_delta = _decimal(quantity) * _decimal(fill_price)
        fee = _decimal(fee_amount)
        cash_delta = -(cash_delta + fee) if side == Signal.BUY else cash_delta - fee
        cash = _decimal(_first_present(account, "cash", "cashBalance", "buyingPower", default=0)) + cash_delta
        prior_realized = _decimal(account.get("realizedPnl") or account.get("realizedPnlToday") or 0)
        positions = self.positions(include_flat=False)
        unrealized = sum((_decimal(position.get("unrealizedPnl")) for position in positions), Decimal("0"))
        equity = _max_decimal(Decimal("0"), cash + _owned_position_market_value(positions))
        self.store.write_snapshot(
            "local_account.latest",
            self._account_payload(
                initial_cash=_decimal(account.get("initialCash") or _configured_initial_cash()),
                cash=cash,
                realized_pnl=prior_realized + _decimal(realized_pnl),
                observed_at=observed_at,
                equity=equity,
                unrealized_pnl=unrealized,
                intraday_equity_high=_decimal(account.get("intradayEquityHigh") or equity),
                reason_codes=["voting_ensemble.local_paper.account_updated_from_local_fill"],
            ),
        )

    def _total_open_risk_dollars(self, positions: list[dict[str, Any]]) -> Decimal:
        total = Decimal("0")
        for position in positions:
            quantity = abs(int(position.get("quantity") or 0))
            if quantity <= 0:
                continue
            stop = position.get("stopPrice") or position.get("protectiveStopPrice")
            if stop is None:
                continue
            mark = _decimal(position.get("markPrice") or position.get("averagePrice") or 0)
            risk_per_share = abs(mark - _decimal(stop))
            total += risk_per_share * Decimal(quantity)
        return total

    def _last_mark(self, positions: list[dict[str, Any]]) -> tuple[Decimal | None, datetime | None]:
        latest_price: Decimal | None = None
        latest_at: datetime | None = None
        for position in positions:
            marked_at = _parse_time(position.get("lastMarkedAt") or position.get("updatedAt"))
            if marked_at is None:
                continue
            if latest_at is None or marked_at > latest_at:
                latest_at = marked_at
                latest_price = _decimal(position.get("markPrice") or position.get("averagePrice") or 0)
        return latest_price, latest_at

    def _trades_today(self, session_date: date) -> int:
        count = 0
        for fill in self.fills():
            filled_at = _parse_time(fill.get("filledAt"))
            if filled_at is not None and filled_at.date() == session_date:
                count += 1
        return count

    def _records(self, prefix: str, *, namespace: str = VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE) -> list[dict[str, Any]]:
        return [
            dict(payload)
            for key, payload in sorted(self.store.snapshots.items())
            if key.startswith(f"{namespace}.{prefix}")
            and payload.get("algorithmId", payload.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
            and payload.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
        ]

    @staticmethod
    def _broker_order(payload: Mapping[str, Any]) -> BrokerOrderState:
        submitted_at = _parse_time(payload.get("submittedAt")) or datetime.now(UTC)
        return BrokerOrderState(
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            orderIntentId=str(payload.get("orderIntentId") or ""),
            clientOrderId=str(payload.get("clientOrderId") or ""),
            orderType=str(payload.get("orderType") or "LIMIT").upper(),
            symbol=str(payload.get("symbol") or "SPY").upper(),
            side=Signal(payload.get("side") or Signal.BUY),
            status=str(payload.get("status") or "ACCEPTED"),
            quantity=int(payload.get("quantity") or 0),
            filledQuantity=int(payload.get("filledQuantity") or 0),
            entryPrice=_positive_float(payload.get("entryPrice") or payload.get("limitPrice")) or 0.01,
            stopPrice=_positive_float(payload.get("stopPrice")),
            submittedAt=submitted_at,
        )

    @staticmethod
    def _broker_position(payload: Mapping[str, Any], *, observed_at: datetime) -> BrokerPositionState:
        quantity = int(payload.get("quantity") or 0)
        side = Signal.BUY if quantity >= 0 else Signal.SELL
        average = _positive_float(payload.get("averagePrice")) or 0.01
        mark = _positive_float(payload.get("markPrice") or payload.get("averagePrice")) or average
        return BrokerPositionState(
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            symbol=str(payload.get("symbol") or "SPY").upper(),
            side=side,
            quantity=abs(quantity),
            averageEntryPrice=average,
            markPrice=mark,
            realizedPnlToday=0.0,
            openedAt=_require_utc(observed_at),
        )


def _owned_record(payload: dict[str, Any]) -> dict[str, Any]:
    payload["algorithmId"] = VOTING_ENSEMBLE_ALGORITHM_ID
    payload["algorithm_id"] = VOTING_ENSEMBLE_ALGORITHM_ID
    payload["capitalPartitionId"] = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    payload["accountId"] = VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
    payload["executionMode"] = "LOCAL_PAPER"
    return payload


def _owned_keys(snapshots: Mapping[str, Mapping[str, Any]], prefix: str) -> list[str]:
    return [
        key
        for key, payload in sorted(snapshots.items())
        if key.startswith(prefix)
        and payload.get("algorithmId", payload.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
        and payload.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
    ]


def _is_voting_ensemble_owned_position(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("algorithmId", payload.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
        and payload.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
        and payload.get("accountId", VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID) == VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
        and payload.get("positionOwner", VOTING_ENSEMBLE_ALGORITHM_ID) == VOTING_ENSEMBLE_ALGORITHM_ID
        and payload.get("exitOwner", VOTING_ENSEMBLE_ALGORITHM_ID) == VOTING_ENSEMBLE_ALGORITHM_ID
    )


def _account_requires_upgrade(payload: Mapping[str, Any]) -> bool:
    required = {
        "initialCash",
        "cash",
        "equity",
        "buyingPower",
        "usableEntryBuyingPower",
        "cashBuyingPower",
        "marginBuyingPower",
        "allowLeverage",
        "allowMargin",
        "allowShorts",
        "maxLeverage",
        "buyingPowerModel",
        "equityModel",
        "realizedPnl",
        "realizedPnlToday",
        "unrealizedPnl",
        "dailyNetPnl",
        "intradayEquityHigh",
        "drawdownDollars",
        "drawdownPercent",
        "openPositionNotional",
        "grossExposure",
        "netExposure",
        "totalOpenRiskDollars",
        "totalOpenRiskPercent",
        "tradesToday",
        "sessionDate",
        "lastMarkPrice",
        "lastMarkedAt",
        "appliedFillIds",
        "algorithmId",
        "capitalPartitionId",
        "version",
    }
    return payload.get("version") != VOTING_ENSEMBLE_LOCAL_PAPER_ACCOUNT_VERSION or any(key not in payload for key in required)


def _configured_initial_cash() -> Decimal:
    raw = os.getenv("VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH")
    if raw is None or str(raw).strip() == "":
        return VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH
    value = _decimal(raw)
    return value if value > 0 else VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH


def _configured_max_quote_age_seconds() -> Decimal:
    value = _decimal(os.getenv("VOTING_ENSEMBLE_LOCAL_PAPER_MAX_MARK_QUOTE_AGE_SECONDS"))
    return value if value > 0 else Decimal("5")


def _mark_input_from_nbbo(symbol: str, nbbo: Mapping[str, Any] | None, observed_at: datetime) -> dict[str, Any]:
    max_quote_age = _configured_max_quote_age_seconds()
    base = {
        "symbol": symbol.upper(),
        "fresh": False,
        "bid": None,
        "ask": None,
        "quoteTimestamp": None,
        "marketDataReceiptTimestamp": None,
        "quoteAgeSeconds": 0.0,
        "marketDataReceiptAgeSeconds": 0.0,
        "maxQuoteAgeSeconds": max_quote_age,
        "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.quote_missing"],
    }
    if not isinstance(nbbo, Mapping):
        return base
    bid = _positive_float(nbbo.get("bid"))
    ask = _positive_float(nbbo.get("ask"))
    bid_size = _positive_float(nbbo.get("bidSize") or nbbo.get("bid_size")) or 0.0
    ask_size = _positive_float(nbbo.get("askSize") or nbbo.get("ask_size")) or 0.0
    quote_timestamp = _parse_time(nbbo.get("quoteTimestamp") or nbbo.get("timestamp"))
    receipt_timestamp = _parse_time(nbbo.get("marketDataReceiptTimestamp") or nbbo.get("receivedAt") or nbbo.get("receiptTimestamp"))
    if bid is None or ask is None or quote_timestamp is None or receipt_timestamp is None:
        return {**base, "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.quote_malformed"]}
    if ask < bid:
        return {**base, "bid": bid, "ask": ask, "quoteTimestamp": quote_timestamp, "marketDataReceiptTimestamp": receipt_timestamp, "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.quote_crossed"]}
    quote_age = Decimal(max(0.0, (observed_at - quote_timestamp).total_seconds()))
    receipt_age = Decimal(max(0.0, (observed_at - receipt_timestamp).total_seconds()))
    if quote_timestamp > observed_at or receipt_timestamp > observed_at:
        return {
            **base,
            "bid": bid,
            "ask": ask,
            "bidSize": bid_size,
            "askSize": ask_size,
            "quoteTimestamp": quote_timestamp,
            "marketDataReceiptTimestamp": receipt_timestamp,
            "quoteAgeSeconds": float(quote_age),
            "marketDataReceiptAgeSeconds": float(receipt_age),
            "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.future_quote"],
        }
    if quote_age > max_quote_age or receipt_age > max_quote_age:
        return {
            **base,
            "bid": bid,
            "ask": ask,
            "bidSize": bid_size,
            "askSize": ask_size,
            "quoteTimestamp": quote_timestamp,
            "marketDataReceiptTimestamp": receipt_timestamp,
            "quoteAgeSeconds": float(quote_age),
            "marketDataReceiptAgeSeconds": float(receipt_age),
            "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.stale_quote"],
        }
    return {
        **base,
        "fresh": True,
        "bid": bid,
        "ask": ask,
        "bidSize": bid_size,
        "askSize": ask_size,
        "quoteTimestamp": quote_timestamp,
        "marketDataReceiptTimestamp": receipt_timestamp,
        "quoteAgeSeconds": float(quote_age),
        "marketDataReceiptAgeSeconds": float(receipt_age),
        "reasonCodes": ["voting_ensemble.local_paper.mark_to_market.fresh_nbbo"],
    }


def _fill_fee(quantity: int, fill_price: Decimal | float = Decimal("0")) -> Decimal:
    per_share = _non_negative_env_decimal("VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE")
    flat = _non_negative_env_decimal("VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL")
    fee_bps = _non_negative_env_decimal("VOTING_ENSEMBLE_LOCAL_PAPER_FEE_BPS")
    notional = Decimal(max(0, int(quantity))) * _decimal(fill_price)
    return per_share * Decimal(max(0, int(quantity))) + flat + (notional * fee_bps / Decimal("10000"))


def _unrealized_pnl(quantity: int, average_entry: Decimal, mark: Decimal) -> Decimal:
    if quantity > 0:
        return (mark - average_entry) * Decimal(quantity)
    if quantity < 0:
        return (average_entry - mark) * Decimal(abs(quantity))
    return Decimal("0")


def _non_negative_env_decimal(name: str) -> Decimal:
    value = _decimal(os.getenv(name))
    return value if value > 0 else Decimal("0")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _first_present(payload: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return default


def _abs_decimal(value: Any) -> Decimal:
    return abs(_decimal(value))


def _max_decimal(*values: Decimal) -> Decimal:
    return max(values)


def _min_decimal(*values: Decimal) -> Decimal:
    return min(values)


def _owned_position_market_value(positions: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for position in positions:
        if int(position.get("quantity") or position.get("signedQuantity") or 0) <= 0:
            continue
        total += _max_decimal(Decimal("0"), _decimal(position.get("notional") or position.get("marketValue") or 0))
    return total


def _money(value: Any) -> float:
    return float(_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _price(value: Any) -> float:
    return float(_decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _percent(value: Any) -> float:
    return float(_decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _execution_key(key: str) -> str:
    return f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.{key}"


def _applied_fill_id(
    *,
    applied_fill_id: str | None,
    client_order_id: str,
    order_intent_id: str,
    symbol: str,
    side: Signal | str,
    requested_quantity: int,
    fill_price: float | Decimal,
    filled_at: datetime,
) -> str:
    if applied_fill_id:
        return str(applied_fill_id)
    return _hash(
        {
            "clientOrderId": client_order_id,
            "orderIntentId": order_intent_id,
            "symbol": symbol.upper(),
            "side": Signal(side).value,
            "requestedQuantity": int(requested_quantity),
            "fillPrice": _price(fill_price),
            "filledAt": _iso(filled_at),
        }
    )


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _iso(value: datetime) -> str:
    return _require_utc(value).isoformat().replace("+00:00", "Z")


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
