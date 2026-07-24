from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from backend.app.algorithms.session import SessionDecisionJsonlStore, build_session_decision_record, resolve_session_profile
from session_test_fixtures import NOW, classification_fixture


def test_session_persistence_round_trip_is_idempotent() -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch)
    classification = classification_fixture()
    record = build_session_decision_record(classification=classification, profile=resolve_session_profile(classification), output_mode="shadow", transition_state={"currentBehavior": classification.behavior.value}, persisted_at=NOW)

    assert store.write_record(record) == "persisted"
    assert store.write_record(record) == "duplicate"
    records = store.read_records(symbol="SPY", session_date="2026-07-23")

    assert len(records) == 1
    assert records[0].classificationId == record.classificationId
    assert records[0].featureSchemaVersion
    assert records[0].classifierVersion
    shutil.rmtree(scratch, ignore_errors=True)


def test_session_persistence_unknown_output_mode_remains_auditable() -> None:
    scratch = _scratch_path()
    store = SessionDecisionJsonlStore(root=scratch)
    classification = classification_fixture(block=True)
    record = build_session_decision_record(classification=classification, profile=resolve_session_profile(classification), output_mode="display_only", transition_state={}, persisted_at=NOW)

    store.write_record(record)
    saved = store.read_records(symbol="SPY")[0]

    assert saved.outputMode == "display_only"
    assert saved.safetyBlocks["blockNewEntries"] is True
    shutil.rmtree(scratch, ignore_errors=True)


def _scratch_path() -> Path:
    path = Path("backend") / ".test_artifacts" / f"session_named_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path
