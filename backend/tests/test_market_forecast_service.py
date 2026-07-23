from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import market_forecast
from backend.app import tick_data
from backend.app.main import app
from backend.app.market_forecast import HEURISTIC_ESTIMATE_NOT_ML, FORECAST_STATUS_MODEL_UNAVAILABLE, MODEL_STATE_ACTIVE, MODEL_STATE_RETIRED, MODEL_STATE_TRAINED_CANDIDATE, MarketForecastService
from backend.app.market_forecast import FORECAST_STATUS_INFERENCE_NOT_RUN, extract_market_forecast_features, flatten_forecast_features, read_market_forecast_prediction_log, record_market_forecast_prediction, resolve_market_forecast_record, select_approved_forecast_artifact
from backend.app.ml import v1 as ml_v1
from backend.app.train_market_forecast import (
    AMBIGUOUS_LABEL_POLICY,
    DEFAULT_SUCCESS_THRESHOLD,
    ENTRY_PRICE_POLICY,
    OUTCOME_STOP,
    OUTCOME_TARGET,
    OUTCOME_TIMEOUT,
    build_training_rows,
    chronological_forecast_partitions,
    evaluate_outcome_probabilities,
    future_trade_outcome_label,
    train_and_score_validation_fold,
    xgboost_hyperparameters,
)


def candles(count: int = 60) -> list[dict]:
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    rows: list[dict] = []
    price = 600.0
    for index in range(count):
        price += 0.05 if index % 3 else -0.02
        rows.append(
            {
                "symbol": "SPY",
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": price - 0.03,
                "high": price + 0.08,
                "low": price - 0.08,
                "close": price,
                "volume": 100_000 + index * 100,
                "vwap": price - 0.01,
            }
        )
    return rows


def promotion_gate_metadata(*, average_ev: float = 0.04, total_ev: float = 0.48, selected_trades: int = 12) -> dict:
    return {
        "trainingModelKind": "logistic",
        "featureNames": ["bias"],
        "calibration": {"method": "per_class_platt_sigmoid"},
        "optimizationPolicy": {"thresholdSource": "threshold_selection_ev_optimized_threshold"},
        "validationDeploymentParity": {"featureSchemaHash": "schema-test"},
        "walkForwardValidation": {"summary": {"status": "validated", "folds": 1}},
        "metrics": {
            "finalUntouchedTest": {
                "rows": 100,
                "expectedValue": {
                    "selectedTrades": selected_trades,
                    "averageEvSelected": average_ev,
                    "totalEvSelected": total_ev,
                },
            }
        },
    }


def test_service_never_applies_heuristic_when_approved_model_is_missing() -> None:
    with (
        patch("backend.app.market_forecast.load_market_forecast_artifact", return_value=None),
        patch("backend.app.market_forecast.run_forecast_inference", side_effect=AssertionError("inference must not run without approved artifact")),
    ):
        forecast = MarketForecastService().predict(candles())

    assert forecast["status"] == FORECAST_STATUS_MODEL_UNAVAILABLE
    assert forecast["forecast_status"] == FORECAST_STATUS_MODEL_UNAVAILABLE
    assert forecast["inferenceStatus"] == FORECAST_STATUS_INFERENCE_NOT_RUN
    assert forecast["inference"] == {
        "status": FORECAST_STATUS_INFERENCE_NOT_RUN,
        "probabilityBuySuccess": None,
        "probabilitySellSuccess": None,
        "probabilityTimeout": None,
    }
    assert forecast["probabilityBuySuccess"] is None
    assert forecast["probabilitySellSuccess"] is None
    assert forecast["probabilityTimeout"] is None
    assert forecast["probability_buy"] is None
    assert forecast["probability_sell"] is None
    assert forecast["allowed"] is False
    assert forecast["inference_performed"] is False
    assert forecast["forecast_applied_to_order"] is False
    assert forecast["forecastAppliedToOrder"] is False
    assert forecast["decision"]["action"] == "no_trade"
    assert forecast["decision"]["confidence"] is None
    assert forecast["heuristicEstimate"]["status"] == HEURISTIC_ESTIMATE_NOT_ML
    assert forecast["heuristicEstimate"]["forecast_applied_to_order"] is False
    assert forecast["heuristicEstimate"]["probabilityBuySuccess"] is not None
    assert forecast["heuristicEstimate"]["probabilitySellSuccess"] is not None


