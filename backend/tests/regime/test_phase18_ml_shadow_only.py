from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.ml.predictor import evaluate_regime_ml_shadow
from backend.app.main import app


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-phase18",
    "accountId": "paper-phase18",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


def test_phase18_ml_shadow_output_has_no_direction_sizing_gate_or_order_authority() -> None:
    output = evaluate_regime_ml_shadow(
        {"decisionId": "regime-decision-1", "signal": "Buy", "orderProposal": {"quantity": 10}},
        artifact={"trusted": True, "suggestedSignal": "Sell", "suggestedQuantity": 100},
        mode="active",
    )

    assert output["mode"] == "shadow"
    assert output["storage"] == "regime_ml_predictions"
    assert output["mayCreateDirection"] is False
    assert output["mayReverseSignal"] is False
    assert output["mayIncreaseSize"] is False
    assert output["mayLoosenGate"] is False
    assert output["mayCreateOrder"] is False
    assert output["orderAuthority"] == "none"


def test_phase18_confirm_only_ml_still_cannot_increase_authority() -> None:
    output = evaluate_regime_ml_shadow({"decisionId": "regime-decision-1", "signal": "Hold"}, mode="confirm_only")

    assert output["mode"] == "confirm_only"
    assert output["maximumAutomaticPromotionMode"] == "confirm_only"
    assert output["mayCreateDirection"] is False
    assert output["mayIncreaseSize"] is False
    assert output["mayLoosenGate"] is False
    assert output["mayCreateOrder"] is False


def test_phase18_deterministic_backtest_runs_when_ml_is_off_and_unavailable() -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY, "ml_shadow": {"mode": "off", "mayAlterSignals": False, "mayAlterSizing": False, "mayAlterOrders": False}}).as_dict()

    result = run_regime_backtest({"symbol": "SPY", "candles": _candles(30), "__regime_settings_snapshot": settings})

    assert result["algorithmId"] == "regime"
    assert len(result["decisions"]) == 30
    assert result["settingsSnapshot"]["ml_shadow"]["mode"] == "off"
    assert result["parity"]["apiOrFrontendTradingAuthority"] is False


def test_phase18_api_cannot_record_frontend_supplied_ml_promotion_evidence() -> None:
    client = TestClient(app)

    response = client.post("/api/regime/ml/promotion/evidence", json={**_promotion_evidence(), "requestSource": "frontend"})

    assert response.status_code == 200
    assert response.json()["recorded"] is False
    assert response.json()["reason"] == "frontend_supplied_evidence_rejected"


def _promotion_evidence() -> dict[str, object]:
    now = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)
    return {
        "artifact_id": "phase18-artifact",
        "artifact_hash": "sha256:phase18",
        "model_version": "regime-ml-shadow-v1",
        "feature_schema_version": "regime-features-v1",
        "label_version": "regime-labels-v1",
        "deterministic_baseline_version": "regime_algorithm_v3_backend_authoritative",
        "walk_forward_passed": True,
        "untouched_holdout_passed": True,
        "deterministic_baseline_comparison_passed": True,
        "calibration_passed": True,
        "leakage_tests_passed": True,
        "paper_stability_passed": True,
        "paper_shadow_decision_count": 300,
        "paper_trading_day_count": 12,
        "distinct_regimes_observed": 6,
        "minimum_regime_coverage_passed": True,
        "global_risk_violations": 0,
        "unexpected_decision_mutations": 0,
        "broker_reconciliation_failures": 0,
        "operational_errors": 0,
        "performance_review_passed": True,
        "rollback_artifact_retained": True,
        "tests_passed": True,
        "evidence_generated_at": (now - timedelta(days=1)).isoformat(),
        "evidence_expiration_at": (now + timedelta(days=1)).isoformat(),
        "backend_evidence_source": "regime_ml_promotion_worker",
        "replay_evidence_id": "phase18-replay",
        "walk_forward_evidence_id": "phase18-walk-forward",
        "holdout_evidence_id": "phase18-holdout",
        "paper_stability_evidence_id": "phase18-paper-stability",
        "promotion_audit_id": "phase18-audit",
        "created_by": "regime-ml-promotion-worker",
        "activation_reason": "phase18 backend evidence test",
        "rollback_artifact_id": "phase18-rollback",
    }


def _candles(count: int) -> list[dict[str, float | str]]:
    start = datetime(2026, 7, 31, 13, 30, tzinfo=UTC)
    price = 100.0
    rows: list[dict[str, float | str]] = []
    for index in range(count):
        price += 0.04
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": price - 0.02,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price,
                "volume": 120_000 + index,
            }
        )
    return rows
