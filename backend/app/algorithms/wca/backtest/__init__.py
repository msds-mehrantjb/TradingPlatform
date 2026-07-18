"""WCA backtest namespace and executable inventory."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.wca.backtest.engine import WCA_BACKTEST_ENGINE_VERSION, run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.contracts import BacktestResult, BacktestRunConfiguration, BacktestTrade, WcaBacktestRequest, WcaBacktestSuiteResult


@dataclass(frozen=True)
class WcaBacktestResponsibility:
    responsibility_id: str
    owner_file: str
    responsibility: str


WCA_BACKTEST_FILE_INVENTORY = (
    "__init__.py",
    "engine.py",
    "execution.py",
    "ledger.py",
    "metrics.py",
    "reports.py",
    "walk_forward.py",
)

WCA_BACKTEST_INVENTORY: tuple[WcaBacktestResponsibility, ...] = (
    WcaBacktestResponsibility("wca_replay_orchestration", "engine.py", "Orchestrate WCA historical replay through the backend decision pipeline."),
    WcaBacktestResponsibility("point_in_time_snapshots", "engine.py", "Build immutable WCA market snapshots using only completed information available at bar t."),
    WcaBacktestResponsibility("signal_generation", "engine.py", "Generate WCA signals through the production strategy, confidence, weighting, profile, and aggregation pipeline."),
    WcaBacktestResponsibility("next_bar_execution", "engine.py", "Fill eligible proposals no earlier than the next bar open to avoid same-candle signal/fill bias."),
    WcaBacktestResponsibility("fill_simulation", "execution.py", "Model WCA simulated fills from approved backend order proposals."),
    WcaBacktestResponsibility("slippage_and_trading_costs", "execution.py", "Apply WCA slippage, fees, spread, and market-impact assumptions."),
    WcaBacktestResponsibility("partial_fill_simulation", "execution.py", "Bound WCA fills by volume participation and record partial or unfilled orders."),
    WcaBacktestResponsibility("wca_position_ledger", "ledger.py", "Maintain WCA backtest position state and mark-to-market equity history."),
    WcaBacktestResponsibility("wca_trade_ledger", "ledger.py", "Maintain WCA-attributed backtest trades and realized PnL."),
    WcaBacktestResponsibility("wca_metrics", "metrics.py", "Produce WCA aggregate performance metrics and execution quality measurements."),
    WcaBacktestResponsibility("rolling_diagnostics", "metrics.py", "Produce WCA rolling diagnostics, breakdowns, and rejected-signal evidence."),
    WcaBacktestResponsibility("walk_forward_testing", "walk_forward.py", "Run chronological WCA walk-forward windows without holdout leakage."),
    WcaBacktestResponsibility("untouched_holdout_testing", "walk_forward.py", "Reserve untouched WCA holdout data from configuration selection and optimization."),
    WcaBacktestResponsibility("wca_reports", "reports.py", "Expose WCA backend-authoritative backtest reports."),
    WcaBacktestResponsibility("baseline_comparison", "reports.py", "Compare WCA results with baseline alternatives using identical datasets and execution assumptions."),
)

WCA_BACKTEST_RESPONSIBILITY_IDS = frozenset(row.responsibility_id for row in WCA_BACKTEST_INVENTORY)

__all__ = [
    "WCA_BACKTEST_ENGINE_VERSION",
    "WCA_BACKTEST_FILE_INVENTORY",
    "WCA_BACKTEST_INVENTORY",
    "WCA_BACKTEST_RESPONSIBILITY_IDS",
    "BacktestResult",
    "BacktestRunConfiguration",
    "BacktestTrade",
    "WcaBacktestResponsibility",
    "WcaBacktestRequest",
    "WcaBacktestSuiteResult",
    "run_wca_backtest",
    "run_wca_backtest_modes",
]
