"""Weighted Voting internal market-condition classification."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import (
    WeightedEventRiskLevel,
    WeightedLiquidityLevel,
    WeightedMarketCondition,
    WeightedMarketQuality,
    WeightedRangeCondition,
    WeightedSessionPhase,
    WeightedTrendDirection,
    WeightedVolatilityLevel,
)
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume, eastern_minutes, regular_session_candles, slope, vwap


WEIGHTED_VOTING_MARKET_CONDITION_VERSION = "weighted_voting_market_condition_v2"


@dataclass(frozen=True)
class WeightedConditionCandidate:
    trend_direction: WeightedTrendDirection
    volatility_level: WeightedVolatilityLevel
    range_condition: WeightedRangeCondition
    liquidity_level: WeightedLiquidityLevel
    session_phase: WeightedSessionPhase
    event_risk: WeightedEventRiskLevel
    market_quality: WeightedMarketQuality
    confidence: float
    condition_inputs: dict[str, float | str | bool | None]
    reason_codes: tuple[str, ...]

    @property
    def condition_key(self) -> str:
        return "|".join(
            (
                str(self.trend_direction.value),
                str(self.volatility_level.value),
                str(self.liquidity_level.value),
                str(self.session_phase.value),
                str(self.event_risk.value),
                str(self.market_quality.value),
            )
        )


def classify_market_condition(
    snapshot: WeightedVotingMarketSnapshot,
    *,
    config: WeightedVotingConfig | None = None,
    previous_condition: WeightedMarketCondition | None = None,
) -> WeightedMarketCondition:
    active_config = config or WeightedVotingConfig()
    completed_candles = tuple(
        sorted((candle for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp), key=lambda candle: candle.timestamp)
    )
    if not completed_candles:
        return WeightedMarketCondition(
            trend_direction=WeightedTrendDirection.UNKNOWN,
            volatility_level=WeightedVolatilityLevel.UNKNOWN,
            range_condition=WeightedRangeCondition.UNKNOWN,
            liquidity_level=WeightedLiquidityLevel.UNKNOWN,
            session_phase=WeightedSessionPhase.UNKNOWN,
            event_risk=WeightedEventRiskLevel.UNKNOWN,
            market_quality=WeightedMarketQuality.UNKNOWN,
            session_label="unknown",
            data_ready=False,
            data_timestamp=snapshot.data_timestamp,
            reason_codes=("weighted_voting.market_condition.no_completed_candles",),
            explanation="Weighted Voting market condition unavailable: no one-minute candles.",
        )

    candidate = _candidate(snapshot, completed_candles, active_config)
    selected = _apply_hysteresis(candidate, previous_condition, active_config)
    return _condition_from_candidate(selected, snapshot, previous_condition, active_config)


def _candidate(snapshot, candles, config: WeightedVotingConfig) -> WeightedConditionCandidate:
    session_candles = regular_session_candles(candles) or candles
    closes = [candle.close for candle in session_candles]
    latest = session_candles[-1]
    atr = average_true_range(session_candles, 14)
    atr_percent = (atr / latest.close) if atr is not None and latest.close > 0 else None
    trend_slope = slope(closes, min(30, len(closes) - 1)) if len(closes) > 1 else 0.0
    short_slope = slope(closes, min(10, len(closes) - 1)) if len(closes) > 1 else 0.0
    current_vwap = vwap(session_candles)
    prior_vwap = vwap(tuple(session_candles[:-10])) if len(session_candles) > 20 else None
    vwap_slope = ((current_vwap - prior_vwap) / prior_vwap) if current_vwap is not None and prior_vwap and prior_vwap > 0 else 0.0
    relative_volume = _relative_volume(session_candles)
    spread_percent = _spread_percent(snapshot)
    gap_percent = _gap_percent(session_candles)
    reason_codes: list[str] = []

    trend_direction = _trend_direction(trend_slope, short_slope, vwap_slope, config)
    volatility_level = _volatility_level(atr_percent, config)
    liquidity_level = _liquidity_level(relative_volume, spread_percent, config)
    session_phase = _session_phase(snapshot)
    event_risk = _event_risk(gap_percent, volatility_level, config)
    market_quality = _market_quality(volatility_level, liquidity_level, event_risk, spread_percent)
    range_condition = _range_condition(trend_direction, volatility_level)

    if trend_direction in (WeightedTrendDirection.STRONG_UPTREND, WeightedTrendDirection.STRONG_DOWNTREND):
        reason_codes.append("weighted_voting.market_condition.strong_trend")
    if volatility_level == WeightedVolatilityLevel.EXTREME:
        reason_codes.append("weighted_voting.market_condition.extreme_volatility")
    if liquidity_level == WeightedLiquidityLevel.POOR:
        reason_codes.append("weighted_voting.market_condition.poor_liquidity")
    if event_risk == WeightedEventRiskLevel.BLOCKED:
        reason_codes.append("weighted_voting.market_condition.blocked_event_risk")
    if not reason_codes:
        reason_codes.append("weighted_voting.market_condition.classified")

    inputs = {
        "completed_candle_count": float(len(session_candles)),
        "trend_slope": round(trend_slope, 10),
        "short_slope": round(short_slope, 10),
        "vwap_slope": round(vwap_slope, 10),
        "atr_percent": round(atr_percent, 10) if atr_percent is not None else None,
        "relative_volume": round(relative_volume, 10) if relative_volume is not None else None,
        "spread_percent": round(spread_percent, 10) if spread_percent is not None else None,
        "gap_percent": round(gap_percent, 10) if gap_percent is not None else None,
        "latest_close": round(latest.close, 10),
    }
    confidence = _confidence(inputs, market_quality, len(session_candles))
    return WeightedConditionCandidate(
        trend_direction=trend_direction,
        volatility_level=volatility_level,
        range_condition=range_condition,
        liquidity_level=liquidity_level,
        session_phase=session_phase,
        event_risk=event_risk,
        market_quality=market_quality,
        confidence=confidence,
        condition_inputs=inputs,
        reason_codes=tuple(reason_codes),
    )


def _apply_hysteresis(
    candidate: WeightedConditionCandidate,
    previous_condition: WeightedMarketCondition | None,
    config: WeightedVotingConfig,
) -> WeightedConditionCandidate:
    if previous_condition is None or not previous_condition.data_ready:
        return candidate
    if _immediate_condition(candidate):
        return _with_reason(candidate, "weighted_voting.market_condition.immediate_deterioration")
    previous_key = _condition_key_from_condition(previous_condition)
    if candidate.condition_key == previous_key:
        return candidate
    candidate_exposure = _exposure_score(candidate)
    previous_exposure = _exposure_score_from_condition(previous_condition)
    if candidate_exposure <= previous_exposure:
        return _with_reason(candidate, "weighted_voting.market_condition.deterioration_accepted")

    pending_count = previous_condition.pending_confirmation_count + 1 if previous_condition.pending_condition_key == candidate.condition_key else 1
    if pending_count < config.market_condition_hysteresis_confirmations:
        held = WeightedConditionCandidate(
            trend_direction=WeightedTrendDirection(previous_condition.trend_direction),
            volatility_level=WeightedVolatilityLevel(previous_condition.volatility_level),
            range_condition=WeightedRangeCondition(previous_condition.range_condition),
            liquidity_level=WeightedLiquidityLevel(previous_condition.liquidity_level),
            session_phase=WeightedSessionPhase(previous_condition.session_phase),
            event_risk=WeightedEventRiskLevel(previous_condition.event_risk),
            market_quality=WeightedMarketQuality(previous_condition.market_quality),
            confidence=max(0.0, min(1.0, previous_condition.confidence)),
            condition_inputs={
                **candidate.condition_inputs,
                "held_previous_condition": True,
                "pending_candidate_key": candidate.condition_key,
                "pending_confirmation_count": float(pending_count),
            },
            reason_codes=tuple(previous_condition.reason_codes) + ("weighted_voting.market_condition.hysteresis_hold",),
        )
        return held
    return _with_reason(candidate, "weighted_voting.market_condition.hysteresis_confirmed")


def _condition_from_candidate(
    candidate: WeightedConditionCandidate,
    snapshot: WeightedVotingMarketSnapshot,
    previous_condition: WeightedMarketCondition | None,
    config: WeightedVotingConfig,
) -> WeightedMarketCondition:
    pending_key = None
    pending_count = 0
    if "weighted_voting.market_condition.hysteresis_hold" in candidate.reason_codes:
        pending_key = _candidate_pending_key(candidate, previous_condition)
        pending_count = int(candidate.condition_inputs.get("pending_confirmation_count") or 1)

    return WeightedMarketCondition(
        trend_direction=candidate.trend_direction,
        volatility_level=candidate.volatility_level,
        range_condition=candidate.range_condition,
        liquidity_level=candidate.liquidity_level,
        session_phase=candidate.session_phase,
        event_risk=candidate.event_risk,
        market_quality=candidate.market_quality,
        session_label=candidate.session_phase.value,
        confidence=round(candidate.confidence, 10),
        condition_inputs={**candidate.condition_inputs, "classifier_version": WEIGHTED_VOTING_MARKET_CONDITION_VERSION},
        pending_condition_key=pending_key,
        pending_confirmation_count=pending_count,
        data_ready=True,
        data_timestamp=snapshot.data_timestamp,
        reason_codes=candidate.reason_codes,
        explanation="Weighted Voting market condition classified from neutral raw market data only; no trade direction is selected.",
    )


def _trend_direction(trend_slope: float, short_slope: float, vwap_slope: float, config: WeightedVotingConfig) -> WeightedTrendDirection:
    composite = (trend_slope * 0.55) + (short_slope * 0.25) + (vwap_slope * 0.20)
    if composite >= config.market_condition_trend_strong_slope:
        return WeightedTrendDirection.STRONG_UPTREND
    if composite >= config.market_condition_trend_weak_slope:
        return WeightedTrendDirection.WEAK_UPTREND
    if composite <= -config.market_condition_trend_strong_slope:
        return WeightedTrendDirection.STRONG_DOWNTREND
    if composite <= -config.market_condition_trend_weak_slope:
        return WeightedTrendDirection.WEAK_DOWNTREND
    return WeightedTrendDirection.SIDEWAYS


def _volatility_level(atr_percent: float | None, config: WeightedVotingConfig) -> WeightedVolatilityLevel:
    if atr_percent is None:
        return WeightedVolatilityLevel.UNKNOWN
    if atr_percent >= config.market_condition_extreme_atr_percent:
        return WeightedVolatilityLevel.EXTREME
    if atr_percent >= config.market_condition_high_atr_percent:
        return WeightedVolatilityLevel.HIGH
    if atr_percent <= config.market_condition_very_low_atr_percent:
        return WeightedVolatilityLevel.VERY_LOW
    if atr_percent <= config.market_condition_low_atr_percent:
        return WeightedVolatilityLevel.LOW
    return WeightedVolatilityLevel.NORMAL


def _liquidity_level(relative_volume: float | None, spread_percent: float | None, config: WeightedVotingConfig) -> WeightedLiquidityLevel:
    if spread_percent is not None and spread_percent >= config.market_condition_poor_spread_percent:
        return WeightedLiquidityLevel.POOR
    if relative_volume is not None and relative_volume < config.market_condition_reduced_relative_volume:
        return WeightedLiquidityLevel.POOR
    if spread_percent is not None and spread_percent >= config.market_condition_reduced_spread_percent:
        return WeightedLiquidityLevel.REDUCED
    if relative_volume is not None and relative_volume < config.market_condition_good_relative_volume:
        return WeightedLiquidityLevel.REDUCED
    return WeightedLiquidityLevel.GOOD


def _session_phase(snapshot: WeightedVotingMarketSnapshot) -> WeightedSessionPhase:
    minutes = eastern_minutes(snapshot.data_timestamp)
    if 570 <= minutes < 600:
        return WeightedSessionPhase.OPENING
    if 600 <= minutes < 690:
        return WeightedSessionPhase.MORNING
    if 690 <= minutes < 810:
        return WeightedSessionPhase.MIDDAY
    if 810 <= minutes < 930:
        return WeightedSessionPhase.AFTERNOON
    if 930 <= minutes <= 960:
        return WeightedSessionPhase.CLOSING
    return WeightedSessionPhase.OUTSIDE_SESSION


def _event_risk(gap_percent: float | None, volatility_level: WeightedVolatilityLevel, config: WeightedVotingConfig) -> WeightedEventRiskLevel:
    if gap_percent is not None and abs(gap_percent) >= config.market_condition_blocked_gap_percent:
        return WeightedEventRiskLevel.BLOCKED
    if volatility_level == WeightedVolatilityLevel.EXTREME:
        return WeightedEventRiskLevel.BLOCKED
    if gap_percent is not None and abs(gap_percent) >= config.market_condition_elevated_gap_percent:
        return WeightedEventRiskLevel.ELEVATED
    return WeightedEventRiskLevel.NONE


def _market_quality(
    volatility_level: WeightedVolatilityLevel,
    liquidity_level: WeightedLiquidityLevel,
    event_risk: WeightedEventRiskLevel,
    spread_percent: float | None,
) -> WeightedMarketQuality:
    if volatility_level == WeightedVolatilityLevel.EXTREME or liquidity_level == WeightedLiquidityLevel.POOR or event_risk == WeightedEventRiskLevel.BLOCKED:
        return WeightedMarketQuality.UNSTABLE
    if volatility_level == WeightedVolatilityLevel.HIGH or liquidity_level == WeightedLiquidityLevel.REDUCED or event_risk == WeightedEventRiskLevel.ELEVATED or spread_percent is None:
        return WeightedMarketQuality.MIXED
    return WeightedMarketQuality.CLEAN


def _range_condition(trend_direction: WeightedTrendDirection, volatility_level: WeightedVolatilityLevel) -> WeightedRangeCondition:
    if trend_direction in (WeightedTrendDirection.STRONG_UPTREND, WeightedTrendDirection.STRONG_DOWNTREND):
        return WeightedRangeCondition.TRENDING
    if volatility_level in (WeightedVolatilityLevel.HIGH, WeightedVolatilityLevel.EXTREME):
        return WeightedRangeCondition.BREAKOUT
    if trend_direction == WeightedTrendDirection.SIDEWAYS:
        return WeightedRangeCondition.RANGE_BOUND
    return WeightedRangeCondition.CHOPPY


def _relative_volume(candles) -> float | None:
    if len(candles) < 5:
        return None
    baseline = average_volume(tuple(candles[:-1]), min(30, len(candles) - 1))
    if baseline <= 0:
        return None
    return candles[-1].volume / baseline


def _spread_percent(snapshot: WeightedVotingMarketSnapshot) -> float | None:
    if snapshot.bid is None or snapshot.ask is None:
        return None
    midpoint = (snapshot.bid + snapshot.ask) / 2.0
    if midpoint <= 0:
        return None
    return (snapshot.ask - snapshot.bid) / midpoint


def _gap_percent(candles) -> float | None:
    if len(candles) < 2:
        return None
    latest_day_session = regular_session_candles(tuple(candles))
    if not latest_day_session:
        return None
    first_session_candle = latest_day_session[0]
    prior = [candle for candle in candles if candle.timestamp < first_session_candle.timestamp]
    if not prior:
        return None
    previous_close = prior[-1].close
    if previous_close <= 0:
        return None
    return (first_session_candle.open - previous_close) / previous_close


def _confidence(inputs: dict[str, float | str | bool | None], market_quality: WeightedMarketQuality, candle_count: int) -> float:
    confidence = min(0.25, candle_count / 200.0)
    if inputs.get("atr_percent") is not None:
        confidence += 0.20
    if inputs.get("relative_volume") is not None:
        confidence += 0.20
    if inputs.get("spread_percent") is not None:
        confidence += 0.15
    confidence += 0.20 if market_quality == WeightedMarketQuality.CLEAN else 0.10 if market_quality == WeightedMarketQuality.MIXED else 0.04
    return max(0.0, min(1.0, confidence))


def _immediate_condition(candidate: WeightedConditionCandidate) -> bool:
    return (
        candidate.volatility_level == WeightedVolatilityLevel.EXTREME
        or candidate.liquidity_level == WeightedLiquidityLevel.POOR
        or candidate.event_risk == WeightedEventRiskLevel.BLOCKED
    )


def _exposure_score(candidate: WeightedConditionCandidate) -> int:
    quality = {
        WeightedMarketQuality.UNSTABLE: 0,
        WeightedMarketQuality.MIXED: 1,
        WeightedMarketQuality.CLEAN: 2,
        WeightedMarketQuality.UNKNOWN: 0,
    }[candidate.market_quality]
    liquidity = {
        WeightedLiquidityLevel.POOR: 0,
        WeightedLiquidityLevel.REDUCED: 1,
        WeightedLiquidityLevel.GOOD: 2,
        WeightedLiquidityLevel.UNKNOWN: 0,
    }[candidate.liquidity_level]
    volatility = {
        WeightedVolatilityLevel.EXTREME: 0,
        WeightedVolatilityLevel.HIGH: 1,
        WeightedVolatilityLevel.VERY_LOW: 1,
        WeightedVolatilityLevel.LOW: 2,
        WeightedVolatilityLevel.NORMAL: 3,
        WeightedVolatilityLevel.UNKNOWN: 0,
    }[candidate.volatility_level]
    event = {
        WeightedEventRiskLevel.BLOCKED: 0,
        WeightedEventRiskLevel.ELEVATED: 1,
        WeightedEventRiskLevel.NONE: 2,
        WeightedEventRiskLevel.UNKNOWN: 0,
    }[candidate.event_risk]
    return quality + liquidity + volatility + event


def _exposure_score_from_condition(condition: WeightedMarketCondition) -> int:
    return _exposure_score(
        WeightedConditionCandidate(
            trend_direction=WeightedTrendDirection(condition.trend_direction),
            volatility_level=WeightedVolatilityLevel(condition.volatility_level),
            range_condition=WeightedRangeCondition(condition.range_condition),
            liquidity_level=WeightedLiquidityLevel(condition.liquidity_level),
            session_phase=WeightedSessionPhase(condition.session_phase),
            event_risk=WeightedEventRiskLevel(condition.event_risk),
            market_quality=WeightedMarketQuality(condition.market_quality),
            confidence=condition.confidence,
            condition_inputs={},
            reason_codes=(),
        )
    )


def _condition_key_from_condition(condition: WeightedMarketCondition | None) -> str | None:
    if condition is None:
        return None
    return "|".join(
        (
            str(condition.trend_direction),
            str(condition.volatility_level),
            str(condition.liquidity_level),
            str(condition.session_phase),
            str(condition.event_risk),
            str(condition.market_quality),
        )
    )


def _with_reason(candidate: WeightedConditionCandidate, reason_code: str) -> WeightedConditionCandidate:
    return WeightedConditionCandidate(
        trend_direction=candidate.trend_direction,
        volatility_level=candidate.volatility_level,
        range_condition=candidate.range_condition,
        liquidity_level=candidate.liquidity_level,
        session_phase=candidate.session_phase,
        event_risk=candidate.event_risk,
        market_quality=candidate.market_quality,
        confidence=candidate.confidence,
        condition_inputs=candidate.condition_inputs,
        reason_codes=tuple(dict.fromkeys(candidate.reason_codes + (reason_code,))),
    )


def _candidate_pending_key(candidate: WeightedConditionCandidate, previous_condition: WeightedMarketCondition | None) -> str | None:
    if previous_condition and previous_condition.pending_condition_key:
        return previous_condition.pending_condition_key
    return str(candidate.condition_inputs.get("pending_candidate_key") or "")
