from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.persistence import REGIME_PERSISTENCE_TABLES, RegimeSqliteRepository
from backend.app.main import app


REQUIRED_COLUMNS = {
    "algorithm_id",
    "algorithm_version",
    "settings_version",
    "strategy_version",
    "profile_version",
    "model_version",
    "timestamp",
    "symbol",
    "data_timestamp",
    "decision_id",
    "order_id",
}


class RegimePhase14PersistenceTest(unittest.TestCase):
    def test_migration_creates_all_regime_tables_with_required_identity_columns(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")

        for table in REGIME_PERSISTENCE_TABLES:
            self.assertTrue(REQUIRED_COLUMNS.issubset(set(repository.table_columns(table))), table)

        with sqlite3.connect(path) as conn:
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        self.assertIn("regime_persistence_phase14_001", versions)

    def test_decision_snapshot_fans_out_and_redacts_secrets(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")
        result = repository.record_decision_snapshot(sample_snapshot())

        self.assertTrue(result["recorded"])
        counts = repository.table_counts()
        self.assertEqual(counts["regime_decisions"], 1)
        self.assertEqual(counts["regime_classifications"], 1)
        self.assertEqual(counts["regime_transitions"], 1)
        self.assertEqual(counts["regime_strategy_outputs"], 2)
        self.assertEqual(counts["regime_context_outputs"], 1)
        self.assertEqual(counts["regime_safety_results"], 1)
        self.assertEqual(counts["regime_family_scores"], 1)
        self.assertEqual(counts["regime_effective_profiles"], 1)
        self.assertEqual(counts["regime_order_intents"], 1)
        self.assertEqual(counts["regime_ml_predictions"], 1)

        with sqlite3.connect(path) as conn:
            payload = conn.execute("SELECT payload_json FROM regime_decisions").fetchone()[0]
        self.assertNotIn("top-secret", payload)
        self.assertIn("[REDACTED]", payload)

    def test_backtest_result_persists_run_and_trade_records(self) -> None:
        path = temp_db_path()
        repository = RegimeSqliteRepository(f"sqlite:///{path}")
        result = repository.record_backtest_result(sample_backtest())

        self.assertTrue(result["recorded"])
        self.assertEqual(result["tradeCount"], 1)
        counts = repository.table_counts()
        self.assertEqual(counts["regime_backtest_runs"], 1)
        self.assertEqual(counts["regime_backtest_trades"], 1)

    def test_api_exposes_regime_persistence_schema(self) -> None:
        client = TestClient(app)
        response = client.get("/api/regime/persistence/schema")

        self.assertEqual(response.status_code, 200, response.text)
        tables = response.json()["tables"]
        self.assertIn("regime_decisions", tables)
        self.assertTrue(REQUIRED_COLUMNS.issubset(set(tables["regime_decisions"])))


def sample_snapshot() -> dict:
    return {
        "capturedAt": "2026-01-05T15:30:00.000Z",
        "symbol": "SPY",
        "apiSecret": "top-secret",
        "regime": {
            "algorithmId": "regime",
            "algorithmVersion": "regime_algorithm_v2",
            "settingsVersion": "regime_base_settings_v1",
            "strategyVersion": "regime_strategy_catalog_v2",
            "profileVersion": "regime_profile_matrix_v1",
            "modelVersion": "regime_ml_test_v1",
            "timestamp": "2026-01-05T15:30:00.000Z",
            "dataTimestamp": "2026-01-05T15:30:00.000Z",
            "symbol": "SPY",
            "decisionId": "regime:SPY:2026-01-05T15:30:00.000Z",
            "orderId": "regime-order-1",
            "rawClassification": {"rawRegime": "strong_uptrend", "axes": {"direction": "strong_up"}, "missingInputs": []},
            "confirmedState": {"confirmedRegime": "strong_uptrend", "candidateCount": 3},
            "selectedStrategies": [{"strategy": "moving_average_trend", "signal": "buy"}],
            "skippedStrategies": [{"name": "rsi_mean_reversion", "reason": "incompatible"}],
            "contextResults": [{"strategyId": "vwap_position", "multiplier": 1}],
            "safetyResults": [{"strategyId": "stale_data", "passed": True}],
            "familyAggregation": [{"family": "trend", "buyScore": 0.2}],
            "effectiveSettings": {"profileId": "strong_uptrend:regime_profile_matrix_v1"},
            "ml": {"prediction": {"probabilityVector": {"strong_uptrend": 0.72}}},
            "orderIntent": {"decisionId": "regime:SPY:2026-01-05T15:30:00.000Z", "idempotencyKey": "regime-order-1"},
        },
    }


def temp_db_path() -> Path:
    root = Path(__file__).resolve().parent / "tmp" / "regime_phase14"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid4().hex}.sqlite"


def sample_backtest() -> dict:
    return {
        "algorithmId": "regime",
        "engineVersion": "regime_backtest_v2",
        "symbol": "SPY",
        "cacheKey": "SPY:2:start:end",
        "decisions": [],
        "trades": [
            {
                "tradeId": "trade-1",
                "entryDecisionTimestamp": "2026-01-05T15:30:00.000Z",
                "entryAt": "2026-01-05T15:31:00.000Z",
                "exitAt": "2026-01-05T15:40:00.000Z",
                "side": "Long",
                "quantity": 10,
                "pnl": 12.5,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
