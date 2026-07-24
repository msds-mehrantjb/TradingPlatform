"""Session-owned read-only strategy routing and dynamic profiles."""

from __future__ import annotations

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import DataQualityState, EventRiskState, LiquidityState, SessionBehavior, SessionClassification, SessionPhase, VolatilityState
from backend.app.algorithms.session.profile import SessionProfile, baseline_session_profile, blocked_session_profile, modify_session_profile


def resolve_session_profile(classification: SessionClassification, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> SessionProfile:
    source_phase = classification.phase.value
    source_behavior = classification.behavior.value

    if classification.event_risk_state == EventRiskState.BLACKOUT or classification.behavior == SessionBehavior.EVENT_DRIVEN:
        return blocked_session_profile(
            profile_id="session.profile.event_blackout",
            reason_code="session.profile.event_blackout",
            config=config,
            source_phase=source_phase,
            source_behavior=source_behavior,
        )
    if classification.liquidity_state in {LiquidityState.STRESSED, LiquidityState.STALE} or classification.behavior == SessionBehavior.LIQUIDITY_STRESS:
        return blocked_session_profile(
            profile_id="session.profile.liquidity_stress",
            reason_code="session.profile.liquidity_stress",
            config=config,
            source_phase=source_phase,
            source_behavior=source_behavior,
        )
    if classification.data_quality_state in {DataQualityState.WARMING_UP, DataQualityState.INCOMPLETE, DataQualityState.STALE, DataQualityState.INVALID} or classification.behavior in {
        SessionBehavior.BUILDING,
        SessionBehavior.UNKNOWN,
    } or classification.phase in {SessionPhase.UNKNOWN, SessionPhase.PREMARKET}:
        return blocked_session_profile(
            profile_id="session.profile.unknown_data_unready",
            reason_code="session.profile.data_unready",
            config=config,
            source_phase=source_phase,
            source_behavior=source_behavior,
        )
    if classification.phase in {SessionPhase.CLOSING_AUCTION, SessionPhase.CLOSED, SessionPhase.POSTMARKET}:
        return blocked_session_profile(
            profile_id="session.profile.closing_cutoff",
            reason_code="session.profile.closing_cutoff",
            config=config,
            source_phase=source_phase,
            source_behavior=source_behavior,
            flatten_by_time=config.market_close.isoformat(timespec="minutes"),
        )

    base = baseline_session_profile(config=config, source_phase=source_phase, source_behavior=source_behavior)

    if classification.phase in {SessionPhase.OPENING_AUCTION, SessionPhase.OPENING_DISCOVERY}:
        if classification.behavior == SessionBehavior.OPENING_DRIVE:
            return _confirmed_opening_drive(base, config)
        return _opening_discovery(base, config)
    if classification.behavior in {SessionBehavior.BREAKOUT_UP, SessionBehavior.BREAKOUT_DOWN} and classification.phase in {SessionPhase.OPENING_RANGE, SessionPhase.MORNING}:
        return _opening_range_breakout(base, config)
    if classification.phase == SessionPhase.MORNING and classification.behavior in {SessionBehavior.TREND_UP, SessionBehavior.TREND_DOWN}:
        return _morning_trend(base, config)
    if classification.phase == SessionPhase.MIDDAY and classification.behavior == SessionBehavior.COMPRESSION:
        return _midday_compressed(base, config)
    if classification.phase == SessionPhase.MIDDAY and classification.behavior in {SessionBehavior.MEAN_REVERTING, SessionBehavior.BALANCED_RANGE}:
        return _midday_mean_reverting(base, config)
    if classification.phase in {SessionPhase.AFTERNOON, SessionPhase.POWER_HOUR} and (
        classification.behavior == SessionBehavior.EXPANSION or classification.volatility_state == VolatilityState.EXPANDING
    ):
        return _afternoon_expansion(base, config)
    if classification.behavior == SessionBehavior.CHOPPY:
        return _choppy(base, config)
    if classification.phase == SessionPhase.POWER_HOUR:
        return _power_hour(base, config)
    if classification.behavior in {SessionBehavior.TREND_UP, SessionBehavior.TREND_DOWN}:
        return _morning_trend(base, config, profile_id="session.profile.trend")
    if classification.behavior in {SessionBehavior.MEAN_REVERTING, SessionBehavior.BALANCED_RANGE}:
        return _midday_mean_reverting(base, config, profile_id="session.profile.range")
    if classification.behavior in {SessionBehavior.FAILED_BREAKOUT_UP, SessionBehavior.FAILED_BREAKOUT_DOWN, SessionBehavior.REVERSAL_UP, SessionBehavior.REVERSAL_DOWN}:
        return _reversal(base, config)
    return base


def session_route_permissions(classification: SessionClassification, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> dict[str, object]:
    profile = resolve_session_profile(classification, config=config)
    return {
        "algorithmId": "session",
        "profile": profile.as_dict(),
        "profileHash": profile.deterministic_hash(),
        "canRouteNewEntries": not profile.block_new_entries and profile.base_risk_multiplier > 0,
        "readOnly": True,
        "cannotBypassGlobalGates": True,
    }


def _opening_discovery(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.opening_discovery",
        reason_codes=("session.profile.opening_discovery", "session.profile.opening_short_validity"),
        config=config,
        allowed_strategy_families=("trend", "vwap"),
        blocked_strategy_families=("opening_range_breakout", "late_breakout_chasing"),
        base_risk_multiplier=0.35,
        maximum_position_percentage=0.03,
        minimum_setup_score=0.72,
        minimum_net_expected_edge=0.02,
        maximum_spread_basis_points=8.0,
        maximum_quote_age_seconds=0.50,
        maximum_participation_rate=0.03,
        allowed_order_types=("limit",),
        entry_timeout_seconds=10,
        signal_validity_period_seconds=15,
        stop_distance_multiplier=0.80,
        target_distance_multiplier=0.80,
        maximum_holding_period_seconds=300,
    )


def _confirmed_opening_drive(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.confirmed_opening_drive",
        reason_codes=("session.profile.confirmed_opening_drive",),
        config=config,
        allowed_strategy_families=("trend", "pullback", "vwap"),
        blocked_strategy_families=("mean_reversion",),
        base_risk_multiplier=0.45,
        maximum_position_percentage=0.04,
        minimum_setup_score=0.74,
        minimum_net_expected_edge=0.025,
        maximum_spread_basis_points=7.5,
        maximum_quote_age_seconds=0.50,
        maximum_participation_rate=0.035,
        allowed_order_types=("limit", "stop_limit"),
        entry_timeout_seconds=12,
        signal_validity_period_seconds=20,
        stop_distance_multiplier=1.00,
        target_distance_multiplier=1.10,
        maximum_holding_period_seconds=420,
    )


def _opening_range_breakout(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.opening_range_breakout",
        reason_codes=("session.profile.opening_range_breakout", "session.profile.strict_spread_and_slippage"),
        config=config,
        allowed_strategy_families=("breakout", "trend", "pullback", "vwap"),
        blocked_strategy_families=("mean_reversion",),
        base_risk_multiplier=0.55,
        maximum_position_percentage=0.06,
        minimum_setup_score=0.76,
        minimum_net_expected_edge=0.03,
        maximum_spread_basis_points=7.0,
        maximum_quote_age_seconds=0.50,
        maximum_participation_rate=0.04,
        allowed_order_types=("limit", "stop_limit"),
        entry_timeout_seconds=15,
        signal_validity_period_seconds=30,
        stop_distance_multiplier=1.20,
        target_distance_multiplier=1.50,
        maximum_holding_period_seconds=600,
    )


def _morning_trend(base: SessionProfile, config: SessionConfig, *, profile_id: str = "session.profile.morning_trend") -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id=profile_id,
        reason_codes=(profile_id, "session.profile.favor_trend_pullback"),
        config=config,
        allowed_strategy_families=("trend", "pullback", "vwap"),
        blocked_strategy_families=("range_fade",),
        base_risk_multiplier=0.75,
        maximum_position_percentage=0.08,
        minimum_setup_score=0.68,
        minimum_net_expected_edge=0.02,
        maximum_spread_basis_points=9.0,
        maximum_quote_age_seconds=0.75,
        maximum_participation_rate=0.05,
        allowed_order_types=("limit", "stop_limit"),
        entry_timeout_seconds=30,
        signal_validity_period_seconds=75,
        stop_distance_multiplier=1.15,
        target_distance_multiplier=1.35,
        maximum_holding_period_seconds=1_500,
        pyramiding_allowed=False,
    )


def _midday_compressed(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.midday_compressed",
        reason_codes=("session.profile.midday_compressed", "session.profile.breakout_disabled_in_compression"),
        config=config,
        allowed_strategy_families=("mean_reversion", "vwap"),
        blocked_strategy_families=("breakout", "trend", "pullback"),
        base_risk_multiplier=0.35,
        maximum_position_percentage=0.03,
        minimum_setup_score=0.74,
        minimum_net_expected_edge=0.015,
        maximum_spread_basis_points=6.0,
        maximum_quote_age_seconds=0.75,
        maximum_participation_rate=0.025,
        allowed_order_types=("limit",),
        entry_timeout_seconds=20,
        signal_validity_period_seconds=45,
        stop_distance_multiplier=0.75,
        target_distance_multiplier=0.65,
        maximum_holding_period_seconds=420,
    )


def _midday_mean_reverting(base: SessionProfile, config: SessionConfig, *, profile_id: str = "session.profile.midday_mean_reverting") -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id=profile_id,
        reason_codes=(profile_id, "session.profile.mean_reversion_only"),
        config=config,
        allowed_strategy_families=("mean_reversion", "reversal", "vwap"),
        blocked_strategy_families=("breakout", "trend"),
        base_risk_multiplier=0.45,
        maximum_position_percentage=0.04,
        minimum_setup_score=0.70,
        minimum_net_expected_edge=0.015,
        maximum_spread_basis_points=7.0,
        maximum_quote_age_seconds=0.75,
        maximum_participation_rate=0.035,
        allowed_order_types=("limit",),
        entry_timeout_seconds=25,
        signal_validity_period_seconds=60,
        stop_distance_multiplier=0.85,
        target_distance_multiplier=0.85,
        maximum_holding_period_seconds=600,
    )


