"""WCA dynamic effective-settings resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import (
    WcaAlgorithmRiskStatus,
    WcaBaselineSettings,
    WcaDataQualityStatus,
    WcaDynamicProfile,
    WcaEffectiveSettings,
    WcaEventRiskStatus,
    WcaLiquidityStatus,
    WcaMarketStatus,
    WcaSessionStatus,
    WcaVolatilityStatus,
)


@dataclass(frozen=True)
class WcaDynamicProfileConfig:
    enabled: bool = True
    profile_version: str = "wca_dynamic_profile_v1"
    minimum_profile_hold_seconds: int = 300
    profile_ttl_seconds: int = 900
    drawdown_reduced_threshold_percent: float = 1.0
    drawdown_defensive_threshold_percent: float = 2.0
    drawdown_stop_threshold_percent: float = 3.0


@dataclass(frozen=True)
class _Overlay:
    name: str
    risk_multiplier: float = 1.0
    quantity_multiplier: float = 1.0
    allocation_multiplier: float = 1.0
    threshold_adjustment: float = 0.0
    agreement_adjustment: float = 0.0
    confidence_adjustment: float = 0.0
    max_trades_multiplier: float = 1.0
    cooldown_seconds: int = 0
    slippage_multiplier: float = 1.0
    stop_multiplier_cap: float = 1.0
    block_entries: bool = False


BASELINE_OVERLAY = _Overlay("baseline")


def resolve_dynamic_profile(
    *,
    baseline: WcaBaselineSettings,
    market_status: WcaMarketStatus,
    calculation_timestamp: datetime | None = None,
    previous_profile: WcaDynamicProfile | None = None,
    current_drawdown_percent: float = 0,
    config: WcaDynamicProfileConfig = WcaDynamicProfileConfig(),
) -> WcaDynamicProfile:
    calculated_at = calculation_timestamp or datetime.now(timezone.utc)
    if not config.enabled:
        return _profile_from_effective(
            _baseline_effective_settings(baseline, market_status, calculated_at, "wca_dynamic_profile_disabled_v1"),
            market_status,
            calculated_at,
            timedelta(seconds=config.profile_ttl_seconds),
            ("wca.dynamic_profile.disabled",),
        )

    overlays = _active_overlays(market_status, current_drawdown_percent, config)
    effective = _effective_from_overlays(baseline, market_status, overlays, calculated_at, config)
    proposed = _profile_from_effective(
        effective,
        market_status,
        calculated_at,
        timedelta(seconds=config.profile_ttl_seconds),
        ("wca.dynamic_profile.calculated",),
    )
    if previous_profile is None:
        return proposed
    if _defensiveness_score(proposed.effective_settings) >= _defensiveness_score(previous_profile.effective_settings):
        return proposed
    previous_age = (calculated_at - previous_profile.calculation_timestamp).total_seconds()
    if previous_age < config.minimum_profile_hold_seconds and previous_profile.expiration_timestamp > calculated_at:
        return previous_profile.model_copy(
            update={"reason_codes": (*previous_profile.reason_codes, "wca.dynamic_profile.hold_previous")}
        )
    return proposed


def protective_stop_distance_for_existing_position(*, current_stop_distance: float, proposed_stop_distance: float) -> float:
    return min(current_stop_distance, proposed_stop_distance)


def _baseline_effective_settings(
    baseline: WcaBaselineSettings,
    market_status: WcaMarketStatus,
    calculated_at: datetime,
    profile_version: str,
) -> WcaEffectiveSettings:
    return WcaEffectiveSettings(
        baseline=baseline,
        baseline_settings_version=baseline.settings_version,
        profile_id="baseline",
        profile_version=profile_version,
        market_status=market_status,
        active_overlays=("baseline",),
        effective_at=calculated_at,
        expiration_timestamp=calculated_at + timedelta(minutes=15),
        risk_multiplier=1,
        quantity_multiplier=1,
        allocation_multiplier=1,
        entry_strictness_multiplier=1,
        threshold_adjustment=0,
        final_risk_percent=min(baseline.base_risk_percent, baseline.hard_max_risk_percent),
        final_order_allocation_percent=min(baseline.order_allocation_percent, baseline.hard_max_order_allocation_percent),
        final_daily_allocation_percent=min(baseline.daily_allocation_percent, baseline.hard_max_daily_allocation_percent),
        final_max_position_percent=min(baseline.max_position_percent, baseline.hard_max_position_percent),
        final_max_daily_loss_percent=min(baseline.max_daily_loss_percent, baseline.hard_max_daily_loss_percent),
        final_max_daily_trades=baseline.max_daily_trades,
        final_max_allowed_shares=_min_nonzero_cap(baseline.max_allowed_shares, baseline.hard_max_allowed_shares),
        final_minimum_score=baseline.minimum_score,
        final_minimum_agreement=baseline.minimum_directional_agreement,
        final_minimum_confidence=baseline.minimum_average_confidence,
        final_atr_stop_multiplier=baseline.atr_stop_multiplier,
        final_minimum_stop_distance_percent=baseline.minimum_stop_distance_percent,
        final_take_profit_r=baseline.take_profit_r,
        final_assumed_slippage_per_share=baseline.assumed_slippage_per_share,
        final_cooldown_seconds=baseline.cooldown_seconds,
        final_entry_cutoff_minutes=baseline.entry_cutoff_minutes,
        final_pyramiding_enabled=baseline.pyramiding_enabled,
        entries_blocked=False,
        reason_codes=("wca.dynamic_profile.baseline",),
    )


def _effective_from_overlays(
    baseline: WcaBaselineSettings,
    market_status: WcaMarketStatus,
    overlays: tuple[_Overlay, ...],
    calculated_at: datetime,
    config: WcaDynamicProfileConfig,
) -> WcaEffectiveSettings:
    risk_multiplier = min(overlay.risk_multiplier for overlay in overlays)
    quantity_multiplier = min(overlay.quantity_multiplier for overlay in overlays)
    allocation_multiplier = min(overlay.allocation_multiplier for overlay in overlays)
    threshold_adjustment = max(overlay.threshold_adjustment for overlay in overlays)
    agreement_adjustment = max(overlay.agreement_adjustment for overlay in overlays)
    confidence_adjustment = max(overlay.confidence_adjustment for overlay in overlays)
    max_trades_multiplier = min(overlay.max_trades_multiplier for overlay in overlays)
    cooldown_seconds = max(baseline.cooldown_seconds, max(overlay.cooldown_seconds for overlay in overlays))
    slippage_multiplier = max(overlay.slippage_multiplier for overlay in overlays)
    stop_multiplier_cap = min(overlay.stop_multiplier_cap for overlay in overlays)
    entries_blocked = any(overlay.block_entries for overlay in overlays)

    return WcaEffectiveSettings(
        baseline=baseline,
        baseline_settings_version=baseline.settings_version,
        profile_id=_profile_id(overlays),
        profile_version=config.profile_version,
        market_status=market_status,
        active_overlays=tuple(overlay.name for overlay in overlays),
        effective_at=calculated_at,
        expiration_timestamp=calculated_at + timedelta(seconds=config.profile_ttl_seconds),
        risk_multiplier=risk_multiplier,
        quantity_multiplier=quantity_multiplier,
        allocation_multiplier=allocation_multiplier,
        entry_strictness_multiplier=1 + threshold_adjustment,
        threshold_adjustment=threshold_adjustment,
        final_risk_percent=min(baseline.base_risk_percent * risk_multiplier, baseline.base_risk_percent, baseline.hard_max_risk_percent),
        final_order_allocation_percent=min(baseline.order_allocation_percent * allocation_multiplier, baseline.order_allocation_percent, baseline.hard_max_order_allocation_percent),
        final_daily_allocation_percent=min(baseline.daily_allocation_percent * allocation_multiplier, baseline.daily_allocation_percent, baseline.hard_max_daily_allocation_percent),
        final_max_position_percent=min(baseline.max_position_percent * quantity_multiplier, baseline.max_position_percent, baseline.hard_max_position_percent),
        final_max_daily_loss_percent=min(baseline.max_daily_loss_percent, baseline.hard_max_daily_loss_percent),
        final_max_daily_trades=max(0, int(baseline.max_daily_trades * max_trades_multiplier)),
        final_max_allowed_shares=_scaled_share_cap(baseline.max_allowed_shares, baseline.hard_max_allowed_shares, quantity_multiplier),
        final_minimum_score=min(1, baseline.minimum_score + threshold_adjustment),
        final_minimum_agreement=min(1, baseline.minimum_directional_agreement + agreement_adjustment),
        final_minimum_confidence=min(1, baseline.minimum_average_confidence + confidence_adjustment),
        final_atr_stop_multiplier=min(baseline.atr_stop_multiplier, baseline.atr_stop_multiplier * stop_multiplier_cap),
        final_minimum_stop_distance_percent=baseline.minimum_stop_distance_percent,
        final_take_profit_r=baseline.take_profit_r,
        final_assumed_slippage_per_share=baseline.assumed_slippage_per_share * slippage_multiplier,
        final_cooldown_seconds=cooldown_seconds,
        final_entry_cutoff_minutes=baseline.entry_cutoff_minutes,
        final_pyramiding_enabled=baseline.pyramiding_enabled and False,
        entries_blocked=entries_blocked,
        reason_codes=("wca.dynamic_profile.effective",),
    )


def _active_overlays(
    market_status: WcaMarketStatus,
    current_drawdown_percent: float,
    config: WcaDynamicProfileConfig,
) -> tuple[_Overlay, ...]:
    overlays = [BASELINE_OVERLAY]
    if market_status.trend in {"strong_uptrend", "strong_downtrend"}:
        overlays.append(_Overlay("trend.strong", threshold_adjustment=0.01))
    if market_status.volatility == WcaVolatilityStatus.VERY_LOW.value:
        overlays.append(_Overlay("volatility.very_low", risk_multiplier=0.70, quantity_multiplier=0.80, threshold_adjustment=0.03))
    elif market_status.volatility == WcaVolatilityStatus.HIGH.value:
        overlays.append(_Overlay("volatility.high", risk_multiplier=0.50, quantity_multiplier=0.60, allocation_multiplier=0.70, threshold_adjustment=0.06, confidence_adjustment=0.05, slippage_multiplier=1.5, stop_multiplier_cap=0.90))
    elif market_status.volatility == WcaVolatilityStatus.EXTREME.value:
        overlays.append(_Overlay("volatility.extreme", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, threshold_adjustment=0.15, confidence_adjustment=0.10, cooldown_seconds=900, slippage_multiplier=2.0, block_entries=True))
    if market_status.liquidity == WcaLiquidityStatus.THIN.value:
        overlays.append(_Overlay("liquidity.thin", risk_multiplier=0.60, quantity_multiplier=0.50, allocation_multiplier=0.60, threshold_adjustment=0.05, agreement_adjustment=0.05, slippage_multiplier=1.75, cooldown_seconds=300))
    elif market_status.liquidity == WcaLiquidityStatus.UNSAFE.value:
        overlays.append(_Overlay("liquidity.unsafe", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, threshold_adjustment=0.20, slippage_multiplier=2.0, block_entries=True))
    if market_status.session in {WcaSessionStatus.OPENING.value, WcaSessionStatus.CLOSING.value}:
        overlays.append(_Overlay(f"session.{market_status.session}", risk_multiplier=0.75, quantity_multiplier=0.80, threshold_adjustment=0.02, cooldown_seconds=120))
    if market_status.event_risk == WcaEventRiskStatus.ELEVATED.value:
        overlays.append(_Overlay("event.elevated", risk_multiplier=0.50, quantity_multiplier=0.50, threshold_adjustment=0.08, cooldown_seconds=600))
    elif market_status.event_risk == WcaEventRiskStatus.BLOCKED.value:
        overlays.append(_Overlay("event.blocked", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, threshold_adjustment=0.25, block_entries=True))
    if market_status.data_quality == WcaDataQualityStatus.DEGRADED.value:
        overlays.append(_Overlay("data_quality.degraded", risk_multiplier=0.50, quantity_multiplier=0.50, threshold_adjustment=0.06))
    elif market_status.data_quality == WcaDataQualityStatus.INVALID.value:
        overlays.append(_Overlay("data_quality.invalid", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, threshold_adjustment=0.25, block_entries=True))
    if market_status.algorithm_risk == WcaAlgorithmRiskStatus.REDUCED.value:
        overlays.append(_Overlay("algorithm_risk.reduced", risk_multiplier=0.75, quantity_multiplier=0.75, max_trades_multiplier=0.75))
    elif market_status.algorithm_risk == WcaAlgorithmRiskStatus.DEFENSIVE.value:
        overlays.append(_Overlay("algorithm_risk.defensive", risk_multiplier=0.40, quantity_multiplier=0.50, allocation_multiplier=0.50, threshold_adjustment=0.08, agreement_adjustment=0.08, confidence_adjustment=0.05, max_trades_multiplier=0.50, cooldown_seconds=600))
    elif market_status.algorithm_risk == WcaAlgorithmRiskStatus.DAILY_STOP.value:
        overlays.append(_Overlay("algorithm_risk.daily_stop", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, threshold_adjustment=0.30, agreement_adjustment=0.10, confidence_adjustment=0.10, max_trades_multiplier=0, cooldown_seconds=900, block_entries=True))
    if current_drawdown_percent >= config.drawdown_stop_threshold_percent:
        overlays.append(_Overlay("drawdown.daily_stop", risk_multiplier=0, quantity_multiplier=0, allocation_multiplier=0, max_trades_multiplier=0, block_entries=True))
    elif current_drawdown_percent >= config.drawdown_defensive_threshold_percent:
        overlays.append(_Overlay("drawdown.defensive", risk_multiplier=0.35, quantity_multiplier=0.50, allocation_multiplier=0.50, threshold_adjustment=0.08, max_trades_multiplier=0.50, cooldown_seconds=600))
    elif current_drawdown_percent >= config.drawdown_reduced_threshold_percent:
        overlays.append(_Overlay("drawdown.reduced", risk_multiplier=0.70, quantity_multiplier=0.75, threshold_adjustment=0.03))
    return tuple(overlays)


def _profile_from_effective(
    effective: WcaEffectiveSettings,
    market_status: WcaMarketStatus,
    calculated_at: datetime,
    ttl: timedelta,
    reason_codes: tuple[str, ...],
) -> WcaDynamicProfile:
    expiration = calculated_at + ttl
    effective = effective.model_copy(update={"expiration_timestamp": expiration})
    return WcaDynamicProfile(
        profile_id=effective.profile_id,
        profile_version=effective.profile_version,
        baseline_settings_version=effective.baseline_settings_version,
        market_status=market_status,
        active_overlays=effective.active_overlays,
        effective_settings=effective,
        calculation_timestamp=calculated_at,
        expiration_timestamp=expiration,
        reason_codes=reason_codes,
    )


def _profile_id(overlays: tuple[_Overlay, ...]) -> str:
    names = tuple(overlay.name for overlay in overlays if overlay.name != "baseline")
    return "baseline" if not names else "dynamic-" + "-".join(name.replace(".", "_") for name in names)


def _defensiveness_score(settings: WcaEffectiveSettings) -> float:
    return (
        (1 - settings.risk_multiplier)
        + settings.threshold_adjustment
        + (1 if settings.entries_blocked else 0)
        + max(0, settings.final_cooldown_seconds / 900)
    )


def _scaled_share_cap(max_allowed_shares: int, hard_cap: int, multiplier: float) -> int:
    cap = _min_nonzero_cap(max_allowed_shares, hard_cap)
    return int(cap * multiplier) if cap > 0 else 0


def _min_nonzero_cap(left: int, right: int) -> int:
    caps = tuple(value for value in (left, right) if value > 0)
    return min(caps) if caps else 0


__all__ = (
    "WcaDynamicProfile",
    "WcaDynamicProfileConfig",
    "WcaEffectiveSettings",
    "protective_stop_distance_for_existing_position",
    "resolve_dynamic_profile",
)
