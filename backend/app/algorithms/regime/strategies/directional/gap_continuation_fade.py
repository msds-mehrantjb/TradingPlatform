from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.exchange_calendar import exchange_session
from backend.app.algorithms.regime.strategies.directional.evidence import (
    clamp01,
    current_atr,
    current_vwap,
    expected_edge_bps,
    opening_range,
    premarket_levels,
    previous_regular_close,
    reference_payload,
    relative_vol,
    settings_payload,
    setup_id,
    structured_evidence,
    valid_until,
)


@dataclass(frozen=True)
class GapContinuationFadeSettings:
    minimum_gap_bps: float = 20.0
    opening_window_minutes: int = 45
    opening_range_minutes: int = 15
    minimum_acceptance_bps: float = 4.0
    minimum_relative_volume: float = 0.95
    maximum_extension_atr: float = 2.2
    continuation_target_atr: float = 1.6
    fade_target_fraction: float = 0.65
    validity_seconds: int = 90


DEFAULT_SETTINGS = GapContinuationFadeSettings()
STRATEGY_ID = "gap_continuation_fade"
STRATEGY_VERSION = "gap_continuation_fade_v2"
FAMILY = "event"


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    latest = snapshot.latest
    previous_close = previous_regular_close(snapshot)
    levels = premarket_levels(snapshot)
    session = exchange_session(latest.timestamp)
    open_price = snapshot.candles[0].open
    gap_bps = ((open_price - previous_close) / previous_close * 10_000) if previous_close else None
    atr_value = current_atr(snapshot)
    vw = current_vwap(snapshot, classification)
    opening = opening_range(snapshot, settings.opening_range_minutes)
    rv = relative_vol(snapshot)
    gap_direction = "up" if gap_bps is not None and gap_bps > 0 else "down" if gap_bps is not None and gap_bps < 0 else "none"
    acceptance_above_open_bps = expected_edge_bps(latest.close - open_price, open_price)
    extension_atr = abs(latest.close - open_price) / max(atr_value or 0.0, 0.01)
    evidence = {
        "previousRegularSessionClose": previous_close,
        "sessionOpen": open_price,
        "gapBps": gap_bps,
        "gapDirection": gap_direction,
        "premarketLevels": levels,
        "openingRange": opening,
        "minutesFromOpen": session.minutes_from_open,
        "sessionStatus": session.status,
        "vwap": vw,
        "relativeVolume": rv,
        "acceptanceFromOpenBps": acceptance_above_open_bps,
        "extensionAtr": extension_atr,
        "confirmedRegime": classification.raw_regime,
        "settings": settings_payload(settings),
    }

    def done(signal: str, confidence: float, reason: str, payload: dict, *, ready: bool = True):
        entry = latest.close
        stop = payload.get("stopLevel")
        target = payload.get("targetLevel")
        edge = payload.get("expectedGrossEdgeBps") or 0.0
        setup_kind = payload.get("setupKind", "none")
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
                expected_gross_edge_bps=edge,
                entry_reference=reference_payload("gap_resolution_close", entry, source="finalized_one_minute", timestamp=latest.timestamp),
                stop_reference=reference_payload("gap_invalidated_level", stop, source="gap_open_or_range", timestamp=latest.timestamp),
                target_reference=reference_payload("gap_target_level", target, source="gap_fill_or_atr_extension", timestamp=latest.timestamp),
                valid_until_timestamp=valid_until(latest.timestamp, seconds=settings.validity_seconds),
                setup_identifier=setup_id(STRATEGY_ID, snapshot.symbol, latest.timestamp, signal, setup_kind, round(float(gap_bps or 0), 2)),
                reason_codes=(reason,),
                evidence=payload,
                data_ready=ready,
            ),
        )

    if previous_close is None or gap_bps is None:
        return done(
            "Hold",
            0.0,
            "regime.strategy.gap_continuation_fade.missing_inputs",
            {**evidence, "missingInputReasons": ("previousRegularClose",)},
            ready=False,
        )
    if session.minutes_from_open is None or session.minutes_from_open > settings.opening_window_minutes:
        return done("Hold", 0.36, "regime.strategy.gap_continuation_fade.opening_window_required", evidence)
    if abs(gap_bps) < settings.minimum_gap_bps:
        return done("Hold", 0.40, "regime.strategy.gap_continuation_fade.gap_too_small", evidence)
    if rv < settings.minimum_relative_volume:
        return done("Hold", 0.38, "regime.strategy.gap_continuation_fade.volume_not_confirmed", evidence)
    if extension_atr > settings.maximum_extension_atr:
        return done("Hold", 0.44, "regime.strategy.gap_continuation_fade.extension_chase_block", evidence)

    range_high = opening["high"] if opening.get("complete") else levels["high"]
    range_low = opening["low"] if opening.get("complete") else levels["low"]
    continuation_long = gap_bps > 0 and range_high is not None and latest.close > range_high
    continuation_short = gap_bps < 0 and range_low is not None and latest.close < range_low
    gap_up_fade = gap_bps > 0 and latest.close < open_price and (vw is None or latest.close < vw)
    gap_down_fade = gap_bps < 0 and latest.close > open_price and (vw is None or latest.close > vw)

    if gap_bps > 0:
        if continuation_long and acceptance_above_open_bps >= settings.minimum_acceptance_bps:
            stop = max(open_price, range_high or open_price)
            target = latest.close + max(abs(latest.close - stop), atr_value or 0.0) * settings.continuation_target_atr
            edge = expected_edge_bps(target - latest.close, latest.close)
            payload = {
                **evidence,
                "setupKind": "gap_up_continuation",
                "acceptanceLevel": range_high,
                "continuationCriteria": ("gap_up", "accepted_above_opening_reference", "volume_confirmed"),
                "stopLevel": stop,
                "targetLevel": target,
                "expectedGrossEdgeBps": edge,
            }
            return done("Buy", clamp01(0.58 + min(abs(gap_bps) / 1000, 0.12) + min(edge / 600, 0.06)), "regime.strategy.gap_continuation_fade.gap_up_continuation", payload)
        if gap_up_fade:
            stop = max(open_price, range_high or open_price)
            target = open_price - (open_price - previous_close) * settings.fade_target_fraction
            edge = expected_edge_bps(latest.close - target, latest.close)
            payload = {
                **evidence,
                "setupKind": "gap_up_fade",
                "fadeCriteria": ("failed_open_acceptance", "below_vwap_or_vwap_missing"),
                "stopLevel": stop,
                "targetLevel": target,
                "expectedGrossEdgeBps": edge,
            }
            return done("Sell", clamp01(0.56 + min(abs(gap_bps) / 1200, 0.10) + min(edge / 700, 0.05)), "regime.strategy.gap_continuation_fade.gap_up_fade", payload)
    if gap_bps < 0:
        if continuation_short and acceptance_above_open_bps >= settings.minimum_acceptance_bps:
            stop = max(open_price, range_low or open_price)
            target = latest.close - max(abs(stop - latest.close), atr_value or 0.0) * settings.continuation_target_atr
            edge = expected_edge_bps(latest.close - target, latest.close)
            payload = {
                **evidence,
                "setupKind": "gap_down_continuation",
                "acceptanceLevel": range_low,
                "continuationCriteria": ("gap_down", "accepted_below_opening_reference", "volume_confirmed"),
                "stopLevel": stop,
                "targetLevel": target,
                "expectedGrossEdgeBps": edge,
            }
            return done("Sell", clamp01(0.58 + min(abs(gap_bps) / 1000, 0.12) + min(edge / 600, 0.06)), "regime.strategy.gap_continuation_fade.gap_down_continuation", payload)
        if gap_down_fade:
            stop = min(open_price, range_low or open_price)
            target = open_price + (previous_close - open_price) * settings.fade_target_fraction
            edge = expected_edge_bps(target - latest.close, latest.close)
            payload = {
                **evidence,
                "setupKind": "gap_down_fade",
                "fadeCriteria": ("failed_open_acceptance", "above_vwap_or_vwap_missing"),
                "stopLevel": stop,
                "targetLevel": target,
                "expectedGrossEdgeBps": edge,
            }
            return done("Buy", clamp01(0.56 + min(abs(gap_bps) / 1200, 0.10) + min(edge / 700, 0.05)), "regime.strategy.gap_continuation_fade.gap_down_fade", payload)
    return done("Hold", 0.42, "regime.strategy.gap_continuation_fade.no_gap_resolution", evidence)
