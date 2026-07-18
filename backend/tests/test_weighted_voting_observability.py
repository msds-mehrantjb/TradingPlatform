from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.decision_gates import WeightedGateEvaluationMode, WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.execution_gateway import submit_weighted_voting_paper_order
from backend.app.algorithms.weighted_voting.observability import (
    DECISION_OBSERVABILITY_PREFIX,
    EXECUTION_OBSERVABILITY_PREFIX,
    METRICS_KEY,
    WEIGHTED_VOTING_OBSERVABILITY_REQUIRED_FIELDS,
    WEIGHTED_VOTING_OBSERVABILITY_STAGES,
    observability_status,
)
from backend.app.algorithms.weighted_voting.models import WeightedGateResult, WeightedGateStatus
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class WeightedVotingObservabilityTest(unittest.TestCase):
    def test_evaluation_records_full_immutable_decision_snapshot_and_metrics(self) -> None:
        store = MemoryStore()
        result = WeightedVotingService(store=store).evaluate(evaluate_payload())
        decision_id = result["decision"]["decision_id"]

        snapshot = store.snapshots[f"{DECISION_OBSERVABILITY_PREFIX}{decision_id}"]
        metrics = store.snapshots[METRICS_KEY]

        self.assertTrue(snapshot["immutable"])
        self.assertEqual(snapshot["decisionId"], decision_id)
        self.assertEqual(datetime.fromisoformat(snapshot["dataTimestamp"]), datetime.fromisoformat(result["decision"]["data_timestamp"].replace("Z", "+00:00")))
        for required_key in (
            "dataFreshness",
            "marketSnapshotHash",
            "strategyOutputs",
            "strategySignals",
            "strategyProbabilities",
            "activeWeights",
            "weightStages",
            "familyContributions",
            "aggregatedScores",
            "scoreTotals",
            "winner",
            "edge",
            "marketCondition",
            "settings",
            "configurationVersions",
            "localGateOutcomes",
            "localGateResults",
            "proposedQuantity",
            "globalGateResult",
            "executableQuantity",
            "finalProposal",
            "orderLevels",
            "stageTimings",
            "exceptions",
            "dataQualityWarnings",
            "reasonCodes",
            "explanation",
            "rejectionReason",
            "eventualOutcome",
            "snapshotHash",
        ):
            self.assertIn(required_key, snapshot)
        self.assertEqual(snapshot["authoritativeSource"], "data/algorithms/weighted_voting/observability/")
        self.assertEqual(snapshot["marketSnapshotHash"], result["decision"]["data_manifest_hash"])
        self.assertEqual(snapshot["strategyOutputs"], snapshot["strategySignals"])
        self.assertEqual(snapshot["activeWeights"], snapshot["weightStages"]["activeWeightState"])
        self.assertEqual(snapshot["aggregatedScores"], snapshot["scoreTotals"])
        self.assertEqual(snapshot["localGateOutcomes"], snapshot["localGateResults"])
        self.assertEqual(snapshot["finalProposal"]["acceptedQuantity"], snapshot["executableQuantity"])
        self.assertEqual(set(snapshot["stageTimings"]), set(WEIGHTED_VOTING_OBSERVABILITY_STAGES))
        self.assertIn("weighted_voting.observability.decision_recorded", snapshot["reasonCodes"])
        self.assertEqual(snapshot["configurationVersions"]["configurationVersion"], result["decision"]["configuration_version"])
        self.assertEqual(snapshot["configurationVersions"]["weightVersion"], result["decision"]["weight_version"])
        self.assertEqual(snapshot["configurationVersions"]["settingsVersion"], result["decision"]["settings_version"])
        self.assertEqual(snapshot["configurationVersions"]["dataManifestHash"], result["decision"]["data_manifest_hash"])
        self.assertEqual(snapshot["eventualOutcome"]["status"], "pending")
        self.assertIn("defaultSettings", snapshot["settings"])
        self.assertIn("dynamicMultipliers", snapshot["settings"])
        self.assertIn("effectiveSettings", snapshot["settings"])
        self.assertIn(result["decision"]["signal"], metrics["decisionsBySide"])
        self.assertGreaterEqual(metrics["decisionCount"], 1)
        self.assertIn("averageWeight", metrics)
        self.assertIn("gateRejectionFrequency", metrics)
        self.assertIn("sizingLimitingFactors", metrics)
        self.assertIn("schedulerStatus", metrics)

    def test_observability_inventory_is_weighted_voting_authoritative(self) -> None:
        status = observability_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertEqual(status["authoritativeSource"], "data/algorithms/weighted_voting/observability/")
        self.assertTrue(status["isolation"]["ownsNamespace"])
        self.assertFalse(status["isolation"]["sharedDashboardsMayMutate"])
        self.assertIn("final_proposal", WEIGHTED_VOTING_OBSERVABILITY_REQUIRED_FIELDS)
        self.assertIn("exceptions", status["requiredFields"])
        self.assertIn("observability_persistence", status["stageTimingContract"])

    def test_hold_or_rejected_decisions_are_explainable_and_quantities_are_distinct_fields(self) -> None:
        store = MemoryStore()
        payload = evaluate_payload()
        payload["globalGateResponse"] = {
            "action": "REJECT_NEW_ENTRY",
            "maximumAllowedQuantity": 0,
            "maximumAdditionalRiskDollars": 0,
            "rejectionReasons": ["global.test_reject"],
            "evaluatedAt": payload["data_timestamp"],
            "configurationHash": "global-test-reject",
        }

        result = WeightedVotingService(store=store).evaluate(payload)
        decision_id = result["decision"]["decision_id"]
        snapshot = store.snapshots[f"{DECISION_OBSERVABILITY_PREFIX}{decision_id}"]

        self.assertIn("proposedQuantity", snapshot)
        self.assertIn("executableQuantity", snapshot)
        self.assertIn("global.test_reject", snapshot["rejectionReason"])
        self.assertGreater(len(snapshot["rejectionReason"]), 0)

    def test_paper_order_execution_records_eventual_outcome_traceable_to_decision(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        gateway = PaperOrderGateway(broker, store)
        proposal = global_proposal()
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="global-config",
        )
        application = apply_global_gate_response(proposal, response)

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=application,
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        outcome = store.snapshots[f"{EXECUTION_OBSERVABILITY_PREFIX}{proposal.orderIntentId}"]
        metrics = store.snapshots[METRICS_KEY]
        self.assertTrue(result.submitted)
        self.assertEqual(outcome["traceability"]["decisionId"], proposal.decisionId)
        self.assertEqual(outcome["traceability"]["orderIntentId"], proposal.orderIntentId)
        self.assertEqual(outcome["eventualOutcome"]["status"], "FILLED")
        self.assertEqual(outcome["eventualOutcome"]["fill"]["orderIntentId"], proposal.orderIntentId)
        self.assertIn("FILLED", metrics["executionStatus"])
        self.assertIn("fillQuality", metrics)


