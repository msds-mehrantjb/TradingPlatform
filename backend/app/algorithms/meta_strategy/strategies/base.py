"""Base contracts for Meta-Strategy-owned snapshot-only strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot


@dataclass(frozen=True)
class SnapshotEvaluationResult:
    strategy_id: str
    signal: str
    confidence: float
    eligible: bool
    family: str = "UNKNOWN"
    evidence: dict[str, object] | None = None
    required_input_status: dict[str, bool] | None = None
    reason_codes: tuple[str, ...] = ()


class MetaStrategySnapshotOnlyStrategy(Protocol):
    strategy_id: str

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> SnapshotEvaluationResult:
        ...


def hold_result(
    strategy_id: str,
    reason_code: str,
    *,
    family: str = "UNKNOWN",
    evidence: dict[str, object] | None = None,
    required_input_status: dict[str, bool] | None = None,
) -> SnapshotEvaluationResult:
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.0,
        eligible=False,
        family=family,
        evidence=evidence or {},
        required_input_status=required_input_status or {},
        reason_codes=(reason_code,),
    )
