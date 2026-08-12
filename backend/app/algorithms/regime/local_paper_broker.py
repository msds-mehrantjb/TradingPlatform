"""Regime-owned local paper execution simulator.

The broker in this module is only for Regime LOCAL_PAPER runtime. It never calls
Alpaca trading endpoints and never treats broker/account state as authoritative.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Mapping

from backend.app.algorithms.regime.account_snapshot import fail_closed_regime_account_snapshot
from backend.app.algorithms.regime.contracts import RegimeRuntimeMode
from backend.app.algorithms.regime.local_paper_account import (
    REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE,
    REGIME_LOCAL_PAPER_SOURCE_AUTHORITY,
    RegimeLocalPaperAccount,
)
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill
from backend.app.risk.types import AccountSnapshot, PendingOrder, PortfolioPosition, PortfolioSnapshot


REGIME_LOCAL_PAPER_BROKER_VERSION = "regime_local_paper_broker_v1"
REGIME_LOCAL_PAPER_ORDERS_KEY = "local_paper_broker.orders"
REGIME_LOCAL_PAPER_FILLS_KEY = "local_paper_broker.fills"
REGIME_ALGORITHM_ID = "regime"
_OPEN_STATUSES = {"ACCEPTED", "NEW", "OPEN", "PARTIALLY_FILLED"}
_TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class RegimeLocalPaperBroker:
    """Local Regime execution venue for simulated LIMIT and STOP_LIMIT orders."""

    broker_kind = "regime_local_paper"
    account_type = "simulated"
    base_url = "local-paper://regime"
    paper_only = True
    live_trading_enabled = False
    credentials_verified = True
    account_endpoint_responsive = True
    account_matches_configured_identity = True
    account_allowed_to_trade = True
    market_data_credentials_configured = True

    def __init__(
        self,
        *,
        repository: Any,
        identity: Mapping[str, Any],
        starting_balance: float = REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE,
        commission_per_share: float = 0.0,
        minimum_commission: float = 0.0,
        regulatory_fee_per_share: float = 0.0,
        slippage_per_share: float = 0.0,
        spread_cost_multiplier: float = 0.0,
        participation_limit: float = 1.0,
        maximum_fill_quantity: int | None = None,
        allow_partial_fills: bool = False,
        allow_bar_execution: bool = False,
    ) -> None:
        self.repository = repository
        self.identity = _identity(identity)
        self.account_id = self.identity["accountId"]
        self.starting_balance = float(starting_balance or REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE)
        self.commission_per_share = max(0.0, float(commission_per_share or 0.0))
        self.minimum_commission = max(0.0, float(minimum_commission or 0.0))
        self.regulatory_fee_per_share = max(0.0, float(regulatory_fee_per_share or 0.0))
        self.slippage_per_share = max(0.0, float(slippage_per_share or 0.0))
        self.spread_cost_multiplier = max(0.0, float(spread_cost_multiplier or 0.0))
        self.participation_limit = max(0.0, min(1.0, float(participation_limit or 0.0)))
        self.maximum_fill_quantity = int(maximum_fill_quantity) if maximum_fill_quantity else None
        self.allow_partial_fills = bool(allow_partial_fills)
        self.allow_bar_execution = bool(allow_bar_execution)
        self.last_verification_reason_codes: tuple[str, ...] = ()
        self._last_market_update: dict[str, Any] | None = None
        self._orders = tuple(self._load_records(REGIME_LOCAL_PAPER_ORDERS_KEY, "orders"))
        self._fills = tuple(self._load_records(REGIME_LOCAL_PAPER_FILLS_KEY, "fills"))

    @property
    def configured(self) -> bool:
        return bool(self.account_id)

    def close(self) -> None:
        return None

    def verify_paper_account(self) -> bool:
        return True

    def startup_verification(self) -> dict[str, Any]:
        return self.paper_trading_configuration() | {"verified": True, "accountEndpointResponsive": True}

    def paper_trading_configuration(self) -> dict[str, Any]:
        return {
            "brokerVersion": REGIME_LOCAL_PAPER_BROKER_VERSION,
            "brokerKind": self.broker_kind,
            "baseUrl": self.base_url,
            "paperOnly": True,
            "localPaper": True,
            "liveTradingEnabled": False,
            "accountType": self.account_type,
            "accountId": self.account_id,
            "configured": True,
            "credentialsConfigured": True,
            "credentialsVerified": True,
            "marketDataCredentialsConfigured": True,
            "accountMatchesConfiguredIdentity": True,
            "accountAllowedToTrade": True,
            "reasonCodes": (),
        }

    def refresh_account_snapshot(self) -> dict[str, Any]:
        try:
            stored = self.repository.read_local_paper_account_snapshot(self.identity)
        except Exception:
            return fail_closed_regime_account_snapshot(
                self.identity,
                reason_codes=("regime.account_snapshot.local_paper_account_load_failed",),
                source_authority="regime_local_paper_account",
            ) | {"paperOnly": True, "localPaper": True, "liveTradingEnabled": False}
        if stored is None:
            return fail_closed_regime_account_snapshot(
                self.identity,
                reason_codes=("regime.account_snapshot.local_paper_account_missing",),
                source_authority="regime_local_paper_account",
            ) | {"paperOnly": True, "localPaper": True, "liveTradingEnabled": False}
        try:
            snapshot = RegimeLocalPaperAccount.from_snapshot(stored).get_account_snapshot().to_dict()
        except Exception:
            return fail_closed_regime_account_snapshot(
                self.identity,
                reason_codes=("regime.account_snapshot.local_paper_account_inconsistent",),
                source_authority="regime_local_paper_account",
            ) | {"paperOnly": True, "localPaper": True, "liveTradingEnabled": False}
        return {
            **snapshot,
            "sourceAuthority": REGIME_LOCAL_PAPER_SOURCE_AUTHORITY,
            "accountSnapshotId": f"regime-local-paper-{self.account_id}-{snapshot['stateVersion']}",
            "accountId": self.account_id,
            "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
            "globalRiskCapacityQuantity": int(snapshot["availableBuyingPower"]),
            "dailyAccountPnl": snapshot["dailyRealizedPnl"],
            "accountSnapshotFresh": True,
            "buyingPowerCurrent": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": False,
            "paperOnly": True,
            "localPaper": True,
            "liveTradingEnabled": False,
            "reasonCodes": ("regime.local_paper.account_snapshot_authoritative",),
        }

    def gateway_account_snapshot(self, **_: Any) -> AccountSnapshot:
        snapshot = self.refresh_account_snapshot()
        equity = float(snapshot["equity"])
        return AccountSnapshot(
            accountSnapshotId=str(snapshot.get("accountSnapshotId") or f"regime-local-paper-fail-closed-{self.account_id}"),
            accountId=self.account_id,
            equity=equity,
            highWaterEquity=max(equity, float(snapshot.get("highWaterMark") or equity)),
            availableBuyingPower=float(snapshot["availableBuyingPower"]),
            settledCash=float(snapshot.get("cash") or 0.0),
            realizedDailyPnl=float(snapshot.get("dailyRealizedPnl") or 0.0),
            unrealizedDailyPnl=float(snapshot.get("dailyUnrealizedPnl") or 0.0),
            observedAt=_parse_dt(snapshot.get("observedAt")) or _utc_now(),
        )

    def gateway_portfolio_snapshot(self, **_: Any) -> PortfolioSnapshot:
        account = self._account().get_account_snapshot()
        positions = tuple(
            PortfolioPosition(
                algorithmId=REGIME_ALGORITHM_ID,
                symbol=position.symbol,
                quantity=position.quantity,
                marketValue=abs(position.marketValue),
                openRiskDollars=0.0,
                side="long" if position.side.lower() == "long" else "short",
            )
            for position in account.positions
        )
        pending = tuple(
            PendingOrder(
                algorithmId=REGIME_ALGORITHM_ID,
                symbol=str(order["symbol"]),
                side="Sell" if _normal_side(order.get("side")) == Signal.SELL else "Buy",
                quantity=int(order.get("remainingQuantity") or order.get("quantity") or 0),
                notional=float(order.get("remainingQuantity") or order.get("quantity") or 0) * float(order.get("limitPrice") or order.get("stopLimitPrice") or order.get("stopPrice") or 0.01),
                riskDollars=float(order.get("plannedRiskDollars") or 0.0),
                decisionId=str(order.get("decisionId") or order.get("orderIntentId") or order.get("clientOrderId")),
                clientOrderId=str(order.get("clientOrderId")),
                intentKey=str(order.get("orderIntentId") or order.get("clientOrderId")),
                submittedAt=_parse_dt(order.get("createdAt")) or _utc_now(),
            )
            for order in self.get_open_orders()
        )
        return PortfolioSnapshot(
            positions=positions,
            pendingOrders=pending,
            tradesToday=int(account.tradeCount),
            algorithmTradesToday={REGIME_ALGORITHM_ID: int(account.tradeCount)},
        )

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        order = self.submit_order(intent)
        status = str(order.get("status") or "REJECTED")
        return PaperGatewayBrokerAck(
            clientOrderId=str(order.get("clientOrderId") or getattr(intent, "clientOrderId", "")),
            brokerOrderId=str(order.get("localOrderId") or "") or None,
            status=status if status in _OPEN_STATUSES or status in _TERMINAL_STATUSES else "REJECTED",
            acceptedAt=_parse_dt(order.get("createdAt")) if status != "REJECTED" else None,
            rejectedReason=str(order.get("rejectedReason") or "") or None,
        )

    def submit_order(self, order_intent: Any) -> dict[str, Any]:
        payload = _mapping_from_order(order_intent)
        self._assert_regime_payload(payload)
        raw_order_type = str(payload.get("orderType") or payload.get("type") or "LIMIT").upper()
        order_type = {"BRACKET_LIMIT": "LIMIT", "BRACKET_STOP_LIMIT": "STOP_LIMIT"}.get(raw_order_type, raw_order_type)
        if order_type not in {"LIMIT", "STOP_LIMIT"}:
            return self._rejected_order(payload, "regime.local_paper.unsupported_order_type")
        tif = str(payload.get("timeInForce") or payload.get("time_in_force") or "DAY").upper()
        if tif != "DAY":
            return self._rejected_order(payload, "regime.local_paper.day_tif_required")
        quantity = int(payload.get("submittedQuantity") or payload.get("quantity") or 0)
        if quantity <= 0:
            return self._rejected_order(payload, "regime.local_paper.quantity_required")
        client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
        if not client_order_id:
            return self._rejected_order(payload, "regime.local_paper.client_order_id_required")
        existing = self.find_order_by_client_order_id(client_order_id)
        if existing is not None:
            return existing
        now = _utc_now()
        order = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "algorithmInstanceId": self.identity["algorithmInstanceId"],
            "accountId": self.account_id,
            "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
            "symbol": str(payload.get("symbol") or self.identity.get("symbol") or "SPY").upper(),
            "localOrderId": f"regime-local-order-{_stable_hash((self.account_id, client_order_id))[:16]}",
            "clientOrderId": client_order_id,
            "orderIntentId": str(payload.get("orderIntentId") or client_order_id),
            "decisionId": str(payload.get("decisionId") or payload.get("orderIntentId") or client_order_id),
            "side": _normal_side(payload.get("side")).value,
            "orderType": order_type,
            "timeInForce": tif,
            "quantity": quantity,
            "submittedQuantity": quantity,
            "remainingQuantity": quantity,
            "limitPrice": _optional_float(payload.get("limitPrice") or payload.get("limit_price")),
            "stopPrice": _optional_float(payload.get("stopPrice") or payload.get("stop_price")),
            "stopLimitPrice": _optional_float(payload.get("stopLimitPrice") or payload.get("stop_limit_price")),
            "targetPrice": _optional_float(payload.get("targetPrice") or payload.get("target_price")),
            "plannedRiskDollars": float(payload.get("plannedRiskDollars") or 0.0),
            "positionEffect": payload.get("positionEffect") or payload.get("position_effect"),
            "status": "ACCEPTED",
            "stopTriggered": False,
            "createdAt": now.isoformat().replace("+00:00", "Z"),
            "updatedAt": now.isoformat().replace("+00:00", "Z"),
            "reasonCodes": ["regime.local_paper.order_accepted"],
        }
        if order_type == "LIMIT" and order["limitPrice"] is None:
            return self._rejected_order(payload, "regime.local_paper.limit_price_required")
        if order_type == "STOP_LIMIT" and (order["stopPrice"] is None or (order["limitPrice"] is None and order["stopLimitPrice"] is None)):
            return self._rejected_order(payload, "regime.local_paper.stop_limit_prices_required")
        order["reservedCash"] = _estimated_order_cost(order, commission_per_share=self.commission_per_share, minimum_commission=self.minimum_commission, slippage_per_share=self.slippage_per_share)
        updated_orders = [*self._orders, order]
        reserver = getattr(self.repository, "apply_local_paper_order_reservation", None)
        if not callable(reserver):
            raise RuntimeError("Regime repository does not support atomic local paper order reservations")
        try:
            reserver(
                self.identity,
                order=order,
                orders_snapshot=updated_orders,
                starting_balance=self.starting_balance,
                action="reserve",
            )
        except ValueError as exc:
            return self._rejected_order(payload, str(exc) or "regime.local_paper.insufficient_buying_power")
        self._orders = tuple(updated_orders)
        return copy.deepcopy(order)

    def cancel_order(self, client_order_id: str) -> bool:
        found = False
        updated = []
        now = _utc_now().isoformat().replace("+00:00", "Z")
        released_order: dict[str, Any] | None = None
        for order in self._orders:
            if str(order.get("clientOrderId")) == str(client_order_id) and str(order.get("status")) in _OPEN_STATUSES:
                self._assert_regime_payload(order)
                released_order = {**order, "status": "CANCELED", "remainingQuantity": 0, "reservedCash": 0.0, "updatedAt": now, "reasonCodes": [*order.get("reasonCodes", ()), "regime.local_paper.order_cancelled"]}
                order = released_order
                found = True
            updated.append(order)
        if found and released_order is not None:
            releaser = getattr(self.repository, "apply_local_paper_order_reservation", None)
            if not callable(releaser):
                raise RuntimeError("Regime repository does not support atomic local paper order reservations")
            original = self.find_order_by_client_order_id(client_order_id) or {}
            releaser(
                self.identity,
                order={**released_order, "reservedCash": float(original.get("reservedCash") or 0.0)},
                orders_snapshot=updated,
                starting_balance=self.starting_balance,
                action="release",
            )
            self._orders = tuple(updated)
        return found

    def process_market_update(self, market_update: Mapping[str, Any]) -> tuple[PaperGatewayFill, ...]:
        market = _normalize_market_update(market_update)
        self._last_market_update = market
        mark_price = _mark_price(market)
        if mark_price is not None:
            try:
                account = self._account()
            except ValueError:
                return ()
            account.mark_to_market(symbol=market["symbol"], marketPrice=mark_price, algorithmId=REGIME_ALGORITHM_ID, observedAt=market.get("timestamp"))
            account.persist(self.repository, symbol=market["symbol"])
        fills = []
        for order in self.get_open_orders(symbol=market["symbol"]):
            fill = self.simulate_fill(order, market)
            if fill is not None:
                fills.append(fill)
        return tuple(fills)

    def simulate_fill(self, order: Mapping[str, Any], market_update: Mapping[str, Any] | None = None) -> PaperGatewayFill | None:
        self._assert_regime_payload(order)
        if str(order.get("status")) not in _OPEN_STATUSES:
            return None
        market = _normalize_market_update(market_update or self._last_market_update or {})
        if market["symbol"] != str(order.get("symbol") or "").upper():
            return None
        created_at = _parse_dt(order.get("createdAt"))
        market_timestamp = market.get("timestamp")
        if created_at is not None and isinstance(market_timestamp, datetime) and market_timestamp < created_at:
            return None
        decision = self._fill_decision(order, market)
        if decision is None:
            return None
        fill_quantity, fill_price, reference_price = decision
        if fill_quantity <= 0:
            return None
        now = market.get("timestamp") or _utc_now()
        commission = max(self.minimum_commission if self.commission_per_share else 0.0, fill_quantity * self.commission_per_share)
        fees = fill_quantity * self.regulatory_fee_per_share if _normal_side(order.get("side")) == Signal.SELL else 0.0
        slippage = fill_quantity * self.slippage_per_share
        spread = max(0.0, float(market.get("ask") or 0.0) - float(market.get("bid") or 0.0)) if market.get("quoteValid") else 0.0
        spread_cost = fill_quantity * spread * self.spread_cost_multiplier
        total_cost = round(commission + fees + slippage + spread_cost, 10)
        fill = PaperGatewayFill(
            executionMode="LOCAL_PAPER",
            clientOrderId=str(order["clientOrderId"]),
            algorithmId=REGIME_ALGORITHM_ID,
            capitalPartitionId=str(order.get("capitalPartitionId")) if order.get("capitalPartitionId") else None,
            accountId=self.account_id,
            orderIntentId=str(order["orderIntentId"]),
            symbol=str(order["symbol"]),
            side=_normal_side(order.get("side")),
            filledQuantity=fill_quantity,
            averageFillPrice=round(fill_price, 10),
            marketReferencePrice=round(reference_price, 10),
            slippagePerShare=self.slippage_per_share,
            spreadImpactPerShare=round(spread * self.spread_cost_multiplier, 10),
            commission=round(commission, 10),
            regulatoryFees=round(fees, 10),
            totalExecutionCost=total_cost,
            executionCostBreakdown={"commission": round(commission, 10), "regulatoryFees": round(fees, 10), "slippage": round(slippage, 10), "spreadCost": round(spread_cost, 10)},
            status="FILLED" if fill_quantity >= int(order.get("remainingQuantity") or 0) else "PARTIALLY_FILLED",
            filledAt=now,
        )
        fill = fill.model_copy(update={"executionCostBreakdown": {**fill.executionCostBreakdown, "fillId": _fill_id(fill)}})
        self._apply_fill(fill, order)
        return fill

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        existing = next((fill for fill in self._fills if str(fill.get("clientOrderId")) == str(client_order_id)), None)
        if existing is not None:
            return _fill_from_record(existing)
        order = self.find_order_by_client_order_id(client_order_id)
        if order is None or self._last_market_update is None:
            return None
        return self.simulate_fill(order, self._last_market_update)

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        selected = str(symbol or "").upper()
        orders = [order for order in self._orders if str(order.get("status")) in _OPEN_STATUSES and (not selected or str(order.get("symbol") or "").upper() == selected)]
        return copy.deepcopy(orders)

    def get_fills(self, client_order_id: str | None = None) -> list[dict[str, Any]]:
        selected = str(client_order_id or "")
        fills = [fill for fill in self._fills if not selected or str(fill.get("clientOrderId")) == selected]
        return copy.deepcopy(fills)

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []

    def refresh_open_orders(self) -> list[dict[str, Any]]:
        return self.get_open_orders()

    def refresh_fills(self) -> list[dict[str, Any]]:
        return self.get_fills()

    def find_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        for order in self._orders:
            if str(order.get("clientOrderId")) == str(client_order_id):
                return copy.deepcopy(order)
        return None

    def order_status(self, client_order_id: str) -> dict[str, Any] | None:
        return self.find_order_by_client_order_id(client_order_id)

    def _fill_decision(self, order: Mapping[str, Any], market: Mapping[str, Any]) -> tuple[int, float, float] | None:
        side = _normal_side(order.get("side"))
        limit_price = _optional_float(order.get("stopLimitPrice") if str(order.get("orderType")) == "STOP_LIMIT" else None) or _optional_float(order.get("limitPrice"))
        if limit_price is None:
            return None
        order_type = str(order.get("orderType") or "LIMIT").upper()
        if order_type == "STOP_LIMIT" and not bool(order.get("stopTriggered")):
            stop_price = _optional_float(order.get("stopPrice"))
            if stop_price is None or not _stop_triggered(side, stop_price, market):
                return None
            self._mark_stop_triggered(order)
            order = self.find_order_by_client_order_id(str(order["clientOrderId"])) or order
        reference = _quote_reference(side, market)
        if reference is not None:
            if side == Signal.BUY and reference <= limit_price:
                return self._fill_quantity(order, market), min(limit_price, reference + self.slippage_per_share), reference
            if side == Signal.SELL and reference >= limit_price:
                return self._fill_quantity(order, market), max(limit_price, reference - self.slippage_per_share), reference
            return None
        if not self.allow_bar_execution:
            return None
        low = _optional_float(market.get("low"))
        high = _optional_float(market.get("high"))
        if low is None or high is None:
            return None
        if side == Signal.BUY and low <= limit_price:
            return self._fill_quantity(order, market), limit_price, limit_price
        if side == Signal.SELL and high >= limit_price:
            return self._fill_quantity(order, market), limit_price, limit_price
        return None

    def _fill_quantity(self, order: Mapping[str, Any], market: Mapping[str, Any]) -> int:
        remaining = int(order.get("remainingQuantity") or 0)
        capped = remaining
        volume = int(float(market.get("volume") or 0))
        if self.participation_limit and volume > 0:
            capped = min(capped, max(1, int(volume * self.participation_limit)))
        if self.maximum_fill_quantity is not None:
            capped = min(capped, self.maximum_fill_quantity)
        if not self.allow_partial_fills and capped < remaining:
            return 0
        return max(0, capped)

    def _apply_fill(self, fill: PaperGatewayFill, order: Mapping[str, Any]) -> None:
        fill_id = str(fill.executionCostBreakdown.get("fillId") or _fill_id(fill))
        if any(str(existing.get("fillId")) == fill_id for existing in self._fills):
            return
        remaining = int(order.get("remainingQuantity") or 0) - fill.filledQuantity
        status = "FILLED" if remaining <= 0 else "PARTIALLY_FILLED"
        now = fill.filledAt.isoformat().replace("+00:00", "Z")
        actual_reserved_conversion = ((fill.filledQuantity * float(fill.averageFillPrice or 0.0)) + fill.totalExecutionCost) if fill.side == Signal.BUY else 0.0
        original_reserved = float(order.get("reservedCash") or 0.0)
        release_leftover = max(0.0, original_reserved - actual_reserved_conversion) if status == "FILLED" and fill.side == Signal.BUY else 0.0
        updated_orders = []
        for existing in self._orders:
            if str(existing.get("clientOrderId")) == fill.clientOrderId:
                next_reserved = 0.0 if status == "FILLED" else max(0.0, float(existing.get("reservedCash") or 0.0) - actual_reserved_conversion)
                existing = {**existing, "remainingQuantity": max(0, remaining), "reservedCash": next_reserved, "status": status, "updatedAt": now, "reasonCodes": [*existing.get("reasonCodes", ()), "regime.local_paper.order_filled"]}
            updated_orders.append(existing)
        record = fill.model_dump(mode="json") | {
            "fillId": fill_id,
            "algorithmInstanceId": self.identity["algorithmInstanceId"],
            "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
            "decisionId": order.get("decisionId"),
        }
        fill_payload = {
            "fillId": fill_id,
            "algorithmId": REGIME_ALGORITHM_ID,
            "algorithmInstanceId": self.identity["algorithmInstanceId"],
            "accountId": self.account_id,
            "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
            "symbol": fill.symbol,
            "side": "Buy" if fill.side == Signal.BUY else "Sell",
            "filledQuantity": fill.filledQuantity,
            "averageFillPrice": fill.averageFillPrice,
            "commission": fill.commission,
            "fees": fill.regulatoryFees,
            "slippage": fill.totalExecutionCost - fill.commission - fill.regulatoryFees,
            "totalExecutionCost": fill.totalExecutionCost,
            "filledAt": fill.filledAt.isoformat().replace("+00:00", "Z"),
            "decisionId": order.get("decisionId"),
            "orderIntentId": order.get("orderIntentId"),
            "stopPrice": order.get("stopPrice"),
            "targetPrice": order.get("targetPrice"),
            "positionEffect": order.get("positionEffect"),
            "reservationReleaseAmount": release_leftover,
        }
        applier = getattr(self.repository, "apply_local_paper_fill_transaction", None)
        if not callable(applier):
            raise RuntimeError("Regime repository does not support atomic local paper fill application")
        result = applier(
            self.identity,
            order={**dict(order), "remainingQuantity": max(0, remaining), "status": status, "updatedAt": now},
            fill=fill_payload,
            orders_snapshot=updated_orders,
            fills_snapshot=[*self._fills, record],
            starting_balance=self.starting_balance,
        )
        if result.get("updated") or result.get("duplicate"):
            self._orders = tuple(updated_orders)
            self._fills = (*self._fills, record)

    def _mark_stop_triggered(self, order: Mapping[str, Any]) -> None:
        now = _utc_now().isoformat().replace("+00:00", "Z")
        self._orders = tuple(
            {**existing, "stopTriggered": True, "updatedAt": now, "reasonCodes": [*existing.get("reasonCodes", ()), "regime.local_paper.stop_triggered"]}
            if str(existing.get("clientOrderId")) == str(order.get("clientOrderId"))
            else existing
            for existing in self._orders
        )
        self._persist_orders()

    def _rejected_order(self, payload: Mapping[str, Any], reason: str) -> dict[str, Any]:
        now = _utc_now().isoformat().replace("+00:00", "Z")
        return {
            "algorithmId": REGIME_ALGORITHM_ID,
            "algorithmInstanceId": self.identity["algorithmInstanceId"],
            "accountId": self.account_id,
            "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
            "symbol": str(payload.get("symbol") or self.identity.get("symbol") or "SPY").upper(),
            "clientOrderId": str(payload.get("clientOrderId") or payload.get("client_order_id") or ""),
            "orderIntentId": str(payload.get("orderIntentId") or payload.get("clientOrderId") or ""),
            "status": "REJECTED",
            "createdAt": now,
            "updatedAt": now,
            "rejectedReason": reason,
            "reasonCodes": [reason],
        }

    def _account(self) -> RegimeLocalPaperAccount:
        snapshot = self.repository.read_local_paper_account_snapshot(self.identity)
        if snapshot is None:
            raise ValueError("regime.local_paper.account_missing_fail_closed")
        return RegimeLocalPaperAccount.from_snapshot(snapshot)

    def _assert_regime_payload(self, payload: Mapping[str, Any]) -> None:
        algorithm_id = str(payload.get("algorithmId") or payload.get("algorithm_id") or REGIME_ALGORITHM_ID)
        if algorithm_id != REGIME_ALGORITHM_ID:
            raise ValueError("Regime local paper broker rejects cross-algorithm payload")
        account_id = payload.get("accountId") or payload.get("account_id")
        if account_id not in (None, "") and str(account_id) != self.account_id:
            raise ValueError("Regime local paper broker rejects account mismatch")

    def _load_records(self, key: str, field: str) -> list[dict[str, Any]]:
        reader = getattr(self.repository, "read_runtime_snapshot", None)
        if not callable(reader):
            return []
        snapshot = reader(self.identity, key) or {}
        records = snapshot.get(field) if isinstance(snapshot, Mapping) else None
        return [dict(record) for record in records or [] if isinstance(record, Mapping) and str(record.get("algorithmId")) == REGIME_ALGORITHM_ID and str(record.get("accountId")) == self.account_id]

    def _persist_orders(self) -> None:
        self._write_snapshot(REGIME_LOCAL_PAPER_ORDERS_KEY, {"brokerVersion": REGIME_LOCAL_PAPER_BROKER_VERSION, "orders": list(self._orders)})

    def _persist_fills(self) -> None:
        self._write_snapshot(REGIME_LOCAL_PAPER_FILLS_KEY, {"brokerVersion": REGIME_LOCAL_PAPER_BROKER_VERSION, "fills": list(self._fills)})

    def _write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        writer = getattr(self.repository, "write_runtime_snapshot", None)
        if callable(writer):
            writer(self.identity, key, {"algorithmId": REGIME_ALGORITHM_ID, **snapshot})


def _identity(identity: Mapping[str, Any]) -> dict[str, str]:
    algorithm_id = str(identity.get("algorithmId") or identity.get("algorithm_id") or REGIME_ALGORITHM_ID)
    if algorithm_id != REGIME_ALGORITHM_ID:
        raise ValueError("Regime local paper broker requires Regime identity")
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "algorithmInstanceId": str(identity.get("algorithmInstanceId") or identity.get("algorithm_instance_id") or "regime-local-paper-default"),
        "accountId": str(identity.get("accountId") or identity.get("account_id") or "regime-local-paper-account"),
        "runtimeMode": RegimeRuntimeMode.LOCAL_PAPER.value,
        "symbol": str(identity.get("symbol") or "SPY").upper(),
    }


def _mapping_from_order(order: Any) -> dict[str, Any]:
    if isinstance(order, Mapping):
        return dict(order)
    if hasattr(order, "model_dump"):
        return dict(order.model_dump())
    return {name: getattr(order, name) for name in dir(order) if not name.startswith("_") and not callable(getattr(order, name))}


def _normal_side(value: Any) -> Signal:
    if isinstance(value, Signal):
        return value
    raw = str(value or "").strip().upper()
    if raw == "BUY":
        return Signal.BUY
    if raw == "SELL":
        return Signal.SELL
    raise ValueError("Regime local paper side must be BUY or SELL")


def _estimated_order_cost(
    order: Mapping[str, Any],
    *,
    commission_per_share: float,
    minimum_commission: float,
    slippage_per_share: float,
) -> float:
    if _normal_side(order.get("side")) != Signal.BUY:
        return 0.0
    quantity = int(order.get("remainingQuantity") or order.get("quantity") or 0)
    limit_price = _optional_float(order.get("stopLimitPrice") if str(order.get("orderType") or "").upper() == "STOP_LIMIT" else None) or _optional_float(order.get("limitPrice"))
    if quantity <= 0 or limit_price is None:
        return 0.0
    commission = max(minimum_commission if commission_per_share else 0.0, quantity * commission_per_share)
    slippage = quantity * slippage_per_share
    return round((quantity * limit_price) + commission + slippage, 10)


def _normalize_market_update(update: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(update, Mapping):
        raise ValueError("Regime local paper market update must be a mapping")
    symbol = str(update.get("symbol") or update.get("ticker") or "SPY").upper()
    bid = _optional_float(update.get("bid") or update.get("bidPrice") or update.get("bestBid"))
    ask = _optional_float(update.get("ask") or update.get("askPrice") or update.get("bestAsk"))
    quote_valid = bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "last": _optional_float(update.get("last") or update.get("lastPrice") or update.get("tradePrice") or update.get("close")),
        "high": _optional_float(update.get("high") or update.get("barHigh")),
        "low": _optional_float(update.get("low") or update.get("barLow")),
        "volume": int(float(update.get("volume") or update.get("barVolume") or 0)),
        "timestamp": _parse_dt(update.get("timestamp") or update.get("quoteTimestamp") or update.get("barTimestamp") or update.get("observedAt")) or _utc_now(),
        "quoteValid": quote_valid,
    }


def _mark_price(market: Mapping[str, Any]) -> float | None:
    last = _optional_float(market.get("last"))
    if last is not None:
        return last
    if market.get("quoteValid"):
        return round((float(market["bid"]) + float(market["ask"])) / 2.0, 10)
    return None


def _quote_reference(side: Signal, market: Mapping[str, Any]) -> float | None:
    if market.get("quoteValid"):
        return float(market["ask"] if side == Signal.BUY else market["bid"])
    return None


def _stop_triggered(side: Signal, stop_price: float, market: Mapping[str, Any]) -> bool:
    reference = _quote_reference(side, market)
    if reference is None:
        reference = _optional_float(market.get("last"))
    if reference is not None:
        return reference >= stop_price if side == Signal.BUY else reference <= stop_price
    if not (market.get("high") is not None and market.get("low") is not None):
        return False
    return float(market["high"]) >= stop_price if side == Signal.BUY else float(market["low"]) <= stop_price


def _fill_from_record(record: Mapping[str, Any]) -> PaperGatewayFill:
    allowed = set(PaperGatewayFill.model_fields)
    payload = {key: value for key, value in dict(record).items() if key in allowed}
    payload["side"] = _normal_side(payload.get("side"))
    payload["filledAt"] = _parse_dt(payload.get("filledAt")) or _utc_now()
    return PaperGatewayFill.model_validate(payload)


def _fill_id(fill: PaperGatewayFill) -> str:
    seed = {
        "clientOrderId": fill.clientOrderId,
        "quantity": fill.filledQuantity,
        "price": fill.averageFillPrice,
        "filledAt": fill.filledAt.isoformat(),
    }
    return f"regime-local-fill-{_stable_hash(seed)[:16]}"


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    return parsed if parsed > 0 else None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


__all__ = [
    "REGIME_LOCAL_PAPER_BROKER_VERSION",
    "RegimeLocalPaperBroker",
]




