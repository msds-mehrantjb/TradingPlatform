from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, opening_range, premarket_levels, rolling_reference, settings_payload


@dataclass(frozen=True)
class FailedBreakoutReversalSettings:
    opening_range_minutes: int = 30
    lookback: int = 24
    minimum_trade_through_bps: float = 2.0


DEFAULT_SETTINGS = FailedBreakoutReversalSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    latest = snapshot.latest
    previous = snapshot.candles[-2]
    references = {
        "openingRangeHigh": opening_range(snapshot, settings.opening_range_minutes)["high"],
        "openingRangeLow": opening_range(snapshot, settings.opening_range_minutes)["low"],
        "recentHigh": rolling_reference(snapshot, settings.lookback)["high"],
        "recentLow": rolling_reference(snapshot, settings.lookback)["low"],
        "premarketHigh": premarket_levels(snapshot)["high"],
        "premarketLow": premarket_levels(snapshot)["low"],
    }
    evidence = {"references": references, "previousClose": previous.close, "close": latest.close, "high": latest.high, "low": latest.low, "settings": settings_payload(settings)}
    for name, level in references.items():
        if level is None:
            continue
        if name.endswith("High") and previous.high > level and latest.close < level:
            return "Sell", clamp01(0.60 + min((previous.high - level) / max(level, 0.01) * 50, 0.12)), "regime.strategy.failed_breakout_reversal.failed_high_acceptance", {**evidence, "failedLevel": name, "failedLevelPrice": level}
        if name.endswith("Low") and previous.low < level and latest.close > level:
            return "Buy", clamp01(0.60 + min((level - previous.low) / max(level, 0.01) * 50, 0.12)), "regime.strategy.failed_breakout_reversal.failed_low_acceptance", {**evidence, "failedLevel": name, "failedLevelPrice": level}
    if not any(value is not None for value in references.values()):
        return "Hold", 0.0, "regime.strategy.failed_breakout_reversal.missing_inputs", {**evidence, "missingInputReasons": ("referenceLevels",)}
    return "Hold", 0.42, "regime.strategy.failed_breakout_reversal.no_failed_acceptance", evidence
