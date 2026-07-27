from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.exchange_calendar import exchange_session
from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, premarket_levels, previous_regular_close, settings_payload


@dataclass(frozen=True)
class GapContinuationFadeSettings:
    minimum_gap_bps: float = 20.0
    opening_window_minutes: int = 45


DEFAULT_SETTINGS = GapContinuationFadeSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    previous_close = previous_regular_close(snapshot)
    levels = premarket_levels(snapshot)
    session = exchange_session(snapshot.latest.timestamp)
    open_price = snapshot.candles[0].open
    gap_bps = ((open_price - previous_close) / previous_close * 10_000) if previous_close else None
    latest = snapshot.latest
    evidence = {
        "previousRegularSessionClose": previous_close,
        "sessionOpen": open_price,
        "gapBps": gap_bps,
        "premarketLevels": levels,
        "minutesFromOpen": session.minutes_from_open,
        "settings": settings_payload(settings),
    }
    if previous_close is None or gap_bps is None:
        return "Hold", 0.0, "regime.strategy.gap_continuation_fade.missing_inputs", {**evidence, "missingInputReasons": ("previousRegularClose",)}
    if session.minutes_from_open is None or session.minutes_from_open > settings.opening_window_minutes:
        return "Hold", 0.36, "regime.strategy.gap_continuation_fade.opening_window_required", evidence
    if abs(gap_bps) < settings.minimum_gap_bps:
        return "Hold", 0.40, "regime.strategy.gap_continuation_fade.gap_too_small", evidence
    if gap_bps > 0:
        if levels["high"] is not None and latest.close > levels["high"]:
            return "Buy", clamp01(0.58 + min(abs(gap_bps) / 1000, 0.12)), "regime.strategy.gap_continuation_fade.gap_up_continuation", evidence
        if latest.close < open_price:
            return "Sell", clamp01(0.56 + min(abs(gap_bps) / 1200, 0.10)), "regime.strategy.gap_continuation_fade.gap_up_fade", evidence
    if gap_bps < 0:
        if levels["low"] is not None and latest.close < levels["low"]:
            return "Sell", clamp01(0.58 + min(abs(gap_bps) / 1000, 0.12)), "regime.strategy.gap_continuation_fade.gap_down_continuation", evidence
        if latest.close > open_price:
            return "Buy", clamp01(0.56 + min(abs(gap_bps) / 1200, 0.10)), "regime.strategy.gap_continuation_fade.gap_down_fade", evidence
    return "Hold", 0.42, "regime.strategy.gap_continuation_fade.no_gap_resolution", evidence
