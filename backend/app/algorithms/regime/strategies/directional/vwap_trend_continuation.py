from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import atr_distance, clamp01, confirmation_candle, current_vwap, settings_payload, trend_evidence, vwap_slope


@dataclass(frozen=True)
class VwapTrendContinuationSettings:
    minimum_vwap_slope: float = 0.00008
    maximum_interaction_distance_atr: float = 0.9
    minimum_adx: float = 16.0


DEFAULT_SETTINGS = VwapTrendContinuationSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    trend = trend_evidence(snapshot, classification)
    vw = current_vwap(snapshot, classification)
    slope = vwap_slope(snapshot)
    interaction_distance = atr_distance(snapshot, snapshot.latest.close, vw) if vw is not None else None
    direction = trend["direction"]
    held_or_reclaimed = interaction_distance is not None and interaction_distance <= settings.maximum_interaction_distance_atr
    evidence = {
        **trend,
        "vwapSlope": slope,
        "interactionDistanceAtr": interaction_distance,
        "heldOrReclaimedVwap": held_or_reclaimed,
        "settings": settings_payload(settings),
    }
    missing = list(trend["missingInputs"])
    if slope is None:
        missing.append("vwapSlope")
    if interaction_distance is None:
        missing.append("interactionDistanceAtr")
    if missing:
        return "Hold", 0.0, "regime.strategy.vwap_trend_continuation.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if direction == "none" or trend["adx"] < settings.minimum_adx:
        return "Hold", 0.38, "regime.strategy.vwap_trend_continuation.trend_required", evidence
    if abs(slope) < settings.minimum_vwap_slope or not held_or_reclaimed:
        return "Hold", 0.42, "regime.strategy.vwap_trend_continuation.vwap_interaction_required", evidence
    if direction == "up" and slope > 0 and confirmation_candle(snapshot, "up"):
        return "Buy", clamp01(0.56 + trend["adx"] / 150), "regime.strategy.vwap_trend_continuation.bullish_reclaim", evidence
    if direction == "down" and slope < 0 and confirmation_candle(snapshot, "down"):
        return "Sell", clamp01(0.56 + trend["adx"] / 150), "regime.strategy.vwap_trend_continuation.bearish_reclaim", evidence
    return "Hold", 0.42, "regime.strategy.vwap_trend_continuation.awaiting_confirmation", evidence