def test_artifact_selection_is_separate_from_inference_and_requires_approval() -> None:
    artifact = {
        "version": market_forecast.MODEL_VERSION,
        "modelKind": "logistic",
        "lifecycleState": MODEL_STATE_ACTIVE,
        "approved": True,
        "weights": {"bias": 1.0},
    }

    with patch("backend.app.market_forecast.load_market_forecast_artifact", return_value=artifact):
        selected = select_approved_forecast_artifact("SPY")

    assert selected is not None
    assert selected.symbol == "SPY"
    assert selected.payload is artifact

    with patch("backend.app.market_forecast.load_market_forecast_artifact", return_value={**artifact, "approved": False}):
        assert select_approved_forecast_artifact("SPY") is None

    with patch("backend.app.market_forecast.load_market_forecast_artifact", return_value={**artifact, "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE}):
        assert select_approved_forecast_artifact("SPY") is None


def test_market_forecast_algorithm_features_use_stable_contract_ids_not_old_display_names() -> None:
    features = extract_market_forecast_features(candles(90))
    algorithm = features["algorithm"]
    flattened = flatten_forecast_features(features)
    expected_ids = {
        "multi_timeframe_trend_alignment",
        "first_pullback_after_open",
        "failed_breakout_reversal",
        "liquidity_sweep_reversal",
        "bollinger_atr_reversion",
    }

    for strategy_id in expected_ids:
        prefix = f"strategy__{strategy_id}"
        assert f"{prefix}__signal" in algorithm
        assert f"{prefix}__confidence_or_setup_quality" in algorithm
        assert f"{prefix}__eligible" in algorithm
        assert f"{prefix}__regime_compatibility" in algorithm
        assert f"algorithm.{prefix}__signal" in flattened

    forbidden_fragments = ("rsi_mean_reversion", "macd", "algorithm_1", "algorithm_2", "algorithm_3")
    assert not any(fragment in key for fragment in forbidden_fragments for key in algorithm)
    assert not any("weight_state" in key or "learned_state" in key or "performance_history" in key for key in flattened)


def test_execution_quality_adjusts_ev_and_can_block_orders() -> None:
    artifact = {
        "version": market_forecast.MODEL_VERSION,
        "modelKind": "logistic",
        "lifecycleState": MODEL_STATE_ACTIVE,
        "approved": True,
        "weightsByClass": {
            market_forecast.OUTCOME_TARGET: {"bias": 3.0},
            market_forecast.OUTCOME_STOP: {"bias": -2.0},
            market_forecast.OUTCOME_TIMEOUT: {"bias": -2.0},
        },
        "threshold": 0.45,
        "labelConfig": {
            "profitTargetDollars": 0.08,
            "stopLossDollars": 0.08,
            "minTargetPct": 0.0,
            "minStopPct": 0.0,
            "targetAtrMultiplier": 0.0,
            "stopAtrMultiplier": 0.0,
        },
    }

    with (
        patch("backend.app.market_forecast.load_market_forecast_artifact", return_value=artifact),
        patch("backend.app.execution.cost_model.select_approved_execution_cost_artifact", return_value=None),
    ):
        forecast = MarketForecastService().predict(candles(90), spread=0.30, slippage=0.08, fees=0.02)

    execution = forecast["executionQuality"]
    assert execution["metric"] == "incremental_realized_net_value_after_execution_costs"
    assert execution["status"] == "CONSERVATIVE_FALLBACK_MODEL_INACTIVE"
    assert execution["modelApplied"] is False
    assert execution["totalEstimatedCost"] > execution["baseCost"]
    assert forecast["expectedValue"] == forecast["incrementalExpectedNetValueAfterExecutionCosts"]
    assert forecast["allowed"] is False
    assert any("Execution-adjusted net expected value" in reason or "Fill probability" in reason for reason in forecast["decision"]["reasons"])


def test_resolution_records_incremental_realized_net_value_after_execution_costs() -> None:
    rows = candles(12)
    rows[1] = {**rows[1], "open": rows[0]["close"] + 0.04, "high": rows[0]["close"] + 0.20, "low": rows[0]["close"] - 0.01, "close": rows[0]["close"] + 0.12}
    record = {
        "predictionTimestamp": rows[0]["timestamp"],
        "horizonMinutes": 5,
        "entryPrice": rows[0]["close"],
        "prediction": {"decisionAction": "buy", "candidateAction": "buy"},
        "barriers": {"targetDistance": 0.10, "stopDistance": 0.10},
        "costs": 0.02,
        "baseCosts": 0.02,
        "executionQuality": {"totalEstimatedCost": 0.05, "stopLimitMissProbability": 0.10},
        "actual": {"status": "pending"},
    }

    resolved = resolve_market_forecast_record(record, rows)

    actual = resolved["record"]["actual"]
    assert resolved["resolved"] is True
    assert actual["realizedExecution"]["status"] == "resolved"
    assert actual["incrementalRealizedNetValueAfterExecutionCosts"] == round(
        actual["buyValueDollars"] - actual["realizedExecution"]["realizedExecutionCost"],
        4,
    )
    assert "realizedVersusEstimatedCostError" in actual


def test_authoritative_ledger_records_every_one_minute_forecast_invocation(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"market_forecast_event_ledger_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(market_forecast, "PREDICTION_LOG_DIR", scratch / "event_ledger")
    monkeypatch.setattr(market_forecast, "LEGACY_PREDICTION_LOG_DIR", scratch / "legacy")

    try:
        rows = candles(2)
        assert rows[-1]["timestamp"].endswith("13:31:00+00:00")
        forecast_one = MarketForecastService().predict(rows)
        forecast_two = MarketForecastService().predict(rows)

        first = record_market_forecast_prediction("SPY", "iex", "1Min", rows, forecast_one)
        second = record_market_forecast_prediction("SPY", "iex", "1Min", rows, forecast_two)
        ledger = read_market_forecast_prediction_log("SPY", date="2026-07-23", limit=10)

        assert first["saved"] is True
        assert second["saved"] is True
        assert first["recordingPolicy"] == "every_forecast_invocation_from_finalized_one_minute_events"
        assert len(ledger["records"]) == 2
        assert {record["forecastInvocationId"] for record in ledger["records"]} == {
            forecast_one["forecastInvocationId"],
            forecast_two["forecastInvocationId"],
        }
        record = ledger["records"][0]
        for field in (
            "eventTimestamp",
            "barFinalizationTimestamp",
            "featureReadyTimestamp",
            "decisionTimestamp",
            "artifactId",
            "featureSchemaHash",
            "predictionProbabilities",
            "uncertainty",
            "expectedCosts",
        ):
            assert field in record
        assert record["actual"]["actualFill"]["status"] == "pending"
        assert record["actual"]["realizedOutcome"] is None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_market_forecast_promotion_is_explicit_atomic_and_reversible(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"market_forecast_lifecycle_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(market_forecast, "FORECAST_CANDIDATE_ARTIFACT_DIR", scratch / "artifacts" / "candidates")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_ARTIFACT_DIR", scratch / "artifacts" / "active")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_HISTORY_DIR", scratch / "artifacts" / "active_history")

    try:
        active_path = market_forecast.market_forecast_artifact_path("SPY")
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": "previous-active",
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_ACTIVE,
                    "promotionStatus": MODEL_STATE_ACTIVE,
                    "approved": True,
                    "weights": {"bias": 0.1},
                }
            ),
            encoding="utf-8",
        )

        artifact_id = "candidate-1"
        candidate_path = market_forecast.market_forecast_candidate_artifact_path(artifact_id)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": artifact_id,
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE,
                    "promotionStatus": MODEL_STATE_TRAINED_CANDIDATE,
                    "approved": False,
                    "weights": {"bias": 0.9},
                    **promotion_gate_metadata(),
                }
            ),
            encoding="utf-8",
        )

        promoted = market_forecast.promote_market_forecast_candidate(
            artifact_id,
            symbol="SPY",
            promoted_by="test",
            reason="unit test promotion",
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        original_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        rollback_path = promoted["rollbackArtifactPath"]

        assert promoted["status"] == "promoted"
        assert promoted["promotionValidationGates"]["passed"] is True
        assert active["artifactId"] == artifact_id
        assert active["lifecycleState"] == MODEL_STATE_ACTIVE
        assert active["approved"] is True
        assert active["promotionValidationGates"]["passed"] is True
        assert original_candidate["lifecycleState"] == MODEL_STATE_TRAINED_CANDIDATE
        assert rollback_path
        assert json.loads(Path(rollback_path).read_text(encoding="utf-8"))["lifecycleState"] == MODEL_STATE_RETIRED

        rolled_back = market_forecast.rollback_active_market_forecast_artifact(
            symbol="SPY",
            rollback_artifact_path=rollback_path,
            rolled_back_by="test",
            reason="unit test rollback",
        )
        restored = json.loads(active_path.read_text(encoding="utf-8"))

        assert rolled_back["status"] == "rolled_back"
        assert restored["artifactId"] == "previous-active"
        assert restored["lifecycleState"] == MODEL_STATE_ACTIVE
        assert restored["approved"] is True
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_market_forecast_promotion_requires_positive_oos_net_value(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"market_forecast_oos_gate_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(market_forecast, "FORECAST_CANDIDATE_ARTIFACT_DIR", scratch / "artifacts" / "candidates")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_ARTIFACT_DIR", scratch / "artifacts" / "active")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_HISTORY_DIR", scratch / "artifacts" / "active_history")

    try:
        artifact_id = "negative-oos-candidate"
        candidate_path = market_forecast.market_forecast_candidate_artifact_path(artifact_id)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": artifact_id,
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE,
                    "promotionStatus": MODEL_STATE_TRAINED_CANDIDATE,
                    "approved": False,
                    "weights": {"bias": 0.9},
                    **promotion_gate_metadata(average_ev=-0.01, total_ev=-0.08, selected_trades=8),
                }
            ),
            encoding="utf-8",
        )

        try:
            market_forecast.promote_market_forecast_candidate(artifact_id, symbol="SPY", promoted_by="test")
        except ValueError as exc:
            assert "validation gates pass" in str(exc)
            assert "market_forecast.oos.positive_incremental_net_value" in str(exc)
        else:
            raise AssertionError("market forecast promotion should require positive final holdout net value")

        assert market_forecast.market_forecast_artifact_path("SPY").exists() is False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_market_forecast_promotion_requires_all_validation_gates_not_only_oos(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"market_forecast_metadata_gate_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(market_forecast, "FORECAST_CANDIDATE_ARTIFACT_DIR", scratch / "artifacts" / "candidates")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_ARTIFACT_DIR", scratch / "artifacts" / "active")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_HISTORY_DIR", scratch / "artifacts" / "active_history")

    try:
        artifact_id = "positive-oos-missing-gates"
        candidate_path = market_forecast.market_forecast_candidate_artifact_path(artifact_id)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": artifact_id,
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE,
                    "promotionStatus": MODEL_STATE_TRAINED_CANDIDATE,
                    "approved": False,
                    "weights": {"bias": 0.9},
                    "metrics": promotion_gate_metadata()["metrics"],
                }
            ),
            encoding="utf-8",
        )

        try:
            market_forecast.promote_market_forecast_candidate(artifact_id, symbol="SPY", promoted_by="test")
        except ValueError as exc:
            assert "market_forecast.validation.walk_forward_present" in str(exc)
            assert "market_forecast.calibration.present" in str(exc)
        else:
            raise AssertionError("market forecast promotion should require every validation gate")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_market_forecast_promotion_and_rollback_endpoints(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"market_forecast_api_lifecycle_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(market_forecast, "FORECAST_CANDIDATE_ARTIFACT_DIR", scratch / "artifacts" / "candidates")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_ARTIFACT_DIR", scratch / "artifacts" / "active")
    monkeypatch.setattr(market_forecast, "FORECAST_ACTIVE_HISTORY_DIR", scratch / "artifacts" / "active_history")

    try:
        active_path = market_forecast.market_forecast_artifact_path("SPY")
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": "api-previous-active",
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_ACTIVE,
                    "promotionStatus": MODEL_STATE_ACTIVE,
                    "approved": True,
                    "weights": {"bias": 0.1},
                }
            ),
            encoding="utf-8",
        )

        artifact_id = "api-candidate-1"
        candidate_path = market_forecast.market_forecast_candidate_artifact_path(artifact_id)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "version": market_forecast.MODEL_VERSION,
                    "artifactId": artifact_id,
                    "modelKind": "logistic",
                    "lifecycleState": MODEL_STATE_TRAINED_CANDIDATE,
                    "promotionStatus": MODEL_STATE_TRAINED_CANDIDATE,
                    "approved": False,
                    "weights": {"bias": 0.9},
                    **promotion_gate_metadata(),
                }
            ),
            encoding="utf-8",
        )

        client = TestClient(app)
        promoted = client.post(
            "/api/market-forecast/artifacts/promote",
            json={"symbol": "SPY", "artifactId": artifact_id, "promotedBy": "test", "reason": "endpoint test"},
        )

        assert promoted.status_code == 200
        promoted_body = promoted.json()
        assert promoted_body["status"] == "promoted"
        assert promoted_body["promotionValidationGates"]["passed"] is True
        assert json.loads(active_path.read_text(encoding="utf-8"))["artifactId"] == artifact_id
        assert json.loads(candidate_path.read_text(encoding="utf-8"))["lifecycleState"] == MODEL_STATE_TRAINED_CANDIDATE

        rolled_back = client.post(
            "/api/market-forecast/artifacts/rollback",
            json={
                "symbol": "SPY",
                "rollbackArtifactPath": promoted_body["rollbackArtifactPath"],
                "rolledBackBy": "test",
                "reason": "endpoint rollback test",
            },
        )

        assert rolled_back.status_code == 200
        assert rolled_back.json()["status"] == "rolled_back"
        assert json.loads(active_path.read_text(encoding="utf-8"))["artifactId"] == "api-previous-active"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_ml_v1_forecast_facade_uses_authoritative_service() -> None:
    with patch.object(
        market_forecast.MARKET_FORECAST_SERVICE,
        "predict",
        return_value={"status": FORECAST_STATUS_MODEL_UNAVAILABLE},
    ) as predict:
        forecast = ml_v1.forecast_prediction(candles(2), microstructure_rows=[])

    assert forecast == {"status": FORECAST_STATUS_MODEL_UNAVAILABLE}
    predict.assert_called_once()


