"""Session backtest and replay parity interfaces."""

from backend.app.algorithms.session.backtest.engine import (
    SESSION_RUNTIME_PARITY_VERSION,
    SessionBacktestEngine,
    SessionBacktestExecutionConfig,
    SessionBacktestExecutionResult,
    SessionRuntimeMode,
    run_session_backtest,
    run_session_event_stream,
)
from backend.app.algorithms.session.backtest.result import (
    SessionRuntimeDecisionSnapshot,
    SessionRuntimeParityResult,
    compare_session_runtime_parity,
)

__all__ = [
    "SESSION_RUNTIME_PARITY_VERSION",
    "SessionBacktestEngine",
    "SessionBacktestExecutionConfig",
    "SessionBacktestExecutionResult",
    "SessionRuntimeDecisionSnapshot",
    "SessionRuntimeMode",
    "SessionRuntimeParityResult",
    "compare_session_runtime_parity",
    "run_session_backtest",
    "run_session_event_stream",
]