def _afternoon_expansion(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.afternoon_expansion",
        reason_codes=("session.profile.afternoon_expansion", "session.profile.higher_minimum_edge"),
        config=config,
        allowed_strategy_families=("breakout", "trend", "pullback"),
        blocked_strategy_families=("mean_reversion",),
        base_risk_multiplier=0.50,
        maximum_position_percentage=0.05,
        minimum_setup_score=0.75,
        minimum_net_expected_edge=0.03,
        maximum_spread_basis_points=7.0,
        maximum_quote_age_seconds=0.60,
        maximum_participation_rate=0.04,
        allowed_order_types=("limit", "stop_limit"),
        entry_timeout_seconds=20,
        signal_validity_period_seconds=45,
        stop_distance_multiplier=1.35,
        target_distance_multiplier=1.40,
        maximum_holding_period_seconds=900,
    )


def _power_hour(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.power_hour",
        reason_codes=("session.profile.power_hour", "session.profile.shorter_holding_period"),
        config=config,
        allowed_strategy_families=("trend", "reversal", "vwap"),
        blocked_strategy_families=("slow_breakout", "long_hold"),
        base_risk_multiplier=0.35,
        maximum_position_percentage=0.03,
        minimum_setup_score=0.78,
        minimum_net_expected_edge=0.03,
        maximum_spread_basis_points=6.0,
        maximum_quote_age_seconds=0.50,
        maximum_participation_rate=0.03,
        allowed_order_types=("limit",),
        entry_timeout_seconds=12,
        signal_validity_period_seconds=20,
        stop_distance_multiplier=0.95,
        target_distance_multiplier=1.00,
        maximum_holding_period_seconds=300,
        new_entry_cutoff=config.market_close.isoformat(timespec="minutes"),
        flatten_by_time=config.market_close.isoformat(timespec="minutes"),
    )


