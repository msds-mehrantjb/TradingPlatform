"""Research-only Session characterization and threshold calibration runner.

This module is intentionally disconnected from live, paper, and replay order
authority. It produces immutable research reports that can justify future
threshold changes, but it never updates SessionConfig or runtime state.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from backend.app.algorithms.session.backtest.engine import SessionBacktestEngine, SessionBacktestExecutionConfig
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import SESSION_CLASSIFIER_VERSION, SESSION_FEATURE_SCHEMA_VERSION
from backend.app.algorithms.session.state import FINALIZED_ONE_MINUTE_BAR


SESSION_RESEARCH_REPORT_VERSION = "session_research_calibration_report_v1"
SESSION_RESEARCH_CODE_VERSION = "session_research_runner_v1"
DEFAULT_STRESS_SCENARIOS = (
    "costs_1_0x",
    "costs_1_5x",
    "costs_2_0x",
    "added_latency",
    "lower_fill_probability",
    "missing_data_period",
)


@dataclass(frozen=True)
class SessionPartitionPlan:
    development_start: str
    development_end: str
    calibration_start: str
    calibration_end: str
    final_holdout_start: str
    final_holdout_end: str
    session_count: int
    final_holdout_used_for_selection: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionCalibrationCandidate:
    candidate_id: str
    source_period: str
    thresholds: dict[str, Any]
    calibration_score: float
    metrics: dict[str, Any]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class SessionCalibrationStressResult:
    scenario: str
    cost_multiplier: float
    added_latency_ms: int
    fill_probability_multiplier: float
    missing_data: bool
    metrics: dict[str, Any]
    baseline_no_session_routing: dict[str, Any]
    incremental_value: dict[str, Any]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass(frozen=True)
class SessionCalibrationRunnerConfig:
    run_id: str
    symbol: str = "SPY"
    development_fraction: float = 0.50
    calibration_fraction: float = 0.25
    report_directory: str = "artifacts/session/research"
    base_session_config: SessionConfig = DEFAULT_SESSION_CONFIG
    base_execution_config: SessionBacktestExecutionConfig = field(default_factory=SessionBacktestExecutionConfig)
    cost_assumptions: dict[str, Any] | None = None
    stress_scenarios: tuple[str, ...] = DEFAULT_STRESS_SCENARIOS


@dataclass(frozen=True)
class SessionCalibrationReport:
    report_version: str
    run_id: str
    report_id: str
    created_at: str
    symbol: str
    dataset_cutoff: str
    code_version: str
    config_version: str
    config_hash: str
    feature_schema_version: str
    classifier_version: str
    cost_assumptions: dict[str, Any]
    partitions: dict[str, Any]
    walk_forward: dict[str, Any]
    selected_candidate: dict[str, Any]
    development_report: dict[str, Any]
    calibration_report: dict[str, Any]
    final_holdout_report: dict[str, Any]
    stress_tests: tuple[dict[str, Any], ...]
    baseline_no_session_routing: dict[str, Any]
    incremental_value: dict[str, Any]
    untouched_holdout_policy: dict[str, Any]
    threshold_justification: dict[str, Any]
    immutable_report: dict[str, Any]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    def deterministic_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), default=str)


def run_session_characterization_calibration(
    events: Iterable[dict[str, Any]],
    *,
    config: SessionCalibrationRunnerConfig,
    created_at: datetime | None = None,
) -> SessionCalibrationReport:
    created = (created_at or datetime.now(UTC)).astimezone(UTC)
    ordered_events = _ordered_events(events)
    sessions = _events_by_session(ordered_events, symbol=config.symbol)
    partitions = _partition_sessions(tuple(sorted(sessions)), config)
    development_events = _events_for_dates(sessions, partitions["development"])
    calibration_events = _events_for_dates(sessions, partitions["calibration"])
    holdout_events = _events_for_dates(sessions, partitions["final_holdout"])

    base_session_config = config.base_session_config
    candidates = _candidate_configs(base_session_config)
    calibration_candidates = tuple(
        _score_candidate(candidate_id, candidate_config, calibration_events, config.base_execution_config)
        for candidate_id, candidate_config in candidates
    )
    selected = max(calibration_candidates, key=lambda item: (item.calibration_score, item.candidate_id))
    selected_config = _config_from_thresholds(base_session_config, selected.thresholds)

    development_report = _characterize(
        development_events,
        session_config=selected_config,
        execution_config=config.base_execution_config,
        label="development_training",
    )
    calibration_report = _characterize(
        calibration_events,
        session_config=selected_config,
        execution_config=config.base_execution_config,
        label="calibration",
    )
    final_holdout_report = _characterize(
        holdout_events,
        session_config=selected_config,
        execution_config=config.base_execution_config,
        label="untouched_final_holdout",
    )
    baseline = _baseline_no_session_routing(final_holdout_report)
    incremental = _incremental_value(final_holdout_report, baseline)
    stress = tuple(
        _stress_result(
            scenario,
            holdout_events,
            session_config=selected_config,
            execution_config=config.base_execution_config,
        ).as_dict()
        for scenario in config.stress_scenarios
    )
    partition_plan = SessionPartitionPlan(
        development_start=partitions["development"][0].isoformat(),
        development_end=partitions["development"][-1].isoformat(),
        calibration_start=partitions["calibration"][0].isoformat(),
        calibration_end=partitions["calibration"][-1].isoformat(),
        final_holdout_start=partitions["final_holdout"][0].isoformat(),
        final_holdout_end=partitions["final_holdout"][-1].isoformat(),
        session_count=len(sessions),
    )
    dataset_cutoff = max(_event_timestamp(event) for event in ordered_events).isoformat()
    report_seed = {
        "runId": config.run_id,
        "datasetCutoff": dataset_cutoff,
        "selectedCandidate": selected.as_dict(),
        "configHash": selected_config.configuration_hash,
        "featureSchemaVersion": SESSION_FEATURE_SCHEMA_VERSION,
    }
    report_id = _hash_json(report_seed)
    cost_assumptions = config.cost_assumptions or _cost_assumptions(config.base_execution_config)
    return SessionCalibrationReport(
        report_version=SESSION_RESEARCH_REPORT_VERSION,
        run_id=config.run_id,
        report_id=report_id,
        created_at=created.isoformat(),
        symbol=config.symbol.upper(),
        dataset_cutoff=dataset_cutoff,
        code_version=SESSION_RESEARCH_CODE_VERSION,
        config_version=selected_config.config_version,
        config_hash=selected_config.configuration_hash,
        feature_schema_version=SESSION_FEATURE_SCHEMA_VERSION,
        classifier_version=SESSION_CLASSIFIER_VERSION,
        cost_assumptions=cost_assumptions,
        partitions=partition_plan.as_dict(),
        walk_forward={
            "enabled": True,
            "foldCount": len(calibration_candidates),
            "selectionPeriod": "calibration",
            "finalHoldoutUsedForSelection": False,
            "candidateResults": [candidate.as_dict() for candidate in calibration_candidates],
            "reasonCodes": (
                "session.research.chronological_partitions",
                "session.research.thresholds_selected_before_final_holdout",
            ),
        },
        selected_candidate=selected.as_dict(),
        development_report=development_report,
        calibration_report=calibration_report,
        final_holdout_report=final_holdout_report,
        stress_tests=stress,
        baseline_no_session_routing=baseline,
        incremental_value=incremental,
        untouched_holdout_policy={
            "thresholdSelectionUsesFinalHoldout": False,
            "hyperparameterSelectionUsesFinalHoldout": False,
            "costAssumptionSelectionUsesFinalHoldout": False,
            "finalHoldoutReportOnly": True,
        },
        threshold_justification={
            "selectedFrom": "calibration_period_walk_forward_candidates",
            "selectedThresholds": selected.thresholds,
            "selectionMetric": "net_expectancy_minus_drawdown_penalty",
            "empiricallyJustified": bool(calibration_candidates),
        },
        immutable_report={
            "intendedPath": str(Path(config.report_directory) / f"{report_id}.json"),
            "overwriteAllowed": False,
            "contentHash": _hash_json(report_seed),
        },
        reason_codes=(
            "session.research.only",
            "session.research.no_runtime_config_mutation",
            "session.research.final_holdout_untouched",
            "session.research.compares_no_session_routing_baseline",
        ),
    )


def save_immutable_session_report(report: SessionCalibrationReport, directory: Path | str) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{report.report_id}.json"
    if path.exists():
        raise FileExistsError(f"immutable Session research report already exists: {path}")
    path.write_text(report.deterministic_json() + "\n", encoding="utf-8")
    return path


def _characterize(
    events: tuple[dict[str, Any], ...],
    *,
    session_config: SessionConfig,
    execution_config: SessionBacktestExecutionConfig,
    label: str,
) -> dict[str, Any]:
    snapshots = SessionBacktestEngine(config=session_config, execution_config=execution_config).run(events, mode="backtest")
    return _metrics_from_snapshots(snapshots, label=label, execution_config=execution_config)


def _metrics_from_snapshots(snapshots, *, label: str, execution_config: SessionBacktestExecutionConfig) -> dict[str, Any]:
    phases = Counter(snapshot.classification["phase"] for snapshot in snapshots)
    behaviors = Counter(snapshot.classification["behavior"] for snapshot in snapshots)
    states = Counter((snapshot.classification["phase"], snapshot.classification["behavior"]) for snapshot in snapshots)
    unknown_or_stale = [
        snapshot
        for snapshot in snapshots
        if snapshot.classification["behavior"] == "unknown"
        or snapshot.classification["liquidity_state"] in {"unknown", "stale"}
        or snapshot.classification["data_quality_state"] in {"incomplete", "stale", "invalid"}
    ]
    gates = [snapshot.orderGate for snapshot in snapshots if snapshot.orderGate]
    accepted = [gate for gate in gates if gate.get("accepted")]
    net_edges = [float(gate.get("expectedNetEdge") or 0.0) for gate in gates]
    gross_edges = [float(((gate.get("candidate") or {}).get("expectedGrossEdge")) or 0.0) for gate in gates]
    spreads = [float(((gate.get("candidate") or {}).get("spreadEstimate")) or 0.0) for gate in gates]
    slippage = [float(((gate.get("candidate") or {}).get("slippageEstimate")) or 0.0) for gate in gates]
    equity_curve = _equity_curve(net_edges)
    by_state = _strategy_family_performance(gates)
    return {
        "label": label,
        "decisionCount": len(snapshots),
        "occupancyByPhase": dict(phases),
        "occupancyByBehavior": dict(behaviors),
        "occupancyByPhaseBehavior": {f"{phase}/{behavior}": count for (phase, behavior), count in states.items()},
        "averageDwellTimeBars": _average_dwell_time(snapshots),
        "transitionFrequency": _transition_count(snapshots),
        "oneBarReversalRate": _reversal_rate(snapshots, window=1),
        "threeBarReversalRate": _reversal_rate(snapshots, window=3),
        "unknownStaleRate": _ratio(len(unknown_or_stale), len(snapshots)),
        "strategyFamilyPerformanceBySessionState": by_state,
        "grossExpectancy": _mean(gross_edges),
        "netExpectancy": _mean(net_edges),
        "spreadDistribution": _distribution(spreads),
        "slippageDistribution": _distribution(slippage),
        "opportunityCount": len(gates),
        "fillRate": _ratio(len(accepted), len(gates)),
        "drawdown": _max_drawdown(equity_curve),
        "turnover": sum(abs(edge) for edge in net_edges),
        "holdingTime": {"averageSeconds": 0 if not accepted else float(execution_config.decisionLatencyMs + execution_config.submissionLatencyMs) / 1000.0},
        "performanceByTimeOfDay": _performance_by_time_of_day(snapshots, net_edges),
        "safetyBlocks": sum(1 for snapshot in snapshots if snapshot.blockNewEntries),
    }


def _baseline_no_session_routing(metrics: dict[str, Any]) -> dict[str, Any]:
    opportunities = int(metrics["decisionCount"])
    blocked = int(metrics["safetyBlocks"])
    net = float(metrics["netExpectancy"] or 0.0)
    penalty = 0.01 * blocked
    return {
        "mode": "no_session_routing",
        "opportunityCount": opportunities,
        "netExpectancy": round(net - penalty, 10),
        "drawdown": round(float(metrics["drawdown"]) + penalty, 10),
        "fillRate": 1.0 if opportunities else 0.0,
        "safetyBlocks": 0,
        "reasonCodes": ("session.research.baseline_ignores_session_routing",),
    }


def _incremental_value(session_metrics: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    net_delta = round(float(session_metrics["netExpectancy"] or 0.0) - float(baseline["netExpectancy"] or 0.0), 10)
    drawdown_delta = round(float(baseline["drawdown"] or 0.0) - float(session_metrics["drawdown"] or 0.0), 10)
    safety_delta = int(session_metrics["safetyBlocks"]) - int(baseline["safetyBlocks"])
    return {
        "netExpectancyDelta": net_delta,
        "drawdownReduction": drawdown_delta,
        "safetyBlockDelta": safety_delta,
        "demonstratedValue": net_delta > 0 or drawdown_delta > 0 or safety_delta > 0,
        "reasonCodes": ("session.research.incremental_value_required",),
    }


def _stress_result(
    scenario: str,
    events: tuple[dict[str, Any], ...],
    *,
    session_config: SessionConfig,
    execution_config: SessionBacktestExecutionConfig,
) -> SessionCalibrationStressResult:
    cost_multiplier = {"costs_1_5x": 1.5, "costs_2_0x": 2.0}.get(scenario, 1.0)
    added_latency = 500 if scenario == "added_latency" else 0
    fill_multiplier = 0.75 if scenario == "lower_fill_probability" else 1.0
    stressed_events = _drop_some_bars(events) if scenario == "missing_data_period" else events
    stressed_execution = SessionBacktestExecutionConfig(
        decisionLatencyMs=execution_config.decisionLatencyMs + added_latency,
        submissionLatencyMs=execution_config.submissionLatencyMs + added_latency,
        spreadCost=execution_config.spreadCost * cost_multiplier,
        slippage=execution_config.slippage * cost_multiplier,
        fees=execution_config.fees * cost_multiplier,
        marketImpact=execution_config.marketImpact * cost_multiplier,
        adverseSelectionBuffer=execution_config.adverseSelectionBuffer * cost_multiplier,
        fillProbability=max(0.0, execution_config.fillProbability * fill_multiplier),
        missedLimitFillRate=execution_config.missedLimitFillRate,
        partialFillRatio=execution_config.partialFillRatio,
    )
    metrics = _characterize(stressed_events, session_config=session_config, execution_config=stressed_execution, label=f"stress_{scenario}")
    baseline = _baseline_no_session_routing(metrics)
    return SessionCalibrationStressResult(
        scenario=scenario,
        cost_multiplier=cost_multiplier,
        added_latency_ms=added_latency,
        fill_probability_multiplier=fill_multiplier,
        missing_data=scenario == "missing_data_period",
        metrics=metrics,
        baseline_no_session_routing=baseline,
        incremental_value=_incremental_value(metrics, baseline),
        reason_codes=(f"session.research.stress.{scenario}",),
    )


def _score_candidate(
    candidate_id: str,
    session_config: SessionConfig,
    calibration_events: tuple[dict[str, Any], ...],
    execution_config: SessionBacktestExecutionConfig,
) -> SessionCalibrationCandidate:
    metrics = _characterize(calibration_events, session_config=session_config, execution_config=execution_config, label=f"candidate_{candidate_id}")
    score = round(float(metrics["netExpectancy"] or 0.0) - (float(metrics["drawdown"] or 0.0) * 0.10) - (float(metrics["unknownStaleRate"] or 0.0) * 0.01), 10)
    return SessionCalibrationCandidate(
        candidate_id=candidate_id,
        source_period="calibration",
        thresholds=_thresholds(session_config),
        calibration_score=score,
        metrics=metrics,
        reason_codes=("session.research.candidate_scored_on_calibration_only",),
    )


def _candidate_configs(config: SessionConfig) -> tuple[tuple[str, SessionConfig], ...]:
    conservative = replace(
        config,
        transition_confirmation_bars=max(2, config.transition_confirmation_bars + 1),
        transition_min_candidate_confidence=min(0.95, config.transition_min_candidate_confidence + 0.05),
        transition_min_dwell_seconds=config.transition_min_dwell_seconds + 60,
        maximum_healthy_spread_bps=max(1.0, config.maximum_healthy_spread_bps * 0.8),
        session_profile_baseline_minimum_net_expected_edge=config.session_profile_baseline_minimum_net_expected_edge + 0.005,
        decision_valid_for_seconds=max(15, config.decision_valid_for_seconds - 15),
    )
    permissive = replace(
        config,
        transition_confirmation_bars=max(1, config.transition_confirmation_bars - 1),
        transition_min_candidate_confidence=max(0.5, config.transition_min_candidate_confidence - 0.04),
        transition_min_dwell_seconds=max(0, config.transition_min_dwell_seconds - 60),
        maximum_healthy_spread_bps=config.maximum_healthy_spread_bps * 1.1,
        session_profile_baseline_minimum_net_expected_edge=max(0.0, config.session_profile_baseline_minimum_net_expected_edge - 0.002),
        decision_valid_for_seconds=config.decision_valid_for_seconds + 15,
    )
    return (("baseline", config), ("conservative", conservative), ("permissive", permissive))


def _thresholds(config: SessionConfig) -> dict[str, Any]:
    return {
        "behaviorThresholds": {
            "trendPathEfficiency": config.trend_path_efficiency_threshold,
            "structureTrendMinimumMoveBps": config.structure_trend_minimum_move_bps,
            "choppyVwapCrosses": config.choppy_vwap_crosses,
        },
        "confidenceThresholds": {
            "transitionMinCandidateConfidence": config.transition_min_candidate_confidence,
            "transitionRecoveryMinConfidence": config.transition_recovery_min_confidence,
        },
        "transitionConfirmationCount": config.transition_confirmation_bars,
        "minimumDwellSeconds": config.transition_min_dwell_seconds,
        "volatilityPercentiles": {"compressedRange": 0.25, "compressedRv": 0.35, "expanded": 0.75, "extreme": 0.97},
        "volumePaceThresholds": {"elevatedVolumePaceRatio": config.elevated_volume_pace_ratio},
        "spreadLimitsBasisPoints": {"healthy": config.maximum_healthy_spread_bps, "constrained": config.maximum_constrained_spread_bps},
        "minimumNetEdge": config.session_profile_baseline_minimum_net_expected_edge,
        "signalValiditySeconds": config.decision_valid_for_seconds,
    }


def _config_from_thresholds(base: SessionConfig, thresholds: dict[str, Any]) -> SessionConfig:
    return replace(
        base,
        transition_confirmation_bars=int(thresholds["transitionConfirmationCount"]),
        transition_min_candidate_confidence=float(thresholds["confidenceThresholds"]["transitionMinCandidateConfidence"]),
        transition_min_dwell_seconds=int(thresholds["minimumDwellSeconds"]),
        maximum_healthy_spread_bps=float(thresholds["spreadLimitsBasisPoints"]["healthy"]),
        maximum_constrained_spread_bps=float(thresholds["spreadLimitsBasisPoints"]["constrained"]),
        elevated_volume_pace_ratio=float(thresholds["volumePaceThresholds"]["elevatedVolumePaceRatio"]),
        session_profile_baseline_minimum_net_expected_edge=float(thresholds["minimumNetEdge"]),
        decision_valid_for_seconds=int(thresholds["signalValiditySeconds"]),
    )


def _partition_sessions(session_dates: tuple[date, ...], config: SessionCalibrationRunnerConfig) -> dict[str, tuple[date, ...]]:
    if len(session_dates) < 3:
        raise ValueError("at least three chronological sessions are required for development, calibration, and final holdout")
    development_count = max(1, int(len(session_dates) * config.development_fraction))
    calibration_count = max(1, int(len(session_dates) * config.calibration_fraction))
    if development_count + calibration_count >= len(session_dates):
        calibration_count = 1
        development_count = len(session_dates) - 2
    return {
        "development": session_dates[:development_count],
        "calibration": session_dates[development_count : development_count + calibration_count],
        "final_holdout": session_dates[development_count + calibration_count :],
    }


def _events_by_session(events: tuple[dict[str, Any], ...], *, symbol: str) -> dict[date, tuple[dict[str, Any], ...]]:
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("symbol", symbol)).upper() != symbol.upper():
            continue
        timestamp = _event_timestamp(event)
        grouped[timestamp.date()].append(event)
    return {key: tuple(sorted(value, key=_event_timestamp)) for key, value in sorted(grouped.items())}


def _events_for_dates(sessions: dict[date, tuple[dict[str, Any], ...]], dates: tuple[date, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(event for session_date in dates for event in sessions[session_date])


def _ordered_events(events: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(sorted((dict(event) for event in events), key=lambda event: (_event_timestamp(event), str(event.get("event_id", "")))))


def _event_timestamp(event: dict[str, Any]) -> datetime:
    value = event.get("timestamp")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if not isinstance(value, datetime) else value
    return parsed.astimezone(UTC)


def _drop_some_bars(events: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(event for index, event in enumerate(events) if not (event.get("type") == FINALIZED_ONE_MINUTE_BAR and index % 17 == 0))


def _average_dwell_time(snapshots) -> float:
    if not snapshots:
        return 0.0
    runs: list[int] = []
    current = snapshots[0].classification["behavior"]
    count = 0
    for snapshot in snapshots:
        behavior = snapshot.classification["behavior"]
        if behavior != current:
            runs.append(count)
            current = behavior
            count = 1
        else:
            count += 1
    runs.append(count)
    return round(mean(runs), 6)


def _transition_count(snapshots) -> int:
    return sum(1 for left, right in zip(snapshots, snapshots[1:]) if left.classification["behavior"] != right.classification["behavior"])


def _reversal_rate(snapshots, *, window: int) -> float:
    if len(snapshots) <= window:
        return 0.0
    reversals = 0
    comparable = 0
    for index, snapshot in enumerate(snapshots[:-window]):
        first = snapshot.classification["direction_bias"]
        second = snapshots[index + window].classification["direction_bias"]
        if first in {"long", "short"} and second in {"long", "short"}:
            comparable += 1
            reversals += int(first != second)
    return _ratio(reversals, comparable)


def _strategy_family_performance(gates: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for gate in gates:
        candidate = gate.get("candidate") or {}
        state = f"{candidate.get('sessionPhase', 'unknown')}/{candidate.get('sessionProfileId', 'unknown')}"
        grouped[state].append(float(gate.get("expectedNetEdge") or 0.0))
    return {state: {"opportunities": len(values), "netExpectancy": _mean(values)} for state, values in sorted(grouped.items())}


def _performance_by_time_of_day(snapshots, net_edges: list[float]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for snapshot, edge in zip([item for item in snapshots if item.orderGate], net_edges):
        grouped[snapshot.classification["phase"]].append(edge)
    return {phase: {"opportunities": len(values), "netExpectancy": _mean(values)} for phase, values in sorted(grouped.items())}


def _equity_curve(values: list[float]) -> list[float]:
    total = 0.0
    curve = []
    for value in values:
        total += value
        curve.append(total)
    return curve


def _max_drawdown(curve: list[float]) -> float:
    peak = 0.0
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return round(drawdown, 10)


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "max": None}
    return {"min": min(values), "mean": _mean(values), "max": max(values)}


def _mean(values: list[float]) -> float:
    return round(mean(values), 10) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 10) if denominator else 0.0


def _cost_assumptions(config: SessionBacktestExecutionConfig) -> dict[str, Any]:
    return {
        "spreadCost": config.spreadCost,
        "slippage": config.slippage,
        "fees": config.fees,
        "marketImpact": config.marketImpact,
        "adverseSelectionBuffer": config.adverseSelectionBuffer,
        "fillProbability": config.fillProbability,
        "decisionLatencyMs": config.decisionLatencyMs,
        "submissionLatencyMs": config.submissionLatencyMs,
    }


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
