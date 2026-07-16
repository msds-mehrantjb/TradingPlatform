from __future__ import annotations

import json
import shutil
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.persistence import (
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_ARTIFACT_CATEGORIES,
    WeightedVotingFilesystemStateStore,
    load_effective_settings,
    persist_authoritative_artifact,
    persist_effective_settings,
)
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state


TS = datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc)


class WeightedVotingPersistenceTest(unittest.TestCase):
    def test_backend_restart_preserves_authoritative_settings_and_weights(self) -> None:
        with workspace_temp_dir() as temp_dir:
            root = Path(temp_dir) / "data" / "algorithms" / "weighted_voting"
            first_store = WeightedVotingFilesystemStateStore(root=root)
            settings = resolve_effective_settings(timestamp=TS)
            weights = create_unseeded_equal_weight_state(timestamp=TS)

            persist_effective_settings(first_store, settings)
            first_store.write_artifact(
                "active_weights",
                weights.weight_version,
                weights.model_dump(mode="json"),
                run_id="restart-test",
                data_hash="data-hash",
                config_hash=settings.configuration_hash,
                weight_version=weights.weight_version,
                created_at=TS,
            )

            restarted_store = WeightedVotingFilesystemStateStore(root=root)
            loaded_settings = load_effective_settings(restarted_store)
            loaded_weights = restarted_store.read_artifact("active_weights", weights.weight_version)

            self.assertEqual(loaded_settings, settings)
            self.assertEqual(loaded_weights["payload"]["weight_version"], weights.weight_version)
            self.assertEqual(loaded_weights["metadata"]["algorithm_id"], WEIGHTED_VOTING_ALGORITHM_ID)

    def test_frontend_storage_clearing_does_not_reset_backend_algorithm_state(self) -> None:
        with workspace_temp_dir() as temp_dir:
            backend_root = Path(temp_dir) / "data" / "algorithms" / "weighted_voting"
            frontend_storage = Path(temp_dir) / "frontend-local-storage"
            frontend_storage.mkdir()
            (frontend_storage / "display-preferences.json").write_text('{"theme":"dark"}', encoding="utf-8")
            store = WeightedVotingFilesystemStateStore(root=backend_root)
            settings = resolve_effective_settings(timestamp=TS)

            persist_effective_settings(store, settings)
            shutil.rmtree(frontend_storage, ignore_errors=True)

            self.assertEqual(load_effective_settings(WeightedVotingFilesystemStateStore(root=backend_root)), settings)

    def test_all_weighted_voting_artifact_categories_are_isolated_and_metadata_rich(self) -> None:
        required_categories = {
            "configurations",
            "settings",
            "active_weights",
            "historical_weights",
            "strategy_outcomes",
            "strategy_statistics",
            "decisions",
            "order_proposals",
            "gate_results",
            "positions",
            "trades",
            "backtest_runs",
            "walk_forward_folds",
            "equity_curves",
            "daily_update_status",
        }
        self.assertTrue(required_categories.issubset(WEIGHTED_VOTING_ARTIFACT_CATEGORIES))
        with workspace_temp_dir() as temp_dir:
            store = WeightedVotingFilesystemStateStore(root=Path(temp_dir) / "data" / "algorithms" / "weighted_voting")

            for category in sorted(required_categories):
                metadata = persist_authoritative_artifact(
                    store,
                    category=category,
                    artifact_id=f"{category}-artifact",
                    payload={"category": category, "run_id": "run-1", "weight_version": "weights-v1"},
                    run_id="run-1",
                    data_hash="data-hash",
                    config_hash="config-hash",
                    weight_version="weights-v1",
                    created_at=TS,
                )
                envelope = store.read_artifact(category, f"{category}-artifact")

                self.assertEqual(metadata.algorithm_id, WEIGHTED_VOTING_ALGORITHM_ID)
                self.assertEqual(envelope["metadata"]["run_id"], "run-1")
                self.assertEqual(envelope["metadata"]["algorithm_version"], store.algorithm_version)
                self.assertEqual(envelope["metadata"]["data_hash"], "data-hash")
                self.assertEqual(envelope["metadata"]["config_hash"], "config-hash")
                self.assertEqual(envelope["metadata"]["weight_version"], "weights-v1")
                self.assertEqual(envelope["metadata"]["created_at"], TS.isoformat())
                self.assertTrue(str(store.artifact_path(category, f"{category}-artifact")).startswith(str(store.root.resolve())))

    def test_other_algorithms_cannot_write_weighted_voting_artifacts(self) -> None:
        with workspace_temp_dir() as temp_dir:
            store = WeightedVotingFilesystemStateStore(
                root=Path(temp_dir) / "data" / "algorithms" / "weighted_voting",
                writer_algorithm_id="voting_ensemble",
            )

            with self.assertRaises(PermissionError):
                store.write_artifact(
                    "decisions",
                    "foreign-write",
                    {"decision": "nope"},
                    run_id="foreign",
                    data_hash="data",
                    config_hash="config",
                    weight_version="weights",
                    created_at=TS,
                )

    def test_artifact_hash_validation_detects_tampering(self) -> None:
        with workspace_temp_dir() as temp_dir:
            store = WeightedVotingFilesystemStateStore(root=Path(temp_dir) / "data" / "algorithms" / "weighted_voting")
            store.write_artifact(
                "decisions",
                "decision-1",
                {"decision_id": "decision-1", "proposed_side": "Buy"},
                run_id="run-1",
                data_hash="data",
                config_hash="config",
                weight_version="weights",
                created_at=TS,
            )
            path = store.artifact_path("decisions", "decision-1")
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["payload"]["proposed_side"] = "Sell"
            path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")

            with self.assertRaises(ValueError):
                store.read_artifact("decisions", "decision-1")


@contextmanager
def workspace_temp_dir():
    base = Path("backend") / "tests" / ".tmp_weighted_voting_persistence"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
