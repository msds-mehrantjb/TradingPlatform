"""Meta-Strategy dedicated backtesting package."""

from backend.app.algorithms.meta_strategy.backtest.diagnostics import MetaStrategyBacktestDiagnostics, build_backtest_diagnostics
from backend.app.algorithms.meta_strategy.backtest.engine import MetaStrategyBacktestRequest, MetaStrategyBacktestResult, run_meta_strategy_backtest
from backend.app.algorithms.meta_strategy.backtest.execution_simulator import (
    MetaStrategySimulatedAccountSnapshot,
    MetaStrategySimulatedBrokerAdapter,
    MetaStrategySimulationConfig,
)
from backend.app.algorithms.meta_strategy.backtest.holdout import MetaStrategyHoldoutWindow, validate_holdout_window
from backend.app.algorithms.meta_strategy.backtest.ledger import MetaStrategyBacktestLedger, MetaStrategyBacktestTrade, ledger_from_pipeline_results
from backend.app.algorithms.meta_strategy.backtest.metrics import MetaStrategyBacktestMetrics, calculate_backtest_metrics
from backend.app.algorithms.meta_strategy.backtest.reports import MetaStrategyBacktestReport, build_backtest_report
from backend.app.algorithms.meta_strategy.backtest.runtime_parity import (
    BACKTEST_REPLACES_ONLY_RUNTIME_BOUNDARIES,
    BACKTEST_RUNTIME_PIPELINE_ENTRYPOINT,
    MetaStrategyRuntimeParityReport,
    assert_backtest_runtime_parity,
)
from backend.app.algorithms.meta_strategy.backtest.walk_forward import MetaStrategyArtifactTimeline, select_point_in_time_artifact
from backend.app.algorithms.meta_strategy.backtest.comparisons import (
    ComparisonScenario,
    ComparisonScope,
    MetaStrategyBacktestComparison,
    MetaStrategyBacktestComparisonReport,
    MetaStrategyBacktestComparisonRequest,
    MetaStrategyComparisonMetrics,
    build_backtest_comparison_report,
    build_holdout_comparison_report,
    build_walk_forward_comparison_report,
)

__all__ = [
    "BACKTEST_REPLACES_ONLY_RUNTIME_BOUNDARIES",
    "BACKTEST_RUNTIME_PIPELINE_ENTRYPOINT",
    "ComparisonScenario",
    "ComparisonScope",
    "MetaStrategyArtifactTimeline",
    "MetaStrategyBacktestComparison",
    "MetaStrategyBacktestComparisonReport",
    "MetaStrategyBacktestComparisonRequest",
    "MetaStrategyBacktestDiagnostics",
    "MetaStrategyBacktestLedger",
    "MetaStrategyBacktestMetrics",
    "MetaStrategyBacktestReport",
    "MetaStrategyBacktestRequest",
    "MetaStrategyBacktestResult",
    "MetaStrategyBacktestTrade",
    "MetaStrategyHoldoutWindow",
    "MetaStrategyRuntimeParityReport",
    "MetaStrategySimulatedAccountSnapshot",
    "MetaStrategySimulatedBrokerAdapter",
    "MetaStrategySimulationConfig",
    "MetaStrategyComparisonMetrics",
    "assert_backtest_runtime_parity",
    "build_backtest_comparison_report",
    "build_backtest_diagnostics",
    "build_holdout_comparison_report",
    "build_backtest_report",
    "build_walk_forward_comparison_report",
    "calculate_backtest_metrics",
    "ledger_from_pipeline_results",
    "run_meta_strategy_backtest",
    "select_point_in_time_artifact",
    "validate_holdout_window",
]
