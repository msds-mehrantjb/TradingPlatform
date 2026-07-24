"""Backend-authoritative Regime classifier."""

from __future__ import annotations

from datetime import time
from statistics import mean

from backend.app.algorithms.regime.contracts import RegimeAxes, RegimeCandle, RegimeClassification, RegimeMarketSnapshot
from backend.app.algorithms.regime.exchange_calendar import exchange_session, parse_exchange_timestamp, regime_session_axis
from backend.app.algorithms.regime.indicators import (
    atr,
    directional_movement,
    efficiency_ratio,
    ema,
    realized_volatility,
    relative_volume,
    vwap,
)

VOLATILITY_PERCENTILE_POLICY = {
    "version": "regime_intraday_volatility_percentiles_v1",
    "basis": "time_of_day_normalized_percentiles",
    "extreme": {"atrPercentileGte": 0.97, "realizedVolatilityPercentileGte": 0.97},
    "expanded": {"atrPercentileGte": 0.75, "realizedVolatilityPercentileGte": 0.75},
    "compressed": {"atrPercentileLte": 0.25, "realizedVolatilityPercentileLte": 0.35},
    "supplementalRatios": {
        "currentRangeVsExpectedExtremeGte": 3.0,
        "currentRangeVsExpectedExpandedGte": 1.5,
        "currentRangeVsExpectedCompressedLte": 0.75,
    },
    "combinationRule": (
        "ATR percentile and realized-volatility percentile are complementary; clean high/low volatility requires "
        "agreement, while current range-vs-expected can confirm a transition during disagreement."
    ),
    "calibrationRequirement": "Thresholds must be calibrated through historical regime occupancy and out-of-sample results.",
}

LIQUIDITY_POLICY = {
    "version": "regime_liquidity_fail_closed_v1",
    "unitConvention": {
        "spreadPercent": "decimal_ratio",
        "spreadBps": "basis_points",
        "atrPercent": "decimal_ratio",
        "riskPercent": "decimal_ratio",
        "positionPercent": "decimal_ratio",
        "participationRate": "decimal_ratio",
    },
    "maximumQuoteAgeMs": 15000,
    "acceptableSpreadBpsLte": 12.0,
    "maximumSpreadBps": 30.0,
    "minimumRelativeOneMinuteVolume": 0.45,
    "acceptableRelativeOneMinuteVolume": 0.75,
    "maximumParticipationRate": 0.10,
}

INDICATOR_WARMUP_REQUIREMENTS = {
    "ema20": 20,
    "ema20Slope": 26,
    "ema50": 50,
    "ema50Slope": 56,
    "vwap": 1,
    "vwapSlope": 6,
    "atr": 15,
    "adx": 15,
    "directionalMovementSpread": 15,
    "efficiencyRatio": 21,
    "realizedVolatility": 21,
    "marketStructure": 4,
    "breakOfStructure": 6,
    "openingRange": 30,
}


def classify_market_regime(snapshot: RegimeMarketSnapshot) -> RegimeClassification:
    candles = snapshot.candles
    closes = [c.close for c in candles]
    latest = snapshot.latest
    computed_vwap = vwap(candles)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    previous_ema20 = ema(closes[:-5], 20) if len(closes) > 25 else None
    previous_ema50 = ema(closes[:-5], 50) if len(closes) > 55 else None
    previous_vwap = vwap(candles[:-5]) if len(candles) > 5 else None
    latest_atr = atr(candles)
    atr_percent = (latest_atr / max(latest.close, 0.01)) if latest_atr is not None else None
    latest_rv = realized_volatility(closes)
    movement = directional_movement(candles)
    efficiency = efficiency_ratio(closes)
    rel_volume = relative_volume(candles)
    volatility_evidence = _volatility_evidence(snapshot, latest_atr=latest_atr, atr_percent=atr_percent, rv=latest_rv, rel_volume=rel_volume)
    liquidity_evidence = _liquidity_evidence(snapshot, rel_volume)
    direction_evidence = _direction_evidence(
        snapshot,
        latest_close=latest.close,
        ema20=ema20,
        ema50=ema50,
        previous_ema20=previous_ema20,
        previous_ema50=previous_ema50,
        computed_vwap=computed_vwap,
        previous_vwap=previous_vwap,
    )
    trend_strength_evidence = _trend_strength_evidence(movement, efficiency)
    direction_axis = _direction_axis(direction_evidence["score"], trend_strength_evidence["score"])
    bull_score = max(0, round(direction_evidence["score"] * 5))
    bear_score = max(0, round(-direction_evidence["score"] * 5))
    missing = _missing_indicator_inputs(
        snapshot,
        ema20=ema20,
        ema50=ema50,
        latest_atr=latest_atr,
        latest_rv=latest_rv,
        movement=movement,
        efficiency=efficiency,
        direction_evidence=direction_evidence,
        volatility_evidence=volatility_evidence,
    )
    no_trade = _no_trade_reasons(snapshot, volatility_evidence, liquidity_evidence)
    structure_evidence = _structure_evidence(
        snapshot,
        bull_score,
        bear_score,
        computed_vwap=computed_vwap,
        directional_efficiency=efficiency,
    )
    structure_axis = _structure_axis(structure_evidence)
    liquidity_axis = _liquidity_axis(liquidity_evidence)
    event_evidence = _event_evidence(snapshot)
    event_axis = _event_axis(event_evidence)
    cross_market_evidence = _cross_market_context_evidence(snapshot, direction_axis, structure_axis, event_axis)
    session_evidence = exchange_session(latest.timestamp)
    axes = RegimeAxes(
        direction=direction_axis,
        volatility=_volatility_axis(volatility_evidence),
        structure=structure_axis,
        liquidity=liquidity_axis,
        session=session_evidence.status,
        event_risk=event_axis,
    )
    raw_regime = _composite_regime(axes)
    confidence_evidence = _confidence_evidence(
        axes,
        raw_regime,
        direction_evidence=direction_evidence,
        volatility_evidence=volatility_evidence,
        structure_evidence=structure_evidence,
        liquidity_evidence=liquidity_evidence,
        event_state=event_evidence,
        missing_inputs=missing,
        no_trade=no_trade,
        context_evidence=cross_market_evidence,
    )
    confidence = confidence_evidence["compositeConfidence"]
    features = {
        "vwap": computed_vwap,
        "ema20": ema20,
        "ema50": ema50,
        "ema20Slope": direction_evidence["components"]["ema20Slope"]["value"],
        "ema50Slope": direction_evidence["components"]["ema50Slope"]["value"],
        "vwapSlope": direction_evidence["components"]["vwapSlope"]["value"],
        "vwapLocation": direction_evidence["components"]["vwapLocation"]["value"],
        "atr": latest_atr,
        "atrPercent": atr_percent,
        "atrPercentile": volatility_evidence.get("atrPercentile"),
        "adx": movement.get("adx"),
        "plusDi": movement.get("plusDi"),
        "minusDi": movement.get("minusDi"),
        "directionalMovementSpread": movement.get("directionalMovementSpread"),
        "efficiencyRatio": efficiency,
        "relativeVolume": rel_volume,
        "liquidityStatus": liquidity_evidence["axis"],
        "liquidityBlockNewEntries": liquidity_evidence["blockNewEntries"],
        "spreadBps": liquidity_evidence["spreadBps"],
        "spreadPercent": liquidity_evidence["spreadPercent"],
        "quoteAgeMs": liquidity_evidence["quoteAgeMs"],
        "unitConvention": LIQUIDITY_POLICY["unitConvention"],
        "directionConfidence": confidence_evidence["directionConfidence"],
        "volatilityConfidence": confidence_evidence["volatilityConfidence"],
        "structureConfidence": confidence_evidence["structureConfidence"],
        "liquidityConfidence": confidence_evidence["liquidityConfidence"],
        "eventConfidence": confidence_evidence["eventConfidence"],
        "compositeConfidence": confidence_evidence["compositeConfidence"],
        "safetyBlockConfidence": confidence_evidence["safetyBlockConfidence"],
        "bullScore": bull_score,
        "bearScore": bear_score,
        "directionScore": direction_evidence["score"],
        "trendStrengthScore": trend_strength_evidence["score"],
        "realizedVolatility": latest_rv,
        "realizedVolatilityPercentile": volatility_evidence.get("realizedVolatilityPercentile"),
        "currentRangeVsExpected": volatility_evidence.get("currentRangeVsExpected"),
        "currentVolumeVsExpected": volatility_evidence.get("currentVolumeVsExpected"),
        "volatilityCalibrationStatus": volatility_evidence.get("calibrationStatus"),
        "volatilityThresholdPolicy": VOLATILITY_PERCENTILE_POLICY["version"],
        "crossMarketContextLabel": cross_market_evidence["label"],
        "crossMarketConfidenceAdjustment": cross_market_evidence["confidenceAdjustment"],
        "structureLabel": structure_evidence["label"],
        "structureReferenceLevel": structure_evidence["activeReferenceLevel"],
        "vwapCrossingFrequency": structure_evidence["vwapCrossingFrequency"],
        "sessionTimezone": "America/New_York",
        "sessionDate": session_evidence.session_date,
        "sessionMarketOpenEt": session_evidence.market_open_et,
        "sessionMarketCloseEt": session_evidence.market_close_et,
        "sessionEarlyClose": session_evidence.is_early_close,
        "minutesFromOpen": session_evidence.minutes_from_open,
        "minutesToClose": session_evidence.minutes_to_close,
    }
    evidence = {
        **features,
        "close": latest.close,
        "quoteFreshness": snapshot.context_feeds["quoteFreshness"].get("status"),
        "qqqRelativeStrength": snapshot.context_feeds["qqqRelativeStrength"].get("relativeToPrimaryPercent"),
        "iwmRelativeStrength": snapshot.context_feeds["iwmRelativeStrength"].get("relativeToPrimaryPercent"),
        "marketBreadth": snapshot.context_feeds["marketBreadth"].get("advanceDeclineRatio") or snapshot.context_feeds["marketBreadth"].get("state"),
        "vixState": snapshot.context_feeds["vix"].get("value") or snapshot.context_feeds["vix"].get("state"),
        "vix1dState": snapshot.context_feeds["vix1d"].get("value") or snapshot.context_feeds["vix1d"].get("state"),
        "esFuturesState": snapshot.context_feeds["esFutures"].get("changePercent") or snapshot.context_feeds["esFutures"].get("trend"),
        "scheduledEventState": snapshot.context_feeds["scheduledEconomicEvent"].get("state"),
        "haltLuldState": snapshot.context_feeds["haltLuldCircuitBreaker"],
        "crossMarketContextEvidence": cross_market_evidence,
        "sessionEvidence": {
            "status": session_evidence.status,
            "timestampEt": session_evidence.timestamp_et,
            "sessionDate": session_evidence.session_date,
            "marketOpenEt": session_evidence.market_open_et,
            "marketCloseEt": session_evidence.market_close_et,
            "isEarlyClose": session_evidence.is_early_close,
            "minutesFromOpen": session_evidence.minutes_from_open,
            "minutesToClose": session_evidence.minutes_to_close,
            "reason": session_evidence.reason,
            "calendar": "NYSE/Nasdaq DST-aware calendar",
        },
        "directionEvidence": direction_evidence,
        "trendStrengthEvidence": trend_strength_evidence,
        "volatilityEvidence": volatility_evidence,
        "structureEvidence": structure_evidence,
        "liquidityEvidence": liquidity_evidence,
        "eventEvidence": event_evidence,
        "confidenceEvidence": confidence_evidence,
        "indicatorReadiness": _indicator_readiness(
            snapshot,
            ema20=ema20,
            ema50=ema50,
            latest_atr=latest_atr,
            latest_rv=latest_rv,
            movement=movement,
            efficiency=efficiency,
            direction_evidence=direction_evidence,
            volatility_evidence=volatility_evidence,
            structure_evidence=structure_evidence,
            liquidity_evidence=liquidity_evidence,
            event_evidence=event_evidence,
        ),
        "noTradeReasons": no_trade,
    }
    return RegimeClassification(
        raw_regime=raw_regime,
        axes=axes,
        confidence=confidence,
        features=features,
        evidence=evidence,
        missing_inputs=missing,
        no_trade_reasons=no_trade,
        timestamp=latest.timestamp,
    )


