from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    atr_distance,
    clamp01,
    confirmation_candle,
    current_vwap,
    recent_volume_contraction,
    settings_payload,
    trend_evidence,
)


@dataclass(frozen=True)
class TrendPullbackSettings:
    minimum_adx: float = 18.0
    minimum_pullback_atr: float = 0.25
    maximum_pullback_atr: float = 1.8
    structure_regimes: tuple[str, ...] = ("trend", "reversal", "mixed")


DEFAULT_SETTINGS = TrendPullbackSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    trend = trend_evidence(snapshot, classification)
    vw = current_vwap(snapshot, classification)
    ema20 = trend["ema20"]
    pullback_ref = ema20 if ema20 is not None and abs(snapshot.latest.close - ema20) <= abs(snapshot.latest.close - vw) else vw
    depth = atr_distance(snapshot, snapshot.latest.close, pullback_ref) if pullback_ref is not None else None
    contraction = recent_volume_contraction(snapshot)
    structure_label = classification.features.get("structureLabel") or classification.axes.structure
    direction = trend["direction"]
    confirms = confirmation_candle(snapshot, direction)
    invalidation = snapshot.latest.low if direction == "up" else snapshot.latest.high if direction == "down" else None
    evidence = {
        **trend,
        "pullbackReference": pullback_ref,
        "pullbackDepthAtr": depth,
        "pullbackVolumeContraction": contraction,
        "structureLabel": structure_label,
        "confirmationCandle": confirms,
        "invalidationLevel": invalidation,
        "settings": settings_payload(settings),
    }
    missing = list(trend["missingInputs"])
    if depth is None:
        missing.append("pullbackDepthAtr")
    if contraction is None:
        missing.append("pullbackVolumeContraction")
    if missing:
        return "Hold", 0.0, "regime.strategy.trend_pullback.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if direction == "none" or trend["adx"] < settings.minimum_adx:
        return "Hold", 0.36, "regime.strategy.trend_pullback.established_trend_required", evidence
    if depth < settings.minimum_pullback_atr or depth > settings.maximum_pullback_atr:
        return "Hold", 0.40, "regime.strategy.trend_pullback.depth_out_of_bounds", evidence
    if contraction is not True or not confirms:
        return "Hold", 0.44, "regime.strategy.trend_pullback.awaiting_pullback_confirmation", evidence
    if str(structure_label) not in settings.structure_regimes:
        return "Hold", 0.38, "regime.strategy.trend_pullback.structure_not_preserved", evidence
    confidence = clamp01(0.55 + min(depth, 1.0) * 0.12 + trend["adx"] / 140)
    return ("Buy" if direction == "up" else "Sell"), confidence, "regime.strategy.trend_pullback.confirmed", evidence
