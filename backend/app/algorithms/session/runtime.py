"""Authoritative point-in-time Session classifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite, sqrt
from statistics import mean
from typing import Any

from backend.app.algorithms.session.calendar import SessionClock, resolve_session_clock
from backend.app.algorithms.session.classifier import SESSION_ALLOWED_FAMILIES, SESSION_BLOCKED_FAMILIES, classify_session_axes
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.data_quality import evaluate_session_data_quality
from backend.app.algorithms.session.baselines import SessionBaselineArtifact
from backend.app.algorithms.session.models import DataQualityState, LiquidityState, SessionBehavior, SessionClassification, SessionPhase, VolatilityState
from backend.app.algorithms.session.liquidity import analyze_session_liquidity
from backend.app.algorithms.session.opening_range import analyze_opening_ranges, legacy_opening_range_value
from backend.app.algorithms.session.structure import analyze_session_structure, legacy_pullback_depth_value, same_time_volume_value
from backend.app.algorithms.session.volatility import analyze_session_volatility
from backend.app.algorithms.session.volume import analyze_session_participation
from backend.app.algorithms.session.vwap import analyze_vwap, legacy_vwap_crosses, legacy_vwap_slope, legacy_vwap_value
from backend.app.algorithms.session.state import (
    FINALIZED_ONE_MINUTE_BAR,
    MARKET_STATUS_CALENDAR_UPDATE,
    QUOTE_NBBO_UPDATE,
    REPLAY_RESET,
    SCHEDULED_EVENT_RISK_UPDATE,
    SESSION_RESET,
    SessionRuntimeKey,
    SessionRuntimeState,
    bar_from_event,
    calendar_from_event,
    event_risk_from_event,
    normalize_session_event,
    quote_from_event,
    runtime_key_for_event,
    stable_hash,
)


class EventDrivenSessionRuntime:
    """Stateful Session runtime fed by finalized market events."""

    def __init__(self, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> None:
        self.config = config
        self._states: dict[SessionRuntimeKey, SessionRuntimeState] = {}

    def process_event(self, raw_event: dict[str, Any]) -> SessionClassification | None:
        event = normalize_session_event(raw_event, config=self.config)

        if event.event_type == REPLAY_RESET:
            self._reset_replay(event.symbol, event.runtime_mode)
            return None

        key = runtime_key_for_event(event, config=self.config)

        if event.event_type == SESSION_RESET:
            self._states.pop(key, None)
            return None

        state = self._states.get(key) or SessionRuntimeState(key=key)
        if event.event_id in state.processed_event_ids:
            return self._classify_state(state)

        if event.event_type == FINALIZED_ONE_MINUTE_BAR:
            if not bool(event.payload.get("finalized", event.payload.get("is_finalized", True))):
                self._states[key] = state.mark_unfinalized(event.event_id)
                return None
            self._states[key] = state.apply_bar(bar_from_event(event), event.event_id)
            return self._classify_state(self._states[key])

        if event.event_type == QUOTE_NBBO_UPDATE:
            self._states[key] = state.apply_quote(quote_from_event(event), event.event_id)
            return self._classify_state(self._states[key])

        if event.event_type == MARKET_STATUS_CALENDAR_UPDATE:
            self._states[key] = state.apply_calendar(calendar_from_event(event), event.event_id)
            return self._classify_state(self._states[key])

        if event.event_type == SCHEDULED_EVENT_RISK_UPDATE:
            self._states[key] = state.apply_event_risk(event_risk_from_event(event), event.event_id)
            return self._classify_state(self._states[key])

        raise ValueError(f"Unsupported Session event type: {event.event_type}")

    def state_for(self, *, symbol: str, session_date: str, runtime_mode: str) -> SessionRuntimeState | None:
        return self._states.get(SessionRuntimeKey(symbol=symbol.upper(), session_date=session_date, runtime_mode=runtime_mode))

    def snapshot(self) -> dict[str, Any]:
        return {
            "configHash": self.config.configuration_hash,
            "states": [self._states[key].to_snapshot() for key in sorted(self._states)],
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> EventDrivenSessionRuntime:
        runtime = cls(config=config)
        for state_payload in snapshot.get("states", ()):
            state = SessionRuntimeState.from_snapshot(state_payload)
            runtime._states[state.key] = state
        return runtime

    def _reset_replay(self, symbol: str, runtime_mode: str) -> None:
        keys = [key for key in self._states if key.symbol == symbol.upper() and key.runtime_mode == runtime_mode]
        for key in keys:
            self._states.pop(key, None)

    def _classify_state(self, state: SessionRuntimeState) -> SessionClassification | None:
        if not state.bars:
            return None

        latest_bar = state.bars[-1]
        decision_time = latest_bar.timestamp_utc
        clock = resolve_session_clock(decision_time, config=self.config)
        quote = state.quote if state.quote and state.quote.timestamp_utc <= decision_time else None
        candles = [_runtime_candle(bar, quote if bar.timestamp_utc == latest_bar.timestamp_utc else None, decision_time=decision_time) for bar in state.bars]
        quality = evaluate_session_data_quality(state.bars, quote=quote, clock=clock, decision_time=decision_time, config=self.config)
        classification = classify_session(
            state.key.symbol,
            candles,
            config=self.config,
            decision_time=decision_time,
            data_quality_report=quality,
            event_risk_context=state.event_risk,
        )
        feature_snapshot_id = state.feature_snapshot_id(config=self.config)
        market_event_id = latest_bar.event_id
        reason_codes = tuple(dict.fromkeys((*classification.reason_codes, *quality.reason_codes)))
        classification_id = "session-classification-" + stable_hash(
            {
                "marketEventId": market_event_id,
                "featureSnapshotId": feature_snapshot_id,
                "symbol": state.key.symbol,
                "sessionDate": state.key.session_date,
                "runtimeMode": state.key.runtime_mode,
                "phase": classification.phase.value,
                "behavior": classification.behavior.value,
                "dataQualityState": quality.state.value,
                "reasonCodes": reason_codes,
                "blockNewEntries": classification.block_new_entries,
            }
        )
        return classification.model_copy(
            update={
                "data_quality_state": quality.state,
                "data_quality_confidence": quality.confidence,
                "reason_codes": reason_codes,
                "evidence": {
                    **classification.evidence,
                    "marketEventId": market_event_id,
                    "featureSnapshotId": feature_snapshot_id,
                    "classificationId": classification_id,
                    "runtime": {
                        "runtimeMode": state.key.runtime_mode,
                        "processedEventCount": len(state.processed_event_ids),
                        "lateBarEventIds": tuple(sorted(state.late_bar_event_ids)),
                        "revisedBarTimestamps": tuple(sorted(state.revised_bar_timestamps)),
                        "ignoredUnfinalizedEventIds": tuple(sorted(state.ignored_unfinalized_event_ids)),
                    },
                    "dataQuality": quality.evidence,
                    "eventRisk": None
                    if state.event_risk is None
                    else {
                        "riskState": state.event_risk.risk_state,
                        "blockNewEntries": state.event_risk.block_new_entries,
                        "reasonCodes": state.event_risk.reason_codes,
                    },
                },
            }
        )


def classify_session(
    symbol: str,
    candles: list[dict[str, Any]],
    daily_candles: list[dict[str, Any]] | None = None,
    *,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
    decision_time: datetime | None = None,
    baseline_artifact: SessionBaselineArtifact | None = None,
    data_quality_report: Any | None = None,
    event_risk_context: Any | None = None,
) -> SessionClassification:
    normalized = sorted((_normalize_candle(candle) for candle in candles), key=lambda candle: candle["timestamp"])
    latest = normalized[-1] if normalized else None
    market_event_time = _parse_time(latest["timestamp"]) if latest else None
    feature_snapshot_time = market_event_time
    decision_at = _ensure_aware(decision_time) or feature_snapshot_time or datetime.now(UTC)
    valid_until = decision_at + timedelta(seconds=config.decision_valid_for_seconds)
    clock = _session_clock(market_event_time, config)
    phase = clock.current_phase if clock else SessionPhase.UNKNOWN
    session_date = clock.session_date if clock else None

    if len(normalized) < config.minimum_behavior_bars:
        return _building_classification(
            symbol,
            normalized,
            config=config,
            phase=phase,
            session_date=session_date,
            market_event_time=market_event_time,
            feature_snapshot_time=feature_snapshot_time,
            decision_time=decision_at,
            valid_until=valid_until,
            clock=clock,
        )

    evidence = _session_evidence(normalized, daily_candles or [], config, clock=clock, symbol=symbol, baseline_artifact=baseline_artifact)
    if data_quality_report is None:
        liquidity_for_data = LiquidityState((evidence.get("liquidityEvidence") or {}).get("liquidityState") or LiquidityState.UNKNOWN.value)
        data_quality_state, data_quality_confidence, data_quality_reasons = _data_quality_state(normalized, evidence, liquidity_for_data, config)
        data_quality_report = {
            "state": data_quality_state.value,
            "confidence": data_quality_confidence,
            "reasonCodes": data_quality_reasons,
            "blockNewEntries": data_quality_state in {DataQualityState.INCOMPLETE, DataQualityState.STALE, DataQualityState.INVALID},
        }
    axes = classify_session_axes(
        clock=clock,
        data_quality_result=data_quality_report,
        opening_range_features=evidence.get("openingRanges"),
        vwap_features=evidence.get("vwapFeatures"),
        volatility_features=evidence.get("volatilityEvidence"),
        volume_features=evidence.get("participationEvidence"),
        liquidity_features=evidence.get("liquidityEvidence"),
        structure_features=evidence.get("structureEvidence"),
        event_risk_context=event_risk_context,
        config=config,
    )
    reason_codes = tuple(
        dict.fromkeys(
            [
                *_phase_reason_codes(axes.phase),
                *axes.reason_codes,
                *((evidence.get("openingRanges") or {}).get("reasonCodes") or ()),
                *((evidence.get("vwapFeatures") or {}).get("reasonCodes") or ()),
                *((evidence.get("volatilityEvidence") or {}).get("reasonCodes") or ()),
                *((evidence.get("participationEvidence") or {}).get("reasonCodes") or ()),
                *((evidence.get("liquidityEvidence") or {}).get("reasonCodes") or ()),
                *((evidence.get("structureEvidence") or {}).get("reasonCodes") or ()),
                *_volatility_reason_codes(axes.volatility_state),
                *_liquidity_reason_codes(axes.liquidity_state),
            ]
        )
    )

    return SessionClassification(
        symbol=symbol,
        session_date=session_date,
        exchange_timezone=config.exchange_timezone,
        market_event_time=market_event_time,
        feature_snapshot_time=feature_snapshot_time,
        decision_time=decision_at,
        valid_until=valid_until,
        phase=axes.phase,
        behavior=axes.behavior,
        volatility_state=axes.volatility_state,
        liquidity_state=axes.liquidity_state,
        data_quality_state=axes.data_quality_state,
        event_risk_state=axes.event_risk_state,
        direction_bias=axes.direction_bias,
        phase_confidence=axes.phase_confidence,
        behavior_confidence=axes.behavior_confidence,
        volatility_confidence=axes.volatility_confidence,
        liquidity_confidence=axes.liquidity_confidence,
        data_quality_confidence=axes.data_quality_confidence,
        overall_confidence=axes.overall_confidence,
        safety_block_confidence=axes.safety_block_confidence,
        reason_codes=reason_codes,
        evidence={**evidence, "classifierAxes": axes.evidence, "legacyTags": axes.legacy_tags, "configurationHash": config.configuration_hash},
        allowed_strategy_families=axes.allowed_strategy_families,
        blocked_strategy_families=axes.blocked_strategy_families,
        block_new_entries=axes.block_new_entries,
    )


def legacy_volume_pace(volumes: list[float], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> float | None:
    if len(volumes) < config.vwap_slope_minimum_bars:
        return None
    recent = mean(volumes[-10:])
    baseline = mean(volumes[:-10])
    if baseline <= 0:
        return None
    return recent / baseline


def legacy_failed_breakouts(candles: list[dict[str, Any]], opening_high: float, opening_low: float) -> str:
    normalized = sorted((_normalize_candle(candle) for candle in candles), key=lambda candle: candle["timestamp"])
    if len(normalized) < 15:
        return "NA"
    failures = 0
    for candle in normalized[15:]:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if high > opening_high and close < opening_high:
            failures += 1
        if low < opening_low and close > opening_low:
            failures += 1
    return str(failures)


def legacy_liquidity_stress_signal(candles: list[dict[str, Any]], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> str:
    if not candles:
        return "unknown"
    latest = sorted((_normalize_candle(candle) for candle in candles), key=lambda candle: candle["timestamp"])[-1]
    state = LiquidityState(analyze_session_liquidity(latest, decision_time=latest["timestamp"], config=config)["liquidityState"])
    return _legacy_liquidity_stress_value(state)


def _building_classification(
    symbol: str,
    candles: list[dict[str, Any]],
    *,
    config: SessionConfig,
    phase: SessionPhase,
    session_date: str | None,
    market_event_time: datetime | None,
    feature_snapshot_time: datetime | None,
    decision_time: datetime,
    valid_until: datetime,
    clock: SessionClock | None,
) -> SessionClassification:
    state = DataQualityState.INCOMPLETE if not candles else DataQualityState.WARMING_UP
    evidence = _empty_session_evidence(candles, clock=clock)
    return SessionClassification(
        symbol=symbol,
        session_date=session_date,
        exchange_timezone=config.exchange_timezone,
        market_event_time=market_event_time,
        feature_snapshot_time=feature_snapshot_time,
        decision_time=decision_time,
        valid_until=valid_until,
        phase=phase,
        behavior=SessionBehavior.BUILDING,
        volatility_state=VolatilityState.UNKNOWN if not candles else VolatilityState.NORMAL,
        liquidity_state=LiquidityState.UNKNOWN,
        data_quality_state=state,
        direction_bias="neutral",
        phase_confidence=0.35 if phase in {SessionPhase.UNKNOWN, SessionPhase.CLOSED} else 0.75,
        behavior_confidence=0.25,
        volatility_confidence=0.25,
        liquidity_confidence=0.25,
        data_quality_confidence=0.2 if not candles else 0.45,
        overall_confidence=0.25,
        reason_codes=("session.data.warming_up", "session.behavior.building"),
        evidence={**evidence, "legacyTags": ("wait",), "configurationHash": config.configuration_hash},
        allowed_strategy_families=SESSION_ALLOWED_FAMILIES[SessionBehavior.BUILDING],
        blocked_strategy_families=SESSION_BLOCKED_FAMILIES[SessionBehavior.BUILDING],
        block_new_entries=True,
    )


def _session_evidence(
    candles: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    config: SessionConfig,
    *,
    clock: SessionClock | None,
    symbol: str,
    baseline_artifact: SessionBaselineArtifact | None,
) -> dict[str, Any]:
    first = candles[0]
    latest = candles[-1]
    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]
    session_high = max(highs)
    session_low = min(lows)
    session_range = max(session_high - session_low, 0.01)
    move = float(latest["close"]) - float(first["open"])
    opening = candles[: min(config.opening_range_minutes, len(candles))]
    opening_high = max(float(candle["high"]) for candle in opening)
    opening_low = min(float(candle["low"]) for candle in opening)
    opening_ranges = analyze_opening_ranges(candles, config=config)
    references = opening_ranges.get("references") or {}
    or30 = references.get("OR30") or {}
    opening_high = float(or30.get("high")) if or30.get("high") is not None else opening_high
    opening_low = float(or30.get("low")) if or30.get("low") is not None else opening_low
    vwap_features = analyze_vwap(candles, config=config)
    vwap = legacy_vwap_value(vwap_features)
    vwap_slope = legacy_vwap_slope(vwap_features, config=config)
    vwap_crosses = legacy_vwap_crosses(vwap_features) or 0
    vwap_current = vwap_features.get("current") or {}
    volatility_evidence = analyze_session_volatility(candles, symbol=symbol, baseline_artifact=baseline_artifact, decision_time=latest["timestamp"], config=config)
    participation_evidence = analyze_session_participation(candles, symbol=symbol, baseline_artifact=baseline_artifact, decision_time=latest["timestamp"], config=config)
    liquidity_evidence = analyze_session_liquidity(latest, decision_time=latest["timestamp"], config=config)
    structure_context = {
        "openingRanges": opening_ranges,
        "vwapFeatures": vwap_features,
        "participationEvidence": participation_evidence,
        "priorDayHigh": latest.get("priorDayHigh") or latest.get("prior_day_high"),
        "priorDayLow": latest.get("priorDayLow") or latest.get("prior_day_low"),
        "premarketHigh": latest.get("premarketHigh") or latest.get("premarket_high"),
        "premarketLow": latest.get("premarketLow") or latest.get("premarket_low"),
        "vwapCrossingFrequencyPerHour": vwap_current.get("crossingFrequencyPerHour"),
    }
    structure_evidence = analyze_session_structure(candles, structure_context, config=config)
    recent_range = float(volatility_evidence["oneMinuteTrueRangePercent"] or 0.0)
    base_range = float((volatility_evidence.get("baseline") or {}).get("rangePctMedian") or recent_range or _average_range(candles[:-15] or candles))
    avg_daily_range = _average_daily_range(daily, 20)
    volume_pace = participation_evidence.get("volumePaceRatio") if participation_evidence.get("status") == "ready" else legacy_volume_pace(volumes, config=config)
    return {
        "barCount": len(candles),
        "latestTimestamp": latest["timestamp"],
        "sessionHigh": session_high,
        "sessionLow": session_low,
        "sessionRange": session_range,
        "efficiency": abs(move) / session_range,
        "vwap": vwap,
        "vwapSlope": vwap_slope,
        "vwapCrosses": vwap_crosses,
        "recentRange": recent_range,
        "baseRange": base_range,
        "rangeVsAverageDailyRange": session_range / avg_daily_range if avg_daily_range else None,
        "realizedIntradayVolatility": _intraday_realized_vol(closes),
        "openingHigh": opening_high,
        "openingLow": opening_low,
        "latestClose": float(latest["close"]),
        "aboveVwap": vwap_current.get("position") == "above",
        "belowVwap": vwap_current.get("position") == "below",
        "vwapDistanceDollars": vwap_current.get("distanceDollars"),
        "vwapDistanceBps": vwap_current.get("distanceBps"),
        "vwapDistanceAtr": vwap_current.get("distanceAtr"),
        "vwapCrossingFrequencyPerHour": vwap_current.get("crossingFrequencyPerHour"),
        "vwapTimeAboveBars": vwap_current.get("timeAboveBars"),
        "vwapTimeBelowBars": vwap_current.get("timeBelowBars"),
        "vwapAcceptanceAbove": vwap_current.get("acceptanceAbove"),
        "vwapAcceptanceBelow": vwap_current.get("acceptanceBelow"),
        "vwapAverageExcursion": vwap_current.get("averageExcursion"),
        "vwapReclaimAbove": vwap_current.get("reclaimAbove"),
        "vwapReclaimBelow": vwap_current.get("reclaimBelow"),
        "vwapRejectionAbove": vwap_current.get("rejectionAbove"),
        "vwapRejectionBelow": vwap_current.get("rejectionBelow"),
        "vwapFeatures": vwap_features,
        "volumePace": volume_pace,
        "participationEvidence": participation_evidence,
        "volatilityEvidence": volatility_evidence,
        "liquidityEvidence": liquidity_evidence,
        "liquidityStress": _legacy_liquidity_stress_value(LiquidityState(liquidity_evidence["liquidityState"])),
        "structureEvidence": structure_evidence,
        "pullbackDepth": legacy_pullback_depth_value(structure_evidence),
        "sameTimeVolumeAvg": same_time_volume_value(participation_evidence),
        "failedBreakouts": legacy_failed_breakouts(candles, opening_high, opening_low),
        "openingRange5m": legacy_opening_range_value(references.get("OR5")),
        "openingRange15m": legacy_opening_range_value(references.get("OR15")),
        "openingRange30m": legacy_opening_range_value(references.get("OR30")),
        "openingRanges": opening_ranges,
        "candleWindow": _candle_window("1Min", candles, "Today's 1-minute candles"),
        "sessionClock": clock.as_dict() if clock else None,
    }


def _empty_session_evidence(candles: list[dict[str, Any]], *, clock: SessionClock | None = None) -> dict[str, Any]:
    return {
        "barCount": len(candles),
        "vwap": None,
        "vwapSlope": None,
        "vwapCrosses": None,
        "vwapFeatures": {
            "status": "not_ready",
            "metadata": {
                "priceConvention": "typical_price_x_volume",
                "priceConventionDescription": "VWAP uses finalized one-minute typical price ((high + low + close) / 3) weighted by finalized bar volume.",
            },
            "current": None,
            "slopes": {},
            "history": [],
            "barCount": len(candles),
            "cumulativeVolume": 0.0,
            "reasonCodes": ("session.vwap.no_regular_bars",),
        },
        "rangeVsAverageDailyRange": None,
        "realizedIntradayVolatility": None,
        "volatilityEvidence": {
            "status": "unknown",
            "oneMinuteTrueRangePercent": None,
            "shortWindowRealizedVolatility": None,
            "rangePercentile": None,
            "realizedVolatilityPercentile": None,
            "baseline": {"baselineVersion": None, "baselineCutoffDate": None},
            "reasonCodes": ("session.volatility.no_bars",),
        },
        "efficiency": None,
        "volumePace": None,
        "participationEvidence": {
            "status": "unknown",
            "currentCumulativeVolume": None,
            "expectedCumulativeVolume": None,
            "volumePaceRatio": None,
            "oneMinuteRelativeVolume": None,
            "rollingRelativeVolume": None,
            "oneMinuteVolumePercentile": None,
            "cumulativeVolumePercentile": None,
            "baseline": {"baselineVersion": None, "baselineCutoffDate": None},
            "reasonCodes": ("session.volume.no_bars",),
        },
        "failedBreakouts": "NA",
        "liquidityStress": "NA",
        "liquidityEvidence": {
            "status": "unknown",
            "liquidityState": "unknown",
            "dataQualityState": "incomplete",
            "blockNewEntries": True,
            "reasonCodes": ("session.liquidity.quote_missing",),
        },
        "structureEvidence": {
            "status": "not_ready",
            "behavior": "unknown",
            "reasonCodes": ("session.structure.not_enough_bars",),
        },
        "pullbackDepth": "not-ready",
        "sameTimeVolumeAvg": "not-ready",
        "openingRange5m": "NA",
        "openingRange15m": "NA",
        "openingRange30m": "NA",
        "openingRanges": {
            "references": {},
            "breakouts": {},
            "openingDrive": {"status": "not_ready", "direction": "unknown", "reasonCodes": ("session.opening_range.no_regular_bars",)},
            "reasonCodes": ("session.opening_range.no_regular_bars",),
        },
        "candleWindow": _candle_window("1Min", candles, "Today's intraday candles"),
        "sessionClock": clock.as_dict() if clock else None,
    }


def _session_behavior(evidence: dict[str, Any], liquidity_state: LiquidityState, config: SessionConfig) -> tuple[SessionBehavior, str, list[str], tuple[str, ...]]:
    if liquidity_state in {LiquidityState.STRESSED, LiquidityState.STALE}:
        return SessionBehavior.LIQUIDITY_STRESS, "cash", ["session.liquidity.blocking_stress"], ("liquidity-stress",)
    structure = evidence.get("structureEvidence") or {}
    structure_behavior = structure.get("behavior")
    structure_reasons = list(structure.get("reasonCodes") or ())
    if structure_behavior == "valid_breakout_up":
        return SessionBehavior.BREAKOUT_UP, "long", [*structure_reasons, "session.behavior.breakout_up"], ("breakout", "long-bias")
    if structure_behavior == "valid_breakout_down":
        return SessionBehavior.BREAKOUT_DOWN, "short", [*structure_reasons, "session.behavior.breakout_down"], ("breakout", "short-bias")
    if structure_behavior == "failed_breakout_up":
        return SessionBehavior.FAILED_BREAKOUT_UP, "short", [*structure_reasons, "session.behavior.failed_breakout_up"], ("failed-breakout", "reversal")
    if structure_behavior == "failed_breakout_down":
        return SessionBehavior.FAILED_BREAKOUT_DOWN, "long", [*structure_reasons, "session.behavior.failed_breakout_down"], ("failed-breakout", "reversal")
    if structure_behavior == "reversal_up":
        return SessionBehavior.REVERSAL_UP, "long", [*structure_reasons, "session.behavior.reversal_up"], ("reversal", "long-bias")
    if structure_behavior == "reversal_down":
        return SessionBehavior.REVERSAL_DOWN, "short", [*structure_reasons, "session.behavior.reversal_down"], ("reversal", "short-bias")
    if structure_behavior == "trend_up":
        return SessionBehavior.TREND_UP, "long", [*structure_reasons, "session.behavior.trend_up"], ("trend-day", "long-bias", "above-vwap")
    if structure_behavior == "trend_down":
        return SessionBehavior.TREND_DOWN, "short", [*structure_reasons, "session.behavior.trend_down"], ("trend-day", "short-bias", "below-vwap")
    if structure_behavior == "choppy":
        return SessionBehavior.CHOPPY, "neutral", [*structure_reasons, "session.behavior.choppy"], ("chop", "avoid-breakout")
    if structure_behavior == "mean_reverting":
        return SessionBehavior.MEAN_REVERTING, "neutral", [*structure_reasons, "session.behavior.mean_reverting"], ("mean-reversion", "vwap-reversion")
    latest_close = float(evidence["latestClose"])
    opening_high = float(evidence["openingHigh"])
    opening_low = float(evidence["openingLow"])
    efficiency = float(evidence["efficiency"])
    vwap_crosses = int(evidence["vwapCrosses"])
    if efficiency > config.trend_efficiency_threshold and evidence["aboveVwap"] and latest_close > opening_high:
        return SessionBehavior.TREND_UP, "long", ["session.behavior.trend_up", "session.vwap.price_above", "session.opening_range.high_cleared"], ("trend-day", "long-bias", "above-vwap")
    if efficiency > config.trend_efficiency_threshold and evidence["belowVwap"] and latest_close < opening_low:
        return SessionBehavior.TREND_DOWN, "short", ["session.behavior.trend_down", "session.vwap.price_below", "session.opening_range.low_cleared"], ("trend-day", "short-bias", "below-vwap")
    if vwap_crosses >= config.choppy_vwap_crosses and efficiency < config.choppy_efficiency_threshold:
        return SessionBehavior.CHOPPY, "neutral", ["session.behavior.choppy_vwap_whipsaw"], ("chop", "avoid-breakout")
    if vwap_crosses >= config.mean_reversion_vwap_crosses and efficiency < config.mean_reversion_efficiency_threshold:
        return SessionBehavior.MEAN_REVERTING, "neutral", ["session.behavior.mean_reverting_vwap_rotation"], ("mean-reversion", "vwap-reversion")
    return SessionBehavior.BALANCED_RANGE, "neutral", ["session.behavior.balanced_range"], ("balanced",)


def _volatility_state(evidence: dict[str, Any], config: SessionConfig) -> VolatilityState:
    normalized = evidence.get("volatilityEvidence") or {}
    if normalized.get("status") != "ready":
        return VolatilityState.UNKNOWN
    range_percentile = normalized.get("rangePercentile")
    rv_percentile = normalized.get("realizedVolatilityPercentile")
    if range_percentile is not None and rv_percentile is not None:
        if range_percentile >= 0.97 or rv_percentile >= 0.97:
            return VolatilityState.EXTREME
        if range_percentile >= 0.75 or rv_percentile >= 0.75:
            return VolatilityState.EXPANDING
        if range_percentile <= 0.25 and rv_percentile <= 0.35:
            return VolatilityState.COMPRESSED
        return VolatilityState.NORMAL
    recent = float(evidence["recentRange"])
    base = float(evidence["baseRange"])
    if base <= 0:
        return VolatilityState.UNKNOWN
    ratio = recent / base
    if ratio > config.expansion_range_ratio * 2.0:
        return VolatilityState.EXTREME
    if ratio > config.expansion_range_ratio:
        return VolatilityState.EXPANDING
    if ratio < config.compression_range_ratio:
        return VolatilityState.COMPRESSED
    return VolatilityState.NORMAL


def _liquidity_state(candles: list[dict[str, Any]], config: SessionConfig) -> LiquidityState:
    if not candles:
        return LiquidityState.UNKNOWN
    latest = sorted(candles, key=lambda candle: str(candle.get("timestamp") or ""))[-1]
    explicit = latest.get("liquidityStress") or latest.get("liquidity_stress")
    if isinstance(explicit, str) and explicit.strip():
        normalized = explicit.strip().lower()
        if normalized in {"active", "stress", "stressed", "poor", "true"}:
            return LiquidityState.STRESSED
        if normalized in {"inactive", "normal", "ok", "false"}:
            return LiquidityState.HEALTHY
        return LiquidityState.UNKNOWN
    spread_bps = _spread_bps(latest)
    quote_age_ms = _numeric_optional(_first_present(latest, "quoteAgeMs", "quote_age_ms", "ageMs", "age_ms"))
    if spread_bps is None and quote_age_ms is None:
        return LiquidityState.UNKNOWN
    if quote_age_ms is not None and quote_age_ms > config.maximum_stale_quote_age_ms:
        return LiquidityState.STALE
    if quote_age_ms is not None and quote_age_ms > config.maximum_fresh_quote_age_ms:
        return LiquidityState.STRESSED
    if spread_bps is not None and spread_bps > config.maximum_constrained_spread_bps:
        return LiquidityState.STRESSED
    if spread_bps is not None and spread_bps > config.maximum_healthy_spread_bps:
        return LiquidityState.CONSTRAINED
    if spread_bps is not None:
        return LiquidityState.HEALTHY
    return LiquidityState.UNKNOWN


def _data_quality_state(candles: list[dict[str, Any]], evidence: dict[str, Any], liquidity_state: LiquidityState, config: SessionConfig) -> tuple[DataQualityState, float, list[str]]:
    if not candles:
        return DataQualityState.INCOMPLETE, 0.2, ["session.data.no_bars"]
    required = ("timestamp", "open", "high", "low", "close", "volume")
    if any(any(key not in candle or candle[key] is None for key in required) for candle in candles):
        return DataQualityState.INVALID, 0.0, ["session.data.required_field_missing"]
    if any(float(candle["low"]) > float(candle["high"]) for candle in candles):
        return DataQualityState.INVALID, 0.0, ["session.data.invalid_ohlc"]
    if len(candles) < config.minimum_behavior_bars:
        return DataQualityState.WARMING_UP, 0.45, ["session.data.warming_up"]
    if liquidity_state == LiquidityState.STALE:
        return DataQualityState.STALE, 0.35, ["session.data.quote_stale"]
    if evidence.get("vwap") is None:
        return DataQualityState.INCOMPLETE, 0.55, ["session.data.vwap_unavailable"]
    liquidity_quality = (evidence.get("liquidityEvidence") or {}).get("dataQualityState")
    if liquidity_quality == DataQualityState.INVALID.value:
        return DataQualityState.INVALID, 0.0, ["session.data.liquidity_invalid"]
    if liquidity_quality == DataQualityState.STALE.value:
        return DataQualityState.STALE, 0.35, ["session.data.quote_stale"]
    return DataQualityState.READY, 0.9, ["session.data.ready"]


def _phase_reason_codes(phase: SessionPhase) -> list[str]:
    return [f"session.phase.{phase.value}"]


def _volatility_reason_codes(state: VolatilityState) -> list[str]:
    return [f"session.volatility.{state.value}"]


def _liquidity_reason_codes(state: LiquidityState) -> list[str]:
    return [f"session.liquidity.{state.value}"]


def _legacy_liquidity_stress_value(state: LiquidityState) -> str:
    if state == LiquidityState.UNKNOWN:
        return "unknown"
    if state in {LiquidityState.STRESSED, LiquidityState.STALE}:
        return "Active"
    return "Inactive"


def _liquidity_confidence(state: LiquidityState, evidence: dict[str, Any]) -> float:
    if evidence.get("dataQualityState") == DataQualityState.INVALID.value:
        return 0.0
    if state == LiquidityState.UNKNOWN:
        return 0.25
    if state == LiquidityState.HEALTHY:
        return 0.9
    if state == LiquidityState.STALE:
        return 0.35
    return 0.75


def _session_clock(timestamp: datetime | None, config: SessionConfig) -> SessionClock | None:
    if timestamp is None:
        return None
    return resolve_session_clock(timestamp, config=config)


def _runtime_candle(bar: Any, quote: Any | None, *, decision_time: datetime) -> dict[str, Any]:
    candle = bar.as_candle(quote, decision_time=decision_time)
    if candle["volume"] is None:
        candle["volume"] = 0.0
    return candle


def _normalize_candle(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        **candle,
        "timestamp": str(candle["timestamp"]),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": float(candle["volume"]),
    }


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware(parsed)


def _session_vwap(candles: list[dict[str, Any]]) -> float | None:
    total_volume = sum(float(candle["volume"]) for candle in candles)
    if total_volume <= 0:
        return None
    return sum(((float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3) * float(candle["volume"]) for candle in candles) / total_volume


def _vwap_slope(candles: list[dict[str, Any]], config: SessionConfig) -> float | None:
    if len(candles) < config.vwap_slope_minimum_bars:
        return None
    early = _session_vwap(candles[: len(candles) // 2])
    late = _session_vwap(candles[len(candles) // 2 :])
    if early is None or late is None or early == 0:
        return None
    return (late - early) / early


def _level_crosses(values: list[float], level: float | None) -> int:
    if not values or not level:
        return 0
    signs = [value >= level for value in values]
    return sum(1 for index in range(1, len(signs)) if signs[index] != signs[index - 1])


def _average_range(candles: list[dict[str, Any]]) -> float:
    if not candles:
        return 0.01
    return max(mean(float(candle["high"]) - float(candle["low"]) for candle in candles), 0.01)


def _average_daily_range(candles: list[dict[str, Any]], period: int) -> float | None:
    if len(candles) < 2:
        return None
    sample = candles[-period:]
    if not sample:
        return None
    return mean(float(candle["high"]) - float(candle["low"]) for candle in sample)


def _intraday_realized_vol(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    returns = [(values[index] - values[index - 1]) / values[index - 1] for index in range(1, len(values)) if values[index - 1] != 0]
    if not returns:
        return None
    return sqrt(sum(value * value for value in returns))


def _range_value(candles: list[dict[str, Any]]) -> str:
    if not candles:
        return "NA"
    high = max(float(candle["high"]) for candle in candles)
    low = min(float(candle["low"]) for candle in candles)
    start = float(candles[0]["open"])
    pct = (high - low) / start if start else 0
    return f"{high - low:.2f} ({pct * 100:.2f}%)"


def _candle_window(timeframe: str, candles: list[dict[str, Any]], label: str) -> dict[str, Any]:
    return {
        "timeframe": timeframe,
        "count": len(candles),
        "label": label,
        "start": candles[0]["timestamp"] if candles else None,
        "end": candles[-1]["timestamp"] if candles else None,
        "segments": [{"start": candles[0]["timestamp"] if candles else None, "end": candles[-1]["timestamp"] if candles else None}],
    }


def _spread_bps(candle: dict[str, Any]) -> float | None:
    direct = _numeric_optional(_first_present(candle, "spreadBps", "spread_bps"))
    if direct is not None:
        return direct
    spread_percent = _numeric_optional(_first_present(candle, "spreadPercent", "spread_percent"))
    if spread_percent is not None:
        return spread_percent * 100
    bid = _numeric_optional(candle.get("bid"))
    ask = _numeric_optional(candle.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * 10_000


def _numeric_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _confidence(flags: list[bool], *, floor: float) -> float:
    if not flags:
        return floor
    score = floor + (sum(1 for flag in flags if flag) / len(flags)) * (0.94 - floor)
    return max(floor, min(0.94, score))