def _direction_axis(direction_score: float, trend_strength: float | None = None) -> str:
    if trend_strength is None:
        edge = direction_score
        if edge >= 4:
            return "strong_up"
        if edge >= 2:
            return "weak_up"
        if edge <= -4:
            return "strong_down"
        if edge <= -2:
            return "weak_down"
        return "neutral"
    direction_threshold = 0.30
    strong_threshold = 0.62
    if direction_score >= direction_threshold and trend_strength >= strong_threshold:
        return "strong_up"
    if direction_score >= direction_threshold:
        return "weak_up"
    if direction_score <= -direction_threshold and trend_strength >= strong_threshold:
        return "strong_down"
    if direction_score <= -direction_threshold:
        return "weak_down"
    return "neutral"


def _direction_evidence(
    snapshot: RegimeMarketSnapshot,
    *,
    latest_close: float,
    ema20: float | None,
    ema50: float | None,
    previous_ema20: float | None,
    previous_ema50: float | None,
    computed_vwap: float,
    previous_vwap: float | None,
) -> dict:
    observations = len(snapshot.candles)
    ema20_slope = ((ema20 - previous_ema20) / max(latest_close, 0.01)) if ema20 is not None and previous_ema20 is not None else None
    ema50_slope = ((ema50 - previous_ema50) / max(latest_close, 0.01)) if ema50 is not None and previous_ema50 is not None else None
    vwap_slope = ((computed_vwap - previous_vwap) / max(latest_close, 0.01)) if previous_vwap is not None else None
    vwap_location = (latest_close - computed_vwap) / max(latest_close, 0.01)
    structure_score = 1.0 if _higher_highs_and_lows(snapshot.candles) else -1.0 if _lower_highs_and_lows(snapshot.candles) else 0.0
    components = {
        "ema20Slope": _ready_component(ema20_slope, _scaled_score(ema20_slope, 0.0015), observations, "ema20Slope"),
        "ema50Slope": _ready_component(ema50_slope, _scaled_score(ema50_slope, 0.0010), observations, "ema50Slope"),
        "vwapSlope": _ready_component(vwap_slope, _scaled_score(vwap_slope, 0.0010), observations, "vwapSlope"),
        "vwapLocation": _ready_component(vwap_location, _scaled_score(vwap_location, 0.004), observations, "vwap", source="computed_or_explicit_vwap"),
        "marketStructure": _ready_component(structure_score, structure_score, observations, "marketStructure"),
    }
    score = (
        components["ema20Slope"]["score"] * 0.25
        + components["ema50Slope"]["score"] * 0.25
        + components["vwapSlope"]["score"] * 0.20
        + components["vwapLocation"]["score"] * 0.15
        + components["marketStructure"]["score"] * 0.15
    )
    return {
        "score": round(_clamp(score, -1.0, 1.0), 4),
        "threshold": 0.30,
        "concept": "direction",
        "components": components,
        "rule": "EMA20/50 slopes, VWAP slope/location, and market structure estimate direction only; cross-market relative strength is separate context evidence.",
    }


def _trend_strength_evidence(movement: dict, efficiency: float | None) -> dict:
    adx = movement.get("adx")
    spread = abs(float(movement.get("directionalMovementSpread") or 0.0)) if movement.get("directionalMovementSpread") is not None else None
    observations = int(movement.get("observations") or 0)
    components = {
        "adx": _ready_component(adx, _scaled_score(adx, 35.0, floor=12.0), observations, "adx"),
        "directionalMovementSpread": _ready_component(spread, _scaled_score(spread, 0.35), observations, "directionalMovementSpread"),
        "efficiencyRatio": _ready_component(efficiency, _scaled_score(efficiency, 0.65), observations, "efficiencyRatio"),
    }
    available = [component["score"] for component in components.values() if component["value"] is not None]
    score = sum(available) / len(available) if available else 0.0
    return {
        "score": round(_clamp(score, 0.0, 1.0), 4),
        "strongThreshold": 0.62,
        "concept": "trend_strength",
        "components": components,
        "rule": "ADX, +DI/-DI spread, and efficiency ratio determine whether a directional move is strong or weak; they do not determine direction.",
    }