def validated_rollout_flags() -> WeightedVotingRolloutFlags:
    return WeightedVotingRolloutFlags(
        v2_enabled=True,
        shadow_mode=False,
        dynamic_reduction_enabled=True,
        dynamic_increase_enabled=True,
        auto_submit_enabled=True,
    )


def validated_rollout_validation() -> WeightedVotingRolloutValidation:
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=True,
        shadow_comparison_passed=True,
        static_equal_weights_passed=True,
        performance_weights_validated=True,
        dynamic_reduction_validated=True,
        dynamic_entry_exit_validated=True,
        dynamic_increase_validated=True,
        manual_paper_submission_validated=True,
        tests_passed=True,
        paper_validations_passed=True,
        live_trading_enabled=False,
    )


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


class FakePaperBroker:
    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill:
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="weighted_voting",
            orderIntentId="wv-observability-intent",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=10,
            averageFillPrice=100.05,
            status="FILLED",
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []


def evaluate_payload(count: int = 95) -> dict:
    rows = candle_rows(count=count)
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "account_equity": 100000,
        "available_buying_power": 100000,
        "capital_available": 100000,
    }


def candle_rows(count: int = 95) -> list[dict]:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return rows


def global_proposal() -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="wv-observability-decision",
        orderIntentId="wv-observability-intent",
        intent="new_entry",
        symbol="SPY",
        side="BUY",
        quantity=10,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        plannedRiskDollars=100.0,
        settingsSnapshot={"settings_version": "test"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="strategy-state",
        proposedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="wv-observability-config",
    )


def local_gate(passed: bool) -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=passed,
        mode=WeightedGateEvaluationMode.AUTOMATIC,
        gate_results=(
            WeightedGateResult(
                gate_id="observability_test_gate",
                gate_name="Observability Test Gate",
                status=WeightedGateStatus.PASS if passed else WeightedGateStatus.FAIL,
                blocks_order=not passed,
                data_timestamp=NOW,
                reason_codes=("weighted_voting.observability_gate_passed" if passed else "weighted_voting.observability_gate_failed",),
                explanation="Synthetic observability gate.",
            ),
        ),
        reason_codes=("weighted_voting.observability_gate_passed" if passed else "weighted_voting.observability_gate_failed",),
        explanation="Synthetic gate pipeline.",
    )


if __name__ == "__main__":
    unittest.main()
