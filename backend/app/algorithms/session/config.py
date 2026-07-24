"""Versioned thresholds and windows for the Session subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
import hashlib
import json
from typing import Any


SESSION_CONFIG_VERSION = "session_config_v1"


@dataclass(frozen=True)
class SessionConfig:
    config_version: str = SESSION_CONFIG_VERSION
    exchange_timezone: str = "America/New_York"
    minimum_behavior_bars: int = 10
    vwap_slope_minimum_bars: int = 20
    vwap_slope_windows: tuple[int, ...] = (5, 20)
    vwap_crossing_lookback_bars: int = 30
    vwap_acceptance_bars: int = 2
    vwap_deadband_bps: float = 1.0
    vwap_average_excursion_bars: int = 30
    baseline_version: str = "session_baseline_v1"
    minimum_baseline_samples: int = 3
    realized_volatility_window_bars: int = 5
    rolling_relative_volume_window_bars: int = 5
    opening_discovery_minutes: int = 5
    opening_range_minutes: int = 30
    opening_auction_minutes: int = 1
    opening_range_acceptance_bars: int = 2
    opening_range_missing_bars_allowed: int = 0
    opening_drive_minimum_move_bps: float = 8.0
    market_open: time = time(9, 30)
    market_close: time = time(16, 0)
    premarket_start: time = time(4, 0)
    postmarket_end: time = time(20, 0)
    morning_end: time = time(11, 30)
    midday_end: time = time(14, 0)
    power_hour_start: time = time(15, 0)
    closing_auction_minutes: int = 15
    new_entry_cutoff_minutes_before_close: int = 15
    trend_efficiency_threshold: float = 0.62
    mean_reversion_efficiency_threshold: float = 0.42
    choppy_efficiency_threshold: float = 0.38
    choppy_vwap_crosses: int = 5
    mean_reversion_vwap_crosses: int = 2
    expansion_range_ratio: float = 1.35
    compression_range_ratio: float = 0.70
    elevated_volume_pace_ratio: float = 1.70
    maximum_healthy_spread_bps: float = 15.0
    maximum_constrained_spread_bps: float = 30.0
    maximum_fresh_quote_age_ms: int = 1_000
    maximum_stale_quote_age_ms: int = 5_000
    maximum_quote_age_seconds: float = 1.0
    maximum_stale_quote_age_seconds: float = 5.0
    minimum_top_of_book_size_shares: float = 100.0
    maximum_intended_participation_ratio: float = 0.10
    constrained_intended_participation_ratio: float = 0.05
    maximum_recent_slippage_error_bps: float = 5.0
    stressed_recent_slippage_error_bps: float = 12.0
    minimum_trade_rate_per_second: float = 0.10
    structure_swing_lookback_bars: int = 2
    structure_acceptance_bars: int = 2
    structure_failed_acceptance_threshold: int = 2
    structure_valid_breakout_volume_ratio: float = 1.2
    shallow_pullback_max_fraction: float = 0.38
    deep_pullback_max_fraction: float = 0.70
    pullback_volume_contraction_ratio: float = 0.80
    choppy_overlap_ratio_threshold: float = 0.80
    choppy_path_ratio_threshold: float = 4.0
    trend_path_efficiency_threshold: float = 0.45
    structure_trend_minimum_move_bps: float = 100.0
    breakout_max_bars_after_acceptance: int = 6
    transition_confirmation_bars: int = 3
    transition_min_candidate_confidence: float = 0.62
    transition_min_confidence_improvement: float = 0.05
    transition_min_dwell_seconds: int = 180
    transition_recovery_confirmation_bars: int = 3
    transition_recovery_min_confidence: float = 0.70
    transition_oscillation_confirmation_bars: int = 4
    transition_history_limit: int = 100
    session_profile_version: str = "session_profile_v1"
    session_profile_global_max_risk_multiplier: float = 1.0
    session_profile_global_max_position_percentage: float = 0.10
    session_profile_baseline_minimum_setup_score: float = 0.60
    session_profile_baseline_minimum_net_expected_edge: float = 0.01
    session_profile_baseline_entry_timeout_seconds: int = 45
    session_profile_baseline_signal_validity_seconds: int = 60
    session_profile_baseline_stop_multiplier: float = 1.0
    session_profile_baseline_target_multiplier: float = 1.0
    session_profile_baseline_max_holding_seconds: int = 900
    session_profile_max_concurrent_positions: int = 1
    session_execution_minimum_fill_probability: float = 0.55
    session_execution_latency_budget_ms: int = 750
    session_execution_max_opportunity_decay: float = 0.02
    session_execution_default_capital_partition_id: str = "session.paper.default"
    decision_valid_for_seconds: int = 60

    @property
    def configuration_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("market_open", "market_close", "premarket_start", "postmarket_end", "morning_end", "midday_end", "power_hour_start"):
            payload[key] = payload[key].isoformat()
        return payload


DEFAULT_SESSION_CONFIG = SessionConfig()
