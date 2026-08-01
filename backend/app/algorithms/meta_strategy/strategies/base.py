"""Base contracts for Meta-Strategy-owned snapshot-only strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext


@dataclass(frozen=True)
class SnapshotEvaluationResult:
    strategy_id: str
    signal: str
    confidence: float
    eligible: bool
    strategy_version: str = "meta_strategy_strategy_v1"
    required_inputs: tuple[str, ...] = ()
    minimum_warmup: int = 0
    supported_directions: tuple[str, ...] = ("BUY", "SELL", "HOLD")
    entry_reference: float | None = None
    invalidation_reference: float | None = None
    suggested_stop_reference: float | None = None
    settings_version: str = "meta_strategy_settings_v1"
    effective_settings_hash: str = "meta_strategy_settings_unresolved"
    family: str = "UNKNOWN"
    evidence: dict[str, object] | None = None
    required_input_status: dict[str, bool] | None = None
    reason_codes: tuple[str, ...] = ()


class MetaStrategySnapshotOnlyStrategy(Protocol):
    strategy_id: str

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> SnapshotEvaluationResult:
        ...


def hold_result(
    strategy_id: str,
    reason_code: str,
    *,
    family: str = "UNKNOWN",
    settings_version: str = "meta_strategy_settings_v1",
    effective_settings_hash: str = "meta_strategy_settings_unresolved",
    evidence: dict[str, object] | None = None,
    required_input_status: dict[str, bool] | None = None,
) -> SnapshotEvaluationResult:
    return SnapshotEvaluationResult(
        strategy_id=strategy_id,
        signal="HOLD",
        confidence=0.0,
        eligible=False,
        settings_version=settings_version,
        effective_settings_hash=effective_settings_hash,
        family=family,
        evidence=evidence or {},
        required_input_status=required_input_status or {},
        reason_codes=(reason_code,),
    )
