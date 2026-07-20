"""Economic event context."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ContextSnapshotStrategy


class EconomicEventContextStrategy(ContextSnapshotStrategy):
    strategy_id = "economic_event_context"
    required_inputs = ("economic_event_state", "session_phase", "spread")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        state = str(snapshot.economic_event_state.get("state") or "none").lower()
        severity = str(snapshot.economic_event_state.get("severity") or "none").lower()
        minutes_to_event = snapshot.economic_event_state.get("minutesToEvent")
        spread_bps = float(snapshot.spread.get("basisPoints") or snapshot.spread_bps or 0.0)
        active = bool(snapshot.economic_event_state.get("active") or state in {"active", "blocked", "halt"})
        return {
            "eventState": state,
            "eventSeverity": severity,
            "minutesToEvent": minutes_to_event,
            "sessionPhase": snapshot.session_phase,
            "spreadBasisPoints": round(spread_bps, 6),
            "eventActive": active,
        }

    def adjustments(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> dict[str, float]:
        state = str(evidence["eventState"])
        severity = str(evidence["eventSeverity"])
        spread_bps = float(evidence["spreadBasisPoints"])
        near_event = evidence["minutesToEvent"] is not None and abs(float(evidence["minutesToEvent"])) <= 15.0
        blocked = state in {"blocked", "halt"} or severity in {"high", "critical"} and near_event
        event_penalty = -0.75 if blocked else -0.25 if evidence["eventActive"] or near_event else 0.0
        spread_penalty = -0.15 if spread_bps >= 12.0 else 0.0
        penalty = event_penalty + spread_penalty
        return {
            "eligibilityAdjustment": penalty,
            "confidenceAdjustment": penalty * 0.25,
            "familyWeightMultiplier": 1.0 + penalty * 0.4,
            "candidateQualityAdjustment": penalty * 0.2,
            "profileSelectionBias": penalty,
        }
