from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.main import app


SESSION_DATE = date(2026, 1, 5)
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class ApiV2EndpointsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_features_endpoint_returns_versioned_backend_feature_snapshot(self) -> None:
        payload = feature_request(candles(20))

        response = self.client.post("/api/v2/features/evaluate", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["apiVersion"], "api_v2")
        self.assertEqual(body["endpointVersion"], "features_evaluate_v1")
        self.assertTrue(body["configurationHash"])
        self.assertEqual(body["payload"]["featureSnapshot"]["engineVersion"], "point_in_time_feature_engine_v1")

    def test_paper_decision_endpoint_runs_complete_sequence_without_submission(self) -> None:
        payload = paper_decision_request(candles(24))

        response = self.client.post("/api/v2/paper-decisions/evaluate", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        decision = body["payload"]
        self.assertEqual(body["apiVersion"], "api_v2")
        self.assertEqual(body["endpointVersion"], "paper_decision_evaluate_v1")
        self.assertIn("strategyOutputs", decision)
        self.assertIn("contextOutputs", decision)
        self.assertIn("regime", decision)
        self.assertIn("familyEnsemble", decision)
        self.assertIn("gateResults", decision)
        self.assertIn("mlResult", decision)
        self.assertIn("effectivePolicy", decision)
        self.assertIn("orderPlan", decision)
        self.assertTrue(decision["eligibility"]["submissionSeparated"])
        self.assertIn("without submitting", decision["explanation"])

    def test_paper_shadow_endpoint_records_v2_would_have_done_without_submission(self) -> None:
        payload = {
            **paper_decision_request(candles(24)),
            "baselineDecision": {
                "baselineVersion": "current_baseline_v1",
                "decisionTimestampUtc": candles(24)[-1]["timestamp"],
                "signal": "HOLD",
                "wouldTrade": False,
                "orderQuantity": None,
                "expectedNotional": None,
                "rawDecision": {"signal": "Hold"},
                "explanation": "Synthetic baseline comparison input.",
            },
        }

        response = self.client.post("/api/v2/paper-shadow/evaluate", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "paper_shadow_evaluate_v1")
        self.assertTrue(report["mode"]["strategyEngineV2Enabled"])
        self.assertTrue(report["mode"]["familyEnsembleV2Enabled"])
        self.assertTrue(report["mode"]["globalGateEngineEnabled"])
        self.assertFalse(report["mode"]["metaModelV2Enabled"])
        self.assertFalse(report["mode"]["dynamicTradingPolicyEnabled"])
        self.assertFalse(report["mode"]["paperOrderSubmissionEnabled"])
        self.assertFalse(report["automaticPaperSubmission"])
        self.assertEqual(report["v2DecisionSnapshot"]["submissionStatus"], "NOT_SUBMITTED_SHADOW_ONLY")
        self.assertIn("gateDecision", report)
        self.assertIn("effectivePolicy", report)
        self.assertIn("comparison", report)

    def test_deterministic_v2_activation_endpoint_records_shadow_ml_and_rollback(self) -> None:
        response = self.client.post(
            "/api/v2/activation/deterministic/evaluate",
            json={**paper_decision_request(candles(24)), "rollbackMode": "DISABLE_AUTOMATIC_ENTRIES"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "deterministic_v2_activation_evaluate_v1")
        self.assertTrue(report["activationConfig"]["strategyEngineV2Enabled"])
        self.assertTrue(report["activationConfig"]["familyEnsembleV2Enabled"])
        self.assertTrue(report["activationConfig"]["globalGateEngineEnabled"])
        self.assertEqual(report["activationConfig"]["metaModelV2Mode"], "SHADOW")
        self.assertEqual(report["activationConfig"]["dynamicTradingPolicyMode"], "SHADOW")
        self.assertFalse(report["activationConfig"]["mlMayAffectExecution"])
        self.assertFalse(report["activationConfig"]["dynamicPolicyMayAffectExecution"])
        self.assertEqual(report["rollback"]["effectiveExecutionPath"], "AUTOMATIC_ENTRIES_DISABLED")
        self.assertFalse(report["automaticPaperEntryAllowed"])
        self.assertFalse(report["submittedPaperOrder"])
        self.assertEqual(report["mlShadow"]["mode"], "SHADOW")
        self.assertFalse(report["mlShadow"]["appliedToExecution"])
        self.assertEqual(report["dynamicPolicyShadow"]["mode"], "SHADOW")
        self.assertFalse(report["dynamicPolicyShadow"]["appliedToExecution"])

    def test_ml_filter_rollout_endpoint_exercises_unavailable_model_fallback_without_sizing_change(self) -> None:
        response = self.client.post(
            "/api/v2/ml-filter/rollout/evaluate",
            json={
                **paper_decision_request(candles(24)),
                "stage": "FILTER_ACTIVE",
                "shadowComparisonPassed": True,
                "modelArtifact": None,
                "fallbackBehavior": "DETERMINISTIC_BASELINE",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "ml_filter_rollout_evaluate_v1")
        self.assertEqual(report["stage"], "FILTER_ACTIVE")
        self.assertEqual(report["rolloutConfig"]["metaModelV2Mode"], "FILTER")
        self.assertFalse(report["rolloutConfig"]["dynamicRiskSizingEnabled"])
        self.assertFalse(report["rolloutConfig"]["mlMayAlterDirection"])
        self.assertFalse(report["rolloutConfig"]["mlMayCreateTrade"])
        self.assertFalse(report["rolloutConfig"]["mlMayAffectSizing"])
        self.assertTrue(report["staticRiskSizing"]["unchanged"])
        self.assertTrue(report["fallback"]["exercised"])
        self.assertEqual(report["fallback"]["modelHealthStatus"], "UNAVAILABLE")
        self.assertFalse(report["submittedPaperOrder"])
        self.assertTrue(report["submissionSeparated"])

    def test_dynamic_policy_shadow_endpoint_keeps_static_execution_path(self) -> None:
        response = self.client.post(
            "/api/v2/dynamic-policy/shadow/evaluate",
            json={**paper_decision_request(candles(24)), "dynamicPolicyShadowEnabled": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "dynamic_policy_shadow_evaluate_v1")
        self.assertEqual(report["shadowConfig"]["dynamicTradingPolicyMode"], "SHADOW")
        self.assertTrue(report["shadowConfig"]["staticPaperExecutionEnabled"])
        self.assertFalse(report["shadowConfig"]["dynamicMaySubmitOrders"])
        self.assertFalse(report["dynamicSubmittedPaperOrder"])
        self.assertTrue(report["staticPaperOrderPathUnchanged"])
        self.assertTrue(report["replayableSideBySide"])
        self.assertTrue(report["capBreakdownsComplete"])
        self.assertTrue(report["hardLimitsRespected"])
        self.assertTrue(report["baselineRiskNotExceeded"])

    def test_dynamic_policy_activation_endpoint_requires_stage_evidence_and_keeps_ml_filter_only(self) -> None:
        response = self.client.post(
            "/api/v2/dynamic-policy/activation/evaluate",
            json={
                **paper_decision_request(candles(24)),
                "requestedStages": ["RISK_REDUCTION"],
                "stageComparisons": [stage_comparison("RISK_REDUCTION")],
                "rollback": {},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "dynamic_policy_activation_evaluate_v1")
        self.assertEqual(report["activationConfig"]["dynamicTradingPolicyMode"], "ACTIVE")
        self.assertEqual(report["activationConfig"]["metaModelV2Mode"], "FILTER")
        self.assertTrue(report["globalRiskAuthoritative"])
        self.assertTrue(report["brokerReconciliationAuthoritative"])
        self.assertTrue(report["mlLimitedToTradeFiltering"])
        self.assertFalse(report["pyramidingEnabled"])
        self.assertFalse(report["partialExitsEnabled"])
        self.assertFalse(report["trailingBehaviorEnabled"])
        self.assertTrue(report["orderPolicyMatch"]["matchesDisplayedPolicy"])
        self.assertFalse(report["submittedPaperOrder"])

    def test_ml_risk_modifier_experiment_endpoint_is_disabled_by_default(self) -> None:
        response = self.client.post(
            "/api/v2/ml-risk-modifier/experiment/evaluate",
            json={
                **paper_decision_request(candles(24)),
                "requestedStages": ["RISK_REDUCTION"],
                "stageComparisons": [stage_comparison("RISK_REDUCTION")],
                "rollback": {},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        decision = report["mlRiskModifierDecision"]
        self.assertEqual(body["endpointVersion"], "ml_risk_modifier_experiment_evaluate_v1")
        self.assertFalse(report["config"]["experimentEnabled"])
        self.assertTrue(report["featureDisabledByDefault"])
        self.assertTrue(report["deterministicPolicyFallback"])
        self.assertTrue(decision["deterministicFallbackUsed"])
        self.assertFalse(decision["featureEnabled"])
        self.assertFalse(decision["appliedToPaperOrder"])
        self.assertFalse(report["submittedPaperOrder"])

    def test_order_validation_is_separate_from_submission(self) -> None:
        response = self.client.post(
            "/api/v2/orders/validate",
            json={"orderPlan": order_plan(), "gateDecision": None},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()["payload"]
        self.assertTrue(payload["eligible"])
        self.assertTrue(payload["submissionSeparated"])

    def test_backtest_run_can_be_retrieved_by_id(self) -> None:
        response = self.client.post("/api/v2/backtests/run", json=backtest_request(candles(24)))

        self.assertEqual(response.status_code, 200, response.text)
        backtest_id = response.json()["payload"]["backtestId"]
        retrieved = self.client.get(f"/api/v2/backtests/{backtest_id}")

        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertEqual(retrieved.json()["payload"]["backtestId"], backtest_id)

    def test_historical_shadow_comparison_records_v2_without_order_submission(self) -> None:
        rows = candles(24)
        request = {
            **backtest_request(rows),
            "v1Decisions": [
                {
                    "snapshotId": "v1-shadow-1",
                    "symbol": "SPY",
                    "decisionTimestampUtc": rows[0]["timestamp"],
                    "sessionDate": SESSION_DATE.isoformat(),
                    "signal": "HOLD",
                    "tradeOpened": False,
                    "expectedValue": None,
                    "drawdown": 0,
                    "strategyProxyMappings": ["documented V1 proxy map"],
                    "explanation": "V1 reference row.",
                }
            ],
            "minimumCleanV2SnapshotsForMl": 100,
        }

        response = self.client.post("/api/v2/backtests/shadow-comparison", json=request)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        report = body["payload"]["report"]
        self.assertEqual(body["endpointVersion"], "historical_shadow_comparison_v1")
        self.assertTrue(report["featureFlags"]["strategyEngineV2Enabled"])
        self.assertTrue(report["featureFlags"]["familyEnsembleV2Enabled"])
        self.assertFalse(report["featureFlags"]["paperOrderSubmissionEnabled"])
        self.assertEqual(report["storage"]["v1Namespace"], "voting_ensemble_v1_reference")
        self.assertEqual(report["storage"]["v2Namespace"], "family_ensemble_v2_shadow")
        self.assertFalse(report["v2MlTrainingAllowed"])
        self.assertTrue(all(snapshot["orderBehavior"] == "DISABLED" for snapshot in report["v2ShadowSnapshots"]))

    def test_model_status_endpoint_returns_versions_and_hashes(self) -> None:
        response = self.client.get("/api/v2/models/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["endpointVersion"], "models_status_v1")
        self.assertTrue(body["configurationHash"])
        self.assertEqual(body["payload"]["metaModel"]["configurationHash"], "safe_ml_inference_config_v1")


def feature_request(rows: list[dict]) -> dict:
    return {
        "evaluationTimestamp": rows[-1]["timestamp"],
        "sessionDate": SESSION_DATE.isoformat(),
        "spy1mCandles": rows,
        "spy5mCandles": rows,
        "spy15mCandles": rows,
        "qqqAlignedCandles": rows,
        "iwmAlignedCandles": rows,
        "priorDayOHLC": prior_day(),
        "breadthComponents": {"XLK": rows},
        "economicEventState": {"active": False, "importance": "low"},
        "executionStyle": "live",
    }


def paper_decision_request(rows: list[dict]) -> dict:
    return {
        "symbol": "SPY",
        "sessionDate": SESSION_DATE.isoformat(),
        "evaluationTimestamp": rows[-1]["timestamp"],
        "spy1mCandles": rows,
        "spy5mCandles": rows,
        "spy15mCandles": rows,
        "qqqCandles": rows,
        "iwmCandles": rows,
        "priorDayOHLC": prior_day(),
        "breadthComponents": {"XLK": rows},
        "economicEventState": {"active": False, "importance": "low"},
    }


def backtest_request(rows: list[dict]) -> dict:
    return {
        "symbol": "SPY",
        "sessionDate": SESSION_DATE.isoformat(),
        "spy1mCandles": rows,
        "spy5mCandles": rows,
        "spy15mCandles": rows,
        "qqqCandles": rows,
        "iwmCandles": rows,
        "priorDayOHLC": prior_day(),
        "breadthComponents": {"XLK": rows},
        "economicEventState": {"active": False, "importance": "low"},
    }


def candles(count: int) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        timestamp = START + timedelta(minutes=index)
        close = price + 0.08
        rows.append(
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": close + 0.15,
                "low": price - 0.05,
                "close": close,
                "volume": 1000 + index,
                "tradeCount": 100 + index,
                "symbol": "SPY",
                "timeframe": "1Min",
            }
        )
        price = close
    return rows


def prior_day() -> dict:
    return {
        "sessionDate": "2026-01-02",
        "open": 99.0,
        "high": 101.0,
        "low": 98.5,
        "close": 99.5,
    }


def order_plan() -> dict:
    now = START.isoformat().replace("+00:00", "Z")
    return {
        "orderPlanId": "order-api-v2",
        "candidateId": "candidate-api-v2",
        "symbol": "SPY",
        "side": "BUY",
        "orderType": "LIMIT",
        "quantity": 10,
        "entryPrice": 100,
        "stopPrice": 99,
        "targetPrice": 102,
        "limitPrice": 100,
        "timeInForce": "DAY",
        "eligible": True,
        "validationErrors": [],
        "explanation": "Synthetic API V2 order.",
        "generatedAt": now,
        "sessionDate": SESSION_DATE.isoformat(),
        "configurationHash": "order-api-v2",
    }


def stage_comparison(stage: str) -> dict:
    return {
        "stage": stage,
        "walkForwardReplayWindow": "2026-01-01/2026-02-01",
        "paperShadowWindow": "2026-02-02/2026-02-15",
        "walkForwardRiskAdjustedDelta": 0.01,
        "paperShadowRiskAdjustedDelta": 0.0,
        "walkForwardSampleCount": 100,
        "paperShadowSampleCount": 25,
        "improvesOrPreservesRiskAdjustedResults": True,
        "reasonCodes": [f"comparison.{stage.lower()}.passed"],
        "explanation": "Synthetic comparison preserves or improves risk-adjusted results.",
    }


if __name__ == "__main__":
    unittest.main()
