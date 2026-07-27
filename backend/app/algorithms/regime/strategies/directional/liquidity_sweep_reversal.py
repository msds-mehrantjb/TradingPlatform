from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, opening_range, premarket_levels, relative_vol, rolling_reference, settings_payload


@dataclass(frozen=True)
class LiquiditySweepReversalSettings:
    opening_range_minutes: int = 30
    lookback: int = 24
    minimum_relative_volume: float = 1.05
    minimum_wick_fraction: float = 0.35


DEFAULT_SETTINGS = LiquiditySweepReversalSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    latest = snapshot.latest
    candle_range = max(latest.high - latest.low, 0.01)
    upper_wick = (latest.high - max(latest.open, latest.close)) / candle_range
    lower_wick = (min(latest.open, latest.close) - latest.low) / candle_range
    references = {
        "openingRangeHigh": opening_range(snapshot, settings.opening_range_minutes)["high"],
        "openingRangeLow": opening_range(snapshot, settings.opening_range_minutes)["low"],
        "recentHigh": rolling_reference(snapshot, settings.lookback)["high"],
        "recentLow": rolling_reference(snapshot, settings.lookback)["low"],
        "premarketHigh": premarket_levels(snapshot)["high"],
        "premarketLow": premarket_levels(snapshot)["low"],
    }
    rv = relative_vol(snapshot)
    evidence = {"references": references, "upperWickFraction": upper_wick, "lowerWickFraction": lower_wick, "relativeVolume": rv, "settings": settings_payload(settings)}
    if not any(value is not None for value in references.values()):
        return "Hold", 0.0, "regime.strategy.liquidity_sweep_reversal.missing_inputs", {**evidence, "missingInputReasons": ("liquidityLevels",)}
    for name, level in references.items():
        if level is None:
            continue
        if name.endswith("High") and latest.high > level and latest.close < level and upper_wick >= settings.minimum_wick_fraction and rv >= settings.minimum_relative_volume:
            return "Sell", clamp01(0.60 + min(upper_wick / 4, 0.12)), "regime.strategy.liquidity_sweep_reversal.high_sweep_rejection", {**evidence, "sweptLevel": name, "sweptLevelPrice": level, "invalidationLevel": latest.high}
        if name.endswith("Low") and latest.low < level and latest.close > level and lower_wick >= settings.minimum_wick_fraction and rv >= settings.minimum_relative_volume:
            return "Buy", clamp01(0.60 + min(lower_wick / 4, 0.12)), "regime.strategy.liquidity_sweep_reversal.low_sweep_rejection", {**evidence, "sweptLevel": name, "sweptLevelPrice": level, "invalidationLevel": latest.low}
    return "Hold", 0.42, "regime.strategy.liquidity_sweep_reversal.no_sweep_rejection", evidence