def test_forecast_training_uses_four_chronological_partitions_and_report_only_holdout_metrics() -> None:
    rows = [
        {
            "timestamp": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)).isoformat(),
            "labelEnd": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index + 5)).isoformat(),
            "features": {"bias": 1.0},
            "target": 1 if index % 4 == 0 else -1 if index % 4 == 1 else 0,
            "targetProfit": 1.0,
            "stopLoss": 0.5,
            "tradingCost": 0.02,
        }
        for index in range(1_000)
    ]

    partitions = chronological_forecast_partitions(rows)

    assert [len(partitions[name]) for name in ("training", "calibration", "thresholdSelection", "finalUntouchedTest")] == [600, 150, 150, 100]
    assert partitions["training"][-1]["timestamp"] < partitions["calibration"][0]["timestamp"]
    assert partitions["calibration"][-1]["timestamp"] < partitions["thresholdSelection"][0]["timestamp"]
    assert partitions["thresholdSelection"][-1]["timestamp"] < partitions["finalUntouchedTest"][0]["timestamp"]

    scored = [
        ({OUTCOME_STOP: 0.2, OUTCOME_TIMEOUT: 0.2, OUTCOME_TARGET: 0.6}, 1, {"targetProfit": 1.0, "stopLoss": 0.5, "tradingCost": 0.02}),
        ({OUTCOME_STOP: 0.7, OUTCOME_TIMEOUT: 0.1, OUTCOME_TARGET: 0.2}, -1, {"targetProfit": 1.0, "stopLoss": 0.5, "tradingCost": 0.02}),
    ]
    final_metrics = evaluate_outcome_probabilities(
        scored,
        threshold=DEFAULT_SUCCESS_THRESHOLD,
        include_threshold_optimization=False,
    )

    assert final_metrics["thresholdOptimization"] == "disabled_report_only"
    assert "evOptimizedThreshold" not in final_metrics
    assert "evOptimized" not in final_metrics