def _cross_market_context_evidence(
    snapshot: RegimeMarketSnapshot,
    direction: str,
    structure: str,
    event_risk: str,
) -> dict:
    feeds = snapshot.context_feeds
    qqq = feeds["qqqRelativeStrength"].get("relativeToPrimaryPercent")
    iwm = feeds["iwmRelativeStrength"].get("relativeToPrimaryPercent")
    breadth = feeds["marketBreadth"].get("advanceDeclineRatio")
    breadth_state = str(feeds["marketBreadth"].get("state") or "unknown")
    vix_state = str(feeds["vix"].get("state") or "unknown")
    vix1d_state = str(feeds["vix1d"].get("state") or "unknown")
    es_change = feeds["esFutures"].get("changePercent")
    es_trend = str(feeds["esFutures"].get("trend") or "unknown")
    event_state = str(feeds["scheduledEconomicEvent"].get("state") or "unknown")
    optional_missing = [
        name
        for name, value in {
            "qqqRelativeStrength": qqq,
            "iwmRelativeStrength": iwm,
            "marketBreadth": breadth if breadth is not None else None if breadth_state == "unknown" else breadth_state,
            "vix": feeds["vix"].get("value") if feeds["vix"].get("value") is not None else None if vix_state == "unknown" else vix_state,
            "vix1d": feeds["vix1d"].get("value") if feeds["vix1d"].get("value") is not None else None if vix1d_state == "unknown" else vix1d_state,
            "esFutures": es_change if es_change is not None else None if es_trend == "unknown" else es_trend,
        }.items()
        if value is None
    ]
    direction_sign = 1 if direction in {"strong_up", "weak_up"} else -1 if direction in {"strong_down", "weak_down"} else 0
    qqq_score = _signed_context_score(qqq, scale=0.75)
    iwm_score = _signed_context_score(iwm, scale=0.75)
    breadth_score = _breadth_score(breadth, breadth_state)
    es_score = _es_score(es_change, es_trend)
    risk_off = vix_state in {"elevated", "stress"} or vix1d_state in {"elevated", "stress"}
    market_support_score = mean([qqq_score, iwm_score, breadth_score, es_score])
    reason_codes: list[str] = []
    label = "neutral_context"
    confidence_adjustment = 0.0

    if event_risk in {"blackout", "elevated"} or event_state in {"soon", "elevated", "blackout"}:
        label = "event_driven_volatility"
        confidence_adjustment -= 0.08
        reason_codes.append("regime.context.event_driven_volatility")
    elif direction_sign and risk_off and market_support_score * direction_sign < -0.10:
        label = "risk_off_divergence"
        confidence_adjustment -= 0.10
        reason_codes.append("regime.context.risk_off_divergence")
    elif direction_sign and market_support_score * direction_sign >= 0.35:
        label = "broad_market_trend"
        confidence_adjustment += 0.04
        reason_codes.append("regime.context.broad_market_confirmation")
    elif direction_sign and market_support_score * direction_sign <= -0.10:
        label = "narrow_or_unsupported_trend"
        confidence_adjustment -= 0.07
        reason_codes.append("regime.context.unsupported_spy_trend")
    elif structure in {"breakout", "valid_breakout", "opening_range_breakout", "prior_day_level_breakout", "premarket_level_breakout"} and market_support_score * max(direction_sign, 1) <= 0:
        label = "false_breakout_context"
        confidence_adjustment -= 0.08
        reason_codes.append("regime.context.false_breakout_risk")
    elif qqq is not None and iwm is not None and abs(float(qqq) - float(iwm)) >= 0.75:
        label = "index_rotation"
        confidence_adjustment -= 0.03
        reason_codes.append("regime.context.index_rotation")

    if optional_missing:
        confidence_adjustment -= min(0.08, len(optional_missing) * 0.015)
        reason_codes.append("regime.context.optional_feeds_missing")

    return {
        "role": "secondary_context_confidence_modifier",
        "label": label,
        "confidenceAdjustment": round(_clamp(confidence_adjustment, -0.18, 0.06), 4),
        "marketSupportScore": round(_clamp(market_support_score, -1.0, 1.0), 4),
        "directionSign": direction_sign,
        "riskOff": risk_off,
        "optionalMissingFeeds": optional_missing,
        "reasonCodes": reason_codes,
        "components": {
            "spyVsQqqRelativeStrength": qqq,
            "spyVsIwmRelativeStrength": iwm,
            "marketBreadthScore": round(breadth_score, 4),
            "vixState": vix_state,
            "vix1dState": vix1d_state,
            "esFuturesScore": round(es_score, 4),
            "eventState": event_state,
        },
        "rule": "Cross-market inputs are secondary evidence; they adjust confidence and context but do not overpower SPY price action.",
    }


def _signed_context_score(value, *, scale: float) -> float:
    if value is None:
        return 0.0
    return _clamp(float(value) / max(scale, 0.000001), -1.0, 1.0)


def _breadth_score(ratio, state: str) -> float:
    if ratio is not None:
        return _clamp((float(ratio) - 1.0) / 0.5, -1.0, 1.0)
    if state == "positive":
        return 0.5
    if state == "negative":
        return -0.5
    return 0.0


def _es_score(change, trend: str) -> float:
    if change is not None:
        return _clamp(float(change) / 0.6, -1.0, 1.0)
    if trend == "up":
        return 0.35
    if trend == "down":
        return -0.35
    return 0.0


def _scaled_score(value, scale: float, *, floor: float = 0.0) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if floor:
        number = max(0.0, number - floor)
    return _clamp(number / max(scale, 0.000001), -1.0, 1.0)


def _ready_component(
    value,
    score: float,
    observations_available: int,
    requirement_key: str,
    *,
    source: str = "computed_from_completed_candles",
) -> dict:
    return {
        "value": value,
        "score": score,
        **_readiness(observations_available, requirement_key, value, source=source),
    }


def _readiness(
    observations_available: int,
    requirement_key: str,
    value,
    *,
    source: str = "computed_from_completed_candles",
    observation_label: str = "observationsAvailable",
) -> dict:
    required = INDICATOR_WARMUP_REQUIREMENTS[requirement_key]
    return {
        "dataReady": value is not None and observations_available >= required,
        "requiredObservations": required,
        observation_label: observations_available,
        "source": source,
    }


def _opening_range_ready(snapshot: RegimeMarketSnapshot, reference_levels: list[dict]) -> bool:
    has_range = _level_value(reference_levels, "opening_range_high") is not None and _level_value(reference_levels, "opening_range_low") is not None
    if not has_range:
        return False
    context_levels = snapshot.context_feeds.get("marketStructureLevels") or {}
    if context_levels.get("openingRangeHigh") is not None and context_levels.get("openingRangeLow") is not None:
        return True
    minutes_from_open = exchange_session(snapshot.latest.timestamp).minutes_from_open
    return minutes_from_open is not None and minutes_from_open >= INDICATOR_WARMUP_REQUIREMENTS["openingRange"]


def _missing_indicator_inputs(
    snapshot: RegimeMarketSnapshot,
    *,
    ema20: float | None,
    ema50: float | None,
    latest_atr: float | None,
    latest_rv: float | None,
    movement: dict,
    efficiency: float | None,
    direction_evidence: dict,
    volatility_evidence: dict,
) -> tuple[str, ...]:
    missing: list[str] = []
    checks = {
        "ema20": ema20,
        "ema50": ema50,
        "atr": latest_atr,
        "realizedVolatility": latest_rv,
        "adx": movement.get("adx"),
        "plusDi": movement.get("plusDi"),
        "minusDi": movement.get("minusDi"),
        "directionalMovementSpread": movement.get("directionalMovementSpread"),
        "efficiencyRatio": efficiency,
        "vwapSlope": direction_evidence["components"]["vwapSlope"]["value"],
    }
    for name, value in checks.items():
        if value is None:
            missing.append(name)
    return tuple(dict.fromkeys(missing))


def _indicator_readiness(
    snapshot: RegimeMarketSnapshot,
    *,
    ema20: float | None,
    ema50: float | None,
    latest_atr: float | None,
    latest_rv: float | None,
    movement: dict,
    efficiency: float | None,
    direction_evidence: dict,
    volatility_evidence: dict,
    structure_evidence: dict,
    liquidity_evidence: dict,
    event_evidence: dict,
) -> dict:
    observations = len(snapshot.candles)
    movement_observations = int(movement.get("observations") or observations)
    readiness = {
        "ema20": _readiness(observations, "ema20", ema20),
        "ema20Slope": direction_evidence["components"]["ema20Slope"],
        "ema50": _readiness(observations, "ema50", ema50),
        "ema50Slope": direction_evidence["components"]["ema50Slope"],
        "vwap": _readiness(observations, "vwap", direction_evidence["components"]["vwapLocation"]["value"], source="computed_or_explicit_vwap"),
        "vwapSlope": direction_evidence["components"]["vwapSlope"],
        "atr": _readiness(observations, "atr", latest_atr),
        "adx": _readiness(movement_observations, "adx", movement.get("adx")),
        "plusDi": _readiness(movement_observations, "directionalMovementSpread", movement.get("plusDi")),
        "minusDi": _readiness(movement_observations, "directionalMovementSpread", movement.get("minusDi")),
        "directionalMovementSpread": _readiness(movement_observations, "directionalMovementSpread", movement.get("directionalMovementSpread")),
        "efficiencyRatio": _readiness(observations, "efficiencyRatio", efficiency),
        "realizedVolatility": _readiness(observations, "realizedVolatility", latest_rv),
        "volatilityPercentiles": {
            "dataReady": bool(volatility_evidence.get("percentileDataReady")),
            "calibrationStatus": volatility_evidence.get("calibrationStatus"),
            "sampleSize": volatility_evidence.get("sampleSize"),
            "source": volatility_evidence.get("source"),
        },
        "structure": {
            "dataReady": bool(structure_evidence.get("dataReady")),
            "componentReadiness": structure_evidence.get("componentReadiness"),
        },
        "liquidity": {
            "dataReady": not bool(liquidity_evidence.get("missingCriticalFields")) and liquidity_evidence.get("status") == "fresh",
            "blockNewEntries": liquidity_evidence.get("blockNewEntries"),
            "missingCriticalFields": liquidity_evidence.get("missingCriticalFields"),
        },
        "eventRisk": {
            "dataReady": event_evidence.get("scheduledState") != "unknown",
            "newEntriesBlocked": event_evidence.get("newEntriesBlocked"),
        },
    }
    return {
        "overallDataReady": all(
            bool(readiness[name].get("dataReady"))
            for name in ("ema20", "ema50", "atr", "adx", "directionalMovementSpread", "efficiencyRatio", "realizedVolatility", "liquidity", "eventRisk")
        ),
        "observationsAvailable": observations,
        "requirements": INDICATOR_WARMUP_REQUIREMENTS,
        "indicators": readiness,
    }


