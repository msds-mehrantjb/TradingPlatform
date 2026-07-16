"""WCA backtest namespace."""

from backend.app.algorithms.wca.backtest.engine import WCA_BACKTEST_ENGINE_VERSION, run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.contracts import BacktestResult, BacktestRunConfiguration, BacktestTrade, WcaBacktestRequest, WcaBacktestSuiteResult

__all__ = [
    "WCA_BACKTEST_ENGINE_VERSION",
    "BacktestResult",
    "BacktestRunConfiguration",
    "BacktestTrade",
    "WcaBacktestRequest",
    "WcaBacktestSuiteResult",
    "run_wca_backtest",
    "run_wca_backtest_modes",
]
