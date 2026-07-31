"""Regime adapter for the shared global account-risk infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from backend.app.risk.manager import GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION, GlobalPortfolioRiskManager
from backend.app.risk.types import AccountSnapshot, GlobalOrderIntent, MarketSnapshot, PendingOrder, PortfolioPosition, PortfolioSnapshot

REGIME_GLOBAL_RISK_ADAPTER_VERSION = "regime_global_risk_adapter_v2"
REGIME_SHARED_GLOBAL_RISK_MANAGER = GlobalPortfolioRiskManager()


@dataclass(frozen=True)
class RegimeGlobalRiskRequest:
    algorithm_id: str
    decision_id: str
    order_intent_id: str
    symbol: str
    side: str
    requested_quantity: int
    requested_risk_dollars: float
    stop_price: float | None
    estimated_notional: float
    existing_regime_exposure: Mapping[str, Any] | float | int | None
    existing_account_exposure: Mapping[str, Any] | None
    settings_version: str
    algorithm_version: str
    expiration_timestamp: datetime | str
    idempotency_key: str
    entry_price: float | None = None
    target_price: float | None = None
    position_effect: str | None = None
    intent_type: str = "new_entry"
    order_type: str = "limit"
    account_snapshot: Mapping[str, Any] | None = None
    market_snapshot: Mapping[str, Any] | None = None
    portfolio_snapshot: Mapping[str, Any] | None = None
    profile_version: str = "unknown_profile"
    generated_at: datetime | str | None = None
    market_data_timestamp: datetime | str | None = None
    fractional_quantity_allowed: bool = False
    shortable: bool = True
    borrow_available: bool | None = None


@dataclass(frozen=True)
class RegimeGlobalRiskApproval:
    algorithm_id: str
    decision_id: str
    order_intent_id: str
    approved_quantity: int
    rejected: bool
    reason_codes: tuple[str, ...]
    reservation_id: str | None
    expiration_timestamp: str
    account_risk_snapshot_version: str
    status: str
    approved_risk_dollars: float
    account_snapshot_id: str | None
    idempotency_key: str
    evaluated_at: str
    signal_rewritten: bool = False
    settings_rewritten: bool = False
    stops_rewritten: bool = False


def evaluate_regime_global_risk_request(
    request: RegimeGlobalRiskRequest,
    *,
    manager: GlobalPortfolioRiskManager | None = None,
    reserve: bool = True,
) -> RegimeGlobalRiskApproval:
    manager = manager or REGIME_SHARED_GLOBAL_RISK_MANAGER
    evaluated_at = _as_utc(request.generated_at) if request.generated_at is not None else datetime.now(UTC)
    expires_at = _as_utc(request.expiration_timestamp)
    try:
        if request.algorithm_id != "regime":
            raise ValueError("algorithm_id must be regime")
        intent = _global_intent(request, evaluated_at, expires_at)
        account = _account_snapshot(request.account_snapshot, evaluated_at)
        market = _market_snapshot(request.market_snapshot, evaluated_at, intent.marketDataTimestamp)
        portfolio = _portfolio_snapshot(request, intent, evaluated_at)
        decision = manager.evaluate(intent=intent, account=account, market=market, portfolio=portfolio, evaluated_at=evaluated_at, reserve=reserve)
    except Exception:
        return _denied(
            request,
            evaluated_at=evaluated_at,
            expires_at=expires_at,
            reason_codes=("regime.global_risk_adapter.request_invalid",),
        )

    approved_quantity = min(max(0, decision.approvedQuantity), max(0, request.requested_quantity))
    reason_codes = _reason_codes(decision, approved_quantity, request.requested_quantity)
    return RegimeGlobalRiskApproval(
        algorithm_id="regime",
        decision_id=request.decision_id,
        order_intent_id=request.order_intent_id,
        approved_quantity=approved_quantity,
        rejected=decision.status == "denied" or approved_quantity <= 0 < request.requested_quantity,
        reason_codes=reason_codes,
        reservation_id=decision.reservationId,
        expiration_timestamp=expires_at.isoformat().replace("+00:00", "Z"),
        account_risk_snapshot_version=GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION,
        status=decision.status,
        approved_risk_dollars=decision.approvedRiskDollars if approved_quantity == decision.approvedQuantity else _scaled_risk(request.requested_risk_dollars, request.requested_quantity, approved_quantity),
        account_snapshot_id=decision.accountSnapshotId,
        idempotency_key=request.idempotency_key,
        evaluated_at=decision.evaluatedAt.isoformat().replace("+00:00", "Z"),
    )


def release_regime_global_risk_reservation(
    reservation_id: str | None,
    *,
    manager: GlobalPortfolioRiskManager | None = None,
) -> bool:
    if not reservation_id:
        return False
    (manager or REGIME_SHARED_GLOBAL_RISK_MANAGER).release_reservation(reservation_id)
    return True


def commit_regime_global_risk_reservation(
    reservation_id: str | None,
    *,
    manager: GlobalPortfolioRiskManager | None = None,
    broker_order_id: str | None = None,
) -> bool:
    if not reservation_id:
        return False
    (manager or REGIME_SHARED_GLOBAL_RISK_MANAGER).commit_reservation(reservation_id, broker_order_id=broker_order_id)
    return True


def regime_global_risk_adapter_inventory() -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "version": REGIME_GLOBAL_RISK_ADAPTER_VERSION,
        "sharedManagerVersion": GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION,
        "sharedBoundary": "global account-risk engine may reduce or reject quantity only",
        "reservationLifecycle": ("release_on_rejection", "release_on_cancellation", "release_on_expiration", "release_on_reconciliation_correction", "commit_on_final_fill"),
        "mayRewriteSignals": False,
        "mayRewriteSettings": False,
        "mayRewriteStops": False,
        "requiresAttribution": (
            "algorithm_id",
            "decision_id",
            "order_intent_id",
            "symbol",
            "side",
            "requested_quantity",
            "requested_risk_dollars",
            "stop_price",
            "estimated_notional",
            "existing_regime_exposure",
            "existing_account_exposure",
            "settings_version",
            "algorithm_version",
            "expiration_timestamp",
            "idempotency_key",
        ),
    }


def _global_intent(request: RegimeGlobalRiskRequest, generated_at: datetime, expires_at: datetime) -> GlobalOrderIntent:
    side = _side(request.side)
    quantity = max(0, int(request.requested_quantity))
    entry_price = _entry_price(request, quantity)
    market_data_timestamp = _as_utc(request.market_data_timestamp or _lookup(request.market_snapshot, "candleTimestamp", "barTimestamp", "timestamp") or generated_at)
    return GlobalOrderIntent(
        decisionId=request.decision_id,
        clientOrderId=request.idempotency_key,
        algorithmId="regime",
        symbol=str(request.symbol).upper(),
        side=side,
        positionEffect=request.position_effect or ("enter_long" if side == "Buy" else "enter_short"),
        intentType=request.intent_type,
        requestedQuantity=quantity,
        expectedEntryPrice=entry_price,
        protectiveStopPrice=_positive_or_none(request.stop_price),
        targetPrice=_positive_or_none(request.target_price),
        requestedRiskDollars=max(0.0, float(request.requested_risk_dollars or 0.0)),
        orderType=request.order_type,
        marketDataTimestamp=market_data_timestamp,
        generatedAt=generated_at,
        expiresAt=expires_at,
        settingsVersion=request.settings_version,
        profileVersion=request.profile_version,
        fractionalQuantityAllowed=request.fractional_quantity_allowed,
        shortable=request.shortable,
        borrowAvailable=request.borrow_available,
    )


def _account_snapshot(payload: Mapping[str, Any] | None, evaluated_at: datetime) -> AccountSnapshot:
    record = dict(payload or {})
    equity = _positive(_lookup(record, "equity", "accountEquity", "portfolioValue", "cashEquity"))
    buying_power = _number(_lookup(record, "availableBuyingPower", "buyingPower", "buying_power", "buyingPowerCurrent"))
    if equity is None or buying_power is None:
        raise ValueError("trusted account equity and buying power are required")
    return AccountSnapshot(
        accountSnapshotId=str(_lookup(record, "accountSnapshotId", "snapshotId", "id") or f"regime-account-{int(evaluated_at.timestamp())}"),
        accountId=str(_lookup(record, "accountId", "account_id") or "paper-account"),
        equity=equity,
        highWaterEquity=_positive(_lookup(record, "highWaterEquity", "high_water_equity")) or equity,
        availableBuyingPower=max(0.0, buying_power),
        settledCash=_nonnegative_or_none(_lookup(record, "settledCash", "cash")),
        realizedDailyPnl=float(_lookup(record, "realizedDailyPnl", "realized_daily_pnl", "dailyPnl") or 0.0),
        unrealizedDailyPnl=float(_lookup(record, "unrealizedDailyPnl", "unrealized_daily_pnl") or 0.0),
        brokerConnected=bool(_lookup(record, "brokerConnected", default=True)),
        brokerAccountActive=bool(_lookup(record, "brokerAccountActive", default=True)),
        tradingPermission=bool(_lookup(record, "tradingPermission", default=True)),
        clockSynchronized=bool(_lookup(record, "clockSynchronized", default=True)),
        accountSnapshotFresh=bool(_lookup(record, "accountSnapshotFresh", "buyingPowerCurrent", default=True)),
        localBrokerOrdersReconciled=bool(_lookup(record, "localBrokerOrdersReconciled", default=True)),
        localBrokerPositionsReconciled=bool(_lookup(record, "localBrokerPositionsReconciled", default=True)),
        unresolvedSubmissionFailure=bool(_lookup(record, "unresolvedSubmissionFailure", default=False)),
        brokerRateLimited=bool(_lookup(record, "brokerRateLimited", default=False)),
        observedAt=_as_utc(_lookup(record, "observedAt", "timestamp", "marketDataObservedAt", default=evaluated_at)),
    )


def _market_snapshot(payload: Mapping[str, Any] | None, evaluated_at: datetime, candle_timestamp: datetime) -> MarketSnapshot:
    record = dict(payload or {})
    quote_timestamp = _as_utc(_lookup(record, "quoteTimestamp", "quote_timestamp", "quoteObservedAt", default=evaluated_at))
    return MarketSnapshot(
        marketSnapshotId=str(_lookup(record, "marketSnapshotId", "snapshotId", "id") or f"regime-market-{int(evaluated_at.timestamp())}"),
        session=str(_lookup(record, "session", "marketSession", default="regular")),
        regularSessionAllowed=bool(_lookup(record, "regularSessionAllowed", default=True)),
        extendedHoursAllowed=bool(_lookup(record, "extendedHoursAllowed", default=False)),
        marketHoliday=bool(_lookup(record, "marketHoliday", default=False)),
        earlyClose=bool(_lookup(record, "earlyClose", default=False)),
        entryCutoffReached=bool(_lookup(record, "entryCutoffReached", default=False)),
        tradingHalt=bool(_lookup(record, "tradingHalt", default=False)),
        luld=bool(_lookup(record, "luld", default=False)),
        marketWideCircuitBreaker=bool(_lookup(record, "marketWideCircuitBreaker", default=False)),
        candleTimestamp=_as_utc(_lookup(record, "candleTimestamp", "barTimestamp", "timestamp", default=candle_timestamp)),
        quoteTimestamp=quote_timestamp,
        spreadPercent=_nonnegative_or_none(_lookup(record, "spreadPercent", "spreadPct")),
        oneMinuteVolume=_int_or_none(_lookup(record, "oneMinuteVolume", "volume")),
        estimatedSlippagePercent=_nonnegative_or_none(_lookup(record, "estimatedSlippagePercent")),
        eventBlackout=bool(_lookup(record, "eventBlackout", default=False)),
        unsupportedOrderType=bool(_lookup(record, "unsupportedOrderType", default=False)),
        evaluatedAt=evaluated_at,
    )


def _portfolio_snapshot(request: RegimeGlobalRiskRequest, intent: GlobalOrderIntent, evaluated_at: datetime) -> PortfolioSnapshot:
    payload = dict(request.portfolio_snapshot or request.existing_account_exposure or {})
    positions = [_portfolio_position(item, intent.symbol, intent.expectedEntryPrice) for item in payload.get("positions", ()) or ()]
    pending = [_pending_order(item, evaluated_at) for item in payload.get("pendingOrders", ()) or payload.get("pending_orders", ()) or ()]
    regime_position = _regime_exposure_position(request.existing_regime_exposure, intent.symbol, intent.expectedEntryPrice)
    if regime_position is not None:
        positions.append(regime_position)
    return PortfolioSnapshot(
        positions=tuple(item for item in positions if item is not None),
        pendingOrders=tuple(item for item in pending if item is not None),
        tradesToday=int(payload.get("tradesToday") or 0),
        algorithmTradesToday=dict(payload.get("algorithmTradesToday") or {}),
        ordersSubmittedInLastMinute=int(payload.get("ordersSubmittedInLastMinute") or 0),
    )


def _portfolio_position(payload: Any, default_symbol: str, price: float) -> PortfolioPosition | None:
    if not isinstance(payload, Mapping):
        return None
    quantity = int(_number(_lookup(payload, "quantity", "qty")) or 0)
    market_value = _nonnegative_or_none(_lookup(payload, "marketValue", "notional"))
    return PortfolioPosition(
        algorithmId=str(_lookup(payload, "algorithmId", "algorithm_id", default="shared")),
        symbol=str(_lookup(payload, "symbol", default=default_symbol)).upper(),
        quantity=quantity,
        marketValue=market_value if market_value is not None else abs(quantity) * price,
        openRiskDollars=max(0.0, float(_lookup(payload, "openRiskDollars", "riskDollars", default=0.0) or 0.0)),
        side=str(_lookup(payload, "side", default="long" if quantity >= 0 else "short")).lower(),
    )


def _regime_exposure_position(payload: Mapping[str, Any] | float | int | None, symbol: str, price: float) -> PortfolioPosition | None:
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        quantity = int(_number(_lookup(payload, "quantity", "qty")) or 0)
        market_value = _nonnegative_or_none(_lookup(payload, "marketValue", "notional", "positionNotional"))
        open_risk = max(0.0, float(_lookup(payload, "openRiskDollars", "reservedRisk", "riskDollars", default=0.0) or 0.0))
        side = str(_lookup(payload, "side", default="long" if quantity >= 0 else "short")).lower()
    else:
        market_value = max(0.0, float(payload))
        quantity = int(market_value / max(price, 0.01)) if market_value > 0 else 0
        open_risk = 0.0
        side = "long"
    if quantity == 0 and not market_value:
        return None
    return PortfolioPosition(algorithmId="regime", symbol=symbol, quantity=quantity, marketValue=market_value if market_value is not None else abs(quantity) * price, openRiskDollars=open_risk, side=side)


def _pending_order(payload: Any, evaluated_at: datetime) -> PendingOrder | None:
    if not isinstance(payload, Mapping):
        return None
    return PendingOrder(
        algorithmId=str(_lookup(payload, "algorithmId", "algorithm_id", default="shared")),
        symbol=str(_lookup(payload, "symbol", default="SPY")).upper(),
        side=_side(_lookup(payload, "side", default="Buy")),
        quantity=max(0, int(_number(_lookup(payload, "quantity", "qty")) or 0)),
        notional=max(0.0, float(_lookup(payload, "notional", default=0.0) or 0.0)),
        riskDollars=max(0.0, float(_lookup(payload, "riskDollars", "risk_dollars", default=0.0) or 0.0)),
        decisionId=str(_lookup(payload, "decisionId", "decision_id", default="unknown-decision")),
        clientOrderId=_lookup(payload, "clientOrderId", "client_order_id"),
        intentKey=str(_lookup(payload, "intentKey", "idempotencyKey", default="unknown-intent-key")),
        submittedAt=_as_utc(_lookup(payload, "submittedAt", default=evaluated_at)),
    )


def _reason_codes(decision: Any, approved_quantity: int, requested_quantity: int) -> tuple[str, ...]:
    codes: list[str] = [f"regime.global_risk_adapter.{decision.status}"]
    if approved_quantity < requested_quantity:
        codes.append("regime.global_risk_adapter.quantity_reduced")
    for group, prefix in ((decision.failedGates, "failed"), (decision.warningGates, "warning"), (decision.passedGates, "passed")):
        for gate in group:
            codes.append(f"global_risk.{prefix}.{gate.gateId}")
    return tuple(dict.fromkeys(codes))


def _denied(request: RegimeGlobalRiskRequest, *, evaluated_at: datetime, expires_at: datetime, reason_codes: tuple[str, ...]) -> RegimeGlobalRiskApproval:
    return RegimeGlobalRiskApproval(
        algorithm_id="regime",
        decision_id=request.decision_id,
        order_intent_id=request.order_intent_id,
        approved_quantity=0,
        rejected=True,
        reason_codes=reason_codes,
        reservation_id=None,
        expiration_timestamp=expires_at.isoformat().replace("+00:00", "Z"),
        account_risk_snapshot_version=GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION,
        status="denied",
        approved_risk_dollars=0.0,
        account_snapshot_id=None,
        idempotency_key=request.idempotency_key,
        evaluated_at=evaluated_at.isoformat().replace("+00:00", "Z"),
    )


def _lookup(payload: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not payload:
        return default
    for key in keys:
        if key in payload:
            return payload[key]
    return default


def _as_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _side(value: Any) -> str:
    side = str(value)
    if side in {"Buy", "Sell"}:
        return side
    if side.lower() == "buy":
        return "Buy"
    if side.lower() == "sell":
        return "Sell"
    raise ValueError("side must be Buy or Sell")


def _entry_price(request: RegimeGlobalRiskRequest, quantity: int) -> float:
    if request.entry_price is not None and request.entry_price > 0:
        return float(request.entry_price)
    if quantity > 0 and request.estimated_notional > 0:
        return float(request.estimated_notional) / quantity
    raise ValueError("entry price or estimated notional is required")


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _positive_or_none(value: Any) -> float | None:
    return _positive(value)


def _nonnegative_or_none(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scaled_risk(requested_risk_dollars: float, requested_quantity: int, approved_quantity: int) -> float:
    if requested_quantity <= 0:
        return 0.0
    return round(max(0.0, requested_risk_dollars) * max(0, approved_quantity) / requested_quantity, 6)


__all__ = [
    "REGIME_GLOBAL_RISK_ADAPTER_VERSION",
    "REGIME_SHARED_GLOBAL_RISK_MANAGER",
    "RegimeGlobalRiskApproval",
    "RegimeGlobalRiskRequest",
    "commit_regime_global_risk_reservation",
    "evaluate_regime_global_risk_request",
    "regime_global_risk_adapter_inventory",
    "release_regime_global_risk_reservation",
]