def _volatility_evidence(
    snapshot: RegimeMarketSnapshot,
    *,
    latest_atr: float | None,
    atr_percent: float | None,
    rv: float | None,
    rel_volume: float,
) -> dict:
    baseline = snapshot.context_feeds.get("intradayVolatilityBaseline") or {}
    same_minute = _same_minute_volatility_baseline(snapshot, rv=rv, rel_volume=rel_volume)
    atr_percentile = _first_present(baseline.get("atrPercentile"), same_minute.get("atrPercentile"))
    rv_percentile = _first_present(baseline.get("realizedVolatilityPercentile"), same_minute.get("realizedVolatilityPercentile"))
    current_range = snapshot.latest.high - snapshot.latest.low
    expected_range = _first_present(baseline.get("expectedRange"), same_minute.get("expectedRange"))
    expected_volume = _first_present(baseline.get("expectedVolume"), same_minute.get("expectedVolume"))
    range_vs_expected = _first_present(
        baseline.get("currentRangeVsExpected"),
        (current_range / expected_range) if expected_range and expected_range > 0 else None,
    )
    volume_vs_expected = _first_present(
        baseline.get("currentVolumeVsExpected"),
        (snapshot.latest.volume / expected_volume) if expected_volume and expected_volume > 0 else None,
    )
    sample_size = int(max(float(baseline.get("sampleSize") or 0), float(same_minute.get("sampleSize") or 0)))
    status = str(baseline.get("calibrationStatus") or same_minute.get("calibrationStatus") or "").lower()
    if not status:
        status = "ready" if atr_percentile is not None or rv_percentile is not None else "missing_or_insufficient_same_minute_history"
    evidence = {
        "policy": VOLATILITY_PERCENTILE_POLICY,
        "calibrationStatus": status,
        "minuteOfSession": _minute_of_session(snapshot.latest.timestamp),
        "atr": latest_atr,
        "atrDataReady": latest_atr is not None and len(snapshot.candles) >= INDICATOR_WARMUP_REQUIREMENTS["atr"],
        "atrPercent": atr_percent,
        "atrPercentile": _clamp01(atr_percentile),
        "realizedVolatility": rv,
        "realizedVolatilityDataReady": rv is not None and len(snapshot.candles) >= INDICATOR_WARMUP_REQUIREMENTS["realizedVolatility"],
        "realizedVolatilityPercentile": _clamp01(rv_percentile),
        "percentileDataReady": atr_percentile is not None and rv_percentile is not None and status == "ready",
        "currentRange": current_range,
        "expectedRange": expected_range,
        "currentRangeVsExpected": range_vs_expected,
        "currentVolume": snapshot.latest.volume,
        "expectedVolume": expected_volume,
        "currentVolumeVsExpected": volume_vs_expected,
        "sampleSize": sample_size,
        "dataReady": bool(
            latest_atr is not None
            and rv is not None
            and len(snapshot.candles) >= INDICATOR_WARMUP_REQUIREMENTS["realizedVolatility"]
            and (atr_percentile is not None or rv_percentile is not None or range_vs_expected is not None)
        ),
        "source": "context_feed" if baseline.get("calibrationStatus") not in {None, "missing"} else same_minute.get("source", "unavailable"),
    }
    decision = _volatility_decision(evidence)
    return {
        **evidence,
        "axis": decision["axis"],
        "componentStates": decision["componentStates"],
        "agreement": decision["agreement"],
        "disagreementReasonCodes": decision["disagreementReasonCodes"],
    }


def _volatility_axis(volatility_evidence: dict | float | None, rv: float | None = None) -> str:
    if not isinstance(volatility_evidence, dict):
        volatility_evidence = {
            "atrPercentile": _clamp01(volatility_evidence),
            "realizedVolatilityPercentile": _clamp01(rv),
            "currentRangeVsExpected": None,
            "calibrationStatus": "legacy_percentile_args",
        }
    atr_percentile = volatility_evidence.get("atrPercentile")
    rv_percentile = volatility_evidence.get("realizedVolatilityPercentile")
    range_vs_expected = volatility_evidence.get("currentRangeVsExpected")
    if atr_percentile is None and rv_percentile is None and range_vs_expected is None:
        return "normal"
    if volatility_evidence.get("axis"):
        return str(volatility_evidence["axis"])
    return str(_volatility_decision(volatility_evidence)["axis"])


def _structure_axis(
    snapshot_or_evidence: RegimeMarketSnapshot | dict,
    bull_score: int | None = None,
    bear_score: int | None = None,
) -> str:
    if isinstance(snapshot_or_evidence, dict):
        return str(snapshot_or_evidence["axis"])
    return str(_structure_evidence(snapshot_or_evidence, bull_score or 0, bear_score or 0)["axis"])


def _structure_evidence(
    snapshot: RegimeMarketSnapshot,
    bull_score: int,
    bear_score: int,
    *,
    computed_vwap: float | None = None,
    directional_efficiency: float | None = None,
) -> dict:
    candles = snapshot.candles
    observations = len(candles)
    latest = snapshot.latest
    prior = candles[:-1]
    recent = prior[-8:]
    recent_high = max((c.high for c in recent), default=None)
    recent_low = min((c.low for c in recent), default=None)
    reference_levels = _structure_reference_levels(snapshot)
    active_reference = _active_reference_level(latest, reference_levels)
    higher_highs_lows = _higher_highs_and_lows(candles)
    lower_highs_lows = _lower_highs_and_lows(candles)
    break_of_structure = _break_of_structure(latest, recent_high, recent_low)
    change_of_character = _change_of_character(candles, latest, recent_high, recent_low)
    rejection = _rejection_candle(latest)
    failed_acceptance = _failed_acceptance(latest, active_reference)
    confirmed_break = _confirmed_break(latest, active_reference)
    retest_outcome = _retest_outcome(candles, active_reference)
    vwap_crosses = _vwap_crossing_frequency(candles, computed_vwap)
    efficiency = directional_efficiency if directional_efficiency is not None else efficiency_ratio([c.close for c in candles])

    if active_reference and failed_acceptance and rejection["isRejection"] and change_of_character:
        axis = "reversal"
        label = "reference_level_reversal"
    elif active_reference and failed_acceptance and rejection["isRejection"]:
        axis = "liquidity_sweep"
        label = "liquidity_sweep_rejection"
    elif active_reference and failed_acceptance:
        axis = "failed_breakout"
        label = "failed_acceptance_at_reference_level"
    elif active_reference and confirmed_break:
        axis = _breakout_axis_for_level(active_reference["type"])
        label = "confirmed_reference_level_breakout"
    elif break_of_structure["direction"] != "none" and not confirmed_break:
        axis = "breakout" if retest_outcome != "failed" else "failed_breakout"
        label = "recent_structure_break"
    elif change_of_character and active_reference and rejection["isRejection"] and failed_acceptance:
        axis = "reversal"
        label = "change_of_character_reversal"
    elif _is_choppy_structure(vwap_crosses, efficiency, higher_highs_lows, lower_highs_lows):
        axis = "mixed"
        label = "choppy_mixed_structure"
    elif higher_highs_lows or lower_highs_lows or abs(bull_score - bear_score) >= 2:
        axis = "trend"
        label = "ordered_price_structure"
    else:
        axis = "range"
        label = "range_bound_structure"

    return {
        "axis": axis,
        "label": label,
        "higherHighsHigherLows": higher_highs_lows,
        "lowerHighsLowerLows": lower_highs_lows,
        "breakOfStructure": break_of_structure,
        "changeOfCharacter": change_of_character,
        "openingRange": {
            "high": _level_value(reference_levels, "opening_range_high"),
            "low": _level_value(reference_levels, "opening_range_low"),
            "dataReady": _opening_range_ready(snapshot, reference_levels),
            "requiredMinutesFromOpen": INDICATOR_WARMUP_REQUIREMENTS["openingRange"],
            "minutesFromOpen": exchange_session(snapshot.latest.timestamp).minutes_from_open,
        },
        "priorDay": {
            "high": _level_value(reference_levels, "prior_day_high"),
            "low": _level_value(reference_levels, "prior_day_low"),
        },
        "premarket": {
            "high": _level_value(reference_levels, "premarket_high"),
            "low": _level_value(reference_levels, "premarket_low"),
        },
        "referenceLevels": reference_levels,
        "activeReferenceLevel": active_reference,
        "confirmedBreak": confirmed_break,
        "retestOutcome": retest_outcome,
        "rejectionCandle": rejection,
        "failedAcceptance": failed_acceptance,
        "vwapCrossingFrequency": vwap_crosses,
        "dataReady": observations >= INDICATOR_WARMUP_REQUIREMENTS["marketStructure"],
        "componentReadiness": {
            "orderedStructure": _readiness(observations, "marketStructure", higher_highs_lows or lower_highs_lows),
            "breakOfStructure": _readiness(observations, "breakOfStructure", break_of_structure["direction"] != "none"),
            "changeOfCharacter": _readiness(observations, "breakOfStructure", change_of_character),
            "vwapCrossingFrequency": _readiness(observations, "vwapSlope", vwap_crosses),
            "openingRange": _readiness(
                exchange_session(snapshot.latest.timestamp).minutes_from_open or 0,
                "openingRange",
                True if _opening_range_ready(snapshot, reference_levels) else None,
                observation_label="minutesFromOpen",
            ),
        },
        "directionalEfficiency": efficiency,
        "reasonCodes": _structure_reason_codes(axis, active_reference, failed_acceptance, rejection, change_of_character),
        "rule": (
            "Structure is point-in-time evidence over swing order, reference levels, acceptance, retests, VWAP crossings, "
            "and directional efficiency. A rejection candle alone cannot establish a reversal; it must occur at a known "
            "reference level with failed acceptance."
        ),
    }


