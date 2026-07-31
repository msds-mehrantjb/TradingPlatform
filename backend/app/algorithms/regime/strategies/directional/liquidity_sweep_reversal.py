from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import (
    clamp01,
    current_atr,
    expected_edge_bps,
    opening_range,
    premarket_levels,
    reference_payload,
    relative_vol,
    rolling_reference,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class LiquiditySweepReversalSettings:
    opening_range_minutes: int = 30
    lookback: int = 24
    minimum_relative_volume: float = 1.05
    minimum_wick_fraction: float = 0.35
    minimum_trade_through_bps: float = 2.0
    validity_seconds: int = 60


DEFAULT_SETTINGS = LiquiditySweepReversalSettings()
STRATEGY_ID = "liquidity_sweep_reversal"
STRATEGY_VERSION = "liquidity_sweep_reversal_v1_microstructure_required"
FAMILY = "reversal"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    latest = snapshot.latest
    microstructure = snapshot.context_feeds.get("microstructure") or {}
    quote = snapshot.context_feeds.get("quote") or {}
    trades = snapshot.context_feeds.get("tradeTape") or snapshot.context_feeds.get("trades") or {}
    has_trusted_microstructure = bool(
        microstructure.get("trusted")
        or quote.get("trusted")
        or trades.get("trusted")
        or snapshot.context_feeds.get("trustedMicrostructure")
    )
    candle_range = max(latest.high - latest.low, 0.01)
    upper_wick = (latest.high - max(latest.open, latest.close)) / candle_range
    lower_wick = (min(latest.open, latest.close) - latest.low) / candle_range
    references = {
        "openingRangeHigh": opening_range(snapshot, settings.opening_range_minutes)["high"],
        "openingRangeLow": opening_range(snapshot, settings.opening_range_minutes)["low"],
        "recentHigh": rolling_reference(snapshot, settings.lookback)["high"],
        "recentLow": rolling_reference(snapshot, settings.lookback)["low"],
        "premarketHigh": premarket_levels(snapshot)["high"],
        "premarketLow": premarket_levels(snapshot)["low"],
    }
    rv = relative_vol(snapshot)
    atr_value = current_atr(snapshot)
    evidence = {
        "references": references,
        "upperWickFraction": upper_wick,
        "lowerWickFraction": lower_wick,
        "relativeVolume": rv,
        "microstructureTrusted": has_trusted_microstructure,
        "quoteEvidence": quote,
        "tradeTapeEvidence": trades,
        "microstructureEvidence": microstructure,
        "settings": settings_payload(settings),
    }

    def done(signal: str, confidence: float, reason: str, payload: dict, *, ready: bool):
        level = payload.get("sweptLevelPrice")
        stop = payload.get("invalidationLevel")
        target = payload.get("targetLevel")
        return (
            signal,
            confidence,
            reason,
            structured_evidence(
                strategy_id=STRATEGY_ID,
                strategy_version=STRATEGY_VERSION,
                family=FAMILY,
                signal=signal,
                confidence=confidence,
                expected_gross_edge_bps=payload.get("expectedGrossEdgeBps") or 0.0,
                entry_reference=reference_payload("sweep_reversal_confirmation_close", latest.close, source="finalized_one_minute", timestamp=latest.timestamp),
                stop_reference=reference_payload("swept_liquidity_extreme", stop, source="quote_trade_microstructure", timestamp=latest.timestamp),
                target_reference=reference_payload("post_sweep_reversion_target", target, source="prior_liquidity_level", timestamp=latest.timestamp),
                valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds),
                setup_identifier=setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, signal, level),
                reason_codes=(reason,),
                evidence=payload,
                data_ready=ready,
                lifecycle_status="active" if ready else "not_data_ready",
            ),
        )

    if not has_trusted_microstructure:
        return done(
            "Hold",
            0.0,
            "regime.strategy.liquidity_sweep_reversal.microstructure_not_ready",
            {**evidence, "missingInputReasons": ("trustedQuoteOrTradeMicrostructure",)},
            ready=False,
        )
    if not any(value is not None for value in references.values()):
        return done(
            "Hold",
            0.0,
            "regime.strategy.liquidity_sweep_reversal.missing_inputs",
            {**evidence, "missingInputReasons": ("liquidityLevels",)},
            ready=False,
        )
    for name, level in references.items():
        if level is None:
            continue
        if name.endswith("High") and latest.high > level and latest.close < level and upper_wick >= settings.minimum_wick_fraction and rv >= settings.minimum_relative_volume:
            trade_through = expected_edge_bps(latest.high - level, level)
            if trade_through >= settings.minimum_trade_through_bps:
                target = references.get("openingRangeLow") or references.get("recentLow")
                edge = expected_edge_bps(latest.close - (target or latest.close), latest.close)
                payload = {
                    **evidence,
                    "sweptLevel": name,
                    "sweptLevelPrice": level,
                    "tradeThroughBps": trade_through,
                    "invalidationLevel": latest.high + (atr_value or 0.0) * 0.15,
                    "targetLevel": target,
                    "expectedGrossEdgeBps": edge,
                }
                return done("Sell", clamp01(0.60 + min(upper_wick / 4, 0.12) + min(edge / 600, 0.06)), "regime.strategy.liquidity_sweep_reversal.high_sweep_rejection", payload, ready=True)
        if name.endswith("Low") and latest.low < level and latest.close > level and lower_wick >= settings.minimum_wick_fraction and rv >= settings.minimum_relative_volume:
            trade_through = expected_edge_bps(level - latest.low, level)
            if trade_through >= settings.minimum_trade_through_bps:
                target = references.get("openingRangeHigh") or references.get("recentHigh")
                edge = expected_edge_bps((target or latest.close) - latest.close, latest.close)
                payload = {
                    **evidence,
                    "sweptLevel": name,
                    "sweptLevelPrice": level,
                    "tradeThroughBps": trade_through,
                    "invalidationLevel": latest.low - (atr_value or 0.0) * 0.15,
                    "targetLevel": target,
                    "expectedGrossEdgeBps": edge,
                }
                return done("Buy", clamp01(0.60 + min(lower_wick / 4, 0.12) + min(edge / 600, 0.06)), "regime.strategy.liquidity_sweep_reversal.low_sweep_rejection", payload, ready=True)
    return done("Hold", 0.42, "regime.strategy.liquidity_sweep_reversal.no_sweep_rejection", evidence, ready=True)
