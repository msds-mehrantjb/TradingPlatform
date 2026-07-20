"""Market structure context."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class MarketStructureContextStrategy(ContextSnapshotStrategy):
    strategy_id = "market_structure_context"
    required_inputs = ("candles", "moving_averages", "atr")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        candle = snapshot.candles["1m"][-1]
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        ema20 = float(snapshot.moving_averages["1m"].get("ema20") or close)
        ema50 = float(snapshot.moving_averages["1m"].get("ema50") or ema20)
        atr = float(snapshot.atr.get("1m") or 1.0)
        trend_alignment = (ema20 - ema50) / max(atr, 1e-9)
        close_location = ((close - low) / max(high - low, 1e-9)) - 0.5
        structure_edge = trend_alignment * 0.1 + close_location * 0.2
        return {
            "close": close,
            "ema20": ema20,
            "ema50": ema50,
            "atr": atr,
            "trendAlignmentAtr": round(trend_alignment, 6),
            "closeLocation": round(close_location, 6),
            "structureEdge": round(structure_edge, 6),
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        edge = float(evidence["structureEdge"])
        return {
            "eligibilityAdjustment": 0.0,
            "confidenceAdjustment": edge * 0.5,
            "familyWeightMultiplier": 1.0 + edge * 0.5,
            "candidateQualityAdjustment": edge * 0.4,
            "profileSelectionBias": edge * 0.75,
        }
