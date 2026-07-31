"""Backend-owned Regime risk and position sizing."""

from __future__ import annotations

from math import floor
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeDecision, RegimeMarketSnapshot, RegimeSizingResult


REGIME_SIZING_VERSION = "regime_local_risk_sizing_v2"


def calculate_regime_position_size(
    decision: RegimeDecision,
    snapshot: RegimeMarketSnapshot,
    account: dict | None = None,
    inventory: dict | None = None,
    daily_risk_state: dict | None = None,
) -> RegimeSizingResult:
    profile = decision.effective_settings
    latest_price = max(0.01, float(snapshot.latest.close))
    account_snapshot = account if isinstance(account, dict) else {}
    inventory_snapshot = inventory if isinstance(inventory, dict) else {}
    daily_state = daily_risk_state if isinstance(daily_risk_state, dict) else {}
    blockers: list[str] = []

    if decision.signal == "Hold" or not decision.trade_allowed:
        return _blocked_result(decision, latest_price, profile, tuple(decision.trade_blockers or ("regime.sizing.decision_hold",)))
    if snapshot.symbol.upper() not in {str(symbol).upper() for symbol in profile.get("symbolAllowlist", ["SPY"])}:
        blockers.append("regime.sizing.symbol_not_allowed")
    if decision.signal == "Sell" and not bool(profile.get("shortEntriesEnabled") or profile.get("allowShortEntries")):
        blockers.append("regime.sizing.short_entries_disabled")
    if not bool(profile.get("paperOnly", True)):
        blockers.append("regime.sizing.paper_only_required")

    account_validation = _trusted_account_values(account_snapshot)
    inventory_validation = _trusted_inventory_values(inventory_snapshot, symbol=snapshot.symbol)
    blockers.extend(account_validation["blockers"])
    blockers.extend(inventory_validation["blockers"])

    stop_distance = _stop_distance(decision, latest_price, profile)
    if bool(profile.get("mandatoryStop", True)) and stop_distance <= 0:
        blockers.append("regime.sizing.mandatory_stop_distance_missing")
    if bool(profile.get("mandatoryMaxHoldingTime", True)) and int(profile.get("maxHoldingBars") or 0) <= 0:
        blockers.append("regime.sizing.maximum_holding_time_required")

    open_order_quantity = int(inventory_validation["open_order_quantity"])
    current_quantity = int(inventory_validation["quantity"])
    if open_order_quantity > 0:
        blockers.append("regime.sizing.open_entry_order_exists")
    if current_quantity != 0 and not bool(profile.get("pyramidingEnabled", False)):
        blockers.append("regime.sizing.existing_regime_position")
    if current_quantity != 0 and int(profile.get("maxOpenRegimePositions", 1)) <= 1:
        blockers.append("regime.sizing.max_open_regime_positions")
    if int(daily_state.get("entryCount") or daily_state.get("tradeCount") or daily_state.get("totalTrades") or 0) >= int(profile.get("maxEntriesPerDay", profile.get("maxTradesPerDay", 0))):
        blockers.append("regime.sizing.max_entries_per_day")
    if _number(daily_state.get("dailyLossPercent")) is not None and float(daily_state["dailyLossPercent"]) >= float(profile.get("maxDailyLossPercent", 0.0)):
        blockers.append("regime.sizing.daily_loss_cap")

    if blockers:
        return _blocked_result(decision, latest_price, profile, tuple(dict.fromkeys(blockers)), stop_distance=stop_distance)

    equity = float(account_validation["equity"])
    buying_power = float(account_validation["buying_power"])
    available_risk = float(account_validation["remaining_risk"])
    confidence_multiplier = _confidence_multiplier(decision)
    per_trade_risk = min(equity * float(profile["baseRiskPercent"]) / 100.0, available_risk) * confidence_multiplier
    risk_quantity = floor(per_trade_risk / max(stop_distance, 0.01))

    capital_notional_cap = min(
        equity * float(profile["maxPositionPercent"]) / 100.0,
        equity * float(profile.get("dailyAllocationPercent", 100.0)) / 100.0,
        buying_power,
        _positive_or_large(profile.get("maxOrderNotionalDollars") or profile.get("maxNotionalDollars")),
        _positive_or_large(profile.get("maxPositionNotionalDollars") or profile.get("maxNotionalDollars")),
    )
    capital_quantity = floor(capital_notional_cap / latest_price)

    expected_fill_quantity = _expected_fill_quantity(snapshot)
    liquidity_quantity = expected_fill_quantity if expected_fill_quantity is not None else floor(max(0.0, float(snapshot.latest.volume)))
    participation_quantity = floor(max(0.0, float(snapshot.latest.volume)) * float(profile["maxParticipationPercent"]))
    if expected_fill_quantity is not None:
        participation_quantity = min(participation_quantity, expected_fill_quantity)
    liquidity_participation_quantity = max(0, min(liquidity_quantity, participation_quantity))

    inventory_trade_quantity = int(profile.get("maxAllowedShares") or 0)
    remaining_position_quantity = floor(max(0.0, (float(profile.get("maxPositionNotionalDollars") or profile.get("maxNotionalDollars") or 0.0) - _current_notional(inventory_snapshot, latest_price)) / latest_price))
    if remaining_position_quantity > 0:
        inventory_trade_quantity = min(inventory_trade_quantity, remaining_position_quantity) if inventory_trade_quantity > 0 else remaining_position_quantity

    caps = (
        _cap("regime_risk_based_quantity", risk_quantity, per_trade_risk, "regime.sizing.sequence.risk_based_quantity"),
        _cap("regime_capital_cap", capital_quantity, capital_notional_cap, "regime.sizing.sequence.capital_cap"),
        _cap("regime_liquidity_participation_cap", liquidity_participation_quantity, liquidity_quantity, "regime.sizing.sequence.liquidity_participation_cap"),
        _cap("regime_inventory_trade_count_cap", inventory_trade_quantity, current_quantity, "regime.sizing.sequence.inventory_trade_count_cap"),
    )
    quantity = max(0, min(cap["quantity"] for cap in caps))
    limiting = next(cap["label"] for cap in caps if cap["quantity"] == quantity)
    if quantity <= 0:
        blockers.append("regime.sizing.quantity_reduced_to_zero")
    stop_price, target_price = _protection_prices(decision.signal, latest_price, stop_distance, profile)
    return RegimeSizingResult(
        quantity=quantity if not blockers else 0,
        risk_dollars=round(quantity * stop_distance, 6) if not blockers else 0.0,
        stop_distance=round(stop_distance, 6),
        stop_price=stop_price,
        target_price=target_price,
        limiting_factor=limiting if not blockers else "blocked",
        quantity_caps=(
            *caps,
            {
                "label": "shared_global_account_risk_reduction_or_rejection",
                "quantity": quantity,
                "basis": quantity,
                "reasonCode": "regime.sizing.sequence.awaiting_shared_global_risk",
            },
        ),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _trusted_account_values(account: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if not account or str(account.get("sourceAuthority") or "").lower() in {"", "shared_backend_unavailable", "api", "frontend"}:
        blockers.append("regime.sizing.account_snapshot_unavailable")
    if account.get("buyingPowerCurrent") is False or account.get("accountSnapshotFresh") is False:
        blockers.append("regime.sizing.account_snapshot_stale")
    equity = _number(account.get("equity") or account.get("accountEquity") or account.get("portfolioValue"))
    buying_power = _number(account.get("availableBuyingPower") or account.get("buyingPower"))
    remaining_risk = _number(account.get("remainingRegimeRiskDollars") or account.get("remainingAlgorithmRiskDollars") or account.get("availableRiskDollars"))
    if equity is None:
        blockers.append("regime.sizing.account_equity_required")
    if buying_power is None:
        blockers.append("regime.sizing.buying_power_required")
    if remaining_risk is None:
        remaining_risk = equity if equity is not None else 0.0
    return {
        "blockers": blockers,
        "equity": max(0.0, equity or 0.0),
        "buying_power": max(0.0, buying_power or 0.0),
        "remaining_risk": max(0.0, remaining_risk),
    }


def _trusted_inventory_values(inventory: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    blockers: list[str] = []
    if not inventory or str(inventory.get("algorithmId") or "").lower() != "regime":
        blockers.append("regime.sizing.inventory_snapshot_unavailable")
    if str(inventory.get("symbol") or symbol).upper() != symbol.upper():
        blockers.append("regime.sizing.inventory_symbol_mismatch")
    if inventory.get("reconciled") is False or inventory.get("inventoryReconciled") is False:
        blockers.append("regime.sizing.inventory_not_reconciled")
    return {
        "blockers": blockers,
        "quantity": int(_number(inventory.get("quantity")) or 0),
        "open_order_quantity": int(_number(inventory.get("openOrderQuantity") or inventory.get("open_order_quantity")) or 0),
    }


def _blocked_result(decision: RegimeDecision, latest_price: float, profile: dict[str, Any], blockers: tuple[str, ...], *, stop_distance: float | None = None) -> RegimeSizingResult:
    distance = stop_distance if stop_distance is not None else _stop_distance(decision, latest_price, profile)
    stop_price, target_price = _protection_prices(decision.signal, latest_price, distance, profile)
    return RegimeSizingResult(
        quantity=0,
        risk_dollars=0.0,
        stop_distance=round(distance, 6),
        stop_price=stop_price,
        target_price=target_price,
        limiting_factor="blocked",
        quantity_caps=(
            {
                "label": "blocked_before_sizing",
                "quantity": 0,
                "basis": 0,
                "reasonCode": "regime.sizing.blocked_before_quantity",
            },
        ),
        blockers=blockers,
    )


def _stop_distance(decision: RegimeDecision, latest_price: float, profile: dict[str, Any]) -> float:
    features = decision.raw_classification.features
    atr_value = _number(features.get("atr"))
    fallback = latest_price * float(profile["minimumStopDistancePercent"]) / 100.0
    raw_distance = max((atr_value or fallback) * float(profile["atrStopMultiplier"]), fallback)
    return max(0.0, raw_distance)


def _protection_prices(signal: str, latest_price: float, stop_distance: float, profile: dict[str, Any]) -> tuple[float | None, float | None]:
    if stop_distance <= 0:
        return None, None
    if signal == "Buy":
        return round(latest_price - stop_distance, 6), round(latest_price + (stop_distance * float(profile["takeProfitR"])), 6)
    if signal == "Sell":
        return round(latest_price + stop_distance, 6), round(latest_price - (stop_distance * float(profile["takeProfitR"])), 6)
    return None, None


def _confidence_multiplier(decision: RegimeDecision) -> float:
    regime_confidence = _number(getattr(decision.confirmed_state, "regime_confidence", None) or getattr(decision.confirmed_state, "transition_confidence", None))
    signal_confidence = _number(decision.confidence)
    return max(0.10, min(1.0, regime_confidence or 0.0, signal_confidence or 0.0))


def _expected_fill_quantity(snapshot: RegimeMarketSnapshot) -> int | None:
    quote = snapshot.context_feeds.get("quoteFreshness") or snapshot.context_feeds.get("quote") or {}
    if not isinstance(quote, dict):
        return None
    value = _number(quote.get("expectedFillQuantity") or quote.get("displayedSize") or quote.get("topOfBookSize"))
    return None if value is None else max(0, floor(value))


def _current_notional(inventory: dict[str, Any], latest_price: float) -> float:
    notional = _number(inventory.get("notional") or inventory.get("marketValue"))
    if notional is not None:
        return max(0.0, notional)
    return abs(int(_number(inventory.get("quantity")) or 0)) * latest_price


def _positive_or_large(value: Any) -> float:
    number = _number(value)
    return 10**18 if number is None or number <= 0 else number


def _cap(label: str, quantity: int, basis: float, reason_code: str) -> dict[str, Any]:
    return {"label": label, "quantity": max(0, int(quantity)), "basis": round(float(basis or 0.0), 6), "reasonCode": reason_code}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["REGIME_SIZING_VERSION", "calculate_regime_position_size"]
