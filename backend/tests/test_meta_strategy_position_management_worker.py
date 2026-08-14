from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.workers import MetaStrategyPositionManagementWorker


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPositionManagementWorkerTest(unittest.TestCase):
    def test_stop_hit_creates_durable_protective_exit_intent(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROTECTIVE_STOP")
        lifecycle = env.inventory.inventory_records("position_lifecycle", limit=5)[0]
        self.assertEqual(result["status"], "POSITION_MANAGEMENT_EVALUATED")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["status"], "PENDING")
        self.assertEqual(outbox["payload"]["intent"], "protective_exit")
        self.assertEqual(outbox["payload"]["side"], "SELL")
        self.assertEqual(outbox["payload"]["quantity"], 10)
        self.assertEqual(lifecycle["payload"]["exitReason"], "PROTECTIVE_STOP")
        self.assertTrue(lifecycle["payload"]["entryBlockedWhileExitUnresolved"])

    def test_existing_unresolved_exit_prevents_duplicate_exit_intent(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        candle = {"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0}
        env.enqueue_position_job(candle=candle, key="first")
        env.worker().run_once(now=NOW + timedelta(minutes=1))
        env.enqueue_position_job(candle=candle, key="second")

        result = env.worker().run_once(now=NOW + timedelta(minutes=2))

        self.assertEqual(result["createdExitIntentCount"], 0)
        self.assertIn("SPY", result["blockedEntrySymbols"])

    def test_missing_protective_state_is_quarantined_without_guessing_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position(with_order_intent=False)
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        quarantine = env.inventory.inventory_records("quarantine", limit=5)[0]
        self.assertEqual(result["createdExitIntentCount"], 0)
        self.assertEqual(quarantine["payload"]["quarantineReason"], "PROTECTIVE_STATE_MISSING")
        with self.assertRaises(KeyError):
            env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROTECTIVE_STOP")

    def test_end_of_day_deadline_creates_liquidation_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.enqueue_position_job(
            candle={"symbol": "SPY", "timestamp": NOW.isoformat(), "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0},
            extra={"endOfDayExitAt": NOW.isoformat(), "noOvernight": True},
        )

        result = env.worker().run_once(now=NOW)

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.SESSION_END")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["intent"], "end_of_day_liquidation")
        self.assertEqual(outbox["payload"]["exitReason"], "SESSION_END")

    def test_profit_target_creates_risk_reducing_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 104.25, "low": 99.5, "close": 104.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROFIT_TARGET")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["exitReason"], "PROFIT_TARGET")
        self.assertEqual(outbox["payload"]["side"], "SELL")
        self.assertEqual(outbox["payload"]["quantity"], 10)

    def test_signal_exit_creates_event_risk_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.enqueue_position_job(
            candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            extra={"signalInvalidation": {"SPY": True}},
        )

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.EVENT_RISK")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["exitReason"], "EVENT_RISK")
        self.assertEqual(outbox["payload"]["quantity"], 10)

    def test_maximum_holding_time_creates_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position(maximum_holding_minutes=1)
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=2)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5})

        result = env.worker().run_once(now=NOW + timedelta(minutes=2))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.MAXIMUM_HOLD")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["exitReason"], "MAXIMUM_HOLD")
        self.assertEqual(outbox["payload"]["quantity"], 10)

    def test_partial_fill_exit_uses_open_meta_strategy_quantity_without_reversal(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position(quantity=4)
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 104.5, "low": 99.5, "close": 104.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROFIT_TARGET")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["side"], "SELL")
        self.assertEqual(outbox["payload"]["quantity"], 4)

    def test_paper_button_off_does_not_block_position_exit(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off_with_open_position",
            now=NOW,
        )
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROTECTIVE_STOP")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["intent"], "protective_exit")

    def test_readiness_false_does_not_stop_existing_position_management(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position()
        env.jobs.write_gateway_snapshot(
            "meta_strategy.readiness.report",
            {
                "algorithmId": "meta_strategy",
                "status": "REJECTED",
                "complete": False,
                "paperReady": False,
                "reasonCodes": ("meta_strategy.readiness.market_data_unhealthy",),
            },
            now=NOW,
        )
        env.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0})

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = env.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROTECTIVE_STOP")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["intent"], "protective_exit")
        self.assertEqual(env.inventory.current_inventory_snapshot(mark_prices={"SPY": 98.0}).open_positions[0].quantity, 10)

    def test_restart_with_open_position_manages_from_rebuilt_inventory(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        env = RuntimeEnv(database_url=database_url)
        env.seed_long_position()
        restarted = RuntimeEnv(database_url=database_url)
        restarted.enqueue_position_job(candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 104.25, "low": 99.5, "close": 104.0})

        result = restarted.worker().run_once(now=NOW + timedelta(minutes=1))

        outbox = restarted.jobs.outbox_for_order_intent("meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.PROFIT_TARGET")
        self.assertEqual(result["createdExitIntentCount"], 1)
        self.assertEqual(outbox["payload"]["quantity"], 10)

    def test_payload_only_foreign_position_request_is_quarantined_without_exit_intent(self) -> None:
        env = RuntimeEnv()
        env.enqueue_position_job(
            candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 101.0, "low": 97.0, "close": 98.0},
            extra={
                "positionManagementRequests": [
                    {
                        "position": {
                            "algorithmId": "weighted_voting",
                            "capitalPartitionId": "weighted_voting.paper.default",
                            "positionId": "weighted-voting-spy-position",
                            "symbol": "SPY",
                            "side": "BUY",
                            "quantity": 100,
                            "entryPrice": 100.0,
                            "openedAt": NOW.isoformat(),
                            "protectiveStop": 98.0,
                            "profitTarget": 104.0,
                            "maximumHoldingMinutes": 30,
                        },
                        "candle": {"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0},
                    }
                ]
            },
        )

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        quarantine = env.inventory.inventory_records("quarantine", limit=5)[0]
        self.assertEqual(result["createdExitIntentCount"], 0)
        self.assertEqual(result["openPositionCount"], 0)
        self.assertEqual(quarantine["payload"]["quarantineReason"], "FOREIGN_POSITION_MANAGEMENT_REQUEST")
        self.assertEqual(quarantine["payload"]["observedAlgorithmId"], "weighted_voting")
        with self.assertRaises(KeyError):
            env.jobs.outbox_for_order_intent("meta_strategy.exit.weighted-voting-spy-position.PROTECTIVE_STOP")

    def test_explicit_position_request_uses_current_meta_strategy_quantity(self) -> None:
        env = RuntimeEnv()
        env.seed_long_position(quantity=4)
        env.enqueue_position_job(
            candle={"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            extra={
                "positionManagementRequests": [
                    {
                        "position": {
                            "positionId": "meta_strategy.position.meta_strategy.paper.default.SPY",
                            "symbol": "SPY",
                            "side": "BUY",
                            "quantity": 100,
                            "entryPrice": 10.0,
                            "openedAt": NOW.isoformat(),
                            "protectiveStop": 9.0,
                            "profitTarget": 11.0,
                            "maximumHoldingMinutes": 30,
                        },
                        "candle": {"symbol": "SPY", "timestamp": (NOW + timedelta(minutes=1)).isoformat(), "open": 100.0, "high": 104.25, "low": 99.5, "close": 104.0},
                    }
                ]
            },
        )

        result = env.worker().run_once(now=NOW + timedelta(minutes=1))

        checkpoint = env.inventory.inventory_records("reconciliation_checkpoints", limit=5)[0]
        explicit = checkpoint["payload"]["payload"]["decisions"][0]
        self.assertEqual(explicit["exit_decision"]["exit_quantity"], 4)
        self.assertEqual(explicit["exit_decision"]["exit_reason"], "PROFIT_TARGET")


