from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    atr_distance,
    clamp01,
    confirmation_candle,
    cost_bps,
    current_atr,
    current_vwap,
    expected_edge_bps,
    recent_volume_contraction,
    reference_payload,
    relative_vol,
    settings_payload,
    setup_id,
    structured_evidence,
    trend_evidence,
    valid_until,
    vwap_slope,
)


@dataclass(frozen=True)
class VwapTrendContinuationSettings:
    minimum_vwap_slope: float = 0.00008
    maximum_interaction_distance_atr: float = 0.9
    maximum_chase_distance_atr: float = 1.6
    minimum_adx: float = 16.0
    minimum_relative_volume: float = 0.85
    maximum_cost_bps: float = 8.0
    validity_seconds: int = 60


DEFAULT_SETTINGS = VwapTrendContinuationSettings()
STRATEGY_ID = "vwap_trend_continuation"
STRATEGY_VERSION = "vwap_trend_continuation_v2"
FAMILY = "vwap"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    trend = trend_evidence(snapshot, classification)
    vw = current_vwap(snapshot, classification)
    slope = vwap_slope(snapshot)
    interaction_distance = atr_distance(snapshot, snapshot.latest.close, vw) if vw is not None else None
    direction = trend["direction"]
    latest = snapshot.latest
    previous = snapshot.candles[-2] if len(snapshot.candles) >= 2 else latest
    accepted_trend_side = bool(vw is not None and ((direction == "up" and latest.close > vw and previous.close >= vw) or (direction == "down" and latest.close < vw and previous.close <= vw)))
    reclaimed = bool(vw is not None and ((direction == "up" and previous.close < vw <= latest.close) or (direction == "down" and previous.close > vw >= latest.close)))
    continuation = confirmation_candle(snapshot, direction)
    consolidation = recent_volume_contraction(snapshot, lookback=4, baseline=16)
    held_or_reclaimed = interaction_distance is not None and interaction_distance <= settings.maximum_interaction_distance_atr and (accepted_trend_side or reclaimed)
    atr_value = current_atr(snapshot)
    rv = relative_vol(snapshot)
    costs = cost_bps(snapshot, classification)
    stop = (vw - (atr_value or 0.0) * 0.6) if direction == "up" and vw is not None else (vw + (atr_value or 0.0) * 0.6) if direction == "down" and vw is not None else None
    target = latest.close + (latest.close - stop) * 1.4 if direction == "up" and stop is not None else latest.close - (stop - latest.close) * 1.4 if direction == "down" and stop is not None else None
    edge = expected_edge_bps((target or latest.close) - latest.close, latest.close) if target is not None else 0.0
    evidence = {
        **trend,
        "vwapSlope": slope,
        "interactionDistanceAtr": interaction_distance,
        "heldOrReclaimedVwap": held_or_reclaimed,
        "acceptedTrendSide": accepted_trend_side,
        "vwapReclaimed": reclaimed,
        "continuationTrigger": continuation,
        "pullbackOrConsolidation": consolidation,
        "relativeVolume": rv,
        "transactionCostBps": costs,
        "expectedGrossEdgeBps": edge,
        "stopReferencePrice": stop,
        "targetReferencePrice": target,
        "settings": settings_payload(settings),
    }
    missing = list(trend["missingInputs"])
    if slope is None:
        missing.append("vwapSlope")
    if interaction_distance is None:
        missing.append("interactionDistanceAtr")
    if consolidation is None:
        missing.append("pullbackOrConsolidation")
    entry_ref = reference_payload("vwap_reclaim_or_continuation_close", latest.close, source="finalized_one_minute", timestamp=latest.timestamp)
    stop_ref = reference_payload("vwap_atr_invalidation", stop, source="vwap_plus_atr", timestamp=latest.timestamp)
    target_ref = reference_payload("vwap_continuation_target", target, source="risk_multiple", timestamp=latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, direction, vw)
    def done(signal, confidence, reason, *, ready=True):
        return signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence={**evidence, "missingInputReasons": tuple(missing)} if missing else evidence, data_ready=ready)
    if missing:
        return done("Hold", 0.0, "regime.strategy.vwap_trend_continuation.missing_inputs", ready=False)
    if direction == "none" or trend["adx"] < settings.minimum_adx:
        return done("Hold", 0.38, "regime.strategy.vwap_trend_continuation.trend_required")
    if classification.raw_regime not in {"strong_uptrend", "weak_uptrend", "strong_downtrend", "weak_downtrend", "high_volatility_trend", "opening_breakout", "intraday_expansion", "gap_session"}:
        return done("Hold", 0.36, "regime.strategy.vwap_trend_continuation.regime_incompatible")
    if costs > settings.maximum_cost_bps or rv < settings.minimum_relative_volume:
        return done("Hold", 0.40, "regime.strategy.vwap_trend_continuation.cost_or_volume_denied")
    if abs(slope) < settings.minimum_vwap_slope or not held_or_reclaimed or interaction_distance > settings.maximum_chase_distance_atr:
        return done("Hold", 0.42, "regime.strategy.vwap_trend_continuation.vwap_interaction_required")
    if direction == "up" and slope > 0 and continuation and (consolidation is True or reclaimed):
        return done("Buy", clamp01(0.56 + trend["adx"] / 150 + min(edge / 500, 0.08)), "regime.strategy.vwap_trend_continuation.bullish_reclaim")
    if direction == "down" and slope < 0 and continuation and (consolidation is True or reclaimed):
        return done("Sell", clamp01(0.56 + trend["adx"] / 150 + min(edge / 500, 0.08)), "regime.strategy.vwap_trend_continuation.bearish_reclaim")
    return done("Hold", 0.42, "regime.strategy.vwap_trend_continuation.awaiting_confirmation")
