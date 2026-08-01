from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, latest_close, structural_invalidation


class LiquiditySweepReversalStrategy(DirectionalSnapshotStrategy):
    strategy_id = "liquidity_sweep_reversal"
    family = "REVERSAL"
    required_inputs = ("candles", "liquidity", "spread", "volume", "sweepSide", "microstructure_evidence")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        side = str(snapshot.features.get("sweepSide") or "none")
        microstructure = snapshot.features.get("microstructureEvidence") or {}
        has_reliable_microstructure = bool(
            isinstance(microstructure, dict)
            and microstructure.get("reliable")
            and (
                microstructure.get("orderFlowImbalance") is not None
                or microstructure.get("depthSweep") is not None
                or microstructure.get("tradeAggressorImbalance") is not None
            )
        )
        rejection_wick = float(snapshot.features.get("rejectionWickRatio") or 0.0)
        liquidity_score = float(snapshot.liquidity.get("score") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 999.0)
        rejection_ok = rejection_wick >= 0.80
        spread_ok = spread <= 10.0
        liquidity_ok = liquidity_score >= 0.45
        quality = min(0.35, rejection_wick * 0.35) + min(0.2, liquidity_score * 0.2) + (0.2 if has_reliable_microstructure else 0.0)
        return {
            "shadowOnly": True,
            "orderInfluence": 0.0,
            "sweepSide": side,
            "microstructureEvidence": microstructure if isinstance(microstructure, dict) else {},
            "microstructureReliable": has_reliable_microstructure,
            "rejectionWickRatio": rejection_wick,
            "liquidityScore": liquidity_score,
            "spreadBps": spread,
            "entryReference": latest_close(snapshot),
            "invalidationReference": structural_invalidation(snapshot, "BUY" if side == "sell_side" else "SELL"),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if side == "sell_side" else "SELL"),
            "buyScore": quality if has_reliable_microstructure and rejection_ok and spread_ok and liquidity_ok and side == "sell_side" else 0.0,
            "sellScore": quality if has_reliable_microstructure and rejection_ok and spread_ok and liquidity_ok and side == "buy_side" else 0.0,
            "blockReasonCodes": ("meta_strategy.directional.liquidity_sweep.microstructure_unavailable",) if not has_reliable_microstructure else (),
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumWickRatio": 0.80},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return (
            super().regime_allows(snapshot, evidence)
            and bool(evidence.get("microstructureReliable"))
            and evidence["sweepSide"] in {"buy_side", "sell_side"}
            and float(evidence["rejectionWickRatio"]) >= 0.80
        )
