"""Insufficient liquidity safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, liquidity_score, missing_required_evidence, pass_evidence


class InsufficientLiquidityFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "insufficient_liquidity_filter"
    required_inputs = ("liquidity",)
    min_liquidity_score = 0.35

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        score = float(liquidity_score(snapshot) or 0.0)
        level = str(snapshot.liquidity.get("level") or "unknown").lower()
        observed = {"liquidityScore": score, "liquidityLevel": level}
        threshold = {"minLiquidityScore": self.min_liquidity_score}
        if level == "poor" or score < self.min_liquidity_score:
            return block_evidence(reason_code="meta_strategy.safety.insufficient_liquidity.blocked", observed=observed, threshold=threshold, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.insufficient_liquidity.pass", observed=observed, threshold=threshold)
