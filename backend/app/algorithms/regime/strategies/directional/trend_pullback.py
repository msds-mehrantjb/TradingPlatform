from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    atr_distance,
    clamp01,
    confirmation_candle,
    current_atr,
    current_vwap,
    expected_edge_bps,
    reference_payload,
    recent_volume_contraction,
    settings_payload,
    setup_id,
    structured_evidence,
    timeframe_trend,
    trend_evidence,
    valid_until,
)


@dataclass(frozen=True)
class TrendPullbackSettings:
    minimum_adx: float = 18.0
    minimum_pullback_atr: float = 0.25
    maximum_pullback_atr: float = 1.8
    maximum_extension_atr: float = 2.4
    validity_seconds: int = 90
    structure_regimes: tuple[str, ...] = ("trend", "reversal", "mixed")


DEFAULT_SETTINGS = TrendPullbackSettings()
STRATEGY_ID = "trend_pullback"
STRATEGY_VERSION = "trend_pullback_v2"
FAMILY = "trend"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    trend = trend_evidence(snapshot, classification)
    trend_15m = timeframe_trend(snapshot.fifteen_minute_candles, label="15m")
    trend_5m = timeframe_trend(snapshot.five_minute_candles, label="5m")
    vw = current_vwap(snapshot, classification)
    ema20 = trend["ema20"]
    pullback_ref = ema20 if ema20 is not None and (vw is None or abs(snapshot.latest.close - ema20) <= abs(snapshot.latest.close - vw)) else vw
    depth = atr_distance(snapshot, snapshot.latest.close, pullback_ref) if pullback_ref is not None else None
    extension = atr_distance(snapshot, snapshot.latest.close, trend["ema50"]) if trend["ema50"] is not None else None
    contraction = recent_volume_contraction(snapshot)
    structure_label = classification.features.get("structureLabel") or classification.axes.structure
    direction = trend["direction"]
    confirms = confirmation_candle(snapshot, direction)
    atr_value = current_atr(snapshot)
    invalidation = (
        min(snapshot.latest.low, (pullback_ref or snapshot.latest.close) - (atr_value or 0.0) * 0.75)
        if direction == "up"
        else max(snapshot.latest.high, (pullback_ref or snapshot.latest.close) + (atr_value or 0.0) * 0.75)
        if direction == "down"
        else None
    )
    target = (
        snapshot.latest.close + max(snapshot.latest.close - invalidation, atr_value or 0.0) * 1.5
        if direction == "up" and invalidation is not None
        else snapshot.latest.close - max(invalidation - snapshot.latest.close, atr_value or 0.0) * 1.5
        if direction == "down" and invalidation is not None
        else None
    )
    edge = expected_edge_bps((target or snapshot.latest.close) - snapshot.latest.close, snapshot.latest.close) if target is not None else 0.0
    evidence = {
        **trend,
        "fifteenMinuteTrend": trend_15m,
        "fiveMinuteTrend": trend_5m,
        "pullbackReference": pullback_ref,
        "pullbackDepthAtr": depth,
        "extensionAtr": extension,
        "pullbackVolumeContraction": contraction,
        "structureLabel": structure_label,
        "confirmationCandle": confirms,
        "invalidationLevel": invalidation,
        "targetLevel": target,
        "expectedGrossEdgeBps": edge,
        "settings": settings_payload(settings),
    }
    missing = list(trend["missingInputs"])
    if not trend_15m["dataReady"]:
        missing.append("fifteenMinuteTrend")
    if not trend_5m["dataReady"]:
        missing.append("fiveMinuteTrend")
    if depth is None:
        missing.append("pullbackDepthAtr")
    if extension is None:
        missing.append("extensionAtr")
    if contraction is None:
        missing.append("pullbackVolumeContraction")
    entry_ref = reference_payload("pullback_reclaim_close", snapshot.latest.close, source="finalized_one_minute", timestamp=snapshot.latest.timestamp)
    stop_ref = reference_payload("structure_atr_invalidation", invalidation, source="structure_or_atr", timestamp=snapshot.latest.timestamp)
    target_ref = reference_payload("risk_multiple_target", target, source="atr_structure_geometry", timestamp=snapshot.latest.timestamp)
    setup = setup_id(STRATEGY_ID, snapshot.symbol, snapshot.latest.timestamp, direction, pullback_ref)
    if missing:
        reason = "regime.strategy.trend_pullback.missing_inputs"
        return "Hold", 0.0, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.0, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence={**evidence, "missingInputReasons": tuple(missing)}, data_ready=False)
    if direction == "none" or trend["adx"] < settings.minimum_adx:
        reason = "regime.strategy.trend_pullback.established_trend_required"
        return "Hold", 0.36, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.36, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    if trend_15m["direction"] != direction or trend_5m["direction"] != direction:
        reason = "regime.strategy.trend_pullback.higher_timeframe_alignment_required"
        return "Hold", 0.40, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.40, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    if depth < settings.minimum_pullback_atr or depth > settings.maximum_pullback_atr:
        reason = "regime.strategy.trend_pullback.depth_out_of_bounds"
        return "Hold", 0.40, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.40, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    if extension is not None and extension > settings.maximum_extension_atr:
        reason = "regime.strategy.trend_pullback.overextended"
        return "Hold", 0.38, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.38, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    if contraction is not True or not confirms:
        reason = "regime.strategy.trend_pullback.awaiting_pullback_confirmation"
        return "Hold", 0.44, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.44, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    if str(structure_label) not in settings.structure_regimes:
        reason = "regime.strategy.trend_pullback.structure_not_preserved"
        return "Hold", 0.38, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal="Hold", confidence=0.38, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
    confidence = clamp01(0.55 + min(depth, 1.0) * 0.12 + trend["adx"] / 140)
    signal = "Buy" if direction == "up" else "Sell"
    reason = "regime.strategy.trend_pullback.confirmed"
    return signal, confidence, reason, structured_evidence(strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, family=FAMILY, signal=signal, confidence=confidence, expected_gross_edge_bps=edge, entry_reference=entry_ref, stop_reference=stop_ref, target_reference=target_ref, valid_until_timestamp=valid_until(snapshot.latest.timestamp, seconds=settings.validity_seconds), setup_identifier=setup, reason_codes=(reason,), evidence=evidence, data_ready=True)
