from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, extension_atr, settings_payload, trend_evidence


@dataclass(frozen=True)
class MovingAverageTrendSettings:
    minimum_adx: float = 18.0
    minimum_efficiency: float = 0.35
    maximum_extension_atr: float = 2.2


DEFAULT_SETTINGS = MovingAverageTrendSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    evidence = trend_evidence(snapshot, classification)
    extension = extension_atr(snapshot, evidence["ema20"])
    evidence = {**evidence, "extensionAtr": extension, "settings": settings_payload(settings)}
    missing = list(evidence["missingInputs"])
    if extension is None:
        missing.append("extensionAtr")
    if missing:
        return "Hold", 0.0, "regime.strategy.moving_average_trend.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if evidence["adx"] < settings.minimum_adx or evidence["efficiencyRatio"] < settings.minimum_efficiency:
        return "Hold", 0.35, "regime.strategy.moving_average_trend.trend_strength_insufficient", evidence
    if extension > settings.maximum_extension_atr:
        return "Hold", 0.42, "regime.strategy.moving_average_trend.excessive_extension", evidence
    if not evidence["higherTimeframePermission"]:
        return "Hold", 0.38, "regime.strategy.moving_average_trend.higher_timeframe_denied", evidence
    if evidence["direction"] == "up":
        confidence = clamp01(0.52 + evidence["adx"] / 100 + evidence["efficiencyRatio"] * 0.18)
        return "Buy", confidence, "regime.strategy.moving_average_trend.bullish_ema_alignment", evidence
    if evidence["direction"] == "down":
        confidence = clamp01(0.52 + evidence["adx"] / 100 + evidence["efficiencyRatio"] * 0.18)
        return "Sell", confidence, "regime.strategy.moving_average_trend.bearish_ema_alignment", evidence
    return "Hold", 0.40, "regime.strategy.moving_average_trend.no_alignment", evidence
