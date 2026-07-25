from __future__ import annotations

import copy
import inspect
import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.execution_adapter import VotingEnsembleExecutionAdapter
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import directional_signal
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES,
    VOTING_ENSEMBLE_MODULE_INVENTORY,
    StrategyCollection,
    active_module_ids,
)
from backend.app.gates import BrokerOrderState
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_evaluation_jobs import candles as runtime_candles
from backend.tests.test_voting_ensemble_evaluation_jobs import evaluate_payload as runtime_evaluate_payload
from backend.tests.test_voting_ensemble_local_gates import FixedHighFitClassifier
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)

REQUIRED_BOUNDARY_TESTS: dict[str, tuple[str, ...]] = {
    "inventory_and_runtime_binding": ("backend/tests/test_voting_ensemble_module_inventory.py",),
    "import_isolation": ("backend/tests/test_voting_ensemble_boundary_coverage.py",),
    "background_queue_and_worker": ("backend/tests/test_voting_ensemble_evaluation_jobs.py",),
    "command_idempotency": ("backend/tests/test_voting_ensemble_evaluation_jobs.py",),
    "snapshot_point_in_time_correctness": ("backend/tests/test_voting_ensemble_snapshot.py",),
    "every_directional_strategy": (
        "backend/tests/test_voting_ensemble_directional_strategies.py",
        "backend/tests/test_voting_ensemble_opening_range_breakout.py",
        "backend/tests/test_voting_ensemble_vwap_trend_continuation.py",
        "backend/tests/test_voting_ensemble_gap_continuation_fade.py",
    ),
    "every_context_module": ("backend/tests/test_voting_ensemble_context_pipeline.py",),
    "regime_states_and_transitions": ("backend/tests/test_voting_ensemble_regime_classifier.py",),
    "safety_filter": ("backend/tests/test_voting_ensemble_local_gates.py",),
    "global_local_gate_ordering": ("backend/tests/test_voting_ensemble_local_gates.py",),
    "aggregation": ("backend/tests/test_voting_ensemble_family_aware_authoritative.py",),
    "family_overlap_control": ("backend/tests/test_voting_ensemble_family_aware_authoritative.py",),
    "reliability": ("backend/tests/test_voting_ensemble_reliability.py",),
    "settings_validation": ("backend/tests/test_voting_ensemble_trading_settings.py",),
    "dynamic_profiles": ("backend/tests/test_voting_ensemble_trading_settings.py",),
    "cost_model": ("backend/tests/test_voting_ensemble_execution_stress.py",),
    "latency_gate": ("backend/tests/test_voting_ensemble_local_gates.py",),
    "risk_sizing": ("backend/tests/test_voting_ensemble_risk_budget.py",),
    "order_planner": ("backend/tests/test_voting_ensemble_risk_budget.py",),
    "execution_adapter": ("backend/tests/test_voting_ensemble_execution_adapter.py",),
    "position_state": ("backend/tests/test_voting_ensemble_execution_adapter.py",),
    "stop_and_target_policy": (
        "backend/tests/test_voting_ensemble_execution_adapter.py",
        "backend/tests/test_voting_ensemble_execution_stress.py",
    ),
    "backtest_parity": ("backend/tests/test_voting_ensemble_pipeline_parity.py",),
    "replay_parity": ("backend/tests/test_voting_ensemble_pipeline_parity.py",),
    "persistence_and_recovery": (
        "backend/tests/test_voting_ensemble_evaluation_jobs.py",
        "backend/tests/test_voting_ensemble_intelligence_capture.py",
    ),
    "shadow_module_non_interference": ("backend/tests/test_voting_ensemble_boundary_coverage.py",),
    "cross_algorithm_isolation": ("backend/tests/test_voting_ensemble_boundary_coverage.py",),
    "promotion_policy": ("backend/tests/test_voting_ensemble_promotion_policy.py",),
}


