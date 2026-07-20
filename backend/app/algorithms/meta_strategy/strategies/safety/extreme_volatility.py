"""Extreme volatility safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, atr_percent, block_evidence, missing_required_evidence, pass_evidence


class ExtremeVolatilityFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "extreme_volatility_filter"
    required_inputs = ("atr", "relative_volume")
    max_atr_percent = 0.045
    max_relative_volume = 5.0

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        observed_atr_percent = float(atr_percent(snapshot) or 0.0)
        relative_volume = float(snapshot.relative_volume.get("1m") or 0.0)
        observed = {"atrPercent": observed_atr_percent, "relativeVolume": relative_volume}
        threshold = {"maxAtrPercent": self.max_atr_percent, "maxRelativeVolume": self.max_relative_volume}
        if observed_atr_percent > self.max_atr_percent or relative_volume > self.max_relative_volume:
            return block_evidence(reason_code="meta_strategy.safety.extreme_volatility.blocked", observed=observed, threshold=threshold, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.extreme_volatility.pass", observed=observed, threshold=threshold)
