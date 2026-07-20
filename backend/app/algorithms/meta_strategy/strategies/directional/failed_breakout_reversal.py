from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class FailedBreakoutReversalStrategy(DirectionalSnapshotStrategy):
    strategy_id = "failed_breakout_reversal"
    family = "REVERSAL"
    minimum_warmup = 40
    required_inputs = ("candles", "atr", "spread", "liquidity", "failedBreakoutSide")
    buy_threshold = 0.60
    sell_threshold = 0.60

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        side = str(snapshot.features.get("failedBreakoutSide") or "none")
        reclaim_atr = float(snapshot.features.get("reclaimDistanceAtr") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 0.0)
        quality = (0.35 if reclaim_atr >= 0.15 else 0.0) + (0.25 if spread <= 10.0 else 0.0)
        return {
            "failedBreakoutSide": side,
            "reclaimDistanceAtr": reclaim_atr,
            "spreadBps": spread,
            "buyScore": quality if side == "downside" else 0.0,
            "sellScore": quality if side == "upside" else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumReclaimAtr": 0.15},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and evidence["failedBreakoutSide"] in {"upside", "downside"}