class VotingEnsembleBoundaryCoverageTest(unittest.TestCase):
    def test_required_boundary_groups_have_focused_tests(self) -> None:
        missing: dict[str, tuple[str, ...]] = {}
        for boundary, paths in REQUIRED_BOUNDARY_TESTS.items():
            existing = tuple(path for path in paths if (ROOT / path).exists())
            if not existing:
                missing[boundary] = paths

        self.assertEqual(missing, {})

    def test_inventory_declares_existing_voting_ensemble_test_path_for_every_module(self) -> None:
        missing_paths: list[str] = []
        cross_algorithm_paths: list[tuple[str, str]] = []
        for module in VOTING_ENSEMBLE_MODULE_INVENTORY.modules:
            path = module.testPath
            if not (ROOT / path).exists():
                missing_paths.append(f"{module.strategyId}:{path}")
            if module.enabled and any(name in path for name in ("wca", "weighted_voting", "meta_strategy", "session")):
                cross_algorithm_paths.append((module.strategyId, path))

        self.assertEqual(missing_paths, [])
        self.assertEqual(cross_algorithm_paths, [])

    def test_importing_voting_ensemble_service_does_not_import_other_algorithm_internals(self) -> None:
        script = """
import importlib
import json
import sys
forbidden = (
    "backend.app.algorithms.wca",
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.session",
    "backend.app.algorithms.meta_strategy",
)
importlib.import_module("backend.app.algorithms.voting_ensemble.service")
print(json.dumps(sorted(name for name in sys.modules if name.startswith(forbidden))))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(json.loads(result.stdout), [])

    def test_inventory_modules_do_not_point_to_forbidden_mutable_implementations(self) -> None:
        forbidden = []
        for module in VOTING_ENSEMBLE_MODULE_INVENTORY.modules:
            for field_name in ("implementationPath", "runtimeBinding", "backtestBinding"):
                value = getattr(module, field_name)
                if value.startswith(FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES):
                    forbidden.append(f"{module.strategyId}:{field_name}:{value}")

        self.assertEqual(forbidden, [])

    def test_cross_algorithm_settings_do_not_change_voting_ensemble_output(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_classifier = service_module.REGIME_CLASSIFIER
        payload = tradable_payload()
        mutated = copy.deepcopy(payload)
        mutated.update(
            {
                "wcaSettings": {"riskPerTradePercent": 99, "thresholds": {"minScore": 1.0}},
                "weightedVotingSettings": {"weights": {"trend": 0.0, "reversal": 100.0}},
                "regimeSettings": {"adxThreshold": 999, "atrThreshold": 999},
                "sessionSettings": {"entryWindow": "closed"},
                "metaStrategySettings": {"mlMode": "ACTIVE", "forceReject": True},
            }
        )

        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
        try:
            pipeline = VotingEnsemblePipeline(service=VotingEnsembleService())
            baseline = pipeline.run(payload, mode="paper")
            with_cross_algorithm_noise = pipeline.run(mutated, mode="paper")
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_classifier

        self.assertEqual(stable_pre_execution_signature(baseline), stable_pre_execution_signature(with_cross_algorithm_noise))

    def test_voting_ensemble_state_does_not_alter_other_algorithm_broker_state(self) -> None:
        adapter = VotingEnsembleExecutionAdapter()
        other_algorithm_order = BrokerOrderState(
            algorithmId="weighted_voting",
            symbol="SPY",
            side=Signal.BUY,
            clientOrderId="weighted-voting-order",
            orderType="LIMIT",
            quantity=10,
            entryPrice=100.0,
            submittedAt=NOW,
        )
        before = other_algorithm_order.model_dump(mode="json")

        reconciled = adapter.reconcile_broker_state(openOrders=[other_algorithm_order], positions=[], observedAt=NOW)
        adapter.state_store.mark_unknown_order_state("SPY")

        self.assertEqual(reconciled, ())
        self.assertEqual(other_algorithm_order.model_dump(mode="json"), before)
        self.assertEqual(adapter.state_store.namespace, "voting_ensemble.execution_state")

    def test_same_finalized_bar_cannot_create_two_runtime_jobs(self) -> None:
        from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent

        runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), auto_start=False)
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW,
            finalized=True,
            settingsHash="settings-step22",
            evaluationPayload=runtime_payload(),
            correlationId="step22-duplicate-finalized-bar",
        )

        first = runtime.enqueue_finalized_bar_event(event)
        second = runtime.enqueue_finalized_bar_event(event)
        runtime.drain_in_process()

        self.assertEqual(first["jobId"], second["jobId"])
        self.assertFalse(second["accepted"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(runtime.get_job(first["jobId"])["status"], "completed")

    def test_shadow_directional_strategy_cannot_change_quantity_or_direction(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_shadow = service_module.SHADOW_DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_classifier = service_module.REGIME_CLASSIFIER
        payload = tradable_payload()

        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
        try:
            service_module.SHADOW_DIRECTIONAL_STRATEGIES = ()
            baseline = VotingEnsembleService().evaluate(payload)
            service_module.SHADOW_DIRECTIONAL_STRATEGIES = (ShadowSellStrategy(),)
            with_shadow = VotingEnsembleService().evaluate(payload)
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.SHADOW_DIRECTIONAL_STRATEGIES = original_shadow
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_classifier

        self.assertEqual(active_order_signature(baseline), active_order_signature(with_shadow))
        self.assertEqual(with_shadow["shadow_directional_votes"][0]["signal"], "Sell")
        self.assertFalse(with_shadow["shadow_directional_votes"][0]["active"])

    def test_active_inventory_directional_modules_execute_exactly_once(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        calls: dict[str, int] = {}
        wrapped = tuple(CountingSnapshotStrategy(module, calls) for module in original_directional)

        service_module.DIRECTIONAL_STRATEGIES = wrapped
        service_module.CONTEXT_STRATEGIES = ()
        try:
            VotingEnsembleService().evaluate(tradable_payload())
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context

        expected = set(active_module_ids(StrategyCollection.DIRECTIONAL))
        self.assertEqual(set(calls), expected)
        self.assertEqual({module_id: count for module_id, count in calls.items() if count != 1}, {})

    def test_background_failures_fail_safely_without_result_or_order(self) -> None:
        runtime = VotingEnsembleRuntimeOrchestrator(service=FailingService(), auto_start=False)

        job = runtime.enqueue_manual_evaluation(runtime_payload(), correlation_id="step22-failure")
        drained = runtime.drain_in_process()
        status = runtime.get_job(job["jobId"])

        self.assertEqual(len(drained), 1)
        self.assertEqual(status["status"], "failed")
        self.assertIn("synthetic worker failure", status["error"])
        self.assertNotIn("result", status)
        self.assertNotIn("orderSubmissionMode", status)


class CountingSnapshotStrategy:
    def __init__(self, delegate: Any, calls: dict[str, int]) -> None:
        self.delegate = delegate
        self.strategyId = delegate.strategyId
        self.calls = calls

    def evaluate(self, snapshot: Any, *, correlation_id: str, regime_state: Any | None = None) -> Any:
        self.calls[self.strategyId] = self.calls.get(self.strategyId, 0) + 1
        params = inspect.signature(self.delegate.evaluate).parameters
        if "regime_state" in params:
            return self.delegate.evaluate(snapshot, correlation_id=correlation_id, regime_state=regime_state)
        return self.delegate.evaluate(snapshot, correlation_id=correlation_id)


class ShadowSellStrategy:
    strategyId = "opening_range_breakout"

    def evaluate(self, snapshot: Any, *, correlation_id: str) -> Any:
        return directional_signal(
            strategy_id="opening_range_breakout",
            strategy_name="Opening Range Breakout",
            strategy_version="shadow-test",
            family="breakout",
            signal="Sell",
            confidence=1.0,
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=correlation_id,
            evidence=("shadow sell should be diagnostic only",),
            reason_codes=("test.shadow.sell",),
            features={"eventCorrelationId": "shadow-orb"},
        )


class CountingService:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"algorithm_id": "voting_ensemble", "final_signal": "Hold"}


class FailingService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("synthetic worker failure")


def tradable_payload() -> dict[str, Any]:
    payload = snapshot_payload(candles(30))
    payload["market_context"]["operationalHealthSnapshot"].update(
        {"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000}
    )
    return payload


def runtime_payload() -> dict[str, Any]:
    return runtime_evaluate_payload(runtime_candles(30))


def trend_buy(request: VotingEnsembleEvaluateRequest):
    return _vote(
        "Multi-Timeframe Trend Alignment",
        "trend",
        "Buy",
        80,
        "trend",
        "test.trend",
        features={"strategyId": "multi_timeframe_trend_alignment"},
    )


def reversal_buy(request: VotingEnsembleEvaluateRequest):
    return _vote(
        "Failed Breakout Reversal",
        "reversal",
        "Buy",
        80,
        "reversal",
        "test.reversal",
        features={"strategyId": "failed_breakout_reversal"},
    )


def stable_pre_execution_signature(result: dict[str, Any]) -> dict[str, Any]:
    decision = result["preExecutionDecision"]
    return {
        "finalSignal": decision["finalSignal"],
        "baseScore": decision["baseScore"],
        "contextAdjustedScore": decision["contextAdjustedScore"],
        "familyScores": decision["familyScores"],
        "candidate": candidate_signature(decision["candidate"]),
        "orderPlan": order_plan_signature(decision["orderPlan"]),
    }


def active_order_signature(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "final_signal": result["final_signal"],
        "base_score": result["base_score"],
        "context_adjusted_score": result["context_adjusted_score"],
        "candidate": candidate_signature(result["candidate"]),
        "order_plan": order_plan_signature(result["order_plan"]),
    }


def candidate_signature(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "signal": candidate["signal"],
        "direction": candidate["direction"],
        "quantity": candidate["quantity"],
        "entryPrice": candidate["entryPrice"],
        "stopPrice": candidate["stopPrice"],
        "targetPrice": candidate["targetPrice"],
    }


def order_plan_signature(order_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if order_plan is None:
        return None
    return {
        "side": order_plan["side"],
        "orderType": order_plan["orderType"],
        "quantity": order_plan["quantity"],
        "entryPrice": order_plan["entryPrice"],
        "limitPrice": order_plan["limitPrice"],
        "stopPrice": order_plan["stopPrice"],
        "targetPrice": order_plan["targetPrice"],
    }


if __name__ == "__main__":
    unittest.main()
