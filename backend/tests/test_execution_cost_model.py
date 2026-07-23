from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import tick_data
from backend.app.execution import cost_model
from backend.app.execution.cost_model import ExecutionCostModelService
from backend.app.main import app


def configure_scratch(monkeypatch) -> Path:
    scratch = Path("backend/.test_artifacts") / f"execution_cost_model_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(cost_model, "EXECUTION_COST_LEDGER_DIR", scratch / "ledger")
    monkeypatch.setattr(cost_model, "EXECUTION_COST_CANDIDATE_DIR", scratch / "artifacts" / "candidates")
    monkeypatch.setattr(cost_model, "EXECUTION_COST_ACTIVE_DIR", scratch / "artifacts" / "active")
    monkeypatch.setattr(cost_model, "EXECUTION_COST_ACTIVE_HISTORY_DIR", scratch / "artifacts" / "active_history")
    return scratch


def observation(index: int, *, source_mode: str = "paper") -> dict:
    decision_at = datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)
    submitted_at = decision_at + timedelta(milliseconds=350)
    return {
        "observationId": f"paper-fill-{index}",
        "symbol": "SPY",
        "side": "buy" if index % 2 == 0 else "sell",
        "orderType": "limit",
        "sourceMode": source_mode,
        "decisionTimestamp": decision_at.isoformat(),
        "orderSubmissionTimestamp": submitted_at.isoformat(),
        "submittedQuantity": 100,
        "filledQuantity": 100 if index % 5 else 40,
        "averageFillPrice": 600.02,
        "midAtDecision": 600.00,
        "midAtSubmit": 600.01,
        "spreadAtDecision": 0.01,
        "fees": 0.003,
        "estimatedExecutionCost": 0.04,
        "incrementalRealizedNetValue": 0.05,
    }


