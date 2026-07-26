"""WCA background runtime health contracts."""

from __future__ import annotations

from datetime import datetime, timezone

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
    last_processed_bar: datetime | None = None
    lag_seconds: float = Field(default=0, ge=0)
    last_decision_id: str = ""
    recovery_state: str = "not_started"
    paused_new_entries: bool = True
    protective_management_active: bool = True
    reason_codes: tuple[str, ...] = ("wca.runtime.starting_fail_closed",)

    @property
    def block_new_entries(self) -> bool:
        return self.paused_new_entries or self.status not in {"healthy", "idle", "protective_only"}


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
) -> WcaRuntimeHealthSnapshot:
    status = "protective_only" if paused_new_entries else "healthy"
    return WcaRuntimeHealthSnapshot(
        status=status,
        queue_depth=queue_depth,
        command_depth=command_depth,
        last_processed_bar=last_processed_bar,
        lag_seconds=max(0.0, lag_seconds),
        last_decision_id=last_decision_id,
        recovery_state=recovery_state,
        paused_new_entries=paused_new_entries,
        reason_codes=reason_codes,
    )


__all__ = [
    "WCA_RUNTIME_HEALTH_SCHEMA_VERSION",
    "WcaRuntimeHealthSnapshot",
    "healthy_runtime_snapshot",
]
