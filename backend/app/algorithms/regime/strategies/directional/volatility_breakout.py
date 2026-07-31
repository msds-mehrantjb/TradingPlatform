from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    bollinger,
    clamp01,
    compression,
    current_atr,
    expected_edge_bps,
    reference_payload,
    relative_vol,
    rolling_reference,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class VolatilityBreakoutSettings:
    maximum_prior_compression: float = 0.75
    minimum_current_expansion: float = 1.45
    minimum_relative_volume: float = 1.05
    maximum_chase_atr: float = 1.8
    validity_seconds: int = 60


DEFAULT_SETTINGS = VolatilityBreakoutSettings()
STRATEGY_ID = "volatility_breakout"
STRATEGY_VERSION = "volatility_breakout_v2"
FAMILY = "breakout"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    comp = compression(snapshot)
    reference = rolling_reference(snapshot, 20)
    bands = bollinger(snapshot)
    atr_value = current_atr(snapshot)
    latest = snapshot.latest
    reference_high = reference["high"]
    reference_low = reference["low"]
    expansion = None
    if reference_high is not None and reference_low is not None:
        prior_width = max(reference_high - reference_low, 0.01)
        expansion = (latest.high - latest.low) / prior_width
    rv = relative_vol(snapshot)
    body = latest.close - latest.open
    signal = "Buy" if reference_high is not None and latest.close > reference_high and body > 0 else "Sell" if reference_low is not None and latest.close < reference_low and body < 0 else "Hold"
    stop = reference_low if signal == "Buy" else reference_high if signal == "Sell" else None
    target = latest.close + max(abs(latest.close - (stop or latest.close)), atr_value or 0.0) * 1.4 if signal == "Buy" else latest.close - max(abs((stop or latest.close) - latest.close), atr_value or 0.0) * 1.4 if signal == "Sell" else None
    edge = expected_edge_bps((target or latest.close) - latest.close, latest.close) if target is not None else 0.0
    chase_atr = abs(latest.close - (reference_high if signal == "Buy" else reference_low if signal == "Sell" else latest.close)) / max(atr_value or 0.0, 0.01)
    evidence = {
        "reference": reference,
        "bollingerBands": bands,
        "compressionRatio": comp,
        "rangeExpansion": expansion,
        "relativeVolume": rv,
        "bodyDirection": "up" if body > 0 else "down" if body < 0 else "flat",
        "candidateSignal": signal,
        "chaseDistanceAtr": chase_atr,
        "falseBreakoutRejected": signal != "Hold" and ((signal == "Buy" and latest.close > latest.open) or (signal == "Sell" and latest.close < latest.open)),
        "expectedGrossEdgeBps": edge,
        "settings": settings_payload(settings),
    }
    missing = [name for name, value in {"compressionRatio": comp, "rangeExpansion": expansion, "referenceRange": reference_high if reference_high is not None and reference_low is not None else None, "bollingerBands": bands["bandwidth"], "atr": atr_value}.items() if value is None]
    entry_ref = reference_payload("volatility_expansion_close", latest.close, source="finalized_one_minute", timestamp=latest.timestamp)
    stop_ref = reference_payload("compression_range_opposite_side", stop, source="reference_range", timestamp=latest.timestamp)
    target_ref = reference_payload("volatility_breakout_risk_target", target, source="risk_multiple", timestamp=latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, signal, reference_high, reference_low)
    def done(out_signal, confidence, reason, *, ready=True):
        payload = {**evidence, "missingInputReasons": tuple(missing)} if missing else evidence
        return out_signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=out_signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=payload, data_ready=ready)
    if missing:
        return done("Hold", 0.0, "regime.strategy.volatility_breakout.missing_inputs", ready=False)
    if comp > settings.maximum_prior_compression or bands["bandwidth"] is None or bands["bandwidth"] > 0.018:
        return done("Hold", 0.40, "regime.strategy.volatility_breakout.contraction_required")
    if expansion < settings.minimum_current_expansion or rv < settings.minimum_relative_volume:
        return done("Hold", 0.43, "regime.strategy.volatility_breakout.expansion_required")
    if classification.raw_regime not in {"intraday_expansion", "opening_breakout", "high_volatility_trend", "weak_uptrend", "strong_uptrend", "weak_downtrend", "strong_downtrend"}:
        return done("Hold", 0.38, "regime.strategy.volatility_breakout.regime_incompatible")
    if signal == "Hold" or chase_atr > settings.maximum_chase_atr:
        return done("Hold", 0.35, "regime.strategy.volatility_breakout.direction_unconfirmed")
    reason = "regime.strategy.volatility_breakout.upside_expansion" if signal == "Buy" else "regime.strategy.volatility_breakout.downside_expansion"
    return done(signal, clamp01(0.56 + min(expansion / 10, 0.18) + min(rv / 12, 0.10) + min(edge / 700, 0.08)), reason)
