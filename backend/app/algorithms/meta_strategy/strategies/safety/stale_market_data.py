"""Stale market data safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class StaleMarketDataFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "stale_market_data_filter"
    required_inputs = ("source_cutoff_timestamp",)
    max_age_seconds = 90.0

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        age_seconds = (snapshot.timestamp - snapshot.source_cutoff_timestamp).total_seconds()
        observed = {"ageSeconds": age_seconds}
        threshold = {"maxAgeSeconds": self.max_age_seconds}
        if age_seconds > self.max_age_seconds:
            return block_evidence(reason_code="meta_strategy.safety.stale_market_data.blocked", observed=observed, threshold=threshold, existing_position_action="MONITOR")
        return pass_evidence(reason_code="meta_strategy.safety.stale_market_data.pass", observed=observed, threshold=threshold)