def _structure_reference_levels(snapshot: RegimeMarketSnapshot) -> list[dict]:
    context_levels = snapshot.context_feeds.get("marketStructureLevels") or {}
    levels: list[dict] = []
    levels.extend(_context_reference_levels(context_levels))
    levels.extend(_computed_session_reference_levels(snapshot))
    latest = snapshot.latest
    recent = snapshot.candles[:-1][-8:]
    if recent:
        levels.append({"type": "recent_swing_high", "side": "high", "price": max(c.high for c in recent), "source": "recent_candles"})
        levels.append({"type": "recent_swing_low", "side": "low", "price": min(c.low for c in recent), "source": "recent_candles"})
    deduped: dict[tuple[str, str], dict] = {}
    for level in levels:
        price = level.get("price")
        if price is None:
            continue
        key = (str(level["type"]), str(level["side"]))
        existing = deduped.get(key)
        if existing is None or abs(float(price) - latest.close) < abs(float(existing["price"]) - latest.close):
            deduped[key] = level
    return list(deduped.values())


def _context_reference_levels(context_levels: dict) -> list[dict]:
    mapping = {
        "priorDayHigh": ("prior_day_high", "high"),
        "priorDayLow": ("prior_day_low", "low"),
        "premarketHigh": ("premarket_high", "high"),
        "premarketLow": ("premarket_low", "low"),
        "openingRangeHigh": ("opening_range_high", "high"),
        "openingRangeLow": ("opening_range_low", "low"),
    }
    source = str(context_levels.get("source") or "context_feed")
    return [
        {"type": level_type, "side": side, "price": float(context_levels[key]), "source": source}
        for key, (level_type, side) in mapping.items()
        if context_levels.get(key) is not None
    ]


def _computed_session_reference_levels(snapshot: RegimeMarketSnapshot) -> list[dict]:
    latest_session = exchange_session(snapshot.latest.timestamp)
    session_date = latest_session.session_date
    levels: list[dict] = []
    if session_date is None:
        return levels
    regular_candles = [
        candle
        for candle in snapshot.one_minute_candles
        if exchange_session(candle.timestamp).session_date == session_date
        and exchange_session(candle.timestamp).minutes_from_open is not None
    ]
    opening_candles = [
        candle
        for candle in regular_candles
        if (exchange_session(candle.timestamp).minutes_from_open or 0) < 30 and candle.timestamp < snapshot.latest.timestamp
    ]
    if opening_candles and latest_session.minutes_from_open is not None and latest_session.minutes_from_open >= INDICATOR_WARMUP_REQUIREMENTS["openingRange"]:
        levels.append({"type": "opening_range_high", "side": "high", "price": max(c.high for c in opening_candles), "source": "computed_one_minute_candles"})
        levels.append({"type": "opening_range_low", "side": "low", "price": min(c.low for c in opening_candles), "source": "computed_one_minute_candles"})

    premarket = []
    prior_day_groups: dict[str, list[RegimeCandle]] = {}
    for candle in snapshot.one_minute_candles:
        parsed = parse_exchange_timestamp(candle.timestamp)
        if parsed is None:
            continue
        if parsed.date().isoformat() == session_date and time(4, 0) <= parsed.time() < time(9, 30):
            premarket.append(candle)
        elif parsed.date().isoformat() < session_date:
            prior_day_groups.setdefault(parsed.date().isoformat(), []).append(candle)
    if premarket:
        levels.append({"type": "premarket_high", "side": "high", "price": max(c.high for c in premarket), "source": "computed_premarket_candles"})
        levels.append({"type": "premarket_low", "side": "low", "price": min(c.low for c in premarket), "source": "computed_premarket_candles"})
    if prior_day_groups:
        previous_day = max(prior_day_groups)
        prior_candles = prior_day_groups[previous_day]
        levels.append({"type": "prior_day_high", "side": "high", "price": max(c.high for c in prior_candles), "source": "computed_prior_day_candles"})
        levels.append({"type": "prior_day_low", "side": "low", "price": min(c.low for c in prior_candles), "source": "computed_prior_day_candles"})
    return levels


def _active_reference_level(latest: RegimeCandle, levels: list[dict]) -> dict | None:
    if not levels:
        return None
    tolerance = max(latest.close * 0.0015, 0.02)
    candidates = [
        {**level, "distance": abs(float(level["price"]) - latest.close)}
        for level in levels
        if abs(float(level["price"]) - latest.close) <= tolerance
        or (level["side"] == "high" and latest.high >= float(level["price"]))
        or (level["side"] == "low" and latest.low <= float(level["price"]))
    ]
    if not candidates:
        return None
    priority = {
        "prior_day_high": 0,
        "prior_day_low": 0,
        "premarket_high": 1,
        "premarket_low": 1,
        "opening_range_high": 2,
        "opening_range_low": 2,
        "recent_swing_high": 3,
        "recent_swing_low": 3,
    }
    return min(candidates, key=lambda level: (priority.get(str(level["type"]), 9), float(level["distance"])))


def _confirmed_break(latest: RegimeCandle, active_reference: dict | None) -> bool:
    if active_reference is None:
        return False
    price = float(active_reference["price"])
    acceptance_buffer = max(latest.close * 0.0002, 0.01)
    if active_reference["side"] == "high":
        return latest.close > price + acceptance_buffer
    return latest.close < price - acceptance_buffer


def _failed_acceptance(latest: RegimeCandle, active_reference: dict | None) -> bool:
    if active_reference is None:
        return False
    price = float(active_reference["price"])
    if active_reference["side"] == "high":
        return latest.high > price and latest.close <= price
    return latest.low < price and latest.close >= price


def _retest_outcome(candles: tuple[RegimeCandle, ...], active_reference: dict | None) -> str:
    if active_reference is None or len(candles) < 4:
        return "not_tested"
    price = float(active_reference["price"])
    recent = candles[-4:-1]
    tolerance = max(candles[-1].close * 0.0015, 0.02)
    touched = any(abs(candle.low - price) <= tolerance or abs(candle.high - price) <= tolerance for candle in recent)
    if not touched:
        return "not_tested"
    if active_reference["side"] == "high":
        return "held" if candles[-1].close >= price else "failed"
    return "held" if candles[-1].close <= price else "failed"


def _breakout_axis_for_level(level_type: str) -> str:
    if level_type.startswith("opening_range"):
        return "opening_range_breakout"
    if level_type.startswith("prior_day"):
        return "prior_day_level_breakout"
    if level_type.startswith("premarket"):
        return "premarket_level_breakout"
    return "valid_breakout"


def _higher_highs_and_lows(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].high > candles[-2].high > candles[-3].high and candles[-1].low > candles[-2].low > candles[-3].low


