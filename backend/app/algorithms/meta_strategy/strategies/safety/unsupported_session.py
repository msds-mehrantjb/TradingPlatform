"""Unsupported session safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class UnsupportedSessionFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "unsupported_session_filter"
    required_inputs = ("session_phase",)
    supported_sessions = ("opening", "morning", "midday", "afternoon")

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        session_phase = str(snapshot.session_phase).lower()
        observed = {"sessionPhase": session_phase}
        threshold = {"supportedSessions": self.supported_sessions}
        if session_phase not in self.supported_sessions:
            return block_evidence(reason_code="meta_strategy.safety.unsupported_session.blocked", observed=observed, threshold=threshold, existing_position_action="ALLOW_MANAGE")
        return pass_evidence(reason_code="meta_strategy.safety.unsupported_session.pass", observed=observed, threshold=threshold)
