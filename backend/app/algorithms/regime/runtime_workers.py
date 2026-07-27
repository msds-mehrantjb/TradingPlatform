"""Regime runtime worker responsibilities."""

from __future__ import annotations

from abc import ABC, abstractmethod


class RegimeRuntimeWorker(ABC):
    worker_id = "regime_runtime_worker"

    def __init__(self, supervisor) -> None:
        self.supervisor = supervisor

    @abstractmethod
    async def run(self) -> None:
        """Run one concrete Regime background worker loop."""


class FinalisedBarIngestionWorker(RegimeRuntimeWorker):
    worker_id = "regime_finalised_bar_ingestion_worker"

    async def run(self) -> None:
        await self.supervisor.finalised_bar_ingestion_loop(self.worker_id)


class DecisionWorker(RegimeRuntimeWorker):
    worker_id = "regime_decision_worker"

    async def run(self) -> None:
        await self.supervisor.decision_loop(self.worker_id)


class LocalRiskWorker(RegimeRuntimeWorker):
    worker_id = "regime_local_risk_worker"

    async def run(self) -> None:
        await self.supervisor.local_risk_loop(self.worker_id)


class ExecutionOutboxWorker(RegimeRuntimeWorker):
    worker_id = "regime_execution_outbox_worker"

    async def run(self) -> None:
        await self.supervisor.execution_outbox_loop(self.worker_id)


class BrokerReconciliationWorker(RegimeRuntimeWorker):
    worker_id = "regime_broker_reconciliation_worker"

    async def run(self) -> None:
        await self.supervisor.reconciliation_loop(self.worker_id)


class PositionManagementWorker(RegimeRuntimeWorker):
    worker_id = "regime_position_management_worker"

    async def run(self) -> None:
        await self.supervisor.position_management_loop(self.worker_id)


class RecoveryWorker(RegimeRuntimeWorker):
    worker_id = "regime_recovery_worker"

    async def run(self) -> None:
        await self.supervisor.recovery_loop(self.worker_id)


class BacktestJobWorker(RegimeRuntimeWorker):
    worker_id = "regime_backtest_job_worker"

    async def run(self) -> None:
        await self.supervisor.backtest_job_loop(self.worker_id)


class HeartbeatHealthWorker(RegimeRuntimeWorker):
    worker_id = "regime_heartbeat_health_worker"

    async def run(self) -> None:
        await self.supervisor.heartbeat_loop(self.worker_id)


class DailyResetMaintenanceWorker(RegimeRuntimeWorker):
    worker_id = "regime_daily_reset_maintenance_worker"

    async def run(self) -> None:
        await self.supervisor.daily_reset_maintenance_loop(self.worker_id)


class RuntimeCommandWorker(RegimeRuntimeWorker):
    worker_id = "regime_runtime_command_worker"

    async def run(self) -> None:
        await self.supervisor.command_loop(self.worker_id)


REGIME_RUNTIME_WORKER_CLASSES = (
    FinalisedBarIngestionWorker,
    DecisionWorker,
    LocalRiskWorker,
    ExecutionOutboxWorker,
    BrokerReconciliationWorker,
    PositionManagementWorker,
    RecoveryWorker,
    BacktestJobWorker,
    HeartbeatHealthWorker,
    DailyResetMaintenanceWorker,
    RuntimeCommandWorker,
)


__all__ = [
    "BacktestJobWorker",
    "BrokerReconciliationWorker",
    "DailyResetMaintenanceWorker",
    "DecisionWorker",
    "ExecutionOutboxWorker",
    "FinalisedBarIngestionWorker",
    "HeartbeatHealthWorker",
    "LocalRiskWorker",
    "PositionManagementWorker",
    "REGIME_RUNTIME_WORKER_CLASSES",
    "RecoveryWorker",
    "RegimeRuntimeWorker",
    "RuntimeCommandWorker",
]