def _lower_highs_and_lows(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].high < candles[-2].high < candles[-3].high and candles[-1].low < candles[-2].low < candles[-3].low


def _break_of_structure(latest: RegimeCandle, recent_high: float | None, recent_low: float | None) -> dict:
    if recent_high is not None and latest.close > recent_high:
        return {"direction": "up", "level": recent_high}
    if recent_low is not None and latest.close < recent_low:
        return {"direction": "down", "level": recent_low}
    return {"direction": "none", "level": None}


def _change_of_character(candles: tuple[RegimeCandle, ...], latest: RegimeCandle, recent_high: float | None, recent_low: float | None) -> bool:
    previous = candles[-7:-1]
    had_down_structure = _lower_highs_and_lows(previous) if len(previous) >= 4 else False
    had_up_structure = _higher_highs_and_lows(previous) if len(previous) >= 4 else False
    return bool((had_down_structure and recent_high is not None and latest.close > recent_high) or (had_up_structure and recent_low is not None and latest.close < recent_low))


def _rejection_candle(candle: RegimeCandle) -> dict:
    candle_range = max(candle.high - candle.low, 0.000001)
    body_high = max(candle.open, candle.close)
    body_low = min(candle.open, candle.close)
    upper_wick = candle.high - body_high
    lower_wick = body_low - candle.low
    upper_rejection = upper_wick / candle_range >= 0.45 and candle.close < candle.open
    lower_rejection = lower_wick / candle_range >= 0.45 and candle.close > candle.open
    return {
        "isRejection": bool(upper_rejection or lower_rejection),
        "side": "upper" if upper_rejection else "lower" if lower_rejection else "none",
        "upperWickRatio": round(upper_wick / candle_range, 4),
        "lowerWickRatio": round(lower_wick / candle_range, 4),
        "bodyRatio": round(abs(candle.close - candle.open) / candle_range, 4),
    }


def _vwap_crossing_frequency(candles: tuple[RegimeCandle, ...], computed_vwap: float | None) -> int:
    recent = candles[-16:]
    if len(recent) < 3:
        return 0
    states = []
    for candle in recent:
        level = candle.vwap if candle.vwap is not None else computed_vwap
        if level is None:
            continue
        states.append(1 if candle.close >= level else -1)
    return sum(1 for previous, current in zip(states, states[1:]) if previous != current)


def _is_choppy_structure(vwap_crosses: int, efficiency: float | None, higher_highs_lows: bool, lower_highs_lows: bool) -> bool:
    low_efficiency = efficiency is not None and efficiency < 0.28
    conflicting_order = not higher_highs_lows and not lower_highs_lows
    return conflicting_order and (vwap_crosses >= 3 or low_efficiency)


def _level_value(levels: list[dict], level_type: str) -> float | None:
    for level in levels:
        if level["type"] == level_type:
            return float(level["price"])
    return None


def _structure_reason_codes(axis: str, active_reference: dict | None, failed_acceptance: bool, rejection: dict, change_of_character: bool) -> list[str]:
    reasons = [f"regime.structure.{axis}"]
    if active_reference:
        reasons.append(f"regime.structure.reference.{active_reference['type']}")
    if failed_acceptance:
        reasons.append("regime.structure.failed_acceptance")
    if rejection.get("isRejection"):
        reasons.append("regime.structure.rejection_candle")
    if change_of_character:
        reasons.append("regime.structure.change_of_character")
    return reasons


def _liquidity_axis(snapshot_or_evidence: RegimeMarketSnapshot | dict, rel_volume: float | None = None) -> str:
    if isinstance(snapshot_or_evidence, dict):
        return str(snapshot_or_evidence["axis"])
    return str(_liquidity_evidence(snapshot_or_evidence, 1.0 if rel_volume is None else rel_volume)["axis"])


def _liquidity_evidence(snapshot: RegimeMarketSnapshot, rel_volume: float) -> dict:
    quote = snapshot.context_feeds["quoteFreshness"]
    age_ms = quote.get("ageMs")
    max_age_ms = quote.get("maxAgeMs") or LIQUIDITY_POLICY["maximumQuoteAgeMs"]
    bid = quote.get("bid")
    ask = quote.get("ask")
    spread_percent = quote.get("spreadPercent")
    spread_bps = quote.get("spreadBps")
    trade_count = quote.get("tradeCount")
    trade_rate = quote.get("tradeRatePerSecond")
    expected_fill_quantity = quote.get("expectedFillQuantity")
    explicit_participation = quote.get("participationRate")
    participation_rate = explicit_participation
    if participation_rate is None and expected_fill_quantity is not None and snapshot.latest.volume > 0:
        participation_rate = float(expected_fill_quantity) / max(snapshot.latest.volume, 1.0)
    top_depth = quote.get("topOfBookDepth")
    missing_critical = [
        name
        for name, value in {
            "bid": bid,
            "ask": ask,
            "quoteAgeMs": age_ms,
            "spreadBps": spread_bps,
        }.items()
        if value is None
    ]
    reason_codes: list[str] = []
    if quote.get("status") == "unknown":
        reason_codes.append("regime.liquidity.quote_status_unknown")
    if missing_critical:
        reason_codes.append("regime.liquidity.missing_critical_quote_fields")
    if age_ms is not None and float(age_ms) > float(max_age_ms):
        reason_codes.append("regime.liquidity.quote_age_exceeded")
    if quote.get("status") == "stale":
        reason_codes.append("regime.liquidity.quote_stale")
    critical_quote_block = bool(
        quote.get("status") in {"unknown", "stale"}
        or missing_critical
        or (age_ms is not None and float(age_ms) > float(max_age_ms))
    )
    if critical_quote_block:
        axis = "unknown"
        block_new_entries = True
    elif spread_bps is not None and float(spread_bps) > LIQUIDITY_POLICY["maximumSpreadBps"]:
        axis = "poor"
        block_new_entries = True
        reason_codes.append("regime.liquidity.excessive_spread")
    elif rel_volume < LIQUIDITY_POLICY["minimumRelativeOneMinuteVolume"]:
        axis = "poor"
        block_new_entries = True
        reason_codes.append("regime.liquidity.insufficient_relative_volume")
    elif participation_rate is not None and float(participation_rate) > LIQUIDITY_POLICY["maximumParticipationRate"]:
        axis = "poor"
        block_new_entries = True
        reason_codes.append("regime.liquidity.participation_rate_too_high")
    elif top_depth is not None and expected_fill_quantity is not None and float(top_depth) < float(expected_fill_quantity):
        axis = "poor"
        block_new_entries = True
        reason_codes.append("regime.liquidity.insufficient_top_of_book_depth")
    elif rel_volume < LIQUIDITY_POLICY["acceptableRelativeOneMinuteVolume"] or (
        spread_bps is not None and float(spread_bps) > LIQUIDITY_POLICY["acceptableSpreadBpsLte"]
    ):
        axis = "acceptable"
        block_new_entries = False
    else:
        axis = "good"
        block_new_entries = False
    if trade_count is None and trade_rate is None:
        reason_codes.append("regime.liquidity.trade_count_or_rate_missing")
    if expected_fill_quantity is None:
        reason_codes.append("regime.liquidity.expected_fill_quantity_missing")
    return {
        "axis": axis,
        "blockNewEntries": block_new_entries,
        "policy": LIQUIDITY_POLICY,
        "status": quote.get("status"),
        "bid": bid,
        "ask": ask,
        "spreadPercent": spread_percent,
        "spreadBps": spread_bps,
        "quoteAgeMs": age_ms,
        "maximumQuoteAgeMs": max_age_ms,
        "relativeOneMinuteVolume": rel_volume,
        "tradeCount": trade_count,
        "tradeRatePerSecond": trade_rate,
        "expectedFillQuantity": expected_fill_quantity,
        "participationRate": participation_rate,
        "topOfBookDepth": top_depth,
        "missingCriticalFields": missing_critical,
        "reasonCodes": reason_codes,
        "unitConvention": LIQUIDITY_POLICY["unitConvention"],
        "rule": "Liquidity fails closed: missing critical quote fields or stale quote age produce unknown liquidity and block new entries.",
    }


def _session_axis(timestamp: str) -> str:
    return regime_session_axis(timestamp)


def _event_axis(snapshot_or_evidence: RegimeMarketSnapshot | dict) -> str:
    evidence = snapshot_or_evidence if isinstance(snapshot_or_evidence, dict) else _event_evidence(snapshot_or_evidence)
    if evidence.get("newEntriesBlocked"):
        return "blackout"
    event_state = evidence.get("scheduledState")
    if event_state == "blackout":
        return "blackout"
    if event_state in {"elevated", "soon"}:
        return "elevated"
    return "none"


