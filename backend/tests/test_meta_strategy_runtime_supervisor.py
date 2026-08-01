from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.database import CandleStore
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.runtime import MetaStrategyRuntimeDependencies, MetaStrategyRuntimeMode
from backend.app.algorithms.meta_strategy.runtime_supervisor import (
    MARKET_TIME_QUEUES,
    MetaStrategyRuntimeSnapshotSource,
    MetaStrategyRuntimeSupervisor,
    MetaStrategyRuntimeSupervisorConfig,
)
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.execution import PaperOrderGateway
from backend.app.gates import GlobalGateResponse


class MetaStrategyRuntimeSupervisorTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_configuration_does_not_start_runtime(self) -> None:
        supervisor = MetaStrategyRuntimeSupervisor(config=MetaStrategyRuntimeSupervisorConfig(enabled=False))

        await supervisor.start()
        health = supervisor.readiness_status()

        self.assertEqual(health["status"], "disabled")
        self.assertFalse(health["ready"])
        self.assertTrue(health["paperOrdersBlocked"])
        self.assertEqual(health["reasonCodes"], ("meta_strategy.runtime.disabled",))
        self.assertTrue(all(status == "disabled" for status in health["workers"].values()))

    async def test_startup_failure_marks_unavailable_and_blocks_paper_orders(self) -> None:
        dependencies, _gateway = runtime_dependencies(with_broker=False)
        supervisor = MetaStrategyRuntimeSupervisor(
            config=MetaStrategyRuntimeSupervisorConfig(enabled=True, mode=MetaStrategyRuntimeMode.PAPER),
            dependencies=dependencies,
        )

        await supervisor.start()
        health = supervisor.readiness_status()

        self.assertEqual(health["status"], "unavailable")
        self.assertFalse(health["ready"])
        self.assertTrue(health["paperOrdersBlocked"])
        self.assertIn("meta_strategy.runtime.paper_broker_required", health["reasonCodes"])

    async def test_paper_runtime_refuses_non_alpaca_paper_gateway(self) -> None:
        dependencies, _gateway = runtime_dependencies(with_broker=True)
        dependencies.broker_adapter = NonAlpacaBroker()
        supervisor = MetaStrategyRuntimeSupervisor(
            config=MetaStrategyRuntimeSupervisorConfig(enabled=True, mode=MetaStrategyRuntimeMode.PAPER),
            dependencies=dependencies,
            paper_gateway=PaperOrderGateway(NonAlpacaBroker(), dependencies.job_repository.gateway_store()),
            market_data_client=FakeMarketDataClient(),
            candle_store=CandleStore(SimpleNamespace(database_url=f"sqlite:///{dependencies.job_repository.path}")),
        )

        await supervisor.start()
        health = supervisor.readiness_status()

        self.assertEqual(health["status"], "unavailable")
        self.assertTrue(health["paperOrdersBlocked"])
        self.assertIn("meta_strategy.runtime.configured_alpaca_paper_gateway_required", health["reasonCodes"])

    async def test_valid_paper_runtime_starts_required_market_time_worker_loops(self) -> None:
        dependencies, gateway = runtime_dependencies(with_broker=True)
        supervisor = MetaStrategyRuntimeSupervisor(
            config=MetaStrategyRuntimeSupervisorConfig(
                enabled=True,
                mode=MetaStrategyRuntimeMode.PAPER,
                worker_poll_seconds=0.05,
                reconciliation_poll_seconds=0.05,
                stale_order_poll_seconds=0.05,
                inventory_poll_seconds=0.05,
                position_poll_seconds=0.05,
                maintenance_interval_seconds=0.05,
            ),
            dependencies=dependencies,
            paper_gateway=gateway,
            global_risk_source=AllowRisk(),
            market_data_client=FakeMarketDataClient(),
            candle_store=CandleStore(SimpleNamespace(database_url=f"sqlite:///{dependencies.job_repository.path}")),
        )

        await supervisor.start()
        await supervisor._sleep(0.10)
        health = supervisor.readiness_status()
        await supervisor.shutdown()

        self.assertTrue(health["ready"])
        self.assertFalse(health["paperOrdersBlocked"])
        self.assertEqual(set(MARKET_TIME_QUEUES), set(health["workers"]))
        self.assertTrue(all(health["workers"][queue] in {"healthy", "stopped"} for queue in MARKET_TIME_QUEUES))
        self.assertIn("inventory_reconciliation", health["scheduledJobs"])
        self.assertIn("position_management", health["scheduledJobs"])

    def test_service_blocks_paper_evaluate_when_supervisor_blocks_paper_orders(self) -> None:
        service = MetaStrategyApplicationService(
            job_repository=MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}"),
            runtime_readiness_provider=lambda: {
                "paperOrdersBlocked": True,
                "reasonCodes": ("meta_strategy.runtime.startup_failed",),
            },
        )

        result = service.paper_evaluate({"symbol": "SPY"})

        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(result["payload"]["orderSubmissionAllowed"])
        self.assertIn("meta_strategy.runtime.startup_failed", result["reasonCodes"])


def runtime_dependencies(*, with_broker: bool) -> tuple[MetaStrategyRuntimeDependencies, PaperOrderGateway | None]:
    database_url = f"sqlite:///{temp_db_path()}"
    jobs = MetaStrategyJobRepository(database_url)
    inventory = MetaStrategySqliteRepository(database_url)
    settings_store = MetaStrategySettingsStore(temp_db_path(prefix="meta-strategy-settings"))
    baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version=f"settings-{uuid4().hex}"), actor="test")
    settings_store.activate_settings(baseline.settings_version, actor="test")
    broker = FakePaperBroker() if with_broker else None
    gateway = PaperOrderGateway(broker, jobs.gateway_store()) if broker is not None else None
    dependencies = MetaStrategyRuntimeDependencies(
        mode=MetaStrategyRuntimeMode.PAPER,
        persistence_adapter=MetaStrategyRepositoryPersistenceAdapter(inventory),
        broker_adapter=broker,
        inventory_repository=inventory,
        job_repository=jobs,
        settings_store=settings_store,
        account_data_source=MetaStrategyRuntimeSnapshotSource(lambda: {"account": "paper"}),
        global_risk_source=MetaStrategyRuntimeSnapshotSource(lambda: {"globalRisk": "available"}),
        operational_health_source=MetaStrategyRuntimeSnapshotSource(lambda: {"status": "OK"}),
    )
    return dependencies, gateway


class FakeMarketDataClient:
    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str):
        anchor = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
        rows = []
        for index in range(40):
            timestamp = anchor - timedelta(minutes=39 - index)
            rows.append(
                {
                    "provider": "fixture",
                    "feed": feed,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": timestamp.isoformat(),
                    "open": 100.0 + index * 0.01,
                    "high": 100.2 + index * 0.01,
                    "low": 99.9 + index * 0.01,
                    "close": 100.1 + index * 0.01,
                    "volume": 1000 + index,
                    "trade_count": 10,
                    "vwap": 100.05 + index * 0.01,
                    "finalized": True,
                }
            )
        return rows[-limit:]


class FakePaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent):
        raise AssertionError("supervisor startup should not submit orders")

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self):
        return []

    def list_order_events(self):
        return []


class NonAlpacaBroker(FakePaperBroker):
    broker_kind = "fixture"
    paper_endpoint = False


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=datetime.now(UTC),
            configurationHash="runtime-test-allow-risk",
        )


def temp_db_path(*, prefix: str = "meta-strategy-runtime-supervisor") -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
