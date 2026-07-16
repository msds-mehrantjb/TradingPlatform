from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from datetime import timedelta

from backend.app.domain.models import FillResult, OrderPlan, Signal
from backend.app.domain.snapshot_store import (
    DecisionSnapshotV2Store,
    DuplicateDecisionSnapshotError,
    DuplicateOrderDecisionError,
    apply_decision_snapshot_v2_migrations,
)
from backend.tests.test_decision_snapshot_v2_archive import CONFIG_HASH, NOW, snapshot


def order_plan(order_plan_id: str = "order-plan-1") -> OrderPlan:
    return OrderPlan(
        orderPlanId=order_plan_id,
        candidateId="candidate-1",
        symbol="SPY",
        side=Signal.BUY,
        orderType="STOP_LIMIT",
        quantity=10,
        entryPrice=100,
        stopPrice=99,
        targetPrice=102,
        limitPrice=100.02,
        timeInForce="DAY",
        eligible=True,
        explanation="Synthetic order plan.",
        generatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash=CONFIG_HASH,
    )


def fill_result(fill_id: str = "fill-1") -> FillResult:
    return FillResult(
        fillId=fill_id,
        orderPlanId="order-plan-1",
        symbol="SPY",
        side=Signal.BUY,
        quantity=10,
        averageFillPrice=100.01,
        fees=0.25,
        slippagePerShare=0.01,
        filledAt=NOW + timedelta(minutes=1),
        sessionDate=NOW.date(),
        explanation="Synthetic paper fill.",
    )


class SnapshotPersistenceMigrationTest(unittest.TestCase):
    def test_migration_works_on_copy_and_keeps_existing_v1_data_readable(self) -> None:
        source = sqlite3.connect(":memory:")
        copy = sqlite3.connect(":memory:")
        try:
            source.execute("CREATE TABLE candles (symbol TEXT NOT NULL, timestamp TEXT NOT NULL)")
            source.execute("INSERT INTO candles(symbol, timestamp) VALUES ('SPY', '2026-01-05T15:45:00Z')")
            source.execute("CREATE TABLE v1_decision_snapshots (snapshot_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL)")
            source.execute("INSERT INTO v1_decision_snapshots(snapshot_id, raw_json) VALUES ('v1-1', '{}')")
            source.commit()

            copy.executescript("\n".join(source.iterdump()))
            apply_decision_snapshot_v2_migrations(copy)

            tables = {
                row[0]
                for row in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            self.assertIn("decision_snapshots", tables)
            self.assertIn("strategy_outputs", tables)
            self.assertIn("model_training_runs", tables)
            self.assertEqual(
                copy.execute("SELECT raw_json FROM v1_decision_snapshots WHERE snapshot_id = 'v1-1'").fetchone()[0],
                "{}",
            )
            self.assertEqual(copy.execute("SELECT symbol FROM candles").fetchone()[0], "SPY")
        finally:
            source.close()
            copy.close()

    def test_save_supports_normalized_queries_and_raw_replay(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = DecisionSnapshotV2Store(conn)
            stored = snapshot(
                orderPlan=order_plan(),
                fillResult=fill_result(),
                fills=[fill_result()],
                finalOutcome={"rMultiple": 1.25, "closedAt": (NOW + timedelta(minutes=5)).isoformat()},
            )
            result = store.save(stored)

            self.assertTrue(result.inserted)
            self.assertEqual(result.normalizedRows["strategy_outputs"], 1)
            self.assertEqual(result.normalizedRows["regime_states"], 1)
            self.assertEqual(result.normalizedRows["gate_results"], 1)
            self.assertEqual(result.normalizedRows["policy_decisions"], 1)
            self.assertEqual(result.normalizedRows["orders"], 1)
            self.assertEqual(result.normalizedRows["fills"], 1)
            self.assertEqual(result.normalizedRows["trade_outcomes"], 1)
            self.assertEqual(store.raw_snapshot(stored.snapshotId)["snapshotId"], stored.snapshotId)
            self.assertEqual(store.strategy_outputs(stored.snapshotId)[0]["strategy_id"], "trend_alignment")

    def test_duplicate_decision_is_rejected_idempotently(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = DecisionSnapshotV2Store(conn)
            stored = snapshot()
            first = store.save(stored)
            second = store.save(stored)

            self.assertTrue(first.inserted)
            self.assertFalse(second.inserted)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM strategy_outputs").fetchone()[0],
                1,
            )

    def test_duplicate_decision_identity_with_different_raw_json_raises(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = DecisionSnapshotV2Store(conn)
            store.save(snapshot())

            with self.assertRaisesRegex(DuplicateDecisionSnapshotError, "different raw snapshot"):
                store.save(snapshot(snapshotId="snapshot-raw-conflict", codeVersion="different-code"))

    def test_duplicate_order_decision_is_rejected(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            store = DecisionSnapshotV2Store(conn)
            store.save(snapshot(snapshotId="snapshot-order-1", orderPlan=order_plan()))

            with self.assertRaisesRegex(DuplicateOrderDecisionError, "duplicate order decision"):
                store.save(
                    snapshot(
                        snapshotId="snapshot-order-2",
                        decisionTimestamp=NOW + timedelta(minutes=1),
                        decisionTimestampUtc=NOW + timedelta(minutes=1),
                        configurationHash="snapshot-config-2",
                        orderPlan=order_plan("order-plan-2"),
                    )
                )

    def test_migration_is_idempotent(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            apply_decision_snapshot_v2_migrations(conn)
            apply_decision_snapshot_v2_migrations(conn)

            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 'decision_snapshot_v2_normalized_001'"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
