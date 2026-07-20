"""Halt and LULD safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class HaltLuldFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "halt_luld_filter"
    required_inputs = ("halt_luld_state",)

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        state = str(snapshot.features.get("haltLuldState") or "clear").lower()
        observed = {"haltLuldState": state}
        threshold = {"blockedStates": ("halted", "luld_pause", "limit_up", "limit_down")}
        if state in threshold["blockedStates"]:
            return block_evidence(reason_code="meta_strategy.safety.halt_luld.blocked", observed=observed, threshold=threshold, existing_position_action="EXIT_REQUIRED")
        return pass_evidence(reason_code="meta_strategy.safety.halt_luld.pass", observed=observed, threshold=threshold)
