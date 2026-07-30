from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, build_calibration_table
from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import (
    WcaCandle,
    WcaConfidenceCalibrationOutcome,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaQuote,
    WcaSide,
    WcaStrategyEvaluation,
    WcaStrategyPerformanceRecord,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.research_jobs import WcaResearchJobType, research_job
from backend.app.algorithms.wca.research_repository import WcaResearchRepository
from backend.app.algorithms.wca.research_worker import WcaResearchWorker
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import baseline_weight_snapshot, performance_weight_snapshot


DECISION_TIME = datetime(2026, 1, 15, 17, 0, tzinfo=UTC)


def test_decision_uses_active_calibration_table_available_before_timestamp_only() -> None:
    repository = repository_for_step12()
    configuration = default_wca_configuration()
    past_table = build_calibration_table(
        strategy_id="C1",
        strategy_version="C1.test.v1",
        outcomes=tuple(calibration_outcome(success=False, available_at=DECISION_TIME - timedelta(days=1, minutes=index)) for index in range(35)),
        as_of=DECISION_TIME - timedelta(minutes=1),
        config=ConfidenceCalibrationConfig(minimum_samples=30),
    )
    future_table = build_calibration_table(
        strategy_id="C1",
        strategy_version="C1.test.v1",
        outcomes=tuple(calibration_outcome(success=True, available_at=DECISION_TIME + timedelta(minutes=index + 1)) for index in range(35)),
        as_of=DECISION_TIME + timedelta(hours=1),
        config=ConfidenceCalibrationConfig(minimum_samples=30),
    )
    repository.save_confidence_calibration(past_table, symbol="SPY", configuration_version=configuration.configuration_version, engine_version="step12")
    repository.save_confidence_calibration(future_table, symbol="SPY", configuration_version=configuration.configuration_version, engine_version="step12")

    tables = repository.read_active_confidence_calibrations(symbol="SPY", as_of=DECISION_TIME)
    decision = run_wca_execution_pipeline(
        WcaExecutionPipelineInput(
            run_id="step12-calibration",
            decision_id="step12-calibration-decision",
            order_intent_id="step12-calibration-intent",
            snapshot=market_snapshot(),
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=baseline_weight_snapshot(cutoff=DECISION_TIME, weight_version="step12.weights"),
            calibration_tables=tables,
        ),
        voters=(FakeVoter("C1", WcaSide.BUY), FakeVoter("C7", WcaSide.BUY), FakeVoter("C8", WcaSide.BUY)),
    ).decision

    c1 = next(row for row in decision.aggregation.strategy_evaluations if row.strategy_id == "C1")
    assert c1.raw_confidence == 0.9
    assert c1.calibrated_confidence == 0.6
    assert c1.calibration_version == past_table.calibration_version
    assert c1.calibration_version != future_table.calibration_version


def test_future_weight_snapshot_and_future_performance_records_do_not_leak_into_decision() -> None:
    repository = repository_for_step12()
    configuration = default_wca_configuration()
    past_weights = baseline_weight_snapshot(cutoff=DECISION_TIME - timedelta(minutes=1), weight_version="step12.past.weights")
    future_weights = weight_snapshot({"C1": 0.80}, created_at=DECISION_TIME + timedelta(minutes=1), version="step12.future.weights")
    repository.save_weight_snapshot(past_weights, symbol="SPY", configuration_version=configuration.configuration_version, engine_version="step12")
    repository.save_weight_snapshot(future_weights, symbol="SPY", configuration_version=configuration.configuration_version, engine_version="step12")
    records = (
        performance_record("C1", 0.1, DECISION_TIME - timedelta(days=2)),
        performance_record("C1", 8.0, DECISION_TIME + timedelta(minutes=5)),
    )

    active = repository.read_active_weights(as_of=DECISION_TIME)
    candidate = performance_weight_snapshot(records=records, cutoff=DECISION_TIME)

    assert active is not None
    assert active.weight_version == "step12.past.weights"
    assert candidate.weights["C1"] == 1.0
    assert candidate.weight_schema_version == "wca_weight_snapshot_v2"
    assert all(detail.trade_count <= 1 for detail in candidate.details if detail.strategy_id == "C1")


def test_research_worker_creates_real_calibration_and_weight_candidates_without_activation() -> None:
    repository = repository_for_step12()
    research_repository = WcaResearchRepository(repository)
    calibration_job = research_job(
        WcaResearchJobType.CONFIDENCE_CALIBRATION,
        payload={
            "as_of": DECISION_TIME.isoformat(),
            "outcomes": [calibration_outcome(success=index % 2 == 0, available_at=DECISION_TIME - timedelta(days=2, minutes=index)).model_dump(mode="json") for index in range(40)],
        },
        run_id="step12-calibration-job",
        configuration_version="step12",
    )
    weight_job = research_job(
        WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION,
        payload={
            "cutoff": DECISION_TIME.isoformat(),
            "performance_records": [performance_record("C1", 0.5, DECISION_TIME - timedelta(days=2, minutes=index)).model_dump(mode="json") for index in range(45)],
        },
        run_id="step12-weight-job",
        configuration_version="step12",
    )
    research_repository.enqueue_job(calibration_job)
    research_repository.enqueue_job(weight_job)
    worker = WcaResearchWorker(repository=repository, research_repository=research_repository, owner_id="step12-worker")

    calibration_result = worker.run_once()
    weight_result = worker.run_once()

    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        candidates = conn.execute("SELECT candidate_type, payload_json FROM wca_research_candidates ORDER BY created_at").fetchall()
        active_calibrations = conn.execute("SELECT COUNT(*) FROM wca_confidence_calibrations").fetchone()[0]
        active_weights = conn.execute("SELECT COUNT(*) FROM wca_weight_snapshots").fetchone()[0]
    payloads = {row["candidate_type"]: json.loads(row["payload_json"]) for row in candidates}

    assert calibration_result["status"] == "succeeded"
    assert calibration_result["resultReference"]["kind"] == "research_candidate"
    assert weight_result["status"] == "succeeded"
    assert weight_result["resultReference"]["kind"] == "research_candidate"
    assert payloads[WcaResearchJobType.CONFIDENCE_CALIBRATION.value]["calibration_tables"]
    assert payloads[WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value]["weight_snapshot"]["weight_version"].startswith("wca-weight-candidate-")
    assert payloads[WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value]["weight_snapshot"]["weight_schema_version"] == "wca_weight_snapshot_v2"
    assert active_calibrations == 0
    assert active_weights == 0


def test_wca_step12_modules_do_not_import_sibling_algorithm_weight_or_settings_state() -> None:
    root = Path("backend/app/algorithms/wca")
    violations = []
    for module_name in ("confidence.py", "weights.py", "research_worker.py"):
        source = (root / module_name).read_text(encoding="utf-8")
        for forbidden in (
            "backend.app.algorithms.weighted_voting",
            "backend.app.algorithms.voting_ensemble",
            "backend.app.algorithms.regime",
            "backend.app.algorithms.session",
            "backend.app.algorithms.meta_strategy",
        ):
            if forbidden in source:
                violations.append(f"{module_name} imports {forbidden}")
    assert violations == []


class FakeVoter:
    def __init__(self, strategy_id: str, side: WcaSide) -> None:
        self.strategy_id = strategy_id
        self.slug = strategy_id.lower()
        self.name = strategy_id
        self.family = "trend"
        self.version = f"{strategy_id}.test.v1"
        self.base_weight = 1 / 3
        self.side = side

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        direction = 1 if self.side == WcaSide.BUY else -1
        return WcaStrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            name=self.name,
            status=WcaEvaluationStatus.ACTIVE,
            signal=self.side,
            confidence=0.9,
            raw_confidence=0.9,
            calibrated_confidence=0.9,
            direction=self.side,
            applicability=WcaEvaluationStatus.ACTIVE,
            evidence_strength=0.9,
            data_quality_status=WcaEvaluationStatus.ACTIVE,
            base_weight=self.base_weight,
            effective_weight=self.base_weight,
            contribution=direction * self.base_weight * 0.9,
            reason_codes=(f"test.{self.strategy_id}",),
        )


