from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, candle_high, candle_low, candles, close_outside, latest_close, structural_invalidation


class OpeningRangeBreakoutStrategy(DirectionalSnapshotStrategy):
    strategy_id = "opening_range_breakout"
    family = "BREAKOUT"
    required_inputs = ("candles", "atr", "relative_volume", "spread", "liquidity", "opening_range")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        high = float(snapshot.features.get("openingRangeHigh") or 0.0)
        low = float(snapshot.features.get("openingRangeLow") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        spread_value = snapshot.spread.get("basisPoints")
        spread = float(spread_value) if spread_value is not None else 0.0
        liquidity_score = float(snapshot.liquidity.get("score") or 0.0)
        range_duration = int(snapshot.features.get("openingRangeDurationMinutes") or 30)
        range_finalized = high > 0 and low > 0 and high > low and len(candles(snapshot, "1m")) >= range_duration
        buffer = max(atr * 0.10, latest_close(snapshot) * 0.0005)
        close_breakout_up, close_breakout_down = close_outside(snapshot, high=high, low=low, buffer=buffer)
        breakout_up_atr = (latest_close(snapshot) - high) / atr if atr and high else 0.0
        breakout_down_atr = (low - latest_close(snapshot)) / atr if atr and low else 0.0
        overextended_up = atr > 0 and (candle_high(snapshot) - high) / atr > 2.5
        overextended_down = atr > 0 and (low - candle_low(snapshot)) / atr > 2.5
        spread_ok = spread <= 8.0
        liquidity_ok = liquidity_score >= 0.45
        relvol_ok = relvol >= 1.25
        quality = 0.35 + (0.2 if relvol_ok else 0.0) + (0.15 if spread_ok else 0.0) + (0.15 if liquidity_ok else 0.0)
        return {
            "openingRangeDurationMinutes": range_duration,
            "openingRangeFinalized": range_finalized,
            "openingRangeHigh": high,
            "openingRangeLow": low,
            "breakoutBuffer": round(buffer, 6),
            "closeOutsideRange": {"buy": close_breakout_up, "sell": close_breakout_down},
            "breakoutUpAtr": breakout_up_atr,
            "breakoutDownAtr": breakout_down_atr,
            "relativeVolume": relvol,
            "spreadBps": spread,
            "liquidityScore": liquidity_score,
            "optionalRetestConfirmation": {"required": False, "confirmed": True},
            "oneInitialOpportunityPerDirection": True,
            "lateEntryBlocked": overextended_up or overextended_down,
            "overextension": {"buy": overextended_up, "sell": overextended_down},
            "failedBreakoutState": {
                "usableByReversal": True,
                "buySideFailure": candle_high(snapshot) > high + buffer and latest_close(snapshot) <= high,
                "sellSideFailure": candle_low(snapshot) < low - buffer and latest_close(snapshot) >= low,
            },
            "entryReference": latest_close(snapshot),
            "invalidationReference": low if close_breakout_up else high,
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if close_breakout_up else "SELL"),
            "buyScore": quality if range_finalized and close_breakout_up and relvol_ok and spread_ok and liquidity_ok and not overextended_up else 0.0,
            "sellScore": quality if range_finalized and close_breakout_down and relvol_ok and spread_ok and liquidity_ok and not overextended_down else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumBreakoutAtr": 0.10, "maxSpreadBps": 8.0, "minimumRelativeVolume": 1.25},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["spreadBps"]) <= 8.0