def _event_evidence(snapshot: RegimeMarketSnapshot) -> dict:
    scheduled = snapshot.context_feeds["scheduledEconomicEvent"]
    halt = snapshot.context_feeds["haltLuldCircuitBreaker"]
    event_type = scheduled.get("eventType")
    scheduled_state = scheduled.get("state")
    luld_blocked = bool(halt.get("newEntriesBlocked"))
    reason_codes: list[str] = []
    if event_type in {"cpi", "fomc", "jobs", "fed"} and scheduled_state in {"soon", "elevated", "blackout"}:
        reason_codes.append(f"regime.event.scheduled_macro.{event_type}")
    if luld_blocked:
        reason_codes.append("regime.event.unscheduled_halt_luld")
    if scheduled_state == "unknown":
        reason_codes.append("regime.event.scheduled_state_unknown")
    return {
        "scheduledState": scheduled_state,
        "minutesUntilEvent": scheduled.get("minutesUntilEvent"),
        "eventType": event_type,
        "source": scheduled.get("source"),
        "isScheduledMacroEvent": event_type in {"cpi", "fomc", "jobs", "fed"},
        "haltState": halt.get("haltState"),
        "circuitBreakerState": halt.get("circuitBreakerState"),
        "newEntriesBlocked": luld_blocked,
        "reasonCodes": reason_codes,
        "rule": "Event risk uses scheduled CPI/FOMC/jobs/Fed event state plus unscheduled halt/LULD state.",
    }


def _composite_regime(axes: RegimeAxes) -> str:
    if axes.volatility == "extreme":
        return "extreme_volatility_no_trade"
    if axes.event_risk in {"blackout", "elevated"}:
        return "event_risk"
    if axes.liquidity in {"poor", "unknown"}:
        return "liquidity_stress"
    if axes.structure in {"failed_breakout", "liquidity_sweep", "reversal"}:
        return "failed_breakout_reversal"
    if axes.structure == "opening_range_breakout" or (axes.session == "opening" and axes.structure in {"breakout", "valid_breakout"}):
        return "opening_breakout"
    if axes.structure in {"breakout", "valid_breakout", "prior_day_level_breakout", "premarket_level_breakout"} and axes.volatility == "expanded":
        return "intraday_expansion"
    if axes.structure in {"mixed", "choppy_mixed"}:
        return "choppy_mixed"
    if axes.volatility == "expanded" and axes.direction in {"strong_up", "strong_down"}:
        return "high_volatility_trend"
    if axes.volatility == "compressed":
        return "low_volatility_quiet"
    if axes.direction == "strong_up":
        return "strong_uptrend"
    if axes.direction == "weak_up":
        return "weak_uptrend"
    if axes.direction == "strong_down":
        return "strong_downtrend"
    if axes.direction == "weak_down":
        return "weak_downtrend"
    if axes.structure == "range":
        return "range_bound"
    return "choppy_mixed"


