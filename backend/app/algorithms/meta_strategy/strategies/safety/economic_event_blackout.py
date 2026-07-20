"""Economic event blackout safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class EconomicEventBlackoutFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "economic_event_blackout_filter"
    required_inputs = ("economic_event_state",)
    blackout_window_minutes = 15.0

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        state = str(snapshot.economic_event_state.get("state") or "none").lower()
        severity = str(snapshot.economic_event_state.get("severity") or "none").lower()
        minutes_to_event = snapshot.economic_event_state.get("minutesToEvent")
        near_event = minutes_to_event is not None and abs(float(minutes_to_event)) <= self.blackout_window_minutes
        observed = {"eventState": state, "severity": severity, "minutesToEvent": minutes_to_event}
        threshold = {"blackoutWindowMinutes": self.blackout_window_minutes}
        if state in {"blocked", "halt"} or severity in {"high", "critical"} and near_event:
            return block_evidence(reason_code="meta_strategy.safety.economic_event_blackout.blocked", observed=observed, threshold=threshold, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.economic_event_blackout.pass", observed=observed, threshold=threshold)
