"""Backtest report assembly."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.backtest.diagnostics import MetaStrategyBacktestDiagnostics
from backend.app.algorithms.meta_strategy.backtest.ledger import MetaStrategyBacktestLedger
from backend.app.algorithms.meta_strategy.backtest.metrics import MetaStrategyBacktestMetrics
from backend.app.algorithms.meta_strategy.backtest.runtime_parity import MetaStrategyRuntimeParityReport
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyBacktestReport:
    algorithm_id: str
    metrics: MetaStrategyBacktestMetrics
    diagnostics: MetaStrategyBacktestDiagnostics
    runtime_parity: MetaStrategyRuntimeParityReport
    ledger: MetaStrategyBacktestLedger

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_backtest_report(
    *,
    metrics: MetaStrategyBacktestMetrics,
    diagnostics: MetaStrategyBacktestDiagnostics,
    runtime_parity: MetaStrategyRuntimeParityReport,
    ledger: MetaStrategyBacktestLedger,
) -> MetaStrategyBacktestReport:
    return MetaStrategyBacktestReport(
        algorithm_id=ALGORITHM_ID,
        metrics=metrics,
        diagnostics=diagnostics,
        runtime_parity=runtime_parity,
        ledger=ledger,
    )


__all__ = ["MetaStrategyBacktestReport", "build_backtest_report"]