def test_xgboost_walk_forward_fold_uses_xgboost_model_family_and_parity_metadata() -> None:
    training_rows = [
        {
            "timestamp": (datetime(2026, 1, 2, tzinfo=UTC) + timedelta(minutes=index)).isoformat(),
            "features": {"bias": 1.0, "trend": float(index % 5)},
            "target": 1 if index % 3 == 0 else -1 if index % 3 == 1 else 0,
            "targetProfit": 1.0,
            "stopLoss": 0.5,
            "tradingCost": 0.02,
        }
        for index in range(90)
    ]
    validation_rows = [
        {
            "timestamp": (datetime(2026, 1, 2, tzinfo=UTC) + timedelta(minutes=90 + index)).isoformat(),
            "features": {"bias": 1.0, "trend": float(index % 5)},
            "target": 1 if index % 3 == 0 else -1 if index % 3 == 1 else 0,
            "targetProfit": 1.0,
            "stopLoss": 0.5,
            "tradingCost": 0.02,
        }
        for index in range(30)
    ]

    def fake_xgboost_probabilities(rows: list[dict], feature_names: list[str], model_file: str) -> list[dict[str, float]]:
        return [
            {OUTCOME_STOP: 0.2, OUTCOME_TIMEOUT: 0.2, OUTCOME_TARGET: 0.6}
            if int(row["target"]) == 1
            else {OUTCOME_STOP: 0.6, OUTCOME_TIMEOUT: 0.2, OUTCOME_TARGET: 0.2}
            if int(row["target"]) == -1
            else {OUTCOME_STOP: 0.2, OUTCOME_TIMEOUT: 0.6, OUTCOME_TARGET: 0.2}
            for row in rows
        ]

    with (
        patch("backend.app.train_market_forecast.train_logistic_model", side_effect=AssertionError("logistic branch must not validate xgboost")),
        patch("backend.app.train_market_forecast.train_xgboost_fold_model", return_value={"modelFile": "fold-xgb.json"}) as train_xgb,
        patch("backend.app.train_market_forecast.xgboost_saved_probabilities", side_effect=fake_xgboost_probabilities),
    ):
        metrics = train_and_score_validation_fold(
            training_rows,
            validation_rows,
            ["bias", "trend"],
            model_kind="xgboost",
            symbol="SPY",
            fold=1,
        )

    train_xgb.assert_called_once()
    parity = metrics["validationParity"]
    assert parity["modelFamily"] == "xgboost"
    assert parity["hyperparameters"] == xgboost_hyperparameters()
    assert parity["thresholdPolicy"] == "expected_value_optimized_threshold"
    assert parity["thresholdPolicySource"] == "walk_forward_validation_fold"
    assert parity["barrierDefinitions"] == "row_economics_from_shared_triple_barrier_labels"
    assert parity["costAssumptions"] == "row_economics_tradingCost"


