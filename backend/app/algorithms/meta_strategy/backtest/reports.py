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
    breakdowns: dict[str, dict[str, dict[str, float | int]]]

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
        breakdowns=_breakdowns(ledger),
    )


def _breakdowns(ledger: MetaStrategyBacktestLedger) -> dict[str, dict[str, dict[str, float | int]]]:
    return {
        "strategy": _breakdown_by(ledger, "strategy_ids"),
        "family": _breakdown_by(ledger, "families"),
        "regime": _breakdown_by(ledger, "regime"),
        "session": _breakdown_by(ledger, "session"),
        "marketCondition": _breakdown_by(ledger, "market_condition"),
    }


def _breakdown_by(ledger: MetaStrategyBacktestLedger, field: str) -> dict[str, dict[str, float | int]]:
    rows: dict[str, dict[str, float | int]] = {}
    for no_trade in ledger.no_trades:
        values = getattr(no_trade, field)
        labels = values if isinstance(values, tuple) else (str(values),)
        for label in labels or ("UNKNOWN",):
            bucket = rows.setdefault(str(label), {"trades": 0, "noTrades": 0, "netPnl": 0.0})
            bucket["noTrades"] = int(bucket["noTrades"]) + 1
    for trade in ledger.trades:
        label = getattr(trade, field, None)
        labels = label if isinstance(label, tuple) else (str(label or "UNKNOWN"),)
        for item in labels or ("UNKNOWN",):
            bucket = rows.setdefault(str(item), {"trades": 0, "noTrades": 0, "netPnl": 0.0})
            bucket["trades"] = int(bucket["trades"]) + 1
            bucket["netPnl"] = float(bucket["netPnl"]) + trade.net_pnl
    return rows


__all__ = ["MetaStrategyBacktestReport", "build_backtest_report"]