class RuntimeEnv:
    def __init__(self, *, database_url: str | None = None) -> None:
        database_url = database_url or f"sqlite:///{temp_db_path()}"
        self.jobs = MetaStrategyJobRepository(database_url)
        self.inventory = MetaStrategySqliteRepository(database_url)

    def worker(self) -> MetaStrategyPositionManagementWorker:
        return MetaStrategyPositionManagementWorker(repository=self.jobs, inventory_repository=self.inventory)

    def enqueue_position_job(self, *, candle: dict, key: str = "position-job", extra: dict | None = None) -> None:
        self.jobs.enqueue_job(
            job_type="position_management",
            idempotency_key=f"meta_strategy.position_management.test.{key}",
            payload={
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": "position-settings",
                "decisionId": f"position-decision-{key}",
                "eventId": f"position-event-{key}",
                "correlationId": f"position-correlation-{key}",
                "symbol": "SPY",
                "candle": candle,
                "markPrices": {"SPY": candle["close"]},
                "mode": "PAPER",
                **(extra or {}),
            },
            now=NOW,
        )

    def seed_long_position(self, *, with_order_intent: bool = True, quantity: int = 10, maximum_holding_minutes: int = 30) -> None:
        if with_order_intent:
            self.inventory.record_order_intent(
                {
                    **identity("entry-decision", "entry-job", "entry-event"),
                    "orderIntentId": "entry-intent",
                    "symbol": "SPY",
                    "side": "BUY",
                    "quantity": quantity,
                    "price": 100.0,
                    "limitPrice": 100.0,
                    "stopPrice": 98.0,
                    "targetPrice": 104.0,
                    "maximumHoldingMinutes": maximum_holding_minutes,
                    "reservedRiskDollars": 20.0,
                    "timestamp": NOW.isoformat(),
                }
            )
        self.inventory.ingest_broker_fill(
            {
                **identity("entry-decision", "entry-job", "entry-event"),
                "orderIntentId": "entry-intent",
                "clientOrderId": "entry-client",
                "brokerOrderId": "entry-broker",
                "brokerFillId": "entry-fill",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": quantity,
                "fillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )


def identity(decision_id: str, job_id: str, event_id: str) -> dict:
    return {
        "algorithmId": "meta_strategy",
        "capitalPartitionId": "meta_strategy.paper.default",
        "settingsVersion": "position-settings",
        "strategyCatalogVersion": "meta_strategy_strategy_catalog_v1",
        "featureSchemaVersion": "meta_strategy_feature_schema_v1",
        "modelVersion": "none",
        "decisionId": decision_id,
        "jobId": job_id,
        "eventId": event_id,
        "correlationId": decision_id,
    }


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-position-management-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
