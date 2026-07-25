"""Bounded priority queues for Voting Ensemble runtime commands."""

from __future__ import annotations

from collections import deque
from threading import Condition

from backend.app.algorithms.voting_ensemble.runtime.commands import VotingEnsembleRuntimeCommand


VOTING_ENSEMBLE_QUEUE_VERSION = "voting_ensemble_runtime_queue_v1"


class VotingEnsembleBackpressureError(RuntimeError):
    pass


class VotingEnsemblePriorityQueue:
    def __init__(self, *, high_watermark: int = 128, low_watermark: int = 32) -> None:
        self.high_watermark = high_watermark
        self.low_watermark = low_watermark
        self._high: deque[VotingEnsembleRuntimeCommand] = deque()
        self._low: deque[VotingEnsembleRuntimeCommand] = deque()
        self._condition = Condition()

    def enqueue(self, command: VotingEnsembleRuntimeCommand) -> None:
        with self._condition:
            target = self._high if command.priority == "high" else self._low
            limit = self.high_watermark if command.priority == "high" else self.low_watermark
            if len(target) >= limit:
                raise VotingEnsembleBackpressureError(f"Voting Ensemble {command.priority}-priority queue is full")
            target.append(command)
            self._condition.notify()

    def pop(self, *, timeout: float | None = None) -> VotingEnsembleRuntimeCommand | None:
        with self._condition:
            if not self._high and not self._low:
                self._condition.wait(timeout=timeout)
            if self._high:
                return self._high.popleft()
            if self._low:
                return self._low.popleft()
            return None

    def snapshot(self) -> dict[str, int | str]:
        with self._condition:
            return {
                "queueVersion": VOTING_ENSEMBLE_QUEUE_VERSION,
                "highPriorityDepth": len(self._high),
                "lowPriorityDepth": len(self._low),
                "highPriorityCapacity": self.high_watermark,
                "lowPriorityCapacity": self.low_watermark,
            }