def test_forecast_label_enters_at_first_executable_price_and_never_uses_close_tie_break() -> None:
    start = datetime(2026, 1, 3, tzinfo=UTC)
    rows = [
        {
            "symbol": "SPY",
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 100_000,
            "vwap": 100.0,
        }
        for index in range(10)
    ]
    rows[1] = {**rows[1], "open": 105.0, "high": 105.5, "low": 104.5, "close": 105.1}
    rows[2] = {**rows[2], "open": 105.2, "high": 106.2, "low": 105.0, "close": 106.0}

    label = future_trade_outcome_label(
        rows,
        0,
        profit_target=1.0,
        stop_loss=1.0,
        min_target_pct=0.0,
        min_stop_pct=0.0,
        target_atr_multiplier=0.0,
        stop_atr_multiplier=0.0,
    )

    assert label == 1

    rows[1] = {**rows[1], "open": 100.0, "high": 101.2, "low": 98.8, "close": 100.5}
    ambiguous = future_trade_outcome_label(
        rows,
        0,
        profit_target=1.0,
        stop_loss=1.0,
        min_target_pct=0.0,
        min_stop_pct=0.0,
        target_atr_multiplier=0.0,
        stop_atr_multiplier=0.0,
    )

    assert ambiguous is None


def test_ambiguous_forecast_labels_are_excluded_from_training_rows() -> None:
    start = datetime(2026, 1, 4, tzinfo=UTC)
    rows = [
        {
            "symbol": "SPY",
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 100_000,
            "vwap": 100.0,
        }
        for index in range(72)
    ]
    rows[61] = {**rows[61], "open": 100.0, "high": 101.2, "low": 98.8, "close": 100.5}
    rows[62] = {**rows[62], "open": 100.0, "high": 101.2, "low": 99.7, "close": 101.0}

    training_rows = build_training_rows(
        rows,
        profit_target=1.0,
        stop_loss=1.0,
        min_target_pct=0.0,
        min_stop_pct=0.0,
        target_atr_multiplier=0.0,
        stop_atr_multiplier=0.0,
        atr_lookback_minutes=5,
        training_cost=0.02,
        max_rows=1_000,
    )

    assert all(row["decisionTimestamp"] != rows[60]["timestamp"] for row in training_rows)
    assert any(row["entryTimestamp"] == rows[62]["timestamp"] for row in training_rows)
    assert all(row["entryPricePolicy"] == ENTRY_PRICE_POLICY for row in training_rows)
    assert all(row["sameCandleTargetStopAmbiguityPolicy"] == AMBIGUOUS_LABEL_POLICY for row in training_rows)


