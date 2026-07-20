"""Excessive spread safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence, spread_bps


class ExcessiveSpreadFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "excessive_spread_filter"
    required_inputs = ("spread",)
    max_spread_bps = 12.0

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        observed_spread = float(spread_bps(snapshot) or 0.0)
        observed = {"spreadBasisPoints": observed_spread}
        threshold = {"maxSpreadBasisPoints": self.max_spread_bps}
        if observed_spread > self.max_spread_bps:
            return block_evidence(reason_code="meta_strategy.safety.excessive_spread.blocked", observed=observed, threshold=threshold, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.excessive_spread.pass", observed=observed, threshold=threshold)
