from __future__ import annotations

from math import floor

from backend.app.domain.models import AccountRiskState, BaselineTradingSettings, Direction, HardRiskLimits, Signal, TradeCandidate
from backend.app.trading_policy.models import PositionSizingResult, ShareCap, StopComponent, StopPlan


def stop_distance(candidate: TradeCandidate) -> float:
    if candidate.stopPrice is None:
        return 0.0
    return abs(candidate.entryPrice - candidate.stopPrice)


def stop_plan(
    candidate: TradeCandidate,
    *,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
) -> StopPlan:
    components = stop_components(candidate, baseline_settings=baseline_settings, hard_limits=hard_limits)
    selected = max(components, key=lambda component: (component.distance, component.componentName))
    if candidate.direction == Direction.SHORT.value or candidate.signal == Signal.SELL.value:
        selected_price = candidate.entryPrice + selected.distance
    else:
        selected_price = max(0.01, candidate.entryPrice - selected.distance)
    return StopPlan(
        selectedStopDistance=round(selected.distance, 6),
        selectedStopPrice=round(selected_price, 6),
        limitingComponent=selected.componentName,
        components=components,
        explanation="Stop distance uses the widest necessary component so sizing cannot be inflated by an artificially tight stop.",
    )


def stop_components(
    candidate: TradeCandidate,
    *,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
) -> list[StopComponent]:
    atr_value = _feature_float(candidate, "atr", "atrValue", "ATR", default=0.0)
    spread_value = _feature_float(candidate, "spread", "spreadDollars", "bidAskSpread", default=0.0)
    structural_price = _feature_float(candidate, "structuralInvalidationPrice", "strategyStructuralInvalidationPrice", default=None)
    if structural_price is None and candidate.stopPrice is not None:
        structural_price = candidate.stopPrice
    atr_distance = atr_value * baseline_settings.baseAtrStopMultiplier
    minimum_percent_distance = candidate.entryPrice * (baseline_settings.baseMinimumStopPercent / 100.0)
    spread_distance = max(spread_value * 2.0, hard_limits.minStopDistanceDollars)
    structural_distance = abs(candidate.entryPrice - structural_price) if structural_price is not None else 0.0
    return [
        StopComponent(
            componentName="atrVolatilityStop",
            distance=round(max(0.0, atr_distance), 6),
            sourceValue=atr_value,
            explanation="ATR/volatility stop uses current ATR times the baseline ATR stop multiplier.",
        ),
        StopComponent(
            componentName="minimumPercentageStop",
            distance=round(max(0.0, minimum_percent_distance), 6),
            sourceValue=baseline_settings.baseMinimumStopPercent,
            explanation="Minimum percentage stop prevents overly tight stops relative to entry price.",
        ),
        StopComponent(
            componentName="spreadMicrostructureStop",
            distance=round(max(0.0, spread_distance), 6),
            sourceValue=spread_value,
            explanation="Spread/microstructure stop accounts for current spread and configured minimum stop distance.",
        ),
        StopComponent(
            componentName="strategyStructuralInvalidationStop",
            distance=round(max(0.0, structural_distance), 6),
            sourceValue=structural_price,
            explanation="Structural invalidation stop respects the strategy's invalidation level when available.",
        ),
    ]


def size_position(
    *,
    candidate: TradeCandidate,
    account: AccountRiskState,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
    approved_risk_dollars: float,
    maximum_notional: float,
) -> PositionSizingResult:
    plan = stop_plan(candidate, baseline_settings=baseline_settings, hard_limits=hard_limits)
    caps = share_caps(
        candidate=candidate,
        account=account,
        baseline_settings=baseline_settings,
        hard_limits=hard_limits,
        approved_risk_dollars=approved_risk_dollars,
        maximum_notional=maximum_notional,
        selected_stop_distance=plan.selectedStopDistance,
    )
    limiting = min(caps, key=lambda cap: (cap.shares, cap.capName))
    quantity = 0 if candidate.signal == Signal.HOLD.value else max(0, limiting.shares)
    planned_risk = quantity * plan.selectedStopDistance
    return PositionSizingResult(
        quantity=quantity,
        riskBasedShares=_cap_value(caps, "riskBasedShares"),
        limitingShareCap=limiting.capName,
        shareCaps=caps,
        plannedRiskDollars=round(planned_risk, 6),
        stopPlan=plan,
        explanation="Final quantity is floor(minimum of all independent share caps).",
    )


