from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
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
        if mode == MetaStrategyRuntimeMode.PAPER:
            if not _is_configured_paper_broker(dependencies.broker_adapter):
                reason_codes.append("meta_strategy.runtime.configured_paper_gateway_required")
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
    snapshot = dependencies.inventory_repository.current_inventory_snapshot() if dependencies.inventory_repository is not None else None
    inventory = (
        dependencies.inventory_repository.check_inventory_consistency()
        if dependencies.inventory_repository is not None
        else {"consistent": False, "reasonCodes": ("meta_strategy.runtime.inventory_repository_missing",)}
    )
    recovered_inventory = _recovered_inventory_summary(dependencies.inventory_repository, snapshot)
    decisions = (
        dependencies.job_repository.validate_decision_projection()
        if dependencies.job_repository is not None
        else {"valid": False, "reasonCodes": ("meta_strategy.runtime.job_repository_missing",)}
    )
    queues = dependencies.job_repository.queue_status() if dependencies.job_repository is not None else None
    reconstructed = bool(inventory.get("consistent") and decisions.get("valid") and recovered_inventory.get("rebuiltFromLedger") is True)
    return {
        "status": "OK" if reconstructed else "BLOCKED",
        "authoritativeInventoryApi": "current_inventory_snapshot",
        "inventoryAuthority": "meta_strategy_inventory.current_inventory_snapshot",
        "portfolioImportedFromBroker": False,
        "foreignStateImported": False,
        "inventory": inventory,
        "recoveredInventory": recovered_inventory,
        "decisions": decisions,
        "queues": queues,
        "reasonCodes": (
            "meta_strategy.runtime.restart_reconstruction_complete"
            if reconstructed
            else "meta_strategy.runtime.restart_reconstruction_failed"
        ),
    }


def _recovered_inventory_summary(repository: MetaStrategySqliteRepository | None, snapshot: Any | None) -> dict[str, Any]:
    if repository is None or snapshot is None:
        return {
            "rebuiltFromLedger": False,
            "authoritativeInventoryApi": "current_inventory_snapshot",
            "reasonCodes": ("meta_strategy.runtime.inventory_repository_missing",),
        }
    records = {
        record_type: repository.inventory_records(record_type, limit=500)
        for record_type in (
            "order_intents",
            "orders",
            "order_status_history",
            "fills",
            "risk_reservations",
            "allocated_capital",
            "daily_statistics",
            "strategy_exposure",
            "symbol_exposure",
            "family_exposure",
            "position_lifecycle",
        )
    }
    pending_orders = _pending_order_records(records["order_intents"], records["orders"], records["order_status_history"])
    return {
        "algorithmId": snapshot.algorithm_id,
        "capitalPartitionId": snapshot.capital_partition_id,
        "authoritativeInventoryApi": "current_inventory_snapshot",
        "inventoryAuthority": "meta_strategy_inventory.current_inventory_snapshot",
        "rebuiltFromLedger": bool(snapshot.rebuilt_from_ledger),
        "allocatedCapital": float(snapshot.allocated_capital),
        "openPositions": _runtime_plain(snapshot.open_positions),
        "openLots": _runtime_plain(snapshot.open_lots),
        "pendingOrders": pending_orders,
        "pendingOrderCount": len(pending_orders),
        "partialFillCount": sum(1 for item in records["order_status_history"] if str(item.get("status") or "").upper() == "PARTIALLY_FILLED"),
        "fillCount": len(records["fills"]),
        "realisedPnl": float(snapshot.realised_pnl),
        "unrealisedPnl": float(snapshot.unrealised_pnl),
        "feesAndSlippage": float(snapshot.fees_and_slippage),
        "reservedRiskDollars": float(snapshot.reserved_risk_dollars),
        "dailyTradeCount": int(snapshot.daily_trade_count),
        "dailyRealisedPnl": float(getattr(snapshot, "daily_realised_pnl", 0.0)),
        "dailyRealizedPnl": float(getattr(snapshot, "daily_realised_pnl", 0.0)),
        "strategyExposure": dict(snapshot.strategy_exposure),
        "familyExposure": dict(snapshot.family_exposure),
        "symbolExposure": dict(snapshot.symbol_exposure),
        "latestPositionLifecycle": tuple(item["payload"] for item in records["position_lifecycle"]),
        "recordCounts": {key: len(value) for key, value in records.items()},
        "foreignStateImported": False,
        "portfolioImportedFromBroker": False,
        "reasonCodes": ("meta_strategy.runtime.inventory_rebuilt_from_meta_strategy_ledger",),
    }


def _pending_order_records(order_intents: tuple[Mapping[str, Any], ...], orders: tuple[Mapping[str, Any], ...], statuses: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    terminal_statuses = {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "DEAD_LETTER", "DONE_FOR_DAY"}
    latest_status_by_intent: dict[str, str] = {}
    for record in (*statuses, *orders):
        intent_id = str(record.get("orderIntentId") or "")
        if intent_id and intent_id not in latest_status_by_intent:
            latest_status_by_intent[intent_id] = str(record.get("status") or "").upper()
    pending = []
    for record in order_intents:
        intent_id = str(record.get("orderIntentId") or "")
        status = latest_status_by_intent.get(intent_id, str(record.get("status") or "RECORDED").upper())
        if status not in terminal_statuses:
            pending.append(
                {
                    "orderIntentId": intent_id,
                    "clientOrderId": str(record.get("clientOrderId") or ""),
                    "symbol": str(record.get("symbol") or "").upper(),
                    "side": str(record.get("side") or "").upper(),
                    "quantity": float(record.get("quantity") or 0.0),
                    "status": status,
                    "algorithmId": str(record.get("algorithmId") or "meta_strategy"),
                    "capitalPartitionId": str(record.get("capitalPartitionId") or "meta_strategy.paper.default"),
                }
            )
    return tuple(pending)


def _runtime_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return _runtime_plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _runtime_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_runtime_plain(item) for item in value)
    return value


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


def _is_configured_paper_broker(broker_adapter: Any | None) -> bool:
    if broker_adapter is None:
        return False
    if getattr(broker_adapter, "broker_kind", None) not in {"alpaca_paper", "local_paper", "local_paper_ledger"}:
        return False
    if getattr(broker_adapter, "configured", False) is not True:
        return False
    if getattr(broker_adapter, "paper_endpoint", False) is not True:
        return False
    settings = getattr(broker_adapter, "settings", None)
    if settings is not None and getattr(settings, "has_alpaca_credentials", True) is not True:
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