def proof_observation(index: int, *, incremental_value: float) -> dict:
    decision_at = datetime(2026, 7, 20, 13, 30, tzinfo=UTC) + timedelta(days=index // 70, minutes=index % 70)
    submitted_at = decision_at + timedelta(milliseconds=250)
    return {
        "observationId": f"proof-fill-{index}",
        "symbol": "SPY",
        "side": "buy",
        "orderType": "limit",
        "sourceMode": "paper",
        "decisionTimestamp": decision_at.isoformat(),
        "orderSubmissionTimestamp": submitted_at.isoformat(),
        "submittedQuantity": 100,
        "filledQuantity": 100,
        "averageFillPrice": 600.02,
        "midAtDecision": 600.00,
        "midAtSubmit": 600.01,
        "spreadAtDecision": 0.01,
        "fees": 0.001,
        "estimatedExecutionCost": 0.04,
        "incrementalRealizedNetValue": incremental_value,
    }


def test_execution_cost_model_fallback_is_inactive_without_active_artifact(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        estimate = ExecutionCostModelService().estimate(
            symbol="SPY",
            side="buy",
            order_type="limit",
            feature_snapshot={"features": {}},
            conservative_fallback={
                "metric": "incremental_realized_net_value_after_execution_costs",
                "baseCost": 0.03,
                "totalEstimatedCost": 0.08,
                "fillProbability": 0.7,
                "limitOrderNonFillProbability": 0.3,
                "partialFillProbability": 0.1,
                "expectedPartialFillFraction": 0.95,
                "stopLimitMissProbability": 0.05,
                "opportunityDecay": 0.1,
                "realizedVsEstimatedCostErrorReserve": 0.01,
                "expectedExecutionMultiplier": 0.6,
            },
        )

        assert estimate["status"] == "CONSERVATIVE_FALLBACK_MODEL_INACTIVE"
        assert estimate["modelApplied"] is False
        assert estimate["artifactId"] is None
        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_execution_cost_training_writes_candidate_but_does_not_activate(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        for index in range(25):
            cost_model.record_execution_cost_observation(observation(index))

        result = cost_model.train_execution_cost_candidate("SPY", min_rows=20)

        assert result["status"] == cost_model.MODEL_STATE_TRAINED_CANDIDATE
        assert result["promotionRequired"] is True
        assert result["paperTradingValidated"] is False
        assert cost_model.candidate_artifact_path(result["artifactId"]).exists()
        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_execution_cost_promotion_requires_paper_validated_candidate(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        for index in range(25):
            cost_model.record_execution_cost_observation(observation(index))
        result = cost_model.train_execution_cost_candidate("SPY", min_rows=20)

        try:
            cost_model.promote_execution_cost_candidate(result["artifactId"], symbol="SPY", promoted_by="test")
        except ValueError as exc:
            assert "validation gates pass" in str(exc)
            assert "execution_cost.data.minimum_paper_live_rows" in str(exc)
        else:
            raise AssertionError("promotion should require paper/live validation")

        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_execution_cost_candidate_requires_positive_out_of_sample_net_value_for_promotion(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        for index in range(210):
            value = 0.08 if index < 147 else -0.04
            cost_model.record_execution_cost_observation(proof_observation(index, incremental_value=value))

        result = cost_model.train_execution_cost_candidate("SPY", min_rows=200)
        proof = result["outOfSampleExecutionValueProof"]

        assert result["paperTradingValidated"] is True
        assert result["promotionValidationGates"]["passed"] is False
        assert proof["rows"] >= cost_model.MIN_OUT_OF_SAMPLE_ROWS_FOR_ACTIVE_EXECUTION_COST_MODEL
        assert proof["passed"] is False
        assert proof["averageNetValueSelectedRows"] < 0

        try:
            cost_model.promote_execution_cost_candidate(result["artifactId"], symbol="SPY", promoted_by="test")
        except ValueError as exc:
            assert "validation gates pass" in str(exc)
            assert "execution_cost.oos.positive_incremental_net_value" in str(exc)
        else:
            raise AssertionError("promotion should require positive out-of-sample realized net value")

        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_positive_out_of_sample_proof_does_not_activate_without_explicit_promotion(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        for index in range(210):
            cost_model.record_execution_cost_observation(proof_observation(index, incremental_value=0.08))

        result = cost_model.train_execution_cost_candidate("SPY", min_rows=200)
        artifact = json.loads(cost_model.candidate_artifact_path(result["artifactId"]).read_text(encoding="utf-8"))

        assert result["paperTradingValidated"] is True
        assert result["outOfSampleExecutionValueProof"]["passed"] is True
        assert result["promotionValidationGates"]["passed"] is True
        assert artifact["outOfSampleExecutionValueProof"]["metric"] == "incremental_realized_net_value_after_execution_costs"
        assert artifact["lifecycleState"] == cost_model.MODEL_STATE_TRAINED_CANDIDATE
        assert artifact["approved"] is False
        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_order_fill_logs_calibrate_fill_partial_nonfill_stop_limit_and_adverse_selection(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    try:
        cost_model.record_execution_cost_observation_from_order_log(
            {
                "intent": {
                    "orderIntentId": "partial-1",
                    "clientOrderId": "client-partial-1",
                    "symbol": "SPY",
                    "side": "BUY",
                    "mode": "automatic",
                    "limitPrice": 100.0,
                    "submittedQuantity": 10,
                    "decisionTimestamp": "2026-07-23T13:30:00+00:00",
                    "createdAt": "2026-07-23T13:30:00.100000+00:00",
                },
                "result": {
                    "clientOrderId": "client-partial-1",
                    "orderIntentId": "partial-1",
                    "submitted": True,
                    "status": "PARTIALLY_FILLED",
                    "evaluatedAt": "2026-07-23T13:30:00.250000+00:00",
                    "fill": {
                        "clientOrderId": "client-partial-1",
                        "symbol": "SPY",
                        "side": "BUY",
                        "filledQuantity": 4,
                        "averageFillPrice": 100.03,
                        "status": "PARTIALLY_FILLED",
                        "filledAt": "2026-07-23T13:30:01+00:00",
                    },
                },
                "marketSnapshot": {"bid": 99.99, "ask": 100.01},
                "quoteTicks": [
                    {"timestamp": "2026-07-23T13:29:59.900000Z", "bid_price": 99.99, "ask_price": 100.01},
                    {"timestamp": "2026-07-23T13:30:00.250000Z", "bid_price": 100.00, "ask_price": 100.02},
                ],
                "tradeTicks": [
                    {"timestamp": "2026-07-23T13:30:01Z", "price": 100.03, "size": 4},
                ],
                "executionQuality": {"totalEstimatedCost": 0.02},
            }
        )
        cost_model.record_execution_cost_observation_from_order_log(
            {
                "intent": {
                    "orderIntentId": "miss-1",
                    "clientOrderId": "client-miss-1",
                    "symbol": "SPY",
                    "side": "BUY",
                    "mode": "automatic",
                    "triggerPrice": 100.10,
                    "limitPrice": 100.08,
                    "submittedQuantity": 10,
                    "decisionTimestamp": "2026-07-23T13:31:00+00:00",
                    "createdAt": "2026-07-23T13:31:00.100000+00:00",
                },
                "result": {
                    "clientOrderId": "client-miss-1",
                    "orderIntentId": "miss-1",
                    "submitted": True,
                    "status": "CANCELED",
                    "reasonCodes": ("execution.stop_limit_triggered_not_filled",),
                    "evaluatedAt": "2026-07-23T13:31:00.250000+00:00",
                },
                "marketSnapshot": {"bid": 100.00, "ask": 100.02},
                "executionQuality": {"totalEstimatedCost": 0.02},
            }
        )

        rows = cost_model.load_observations("SPY")
        partial = next(row for row in rows if row["orderIntentId"] == "partial-1")
        miss = next(row for row in rows if row["orderIntentId"] == "miss-1")

        assert partial["labels"]["filled"] is True
        assert partial["labels"]["partialFill"] is True
        assert partial["labels"]["partialFillFraction"] == 0.4
        assert partial["labels"]["adverseSelectionCost"] == 0.03
        assert partial["calibrationInputs"]["adverseSelectionReady"] is True
        assert partial["calibrationInputs"]["quoteTradeTickValidationReady"] is True
        assert partial["tickExecutionQualityValidation"]["quoteTickCount"] == 2
        assert partial["tickExecutionQualityValidation"]["nearestFillTradePrice"] == 100.03
        assert partial["tickExecutionQualityValidation"]["quoteMovementDuringLatency"] == 0.01
        assert miss["labels"]["nonFill"] is True
        assert miss["labels"]["stopLimitMiss"] is True

        trained = cost_model.train_execution_cost_candidate("SPY", min_rows=2)
        artifact = json.loads(cost_model.candidate_artifact_path(trained["artifactId"]).read_text(encoding="utf-8"))
        bucket = artifact["model"]["buckets"]["all|all"]
        assert bucket["fillProbability"] == 0.5
        assert bucket["limitOrderNonFillProbability"] == 0.5
        assert bucket["partialFillProbability"] == 0.5
        assert bucket["stopLimitMissProbability"] == 0.5
        assert bucket["expectedAdverseSelectionCost"] == 0.03
        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_execution_cost_endpoints_keep_model_inactive_until_validated_promotion(monkeypatch) -> None:
    scratch = configure_scratch(monkeypatch)
    monkeypatch.setattr(tick_data, "TICK_DATA_DIR", scratch / "microstructure")
    try:
        client = TestClient(app)

        status = client.get("/api/execution-cost-model/status?symbol=SPY")
        assert status.status_code == 200
        assert status.json()["status"] == "CONSERVATIVE_FALLBACK_MODEL_INACTIVE"
        assert status.json()["modelAppliedByDefault"] is False

        saved = client.post("/api/execution-cost-model/observations", json=observation(1))
        assert saved.status_code == 200
        assert saved.json()["saved"] is True

        ingested = client.post(
            "/api/execution-cost-model/order-logs/ingest",
            json={
                "intent": {
                    "orderIntentId": "endpoint-log-1",
                    "clientOrderId": "endpoint-client-1",
                    "symbol": "SPY",
                    "side": "BUY",
                    "mode": "automatic",
                    "limitPrice": 100.0,
                    "submittedQuantity": 10,
                    "decisionTimestamp": "2026-07-23T13:30:00+00:00",
                },
                "result": {
                    "clientOrderId": "endpoint-client-1",
                    "orderIntentId": "endpoint-log-1",
                    "submitted": True,
                    "status": "FILLED",
                    "fill": {
                        "clientOrderId": "endpoint-client-1",
                        "symbol": "SPY",
                        "side": "BUY",
                        "filledQuantity": 10,
                        "averageFillPrice": 100.01,
                        "status": "FILLED",
                        "filledAt": "2026-07-23T13:30:01+00:00",
                    },
                },
                "marketSnapshot": {"bid": 99.99, "ask": 100.01},
            },
        )
        assert ingested.status_code == 200
        assert ingested.json()["saved"] is True

        ticks = client.post(
            "/api/microstructure/ticks/ingest",
            json={
                "symbol": "SPY",
                "feed": "iex",
                "quotes": [{"timestamp": "2026-07-23T13:30:00Z", "bid_price": 99.99, "ask_price": 100.01}],
                "trades": [{"timestamp": "2026-07-23T13:30:01Z", "price": 100.01, "size": 10}],
            },
        )
        assert ticks.status_code == 200
        assert ticks.json()["activationPolicy"] == "tick_data_is_passive_until_paper_or_live_model_promotion"

        trained = client.post("/api/execution-cost-model/train", json={"symbol": "SPY", "minRows": 1})
        assert trained.status_code == 200
        artifact_id = trained.json()["artifactId"]

        rejected = client.post(
            "/api/execution-cost-model/artifacts/promote",
            json={"symbol": "SPY", "artifactId": artifact_id, "promotedBy": "test"},
        )
        assert rejected.status_code == 400
        assert cost_model.active_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
