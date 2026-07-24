"""Read-only Session dynamic routing profile contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import time
import hashlib
import json
from typing import Any

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


SESSION_PROFILE_SCHEMA_VERSION = "session_profile_schema_v1"
SESSION_PROFILE_BASELINE = "session.profile.baseline"


@dataclass(frozen=True)
class SessionProfile:
    profile_id: str
    profile_version: str
    source_phase: str
    source_behavior: str
    allowed_strategy_families: tuple[str, ...]
    blocked_strategy_families: tuple[str, ...]
    base_risk_multiplier: float
    maximum_position_percentage: float
    minimum_setup_score: float
    minimum_net_expected_edge: float
    maximum_spread_basis_points: float
    maximum_quote_age_seconds: float
    maximum_participation_rate: float
    allowed_order_types: tuple[str, ...]
    entry_timeout_seconds: int
    signal_validity_period_seconds: int
    stop_distance_multiplier: float
    target_distance_multiplier: float
    maximum_holding_period_seconds: int
    new_entry_cutoff: str | None
    flatten_by_time: str | None
    pyramiding_allowed: bool
    maximum_concurrent_session_originated_positions: int
    block_new_entries: bool
    reason_codes: tuple[str, ...]
    schema_version: str = SESSION_PROFILE_SCHEMA_VERSION

    def bounded(self, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> SessionProfile:
        risk = _clamp(self.base_risk_multiplier, 0.0, config.session_profile_global_max_risk_multiplier)
        position = _clamp(self.maximum_position_percentage, 0.0, config.session_profile_global_max_position_percentage)
        spread = _clamp(self.maximum_spread_basis_points, 0.0, config.maximum_healthy_spread_bps)
        quote_age = _clamp(self.maximum_quote_age_seconds, 0.0, config.maximum_quote_age_seconds)
        participation = _clamp(self.maximum_participation_rate, 0.0, config.maximum_intended_participation_ratio)
        return replace(
            self,
            base_risk_multiplier=risk,
            maximum_position_percentage=position,
            minimum_setup_score=_clamp(self.minimum_setup_score, 0.0, 1.0),
            maximum_spread_basis_points=spread,
            maximum_quote_age_seconds=quote_age,
            maximum_participation_rate=participation,
            entry_timeout_seconds=max(0, int(self.entry_timeout_seconds)),
            signal_validity_period_seconds=max(0, int(self.signal_validity_period_seconds)),
            stop_distance_multiplier=max(0.0, float(self.stop_distance_multiplier)),
            target_distance_multiplier=max(0.0, float(self.target_distance_multiplier)),
            maximum_holding_period_seconds=max(0, int(self.maximum_holding_period_seconds)),
            maximum_concurrent_session_originated_positions=max(0, min(int(self.maximum_concurrent_session_originated_positions), config.session_profile_max_concurrent_positions)),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def deterministic_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), default=str)

    def deterministic_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


def baseline_session_profile(*, config: SessionConfig = DEFAULT_SESSION_CONFIG, source_phase: str = "unknown", source_behavior: str = "unknown") -> SessionProfile:
    return SessionProfile(
        profile_id=SESSION_PROFILE_BASELINE,
        profile_version=config.session_profile_version,
        source_phase=source_phase,
        source_behavior=source_behavior,
        allowed_strategy_families=("trend", "pullback", "breakout", "mean_reversion", "reversal", "vwap"),
        blocked_strategy_families=(),
        base_risk_multiplier=1.0,
        maximum_position_percentage=config.session_profile_global_max_position_percentage,
        minimum_setup_score=config.session_profile_baseline_minimum_setup_score,
        minimum_net_expected_edge=config.session_profile_baseline_minimum_net_expected_edge,
        maximum_spread_basis_points=config.maximum_healthy_spread_bps,
        maximum_quote_age_seconds=config.maximum_quote_age_seconds,
        maximum_participation_rate=config.maximum_intended_participation_ratio,
        allowed_order_types=("limit", "stop_limit"),
        entry_timeout_seconds=config.session_profile_baseline_entry_timeout_seconds,
        signal_validity_period_seconds=config.session_profile_baseline_signal_validity_seconds,
        stop_distance_multiplier=config.session_profile_baseline_stop_multiplier,
        target_distance_multiplier=config.session_profile_baseline_target_multiplier,
        maximum_holding_period_seconds=config.session_profile_baseline_max_holding_seconds,
        new_entry_cutoff=_time_value(config.market_close),
        flatten_by_time=None,
        pyramiding_allowed=False,
        maximum_concurrent_session_originated_positions=config.session_profile_max_concurrent_positions,
        block_new_entries=False,
        reason_codes=(SESSION_PROFILE_BASELINE,),
    ).bounded(config=config)


def blocked_session_profile(
    *,
    profile_id: str,
    reason_code: str,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
    source_phase: str = "unknown",
    source_behavior: str = "unknown",
    flatten_by_time: str | None = None,
) -> SessionProfile:
    base = baseline_session_profile(config=config, source_phase=source_phase, source_behavior=source_behavior)
    return replace(
        base,
        profile_id=profile_id,
        allowed_strategy_families=(),
        blocked_strategy_families=("trend", "breakout", "mean_reversion", "reversal", "vwap"),
        base_risk_multiplier=0.0,
        maximum_position_percentage=0.0,
        allowed_order_types=(),
        entry_timeout_seconds=0,
        signal_validity_period_seconds=0,
        maximum_holding_period_seconds=0,
        flatten_by_time=flatten_by_time,
        pyramiding_allowed=False,
        maximum_concurrent_session_originated_positions=0,
        block_new_entries=True,
        reason_codes=tuple(dict.fromkeys((*base.reason_codes, reason_code, "session.profile.zero_new_entry_risk"))),
    ).bounded(config=config)


def modify_session_profile(
    profile: SessionProfile,
    *,
    profile_id: str,
    reason_codes: tuple[str, ...],
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
    allowed_strategy_families: tuple[str, ...] | None = None,
    blocked_strategy_families: tuple[str, ...] | None = None,
    base_risk_multiplier: float | None = None,
    maximum_position_percentage: float | None = None,
    minimum_setup_score: float | None = None,
    minimum_net_expected_edge: float | None = None,
    maximum_spread_basis_points: float | None = None,
    maximum_quote_age_seconds: float | None = None,
    maximum_participation_rate: float | None = None,
    allowed_order_types: tuple[str, ...] | None = None,
    entry_timeout_seconds: int | None = None,
    signal_validity_period_seconds: int | None = None,
    stop_distance_multiplier: float | None = None,
    target_distance_multiplier: float | None = None,
    maximum_holding_period_seconds: int | None = None,
    new_entry_cutoff: str | None = None,
    flatten_by_time: str | None = None,
    pyramiding_allowed: bool | None = None,
    maximum_concurrent_session_originated_positions: int | None = None,
    block_new_entries: bool | None = None,
) -> SessionProfile:
    return replace(
        profile,
        profile_id=profile_id,
        allowed_strategy_families=allowed_strategy_families if allowed_strategy_families is not None else profile.allowed_strategy_families,
        blocked_strategy_families=blocked_strategy_families if blocked_strategy_families is not None else profile.blocked_strategy_families,
        base_risk_multiplier=base_risk_multiplier if base_risk_multiplier is not None else profile.base_risk_multiplier,
        maximum_position_percentage=maximum_position_percentage if maximum_position_percentage is not None else profile.maximum_position_percentage,
        minimum_setup_score=minimum_setup_score if minimum_setup_score is not None else profile.minimum_setup_score,
        minimum_net_expected_edge=minimum_net_expected_edge if minimum_net_expected_edge is not None else profile.minimum_net_expected_edge,
        maximum_spread_basis_points=maximum_spread_basis_points if maximum_spread_basis_points is not None else profile.maximum_spread_basis_points,
        maximum_quote_age_seconds=maximum_quote_age_seconds if maximum_quote_age_seconds is not None else profile.maximum_quote_age_seconds,
        maximum_participation_rate=maximum_participation_rate if maximum_participation_rate is not None else profile.maximum_participation_rate,
        allowed_order_types=allowed_order_types if allowed_order_types is not None else profile.allowed_order_types,
        entry_timeout_seconds=entry_timeout_seconds if entry_timeout_seconds is not None else profile.entry_timeout_seconds,
        signal_validity_period_seconds=signal_validity_period_seconds if signal_validity_period_seconds is not None else profile.signal_validity_period_seconds,
        stop_distance_multiplier=stop_distance_multiplier if stop_distance_multiplier is not None else profile.stop_distance_multiplier,
        target_distance_multiplier=target_distance_multiplier if target_distance_multiplier is not None else profile.target_distance_multiplier,
        maximum_holding_period_seconds=maximum_holding_period_seconds if maximum_holding_period_seconds is not None else profile.maximum_holding_period_seconds,
        new_entry_cutoff=new_entry_cutoff if new_entry_cutoff is not None else profile.new_entry_cutoff,
        flatten_by_time=flatten_by_time if flatten_by_time is not None else profile.flatten_by_time,
        pyramiding_allowed=pyramiding_allowed if pyramiding_allowed is not None else profile.pyramiding_allowed,
        maximum_concurrent_session_originated_positions=maximum_concurrent_session_originated_positions
        if maximum_concurrent_session_originated_positions is not None
        else profile.maximum_concurrent_session_originated_positions,
        block_new_entries=block_new_entries if block_new_entries is not None else profile.block_new_entries,
        reason_codes=tuple(dict.fromkeys((*profile.reason_codes, *reason_codes))),
    ).bounded(config=config)


def _time_value(value: time | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(float(value), maximum))
