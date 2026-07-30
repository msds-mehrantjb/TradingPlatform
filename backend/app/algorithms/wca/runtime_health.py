"""WCA background runtime health contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel


WCA_RUNTIME_HEALTH_SCHEMA_VERSION = "wca_runtime_health_v1"


class WcaRuntimeHealthSnapshot(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_RUNTIME_HEALTH_SCHEMA_VERSION
    health_id: str = Field(default="wca-runtime-health", min_length=1)
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "starting_fail_closed"
    queue_depth: int = Field(default=0, ge=0)
    command_depth: int = Field(default=0, ge=0)
    max_queue_age_seconds: float = Field(default=0, ge=0)
    last_processed_bar: datetime | None = None
    lag_seconds: float = Field(default=0, ge=0)
    last_decision_id: str = ""
    recovery_state: str = "not_started"
    paused_new_entries: bool = True
    protective_management_active: bool = True
    worker_heartbeats: dict[str, datetime] = Field(default_factory=dict)
    database_available: bool = True
    broker_available: bool = True
    market_data_available: bool = True
    clock_skew_seconds: float = Field(default=0, ge=0)
    reconciliation_age_seconds: float | None = Field(default=None, ge=0)
    unprotected_position: bool = False
    duplicate_order_evidence: bool = False
    configuration_ready: bool = True
    weight_calibration_ready: bool = True
    circuit_breaker_open: bool = False
    latency_summary: dict[str, Any] = Field(default_factory=dict)
    health_checks: dict[str, bool] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ("wca.runtime.starting_fail_closed",)

    @property
    def block_new_entries(self) -> bool:
        return self.paused_new_entries or self.status not in {"healthy", "idle", "protective_only"} or bool(critical_health_reason_codes(self))


def critical_health_reason_codes(health: WcaRuntimeHealthSnapshot) -> tuple[str, ...]:
    checks = {
        "database_available": health.database_available,
        "broker_available": health.broker_available,
        "market_data_available": health.market_data_available,
        "configuration_ready": health.configuration_ready,
        "weight_calibration_ready": health.weight_calibration_ready,
        "circuit_breaker_closed": not health.circuit_breaker_open,
        "protected_position": not health.unprotected_position,
        "duplicate_order_clean": not health.duplicate_order_evidence,
    }
    checks.update(health.health_checks)
    reasons = [f"wca.runtime.health.{name}" for name, passed in sorted(checks.items()) if not passed]
    return tuple(reasons)


def healthy_runtime_snapshot(
    *,
    queue_depth: int,
    command_depth: int,
    last_processed_bar: datetime | None,
    lag_seconds: float,
    last_decision_id: str,
    recovery_state: str,
    paused_new_entries: bool,
    reason_codes: tuple[str, ...],
    max_queue_age_seconds: float = 0.0,
    worker_heartbeats: dict[str, datetime] | None = None,
    database_available: bool = True,
    broker_available: bool = True,
    market_data_available: bool = True,
    clock_skew_seconds: float = 0.0,
    reconciliation_age_seconds: float | None = None,
    unprotected_position: bool = False,
    duplicate_order_evidence: bool = False,
    configuration_ready: bool = True,
    weight_calibration_ready: bool = True,
    circuit_breaker_open: bool = False,
    latency_summary: dict[str, Any] | None = None,
    health_checks: dict[str, bool] | None = None,
) -> WcaRuntimeHealthSnapshot:
    candidate = WcaRuntimeHealthSnapshot(
        queue_depth=queue_depth,
        command_depth=command_depth,
        max_queue_age_seconds=max(0.0, max_queue_age_seconds),
        last_processed_bar=last_processed_bar,
        lag_seconds=max(0.0, lag_seconds),
        last_decision_id=last_decision_id,
        recovery_state=recovery_state,
        paused_new_entries=paused_new_entries,
        worker_heartbeats=worker_heartbeats or {},
        database_available=database_available,
        broker_available=broker_available,
        market_data_available=market_data_available,
        clock_skew_seconds=max(0.0, clock_skew_seconds),
        reconciliation_age_seconds=reconciliation_age_seconds,
        unprotected_position=unprotected_position,
        duplicate_order_evidence=duplicate_order_evidence,
        configuration_ready=configuration_ready,
        weight_calibration_ready=weight_calibration_ready,
        circuit_breaker_open=circuit_breaker_open,
        latency_summary=latency_summary or {},
        health_checks=health_checks or {},
        reason_codes=reason_codes,
    )
    critical = critical_health_reason_codes(candidate)
    paused = paused_new_entries or bool(critical)
    status = "protective_only" if paused else "healthy"
    return WcaRuntimeHealthSnapshot(
        status=status,
        queue_depth=queue_depth,
        command_depth=command_depth,
        max_queue_age_seconds=max(0.0, max_queue_age_seconds),
        last_processed_bar=last_processed_bar,
        lag_seconds=max(0.0, lag_seconds),
        last_decision_id=last_decision_id,
        recovery_state=recovery_state,
        paused_new_entries=paused,
        reason_codes=reason_codes,
        worker_heartbeats=worker_heartbeats or {},
        database_available=database_available,
        broker_available=broker_available,
        market_data_available=market_data_available,
        clock_skew_seconds=max(0.0, clock_skew_seconds),
        reconciliation_age_seconds=reconciliation_age_seconds,
        unprotected_position=unprotected_position,
        duplicate_order_evidence=duplicate_order_evidence,
        configuration_ready=configuration_ready,
        weight_calibration_ready=weight_calibration_ready,
        circuit_breaker_open=circuit_breaker_open,
        latency_summary=latency_summary or {},
        health_checks=health_checks or {},
    )


__all__ = [
    "WCA_RUNTIME_HEALTH_SCHEMA_VERSION",
    "WcaRuntimeHealthSnapshot",
    "critical_health_reason_codes",
    "healthy_runtime_snapshot",
]
