from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, cost_bps, expected_edge_bps, opening_range, range_expansion, relative_vol, settings_payload


@dataclass(frozen=True)
class OpeningRangeBreakoutSettings:
    range_minutes: int = 30
    minimum_breakout_distance_bps: float = 3.0
    minimum_volume_expansion: float = 1.2
    minimum_range_expansion: float = 1.15
    latest_entry_minute: int = 90
    maximum_extension_bps: float = 45.0


DEFAULT_SETTINGS = OpeningRangeBreakoutSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    or_state = opening_range(snapshot, settings.range_minutes)
    close = snapshot.latest.close
    high = or_state["high"]
    low = or_state["low"]
    expansion = range_expansion(snapshot)
    volume_expansion = relative_vol(snapshot)
    minutes_from_open = or_state["minutesFromOpen"]
    signal = "Hold"
    distance_bps = 0.0
    if high is not None and close > high:
        signal = "Buy"
        distance_bps = expected_edge_bps(close - high, close)
    elif low is not None and close < low:
        signal = "Sell"
        distance_bps = expected_edge_bps(low - close, close)
    evidence = {
        "openingRange": or_state,
        "close": close,
        "breakoutDistanceBps": distance_bps,
        "rangeExpansion": expansion,
        "relativeVolume": volume_expansion,
        "spreadBps": cost_bps(snapshot, classification),
        "settings": settings_payload(settings),
    }
    missing = []
    if not or_state["complete"]:
        missing.append("completedOpeningRange")
    if minutes_from_open is None:
        missing.append("validSessionWindow")
    if expansion is None:
        missing.append("rangeExpansion")
    if missing:
        return "Hold", 0.0, "regime.strategy.opening_range_breakout.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if signal == "Hold" or distance_bps < settings.minimum_breakout_distance_bps:
        return "Hold", 0.42, "regime.strategy.opening_range_breakout.no_close_beyond_range", evidence
    if minutes_from_open > settings.latest_entry_minute or distance_bps > settings.maximum_extension_bps:
        return "Hold", 0.40, "regime.strategy.opening_range_breakout.late_or_extended", evidence
    if volume_expansion < settings.minimum_volume_expansion and expansion < settings.minimum_range_expansion:
        return "Hold", 0.45, "regime.strategy.opening_range_breakout.expansion_unconfirmed", evidence
    if classification.axes.liquidity in {"poor", "unknown"} or classification.features.get("liquidityBlockNewEntries"):
        return "Hold", 0.30, "regime.strategy.opening_range_breakout.liquidity_denied", evidence
    confidence = clamp01(0.56 + min(distance_bps / 80, 0.18) + min(volume_expansion / 10, 0.12) + min((expansion or 1) / 12, 0.10))
    return signal, confidence, "regime.strategy.opening_range_breakout.confirmed", evidence