def test_quote_trade_ticks_resolve_ambiguous_forecast_label_when_available(monkeypatch) -> None:
    scratch = Path("backend/.test_artifacts") / f"tick_label_resolution_{uuid.uuid4().hex}"
    shutil.rmtree(scratch, ignore_errors=True)
    monkeypatch.setattr(tick_data, "TICK_DATA_DIR", scratch / "microstructure")
    try:
        start = datetime(2026, 1, 5, tzinfo=UTC)
        rows = [
            {
                "symbol": "SPY",
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 100_000,
                "vwap": 100.0,
            }
            for index in range(10)
        ]
        rows[1] = {**rows[1], "open": 100.0, "high": 101.2, "low": 98.8, "close": 100.5}
        tick_data.append_quote_trade_ticks(
            symbol="SPY",
            feed="iex",
            quotes=[],
            trades=[
                {"timestamp": "2026-01-05T00:01:05Z", "price": 101.05, "size": 10},
                {"timestamp": "2026-01-05T00:01:10Z", "price": 98.95, "size": 10},
            ],
        )

        label = future_trade_outcome_label(
            rows,
            0,
            profit_target=1.0,
            stop_loss=1.0,
            min_target_pct=0.0,
            min_stop_pct=0.0,
            target_atr_multiplier=0.0,
            stop_atr_multiplier=0.0,
            symbol="SPY",
            feed="iex",
        )

        assert label == 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
