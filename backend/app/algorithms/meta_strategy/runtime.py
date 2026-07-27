from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.broker_adapter import NoopMetaStrategyBrokerAdapter
from backend.app.algorithms.meta_strategy.execution_pipeline import InMemoryMetaStrategyPersistenceAdapter
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore


class MetaStrategyRuntimeMode(str, Enum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    DIAGNOSTICS = "DIAGNOSTICS"
    TEST = "TEST"
    LIVE = "LIVE"


class RuntimeSnapshotSource(Protocol):
    def load_snapshot(self) -> Mapping[str, Any]:
        ...


class MetaStrategyRuntimeStartupError(RuntimeError):
    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        super().__init__(", ".join(reason_codes))
        self.reason_codes = reason_codes


@dataclass(frozen=True)
class MetaStrategyRuntimeStartupReport:
    ready: bool
    mode: MetaStrategyRuntimeMode
    reason_codes: tuple[str, ...]
    diagnostic_fallbacks_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "mode": self.mode.value,
            "reasonCodes": self.reason_codes,
            "diagnosticFallbacksAllowed": self.diagnostic_fallbacks_allowed,
        }


@dataclass
class MetaStrategyRuntimeDependencies:
    mode: MetaStrategyRuntimeMode
    persistence_adapter: Any | None = None
    broker_adapter: Any | None = None
    inventory_repository: MetaStrategySqliteRepository | None = None
    job_repository: MetaStrategyJobRepository | None = None
    settings_store: MetaStrategySettingsStore | None = None
    account_data_source: RuntimeSnapshotSource | None = None
    global_risk_source: RuntimeSnapshotSource | None = None
    operational_health_source: RuntimeSnapshotSource | None = None
    diagnostic_label: str | None = None
    startup_report: MetaStrategyRuntimeStartupReport | None = field(default=None, init=False)


def configured_meta_strategy_runtime(
    *,
    mode: MetaStrategyRuntimeMode,
    database_url: str,
    settings_store: MetaStrategySettingsStore | None = None,
    broker_adapter: Any | None = None,
    account_data_source: RuntimeSnapshotSource | None = None,
    global_risk_source: RuntimeSnapshotSource | None = None,
    operational_health_source: RuntimeSnapshotSource | None = None,
    diagnostic_label: str | None = None,
) -> MetaStrategyRuntimeDependencies:
    inventory_repository = MetaStrategySqliteRepository(database_url)
    return MetaStrategyRuntimeDependencies(
        mode=mode,
        persistence_adapter=MetaStrategyRepositoryPersistenceAdapter(inventory_repository),
        broker_adapter=broker_adapter,
        inventory_repository=inventory_repository,
        job_repository=MetaStrategyJobRepository(database_url),
        settings_store=settings_store or MetaStrategySettingsStore(Path("./data/meta_strategy_settings.db")),
        account_data_source=account_data_source,
        global_risk_source=global_risk_source,
        operational_health_source=operational_health_source,
        diagnostic_label=diagnostic_label,
    )


