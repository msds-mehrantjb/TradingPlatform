from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import bollinger, clamp01, settings_payload


@dataclass(frozen=True)
class BollingerBandMeanReversionSettings:
    zscore_threshold: float = 1.8
    maximum_bandwidth: float = 0.025


DEFAULT_SETTINGS = BollingerBandMeanReversionSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    bands = bollinger(snapshot)
    latest = snapshot.latest
    previous = snapshot.candles[-2]
    reentered_from_below = previous.close < bands["lower"] and latest.close > bands["lower"] if bands["lower"] is not None else False
    reentered_from_above = previous.close > bands["upper"] and latest.close < bands["upper"] if bands["upper"] is not None else False
    evidence = {
        "bands": bands,
        "bandReentryFromBelow": reentered_from_below,
        "bandReentryFromAbove": reentered_from_above,
        "structureAxis": classification.axes.structure,
        "volatilityAxis": classification.axes.volatility,
        "settings": settings_payload(settings),
    }
    if bands["zscore"] is None or bands["bandwidth"] is None:
        return "Hold", 0.0, "regime.strategy.bollinger_band_mean_reversion.missing_inputs", {**evidence, "missingInputReasons": ("bollingerBands",)}
    if bands["bandwidth"] > settings.maximum_bandwidth or classification.axes.volatility in {"high", "extreme"}:
        return "Hold", 0.34, "regime.strategy.bollinger_band_mean_reversion.bandwidth_or_regime_denied", evidence
    if classification.features.get("structureLabel") == "valid_breakout":
        return "Hold", 0.30, "regime.strategy.bollinger_band_mean_reversion.active_breakout_denied", evidence
    if bands["zscore"] <= -settings.zscore_threshold and reentered_from_below:
        return "Buy", clamp01(0.57 + min(abs(bands["zscore"]) / 20, 0.15)), "regime.strategy.bollinger_band_mean_reversion.lower_band_reentry", evidence
    if bands["zscore"] >= settings.zscore_threshold and reentered_from_above:
        return "Sell", clamp01(0.57 + min(abs(bands["zscore"]) / 20, 0.15)), "regime.strategy.bollinger_band_mean_reversion.upper_band_reentry", evidence
    return "Hold", 0.42, "regime.strategy.bollinger_band_mean_reversion.no_reentry", evidence
