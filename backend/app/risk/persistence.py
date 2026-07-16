from __future__ import annotations

from threading import RLock

from backend.app.risk.types import GlobalGateDecision


class InMemoryGlobalRiskDecisionStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._decisions: dict[str, GlobalGateDecision] = {}

    def record(self, decision_id: str, decision: GlobalGateDecision) -> None:
        with self._lock:
            self._decisions[decision_id] = decision

    def get(self, decision_id: str) -> GlobalGateDecision | None:
        with self._lock:
            return self._decisions.get(decision_id)


__all__ = ["InMemoryGlobalRiskDecisionStore"]