def _choppy(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.choppy",
        reason_codes=("session.profile.choppy", "session.profile.block_breakout_chasing"),
        config=config,
        allowed_strategy_families=("mean_reversion", "reversal", "vwap"),
        blocked_strategy_families=("breakout", "trend", "pullback"),
        base_risk_multiplier=0.25,
        maximum_position_percentage=0.025,
        minimum_setup_score=0.78,
        minimum_net_expected_edge=0.025,
        maximum_spread_basis_points=6.0,
        maximum_quote_age_seconds=0.60,
        maximum_participation_rate=0.025,
        allowed_order_types=("limit",),
        entry_timeout_seconds=15,
        signal_validity_period_seconds=30,
        stop_distance_multiplier=0.75,
        target_distance_multiplier=0.70,
        maximum_holding_period_seconds=360,
    )


def _reversal(base: SessionProfile, config: SessionConfig) -> SessionProfile:
    return modify_session_profile(
        base,
        profile_id="session.profile.reversal",
        reason_codes=("session.profile.reversal",),
        config=config,
        allowed_strategy_families=("reversal", "mean_reversion", "vwap"),
        blocked_strategy_families=("breakout_chasing",),
        base_risk_multiplier=0.45,
        maximum_position_percentage=0.04,
        minimum_setup_score=0.72,
        minimum_net_expected_edge=0.02,
        maximum_spread_basis_points=7.0,
        maximum_quote_age_seconds=0.60,
        maximum_participation_rate=0.035,
        allowed_order_types=("limit",),
        entry_timeout_seconds=20,
        signal_validity_period_seconds=45,
        stop_distance_multiplier=0.90,
        target_distance_multiplier=1.00,
        maximum_holding_period_seconds=600,
    )
