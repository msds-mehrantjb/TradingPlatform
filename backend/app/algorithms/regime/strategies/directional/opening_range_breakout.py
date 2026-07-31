from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    atr_distance,
    clamp01,
    cost_bps,
    current_atr,
    expected_edge_bps,
    opening_range,
    range_expansion,
    reference_payload,
    relative_vol,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class OpeningRangeBreakoutSettings:
    range_minutes: int = 30
    minimum_breakout_distance_bps: float = 3.0
    minimum_volume_expansion: float = 1.2
    minimum_range_expansion: float = 1.15
    latest_entry_minute: int = 90
    maximum_extension_bps: float = 45.0
    maximum_spread_bps: float = 8.0
    breakout_buffer_atr: float = 0.08
    maximum_chase_atr: float = 1.25
    validity_seconds: int = 45


DEFAULT_SETTINGS = OpeningRangeBreakoutSettings()
STRATEGY_ID = "opening_range_breakout"
STRATEGY_VERSION = "opening_range_breakout_v2"
FAMILY = "breakout"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    or_state = opening_range(snapshot, settings.range_minutes)
    close = snapshot.latest.close
    high = or_state["high"]
    low = or_state["low"]
    expansion = range_expansion(snapshot)
    volume_expansion = relative_vol(snapshot)
    minutes_from_open = or_state["minutesFromOpen"]
    atr_value = current_atr(snapshot)
    buffer = max((atr_value or 0.0) * settings.breakout_buffer_atr, close * settings.minimum_breakout_distance_bps / 10_000)
    spread = cost_bps(snapshot, classification)
    signal = "Hold"
    distance_bps = 0.0
    breakout_level = None
    if high is not None and close > high + buffer:
        signal = "Buy"
        breakout_level = high
        distance_bps = expected_edge_bps(close - high, close)
    elif low is not None and close < low - buffer:
        signal = "Sell"
        breakout_level = low
        distance_bps = expected_edge_bps(low - close, close)
    chase_atr = atr_distance(snapshot, close, breakout_level) if breakout_level is not None else None
    stop = low if signal == "Buy" else high if signal == "Sell" else None
    risk = abs(close - stop) if stop is not None else 0.0
    target = close + risk * 1.5 if signal == "Buy" else close - risk * 1.5 if signal == "Sell" else None
    edge = expected_edge_bps((target or close) - close, close) if target is not None else distance_bps
    evidence = {
        "openingRange": or_state,
        "close": close,
        "breakoutDistanceBps": distance_bps,
        "breakoutBuffer": buffer,
        "breakoutLevel": breakout_level,
        "chaseDistanceAtr": chase_atr,
        "rangeExpansion": expansion,
        "relativeVolume": volume_expansion,
        "spreadBps": spread,
        "expectedGrossEdgeBps": edge,
        "openingRangeInvalidation": stop,
        "targetLevel": target,
        "attemptPolicy": {"maximumAttemptsPerDirectionPerSession": 1, "attemptStateSource": "regime_runtime_state"},
        "settings": settings_payload(settings),
    }
    missing = []
    if not or_state["complete"]:
        missing.append("completedOpeningRange")
    if minutes_from_open is None:
        missing.append("validSessionWindow")
    if expansion is None:
        missing.append("rangeExpansion")
    if atr_value is None:
        missing.append("atr")
    entry_ref = reference_payload("opening_range_breakout_close", close, source="finalized_one_minute", timestamp=snapshot.latest.timestamp)
    stop_ref = reference_payload("opening_range_opposite_side", stop, source="opening_range", timestamp=snapshot.latest.timestamp)
    target_ref = reference_payload("opening_range_measured_move", target, source="risk_or_range_multiple", timestamp=snapshot.latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, snapshot.latest.timestamp, signal, high, low)
    def done(out_signal, confidence, reason, *, ready=True):
        return out_signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=out_signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence={**evidence, "missingInputReasons": tuple(missing)} if missing else evidence, data_ready=ready)
    if missing:
        return done("Hold", 0.0, "regime.strategy.opening_range_breakout.missing_inputs", ready=False)
    if signal == "Hold" or distance_bps < settings.minimum_breakout_distance_bps:
        return done("Hold", 0.42, "regime.strategy.opening_range_breakout.no_close_beyond_range")
    if minutes_from_open > settings.latest_entry_minute or distance_bps > settings.maximum_extension_bps or (chase_atr is not None and chase_atr > settings.maximum_chase_atr):
        return done("Hold", 0.40, "regime.strategy.opening_range_breakout.late_or_extended")
    if volume_expansion < settings.minimum_volume_expansion and expansion < settings.minimum_range_expansion:
        return done("Hold", 0.45, "regime.strategy.opening_range_breakout.expansion_unconfirmed")
    if spread > settings.maximum_spread_bps or classification.axes.liquidity in {"poor", "unknown"} or classification.features.get("liquidityBlockNewEntries"):
        return done("Hold", 0.30, "regime.strategy.opening_range_breakout.liquidity_denied")
    confidence = clamp01(0.56 + min(distance_bps / 80, 0.18) + min(volume_expansion / 10, 0.12) + min((expansion or 1) / 12, 0.10))
    return done(signal, confidence, "regime.strategy.opening_range_breakout.confirmed")
