from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    clamp01,
    current_atr,
    current_vwap,
    expected_edge_bps,
    opening_range,
    premarket_levels,
    reference_payload,
    rolling_reference,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class FailedBreakoutReversalSettings:
    opening_range_minutes: int = 30
    lookback: int = 24
    minimum_trade_through_bps: float = 2.0
    minimum_rejection_wick_fraction: float = 0.25
    validity_seconds: int = 90


DEFAULT_SETTINGS = FailedBreakoutReversalSettings()
STRATEGY_ID = "failed_breakout_reversal"
STRATEGY_VERSION = "failed_breakout_reversal_v2"
FAMILY = "reversal"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    latest = snapshot.latest
    previous = snapshot.candles[-2]
    vw = current_vwap(snapshot, classification)
    atr_value = current_atr(snapshot)
    references = {
        "openingRangeHigh": opening_range(snapshot, settings.opening_range_minutes)["high"],
        "openingRangeLow": opening_range(snapshot, settings.opening_range_minutes)["low"],
        "recentHigh": rolling_reference(snapshot, settings.lookback)["high"],
        "recentLow": rolling_reference(snapshot, settings.lookback)["low"],
        "premarketHigh": premarket_levels(snapshot)["high"],
        "premarketLow": premarket_levels(snapshot)["low"],
        "sessionVwap": vw,
    }
    candle_range = max(latest.high - latest.low, 0.01)
    upper_wick = (latest.high - max(latest.open, latest.close)) / candle_range
    lower_wick = (min(latest.open, latest.close) - latest.low) / candle_range
    base_evidence = {
        "references": references,
        "previousClose": previous.close,
        "close": latest.close,
        "high": latest.high,
        "low": latest.low,
        "upperWickFraction": upper_wick,
        "lowerWickFraction": lower_wick,
        "settings": settings_payload(settings),
    }
    def done(signal, confidence, reason, evidence, *, ready=True):
        level = evidence.get("failedLevelPrice")
        stop = evidence.get("stopLevel")
        target = evidence.get("targetLevel")
        edge = evidence.get("expectedGrossEdgeBps") or 0.0
        return signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=reference_payload("failed_breakout_confirmation_close", latest.close, source="finalized_one_minute", timestamp=latest.timestamp), stop_reference=reference_payload("failed_excursion_stop", stop, source="failed_reference_extreme", timestamp=latest.timestamp), target_reference=reference_payload("return_to_value_reference", target, source="vwap_or_opposite_range", timestamp=latest.timestamp), valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, signal, level), reason_codes=(reason,), evidence=evidence, data_ready=ready)
    for name, level in references.items():
        if level is None:
            continue
        trade_through_bps = 0.0
        if name.endswith("High") and latest.high > level and latest.close < level and upper_wick >= settings.minimum_rejection_wick_fraction:
            trade_through_bps = expected_edge_bps(latest.high - level, level)
            if trade_through_bps >= settings.minimum_trade_through_bps:
                stop = latest.high + (atr_value or 0.0) * 0.25
                target = vw or references.get("openingRangeLow") or references.get("recentLow")
                edge = expected_edge_bps(latest.close - (target or latest.close), latest.close)
                payload = {**base_evidence, "failedLevel": name, "failedLevelPrice": level, "tradeThroughBps": trade_through_bps, "stopLevel": stop, "targetLevel": target, "expectedGrossEdgeBps": edge, "entryAfterFailureConfirmed": True}
                return done("Sell", clamp01(0.60 + min(trade_through_bps / 100, 0.12) + min(edge / 500, 0.08)), "regime.strategy.failed_breakout_reversal.failed_high_acceptance", payload)
        if name.endswith("Low") and latest.low < level and latest.close > level and lower_wick >= settings.minimum_rejection_wick_fraction:
            trade_through_bps = expected_edge_bps(level - latest.low, level)
            if trade_through_bps >= settings.minimum_trade_through_bps:
                stop = latest.low - (atr_value or 0.0) * 0.25
                target = vw or references.get("openingRangeHigh") or references.get("recentHigh")
                edge = expected_edge_bps((target or latest.close) - latest.close, latest.close)
                payload = {**base_evidence, "failedLevel": name, "failedLevelPrice": level, "tradeThroughBps": trade_through_bps, "stopLevel": stop, "targetLevel": target, "expectedGrossEdgeBps": edge, "entryAfterFailureConfirmed": True}
                return done("Buy", clamp01(0.60 + min(trade_through_bps / 100, 0.12) + min(edge / 500, 0.08)), "regime.strategy.failed_breakout_reversal.failed_low_acceptance", payload)
    if not any(value is not None for value in references.values()):
        reason = "regime.strategy.failed_breakout_reversal.missing_inputs"
        return done("Hold", 0.0, reason, {**base_evidence, "missingInputReasons": ("referenceLevels",)}, ready=False)
    return done("Hold", 0.42, "regime.strategy.failed_breakout_reversal.no_failed_acceptance", base_evidence)
