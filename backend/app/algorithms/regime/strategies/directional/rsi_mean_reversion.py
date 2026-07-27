from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import atr_distance, clamp01, confirmation_candle, rsi_value, settings_payload, trend_evidence


@dataclass(frozen=True)
class RsiMeanReversionSettings:
    oversold: float = 32.0
    overbought: float = 68.0
    recovery_buffer: float = 2.0
    minimum_target_atr: float = 0.45


DEFAULT_SETTINGS = RsiMeanReversionSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    current_rsi = rsi_value(snapshot)
    trend = trend_evidence(snapshot, classification)
    regime_ok = classification.raw_regime in {"range_bound", "sideways_range", "choppy_mixed", "low_volatility_quiet"} or classification.axes.structure == "range"
    recent = snapshot.candles[-4:]
    recent_low = min(c.low for c in recent)
    recent_high = max(c.high for c in recent)
    target_room = atr_distance(snapshot, snapshot.latest.close, recent_high if current_rsi is not None and current_rsi <= settings.oversold + settings.recovery_buffer else recent_low)
    evidence = {
        "rsi": current_rsi,
        "rangeCompatible": regime_ok,
        "trendDirection": trend["direction"],
        "adx": trend["adx"],
        "targetRoomAtr": target_room,
        "settings": settings_payload(settings),
    }
    missing = []
    if current_rsi is None:
        missing.append("rsi")
    if target_room is None:
        missing.append("targetRoomAtr")
    if missing:
        return "Hold", 0.0, "regime.strategy.rsi_mean_reversion.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if not regime_ok:
        return "Hold", 0.34, "regime.strategy.rsi_mean_reversion.range_regime_required", evidence
    if current_rsi <= settings.oversold + settings.recovery_buffer and trend["direction"] == "down" and trend["adx"] is not None and trend["adx"] >= 28:
        return "Hold", 0.36, "regime.strategy.rsi_mean_reversion.opposing_downtrend_too_strong", evidence
    if current_rsi >= settings.overbought - settings.recovery_buffer and trend["direction"] == "up" and trend["adx"] is not None and trend["adx"] >= 28:
        return "Hold", 0.36, "regime.strategy.rsi_mean_reversion.opposing_uptrend_too_strong", evidence
    if current_rsi <= settings.oversold + settings.recovery_buffer and confirmation_candle(snapshot, "up") and target_room >= settings.minimum_target_atr:
        return "Buy", clamp01(0.55 + (settings.oversold + settings.recovery_buffer - current_rsi) / 100), "regime.strategy.rsi_mean_reversion.oversold_recovery", evidence
    if current_rsi >= settings.overbought - settings.recovery_buffer and confirmation_candle(snapshot, "down") and target_room >= settings.minimum_target_atr:
        return "Sell", clamp01(0.55 + (current_rsi - settings.overbought + settings.recovery_buffer) / 100), "regime.strategy.rsi_mean_reversion.overbought_recovery", evidence
    return "Hold", 0.42, "regime.strategy.rsi_mean_reversion.no_extreme_recovery", evidence