def repository_for_step12() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-step12-{uuid4().hex}.sqlite'}")


def market_snapshot() -> WcaMarketSnapshot:
    candles = tuple(
        WcaCandle(timestamp=DECISION_TIME - timedelta(minutes=69 - index), open=100, high=101, low=99, close=100 + index * 0.01, volume=200_000, vwap=100)
        for index in range(70)
    )
    latest = candles[-1]
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=DECISION_TIME,
        decision_timestamp=DECISION_TIME,
        candles=candles,
        quote=WcaQuote(timestamp=DECISION_TIME, bid=latest.close - 0.01, ask=latest.close + 0.01),
        source="test",
    )


def calibration_outcome(*, success: bool, available_at: datetime) -> WcaConfidenceCalibrationOutcome:
    return WcaConfidenceCalibrationOutcome(
        strategy_id="C1",
        strategy_version="C1.test.v1",
        direction=WcaSide.BUY,
        regime="default",
        raw_confidence=0.9,
        realized_success=success,
        decision_timestamp=available_at - timedelta(minutes=30),
        outcome_available_at=available_at,
    )


def performance_record(strategy_id: str, r_multiple: float, available_at: datetime) -> WcaStrategyPerformanceRecord:
    family = next(definition.family for definition in WCA_STRATEGY_REGISTRY if definition.strategy_id == strategy_id)
    return WcaStrategyPerformanceRecord(
        strategy_id=strategy_id,
        strategy_version=f"{strategy_id}.test.v1",
        family=family,
        decision_timestamp=available_at - timedelta(minutes=30),
        outcome_available_at=available_at,
        r_multiple=r_multiple,
        pnl=r_multiple * 100,
        success=r_multiple > 0,
        regime="default",
    )


def weight_snapshot(overrides: dict[str, float], *, created_at: datetime, version: str) -> WcaWeightSnapshot:
    remaining = 1.0 - sum(overrides.values())
    rest = [definition.strategy_id for definition in WCA_STRATEGY_REGISTRY if definition.strategy_id not in overrides]
    weights = {definition.strategy_id: overrides.get(definition.strategy_id, remaining / len(rest)) for definition in WCA_STRATEGY_REGISTRY}
    return WcaWeightSnapshot(weight_version=version, created_at=created_at, weights=weights, metrics_cutoff_timestamp=created_at)
