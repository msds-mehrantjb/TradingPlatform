"""Command contracts for the Voting Ensemble background runtime."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


VOTING_ENSEMBLE_COMMAND_SCHEMA_VERSION = "voting_ensemble_runtime_command_v2"
VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION = "voting_ensemble_evaluation_result_contract_v2"
VotingEnsembleCommandKind = Literal[
    "finalized_bar_evaluation",
    "manual_evaluation",
    "replay",
    "backtest",
    "settings_refresh",
    "recovery_reconciliation",
]
VotingEnsemblePriority = Literal["high", "low"]


class VotingEnsembleRuntimeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commandSchemaVersion: str = VOTING_ENSEMBLE_COMMAND_SCHEMA_VERSION
    evaluationResultContractVersion: str = VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION
    jobId: str = Field(min_length=1)
    commandId: str = Field(min_length=1)
    commandKind: VotingEnsembleCommandKind
    priority: VotingEnsemblePriority
    symbol: str = Field(min_length=1)
    barEndTimestamp: datetime | None = None
    settingsHash: str = Field(default="voting_ensemble_default_settings", min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlationId: str = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1)
    createdAt: datetime
    deadlineAt: datetime
    source: str = Field(default="api", min_length=1)

    @property
    def evaluation_key(self) -> tuple[str, str, str, str] | None:
        if self.commandKind != "finalized_bar_evaluation" or self.barEndTimestamp is None:
            return None
        return (
            self.symbol.upper(),
            _iso(self.barEndTimestamp),
            self.settingsHash,
            VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
        )


def manual_evaluation_command(
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    deadline_seconds: int = 30,
    settings_hash: str | None = None,
) -> VotingEnsembleRuntimeCommand:
    symbol = str(payload.get("symbol") or "SPY").upper()
    bar_end = _payload_bar_end(payload)
    resolved_settings_hash = settings_hash or _payload_settings_hash(payload)
    return _command(
        command_kind="manual_evaluation",
        priority="high",
        symbol=symbol,
        bar_end_timestamp=bar_end,
        settings_hash=resolved_settings_hash,
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="api.manual_evaluation",
    )


def finalized_bar_evaluation_command(
    payload: dict[str, Any],
    *,
    symbol: str,
    bar_end_timestamp: datetime,
    settings_hash: str,
    correlation_id: str | None = None,
    deadline_seconds: int = 20,
) -> VotingEnsembleRuntimeCommand:
    return _command(
        command_kind="finalized_bar_evaluation",
        priority="high",
        symbol=symbol.upper(),
        bar_end_timestamp=bar_end_timestamp,
        settings_hash=settings_hash,
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="market_data.finalized_one_minute_bar",
    )


def backtest_command(
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    deadline_seconds: int = 3600,
) -> VotingEnsembleRuntimeCommand:
    return _command(
        command_kind="backtest",
        priority="low",
        symbol=str(payload.get("symbol") or "SPY").upper(),
        bar_end_timestamp=None,
        settings_hash=str(payload.get("settingsHash") or "voting_ensemble_backtest_settings"),
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="api.backtest",
    )


def replay_command(payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 1800) -> VotingEnsembleRuntimeCommand:
    return _command(
        command_kind="replay",
        priority="low",
        symbol=str(payload.get("symbol") or "SPY").upper(),
        bar_end_timestamp=None,
        settings_hash=str(payload.get("settingsHash") or "voting_ensemble_replay_settings"),
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="api.replay",
    )


def settings_refresh_command(payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 120) -> VotingEnsembleRuntimeCommand:
    return _command(
        command_kind="settings_refresh",
        priority="high",
        symbol=str(payload.get("symbol") or "GLOBAL").upper(),
        bar_end_timestamp=None,
        settings_hash=str(payload.get("settingsHash") or "voting_ensemble_settings_refresh"),
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="api.settings_refresh",
    )


def recovery_reconciliation_command(payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 300) -> VotingEnsembleRuntimeCommand:
    return _command(
        command_kind="recovery_reconciliation",
        priority="high",
        symbol=str(payload.get("symbol") or "GLOBAL").upper(),
        bar_end_timestamp=None,
        settings_hash=str(payload.get("settingsHash") or "voting_ensemble_recovery"),
        payload=payload,
        correlation_id=correlation_id,
        deadline_seconds=deadline_seconds,
        source="api.recovery_reconciliation",
    )


def _command(
    *,
    command_kind: VotingEnsembleCommandKind,
    priority: VotingEnsemblePriority,
    symbol: str,
    bar_end_timestamp: datetime | None,
    settings_hash: str,
    payload: dict[str, Any],
    correlation_id: str | None,
    deadline_seconds: int,
    source: str,
) -> VotingEnsembleRuntimeCommand:
    now = datetime.now(UTC)
    idempotency_key = _idempotency_key(command_kind, symbol, bar_end_timestamp, settings_hash, payload)
    command_id = f"ve-command-{uuid4().hex[:12]}"
    return VotingEnsembleRuntimeCommand(
        jobId=f"ve-job-{uuid4().hex[:12]}",
        commandId=command_id,
        commandKind=command_kind,
        priority=priority,
        symbol=symbol.upper(),
        barEndTimestamp=bar_end_timestamp,
        settingsHash=settings_hash,
        payload=payload,
        correlationId=correlation_id or f"ve-correlation-{uuid4().hex[:12]}",
        idempotencyKey=idempotency_key,
        createdAt=now,
        deadlineAt=now + timedelta(seconds=max(1, deadline_seconds)),
        source=source,
    )


def _payload_bar_end(payload: dict[str, Any]) -> datetime:
    raw = payload.get("data_timestamp")
    if raw is None and isinstance(payload.get("candles"), list) and payload["candles"]:
        raw = payload["candles"][-1].get("timestamp")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    raise ValueError("Voting Ensemble evaluation requires a data_timestamp or final candle timestamp")


def _payload_settings_hash(payload: dict[str, Any]) -> str:
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    explicit = context.get("settingsHash") or context.get("settings_hash") or payload.get("settingsHash")
    if explicit:
        return str(explicit)
    settings_payload = payload.get("settings") or payload.get("tradingSettings") or {}
    from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings

    return resolve_one_minute_trading_settings(settings_payload).configurationHash


def _idempotency_key(
    command_kind: VotingEnsembleCommandKind,
    symbol: str,
    bar_end_timestamp: datetime | None,
    settings_hash: str,
    payload: dict[str, Any],
) -> str:
    if command_kind == "finalized_bar_evaluation" and bar_end_timestamp is not None:
        base = {
            "kind": command_kind,
            "symbol": symbol.upper(),
            "barEndTimestamp": _iso(bar_end_timestamp),
            "settingsHash": settings_hash,
            "resultContractVersion": VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
        }
    elif command_kind == "manual_evaluation":
        base = {
            "kind": command_kind,
            "symbol": symbol.upper(),
            "settingsHash": settings_hash,
            "requestNonce": uuid4().hex,
        }
    else:
        base = {
            "kind": command_kind,
            "symbol": symbol.upper(),
            "settingsHash": settings_hash,
            "payloadHash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16],
        }
    digest = hashlib.sha256(json.dumps(base, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return f"voting_ensemble:{command_kind}:{digest}"


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
