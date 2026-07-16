from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.wca.contracts import WcaEvaluateRequest
from backend.app.algorithms.wca.shadow_comparison import WcaShadowComparisonTolerance, run_wca_shadow_comparison
from backend.app.main import app


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wca" / "golden_snapshots.json"


def test_shadow_comparison_records_field_by_field_evidence_without_submission() -> None:
    repository = MemoryShadowEvidenceRepository()
    request = WcaEvaluateRequest.model_validate(snapshot())

    evidence = run_wca_shadow_comparison(
        request,
        repository=repository,
        tolerance=WcaShadowComparisonTolerance(numeric=10, quantity=10_000, price=1_000),
    )

    assert evidence.submission_allowed is False
    assert evidence.rollout_phase_passed is evidence.within_tolerance
    assert {row.field for row in evidence.field_comparisons} == {
        "strategy_outputs",
        "scores",
        "decision",
        "quantity",
        "stop",
        "target",
        "gate_results",
    }
    assert repository.evidence == [evidence]
    if evidence.within_tolerance:
        assert "wca.shadow_comparison.within_tolerance" in evidence.reason_codes
    else:
        assert "wca.shadow_comparison.tolerance_failed" in evidence.reason_codes


def test_shadow_comparison_blocks_rollout_phase_when_tolerances_fail() -> None:
    evidence = run_wca_shadow_comparison(
        WcaEvaluateRequest.model_validate(snapshot()),
        tolerance=WcaShadowComparisonTolerance(numeric=0, quantity=0, price=0),
    )

    assert evidence.within_tolerance is False
    assert evidence.rollout_phase_passed is False
    assert evidence.mismatched_fields
    assert "wca.shadow_comparison.tolerance_failed" in evidence.reason_codes


def test_shadow_comparison_api_records_evidence_without_order_submission() -> None:
    response = TestClient(app).post("/api/wca/shadow/compare", json=snapshot())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["submission_allowed"] is False
    assert body["compared_fields"] == [
        "strategy_outputs",
        "scores",
        "decision",
        "quantity",
        "stop",
        "target",
        "gate_results",
    ]
    assert "field_comparisons" in body


class MemoryShadowEvidenceRepository:
    def __init__(self) -> None:
        self.evidence = []

    def write_shadow_comparison_evidence(self, evidence) -> None:
        self.evidence.append(evidence)


def snapshot() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["snapshots"][0]