def validate_meta_strategy_runtime_startup(dependencies: MetaStrategyRuntimeDependencies) -> MetaStrategyRuntimeStartupReport:
    reason_codes: list[str] = []
    mode = dependencies.mode
    memory_persistence = isinstance(dependencies.persistence_adapter, InMemoryMetaStrategyPersistenceAdapter)
    noop_broker = isinstance(dependencies.broker_adapter, NoopMetaStrategyBrokerAdapter)
    diagnostic_mode = mode in {MetaStrategyRuntimeMode.DIAGNOSTICS, MetaStrategyRuntimeMode.TEST}
    diagnostic_fallbacks = bool(dependencies.diagnostic_label) and diagnostic_mode

    if mode == MetaStrategyRuntimeMode.LIVE:
        reason_codes.append("meta_strategy.runtime.live_disabled")
    if diagnostic_mode and (memory_persistence or noop_broker) and not dependencies.diagnostic_label:
        reason_codes.append("meta_strategy.runtime.diagnostic_label_required")
    if mode in {MetaStrategyRuntimeMode.PAPER, MetaStrategyRuntimeMode.SHADOW}:
        if memory_persistence or not isinstance(dependencies.persistence_adapter, MetaStrategyRepositoryPersistenceAdapter):
            reason_codes.append("meta_strategy.runtime.durable_persistence_required")
        if dependencies.job_repository is None:
            reason_codes.append("meta_strategy.runtime.job_repository_required")
        if dependencies.inventory_repository is None:
            reason_codes.append("meta_strategy.runtime.inventory_repository_required")
        if noop_broker or dependencies.broker_adapter is None or getattr(dependencies.broker_adapter, "configured", True) is False:
            reason_codes.append("meta_strategy.runtime.paper_broker_required")
        if not _has_active_settings(dependencies.settings_store):
            reason_codes.append("meta_strategy.runtime.active_settings_required")
        if dependencies.account_data_source is None:
            reason_codes.append("meta_strategy.runtime.account_data_source_required")
        if dependencies.global_risk_source is None:
            reason_codes.append("meta_strategy.runtime.global_risk_source_required")
        if dependencies.operational_health_source is None:
            reason_codes.append("meta_strategy.runtime.operational_health_source_required")

    if reason_codes:
        raise MetaStrategyRuntimeStartupError(tuple(reason_codes))
    report = MetaStrategyRuntimeStartupReport(
        ready=True,
        mode=mode,
        reason_codes=("meta_strategy.runtime.startup_validated",),
        diagnostic_fallbacks_allowed=diagnostic_fallbacks,
    )
    dependencies.startup_report = report
    return report


def reconstruct_meta_strategy_runtime_state(dependencies: MetaStrategyRuntimeDependencies) -> dict[str, Any]:
    inventory = (
        dependencies.inventory_repository.check_inventory_consistency()
        if dependencies.inventory_repository is not None
        else {"consistent": False, "reasonCodes": ("meta_strategy.runtime.inventory_repository_missing",)}
    )
    decisions = (
        dependencies.job_repository.validate_decision_projection()
        if dependencies.job_repository is not None
        else {"valid": False, "reasonCodes": ("meta_strategy.runtime.job_repository_missing",)}
    )
    queues = dependencies.job_repository.queue_status() if dependencies.job_repository is not None else None
    return {
        "status": "OK" if inventory.get("consistent") and decisions.get("valid") else "BLOCKED",
        "inventory": inventory,
        "decisions": decisions,
        "queues": queues,
        "reasonCodes": (
            "meta_strategy.runtime.restart_reconstruction_complete"
            if inventory.get("consistent") and decisions.get("valid")
            else "meta_strategy.runtime.restart_reconstruction_failed"
        ),
    }


def meta_strategy_runtime_retention_policies() -> dict[str, dict[str, Any]]:
    return {
        "meta_strategy_worker_decisions": {
            "mode": "append_only_then_archive",
            "hotRetentionDays": 90,
            "archiveAfterDays": 90,
            "deleteAfterDays": 2555,
        },
        "meta_strategy_execution_outbox": {
            "mode": "append_only_then_archive",
            "hotRetentionDays": 180,
            "archiveAfterDays": 180,
            "deleteAfterDays": 2555,
        },
        "meta_strategy_job_events": {
            "mode": "append_only_then_archive",
            "hotRetentionDays": 90,
            "archiveAfterDays": 90,
            "deleteAfterDays": 2555,
        },
        "meta_strategy_high_volume_evidence": {
            "mode": "summarize_then_archive",
            "hotRetentionDays": 30,
            "archiveAfterDays": 30,
            "deleteAfterDays": 1095,
        },
    }


def _has_active_settings(settings_store: MetaStrategySettingsStore | None) -> bool:
    if settings_store is None:
        return False
    if hasattr(settings_store, "has_active_settings"):
        return bool(settings_store.has_active_settings())
    try:
        settings_store.get_active_settings()
    except Exception:
        return False
    return True


__all__ = [
    "MetaStrategyRuntimeDependencies",
    "MetaStrategyRuntimeMode",
    "MetaStrategyRuntimeStartupError",
    "MetaStrategyRuntimeStartupReport",
    "configured_meta_strategy_runtime",
    "meta_strategy_runtime_retention_policies",
    "reconstruct_meta_strategy_runtime_state",
    "validate_meta_strategy_runtime_startup",
]
