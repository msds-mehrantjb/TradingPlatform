from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    bollinger,
    clamp01,
    current_atr,
    expected_edge_bps,
    macd_evidence,
    reference_payload,
    rsi_value,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class BollingerBandMeanReversionSettings:
    zscore_threshold: float = 1.8
    maximum_bandwidth: float = 0.025
    oversold_rsi: float = 38.0
    overbought_rsi: float = 62.0
    validity_seconds: int = 75


DEFAULT_SETTINGS = BollingerBandMeanReversionSettings()
STRATEGY_ID = "bollinger_band_mean_reversion"
STRATEGY_VERSION = "bollinger_band_mean_reversion_v2"
FAMILY = "mean_reversion"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    bands = bollinger(snapshot)
    latest = snapshot.latest
    previous = snapshot.candles[-2]
    reentered_from_below = previous.close < bands["lower"] and latest.close > bands["lower"] if bands["lower"] is not None else False
    reentered_from_above = previous.close > bands["upper"] and latest.close < bands["upper"] if bands["upper"] is not None else False
    rsi = rsi_value(snapshot)
    macd = macd_evidence(snapshot)
    atr_value = current_atr(snapshot)
    signal = "Buy" if bands["zscore"] is not None and bands["zscore"] <= -settings.zscore_threshold and reentered_from_below else "Sell" if bands["zscore"] is not None and bands["zscore"] >= settings.zscore_threshold and reentered_from_above else "Hold"
    stop = latest.low - (atr_value or 0.0) * 0.5 if signal == "Buy" else latest.high + (atr_value or 0.0) * 0.5 if signal == "Sell" else None
    target = bands["middle"]
    edge = expected_edge_bps((target or latest.close) - latest.close, latest.close) if target is not None else 0.0
    momentum_exhaustion = bool((signal == "Buy" and rsi is not None and rsi <= settings.oversold_rsi and (macd["slope"] or 0) >= 0) or (signal == "Sell" and rsi is not None and rsi >= settings.overbought_rsi and (macd["slope"] or 0) <= 0))
    evidence = {
        "bands": bands,
        "bandReentryFromBelow": reentered_from_below,
        "bandReentryFromAbove": reentered_from_above,
        "rsi": rsi,
        "momentumExhaustion": momentum_exhaustion,
        "macdSlope": macd["slope"],
        "structureAxis": classification.axes.structure,
        "volatilityAxis": classification.axes.volatility,
        "expectedGrossEdgeBps": edge,
        "stopLevel": stop,
        "targetLevel": target,
        "settings": settings_payload(settings),
    }
    missing = [name for name, value in {"bollingerBands": bands["zscore"], "bandwidth": bands["bandwidth"], "rsi": rsi, "atr": atr_value}.items() if value is None]
    entry_ref = reference_payload("bollinger_reentry_close", latest.close, source="finalized_one_minute", timestamp=latest.timestamp)
    stop_ref = reference_payload("band_excursion_stop", stop, source="band_or_atr_geometry", timestamp=latest.timestamp)
    target_ref = reference_payload("bollinger_middle_band", target, source="moving_mean", timestamp=latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, signal, bands["zscore"])
    def done(out_signal, confidence, reason, *, ready=True):
        payload = {**evidence, "missingInputReasons": tuple(missing)} if missing else evidence
        return out_signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=out_signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=payload, data_ready=ready)
    if missing:
        return done("Hold", 0.0, "regime.strategy.bollinger_band_mean_reversion.missing_inputs", ready=False)
    if bands["bandwidth"] > settings.maximum_bandwidth or classification.axes.volatility in {"high", "extreme"}:
        return done("Hold", 0.34, "regime.strategy.bollinger_band_mean_reversion.bandwidth_or_regime_denied")
    if classification.features.get("structureLabel") == "valid_breakout":
        return done("Hold", 0.30, "regime.strategy.bollinger_band_mean_reversion.active_breakout_denied")
    if signal != "Hold" and momentum_exhaustion:
        reason = "regime.strategy.bollinger_band_mean_reversion.lower_band_reentry" if signal == "Buy" else "regime.strategy.bollinger_band_mean_reversion.upper_band_reentry"
        return done(signal, clamp01(0.57 + min(abs(bands["zscore"]) / 20, 0.15) + min(edge / 500, 0.08)), reason)
    return done("Hold", 0.42, "regime.strategy.bollinger_band_mean_reversion.no_reentry")
