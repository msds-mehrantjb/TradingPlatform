"""Meta-Strategy backtest engine using the runtime execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest, MetaStrategySnapshotCandle


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
    for snapshot_request in _event_ordered_requests(request.decision_requests):
        snapshot_request = _with_derived_higher_timeframes(snapshot_request)
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
    ledger = ledger_from_pipeline_results(
        decision_tuple,
        fee_per_share=request.simulation_config.fee_per_share,
        regulatory_fee_per_share=request.simulation_config.regulatory_fee_per_share,
    )
    metrics = calculate_backtest_metrics(ledger)
    diagnostics = build_backtest_diagnostics(
        decision_count=len(decision_tuple),
        artifact_missing_count=missing_artifacts,
        lookahead_violation_count=0,
        spread_bps=request.simulation_config.spread_bps,
        slippage_bps=request.simulation_config.slippage_bps,
        fee_per_share=request.simulation_config.fee_per_share,
        regulatory_fee_per_share=request.simulation_config.regulatory_fee_per_share,
        order_delay_seconds=request.simulation_config.order_delay_seconds,
        limit_order_fill_probability=request.simulation_config.limit_order_fill_probability,
        finalized_one_minute_events=True,
        derived_higher_timeframes=True,
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


def _event_ordered_requests(requests: tuple[MetaStrategyMarketSnapshotRequest, ...]) -> tuple[MetaStrategyMarketSnapshotRequest, ...]:
    ordered = tuple(sorted(requests, key=lambda item: item.decision_timestamp.astimezone(UTC)))
    if tuple(item.decision_timestamp for item in ordered) != tuple(item.decision_timestamp for item in requests):
        raise ValueError("backtest finalized one-minute events must be chronological")
    return ordered


def _with_derived_higher_timeframes(request: MetaStrategyMarketSnapshotRequest) -> MetaStrategyMarketSnapshotRequest:
    updates: dict[str, tuple[MetaStrategySnapshotCandle, ...]] = {}
    if not request.five_minute_candles:
        updates["five_minute_candles"] = _derive_complete_candles(request.one_minute_candles, minutes=5, end=request.decision_timestamp)
    if not request.fifteen_minute_candles:
        updates["fifteen_minute_candles"] = _derive_complete_candles(request.one_minute_candles, minutes=15, end=request.decision_timestamp)
    return request.model_copy(update=updates) if updates else request


def _derive_complete_candles(
    candles: tuple[MetaStrategySnapshotCandle, ...],
    *,
    minutes: int,
    end: datetime,
) -> tuple[MetaStrategySnapshotCandle, ...]:
    eligible = tuple(row for row in candles if row.timestamp.astimezone(UTC) + timedelta(minutes=1) <= end.astimezone(UTC))
    if len(eligible) < minutes:
        return ()
    chunks = []
    for offset in range(0, len(eligible), minutes):
        chunk = eligible[offset : offset + minutes]
        if len(chunk) != minutes:
            continue
        chunks.append(
            MetaStrategySnapshotCandle(
                timestamp=chunk[-1].timestamp,
                open=chunk[0].open,
                high=max(row.high for row in chunk),
                low=min(row.low for row in chunk),
                close=chunk[-1].close,
                volume=sum(row.volume for row in chunk),
                symbol=chunk[-1].symbol,
                timeframe=f"{minutes}Min",
                provider=chunk[-1].provider,
            )
        )
    return tuple(chunks)


__all__ = [
    "MetaStrategyBacktestRequest",
    "MetaStrategyBacktestResult",
    "run_meta_strategy_backtest",
]
