"""Meta-Strategy local paper account and risk authority from backend settings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings, MetaStrategySettingsStore
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal


META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION = "meta_strategy_local_settings_risk_source_v1"
_AUTO_QUANTITY_CAP = 1_000_000_000
_TERMINAL_ORDER_STATUSES = frozenset(
    {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "DONE_FOR_DAY", "DEAD_LETTER"}
)


class MetaStrategyLocalSettingsRiskSource:
    """Uses Meta-Strategy settings plus its own inventory ledger as paper risk state."""

    source_kind = "meta_strategy_local_settings_risk"
    configured = True

    def __init__(
        self,
        *,
        settings_store: MetaStrategySettingsStore,
        inventory_repository: MetaStrategySqliteRepository,
    ) -> None:
        self.settings_store = settings_store
        self.inventory_repository = inventory_repository

    def read_account_snapshot(self, *, at: datetime) -> Mapping[str, Any]:
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot(as_of=at)
        equity = _account_equity(snapshot.allocated_capital, snapshot.realised_pnl, snapshot.unrealised_pnl, snapshot.fees_and_slippage)
        reserved_capital = _reserved_capital(self.inventory_repository)
        buying_power = _buying_power(equity, snapshot.reserved_risk_dollars, snapshot.symbol_exposure, reserved_capital)
        return {
            "source": self.source_kind,
            "sourceVersion": META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION,
            "authoritativeReadOnly": True,
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": snapshot.capital_partition_id,
            "settingsVersion": settings.settings_version,
            "capturedAt": min(_ensure_utc(at), datetime.now(UTC)).isoformat(),
            "accountId": f"{ALGORITHM_ID}:{snapshot.capital_partition_id}",
            "accountType": "paper",
            "liveTradingEnabled": False,
            "allocatedCapital": snapshot.allocated_capital,
            "accountEquity": equity,
            "buyingPower": buying_power,
            "cashAvailable": buying_power,
            "reservedRiskDollars": snapshot.reserved_risk_dollars,
            "reservedCapitalDollars": reserved_capital,
            "realisedPnl": snapshot.realised_pnl,
            "unrealisedPnl": snapshot.unrealised_pnl,
            "feesAndSlippage": snapshot.fees_and_slippage,
            "dailyTradeCount": snapshot.daily_trade_count,
            "dailyRealisedPnl": snapshot.daily_realised_pnl,
            "dailyRealizedPnl": snapshot.daily_realised_pnl,
            "paperAccountVerified": True,
            "accountAuthority": "meta_strategy_inventory.current_inventory_snapshot",
            "reasonCodes": ("meta_strategy.local_settings.account_snapshot_loaded",),
        }

    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str) -> Mapping[str, Any]:
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot(as_of=at)
        if capital_partition_id != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
            return _risk_snapshot(
                settings=settings,
                snapshot=snapshot,
                at=at,
                available_risk=0.0,
                max_quantity=0,
                reject=True,
                reasons=("meta_strategy.local_settings_risk.wrong_capital_partition",),
            )
        available_risk = _remaining_risk(settings, snapshot)
        max_quantity = _max_quantity(settings)
        return _risk_snapshot(
            settings=settings,
            snapshot=snapshot,
            at=at,
            available_risk=available_risk,
            max_quantity=max_quantity,
            reject=available_risk <= 0.0,
            reasons=_risk_snapshot_reasons(settings, snapshot, available_risk),
        )

    def approve_order(self, proposal: GlobalOrderProposal) -> GlobalGateResponse:
        evaluated_at = datetime.now(UTC)
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot(as_of=evaluated_at)
        reasons: list[str] = []
        if proposal.algorithmId != ALGORITHM_ID:
            reasons.append("meta_strategy.local_settings_risk.foreign_algorithm_rejected")
        if proposal.capitalPartitionId != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
            reasons.append("meta_strategy.local_settings_risk.wrong_capital_partition")
        account = self.read_account_snapshot(at=evaluated_at)
        available_risk = _remaining_risk(settings, snapshot)
        is_new_entry = str(proposal.intent) == "new_entry"
        max_quantity = min(int(proposal.quantity), _max_quantity(settings)) if is_new_entry else int(proposal.quantity)
        buying_power = float(account["buyingPower"])
        if is_new_entry:
            if float(account["accountEquity"]) <= 0.0:
                reasons.append("meta_strategy.sizing.zero_account_equity")
            if buying_power <= 0.0:
                reasons.append("meta_strategy.sizing.zero_buying_power")
            if _daily_loss_limit_exceeded(settings, snapshot):
                reasons.append("meta_strategy.local_settings_risk.daily_loss_limit_exceeded")
            if _maximum_open_risk_exceeded(settings, snapshot):
                reasons.append("meta_strategy.local_settings_risk.maximum_open_risk_exceeded")
            if available_risk <= 0.0:
                reasons.append("meta_strategy.sizing.zero_algorithm_risk")
            price = _proposal_price(proposal)
            if price > 0.0:
                buying_power_quantity = int(buying_power // price)
                if buying_power_quantity < max_quantity:
                    max_quantity = buying_power_quantity
                    reasons.append("meta_strategy.local_settings_risk.quantity_reduced_to_buying_power")
                maximum_position_notional = max(0.0, float(account["accountEquity"]) * float(settings.position_sizing.position_cap))
                existing_symbol_exposure = _symbol_exposure(snapshot.symbol_exposure, proposal.symbol)
                position_cap_quantity = int(max(0.0, maximum_position_notional - existing_symbol_exposure) // price)
                if position_cap_quantity < max_quantity:
                    max_quantity = position_cap_quantity
                    reasons.append("meta_strategy.local_settings_risk.quantity_reduced_to_position_cap")
            if proposal.plannedRiskDollars > available_risk and proposal.quantity > 0 and available_risk > 0.0:
                max_quantity = min(max_quantity, int(proposal.quantity * (available_risk / proposal.plannedRiskDollars)))
                reasons.append("meta_strategy.local_settings_risk.quantity_reduced_to_available_risk")
        if proposal.quantity <= 0 or max_quantity <= 0:
            reasons.append("meta_strategy.sizing.approved_quantity_zero")
        action = "ALLOW" if not reasons and max_quantity > 0 else "REJECT_NEW_ENTRY"
        if is_new_entry and any(reason in reasons for reason in ("meta_strategy.local_settings_risk.quantity_reduced_to_available_risk", "meta_strategy.local_settings_risk.quantity_reduced_to_buying_power", "meta_strategy.local_settings_risk.quantity_reduced_to_position_cap")) and max_quantity > 0:
            action = "REDUCE_QUANTITY"
        return GlobalGateResponse(
            action=action,  # type: ignore[arg-type]
            maximumAllowedQuantity=max(0, max_quantity if action != "REJECT_NEW_ENTRY" else 0),
            maximumAdditionalRiskDollars=max(0.0, min(float(proposal.plannedRiskDollars), available_risk)) if is_new_entry else 0.0,
            rejectionReasons=tuple(reasons),
            evaluatedAt=evaluated_at,
            configurationHash=f"{settings.settings_hash}:{snapshot.snapshot_id}",
        )


def _risk_snapshot(
    *,
    settings: MetaStrategySettings,
    snapshot: Any,
    at: datetime,
    available_risk: float,
    max_quantity: int,
    reject: bool,
    reasons: tuple[str, ...],
) -> Mapping[str, Any]:
    return {
        "source": "meta_strategy_local_settings_risk",
        "sourceVersion": META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION,
        "authoritativeReadOnly": True,
        "current": True,
        "status": "OK",
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": snapshot.capital_partition_id,
        "settingsVersion": settings.settings_version,
        "capturedAt": min(_ensure_utc(at), datetime.now(UTC)).isoformat(),
        "availableRiskDollars": max(0.0, float(available_risk)),
        "maxQuantity": max(0, int(max_quantity)),
        "reservedRiskDollars": snapshot.reserved_risk_dollars,
        "reject": reject,
        "tradingHalt": False,
        "reasonCodes": reasons,
    }


def _risk_snapshot_reasons(settings: MetaStrategySettings, snapshot: Any, available_risk: float) -> tuple[str, ...]:
    if available_risk > 0.0:
        return ("meta_strategy.local_settings_risk.snapshot_loaded",)
    reasons: list[str] = []
    if _daily_loss_limit_exceeded(settings, snapshot):
        reasons.append("meta_strategy.local_settings_risk.daily_loss_limit_exceeded")
    if _maximum_open_risk_exceeded(settings, snapshot):
        reasons.append("meta_strategy.local_settings_risk.maximum_open_risk_exceeded")
    reasons.append("meta_strategy.local_settings_risk.zero_available_risk")
    return tuple(dict.fromkeys(reasons))


def _daily_loss_limit_exceeded(settings: MetaStrategySettings, snapshot: Any) -> bool:
    daily_realised_pnl = float(getattr(snapshot, "daily_realised_pnl", snapshot.realised_pnl))
    return daily_realised_pnl <= -abs(float(settings.local_risk.maximum_daily_loss))


def _maximum_open_risk_exceeded(settings: MetaStrategySettings, snapshot: Any) -> bool:
    return float(snapshot.reserved_risk_dollars) >= max(0.0, float(settings.local_risk.maximum_open_risk))


def _remaining_risk(settings: MetaStrategySettings, snapshot: Any) -> float:
    equity = _account_equity(snapshot.allocated_capital, snapshot.realised_pnl, snapshot.unrealised_pnl, snapshot.fees_and_slippage)
    configured_trade_risk = equity * settings.local_risk.risk_percentage
    daily_realised_pnl = float(getattr(snapshot, "daily_realised_pnl", snapshot.realised_pnl))
    daily_loss_remaining = max(0.0, settings.local_risk.maximum_daily_loss + daily_realised_pnl)
    open_risk_remaining = max(0.0, settings.local_risk.maximum_open_risk - snapshot.reserved_risk_dollars)
    return round(max(0.0, min(configured_trade_risk, daily_loss_remaining, open_risk_remaining)), 10)


def _account_equity(allocated_capital: float, realised_pnl: float, unrealised_pnl: float, fees_and_slippage: float) -> float:
    return round(max(0.0, float(allocated_capital) + float(realised_pnl) + float(unrealised_pnl) - float(fees_and_slippage)), 10)


def _symbol_exposure(symbol_exposure: Mapping[str, float], symbol: str) -> float:
    normalized = str(symbol).upper()
    for key, value in symbol_exposure.items():
        if str(key).upper() == normalized:
            return abs(float(value))
    return 0.0

def _buying_power(equity: float, reserved_risk: float, symbol_exposure: Mapping[str, float], reserved_capital: float = 0.0) -> float:
    gross_exposure = sum(abs(float(value)) for value in symbol_exposure.values())
    return round(max(0.0, float(equity) - float(reserved_risk) - gross_exposure - float(reserved_capital)), 10)


def _reserved_capital(inventory_repository: MetaStrategySqliteRepository) -> float:
    try:
        intents = inventory_repository.inventory_records("order_intents", limit=500)
        orders = inventory_repository.inventory_records("orders", limit=500)
        fills = inventory_repository.inventory_records("fills", limit=500)
        statuses = inventory_repository.inventory_records("order_status_history", limit=500)
    except Exception:
        return 0.0
    latest_status = _latest_status_by_order(statuses)
    filled_quantity = _filled_quantity_by_order(fills)
    reserved_by_order: dict[str, float] = {}
    for row in (*intents, *orders):
        payload = _row_payload(row)
        if not _consumes_entry_capital(row, payload):
            continue
        order_key = _order_key(row, payload)
        if not order_key:
            continue
        status = latest_status.get(order_key) or _row_status(row, payload)
        if status in _TERMINAL_ORDER_STATUSES:
            continue
        quantity = _row_number(row, payload, "quantity", "orderQuantity", "submittedQuantity", "proposedQuantity")
        price = _row_number(row, payload, "limitPrice", "entryPrice", "price", "triggerPrice")
        remaining = max(0.0, quantity - filled_quantity.get(order_key, 0.0))
        if remaining <= 1e-9 or price <= 0.0:
            continue
        notional = round((remaining * price) + _estimated_order_cost(payload), 10)
        reserved_by_order[order_key] = max(reserved_by_order.get(order_key, 0.0), notional)
    return round(sum(reserved_by_order.values()), 10)


def _latest_status_by_order(rows: tuple[Mapping[str, Any], ...]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for row in rows:
        payload = _row_payload(row)
        status = _row_status(row, payload)
        if not status:
            continue
        for key in _order_identifiers(row, payload):
            statuses.setdefault(key, status)
    return statuses


def _filled_quantity_by_order(rows: tuple[Mapping[str, Any], ...]) -> dict[str, float]:
    filled: dict[str, float] = {}
    for row in rows:
        payload = _row_payload(row)
        quantity = _row_number(row, payload, "filledQuantity", "filled_quantity", "quantity")
        if quantity <= 0.0:
            continue
        for key in _order_identifiers(row, payload):
            filled[key] = round(filled.get(key, 0.0) + abs(quantity), 10)
    return filled


def _order_key(row: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    identifiers = _order_identifiers(row, payload)
    return identifiers[0] if identifiers else ""


def _order_identifiers(row: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, ...]:
    values = []
    for key in ("orderIntentId", "order_intent_id", "clientOrderId", "client_order_id", "brokerOrderId", "broker_order_id"):
        value = payload.get(key) if key in payload else row.get(key)
        if value:
            values.append(str(value))
    return tuple(dict.fromkeys(values))


def _row_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _row_status(row: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    value = _first_present(row, payload, "orderStatus", "order_status", "status", "fillStatus")
    return str(value or "").upper()


def _consumes_entry_capital(row: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    intent = str(_first_present(row, payload, "intent", "orderIntentType", "order_intent_type") or "new_entry").lower()
    side = str(_first_present(row, payload, "side") or "").upper()
    return intent in {"", "new_entry", "entry"} and side == "BUY"


def _row_number(row: Mapping[str, Any], payload: Mapping[str, Any], *keys: str) -> float:
    value = _first_present(row, payload, *keys)
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_present(row: Mapping[str, Any], payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
        if key in row and row[key] is not None:
            return row[key]
    return None


def _estimated_order_cost(payload: Mapping[str, Any]) -> float:
    return round(
        _number(payload, "commission", "estimatedCommission", "estimatedFees", "fees", "fee")
        + _number(payload, "estimatedSlippage", "estimated_slippage", "slippage"),
        10,
    )


def _number(payload: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _proposal_price(proposal: GlobalOrderProposal) -> float:
    for value in (proposal.limitPrice, proposal.triggerPrice):
        if value is not None and float(value) > 0.0:
            return float(value)
    return 0.0


def _max_quantity(settings: MetaStrategySettings) -> int:
    configured = int(settings.position_sizing.maximum_share_quantity)
    return configured if configured > 0 else _AUTO_QUANTITY_CAP


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION", "MetaStrategyLocalSettingsRiskSource"]
