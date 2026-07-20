"""Meta-Strategy backtest engine using the runtime execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from backend.app.algorithms.meta_strategy.backtest.diagnostics import MetaStrategyBacktestDiagnostics, build_backtest_diagnostics
from backend.app.algorithms.meta_strategy.backtest.execution_simulator import (
    MetaStrategySimulatedAccountSnapshot,
    MetaStrategySimulatedBrokerAdapter,
    MetaStrategySimulationConfig,
)
from backend.app.algorithms.meta_strategy.backtest.ledger import MetaStrategyBacktestLedger, ledger_from_pipeline_results
from backend.app.algorithms.meta_strategy.backtest.metrics import MetaStrategyBacktestMetrics, calculate_backtest_metrics
from backend.app.algorithms.meta_strategy.backtest.reports import MetaStrategyBacktestReport, build_backtest_report
from backend.app.algorithms.meta_strategy.backtest.runtime_parity import MetaStrategyRuntimeParityReport, assert_backtest_runtime_parity
from backend.app.algorithms.meta_strategy.backtest.walk_forward import MetaStrategyArtifactTimeline
from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineConfig
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineRequest,
    MetaStrategyExecutionPipelineResult,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest


@dataclass(frozen=True)
class MetaStrategyBacktestRequest:
    decision_requests: tuple[MetaStrategyMarketSnapshotRequest, ...]
    account_snapshot: MetaStrategySimulatedAccountSnapshot = MetaStrategySimulatedAccountSnapshot()
    simulation_config: MetaStrategySimulationConfig = MetaStrategySimulationConfig()
    model_artifacts: tuple[dict[str, Any], ...] = ()
    inference_config: MetaStrategyInferenceConfig = MetaStrategyInferenceConfig(mode="OFF")


@dataclass(frozen=True)
class MetaStrategyBacktestResult:
    algorithm_id: str
    decisions: tuple[MetaStrategyExecutionPipelineResult, ...]
    ledger: MetaStrategyBacktestLedger
    metrics: MetaStrategyBacktestMetrics
    diagnostics: MetaStrategyBacktestDiagnostics
    runtime_parity: MetaStrategyRuntimeParityReport
    report: MetaStrategyBacktestReport


def run_meta_strategy_backtest(request: MetaStrategyBacktestRequest) -> MetaStrategyBacktestResult:
    parity = assert_backtest_runtime_parity()
    artifact_timeline = MetaStrategyArtifactTimeline(request.model_artifacts)
    broker = MetaStrategySimulatedBrokerAdapter(request.simulation_config)
    decisions: list[MetaStrategyExecutionPipelineResult] = []
    missing_artifacts = 0
    for snapshot_request in request.decision_requests:
        _reject_same_candle_lookahead(snapshot_request)
        artifact = artifact_timeline.artifact_for(snapshot_request.decision_timestamp)
        if request.model_artifacts and artifact is None:
            missing_artifacts += 1
        decisions.append(
            run_meta_strategy_execution_pipeline(
                MetaStrategyExecutionPipelineRequest(
                    mode="BACKTEST",
                    snapshot_request=snapshot_request,
                    model_artifact=artifact,
                    account_equity=request.account_snapshot.account_equity,
                    available_buying_power=request.account_snapshot.buying_power,
                    remaining_algorithm_risk=request.account_snapshot.remaining_algorithm_risk,
                    global_available_risk=request.account_snapshot.global_available_risk,
                    global_quantity_cap=request.account_snapshot.global_quantity_cap,
                ),
                config=MetaStrategyExecutionPipelineConfig(inference_config=request.inference_config),
                broker_adapter=broker,
            )
        )
    decision_tuple = tuple(decisions)
    ledger = ledger_from_pipeline_results(decision_tuple, fee_per_share=request.simulation_config.fee_per_share)
    metrics = calculate_backtest_metrics(ledger)
    diagnostics = build_backtest_diagnostics(
        decision_count=len(decision_tuple),
        artifact_missing_count=missing_artifacts,
        lookahead_violation_count=0,
        spread_bps=request.simulation_config.spread_bps,
        slippage_bps=request.simulation_config.slippage_bps,
        fee_per_share=request.simulation_config.fee_per_share,
    )
    report = build_backtest_report(metrics=metrics, diagnostics=diagnostics, runtime_parity=parity, ledger=ledger)
    return MetaStrategyBacktestResult(
        algorithm_id=ALGORITHM_ID,
        decisions=decision_tuple,
        ledger=ledger,
        metrics=metrics,
        diagnostics=diagnostics,
        runtime_parity=parity,
        report=report,
    )


def _reject_same_candle_lookahead(request: MetaStrategyMarketSnapshotRequest) -> None:
    decision_time = request.decision_timestamp.astimezone(UTC)
    for timeframe, rows in (
        ("1m", request.one_minute_candles),
        ("5m", request.five_minute_candles),
        ("15m", request.fifteen_minute_candles),
    ):
        if any(row.timestamp.astimezone(UTC) >= decision_time for row in rows):
            raise ValueError(f"same-candle lookahead is prohibited for {timeframe} candles")


__all__ = [
    "MetaStrategyBacktestRequest",
    "MetaStrategyBacktestResult",
    "run_meta_strategy_backtest",
]
