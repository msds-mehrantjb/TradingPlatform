from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import (
    DirectionalSnapshotStrategy,
    latest_close,
    ma,
    pct_distance,
    reward_to_risk,
    slope,
    structural_invalidation,
)


class MultiTimeframeTrendAlignmentStrategy(DirectionalSnapshotStrategy):
    strategy_id = "multi_timeframe_trend_alignment"
    family = "TREND"
    required_inputs = ("candles", "moving_averages", "moving_average_slope", "market_structure", "vwap", "atr", "adx", "reward_to_risk")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        pairs = {
            "1m": (ma(snapshot, "1m", "ema20"), ma(snapshot, "1m", "ema50")),
            "5m": (ma(snapshot, "5m", "ema20"), ma(snapshot, "5m", "ema50")),
            "15m": (ma(snapshot, "15m", "ema20"), ma(snapshot, "15m", "ema50")),
        }
        alignment = {
            timeframe: "UP" if fast and slow and fast > slow else "DOWN" if fast and slow and fast < slow else "FLAT"
            for timeframe, (fast, slow) in pairs.items()
        }
        aligned_up = sum(1 for value in alignment.values() if value == "UP")
        aligned_down = sum(1 for value in alignment.values() if value == "DOWN")
        adx = float(snapshot.adx.get("1m") or 0.0)
        vwap_bias = pct_distance(latest_close(snapshot), snapshot.vwap)
        one_minute_slope = slope(snapshot, "1m")
        five_minute_slope = slope(snapshot, "5m")
        fifteen_permission_up = alignment["15m"] == "UP"
        fifteen_permission_down = alignment["15m"] == "DOWN"
        confirmation_up = alignment["5m"] == "UP" and five_minute_slope >= -0.0005
        confirmation_down = alignment["5m"] == "DOWN" and five_minute_slope <= 0.0005
        trigger_up = alignment["1m"] == "UP" and one_minute_slope > 0 and vwap_bias > 0
        trigger_down = alignment["1m"] == "DOWN" and one_minute_slope < 0 and vwap_bias < 0
        stop_buy = structural_invalidation(snapshot, "BUY")
        stop_sell = structural_invalidation(snapshot, "SELL")
        rr_buy = reward_to_risk(snapshot, "BUY", stop=stop_buy)
        rr_sell = reward_to_risk(snapshot, "SELL", stop=stop_sell)
        higher_conflict = (aligned_up > 0 and aligned_down > 0) or alignment["5m"] != alignment["15m"]
        trend_strength = min(1.0, adx / 35.0)
        quality = min(1.0, 0.25 + trend_strength * 0.35 + min(0.2, abs(one_minute_slope) * 50.0) + min(0.2, abs(vwap_bias) * 25.0))
        return {
            "contract": "1m trigger, 5m confirmation, 15m permission",
            "timeframeAlignment": alignment,
            "alignedUpTimeframes": aligned_up,
            "alignedDownTimeframes": aligned_down,
            "adx": adx,
            "vwapBias": vwap_bias,
            "movingAverageSlope": {"1m": one_minute_slope, "5m": five_minute_slope, "15m": slope(snapshot, "15m")},
            "marketStructureConfirmed": aligned_up == 3 or aligned_down == 3,
            "fifteenMinutePermission": {"buy": fifteen_permission_up, "sell": fifteen_permission_down},
            "fiveMinuteConfirmation": {"buy": confirmation_up, "sell": confirmation_down},
            "higherTimeframeConflict": higher_conflict,
            "rewardToRisk": {"buy": round(rr_buy, 6), "sell": round(rr_sell, 6)},
            "entryReference": latest_close(snapshot),
            "invalidationReference": stop_buy if aligned_up >= aligned_down else stop_sell,
            "suggestedStopReference": stop_buy if aligned_up >= aligned_down else stop_sell,
            "buyScore": quality if trigger_up and confirmation_up and fifteen_permission_up and not higher_conflict and adx >= 18.0 and rr_buy >= 1.2 else 0.0,
            "sellScore": quality if trigger_down and confirmation_down and fifteen_permission_down and not higher_conflict and adx >= 18.0 and rr_sell >= 1.2 else 0.0,
            "blockReasonCodes": ("meta_strategy.directional.multi_timeframe.higher_timeframe_conflict",) if higher_conflict else (),
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumAdx": 18.0, "minimumRewardRisk": 1.2},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["adx"]) >= 18.0
