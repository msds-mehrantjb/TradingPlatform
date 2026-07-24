"""Authoritative Session axis classifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.session.calendar import SessionClock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import DataQualityState, DirectionBias, EventRiskState, LiquidityState, SessionBehavior, SessionPhase, VolatilityState


SESSION_DATA_WARMING_UP = "SESSION_DATA_WARMING_UP"
SESSION_QUOTE_STALE = "SESSION_QUOTE_STALE"
SESSION_OR5_BREAK_ACCEPTED = "SESSION_OR5_BREAK_ACCEPTED"
SESSION_VWAP_ROTATION_HIGH = "SESSION_VWAP_ROTATION_HIGH"
SESSION_DIRECTIONAL_EFFICIENCY_HIGH = "SESSION_DIRECTIONAL_EFFICIENCY_HIGH"
SESSION_RANGE_PERCENTILE_EXPANDING = "SESSION_RANGE_PERCENTILE_EXPANDING"
SESSION_FAILED_BREAKOUT = "SESSION_FAILED_BREAKOUT"
SESSION_EVENT_BLACKOUT = "SESSION_EVENT_BLACKOUT"
SESSION_CLASSIFICATION_CONFLICT = "SESSION_CLASSIFICATION_CONFLICT"
SESSION_OR5_NOT_COMPLETE = "SESSION_OR5_NOT_COMPLETE"
SESSION_OPENING_DRIVE = "SESSION_OPENING_DRIVE"
SESSION_MIDDAY_COMPRESSION = "SESSION_MIDDAY_COMPRESSION"
SESSION_LIQUIDITY_BLOCK = "SESSION_LIQUIDITY_BLOCK"
SESSION_DATA_READY = "SESSION_DATA_READY"
SESSION_VOLATILITY_UNKNOWN = "SESSION_VOLATILITY_UNKNOWN"


SESSION_ALLOWED_FAMILIES = {
    SessionBehavior.BUILDING: (),
    SessionBehavior.OPENING_DRIVE: ("trend", "breakout", "vwap"),
    SessionBehavior.TREND_UP: ("trend", "breakout", "vwap"),
    SessionBehavior.TREND_DOWN: ("trend", "breakout", "vwap"),
    SessionBehavior.BALANCED_RANGE: ("trend", "mean_reversion", "reversal", "vwap"),
    SessionBehavior.MEAN_REVERTING: ("mean_reversion", "reversal", "vwap"),
    SessionBehavior.CHOPPY: ("safety",),
    SessionBehavior.BREAKOUT_UP: ("breakout", "trend", "vwap"),
    SessionBehavior.BREAKOUT_DOWN: ("breakout", "trend", "vwap"),
    SessionBehavior.FAILED_BREAKOUT_UP: ("reversal", "mean_reversion"),
    SessionBehavior.FAILED_BREAKOUT_DOWN: ("reversal", "mean_reversion"),
    SessionBehavior.REVERSAL_UP: ("reversal", "mean_reversion"),
    SessionBehavior.REVERSAL_DOWN: ("reversal", "mean_reversion"),
    SessionBehavior.EXPANSION: ("breakout", "trend"),
    SessionBehavior.COMPRESSION: ("mean_reversion",),
    SessionBehavior.EVENT_DRIVEN: ("event", "safety"),
    SessionBehavior.LIQUIDITY_STRESS: ("safety",),
    SessionBehavior.UNKNOWN: (),
}
SESSION_BLOCKED_FAMILIES = {
    SessionBehavior.BUILDING: ("trend", "breakout", "mean_reversion", "reversal", "vwap"),
    SessionBehavior.CHOPPY: ("breakout", "trend"),
    SessionBehavior.COMPRESSION: ("breakout",),
    SessionBehavior.LIQUIDITY_STRESS: ("trend", "breakout", "mean_reversion", "reversal", "vwap"),
    SessionBehavior.EVENT_DRIVEN: ("trend", "breakout", "mean_reversion", "reversal", "vwap"),
    SessionBehavior.UNKNOWN: ("trend", "breakout", "mean_reversion", "reversal", "vwap"),
}
ORDER_AFFECTING_FAMILIES = ("trend", "breakout", "mean_reversion", "reversal", "vwap")


@dataclass(frozen=True)
class SessionAxisClassification:
    phase: SessionPhase
    behavior: SessionBehavior
    direction_bias: DirectionBias
    volatility_state: VolatilityState
    liquidity_state: LiquidityState
    data_quality_state: DataQualityState
    event_risk_state: EventRiskState
    phase_confidence: float
    behavior_confidence: float
    volatility_confidence: float
    liquidity_confidence: float
    data_quality_confidence: float
    overall_confidence: float
    safety_block_confidence: float
    block_new_entries: bool
    reason_codes: tuple[str, ...]
    allowed_strategy_families: tuple[str, ...]
    blocked_strategy_families: tuple[str, ...]
    legacy_tags: tuple[str, ...]
    evidence: dict[str, Any]


def classify_session_axes(
    *,
    clock: SessionClock | None,
    data_quality_result: Any | None,
    opening_range_features: dict[str, Any] | None,
    vwap_features: dict[str, Any] | None,
    volatility_features: dict[str, Any] | None,
    volume_features: dict[str, Any] | None,
    liquidity_features: dict[str, Any] | None,
    structure_features: dict[str, Any] | None,
    event_risk_context: Any | None = None,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> SessionAxisClassification:
    phase = clock.current_phase if clock else SessionPhase.UNKNOWN
    phase_confidence = _phase_confidence(phase)
    data_quality_state, data_quality_confidence, data_reasons, data_blocks = _data_quality_axis(data_quality_result)
    liquidity_state, liquidity_confidence, liquidity_reasons, liquidity_blocks, liquidity_block_confidence = _liquidity_axis(liquidity_features)
    volatility_state, volatility_confidence, volatility_reasons, volatility_conflict = _volatility_axis(volatility_features)
    event_state, event_reasons, event_blocks, event_block_confidence = _event_axis(event_risk_context)

    behavior, direction_bias, behavior_confidence, behavior_reasons, legacy_tags = _behavior_axis(
        phase=phase,
        clock=clock,
        data_quality_state=data_quality_state,
        liquidity_state=liquidity_state,
        event_state=event_state,
        opening=opening_range_features or {},
        vwap=vwap_features or {},
        volatility_state=volatility_state,
        structure=structure_features or {},
        config=config,
    )
    conflicts = _classification_conflicts(structure_features or {}, vwap_features or {}, volatility_conflict)
    if conflicts:
        behavior_confidence = max(0.0, behavior_confidence - (0.12 * len(conflicts)))

    block_new_entries = bool(data_blocks or liquidity_blocks or event_blocks or data_quality_state in {DataQualityState.INCOMPLETE, DataQualityState.STALE, DataQualityState.INVALID})
    safety_block_confidence = max(liquidity_block_confidence, event_block_confidence, 0.85 if data_blocks else 0.0)
    required_confidences = (phase_confidence, behavior_confidence, volatility_confidence, liquidity_confidence, data_quality_confidence)
    overall_confidence = max(0.0, min(required_confidences) - (0.05 * len(conflicts)))
    allowed = SESSION_ALLOWED_FAMILIES.get(behavior, ())
    blocked = SESSION_BLOCKED_FAMILIES.get(behavior, ())
    if block_new_entries:
        blocked = tuple(dict.fromkeys((*blocked, *ORDER_AFFECTING_FAMILIES)))

    reasons = tuple(
        dict.fromkeys(
            (
                f"SESSION_PHASE_{phase.value.upper()}",
                *data_reasons,
                *liquidity_reasons,
                *volatility_reasons,
                *event_reasons,
                *behavior_reasons,
                *conflicts,
            )
        )
    )
    return SessionAxisClassification(
        phase=phase,
        behavior=behavior,
        direction_bias=direction_bias,
        volatility_state=volatility_state,
        liquidity_state=liquidity_state,
        data_quality_state=data_quality_state,
        event_risk_state=event_state,
        phase_confidence=phase_confidence,
        behavior_confidence=behavior_confidence,
        volatility_confidence=volatility_confidence,
        liquidity_confidence=liquidity_confidence,
        data_quality_confidence=data_quality_confidence,
        overall_confidence=overall_confidence,
        safety_block_confidence=safety_block_confidence,
        block_new_entries=block_new_entries,
        reason_codes=reasons,
        allowed_strategy_families=allowed,
        blocked_strategy_families=blocked,
        legacy_tags=legacy_tags,
        evidence={
            "conflicts": conflicts,
            "eventRiskState": event_state.value,
            "safetyBlockConfidence": safety_block_confidence,
            "thresholds": {
                "volatilityPercentileExtreme": 0.97,
                "volatilityPercentileExpanding": 0.75,
                "volatilityPercentileCompressedRange": 0.25,
                "volatilityPercentileCompressedRealized": 0.35,
                "vwapCrossesHigh": config.choppy_vwap_crosses,
                "trendPathEfficiencyThreshold": config.trend_path_efficiency_threshold,
                "structureTrendMinimumMoveBps": config.structure_trend_minimum_move_bps,
            },
        },
    )


def _behavior_axis(
    *,
    phase: SessionPhase,
    clock: SessionClock | None,
    data_quality_state: DataQualityState,
    liquidity_state: LiquidityState,
    event_state: EventRiskState,
    opening: dict[str, Any],
    vwap: dict[str, Any],
    volatility_state: VolatilityState,
    structure: dict[str, Any],
    config: SessionConfig,
) -> tuple[SessionBehavior, DirectionBias, float, tuple[str, ...], tuple[str, ...]]:
    if data_quality_state == DataQualityState.WARMING_UP:
        return SessionBehavior.BUILDING, "neutral", 0.25, (SESSION_DATA_WARMING_UP, "session.behavior.building"), ("wait",)
    if data_quality_state in {DataQualityState.INCOMPLETE, DataQualityState.STALE, DataQualityState.INVALID}:
        return SessionBehavior.UNKNOWN, "cash", 0.2, ("SESSION_DATA_NOT_READY",), ("wait",)
    if event_state == EventRiskState.BLACKOUT:
        return SessionBehavior.EVENT_DRIVEN, "cash", 0.52, (SESSION_EVENT_BLACKOUT,), ("event-risk", "safety")
    if liquidity_state in {LiquidityState.STRESSED, LiquidityState.STALE}:
        return SessionBehavior.LIQUIDITY_STRESS, "cash", 0.5, (SESSION_LIQUIDITY_BLOCK, "session.liquidity.blocking_stress"), ("liquidity-stress",)

    or5 = ((opening.get("references") or {}).get("OR5") or {})
    opening_drive = opening.get("openingDrive") or {}
    if phase in {SessionPhase.OPENING_AUCTION, SessionPhase.OPENING_DISCOVERY} and or5.get("status") != "complete":
        direction = opening_drive.get("direction")
        if direction in {"up", "down"}:
            return SessionBehavior.OPENING_DRIVE, "long" if direction == "up" else "short", 0.55, (SESSION_OR5_NOT_COMPLETE, SESSION_OPENING_DRIVE), ("opening-drive",)
        return SessionBehavior.BUILDING, "neutral", 0.35, (SESSION_OR5_NOT_COMPLETE,), ("wait",)

    vwap_current = vwap.get("current") or {}
    if phase == SessionPhase.MIDDAY and volatility_state == VolatilityState.COMPRESSED:
        return SessionBehavior.COMPRESSION, "neutral", 0.68, (SESSION_MIDDAY_COMPRESSION,), ("compression", "mean-reversion")

    behavior_text = structure.get("behavior")
    structure_reasons = tuple(structure.get("reasonCodes") or ())
    if behavior_text == "valid_breakout_up" and _opening_breakout_allowed(phase, clock):
        return SessionBehavior.BREAKOUT_UP, "long", 0.78, (SESSION_OR5_BREAK_ACCEPTED, "session.behavior.breakout_up", *structure_reasons), ("breakout", "long-bias")
    if behavior_text == "valid_breakout_down" and _opening_breakout_allowed(phase, clock):
        return SessionBehavior.BREAKOUT_DOWN, "short", 0.78, (SESSION_OR5_BREAK_ACCEPTED, "session.behavior.breakout_down", *structure_reasons), ("breakout", "short-bias")
    if behavior_text == "failed_breakout_up":
        return SessionBehavior.FAILED_BREAKOUT_UP, "short", 0.74, (SESSION_FAILED_BREAKOUT, "session.behavior.failed_breakout_up", *structure_reasons), ("failed-breakout", "reversal")
    if behavior_text == "failed_breakout_down":
        return SessionBehavior.FAILED_BREAKOUT_DOWN, "long", 0.74, (SESSION_FAILED_BREAKOUT, "session.behavior.failed_breakout_down", *structure_reasons), ("failed-breakout", "reversal")
    if behavior_text == "reversal_up":
        return SessionBehavior.REVERSAL_UP, "long", 0.72, ("SESSION_REVERSAL_UP", *structure_reasons), ("reversal", "long-bias")
    if behavior_text == "reversal_down":
        return SessionBehavior.REVERSAL_DOWN, "short", 0.72, ("SESSION_REVERSAL_DOWN", *structure_reasons), ("reversal", "short-bias")
    if behavior_text == "trend_up":
        return SessionBehavior.TREND_UP, "long", 0.76, (SESSION_DIRECTIONAL_EFFICIENCY_HIGH, "session.behavior.trend_up", *structure_reasons), ("trend-day", "long-bias", "above-vwap")
    if behavior_text == "trend_down":
        return SessionBehavior.TREND_DOWN, "short", 0.76, (SESSION_DIRECTIONAL_EFFICIENCY_HIGH, "session.behavior.trend_down", *structure_reasons), ("trend-day", "short-bias", "below-vwap")
    if behavior_text == "choppy":
        return SessionBehavior.CHOPPY, "neutral", 0.72, (SESSION_VWAP_ROTATION_HIGH, "session.behavior.choppy", *structure_reasons), ("chop", "avoid-breakout")
    if behavior_text == "mean_reverting":
        return SessionBehavior.MEAN_REVERTING, "neutral", 0.70, (SESSION_VWAP_ROTATION_HIGH, "session.behavior.mean_reverting", *structure_reasons), ("mean-reversion", "vwap-reversion")

    crossing_frequency = _number(vwap_current.get("crossingFrequencyPerHour")) or 0.0
    if crossing_frequency >= config.choppy_vwap_crosses:
        return SessionBehavior.CHOPPY, "neutral", 0.62, (SESSION_VWAP_ROTATION_HIGH,), ("chop", "avoid-breakout")
    if volatility_state == VolatilityState.EXPANDING:
        return SessionBehavior.EXPANSION, "neutral", 0.62, (SESSION_RANGE_PERCENTILE_EXPANDING,), ("expansion",)
    return SessionBehavior.BALANCED_RANGE, "neutral", 0.62, ("SESSION_BALANCED_EVIDENCE_MIXED",), ("balanced",)


def _volatility_axis(features: dict[str, Any] | None) -> tuple[VolatilityState, float, tuple[str, ...], bool]:
    features = features or {}
    if features.get("status") != "ready":
        return VolatilityState.UNKNOWN, 0.45, (SESSION_VOLATILITY_UNKNOWN,), False
    range_percentile = _number(features.get("rangePercentile"))
    rv_percentile = _number(features.get("realizedVolatilityPercentile"))
    if range_percentile is None or rv_percentile is None:
        return VolatilityState.UNKNOWN, 0.45, (SESSION_VOLATILITY_UNKNOWN,), False
    disagreement = (range_percentile >= 0.75 and rv_percentile <= 0.35) or (rv_percentile >= 0.75 and range_percentile <= 0.25)
    confidence = 0.72 if disagreement else 0.86
    if range_percentile >= 0.97 or rv_percentile >= 0.97:
        return VolatilityState.EXTREME, confidence, ("SESSION_RANGE_PERCENTILE_EXTREME",), disagreement
    if range_percentile >= 0.75 or rv_percentile >= 0.75:
        return VolatilityState.EXPANDING, confidence, (SESSION_RANGE_PERCENTILE_EXPANDING,), disagreement
    if range_percentile <= 0.25 and rv_percentile <= 0.35:
        return VolatilityState.COMPRESSED, confidence, ("SESSION_RANGE_PERCENTILE_COMPRESSED",), disagreement
    return VolatilityState.NORMAL, confidence, ("SESSION_RANGE_PERCENTILE_NORMAL",), disagreement


def _liquidity_axis(features: dict[str, Any] | None) -> tuple[LiquidityState, float, tuple[str, ...], bool, float]:
    state = _enum_value(LiquidityState, (features or {}).get("liquidityState"), LiquidityState.UNKNOWN)
    block = bool((features or {}).get("blockNewEntries"))
    reasons = list((features or {}).get("reasonCodes") or ())
    if state == LiquidityState.STALE:
        reasons.append(SESSION_QUOTE_STALE)
    if state in {LiquidityState.STRESSED, LiquidityState.STALE} or block:
        reasons.append(SESSION_LIQUIDITY_BLOCK)
    confidence = {
        LiquidityState.HEALTHY: 0.90,
        LiquidityState.CONSTRAINED: 0.75,
        LiquidityState.STRESSED: 0.70,
        LiquidityState.STALE: 0.35,
        LiquidityState.UNKNOWN: 0.25,
    }[state]
    safety_confidence = 0.92 if state in {LiquidityState.STRESSED, LiquidityState.STALE} else 0.80 if block else 0.0
    return state, confidence, tuple(dict.fromkeys(reasons or [f"SESSION_LIQUIDITY_{state.value.upper()}"])), block, safety_confidence


def _data_quality_axis(result: Any | None) -> tuple[DataQualityState, float, tuple[str, ...], bool]:
    if result is None:
        return DataQualityState.INCOMPLETE, 0.35, ("SESSION_DATA_QUALITY_MISSING",), True
    state = _enum_value(DataQualityState, _get(result, "state"), DataQualityState.INCOMPLETE)
    confidence = _number(_get(result, "confidence"))
    reasons = list(_get(result, "reason_codes") or _get(result, "reasonCodes") or ())
    if state == DataQualityState.WARMING_UP:
        reasons.append(SESSION_DATA_WARMING_UP)
    if state == DataQualityState.READY:
        reasons.append(SESSION_DATA_READY)
    block = bool(_get(result, "block_new_entries") or _get(result, "blockNewEntries") or state in {DataQualityState.INCOMPLETE, DataQualityState.STALE, DataQualityState.INVALID})
    return state, 0.35 if confidence is None else confidence, tuple(dict.fromkeys(reasons or [f"SESSION_DATA_{state.value.upper()}"])), block


def _event_axis(context: Any | None) -> tuple[EventRiskState, tuple[str, ...], bool, float]:
    if context is None:
        return EventRiskState.CLEAR, ("SESSION_EVENT_CLEAR",), False, 0.0
    risk_state = str(_get(context, "risk_state") or _get(context, "riskState") or "unknown").lower()
    block = bool(_get(context, "block_new_entries") or _get(context, "blockNewEntries"))
    reasons = list(_get(context, "reason_codes") or _get(context, "reasonCodes") or ())
    if block or risk_state in {"blackout", "blocked", "event_blackout", "halt", "luld"}:
        return EventRiskState.BLACKOUT, tuple(dict.fromkeys([*reasons, SESSION_EVENT_BLACKOUT])), True, 0.95
    if risk_state in {"elevated", "watch", "risk"}:
        return EventRiskState.ELEVATED, tuple(dict.fromkeys(reasons or ["SESSION_EVENT_ELEVATED"])), False, 0.0
    if risk_state in {"clear", "normal", "none"}:
        return EventRiskState.CLEAR, tuple(dict.fromkeys(reasons or ["SESSION_EVENT_CLEAR"])), False, 0.0
    return EventRiskState.UNKNOWN, tuple(dict.fromkeys(reasons or ["SESSION_EVENT_UNKNOWN"])), False, 0.0


def _classification_conflicts(structure: dict[str, Any], vwap: dict[str, Any], volatility_conflict: bool) -> tuple[str, ...]:
    conflicts: list[str] = []
    behavior = structure.get("behavior")
    current = (vwap.get("current") or {})
    position = current.get("position")
    if behavior in {"trend_up", "valid_breakout_up", "reversal_up"} and position == "below":
        conflicts.append(SESSION_CLASSIFICATION_CONFLICT)
    if behavior in {"trend_down", "valid_breakout_down", "reversal_down"} and position == "above":
        conflicts.append(SESSION_CLASSIFICATION_CONFLICT)
    if volatility_conflict:
        conflicts.append(SESSION_CLASSIFICATION_CONFLICT)
    return tuple(dict.fromkeys(conflicts))


def _opening_breakout_allowed(phase: SessionPhase, clock: SessionClock | None) -> bool:
    if phase in {SessionPhase.OPENING_AUCTION, SessionPhase.OPENING_DISCOVERY, SessionPhase.MIDDAY, SessionPhase.CLOSING_AUCTION, SessionPhase.CLOSED, SessionPhase.PREMARKET, SessionPhase.POSTMARKET, SessionPhase.UNKNOWN}:
        return False
    if clock and clock.minutes_until_close is not None and clock.minutes_until_close <= 15:
        return False
    return True


def _phase_confidence(phase: SessionPhase) -> float:
    if phase == SessionPhase.UNKNOWN:
        return 0.35
    if phase == SessionPhase.CLOSED:
        return 0.50
    return 0.95


def _enum_value(enum_type: Any, value: Any, default: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return default


def _get(source: Any, snake: str, camel: str | None = None) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(snake) if snake in source else source.get(camel or snake)
    return getattr(source, snake, getattr(source, camel or snake, None))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
