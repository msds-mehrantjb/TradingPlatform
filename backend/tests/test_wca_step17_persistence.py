from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from backend.app.algorithms.wca.contracts import WcaBacktestSideMode, WcaSide
from backend.app.algorithms.wca.repository import (
    WCA_PERSISTENCE_MIGRATION_VERSION,
    WcaSqliteRepository,
    apply_wca_persistence_migrations,
    classify_wca_local_storage_key,
)
from backend.app.algorithms.wca.service import WcaService
from backend.tests.test_wca_step14_15_backend_backtest import backtest_request, fake_voters


WCA_TABLES = (
    "wca_configuration_versions",
    "wca_strategy_versions",
    "wca_weight_snapshots",
    "wca_confidence_calibrations",
    "wca_market_status_snapshots",
    "wca_effective_setting_snapshots",
    "wca_decisions",
    "wca_local_gate_evaluations",
    "global_gate_evaluations",
    "wca_proposed_orders",
    "wca_execution_results",
    "wca_trade_ledger",
    "wca_broker_reconciliations",
    "wca_shadow_comparison_evidence",
    "wca_paper_stability_validations",
    "wca_backtest_runs",
    "wca_backtest_trades",
    "wca_strategy_performance",
)

REQUIRED_COLUMNS = {
    "algorithm_id",
    "symbol",
    "timestamp",
    "configuration_version",
    "engine_version",
    "market_snapshot_id",
    "decision_id",
}


class WcaStep17PersistenceTests(unittest.TestCase):
    def test_migration_creates_all_authoritative_wca_tables_idempotently(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            apply_wca_persistence_migrations(conn)
            apply_wca_persistence_migrations(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
                (WCA_PERSISTENCE_MIGRATION_VERSION,),
            ).fetchone()[0]

            self.assertEqual(migration_count, 1)
            self.assertTrue(set(WCA_TABLES).issubset(tables))
            for table in WCA_TABLES:
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                self.assertTrue(REQUIRED_COLUMNS.issubset(columns), table)
                self.assertIn("payload_json", columns, table)

    def test_service_persists_configuration_defaults_and_backtest_run_history(self) -> None:
        db_path = temp_db_path()
        repository = WcaSqliteRepository(f"sqlite:///{db_path}")
        service = WcaService(repository=repository)
        request = backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT)

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = service.run_backtest(request)

        reloaded_repository = WcaSqliteRepository(f"sqlite:///{db_path}")
        reloaded = reloaded_repository.load_backtest_result(result.run_configuration.run_id)
        counts = reloaded_repository.table_counts().table_counts

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.total_pnl, result.total_pnl)
        self.assertGreater(counts["wca_configuration_versions"], 0)
        self.assertGreater(counts["wca_strategy_versions"], 0)
        self.assertGreater(counts["wca_weight_snapshots"], 0)
        self.assertEqual(counts["wca_backtest_runs"], 1)
        self.assertEqual(counts["wca_backtest_trades"], len(result.trades))
        self.assertEqual(counts["wca_trade_ledger"], len(result.trades))
        self.assertEqual(counts["wca_decisions"], len(result.decisions))
        self.assertGreater(counts["wca_local_gate_evaluations"], 0)
        self.assertGreater(counts["global_gate_evaluations"], 0)
        self.assertGreater(counts["wca_proposed_orders"], 0)
        self.assertGreater(counts["wca_execution_results"], 0)
        self.assertGreater(counts["wca_strategy_performance"], 0)

    def test_backend_restart_preserves_run_history_through_service_lookup(self) -> None:
        db_path = temp_db_path()
        first_service = WcaService(repository=WcaSqliteRepository(f"sqlite:///{db_path}"))
        request = backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT)
        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = first_service.run_backtest(request)

        restarted_service = WcaService(repository=WcaSqliteRepository(f"sqlite:///{db_path}"))
        restored = restarted_service.backtest_result(result.run_configuration.run_id)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.run_configuration.run_id, result.run_configuration.run_id)
        self.assertEqual(restarted_service.backtest_status(result.run_configuration.run_id)["status"], "complete")

    def test_legacy_local_storage_authoritative_wca_keys_are_ignored(self) -> None:
        self.assertEqual(classify_wca_local_storage_key("weighted-confidence-trading-settings-v1"), "ignored_authoritative_backend_state")
        self.assertEqual(classify_wca_local_storage_key("confidence-backtest-result-v1"), "ignored_authoritative_backend_state")
        self.assertEqual(classify_wca_local_storage_key("ui-wca-expanded-panels"), "allowed_visual_preference")
        self.assertEqual(classify_wca_local_storage_key("selected-tab-wca"), "allowed_visual_preference")


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step17-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
