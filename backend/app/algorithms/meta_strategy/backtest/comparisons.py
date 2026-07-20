"""Backtest comparison reports for Meta-Strategy variants and references."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from backend.app.algorithms.meta_strategy.backtest.engine import MetaStrategyBacktestRequest, MetaStrategyBacktestResult, run_meta_strategy_backtest
from backend.app.algorithms.meta_strategy.backtest.execution_simulator import (
    MetaStrategySimulatedAccountSnapshot,
    MetaStrategySimulationConfig,
)
from backend.app.algorithms.meta_strategy.backtest.ledger import MetaStrategyBacktestLedger, MetaStrategyBacktestTrade
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest, build_meta_strategy_market_snapshot
from backend.app.algorithms.meta_strategy.versions import meta_strategy_version_identifiers


ComparisonScenario = Literal[
    "DETERMINISTIC_META_STRATEGY",
    "ML_SHADOW",
    "ML_FILTER",
    "ML_RISK_REDUCTION",
    "NO_TRADE_BASELINE",
    "BUY_AND_HOLD_REFERENCE",
]
ComparisonScope = Literal["WALK_FORWARD", "HOLDOUT"]


@dataclass(frozen=True)
class MetaStrategyComparisonMetrics:
    algorithm_id: str
    scenario: ComparisonScenario
    net_pnl: float
    expectancy: float
    drawdown: float
    profit_factor: float
    coverage: float
    acceptance_rate: float
    rejection_rate: float
    performance_by_side: dict[str, float]
    performance_by_regime: dict[str, float]
    performance_by_probability_bucket: dict[str, float]
    calibration: dict[str, float]
    cost_sensitivity: dict[str, float]


@dataclass(frozen=True)
class MetaStrategyBacktestComparison:
    algorithm_id: str
    scenario: ComparisonScenario
    metrics: MetaStrategyComparisonMetrics
    backtest_result: MetaStrategyBacktestResult | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MetaStrategyBacktestComparisonRequest:
    decision_requests: tuple[MetaStrategyMarketSnapshotRequest, ...]
    model_artifacts: tuple[dict[str, Any], ...] = ()
    account_snapshot: MetaStrategySimulatedAccountSnapshot = MetaStrategySimulatedAccountSnapshot()
    simulation_config: MetaStrategySimulationConfig = MetaStrategySimulationConfig()
    cost_sensitivity_multipliers: tuple[float, ...] = (0.0, 1.0, 2.0)


@dataclass(frozen=True)
class MetaStrategyBacktestComparisonReport:
    algorithm_id: str
    scope: ComparisonScope
    comparisons: tuple[MetaStrategyBacktestComparison, ...]
    versions: dict[str, str]
    artifact_manifest: tuple[dict[str, str], ...]
    report_hash: str
    reproducible: bool
    runtime_parity_passed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_walk_forward_comparison_report(request: MetaStrategyBacktestComparisonRequest) -> MetaStrategyBacktestComparisonReport:
    return build_backtest_comparison_report(request, scope="WALK_FORWARD")


def build_holdout_comparison_report(request: MetaStrategyBacktestComparisonRequest) -> MetaStrategyBacktestComparisonReport:
    return build_backtest_comparison_report(request, scope="HOLDOUT")


def build_backtest_comparison_report(
    request: MetaStrategyBacktestComparisonRequest,
    *,
    scope: ComparisonScope = "WALK_FORWARD",
) -> MetaStrategyBacktestComparisonReport:
    comparisons = (
        _pipeline_comparison("DETERMINISTIC_META_STRATEGY", request, MetaStrategyInferenceConfig(mode="OFF")),
        _pipeline_comparison("ML_SHADOW", request, MetaStrategyInferenceConfig(mode="SHADOW")),
        _pipeline_comparison("ML_FILTER", request, MetaStrategyInferenceConfig(mode="FILTER")),
        _pipeline_comparison("ML_RISK_REDUCTION", request, MetaStrategyInferenceConfig(mode="RISK_REDUCTION")),
        _no_trade_baseline(request),
        _buy_and_hold_reference(request),
    )
    versions = meta_strategy_version_identifiers()
    artifact_manifest = _artifact_manifest(request.model_artifacts)
    report_hash = _report_hash(scope, comparisons, versions, artifact_manifest)
    return MetaStrategyBacktestComparisonReport(
        algorithm_id=ALGORITHM_ID,
        scope=scope,
        comparisons=comparisons,
        versions=versions,
        artifact_manifest=artifact_manifest,
        report_hash=report_hash,
        reproducible=True,
        runtime_parity_passed=all(comparison.backtest_result is None or comparison.backtest_result.runtime_parity.passed for comparison in comparisons),
    )


def _pipeline_comparison(
    scenario: ComparisonScenario,
    request: MetaStrategyBacktestComparisonRequest,
    inference_config: MetaStrategyInferenceConfig,
) -> MetaStrategyBacktestComparison:
    result = run_meta_strategy_backtest(
        MetaStrategyBacktestRequest(
            decision_requests=request.decision_requests,
            account_snapshot=request.account_snapshot,
            simulation_config=request.simulation_config,
            model_artifacts=request.model_artifacts,
            inference_config=inference_config,
        )
    )
    return MetaStrategyBacktestComparison(
        algorithm_id=ALGORITHM_ID,
        scenario=scenario,
        metrics=_metrics_from_result(scenario, result, request),
        backtest_result=result,
        reason_codes=("meta_strategy.backtest.comparison.pipeline_scenario",),
    )


def _no_trade_baseline(request: MetaStrategyBacktestComparisonRequest) -> MetaStrategyBacktestComparison:
    metrics = MetaStrategyComparisonMetrics(
        algorithm_id=ALGORITHM_ID,
        scenario="NO_TRADE_BASELINE",
        net_pnl=0.0,
        expectancy=0.0,
        drawdown=0.0,
        profit_factor=0.0,
        coverage=0.0,
        acceptance_rate=0.0,
        rejection_rate=1.0 if request.decision_requests else 0.0,
        performance_by_side={"BUY": 0.0, "SELL": 0.0},
        performance_by_regime={},
        performance_by_probability_bucket={},
        calibration={"sampleCount": 0.0, "averagePredicted": 0.0, "observedWinRate": 0.0, "brierScore": 0.0},
        cost_sensitivity={f"costMultiplier:{multiplier:g}": 0.0 for multiplier in request.cost_sensitivity_multipliers},
    )
    return MetaStrategyBacktestComparison(
        algorithm_id=ALGORITHM_ID,
        scenario="NO_TRADE_BASELINE",
        metrics=metrics,
        backtest_result=None,
        reason_codes=("meta_strategy.backtest.comparison.no_trade_baseline",),
    )


def _buy_and_hold_reference(request: MetaStrategyBacktestComparisonRequest) -> MetaStrategyBacktestComparison:
    if not request.decision_requests:
        pnl = 0.0
    else:
        first = build_meta_strategy_market_snapshot(request.decision_requests[0])
        last = build_meta_strategy_market_snapshot(request.decision_requests[-1])
        quantity = max(0, int(request.account_snapshot.buying_power // first.last_price))
        pnl = (last.last_price - first.last_price) * quantity
    metrics = MetaStrategyComparisonMetrics(
        algorithm_id=ALGORITHM_ID,
        scenario="BUY_AND_HOLD_REFERENCE",
        net_pnl=pnl,
        expectancy=pnl,
        drawdown=max(0.0, -pnl),
        profit_factor=float("inf") if pnl > 0 else 0.0,
        coverage=1.0 if request.decision_requests else 0.0,
        acceptance_rate=1.0 if request.decision_requests else 0.0,
        rejection_rate=0.0,
        performance_by_side={"BUY": pnl, "SELL": 0.0},
        performance_by_regime={"buy_and_hold_reference": pnl},
        performance_by_probability_bucket={},
        calibration={"sampleCount": 0.0, "averagePredicted": 0.0, "observedWinRate": 0.0, "brierScore": 0.0},
        cost_sensitivity={f"costMultiplier:{multiplier:g}": pnl for multiplier in request.cost_sensitivity_multipliers},
    )
    return MetaStrategyBacktestComparison(
        algorithm_id=ALGORITHM_ID,
        scenario="BUY_AND_HOLD_REFERENCE",
        metrics=metrics,
        backtest_result=None,
        reason_codes=("meta_strategy.backtest.comparison.buy_and_hold_reference",),
    )


def _metrics_from_result(
    scenario: ComparisonScenario,
    result: MetaStrategyBacktestResult,
    request: MetaStrategyBacktestComparisonRequest,
) -> MetaStrategyComparisonMetrics:
    ledger = result.ledger
    trade_count = len(ledger.trades)
    decision_count = len(result.decisions)
    accepted = sum(1 for decision in result.decisions if decision.order_intent is not None)
    return MetaStrategyComparisonMetrics(
        algorithm_id=ALGORITHM_ID,
        scenario=scenario,
        net_pnl=float(ledger.net_pnl),
        expectancy=float(ledger.net_pnl / trade_count) if trade_count else 0.0,
        drawdown=_max_drawdown(ledger.trades),
        profit_factor=float(result.metrics.profit_factor),
        coverage=float(accepted / decision_count) if decision_count else 0.0,
        acceptance_rate=float(accepted / decision_count) if decision_count else 0.0,
        rejection_rate=float((decision_count - accepted) / decision_count) if decision_count else 0.0,
        performance_by_side=_performance_by_side(ledger),
        performance_by_regime=_performance_by_regime(result),
        performance_by_probability_bucket=_performance_by_probability_bucket(result),
        calibration=_calibration(result),
        cost_sensitivity=_cost_sensitivity(ledger, request.cost_sensitivity_multipliers),
    )


def _max_drawdown(trades: tuple[MetaStrategyBacktestTrade, ...]) -> float:
    peak = 0.0
    equity = 0.0
    drawdown = 0.0
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _performance_by_side(ledger: MetaStrategyBacktestLedger) -> dict[str, float]:
    values = {"BUY": 0.0, "SELL": 0.0}
    for trade in ledger.trades:
        values[trade.side] = float(values.get(trade.side, 0.0) + trade.net_pnl)
    return values


def _performance_by_regime(result: MetaStrategyBacktestResult) -> dict[str, float]:
    pnl_by_decision = {trade.decision_id: trade.net_pnl for trade in result.ledger.trades}
    values: dict[str, float] = {}
    for decision in result.decisions:
        regime = str(decision.snapshot.session_phase or "unknown")
        values[regime] = float(values.get(regime, 0.0) + pnl_by_decision.get(decision.snapshot.decision_id, 0.0))
    return values


def _performance_by_probability_bucket(result: MetaStrategyBacktestResult) -> dict[str, float]:
    pnl_by_decision = {trade.decision_id: trade.net_pnl for trade in result.ledger.trades}
    values: dict[str, float] = {}
    for decision in result.decisions:
        probability = decision.inference.calibratedProbability or decision.inference.probabilityOfSuccess or 0.0
        bucket = _probability_bucket(probability)
        values[bucket] = float(values.get(bucket, 0.0) + pnl_by_decision.get(decision.snapshot.decision_id, 0.0))
    return values


def _calibration(result: MetaStrategyBacktestResult) -> dict[str, float]:
    rows = []
    pnl_by_decision = {trade.decision_id: trade.net_pnl for trade in result.ledger.trades}
    for decision in result.decisions:
        probability = decision.inference.calibratedProbability or decision.inference.probabilityOfSuccess
        if probability is None:
            continue
        outcome = 1.0 if pnl_by_decision.get(decision.snapshot.decision_id, 0.0) > 0 else 0.0
        rows.append((float(probability), outcome))
    if not rows:
        return {"sampleCount": 0.0, "averagePredicted": 0.0, "observedWinRate": 0.0, "brierScore": 0.0}
    return {
        "sampleCount": float(len(rows)),
        "averagePredicted": sum(row[0] for row in rows) / len(rows),
        "observedWinRate": sum(row[1] for row in rows) / len(rows),
        "brierScore": sum((row[0] - row[1]) ** 2 for row in rows) / len(rows),
    }


def _cost_sensitivity(ledger: MetaStrategyBacktestLedger, multipliers: tuple[float, ...]) -> dict[str, float]:
    values: dict[str, float] = {}
    for multiplier in multipliers:
        values[f"costMultiplier:{multiplier:g}"] = float(sum(trade.gross_pnl - (trade.fees * multiplier) for trade in ledger.trades))
    return values


def _probability_bucket(probability: float) -> str:
    bounded = max(0.0, min(1.0, float(probability)))
    lower = int(bounded * 10) * 10
    upper = min(100, lower + 10)
    return f"{lower:02d}-{upper:02d}"


def _artifact_manifest(artifacts: tuple[dict[str, Any], ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "artifactId": str(artifact.get("artifactId") or artifact.get("artifact_id") or f"artifact-{index}"),
            "version": str(artifact.get("modelArtifactVersion") or artifact.get("modelVersion") or ""),
            "availableAt": str(artifact.get("availableAt") or artifact.get("approvedAt") or artifact.get("createdAt") or ""),
        }
        for index, artifact in enumerate(artifacts)
    )


def _report_hash(
    scope: ComparisonScope,
    comparisons: tuple[MetaStrategyBacktestComparison, ...],
    versions: dict[str, str],
    artifact_manifest: tuple[dict[str, str], ...],
) -> str:
    payload = {
        "scope": scope,
        "versions": versions,
        "artifactManifest": artifact_manifest,
        "comparisons": [
            {
                "scenario": comparison.scenario,
                "metrics": asdict(comparison.metrics),
                "reasonCodes": comparison.reason_codes,
            }
            for comparison in comparisons
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "ComparisonScenario",
    "ComparisonScope",
    "MetaStrategyBacktestComparison",
    "MetaStrategyBacktestComparisonReport",
    "MetaStrategyBacktestComparisonRequest",
    "MetaStrategyComparisonMetrics",
    "build_backtest_comparison_report",
    "build_holdout_comparison_report",
    "build_walk_forward_comparison_report",
]
