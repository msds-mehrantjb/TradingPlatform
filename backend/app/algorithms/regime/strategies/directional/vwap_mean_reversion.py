from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import atr_distance, clamp01, confirmation_candle, cost_bps, current_vwap, expected_edge_bps, settings_payload, trend_evidence


@dataclass(frozen=True)
class VwapMeanReversionSettings:
    minimum_vwap_distance_atr: float = 0.65
    minimum_net_edge_bps: float = 4.0
    maximum_adx: float = 26.0


DEFAULT_SETTINGS = VwapMeanReversionSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    close = snapshot.latest.close
    vw = current_vwap(snapshot, classification)
    distance_atr = atr_distance(snapshot, close, vw) if vw is not None else None
    edge_bps = expected_edge_bps(close - vw, close) if vw is not None else 0.0
    net_edge = edge_bps - cost_bps(snapshot, classification)
    trend = trend_evidence(snapshot, classification)
    regime_ok = classification.raw_regime in {"range_bound", "sideways_range", "choppy_mixed", "low_volatility_quiet"} or classification.axes.structure == "range"
    evidence = {
        "close": close,
        "vwap": vw,
        "distanceAtr": distance_atr,
        "edgeBps": edge_bps,
        "netEdgeBps": net_edge,
        "rangeCompatible": regime_ok,
        "adx": trend["adx"],
        "settings": settings_payload(settings),
    }
    if vw is None or distance_atr is None:
        return "Hold", 0.0, "regime.strategy.vwap_mean_reversion.missing_inputs", {**evidence, "missingInputReasons": ("vwapDistanceAtr",)}
    if not regime_ok or (trend["adx"] is not None and trend["adx"] > settings.maximum_adx):
        return "Hold", 0.35, "regime.strategy.vwap_mean_reversion.regime_denied", evidence
    if distance_atr < settings.minimum_vwap_distance_atr or net_edge < settings.minimum_net_edge_bps:
        return "Hold", 0.41, "regime.strategy.vwap_mean_reversion.edge_or_distance_insufficient", evidence
    if close < vw and confirmation_candle(snapshot, "up"):
        return "Buy", clamp01(0.56 + min(distance_atr / 10, 0.16)), "regime.strategy.vwap_mean_reversion.below_vwap_rejection", evidence
    if close > vw and confirmation_candle(snapshot, "down"):
        return "Sell", clamp01(0.56 + min(distance_atr / 10, 0.16)), "regime.strategy.vwap_mean_reversion.above_vwap_rejection", evidence
    return "Hold", 0.42, "regime.strategy.vwap_mean_reversion.awaiting_rejection", evidence
