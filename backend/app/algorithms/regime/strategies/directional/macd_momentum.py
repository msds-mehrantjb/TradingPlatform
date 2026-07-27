from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, current_atr, macd_evidence, settings_payload, trend_evidence


@dataclass(frozen=True)
class MacdMomentumSettings:
    minimum_normalized_magnitude: float = 0.08
    crossover_freshness_bars: int = 3
    minimum_adx: float = 14.0


DEFAULT_SETTINGS = MacdMomentumSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    macd = macd_evidence(snapshot)
    trend = trend_evidence(snapshot, classification)
    atr_value = current_atr(snapshot)
    normalized = abs(macd["histogram"]) / atr_value if macd["histogram"] is not None and atr_value and atr_value > 0 else None
    fresh_cross = macd["histogram"] is not None and macd["previousHistogram"] is not None and macd["histogram"] * macd["previousHistogram"] <= 0
    evidence = {**macd, "normalizedMagnitude": normalized, "freshCrossover": fresh_cross, "trendDirection": trend["direction"], "adx": trend["adx"], "settings": settings_payload(settings)}
    if macd["histogram"] is None or macd["slope"] is None or normalized is None:
        return "Hold", 0.0, "regime.strategy.macd_momentum.missing_inputs", {**evidence, "missingInputReasons": ("macdHistogram", "macdSlope", "normalizedMagnitude")}
    if normalized < settings.minimum_normalized_magnitude or trend["adx"] < settings.minimum_adx:
        return "Hold", 0.40, "regime.strategy.macd_momentum.magnitude_or_trend_insufficient", evidence
    if macd["histogram"] > 0 and macd["slope"] > 0 and trend["direction"] == "up":
        return "Buy", clamp01(0.55 + min(normalized, 1.0) * 0.16 + (0.04 if fresh_cross else 0.0)), "regime.strategy.macd_momentum.bullish_histogram_acceleration", evidence
    if macd["histogram"] < 0 and macd["slope"] < 0 and trend["direction"] == "down":
        return "Sell", clamp01(0.55 + min(normalized, 1.0) * 0.16 + (0.04 if fresh_cross else 0.0)), "regime.strategy.macd_momentum.bearish_histogram_acceleration", evidence
    return "Hold", 0.42, "regime.strategy.macd_momentum.price_trend_confirmation_missing", evidence
