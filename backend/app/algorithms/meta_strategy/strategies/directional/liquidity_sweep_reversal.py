from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class LiquiditySweepReversalStrategy(DirectionalSnapshotStrategy):
    strategy_id = "liquidity_sweep_reversal"
    family = "REVERSAL"
    minimum_warmup = 40
    required_inputs = ("candles", "liquidity", "spread", "volume", "sweepSide")
    buy_threshold = 0.62
    sell_threshold = 0.62

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        side = str(snapshot.features.get("sweepSide") or "none")
        rejection_wick = float(snapshot.features.get("rejectionWickRatio") or 0.0)
        liquidity_score = float(snapshot.liquidity.get("score") or 0.0)
        quality = min(0.4, rejection_wick * 0.4) + min(0.3, liquidity_score * 0.3)
        return {
            "sweepSide": side,
            "rejectionWickRatio": rejection_wick,
            "liquidityScore": liquidity_score,
            "buyScore": quality if side == "sell_side" else 0.0,
            "sellScore": quality if side == "buy_side" else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumWickRatio": 0.80},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and evidence["sweepSide"] in {"buy_side", "sell_side"} and float(evidence["rejectionWickRatio"]) >= 0.80
