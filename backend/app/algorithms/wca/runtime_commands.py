"""Durable WCA runtime command contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import Field

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent


WCA_RUNTIME_COMMAND_SCHEMA_VERSION = "wca_runtime_command_v1"


class WcaRuntimeCommandType(str, Enum):
    FINALIZED_BAR_DECISION = "finalized_bar_decision"
    MANUAL_PAPER_COMMAND = "manual_paper_command"
    PAUSE_NEW_ENTRIES = "pause_new_entries"
    RESUME_NEW_ENTRIES = "resume_new_entries"
    SET_AUTOMATIC_PAPER = "set_automatic_paper"
    CONFIGURATION_ACTIVATION = "configuration_activation"
    CONFIGURATION_ROLLBACK = "configuration_rollback"
    POSITION_PROTECTIVE_EXIT = "position_protective_exit"
    GLOBAL_RISK_REQUEST = "global_risk_request"
    EXECUTION_OUTBOX = "execution_outbox"
    BROKER_RECONCILIATION = "broker_reconciliation"
    RECOVERY = "recovery"
    EMERGENCY_RISK_REDUCTION = "emergency_risk_reduction"
    HEARTBEAT = "heartbeat"
    END_OF_SESSION = "end_of_session"


class WcaRuntimeCommandStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class WcaRuntimeCommand(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_RUNTIME_COMMAND_SCHEMA_VERSION
    command_id: str = Field(min_length=1)
    command_type: WcaRuntimeCommandType
    event_id: str | None = None
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline_at: datetime
    priority: int = Field(default=50, ge=0, le=100)
    status: WcaRuntimeCommandStatus = WcaRuntimeCommandStatus.QUEUED
    decision_id: str = ""
    run_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    @classmethod
    def from_finalized_bar_event(cls, event: WcaFinalizedBarEvent, *, account_id: str = "paper", deadline_seconds: int = 60) -> "WcaRuntimeCommand":
        decision_id = f"wca-decision-{event.event_id}"
        return cls(
            command_id=f"wca-cmd-decision-{event.event_id}",
            command_type=WcaRuntimeCommandType.FINALIZED_BAR_DECISION,
            event_id=event.event_id,
            account_id=account_id,
            symbol=event.symbol,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
            priority=10,
            decision_id=decision_id,
            run_id=f"wca-runtime-{event.event_id}",
            payload={"event": event.model_dump(mode="json")},
            reason_codes=("wca.runtime.finalized_bar.command_created",),
        )


def runtime_command(
    command_type: WcaRuntimeCommandType,
    *,
    command_id: str | None = None,
    event_id: str | None = None,
    account_id: str = "paper",
    symbol: str = "SPY",
    decision_id: str = "",
    run_id: str = "",
    payload: dict[str, Any] | None = None,
    priority: int = 50,
    deadline_seconds: int = 60,
    reason_codes: tuple[str, ...] = (),
) -> WcaRuntimeCommand:
    return WcaRuntimeCommand(
        command_id=command_id or f"wca-cmd-{command_type.value}-{uuid4().hex}",
        command_type=command_type,
        event_id=event_id,
        account_id=account_id,
        symbol=symbol,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds),
        priority=priority,
        decision_id=decision_id,
        run_id=run_id or f"wca-runtime-{uuid4().hex}",
        payload=payload or {},
        reason_codes=reason_codes,
    )


__all__ = [
    "WCA_RUNTIME_COMMAND_SCHEMA_VERSION",
    "WcaRuntimeCommand",
    "WcaRuntimeCommandStatus",
    "WcaRuntimeCommandType",
    "runtime_command",
]
