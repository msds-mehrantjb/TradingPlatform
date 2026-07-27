"""Unsupported session safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.session import MetaStrategySession, canonical_session
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class UnsupportedSessionFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "unsupported_session_filter"
    required_inputs = ("session_phase",)
    supported_sessions = (
        MetaStrategySession.OPENING.value,
        MetaStrategySession.MORNING.value,
        MetaStrategySession.MIDDAY.value,
        MetaStrategySession.AFTERNOON.value,
    )

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        snapshot = context_market_snapshot(value)
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        try:
            session_phase = canonical_session(snapshot.session_phase).value
        except ValueError:
            session_phase = MetaStrategySession.CLOSED.value
        observed = {"sessionPhase": session_phase}
        threshold = {"supportedSessions": self.supported_sessions}
        if session_phase not in self.supported_sessions:
            return block_evidence(reason_code="meta_strategy.safety.unsupported_session.blocked", observed=observed, threshold=threshold, existing_position_action="ALLOW_MANAGE")
        return pass_evidence(reason_code="meta_strategy.safety.unsupported_session.pass", observed=observed, threshold=threshold)
