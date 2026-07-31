from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    atr_distance,
    clamp01,
    confirmation_candle,
    cost_bps,
    current_atr,
    current_vwap,
    expected_edge_bps,
    reference_payload,
    rsi_value,
    settings_payload,
    setup_id,
    structured_evidence,
    trend_evidence,
    valid_until,
)


@dataclass(frozen=True)
class VwapMeanReversionSettings:
    minimum_vwap_distance_atr: float = 0.65
    minimum_net_edge_bps: float = 4.0
    maximum_adx: float = 26.0
    exhaustion_rsi_low: float = 35.0
    exhaustion_rsi_high: float = 65.0
    validity_seconds: int = 75


DEFAULT_SETTINGS = VwapMeanReversionSettings()
STRATEGY_ID = "vwap_mean_reversion"
STRATEGY_VERSION = "vwap_mean_reversion_v2"
FAMILY = "vwap"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    close = snapshot.latest.close
    vw = current_vwap(snapshot, classification)
    distance_atr = atr_distance(snapshot, close, vw) if vw is not None else None
    edge_bps = expected_edge_bps(close - vw, close) if vw is not None else 0.0
    net_edge = edge_bps - cost_bps(snapshot, classification)
    trend = trend_evidence(snapshot, classification)
    atr_value = current_atr(snapshot)
    rsi = rsi_value(snapshot)
    latest = snapshot.latest
    previous = snapshot.candles[-2] if len(snapshot.candles) >= 2 else latest
    regime_ok = classification.raw_regime in {"range_bound", "choppy_mixed", "low_volatility_quiet"} or classification.axes.structure == "range"
    below_exhaustion = vw is not None and close < vw and rsi is not None and rsi <= settings.exhaustion_rsi_low
    above_exhaustion = vw is not None and close > vw and rsi is not None and rsi >= settings.exhaustion_rsi_high
    reentry = bool(vw is not None and ((previous.close < vw and latest.close > previous.close and confirmation_candle(snapshot, "up")) or (previous.close > vw and latest.close < previous.close and confirmation_candle(snapshot, "down"))))
    stop = latest.low - (atr_value or 0.0) * 0.5 if close < (vw or close) else latest.high + (atr_value or 0.0) * 0.5 if close > (vw or close) else None
    target = vw
    evidence = {
        "close": close,
        "vwap": vw,
        "distanceAtr": distance_atr,
        "edgeBps": edge_bps,
        "netEdgeBps": net_edge,
        "rangeCompatible": regime_ok,
        "rsi": rsi,
        "exhaustionEvidence": "below_vwap_oversold" if below_exhaustion else "above_vwap_overbought" if above_exhaustion else "none",
        "reentryConfirmation": reentry,
        "adx": trend["adx"],
        "expectedGrossEdgeBps": edge_bps,
        "stopLevel": stop,
        "targetLevel": target,
        "timeStopSeconds": settings.validity_seconds,
        "settings": settings_payload(settings),
    }
    missing = [name for name, value in {"vwapDistanceAtr": distance_atr, "rsi": rsi, "atr": atr_value}.items() if value is None]
    entry_ref = reference_payload("vwap_reversion_confirmation_close", close, source="finalized_one_minute", timestamp=latest.timestamp)
    stop_ref = reference_payload("excursion_stop", stop, source="atr_beyond_excursion", timestamp=latest.timestamp)
    target_ref = reference_payload("session_vwap", target, source="session_vwap", timestamp=latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, "below" if close < (vw or close) else "above", vw)
    def done(signal, confidence, reason, *, ready=True):
        payload = {**evidence, "missingInputReasons": tuple(missing)} if missing else evidence
        return signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=signal, confidence=confidence, expected_gross_edge_bps=edge_bps, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=payload, data_ready=ready)
    if missing:
        return done("Hold", 0.0, "regime.strategy.vwap_mean_reversion.missing_inputs", ready=False)
    if not regime_ok or (trend["adx"] is not None and trend["adx"] > settings.maximum_adx):
        return done("Hold", 0.35, "regime.strategy.vwap_mean_reversion.regime_denied")
    if distance_atr < settings.minimum_vwap_distance_atr or net_edge < settings.minimum_net_edge_bps:
        return done("Hold", 0.41, "regime.strategy.vwap_mean_reversion.edge_or_distance_insufficient")
    if close < vw and below_exhaustion and reentry:
        return done("Buy", clamp01(0.56 + min(distance_atr / 10, 0.16) + min(net_edge / 300, 0.08)), "regime.strategy.vwap_mean_reversion.below_vwap_rejection")
    if close > vw and above_exhaustion and reentry:
        return done("Sell", clamp01(0.56 + min(distance_atr / 10, 0.16) + min(net_edge / 300, 0.08)), "regime.strategy.vwap_mean_reversion.above_vwap_rejection")
    return done("Hold", 0.42, "regime.strategy.vwap_mean_reversion.awaiting_rejection")