def share_caps(
    *,
    candidate: TradeCandidate,
    account: AccountRiskState,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
    approved_risk_dollars: float,
    maximum_notional: float,
    selected_stop_distance: float,
) -> list[ShareCap]:
    entry = max(candidate.entryPrice, 0.01)
    risk_based = floor(approved_risk_dollars / selected_stop_distance) if selected_stop_distance > 0 else 0
    order_notional = floor(maximum_notional / entry) if maximum_notional > 0 else 0
    position_limit_dollars = min(
        account.equity * (hard_limits.maximumPositionPercent / 100.0),
        hard_limits.maxPositionNotional,
    )
    position_remaining = max(0.0, position_limit_dollars - account.openPositionNotional)
    daily_remaining_dollars = _feature_float(candidate, "remainingDailyAllocationDollars", default=None)
    if daily_remaining_dollars is None:
        daily_remaining_dollars = account.equity * (baseline_settings.baseDailyAllocationPercent / 100.0)
    conservative_volume = conservative_volume_reference(candidate)
    liquidity_shares = floor(conservative_volume * (hard_limits.maximumVolumeParticipationPercent / 100.0)) if conservative_volume is not None else 0
    absolute_maximum = min(hard_limits.maximumShares, hard_limits.maxShareQuantity)
    global_remaining_notional = _feature_float(candidate, "globalExposureRemainingNotional", default=None)
    if global_remaining_notional is None:
        global_exposure_limit = account.equity * (hard_limits.maximumPositionPercent / 100.0)
        global_remaining_notional = max(0.0, global_exposure_limit - account.openPositionNotional)
    return [
        ShareCap(
            capName="riskBasedShares",
            shares=max(0, risk_based),
            sourceValue=approved_risk_dollars,
            explanation="Risk-based shares are approved risk divided by selected stop distance.",
        ),
        ShareCap(
            capName="orderNotionalShares",
            shares=max(0, order_notional),
            sourceValue=maximum_notional,
            explanation="Order notional shares respect order-level notional capacity.",
        ),
        ShareCap(
            capName="positionLimitShares",
            shares=max(0, floor(position_remaining / entry)),
            sourceValue=position_remaining,
            explanation="Position-limit shares respect remaining symbol position capacity.",
        ),
        ShareCap(
            capName="buyingPowerShares",
            shares=max(0, floor(account.buyingPower / entry)),
            sourceValue=account.buyingPower,
            explanation="Buying-power shares respect current account buying power.",
        ),
        ShareCap(
            capName="remainingDailyAllocationShares",
            shares=max(0, floor(max(0.0, daily_remaining_dollars) / entry)),
            sourceValue=daily_remaining_dollars,
            explanation="Daily allocation shares respect remaining daily allocation capacity.",
        ),
        ShareCap(
            capName="liquidityParticipationShares",
            shares=max(0, liquidity_shares),
            sourceValue=conservative_volume,
            explanation="Liquidity participation shares use current or conservative expected volume, not stale average alone.",
        ),
        ShareCap(
            capName="absoluteMaximumShares",
            shares=max(0, absolute_maximum),
            sourceValue=float(absolute_maximum),
            explanation="Absolute maximum shares enforce configured share hard limits.",
        ),
        ShareCap(
            capName="globalExposureShares",
            shares=max(0, floor(max(0.0, global_remaining_notional) / entry)),
            sourceValue=global_remaining_notional,
            explanation="Global exposure shares include cross-algorithm remaining exposure capacity.",
        ),
    ]


def quantity_from_risk(candidate: TradeCandidate, risk_dollars: float, maximum_notional: float, share_cap: int) -> int:
    if candidate.signal == Signal.HOLD.value:
        return 0
    distance = stop_distance(candidate)
    if distance <= 0 or candidate.entryPrice <= 0 or risk_dollars <= 0:
        return 0
    risk_quantity = floor(risk_dollars / distance)
    notional_quantity = floor(maximum_notional / candidate.entryPrice) if maximum_notional > 0 else 0
    return max(0, min(risk_quantity, notional_quantity, share_cap))


def conservative_volume_reference(candidate: TradeCandidate) -> float | None:
    current_volume = _feature_float(candidate, "currentVolume", "currentExpectedVolume", "currentOneMinuteVolume", default=None)
    expected_volume = _feature_float(candidate, "expectedVolume", "expectedOneMinuteVolume", default=None)
    if current_volume is not None and expected_volume is not None:
        return max(0.0, min(current_volume, expected_volume))
    if current_volume is not None:
        return max(0.0, current_volume)
    if expected_volume is not None:
        return max(0.0, expected_volume)
    return None


def _feature_float(candidate: TradeCandidate, *names: str, default: float | None = 0.0) -> float | None:
    for name in names:
        value = candidate.features.get(name)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric == numeric:
            return numeric
    return default


def _cap_value(caps: list[ShareCap], cap_name: str) -> int:
    for cap in caps:
        if cap.capName == cap_name:
            return cap.shares
    return 0