def _no_trade_reasons(
    snapshot: RegimeMarketSnapshot,
    volatility_evidence: dict | float | None,
    liquidity_evidence_or_rel_volume: dict | float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    liquidity_evidence = (
        liquidity_evidence_or_rel_volume
        if isinstance(liquidity_evidence_or_rel_volume, dict)
        else _liquidity_evidence(snapshot, float(liquidity_evidence_or_rel_volume))
    )
    if snapshot.context_feeds["quoteFreshness"].get("status") == "stale":
        reasons.append("regime.safety.stale_quote")
    if snapshot.context_feeds["quoteFreshness"].get("status") == "unknown":
        reasons.append("regime.safety.missing_quote_freshness")
    if liquidity_evidence.get("blockNewEntries"):
        reasons.append("regime.safety.liquidity_fail_closed")
    if "regime.liquidity.quote_age_exceeded" in liquidity_evidence.get("reasonCodes", []):
        reasons.append("regime.safety.quote_age_exceeded")
    if liquidity_evidence.get("missingCriticalFields"):
        reasons.append("regime.safety.missing_liquidity_quote_fields")
    if snapshot.context_feeds["scheduledEconomicEvent"].get("state") == "blackout":
        reasons.append("regime.safety.event_blackout")
    if snapshot.context_feeds["scheduledEconomicEvent"].get("state") == "unknown":
        reasons.append("regime.safety.missing_event_state")
    if snapshot.context_feeds["haltLuldCircuitBreaker"].get("newEntriesBlocked"):
        reasons.append("regime.safety.halt_luld_circuit")
    if _volatility_axis(volatility_evidence) == "extreme":
        reasons.append("regime.safety.extreme_volatility")
    if liquidity_evidence.get("axis") == "poor":
        reasons.append("regime.safety.insufficient_liquidity")
    return tuple(dict.fromkeys(reasons))


def _higher_highs(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].high > candles[-2].high > candles[-3].high


def _lower_lows(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].low < candles[-2].low < candles[-3].low


def _confidence(
    bull_score: int,
    bear_score: int,
    missing: tuple[str, ...],
    no_trade: tuple[str, ...],
    *,
    context_evidence: dict | None = None,
) -> float:
    score = 0.55 + min(0.25, abs(bull_score - bear_score) * 0.06)
    score -= min(0.20, len(missing) * 0.03)
    if context_evidence:
        score += float(context_evidence.get("confidenceAdjustment") or 0.0)
    return max(0.0, min(1.0, round(score, 4)))


def _confidence_evidence(
    axes: RegimeAxes,
    raw_regime: str,
    *,
    direction_evidence: dict,
    volatility_evidence: dict,
    structure_evidence: dict,
    liquidity_evidence: dict,
    event_state: str | None,
    missing_inputs: tuple[str, ...],
    no_trade: tuple[str, ...],
    context_evidence: dict | None = None,
) -> dict:
    direction_confidence = _direction_confidence(direction_evidence, missing_inputs, context_evidence)
    volatility_confidence = _volatility_confidence(volatility_evidence)
    structure_confidence = _structure_confidence(structure_evidence)
    liquidity_confidence = _liquidity_confidence(liquidity_evidence)
    event_confidence = _event_confidence(event_state)
    required_axes = _required_confidence_axes(raw_regime, axes)
    axis_values = {
        "direction": direction_confidence,
        "volatility": volatility_confidence,
        "structure": structure_confidence,
        "liquidity": liquidity_confidence,
        "event": event_confidence,
    }
    required_values = [axis_values[name] for name in required_axes]
    composite = min(required_values) if required_values else min(axis_values.values())
    safety_block_confidence = _safety_block_confidence(
        no_trade,
        volatility_evidence=volatility_evidence,
        liquidity_evidence=liquidity_evidence,
        event_state=event_state,
    )
    return {
        "directionConfidence": direction_confidence,
        "volatilityConfidence": volatility_confidence,
        "structureConfidence": structure_confidence,
        "liquidityConfidence": liquidity_confidence,
        "eventConfidence": event_confidence,
        "compositeConfidence": round(composite, 4),
        "classificationConfidence": round(composite, 4),
        "safetyBlockConfidence": safety_block_confidence,
        "requiredAxes": required_axes,
        "rawRegime": raw_regime,
        "rule": "Composite market-regime confidence is the minimum required axis confidence; safety-block certainty is tracked separately.",
    }


def _direction_confidence(direction_evidence: dict, missing_inputs: tuple[str, ...], context_evidence: dict | None) -> float:
    score = abs(float(direction_evidence.get("score") or 0.0))
    components = direction_evidence.get("components") or {}
    available = sum(1 for component in components.values() if component.get("value") is not None)
    total = max(len(components), 1)
    confidence = 0.35 + min(0.45, score * 0.45) + (available / total) * 0.20
    confidence -= min(0.12, len(missing_inputs) * 0.03)
    if context_evidence:
        confidence += float(context_evidence.get("confidenceAdjustment") or 0.0) * 0.5
    return round(_clamp(confidence, 0.05, 1.0), 4)


def _volatility_confidence(volatility_evidence: dict) -> float:
    component_states = volatility_evidence.get("componentStates") or {}
    known = sum(1 for state in component_states.values() if state != "unknown")
    total = max(len(component_states), 1)
    confidence = 0.35 + (known / total) * 0.35
    if volatility_evidence.get("calibrationStatus") == "ready":
        confidence += 0.15
    if volatility_evidence.get("agreement") in {"atr_rv_high_agreement", "atr_rv_compressed_agreement", "range_confirmed_transition"}:
        confidence += 0.12
    if volatility_evidence.get("disagreementReasonCodes"):
        confidence -= 0.12
    if int(volatility_evidence.get("sampleSize") or 0) < 20:
        confidence -= 0.08
    return round(_clamp(confidence, 0.05, 1.0), 4)


def _structure_confidence(structure_evidence: dict) -> float:
    axis = structure_evidence.get("axis")
    confidence = 0.45
    if structure_evidence.get("higherHighsHigherLows") or structure_evidence.get("lowerHighsLowerLows"):
        confidence += 0.15
    if structure_evidence.get("breakOfStructure", {}).get("direction") != "none":
        confidence += 0.12
    if structure_evidence.get("activeReferenceLevel"):
        confidence += 0.15
    if structure_evidence.get("confirmedBreak") or structure_evidence.get("failedAcceptance"):
        confidence += 0.10
    if axis in {"mixed", "range"} and structure_evidence.get("vwapCrossingFrequency", 0) >= 3:
        confidence += 0.12
    if structure_evidence.get("directionalEfficiency") is not None:
        confidence += 0.06
    return round(_clamp(confidence, 0.05, 1.0), 4)


def _liquidity_confidence(liquidity_evidence: dict) -> float:
    if liquidity_evidence.get("missingCriticalFields") or liquidity_evidence.get("axis") == "unknown":
        return 0.25
    confidence = 0.72
    if liquidity_evidence.get("tradeCount") is not None or liquidity_evidence.get("tradeRatePerSecond") is not None:
        confidence += 0.08
    if liquidity_evidence.get("expectedFillQuantity") is not None:
        confidence += 0.08
    if liquidity_evidence.get("topOfBookDepth") is not None:
        confidence += 0.06
    if liquidity_evidence.get("axis") == "poor" and liquidity_evidence.get("blockNewEntries"):
        confidence += 0.06
    return round(_clamp(confidence, 0.05, 1.0), 4)


def _event_confidence(event_state: str | dict | None) -> float:
    if isinstance(event_state, dict):
        if event_state.get("newEntriesBlocked"):
            return 0.95
        if event_state.get("scheduledState") in {"blackout", "soon", "elevated", "none"}:
            return 0.92 if event_state.get("isScheduledMacroEvent") else 0.88
        return 0.25
    if event_state in {None, "unknown"}:
        return 0.25
    if event_state in {"blackout", "soon", "elevated", "none"}:
        return 0.90
    return 0.55


def _required_confidence_axes(raw_regime: str, axes: RegimeAxes) -> tuple[str, ...]:
    if raw_regime in {"event_risk"}:
        return ("event", "liquidity")
    if raw_regime in {"liquidity_stress"}:
        return ("liquidity", "event")
    if raw_regime in {"extreme_volatility_no_trade", "high_volatility_trend", "low_volatility_quiet"}:
        return ("volatility", "liquidity", "event")
    if raw_regime in {"opening_breakout", "intraday_expansion", "failed_breakout_reversal"}:
        return ("structure", "volatility", "liquidity", "event")
    if axes.structure in {"range", "mixed"} or raw_regime in {"range_bound", "choppy_mixed"}:
        return ("structure", "volatility", "liquidity", "event")
    return ("direction", "structure", "volatility", "liquidity", "event")


def _safety_block_confidence(
    no_trade: tuple[str, ...],
    *,
    volatility_evidence: dict,
    liquidity_evidence: dict,
    event_state: str | dict | None,
) -> float:
    if not no_trade:
        return 0.0
    confidence = 0.55
    scheduled_state = event_state.get("scheduledState") if isinstance(event_state, dict) else event_state
    if scheduled_state == "blackout" or (isinstance(event_state, dict) and event_state.get("newEntriesBlocked")):
        confidence = max(confidence, 0.95)
    if liquidity_evidence.get("blockNewEntries"):
        confidence = max(confidence, 0.90 if liquidity_evidence.get("axis") != "unknown" else 0.75)
    if _volatility_axis(volatility_evidence) == "extreme":
        confidence = max(confidence, _volatility_confidence(volatility_evidence))
    if "regime.safety.halt_luld_circuit" in no_trade:
        confidence = max(confidence, 0.95)
    return round(_clamp(confidence, 0.0, 1.0), 4)


def _same_minute_volatility_baseline(snapshot: RegimeMarketSnapshot, *, rv: float | None, rel_volume: float) -> dict:
    minute = _minute_key(snapshot.latest.timestamp)
    if minute is None:
        return {"calibrationStatus": "missing_or_insufficient_same_minute_history", "sampleSize": 0, "source": "same_minute_history"}
    peers = [candle for candle in snapshot.one_minute_candles[:-1] if _minute_key(candle.timestamp) == minute]
    if len(peers) < 20:
        return {"calibrationStatus": "missing_or_insufficient_same_minute_history", "sampleSize": len(peers), "source": "same_minute_history"}
    ranges = [max(0.0, candle.high - candle.low) for candle in peers]
    volumes = [max(0.0, candle.volume) for candle in peers]
    return {
        "calibrationStatus": "ready",
        "atrPercentile": None,
        "realizedVolatilityPercentile": None,
        "expectedRange": mean(ranges) if ranges else None,
        "expectedVolume": mean(volumes) if volumes else None,
        "currentVolumeVsExpected": rel_volume,
        "sampleSize": len(peers),
        "source": "same_minute_history",
    }


def _volatility_decision(evidence: dict) -> dict:
    atr_state = _volatility_component_state(evidence.get("atrPercentile"), compressed_lte=0.25)
    rv_state = _volatility_component_state(evidence.get("realizedVolatilityPercentile"), compressed_lte=0.35)
    range_state = _range_expansion_state(evidence.get("currentRangeVsExpected"))
    disagreement = _volatility_disagreement_codes(atr_state, rv_state, range_state)
    atr_high = atr_state in {"expanded", "extreme"}
    rv_high = rv_state in {"expanded", "extreme"}
    range_high = range_state in {"expanded", "extreme"}
    atr_low = atr_state == "compressed"
    rv_low = rv_state == "compressed"
    range_low = range_state == "compressed"
    both_percentiles_ready = atr_state != "unknown" and rv_state != "unknown"

    if both_percentiles_ready and atr_high and rv_high:
        axis = "extreme" if "extreme" in {atr_state, rv_state} else "expanded"
        agreement = "atr_rv_high_agreement"
    elif both_percentiles_ready and atr_low and rv_low and not range_high:
        axis = "compressed"
        agreement = "atr_rv_compressed_agreement"
    elif (atr_high or rv_high) and range_high:
        axis = "extreme" if "extreme" in {atr_state, rv_state, range_state} else "expanded"
        agreement = "range_confirmed_transition"
    elif (atr_low or rv_low) and range_low and not (atr_high or rv_high):
        axis = "compressed" if not both_percentiles_ready else "normal"
        agreement = "range_confirmed_compression" if axis == "compressed" else "atr_rv_disagreement"
    else:
        axis = "normal"
        agreement = "atr_rv_disagreement" if disagreement else "normal_or_incomplete"

    return {
        "axis": axis,
        "componentStates": {
            "atrPercentile": atr_state,
            "realizedVolatilityPercentile": rv_state,
            "currentRangeVsExpected": range_state,
        },
        "agreement": agreement,
        "disagreementReasonCodes": disagreement,
    }


def _volatility_component_state(value, *, compressed_lte: float) -> str:
    if value is None:
        return "unknown"
    if _gte(value, 0.97):
        return "extreme"
    if _gte(value, 0.75):
        return "expanded"
    if _lte(value, compressed_lte):
        return "compressed"
    return "normal"


def _range_expansion_state(value) -> str:
    if value is None:
        return "unknown"
    if _gte(value, 3.0):
        return "extreme"
    if _gte(value, 1.5):
        return "expanded"
    if _lte(value, 0.75):
        return "compressed"
    return "normal"


def _volatility_disagreement_codes(atr_state: str, rv_state: str, range_state: str) -> list[str]:
    reasons: list[str] = []
    if atr_state != "unknown" and rv_state != "unknown":
        atr_direction = _volatility_direction(atr_state)
        rv_direction = _volatility_direction(rv_state)
        if atr_direction != rv_direction:
            reasons.append("regime.volatility.atr_rv_direction_disagreement")
    if range_state != "unknown":
        range_direction = _volatility_direction(range_state)
        percentile_directions = {
            _volatility_direction(state)
            for state in (atr_state, rv_state)
            if state != "unknown"
        }
        if percentile_directions and range_direction not in percentile_directions and range_direction != "normal":
            reasons.append("regime.volatility.range_percentile_disagreement")
    return reasons


def _volatility_direction(state: str) -> str:
    if state in {"expanded", "extreme"}:
        return "high"
    if state == "compressed":
        return "low"
    return "normal"


def _minute_key(timestamp: str) -> str | None:
    parsed = parse_exchange_timestamp(timestamp)
    return parsed.strftime("%H:%M") if parsed is not None else None


def _minute_of_session(timestamp: str) -> int | None:
    return exchange_session(timestamp).minutes_from_open


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _clamp01(value):
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _gte(value, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _lte(value, threshold: float) -> bool:
    return value is not None and float(value) <= threshold
