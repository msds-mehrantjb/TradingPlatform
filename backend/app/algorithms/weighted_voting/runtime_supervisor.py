"""Background one-minute runtime supervisor for Weighted Voting."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.broker_reconciliation import (
    WeightedVotingBrokerFillObservation,
    WeightedVotingBrokerOrderObservation,
    WeightedVotingBrokerPositionObservation,
    reconcile_weighted_voting_broker_observations,
)
from backend.app.algorithms.weighted_voting.decision_kernel import WEIGHTED_VOTING_DECISION_KERNEL_VERSION
from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.execution_gateway import (
    WEIGHTED_VOTING_EXECUTION_NAMESPACE,
    WeightedVotingExecutionQueueItem,
    enqueue_weighted_voting_execution_order,
    submit_queued_weighted_voting_paper_order,
    weighted_voting_execution_queue_item_from_payload,
    _verify_weighted_voting_paper_endpoint,
)
from backend.app.algorithms.weighted_voting.global_interface import (
    WeightedVotingGlobalRiskResponse,
    apply_global_response_to_weighted_voting_proposal,
    build_weighted_voting_global_risk_request,
    fail_closed_global_risk_response,
    global_gate_response_from_weighted_voting_risk,
    validate_weighted_voting_global_risk_response,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import CURRENT_SNAPSHOT_KEY, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.local_paper_broker import WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE, build_weighted_voting_local_paper_gateway_dependencies
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings, WeightedMarketSnapshot, WeightedWeightState
from backend.app.algorithms.weighted_voting.persistence import (
    WEIGHTED_VOTING_SETTINGS_KEY,
    WeightedVotingFilesystemStateStore,
    WeightedVotingStateStore,
    load_effective_settings,
    persist_effective_settings,
)
from backend.app.algorithms.weighted_voting.position_manager import WeightedVotingPositionManagerService
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation, automatic_submission_allowed, load_persisted_rollout_validation, rollout_feature_flags
from backend.app.algorithms.weighted_voting.runtime_context import (
    WeightedVotingAccountObservationPort,
    WeightedVotingExchangeSessionState,
    WeightedVotingExecutionCostEstimate,
    WeightedVotingGlobalRiskPort,
    WeightedVotingRuntimeContext,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingStaticMarketDataPort,
    WeightedVotingUnavailableAccountPort,
    WeightedVotingUnavailableGlobalRiskPort,
)
from backend.app.algorithms.weighted_voting.scheduler import (
    ACTIVE_WEIGHT_STATE_KEY,
    PUBLISHED_WEIGHT_PREFIX,
    WeightedVotingDailySchedulerConfig,
    activate_published_weight_for_session,
    run_after_market_daily_weight_update,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.algorithms.weighted_voting.shadow_evidence import record_shadow_observations
from backend.app.algorithms.weighted_voting.strategy_lifecycle import WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY
from backend.app.domain.exchange_calendar import ExchangeCalendarService, NEW_YORK
from backend.app.execution import PaperOrderGateway
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION = "weighted_voting_runtime_supervisor_v1"
RUNTIME_STATUS_KEY = "weighted_voting.runtime.status"
RUNTIME_HEARTBEAT_KEY = "weighted_voting.runtime.heartbeat"
RUNTIME_EVENT_PREFIX = "weighted_voting.runtime.events."
RUNTIME_CHECKPOINT_PREFIX = "weighted_voting.runtime.checkpoints."
RUNTIME_DECISION_IDEMPOTENCY_PREFIX = "weighted_voting.runtime.decision_idempotency."
RUNTIME_EXECUTION_PREFIX = "weighted_voting.runtime.executions."
RUNTIME_ADMIN_AUDIT_PREFIX = "weighted_voting.runtime.admin_audit."
RUNTIME_STRATEGY_CONTROL_PREFIX = "weighted_voting.runtime.strategy_controls."
RUNTIME_EMERGENCY_FLATTEN_PREFIX = "weighted_voting.runtime.emergency_flatten."
RUNTIME_RECOVERY_STATE_KEY = "weighted_voting.runtime.recovery.state"
RUNTIME_RECOVERY_EVENT_PREFIX = "weighted_voting.runtime.recovery.events."
RUNTIME_QUARANTINE_PREFIX = "weighted_voting.runtime.quarantine."
RUNTIME_HEALTHY_STATE_KEY = "weighted_voting.runtime.recovery.healthy_state"
RUNTIME_CONTROL_KEY = "weighted_voting.runtime.control"
RUNTIME_CONTROL_AUDIT_PREFIX = "weighted_voting.runtime.control_audit."
RUNTIME_ORDER_INTENT_PREFIX = "weighted_voting.runtime.order_intents."
RUNTIME_RISK_PREFIX = "weighted_voting.runtime.risk."
RUNTIME_EXECUTION_OUTBOX_PREFIX = "weighted_voting.runtime.execution_outbox."
RUNTIME_EXECUTION_OUTBOX_ATTEMPT_PREFIX = "weighted_voting.runtime.execution_outbox_attempts."
RUNTIME_OBSERVABILITY_KEY = "weighted_voting.observability.runtime.latest"
RUNTIME_OBSERVABILITY_PREFIX = "weighted_voting.observability.runtime."
RUNTIME_CIRCUIT_BREAKER_KEY = "weighted_voting.runtime.circuit_breaker.latest"
RUNTIME_CIRCUIT_BREAKER_PREFIX = "weighted_voting.runtime.circuit_breaker."
_DEFAULT_PAPER_GATEWAY = object()
LAST_APPROVED_SETTINGS_KEY = "weighted_voting.settings.last_approved"
LAST_APPROVED_WEIGHT_STATE_KEY = "weighted_voting.weights.last_approved"
WEIGHTED_VOTING_RUNTIME_CONTROL_VERSION = "weighted_voting_runtime_control_v1"
WEIGHTED_VOTING_AUTO_PAPER_READINESS_VERSION = "weighted_voting_auto_paper_readiness_v1"
WEIGHTED_VOTING_FINALIZED_BAR_PRODUCER_VERSION = "weighted_voting_finalized_bar_producer_v1"
WEIGHTED_VOTING_MARKET_EVENT_CONTRACT_VERSION = "weighted_voting_market_bar_finalized_event_v1"
WEIGHTED_VOTING_FINALIZED_EVENT_PREFIX = "weighted_voting.runtime.finalized_bar_events."
SECRET_FIELD_MARKERS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "credential",
        "authorization",
        "account_key",
        "broker_key",
        "private_key",
    }
)

WEIGHTED_VOTING_EXECUTION_OUTBOX_PENDING_STATES: frozenset[str] = frozenset(
    {
        "CREATED",
        "RISK_APPROVED",
        "READY_TO_SUBMIT",
        "SUBMITTING",
        "RECONCILIATION_REQUIRED",
    }
)
WEIGHTED_VOTING_EXECUTION_OUTBOX_TERMINAL_STATES: frozenset[str] = frozenset(
    {
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
    }
)


WEIGHTED_VOTING_AUTO_PAPER_CONDITIONS: tuple[str, ...] = (
    "weighted_voting_enabled",
    "paper_trading_enabled",
    "automatic_entries_enabled",
    "broker_endpoint_is_paper",
    "paper_account_verified",
    "paper_gateway_connected",
    "automatic_submission_rollout_passed",
    "runtime_supervisor_healthy",
    "finalized_bar_pipeline_healthy",
    "market_data_fresh",
    "exchange_session_open",
    "inside_entry_decision_window",
    "settings_loaded_and_valid",
    "active_weights_loaded_and_frozen",
    "algorithm_capital_allocation_positive",
    "inventory_loaded",
    "inventory_reconciled",
    "broker_orders_reconciled",
    "no_unprotected_position",
    "no_pending_recovery",
    "no_algorithm_halt",
    "no_global_halt",
    "daily_loss_limit_not_reached",
    "daily_trade_limit_not_reached",
    "remaining_algorithm_risk_positive",
)


@dataclass(frozen=True)
class WeightedVotingAutoPaperReadiness:
    ready: bool
    entry_submission_allowed: bool
    risk_reducing_exits_allowed: bool
    blocking_reason_codes: tuple[str, ...]
    warning_reason_codes: tuple[str, ...]
    checked_at: datetime
    dependency_health: dict[str, dict[str, Any]]
    runtime_status: Literal[
        "OFF",
        "STARTING",
        "RECOVERY_REQUIRED",
        "RECONCILING",
        "SHADOW",
        "PAPER_READY",
        "PAPER_ACTIVE",
        "ENTRY_PAUSED",
        "HALTED",
        "DEGRADED",
    ]
    version: str = WEIGHTED_VOTING_AUTO_PAPER_READINESS_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ready": self.ready,
            "entry_submission_allowed": self.entry_submission_allowed,
            "entrySubmissionAllowed": self.entry_submission_allowed,
            "risk_reducing_exits_allowed": self.risk_reducing_exits_allowed,
            "riskReducingExitsAllowed": self.risk_reducing_exits_allowed,
            "blocking_reason_codes": list(self.blocking_reason_codes),
            "blockingReasonCodes": list(self.blocking_reason_codes),
            "warning_reason_codes": list(self.warning_reason_codes),
            "warningReasonCodes": list(self.warning_reason_codes),
            "checked_at": self.checked_at.isoformat(),
            "checkedAt": self.checked_at.isoformat(),
            "dependency_health": self.dependency_health,
            "dependencyHealth": self.dependency_health,
            "runtime_status": self.runtime_status,
            "runtimeStatus": self.runtime_status,
        }


@dataclass(frozen=True)
class WeightedVotingRuntimeControl:
    algorithm_id: Literal["weighted_voting"] = WEIGHTED_VOTING_ALGORITHM_ID
    paper_trading_enabled: bool = False
    automatic_entries_enabled: bool = False
    mode: Literal["PAPER"] = "PAPER"
    version: str = WEIGHTED_VOTING_RUNTIME_CONTROL_VERSION
    record_version: int = 1
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = "system"
    reason: str = "weighted_voting.runtime.control.default_paper_off"
    reason_codes: tuple[str, ...] = ("weighted_voting.runtime.control.default_paper_off",)

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting runtime control must use algorithm_id weighted_voting")
        if self.mode != "PAPER":
            raise ValueError("Weighted Voting runtime control only supports PAPER mode")
        if self.updated_at.tzinfo is None:
            object.__setattr__(self, "updated_at", self.updated_at.replace(tzinfo=timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "paper_trading_enabled": self.paper_trading_enabled,
            "paperTradingEnabled": self.paper_trading_enabled,
            "automatic_entries_enabled": self.automatic_entries_enabled,
            "automaticEntriesEnabled": self.automatic_entries_enabled,
            "mode": self.mode,
            "version": self.version,
            "record_version": self.record_version,
            "recordVersion": self.record_version,
            "updated_at": self.updated_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "updated_by": self.updated_by,
            "updatedBy": self.updated_by,
            "reason": self.reason,
            "reasonCodes": list(self.reason_codes),
            "reason_codes": list(self.reason_codes),
            "liveTradingEnabled": False,
            "riskReducingExitsEnabled": True,
            "protectiveOrdersEnabled": True,
            "reconciliationEnabled": True,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WeightedVotingRuntimeControl":
        algorithm_id = str(payload.get("algorithm_id") or payload.get("algorithmId") or WEIGHTED_VOTING_ALGORITHM_ID)
        updated_raw = payload.get("updated_at") or payload.get("updatedAt")
        updated_at = _parse_optional_datetime(updated_raw) or _now()
        return cls(
            algorithm_id=algorithm_id,  # type: ignore[arg-type]
            paper_trading_enabled=bool(payload.get("paper_trading_enabled", payload.get("paperTradingEnabled", False))),
            automatic_entries_enabled=bool(payload.get("automatic_entries_enabled", payload.get("automaticEntriesEnabled", False))),
            mode=str(payload.get("mode") or "PAPER"),  # type: ignore[arg-type]
            version=str(payload.get("version") or WEIGHTED_VOTING_RUNTIME_CONTROL_VERSION),
            record_version=int(payload.get("record_version") or payload.get("recordVersion") or 1),
            updated_at=updated_at,
            updated_by=str(payload.get("updated_by") or payload.get("updatedBy") or "system"),
            reason=str(payload.get("reason") or "weighted_voting.runtime.control.loaded"),
            reason_codes=tuple(str(code) for code in payload.get("reason_codes", payload.get("reasonCodes", ("weighted_voting.runtime.control.loaded",)))),
        )


class WeightedVotingMarketDataClient(Protocol):
    async def get_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        limit: int,
        start: str | None,
        end: str | None,
        sort: str,
    ) -> list[dict[str, Any]]:
        ...


class WeightedVotingCandleStore(Protocol):
    def upsert_many(self, candles: list[dict]) -> None:
        ...

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict]:
        ...


@dataclass(frozen=True)
class WeightedVotingFinalisedBarEvent:
    algorithm_id: Literal["weighted_voting"]
    symbol: str
    finalised_candle_timestamp: datetime
    data_manifest_hash: str
    market_payload: dict[str, Any]
    published_at: datetime
    bar_start: datetime | None = None
    bar_end: datetime | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    data_source: str = "unknown"
    source_sequence: int | None = None
    finalized: bool = True
    event_id: str = ""
    replay_recovery: bool = False

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting finalised-bar events require algorithm_id weighted_voting")
        if self.finalised_candle_timestamp.tzinfo is None:
            object.__setattr__(self, "finalised_candle_timestamp", self.finalised_candle_timestamp.replace(tzinfo=timezone.utc))
        if self.published_at.tzinfo is None:
            object.__setattr__(self, "published_at", self.published_at.replace(tzinfo=timezone.utc))
        bar_start = self.bar_start or self.finalised_candle_timestamp
        if bar_start.tzinfo is None:
            bar_start = bar_start.replace(tzinfo=timezone.utc)
        bar_end = self.bar_end or bar_start + timedelta(minutes=1)
        if bar_end.tzinfo is None:
            bar_end = bar_end.replace(tzinfo=timezone.utc)
        object.__setattr__(self, "bar_start", bar_start)
        object.__setattr__(self, "bar_end", bar_end)
        if bar_end - bar_start != timedelta(minutes=1):
            raise ValueError("Weighted Voting finalized-bar events must describe exactly one one-minute candle")
        if not self.finalized:
            raise ValueError("Weighted Voting finalized-bar events must be finalized")
        if self.source_sequence is None:
            object.__setattr__(self, "source_sequence", int(bar_end.timestamp() // 60))
        if not self.event_id:
            object.__setattr__(
                self,
                "event_id",
                weighted_voting_market_event_id(
                    symbol=self.symbol,
                    bar_end=bar_end,
                    source=self.data_source,
                    source_sequence=int(self.source_sequence or 0),
                ),
            )

    def as_dict(self, *, exclude_event_id: bool = False) -> dict[str, Any]:
        payload = {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "symbol": self.symbol,
            "bar_start": self.bar_start.isoformat() if self.bar_start else None,
            "barStart": self.bar_start.isoformat() if self.bar_start else None,
            "bar_end": self.bar_end.isoformat() if self.bar_end else None,
            "barEnd": self.bar_end.isoformat() if self.bar_end else None,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "ohlcv": {
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
            },
            "data_source": self.data_source,
            "dataSource": self.data_source,
            "source_sequence": self.source_sequence,
            "sourceSequence": self.source_sequence,
            "finalized": self.finalized,
            "finalised_candle_timestamp": self.finalised_candle_timestamp.isoformat(),
            "data_manifest_hash": self.data_manifest_hash,
            "dataManifestHash": self.data_manifest_hash,
            "market_payload": self.market_payload,
            "published_at": self.published_at.isoformat(),
            "publishedAt": self.published_at.isoformat(),
            "event_id": self.event_id,
            "eventId": self.event_id,
            "eventType": "weighted_voting.market.bar.finalized",
            "eventContractVersion": WEIGHTED_VOTING_MARKET_EVENT_CONTRACT_VERSION,
            "replay_recovery": self.replay_recovery,
        }
        if exclude_event_id:
            payload.pop("event_id", None)
        return payload


@dataclass(frozen=True)
class WeightedVotingRuntimeConfig:
    symbols: tuple[str, ...] = ("SPY",)
    paper_execution_mode: Literal["LOCAL_PAPER", "BROKER_PAPER"] = "LOCAL_PAPER"
    queue_maxsize: int = 256
    max_queue_lag_seconds: int = 75
    finalized_bar_gap_tolerance: int = 0
    repeated_order_rejection_threshold: int = 3
    market_data_poll_seconds: float = 5.0
    market_data_finalization_delay_seconds: int = 2
    market_data_fetch_limit: int = 450
    market_data_history_limit: int = 390
    worker_restart_failure_threshold: int = 3
    heartbeat_interval_seconds: float = 30.0
    maintenance_interval_seconds: float = 60.0


@dataclass(frozen=True)
class WeightedVotingFinalizedBarProductionResult:
    algorithm_id: Literal["weighted_voting"]
    status: str
    accepted: bool
    event_id: str | None
    symbol: str
    bar_start: str | None
    bar_end: str | None
    reason_codes: tuple[str, ...]
    duplicate: bool = False
    stale: bool = False
    gap_detected: bool = False
    source_sequence: int | None = None
    queue_depth: int | None = None
    queue_lag_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "producerVersion": WEIGHTED_VOTING_FINALIZED_BAR_PRODUCER_VERSION,
            "status": self.status,
            "accepted": self.accepted,
            "event_id": self.event_id,
            "eventId": self.event_id,
            "symbol": self.symbol,
            "bar_start": self.bar_start,
            "barStart": self.bar_start,
            "bar_end": self.bar_end,
            "barEnd": self.bar_end,
            "reason_codes": list(self.reason_codes),
            "reasonCodes": list(self.reason_codes),
            "duplicate": self.duplicate,
            "stale": self.stale,
            "gapDetected": self.gap_detected,
            "source_sequence": self.source_sequence,
            "sourceSequence": self.source_sequence,
            "queue_depth": self.queue_depth,
            "queueDepth": self.queue_depth,
            "queue_lag_seconds": self.queue_lag_seconds,
            "queueLagSeconds": self.queue_lag_seconds,
        }


@dataclass(frozen=True)
class WeightedVotingFinalizedBarProducerConfig:
    symbols: tuple[str, ...] = ("SPY",)
    feed: str = "iex"
    timeframe: str = "1Min"
    fetch_limit: int = 450
    history_limit: int = 390
    finalization_delay_seconds: int = 2
    max_staleness_seconds: int = 75
    source_authority: str = "backend.market_data.weighted_voting_finalized_bar_producer"


class WeightedVotingFinalizedBarProducer:
    def __init__(
        self,
        *,
        market_data_client: WeightedVotingMarketDataClient,
        candle_store: WeightedVotingCandleStore,
        publish_event: Any,
        config: WeightedVotingFinalizedBarProducerConfig | None = None,
    ) -> None:
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.publish_event = publish_event
        self.config = config or WeightedVotingFinalizedBarProducerConfig()

    async def poll_once(self, *, now: datetime | None = None) -> tuple[dict[str, Any], ...]:
        current = _require_utc_datetime(now or datetime.now(UTC))
        results = []
        for symbol in self.config.symbols:
            results.append((await self.process_symbol(symbol, now=current)).as_dict())
        return tuple(results)

    async def process_symbol(self, symbol: str, *, now: datetime | None = None) -> WeightedVotingFinalizedBarProductionResult:
        current = _require_utc_datetime(now or datetime.now(UTC))
        normalized_symbol = symbol.upper()
        rows = await self.market_data_client.get_bars(
            symbol=normalized_symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=self.config.fetch_limit,
            start=None,
            end=current.isoformat(),
            sort="asc",
        )
        valid, invalid = _valid_weighted_voting_candles(
            rows,
            symbol=normalized_symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            now=current,
        )
        if valid:
            self.candle_store.upsert_many(valid)
        if invalid:
            return WeightedVotingFinalizedBarProductionResult(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                status="REJECTED_INVALID_CANDLES",
                accepted=False,
                event_id=None,
                symbol=normalized_symbol,
                bar_start=None,
                bar_end=None,
                reason_codes=("weighted_voting.market_data.invalid_candle_rejected",),
            )

        finalized = [
            row
            for row in valid
            if _is_weighted_voting_finalized_bar(
                row,
                now=current,
                finalization_delay_seconds=self.config.finalization_delay_seconds,
            )
        ]
        if not finalized:
            return WeightedVotingFinalizedBarProductionResult(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                status="BLOCKED_NO_FINALIZED_BAR",
                accepted=False,
                event_id=None,
                symbol=normalized_symbol,
                bar_start=None,
                bar_end=None,
                reason_codes=("weighted_voting.market_data.no_finalized_one_minute_bar",),
            )

        candle = finalized[-1]
        bar_start = _parse_optional_datetime(candle["timestamp"])
        if bar_start is None:
            return WeightedVotingFinalizedBarProductionResult(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                status="REJECTED_INVALID_TIMESTAMP",
                accepted=False,
                event_id=None,
                symbol=normalized_symbol,
                bar_start=None,
                bar_end=None,
                reason_codes=("weighted_voting.market_data.invalid_timestamp",),
            )
        bar_end = bar_start + timedelta(minutes=1)
        stale_seconds = max(0.0, (current - bar_end).total_seconds())
        if stale_seconds > float(self.config.max_staleness_seconds):
            return WeightedVotingFinalizedBarProductionResult(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                status="REJECTED_STALE",
                accepted=False,
                event_id=None,
                symbol=normalized_symbol,
                bar_start=bar_start.isoformat(),
                bar_end=bar_end.isoformat(),
                stale=True,
                reason_codes=("weighted_voting.market_data.stale_finalized_candle_rejected",),
            )

        history = self.candle_store.latest_until(
            symbol=normalized_symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=self.config.history_limit,
            end=bar_start.isoformat(),
        )
        quality = _weighted_voting_history_quality(history, bar_start=bar_start)
        if quality["status"] != "OK":
            return WeightedVotingFinalizedBarProductionResult(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                status=str(quality["status"]),
                accepted=False,
                event_id=None,
                symbol=normalized_symbol,
                bar_start=bar_start.isoformat(),
                bar_end=bar_end.isoformat(),
                reason_codes=tuple(quality["reasonCodes"]),
                gap_detected=bool(quality.get("gaps")),
            )

        event = _weighted_voting_event_from_candle(
            candle,
            history=history,
            received_at=current,
            source_sequence=len(history),
            source_authority=self.config.source_authority,
        )
        result = self.publish_event(event)
        if hasattr(result, "__await__"):
            result = await result
        accepted = bool(result)
        return WeightedVotingFinalizedBarProductionResult(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            status="PUBLISHED" if accepted else "REJECTED_QUEUE_FULL",
            accepted=accepted,
            event_id=event.event_id,
            symbol=normalized_symbol,
            bar_start=event.bar_start.isoformat() if event.bar_start else None,
            bar_end=event.bar_end.isoformat() if event.bar_end else None,
            reason_codes=("weighted_voting.market_data.finalized_bar_published",) if accepted else ("weighted_voting.market_data.queue_full",),
            source_sequence=event.source_sequence,
        )


@dataclass
class WeightedVotingRuntimeMetrics:
    supervisor_started: bool = False
    automatic_order_creation_paused: bool = True
    paused: bool = False
    queue_depth: int = 0
    execution_queue_depth: int = 0
    processed_events: int = 0
    duplicate_events: int = 0
    rejected_events: int = 0
    stale_events: int = 0
    out_of_order_events: int = 0
    persisted_decisions: int = 0
    enqueued_orders: int = 0
    submitted_orders: int = 0
    rejected_execution_events: int = 0
    entry_creation_paused_for_reconciliation: bool = False
    inventory_reconciled: bool = False
    risk_reducing_exits_allowed: bool = True
    worker_failures: dict[str, int] = field(default_factory=dict)
    worker_restarts: dict[str, int] = field(default_factory=dict)
    last_event_timestamp_by_symbol: dict[str, str] = field(default_factory=dict)
    last_checkpoint_by_symbol: dict[str, str] = field(default_factory=dict)
    last_decision_id: str | None = None
    last_decision: dict[str, Any] | None = None
    last_local_gate_result: dict[str, Any] | None = None
    last_finalised_bar_received: dict[str, Any] | None = None
    last_bar_processed: dict[str, Any] | None = None
    processing_lag_seconds: float | None = None
    last_accepted_proposal: dict[str, Any] | None = None
    last_global_risk_response: dict[str, Any] | None = None
    last_order_intent: dict[str, Any] | None = None
    last_order_submission: dict[str, Any] | None = None
    last_acknowledgement: dict[str, Any] | None = None
    last_fill: dict[str, Any] | None = None
    last_reconciliation: dict[str, Any] | None = None
    pause_reason: str | None = None
    decision_latency_ms: float | None = None
    risk_service_latency_ms: float | None = None
    broker_latency_ms: float | None = None
    gate_rejection_counts: dict[str, int] = field(default_factory=dict)
    strategy_opportunity_counts: dict[str, int] = field(default_factory=dict)
    strategy_signal_counts: dict[str, dict[str, int]] = field(default_factory=lambda: {"active": {}, "shadow": {}})
    proposed_vs_allowed_quantity: dict[str, Any] = field(default_factory=dict)
    fill_quality: dict[str, Any] = field(default_factory=dict)
    slippage: dict[str, Any] = field(default_factory=dict)
    reconciliation_discrepancies: int = 0
    recovery_required: bool = False
    recovery_state: dict[str, Any] = field(default_factory=dict)
    quarantined_snapshots: int = 0
    circuit_breaker_open: bool = False
    last_circuit_breaker: dict[str, Any] | None = None
    consecutive_order_rejections: int = 0
    last_error: str | None = None
    queue_lag_seconds: float | None = None
    risk_queue_depth: int = 0
    last_finalized_bar_producer_result: dict[str, Any] | None = None
    finalized_bar_events_published: int = 0
    finalized_bar_event_gaps: int = 0


@dataclass(frozen=True)
class WeightedVotingRiskQueueItem:
    algorithm_id: Literal["weighted_voting"]
    risk_item_id: str
    idempotency_key: str
    decision_id: str
    order_intent_id: str
    proposal: GlobalOrderProposal
    local_gate_result: WeightedVotingGatePipelineResult
    evaluated_at: datetime
    inventory_snapshot_version: int
    current_algorithm_exposure: float
    current_account_exposure: float
    daily_algorithm_pnl: float
    account_level_risk_observations: dict[str, Any]
    settings_version: str
    source_result: dict[str, Any]
    status: str = "PENDING_GLOBAL_RISK"
    runtime_version: str = WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting risk queue rejects cross-algorithm items")
        if self.proposal.algorithmId != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting risk queue requires a weighted_voting proposal")
        if self.proposal.decisionId != self.decision_id or self.proposal.orderIntentId != self.order_intent_id:
            raise ValueError("Weighted Voting risk queue proposal identity mismatch")
        if self.inventory_snapshot_version < 0:
            raise ValueError("Weighted Voting risk queue requires an authoritative inventory snapshot version")

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "runtimeVersion": self.runtime_version,
            "riskItemId": self.risk_item_id,
            "idempotencyKey": self.idempotency_key,
            "decisionId": self.decision_id,
            "orderIntentId": self.order_intent_id,
            "proposal": self.proposal.model_dump(mode="json"),
            "localGateResult": _json_ready(self.local_gate_result),
            "evaluatedAt": self.evaluated_at.isoformat(),
            "inventorySnapshotVersion": self.inventory_snapshot_version,
            "currentAlgorithmExposure": self.current_algorithm_exposure,
            "currentAccountExposure": self.current_account_exposure,
            "dailyAlgorithmPnl": self.daily_algorithm_pnl,
            "accountLevelRiskObservations": _json_ready(self.account_level_risk_observations),
            "settingsVersion": self.settings_version,
            "status": self.status,
            "reasonCodes": ("weighted_voting.runtime.risk_queue.item_persisted",),
        }


class WeightedVotingEventBus:
    def __init__(self, *, maxsize: int = 256) -> None:
        self.queue: asyncio.Queue[WeightedVotingFinalisedBarEvent] = asyncio.Queue(maxsize=maxsize)
        self.dropped_events = 0

    async def publish(self, event: WeightedVotingFinalisedBarEvent) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped_events += 1
            return False

    async def next_event(self) -> WeightedVotingFinalisedBarEvent:
        return await self.queue.get()

    def task_done(self) -> None:
        self.queue.task_done()

    def depth(self) -> int:
        return self.queue.qsize()


@dataclass(frozen=True)
class WeightedVotingSessionClock:
    session_date: str | None
    exchange_open: datetime | None
    exchange_close: datetime | None
    early_close: bool
    current_phase: str
    minute_from_open: int | None
    minutes_until_close: int | None
    regular_session: bool
    event_timestamp_utc: datetime
    event_timestamp_et: datetime
    exchange_timezone: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessionDate": self.session_date,
            "exchangeOpen": self.exchange_open.isoformat() if self.exchange_open else None,
            "exchangeClose": self.exchange_close.isoformat() if self.exchange_close else None,
            "earlyClose": self.early_close,
            "currentPhase": self.current_phase,
            "minuteFromOpen": self.minute_from_open,
            "minutesUntilClose": self.minutes_until_close,
            "regularSession": self.regular_session,
            "eventTimestampUtc": self.event_timestamp_utc.isoformat(),
            "eventTimestampEt": self.event_timestamp_et.isoformat(),
            "exchangeTimezone": self.exchange_timezone,
            "reason": self.reason,
        }


class WeightedVotingMarketCalendar:
    def __init__(self, exchange_calendar: ExchangeCalendarService | None = None) -> None:
        self.exchange_calendar = exchange_calendar or ExchangeCalendarService()

    def session_clock(self, timestamp: datetime) -> WeightedVotingSessionClock:
        event_utc = _require_utc_datetime(timestamp)
        event_et = event_utc.astimezone(NEW_YORK)
        session = self.exchange_calendar.session_for_date(event_et.date())
        if not session.can_trade or session.openTimestamp is None or session.closeTimestamp is None:
            return WeightedVotingSessionClock(
                session_date=session.sessionDate.isoformat(),
                exchange_open=session.openTimestamp,
                exchange_close=session.closeTimestamp,
                early_close=bool(session.isEarlyClose),
                current_phase="closed",
                minute_from_open=None,
                minutes_until_close=None,
                regular_session=False,
                event_timestamp_utc=event_utc,
                event_timestamp_et=event_et,
                exchange_timezone=session.timezone,
                reason=session.closureReason or "holiday_or_weekend",
            )
        if event_utc < session.openTimestamp:
            phase = "premarket"
            regular = False
        elif event_utc >= session.closeTimestamp:
            phase = "postmarket"
            regular = False
        else:
            regular = True
            minute_from_open_value = int((event_utc - session.openTimestamp).total_seconds() // 60)
            if minute_from_open_value == 0:
                phase = "opening_auction"
            elif minute_from_open_value < 30:
                phase = "opening_range"
            elif minute_from_open_value < 120:
                phase = "morning"
            elif (session.closeTimestamp - event_utc).total_seconds() <= 30 * 60:
                phase = "closing_auction"
            else:
                phase = "afternoon"
        minute_from_open = int((event_utc - session.openTimestamp).total_seconds() // 60) if regular else None
        minutes_until_close = int((session.closeTimestamp - event_utc).total_seconds() // 60) if regular else None
        return WeightedVotingSessionClock(
            session_date=session.sessionDate.isoformat(),
            exchange_open=session.openTimestamp,
            exchange_close=session.closeTimestamp,
            early_close=bool(session.isEarlyClose),
            current_phase=phase,
            minute_from_open=minute_from_open,
            minutes_until_close=minutes_until_close,
            regular_session=regular,
            event_timestamp_utc=event_utc,
            event_timestamp_et=event_et,
            exchange_timezone=session.timezone,
            reason="regular_session" if regular else phase,
        )

    def is_trading_session(self, timestamp: datetime, session_phase: str | None = None) -> bool:
        try:
            clock = self.session_clock(timestamp)
        except Exception:
            return False
        if clock.current_phase in {"closed", "premarket", "postmarket", "unknown"}:
            return False
        return bool(clock.regular_session)

    def inside_entry_decision_window(self, timestamp: datetime, config: WeightedVotingConfig) -> bool:
        try:
            clock = self.session_clock(timestamp)
        except Exception:
            return False
        if not clock.regular_session or clock.minute_from_open is None or clock.minutes_until_close is None:
            return False
        entry_delay_minutes = max(0, int(getattr(config, "opening_range_minutes", 15) or 0))
        if clock.minute_from_open < entry_delay_minutes:
            return False
        if clock.minutes_until_close <= max(0, int(getattr(config, "session_cutoff_minutes", 15) or 0)):
            return False
        local_minute = clock.event_timestamp_et.hour * 60 + clock.event_timestamp_et.minute
        return _entry_start_eastern_minutes(config) <= local_minute < _entry_cutoff_eastern_minutes(config)

    def should_cancel_entries(self, timestamp: datetime, config: WeightedVotingConfig) -> bool:
        try:
            clock = self.session_clock(timestamp)
        except Exception:
            return True
        if not clock.regular_session:
            return True
        if clock.minutes_until_close is None:
            return True
        return clock.minutes_until_close <= max(0, int(getattr(config, "session_cutoff_minutes", 15) or 0))

    def should_flatten(self, timestamp: datetime, config: WeightedVotingConfig) -> bool:
        try:
            clock = self.session_clock(timestamp)
        except Exception:
            return True
        if not clock.regular_session:
            return clock.current_phase in {"postmarket", "closed"}
        if clock.minutes_until_close is None:
            return True
        return clock.minutes_until_close <= max(0, int(getattr(config, "force_flat_minutes_before_close", 1) or 0))


class WeightedVotingRuntimeWorker:
    def __init__(self, supervisor: "WeightedVotingRuntimeSupervisor", worker_id: str) -> None:
        self.supervisor = supervisor
        self.worker_id = worker_id

    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingRuntimeFinalizedBarDatasetProvider:
    def __init__(self, store: WeightedVotingStateStore, *, symbol: str) -> None:
        self.store = store
        self.symbol = symbol.upper()

    def candles_for_session(self, session_date: date) -> tuple[WeightedVotingCandle, ...]:
        records = []
        for key, payload in _store_items(self.store):
            if not key.startswith(RUNTIME_EVENT_PREFIX):
                continue
            if str(payload.get("algorithm_id") or payload.get("algorithmId")) != WEIGHTED_VOTING_ALGORITHM_ID:
                continue
            if str(payload.get("symbol") or "").upper() != self.symbol:
                continue
            if str(payload.get("status") or "").startswith("rejected") or str(payload.get("status") or "") in {"duplicate_noop", "paused"}:
                continue
            if payload.get("finalized") is False:
                continue
            bar_end = _parse_optional_datetime(payload.get("bar_end") or payload.get("barEnd") or payload.get("finalised_candle_timestamp"))
            if not bar_end or bar_end.astimezone(NEW_YORK).date() != session_date:
                continue
            records.append((bar_end, payload))
        if not records:
            return ()
        latest = max(records, key=lambda item: item[0])[1]
        history = _candles_from_persisted_market_payload(latest.get("market_payload") if isinstance(latest.get("market_payload"), dict) else {}, session_date)
        if history:
            return history
        event_candles = []
        for _, payload in sorted(records):
            candle = _candle_from_event_payload(payload)
            if candle is not None:
                event_candles.append(candle)
        return tuple(event_candles)


class WeightedVotingBarEventWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            producer = self.supervisor.finalized_bar_producer
            if producer is not None:
                results = await producer.poll_once()
                if results:
                    self.supervisor.metrics.last_finalized_bar_producer_result = results[-1]
                    self.supervisor.store.write_snapshot(
                        "weighted_voting.runtime.finalized_bar_producer.latest",
                        {
                            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                            "producer_version": WEIGHTED_VOTING_FINALIZED_BAR_PRODUCER_VERSION,
                            "results": list(results),
                            "recorded_at": _now().isoformat(),
                            "reason_codes": ("weighted_voting.market_data.producer_polled",),
                        },
                    )
            await asyncio.sleep(self.supervisor.config.market_data_poll_seconds)


class WeightedVotingDecisionWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            event = await self.supervisor.event_bus.next_event()
            try:
                await self.supervisor.process_finalised_bar_event(event)
            finally:
                self.supervisor.event_bus.task_done()


class WeightedVotingRiskWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            item = await self.supervisor.risk_queue.get()
            try:
                self.supervisor.process_risk_queue_item(item)
            finally:
                self.supervisor.risk_queue.task_done()


class WeightedVotingExecutionWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            item = await self.supervisor.execution_queue.get()
            try:
                self.supervisor.process_execution_queue_item(item)
            finally:
                self.supervisor.execution_queue.task_done()


class WeightedVotingReconciliationWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            self.supervisor.reconcile_broker_inventory(trigger="periodic_session")
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingPositionManager(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        self.supervisor.restore_position_management()
        while not self.supervisor.stop_event.is_set():
            self.supervisor.manage_positions_once(trigger="periodic_session")
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingDailyUpdateWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            self.supervisor.run_daily_update_if_due(trigger="periodic_session")
            await asyncio.sleep(self.supervisor.config.maintenance_interval_seconds)


class WeightedVotingRecoveryWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        self.supervisor.recover_from_checkpoints()
        await super().run()


class WeightedVotingHeartbeatWorker(WeightedVotingRuntimeWorker):
    async def run(self) -> None:
        while not self.supervisor.stop_event.is_set():
            self.supervisor.write_heartbeat()
            await asyncio.sleep(self.supervisor.config.heartbeat_interval_seconds)


class WeightedVotingRuntimeSupervisor:
    def __init__(
        self,
        *,
        service: WeightedVotingService | None = None,
        store: WeightedVotingStateStore | None = None,
        config: WeightedVotingRuntimeConfig | None = None,
        weighted_config: WeightedVotingConfig | None = None,
        event_bus: WeightedVotingEventBus | None = None,
        calendar: WeightedVotingMarketCalendar | None = None,
        paper_gateway: Any = _DEFAULT_PAPER_GATEWAY,
        inventory_repository: WeightedVotingInventoryRepository | None = None,
        account_port: WeightedVotingAccountObservationPort | None = None,
        global_risk_port: WeightedVotingGlobalRiskPort | None = None,
        rollout_flags: WeightedVotingRolloutFlags | None = None,
        rollout_validation: WeightedVotingRolloutValidation | None = None,
        position_manager: WeightedVotingPositionManagerService | None = None,
        finalized_bar_producer: WeightedVotingFinalizedBarProducer | None = None,
    ) -> None:
        self.store = store or WeightedVotingFilesystemStateStore()
        self.weighted_config = weighted_config or WeightedVotingConfig()
        self.config = config or WeightedVotingRuntimeConfig(paper_execution_mode=self.weighted_config.paper_execution_mode)
        self.event_bus = event_bus or WeightedVotingEventBus(maxsize=self.config.queue_maxsize)
        self.risk_queue: asyncio.Queue[WeightedVotingRiskQueueItem] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self.execution_queue: asyncio.Queue[WeightedVotingExecutionQueueItem] = asyncio.Queue(maxsize=self.config.queue_maxsize)
        self.calendar = calendar or WeightedVotingMarketCalendar()
        self.inventory_repository = inventory_repository or WeightedVotingInventoryRepository(
            self.store,
            allocated_capital=self.weighted_config.local_paper_initial_capital,
            allow_shorting=self.weighted_config.local_paper_allow_shorting,
        )
        resolved_account_port = account_port
        resolved_global_risk_port = global_risk_port
        resolved_central_risk_service = None
        if paper_gateway is _DEFAULT_PAPER_GATEWAY:
            if self.config.paper_execution_mode == "LOCAL_PAPER":
                if inventory_repository is None:
                    _ensure_weighted_voting_local_paper_session(
                        store=self.store,
                        inventory_repository=self.inventory_repository,
                        initial_capital=self.weighted_config.local_paper_initial_capital,
                    )
                broker, local_risk_port, local_risk_service = build_weighted_voting_local_paper_gateway_dependencies(self.store, self.inventory_repository)
                self.paper_gateway = PaperOrderGateway(
                    broker,
                    self.store,
                    execution_mode="LOCAL_PAPER",
                    account_snapshot_provider=broker.gateway_account_snapshot,
                    portfolio_snapshot_provider=broker.gateway_portfolio_snapshot,
                )
                if resolved_account_port is None:
                    resolved_account_port = broker
                if resolved_global_risk_port is None:
                    resolved_global_risk_port = local_risk_port
                resolved_central_risk_service = local_risk_service
            elif self.config.paper_execution_mode == "BROKER_PAPER":
                from backend.app.algorithms.weighted_voting.alpaca_paper_broker import build_weighted_voting_paper_gateway_dependencies

                broker, broker_account_port = build_weighted_voting_paper_gateway_dependencies()
                self.paper_gateway = PaperOrderGateway(
                    broker,
                    self.store,
                    execution_mode="BROKER_PAPER",
                )
                if resolved_account_port is None:
                    resolved_account_port = broker_account_port
            else:
                raise ValueError(f"Unsupported Weighted Voting paper execution mode: {self.config.paper_execution_mode}")
        elif paper_gateway is None:
            self.paper_gateway = None
        else:
            self.paper_gateway = paper_gateway
            broker_account_port = getattr(paper_gateway.broker, "account_observation", None)
            if resolved_account_port is None and callable(broker_account_port):
                resolved_account_port = paper_gateway.broker
        self.service = service or WeightedVotingService(config=self.weighted_config, store=self.store, central_risk_service=resolved_central_risk_service)
        self.account_port = resolved_account_port or WeightedVotingUnavailableAccountPort()
        self.global_risk_port = resolved_global_risk_port or WeightedVotingUnavailableGlobalRiskPort()
        self.rollout_flags = rollout_flags
        self.rollout_validation = rollout_validation
        self.position_manager = position_manager or WeightedVotingPositionManagerService(store=self.store, inventory_repository=self.inventory_repository)
        self.metrics = WeightedVotingRuntimeMetrics()
        self.finalized_bar_producer = finalized_bar_producer if finalized_bar_producer is not None else self._build_default_finalized_bar_producer()
        self.stop_event = asyncio.Event()
        self.tasks: dict[str, asyncio.Task] = {}
        self.symbol_locks: dict[str, asyncio.Lock] = {symbol.upper(): asyncio.Lock() for symbol in self.config.symbols}
        self.workers = (
            WeightedVotingBarEventWorker(self, "WeightedVotingBarEventWorker"),
            WeightedVotingDecisionWorker(self, "WeightedVotingDecisionWorker"),
            WeightedVotingRiskWorker(self, "WeightedVotingRiskWorker"),
            WeightedVotingExecutionWorker(self, "WeightedVotingExecutionWorker"),
            WeightedVotingReconciliationWorker(self, "WeightedVotingReconciliationWorker"),
            WeightedVotingPositionManager(self, "WeightedVotingPositionManager"),
            WeightedVotingDailyUpdateWorker(self, "WeightedVotingDailyUpdateWorker"),
            WeightedVotingRecoveryWorker(self, "WeightedVotingRecoveryWorker"),
            WeightedVotingHeartbeatWorker(self, "WeightedVotingHeartbeatWorker"),
        )

    async def start(self) -> None:
        if self.metrics.supervisor_started:
            return
        self.stop_event.clear()
        self.metrics.supervisor_started = True
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = "weighted_voting.runtime.restart_fail_closed_until_recovery"
        self.recover_from_checkpoints()
        self.reconcile_broker_inventory(startup=True)
        for worker in self.workers:
            self._start_worker(worker)
        self._write_status("started", ("weighted_voting.runtime.supervisor.started",))

    async def shutdown(self) -> None:
        self.stop_event.set()
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        closer = getattr(getattr(self.paper_gateway, "broker", None), "close", None)
        if callable(closer):
            closer()
        self.metrics.supervisor_started = False
        self._write_status("stopped", ("weighted_voting.runtime.supervisor.stopped",))

    async def publish_finalised_bar(self, event: WeightedVotingFinalisedBarEvent) -> bool:
        if event.symbol.upper() not in {symbol.upper() for symbol in self.config.symbols}:
            self.metrics.rejected_events += 1
            self._write_event_record(event, "rejected_unconfigured_symbol", None, ("weighted_voting.runtime.unconfigured_symbol",))
            return False
        if not event.finalized:
            self.metrics.rejected_events += 1
            self._write_event_record(event, "rejected_partial_candle", None, ("weighted_voting.runtime.partial_candle_rejected",))
            return False
        accepted_key = f"{WEIGHTED_VOTING_FINALIZED_EVENT_PREFIX}accepted.{event.event_id}"
        accepted_payload = _read_optional(self.store, accepted_key)
        if accepted_payload is not None:
            accepted_manifest_hash = str(accepted_payload.get("data_manifest_hash") or accepted_payload.get("dataManifestHash") or "")
            if accepted_manifest_hash and accepted_manifest_hash != event.data_manifest_hash:
                self.metrics.rejected_events += 1
                self.store.write_snapshot(
                    f"{WEIGHTED_VOTING_FINALIZED_EVENT_PREFIX}rejected_conflict.{event.event_id}",
                    {
                        **event.as_dict(),
                        "conflictsWithEventId": accepted_payload.get("event_id") or accepted_payload.get("eventId"),
                        "recorded_at": _now().isoformat(),
                        "reason_codes": ("weighted_voting.runtime.conflicting_revision_rejected",),
                    },
                )
                return False
            self.metrics.duplicate_events += 1
            return False
        for key, payload in _store_items(self.store):
            if not key.startswith(f"{WEIGHTED_VOTING_FINALIZED_EVENT_PREFIX}accepted."):
                continue
            if str(payload.get("symbol") or "").upper() != event.symbol.upper():
                continue
            if str(payload.get("bar_start") or payload.get("barStart") or "") != (event.bar_start.isoformat() if event.bar_start else ""):
                continue
            if str(payload.get("data_manifest_hash") or payload.get("dataManifestHash") or "") != event.data_manifest_hash:
                self.metrics.rejected_events += 1
                self.store.write_snapshot(
                    f"{WEIGHTED_VOTING_FINALIZED_EVENT_PREFIX}rejected_conflict.{event.event_id}",
                    {
                        **event.as_dict(),
                        "conflictsWithEventId": payload.get("event_id") or payload.get("eventId"),
                        "recorded_at": _now().isoformat(),
                        "reason_codes": ("weighted_voting.runtime.conflicting_revision_rejected",),
                    },
                )
                return False
        self.metrics.last_finalised_bar_received = _bar_summary(event)
        self.metrics.queue_lag_seconds = max(0.0, (_now() - event.published_at).total_seconds())
        if self.metrics.queue_lag_seconds > self.config.max_queue_lag_seconds:
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.queue_lag_exceeded",
                trigger="finalized_bar_publish_queue_lag",
                details={"queueLagSeconds": self.metrics.queue_lag_seconds, "maxQueueLagSeconds": self.config.max_queue_lag_seconds, "eventId": event.event_id},
            )
        queue_depth_before_publish = self.event_bus.depth()
        published = await self.event_bus.publish(event)
        if not published:
            self.metrics.rejected_events += 1
            self._write_event_record(event, "rejected_backpressure", None, ("weighted_voting.runtime.queue_full",))
        else:
            self.metrics.finalized_bar_events_published += 1
            self.store.write_snapshot(
                accepted_key,
                {
                    **event.as_dict(),
                    "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                    "queue_lag_seconds": self.metrics.queue_lag_seconds,
                    "queue_depth_before_publish": queue_depth_before_publish,
                    "accepted_at": _now().isoformat(),
                    "reason_codes": ("weighted_voting.runtime.finalized_bar_event_accepted",),
                },
            )
        self.metrics.queue_depth = self.event_bus.depth()
        return published

    async def process_finalised_bar_event(self, event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
        symbol = event.symbol.upper()
        lock = self.symbol_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            try:
                return self._process_finalised_bar_event_locked(event)
            except Exception as exc:
                self.metrics.rejected_events += 1
                self.metrics.automatic_order_creation_paused = True
                self.metrics.recovery_required = True
                self.metrics.last_error = f"WeightedVotingRuntimeEvent: {exc}"
                reason_code = _circuit_breaker_reason_from_exception(exc)
                self._trip_circuit_breaker(
                    reason_code,
                    trigger="finalized_bar_processing_exception",
                    details={"eventId": event.event_id, "error": str(exc)},
                )
                failure = {
                    "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                    "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                    "status": "runtime_exception_safe_degradation",
                    "event_id": event.event_id,
                    "symbol": event.symbol,
                    "finalised_candle_timestamp": event.finalised_candle_timestamp.isoformat(),
                    "recorded_at": _now().isoformat(),
                    "reason_codes": ("weighted_voting.runtime.persistence_or_processing_exception_blocks_new_entries",),
                    "error": str(exc),
                }
                self.metrics.recovery_state = {
                    "recoveryRequired": True,
                    "unresolvedBoundaries": [{"boundary": "persistence_outage", "error": str(exc)}],
                    "newEntriesBlocked": True,
                }
                return failure

    def pause(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.supervisor.paused") -> None:
        prior = self._admin_state()
        self.metrics.paused = True
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        self._write_admin_audit(
            "pause_runtime",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.pause", reason),
        )
        self._write_status("paused", ("weighted_voting.runtime.supervisor.paused",))

    def resume(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.supervisor.resumed") -> None:
        prior = self._admin_state()
        self.metrics.paused = False
        if not self.metrics.entry_creation_paused_for_reconciliation:
            self.metrics.pause_reason = None
        self._write_admin_audit(
            "resume_runtime",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.resume", reason),
        )
        self._write_status("running", ("weighted_voting.runtime.supervisor.resumed",))

    def _build_default_finalized_bar_producer(self) -> WeightedVotingFinalizedBarProducer | None:
        if not isinstance(self.store, WeightedVotingFilesystemStateStore):
            return None
        try:
            from backend.app.alpaca import AlpacaClient
            from backend.app.config import get_settings
            from backend.app.database import CandleStore

            settings = get_settings()
            candle_store = CandleStore(settings)
            config = WeightedVotingFinalizedBarProducerConfig(
                symbols=tuple(symbol.upper() for symbol in self.config.symbols),
                fetch_limit=self.config.market_data_fetch_limit,
                history_limit=self.config.market_data_history_limit,
                finalization_delay_seconds=self.config.market_data_finalization_delay_seconds,
                max_staleness_seconds=self.config.max_queue_lag_seconds,
            )
            return WeightedVotingFinalizedBarProducer(
                market_data_client=AlpacaClient(settings),
                candle_store=candle_store,
                publish_event=self.publish_finalised_bar,
                config=config,
            )
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingFinalizedBarProducer: {exc}"
            self.metrics.automatic_order_creation_paused = True
            return None

    def pause_new_entries(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.entries_paused_by_admin") -> dict[str, Any]:
        prior = self._admin_state()
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        audit = self._write_admin_audit(
            "pause_new_entries",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.pause_new_entries", reason),
        )
        self._write_status("entries_paused", ("weighted_voting.runtime.entries_paused", reason))
        return audit

    def resume_new_entries(
        self,
        *,
        actor: str = "system",
        reason: str = "weighted_voting.runtime.entries_resumed_by_admin",
        validation_passed: bool = True,
    ) -> dict[str, Any]:
        prior = self._admin_state()
        healthy = self.healthy_state_check(actor=actor, reason=reason)
        if not validation_passed or self.metrics.entry_creation_paused_for_reconciliation or not healthy["healthy"]:
            self.metrics.automatic_order_creation_paused = True
            status_reason = "weighted_voting.runtime.entries_resume_rejected_validation_or_reconciliation"
        else:
            if self.metrics.circuit_breaker_open:
                self._close_circuit_breaker(actor=actor, reason=reason, healthy_state=healthy)
            self.metrics.automatic_order_creation_paused = False
            self.metrics.pause_reason = None
            status_reason = "weighted_voting.runtime.entries_resumed"
        audit = self._write_admin_audit(
            "resume_new_entries",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.resume_new_entries", reason, status_reason),
        )
        self._write_status("entries_resume_checked", (status_reason, reason))
        return audit

    def force_reconciliation(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.admin.force_reconciliation") -> dict[str, Any]:
        prior = self._admin_state()
        self.reconcile_broker_inventory(startup=False, reason=reason, trigger="administrator_request")
        return self._write_admin_audit(
            "force_reconciliation",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.admin.force_reconciliation", reason),
        )

    def handle_broker_reconnect(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.broker_reconnect") -> dict[str, Any]:
        prior = self._admin_state()
        self.reconcile_broker_inventory(startup=False, reason=reason, trigger="broker_reconnect")
        return self._write_admin_audit(
            "broker_reconnect_reconciliation",
            actor=actor,
            prior_state=prior,
            new_state=self._admin_state(),
            reason_codes=("weighted_voting.runtime.broker_reconnect.reconciled", reason),
            details={"lastReconciliation": self.metrics.last_reconciliation},
        )

    def runtime_control(self) -> dict[str, Any]:
        control = self._runtime_control()
        readiness = self.auto_paper_readiness(control=control)
        return {
            **control.as_dict(),
            "readiness": readiness.as_dict(),
        }

    def auto_paper_readiness(self, *, control: WeightedVotingRuntimeControl | None = None) -> WeightedVotingAutoPaperReadiness:
        return self._auto_paper_readiness(control=control or self._runtime_control())

    def update_runtime_control(
        self,
        *,
        paper_trading_enabled: bool,
        automatic_entries_enabled: bool | None = None,
        updated_by: str = "api",
        reason: str = "weighted_voting.runtime.api.control_update",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        prior = self._runtime_control()
        if expected_version is not None and int(expected_version) != int(prior.record_version):
            audit = self._write_runtime_control_audit(
                prior=prior,
                control=prior,
                readiness=self.auto_paper_readiness(control=prior).as_dict(),
                transition={
                    "status": "version_conflict",
                    "expectedVersion": expected_version,
                    "currentVersion": prior.record_version,
                    "mutationApplied": False,
                    "reasonCodes": ("weighted_voting.runtime.control.version_conflict",),
                },
            )
            return {
                **prior.as_dict(),
                "status": "version_conflict",
                "audit": audit,
                "expectedVersion": expected_version,
                "currentVersion": prior.record_version,
                "mutationApplied": False,
                "reasonCodes": ("weighted_voting.runtime.control.version_conflict",),
                "reason_codes": ("weighted_voting.runtime.control.version_conflict",),
            }
        requested_auto = bool(automatic_entries_enabled if automatic_entries_enabled is not None else paper_trading_enabled)
        updated_at = _now()
        readiness = self.auto_paper_readiness(control=prior)
        reason_codes = [
            "weighted_voting.runtime.control.paper_trading_requested_on"
            if paper_trading_enabled
            else "weighted_voting.runtime.control.paper_trading_requested_off",
            reason,
        ]
        transition: dict[str, Any] = {}
        if paper_trading_enabled:
            self.reconcile_broker_inventory(startup=False, reason=reason, trigger="before_enabling_automatic_entries")
            requested_control = WeightedVotingRuntimeControl(
                paper_trading_enabled=True,
                automatic_entries_enabled=requested_auto,
                record_version=prior.record_version + 1,
                updated_at=updated_at,
                updated_by=updated_by,
                reason=reason,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
            )
            readiness = self.auto_paper_readiness(control=requested_control)
            automatic_ready = requested_auto and readiness.entry_submission_allowed
            if automatic_ready:
                self.metrics.automatic_order_creation_paused = False
                self.metrics.pause_reason = None
                reason_codes.append("weighted_voting.runtime.control.automatic_entries_armed")
            else:
                self.metrics.automatic_order_creation_paused = True
                self.metrics.pause_reason = readiness.blocking_reason_codes[0] if readiness.blocking_reason_codes else "weighted_voting.runtime.control.automatic_entries_not_requested"
                reason_codes.extend(readiness.blocking_reason_codes or ("weighted_voting.runtime.control.automatic_entries_not_requested",))
            control = WeightedVotingRuntimeControl(
                paper_trading_enabled=True,
                automatic_entries_enabled=automatic_ready,
                record_version=prior.record_version + 1,
                updated_at=updated_at,
                updated_by=updated_by,
                reason=reason,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
            )
        else:
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = reason
            transition = self._disable_paper_entries(updated_at=updated_at, reason=reason)
            self.reconcile_broker_inventory(startup=False, reason=reason, trigger="paper_control_off")
            control = WeightedVotingRuntimeControl(
                paper_trading_enabled=False,
                automatic_entries_enabled=False,
                record_version=prior.record_version + 1,
                updated_at=updated_at,
                updated_by=updated_by,
                reason=reason,
                reason_codes=tuple(dict.fromkeys((*reason_codes, "weighted_voting.runtime.control.new_entries_blocked", "weighted_voting.runtime.control.risk_reducing_exits_remain_enabled"))),
            )
            readiness = self.auto_paper_readiness(control=control)
        self._persist_runtime_control(control)
        audit = self._write_runtime_control_audit(
            prior=prior,
            control=control,
            readiness=readiness.as_dict(),
            transition=transition,
        )
        self._write_status("control_updated", tuple(control.reason_codes))
        return {
            **control.as_dict(),
            "readiness": readiness.as_dict(),
            "audit": audit,
            "transition": transition,
        }

    def set_strategy_runtime_state(
        self,
        strategy_id: str,
        state: Literal["shadow", "disabled"],
        *,
        actor: str = "system",
        reason: str = "weighted_voting.runtime.admin.strategy_state_changed",
    ) -> dict[str, Any]:
        if state not in {"shadow", "disabled"}:
            raise ValueError("Weighted Voting runtime strategy control supports only shadow or disabled")
        strategy_id = strategy_id.upper()
        prior_state = _read_optional(self.store, f"{RUNTIME_STRATEGY_CONTROL_PREFIX}{strategy_id}") or {"strategyId": strategy_id, "runtimeState": None}
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "strategyId": strategy_id,
            "runtimeState": state,
            "updatedAt": _now().isoformat(),
            "updatedBy": actor,
            "reasonCodes": ("weighted_voting.runtime.strategy_control.updated", reason),
        }
        self.store.write_snapshot(f"{RUNTIME_STRATEGY_CONTROL_PREFIX}{strategy_id}", record)
        return self._write_admin_audit(
            "strategy_runtime_state",
            actor=actor,
            prior_state=prior_state,
            new_state=record,
            reason_codes=("weighted_voting.runtime.admin.strategy_state", reason),
        )

    def emergency_flatten(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.admin.emergency_flatten_requested") -> dict[str, Any]:
        prior = self._admin_state()
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = reason
        request = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": "requested",
            "centralRiskProcessRequired": True,
            "liveMoneyTradingEnabled": False,
            "requestedBy": actor,
            "requestedAt": _now().isoformat(),
            "reasonCodes": ("weighted_voting.runtime.emergency_flatten.central_risk_required", reason),
        }
        self.store.write_snapshot(f"{RUNTIME_EMERGENCY_FLATTEN_PREFIX}{_hash_payload(request)}", request)
        audit = self._write_admin_audit(
            "emergency_flatten",
            actor=actor,
            prior_state=prior,
            new_state={**self._admin_state(), "emergencyFlatten": request},
            reason_codes=("weighted_voting.runtime.admin.emergency_flatten", reason),
        )
        self._write_status("emergency_flatten_requested", ("weighted_voting.runtime.emergency_flatten.requested", reason))
        return audit

    def recover_from_checkpoints(self) -> None:
        self._recover_weighted_voting_local_paper_state()
        for symbol in self.config.symbols:
            checkpoint = _read_optional(self.store, _checkpoint_key(symbol))
            if checkpoint:
                self.metrics.last_checkpoint_by_symbol[symbol.upper()] = str(checkpoint.get("idempotency_key", ""))
                if checkpoint.get("finalised_candle_timestamp"):
                    self.metrics.last_event_timestamp_by_symbol[symbol.upper()] = str(checkpoint["finalised_candle_timestamp"])
        self.recover_pending_execution_outbox()
        self.perform_recovery_safety_check(reason="weighted_voting.runtime.recovery.checkpoints_scanned")
        self.restore_position_management()

    def _recover_weighted_voting_local_paper_state(self) -> dict[str, Any] | None:
        if self.paper_gateway is None or getattr(self.paper_gateway, "execution_mode", None) != "LOCAL_PAPER":
            return None
        broker = getattr(self.paper_gateway, "broker", None)
        if getattr(broker, "broker_kind", None) != "weighted_voting_local_paper":
            return None
        recovered_at = _now()
        snapshot = self.inventory_repository.recover_current_snapshot()
        local_orders = [
            payload
            for key, payload in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") and payload.get("algorithmId") == WEIGHTED_VOTING_ALGORITHM_ID
        ]
        local_fills = [
            payload
            for key, payload in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills.") and payload.get("algorithmId") == WEIGHTED_VOTING_ALGORITHM_ID
        ]
        local_protective = [
            payload
            for key, payload in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.") and payload.get("algorithmId") == WEIGHTED_VOTING_ALGORITHM_ID
        ]
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "executionMode": "LOCAL_PAPER",
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "inventorySnapshotVersion": snapshot.snapshot_version,
            "lastEventSequence": snapshot.last_event_sequence,
            "cash": snapshot.cash,
            "reservedCash": snapshot.reserved_cash,
            "buyingPower": snapshot.buying_power,
            "realizedPnl": snapshot.realized_pnl,
            "unrealizedPnl": snapshot.unrealized_pnl,
            "dailyRealizedPnl": snapshot.daily_realized_pnl,
            "dailyUnrealizedPnl": snapshot.daily_unrealized_pnl,
            "dailyLoss": snapshot.daily_loss,
            "dailyTradeCount": snapshot.daily_trade_count,
            "riskUsed": snapshot.risk_used,
            "riskRemaining": snapshot.risk_remaining,
            "positionCount": len(snapshot.open_positions),
            "pendingOrderCount": len(snapshot.pending_orders),
            "partialFillCount": len(snapshot.partially_filled_orders),
            "protectiveOrderCount": len(local_protective),
            "localOrderCount": len(local_orders),
            "localFillCount": len(local_fills),
            "orderIds": [str(order.get("clientOrderId") or "") for order in local_orders],
            "fillIds": [str(fill.get("fillId") or fill.get("clientOrderId") or "") for fill in local_fills],
            "processedFillIds": list(snapshot.processed_fill_ids),
            "lastPrice": snapshot.last_price,
            "pnlRecalculatedFromCurrentPrice": bool(snapshot.open_positions and snapshot.last_price),
            "protectiveOrderMonitoringResumed": bool(local_protective),
            "recoveredAt": recovered_at.isoformat(),
            "reasonCodes": (
                "weighted_voting.local_paper.restart_recovery.rebuilt_from_weighted_voting_inventory_events",
                "weighted_voting.local_paper.restart_recovery.loaded_local_orders_and_fills",
                "weighted_voting.local_paper.restart_recovery.no_alpaca_positions_queried",
            ),
        }
        self.store.write_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.recovery.latest", record)
        self.store.write_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.recovery.{_hash_payload(record)}", record)
        return record

    def recover_pending_execution_outbox(self) -> dict[str, Any]:
        recovered = 0
        reconciled = 0
        blocked = 0
        reason_codes = ["weighted_voting.runtime.execution_outbox.recovery_scanned"]
        for key, payload in _store_items(self.store):
            if not key.startswith(RUNTIME_EXECUTION_OUTBOX_PREFIX):
                continue
            status = str(payload.get("status") or "")
            if status not in WEIGHTED_VOTING_EXECUTION_OUTBOX_PENDING_STATES:
                continue
            item_payload = payload.get("executionQueueItem")
            if not isinstance(item_payload, dict):
                self._write_execution_outbox_from_payload(
                    payload,
                    status="RECONCILIATION_REQUIRED",
                    reason_codes=("weighted_voting.runtime.execution_outbox.recovery_missing_queue_item",),
                )
                blocked += 1
                continue
            try:
                item = weighted_voting_execution_queue_item_from_payload(item_payload)
            except Exception as exc:
                self._write_execution_outbox_from_payload(
                    payload,
                    status="RECONCILIATION_REQUIRED",
                    reason_codes=("weighted_voting.runtime.execution_outbox.recovery_queue_item_invalid", str(exc)),
                )
                blocked += 1
                continue
            if self._outbox_requires_broker_lookup_before_submit(payload):
                broker_state = self._broker_lookup_for_retry(item, reason_code="weighted_voting.runtime.execution_outbox.recovery_broker_lookup")
                if broker_state is not None:
                    self._write_execution_outbox_record(
                        item,
                        status=_outbox_status_from_broker_state(broker_state),
                        reason_codes=("weighted_voting.runtime.execution_outbox.recovered_from_broker_lookup",),
                        broker_lookup=broker_state,
                    )
                    reconciled += 1
                    continue
            try:
                self.execution_queue.put_nowait(item)
                self.metrics.enqueued_orders += 1
                self.metrics.execution_queue_depth = self.execution_queue.qsize()
                self._write_execution_outbox_record(
                    item,
                    status="READY_TO_SUBMIT",
                    reason_codes=("weighted_voting.runtime.execution_outbox.recovered_pending_intent_after_restart",),
                )
                recovered += 1
            except asyncio.QueueFull:
                self.metrics.rejected_execution_events += 1
                self.metrics.automatic_order_creation_paused = True
                self._write_execution_outbox_record(
                    item,
                    status="RECONCILIATION_REQUIRED",
                    reason_codes=("weighted_voting.runtime.execution_outbox.recovery_queue_full",),
                )
                blocked += 1
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "recoveredToQueue": recovered,
            "reconciledFromBroker": reconciled,
            "blocked": blocked,
            "queueDepth": self.execution_queue.qsize(),
            "recordedAt": _now().isoformat(),
            "reasonCodes": tuple(reason_codes),
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}recovery.{_hash_payload(record)}", record)
        return record

    def perform_recovery_safety_check(self, *, reason: str = "weighted_voting.runtime.recovery.safety_check") -> dict[str, Any]:
        reasons: list[str] = [reason]
        unresolved: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []
        restored: list[str] = []
        local_entry_risk_blocks: list[dict[str, Any]] = []
        now = _now()

        try:
            restored.extend(self._validate_or_restore_authoritative_snapshots(now=now, quarantined=quarantined, reasons=reasons))
            unresolved.extend(self._detect_unresolved_execution_crash_points())
            unresolved.extend(self._detect_unprotected_positions())
            if self.event_bus.depth() >= self.config.queue_maxsize:
                unresolved.append({"boundary": "event_backlog", "reasonCode": "weighted_voting.runtime.recovery.event_backlog"})
            if self.metrics.queue_lag_seconds is not None and self.metrics.queue_lag_seconds > self.config.max_queue_lag_seconds:
                unresolved.append({"boundary": "queue_lag", "reasonCode": "weighted_voting.runtime.circuit_breaker.queue_lag_exceeded", "queueLagSeconds": self.metrics.queue_lag_seconds})
            last_received = self.metrics.last_finalised_bar_received if isinstance(self.metrics.last_finalised_bar_received, dict) else {}
            freshness = _optional_float(_first_present(last_received.get("dataFreshnessSeconds"), last_received.get("data_freshness_seconds")))
            if freshness is not None and freshness > self.config.max_queue_lag_seconds:
                unresolved.append({"boundary": "market_data_stale", "reasonCode": "weighted_voting.runtime.circuit_breaker.market_data_stale", "dataFreshnessSeconds": freshness})
            if self.metrics.finalized_bar_event_gaps > self.config.finalized_bar_gap_tolerance:
                unresolved.append({"boundary": "finalized_bar_sequence_gap", "reasonCode": "weighted_voting.runtime.circuit_breaker.finalized_bar_gap_tolerance_exceeded", "gapCount": self.metrics.finalized_bar_event_gaps})
            snapshot = self.inventory_repository.current_snapshot(now=now)
            if float(snapshot.daily_loss_percent or 0.0) >= float(self.weighted_config.maximum_weighted_daily_loss_percent):
                local_entry_risk_blocks.append({"boundary": "daily_loss_limit", "reasonCode": "weighted_voting.runtime.control.daily_loss_limit_reached", "dailyLossPercent": snapshot.daily_loss_percent})
            if int(snapshot.daily_trade_count or 0) >= int(self.weighted_config.maximum_weighted_daily_trades):
                local_entry_risk_blocks.append({"boundary": "daily_trade_limit", "reasonCode": "weighted_voting.runtime.control.daily_trade_limit_reached", "dailyTradeCount": snapshot.daily_trade_count})
            for worker_id, failures in self.metrics.worker_failures.items():
                if failures >= self.config.worker_restart_failure_threshold:
                    unresolved.append({"boundary": "worker_crash_threshold", "workerId": worker_id, "failures": failures, "reasonCode": "weighted_voting.runtime.circuit_breaker.repeated_worker_crashes"})
        except Exception as exc:
            reasons.append("weighted_voting.runtime.recovery.persistence_or_validation_outage")
            unresolved.append({"boundary": "persistence_outage", "error": str(exc), "reasonCode": "weighted_voting.runtime.recovery.persistence_outage"})
            self.metrics.last_error = f"WeightedVotingRecovery: {exc}"

        recovery_required = bool(unresolved or quarantined)
        self.metrics.recovery_required = recovery_required
        self.metrics.quarantined_snapshots += len(quarantined)
        if recovery_required:
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.recovery.unresolved_blocks_new_entries"
        elif local_entry_risk_blocks:
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = str(local_entry_risk_blocks[0]["reasonCode"])
            self.metrics.risk_reducing_exits_allowed = True
        state = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "recoveryRequired": recovery_required,
            "newEntriesBlocked": recovery_required or self.metrics.automatic_order_creation_paused or bool(local_entry_risk_blocks),
            "protectiveExitsMayContinue": True,
            "unresolvedBoundaries": unresolved,
            "localEntryRiskBoundaries": local_entry_risk_blocks,
            "quarantinedSnapshots": quarantined,
            "restoredAuthoritativeSnapshots": restored,
            "checkedAt": now.isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reasons)),
        }
        self.metrics.recovery_state = state
        self._write_recovery_state(state)
        return state

    def healthy_state_check(self, *, actor: str = "system", reason: str = "weighted_voting.runtime.recovery.healthy_check") -> dict[str, Any]:
        state = self.perform_recovery_safety_check(reason=reason)
        healthy = (
            not state["recoveryRequired"]
            and self.metrics.inventory_reconciled
            and not self.metrics.entry_creation_paused_for_reconciliation
            and not self.metrics.last_error
        )
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "healthy": healthy,
            "actor": actor,
            "checkedAt": _now().isoformat(),
            "recoveryStateHash": _hash_payload(state),
            "inventoryReconciled": self.metrics.inventory_reconciled,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "reasonCodes": (
                "weighted_voting.runtime.recovery.healthy_state_ready"
                if healthy
                else "weighted_voting.runtime.recovery.healthy_state_rejected"
            ),
        }
        try:
            self.store.write_snapshot(RUNTIME_HEALTHY_STATE_KEY, record)
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingRecoveryHealthyCheck: {exc}"
            record["healthy"] = False
            record["reasonCodes"] = ("weighted_voting.runtime.recovery.healthy_state_persistence_failed",)
        return record

    def _validate_or_restore_authoritative_snapshots(
        self,
        *,
        now: datetime,
        quarantined: list[dict[str, Any]],
        reasons: list[str],
    ) -> list[str]:
        restored: list[str] = []
        settings = _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY)
        if settings is not None:
            try:
                WeightedEffectiveSettings.model_validate(settings)
                if not (settings.get("configuration_hash") or settings.get("configurationHash")):
                    raise ValueError("missing Weighted Voting settings hash")
            except Exception as exc:
                self._quarantine_snapshot(WEIGHTED_VOTING_SETTINGS_KEY, settings, "weighted_voting.runtime.recovery.settings_corruption", str(exc), now=now, quarantined=quarantined)
                approved = _read_optional(self.store, LAST_APPROVED_SETTINGS_KEY)
                if approved is not None:
                    WeightedEffectiveSettings.model_validate(approved)
                    if not (approved.get("configuration_hash") or approved.get("configurationHash")):
                        raise ValueError("last approved Weighted Voting settings hash missing")
                    self.store.write_snapshot(WEIGHTED_VOTING_SETTINGS_KEY, approved)
                    restored.append(WEIGHTED_VOTING_SETTINGS_KEY)
                reasons.append("weighted_voting.runtime.recovery.settings_corruption_quarantined")

        weights = _read_optional(self.store, ACTIVE_WEIGHT_STATE_KEY)
        if weights is not None:
            try:
                WeightedWeightState.model_validate(weights)
                if not (weights.get("output_hash") or weights.get("outputHash") or weights.get("input_data_hash") or weights.get("inputDataHash")):
                    raise ValueError("missing Weight Voting weight-state hash evidence")
            except Exception as exc:
                self._quarantine_snapshot(ACTIVE_WEIGHT_STATE_KEY, weights, "weighted_voting.runtime.recovery.weight_state_corruption", str(exc), now=now, quarantined=quarantined)
                approved = _read_optional(self.store, LAST_APPROVED_WEIGHT_STATE_KEY)
                if approved is not None:
                    WeightedWeightState.model_validate(approved)
                    if not (approved.get("output_hash") or approved.get("outputHash") or approved.get("input_data_hash") or approved.get("inputDataHash")):
                        raise ValueError("last approved Weighted Voting weight-state hash evidence missing")
                    self.store.write_snapshot(ACTIVE_WEIGHT_STATE_KEY, approved)
                    restored.append(ACTIVE_WEIGHT_STATE_KEY)
                reasons.append("weighted_voting.runtime.recovery.weight_state_corruption_quarantined")

        inventory = _read_optional(self.store, CURRENT_SNAPSHOT_KEY)
        if inventory is not None:
            try:
                self.inventory_repository.current_snapshot(now=now)
            except Exception as exc:
                self._quarantine_snapshot(CURRENT_SNAPSHOT_KEY, inventory, "weighted_voting.runtime.recovery.inventory_snapshot_corruption", str(exc), now=now, quarantined=quarantined)
                reasons.append("weighted_voting.runtime.recovery.inventory_corruption_quarantined")
        return restored

    def _detect_unresolved_execution_crash_points(self) -> list[dict[str, Any]]:
        unresolved: list[dict[str, Any]] = []
        automatic_results = {
            key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
            for key, _ in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
        }
        reconciliations = {
            key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.reconciliation.")
            for key, _ in _store_items(self.store)
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.reconciliation.")
        }
        for key, payload in _store_items(self.store):
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue."):
                client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or key.rsplit(".", 1)[-1])
                status = str(payload.get("status") or "PENDING")
                if client_order_id not in automatic_results:
                    unresolved.append(_unresolved("risk_approval_before_broker_submission", key, status, "weighted_voting.runtime.recovery.execution_queue_unresolved"))
            elif key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.") and key.endswith(".latest"):
                client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
                status = str(payload.get("status") or "")
                if status in {"PENDING", "PENDING_SUBMISSION", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"} and client_order_id not in automatic_results and client_order_id not in reconciliations:
                    unresolved.append(_unresolved("submission_or_acknowledgement_incomplete", key, status, "weighted_voting.runtime.recovery.lifecycle_unresolved"))
            elif key.startswith(RUNTIME_ORDER_INTENT_PREFIX):
                order_intent_id = str(payload.get("orderIntentId") or payload.get("order_intent_id") or key.rsplit(".", 1)[-1])
                status = str(payload.get("status") or "")
                if status in {"PENDING_GLOBAL_RISK", "CREATED", "INTENT_CREATED"} and _read_optional(self.store, f"{RUNTIME_RISK_PREFIX}decisions.{order_intent_id}") is None:
                    unresolved.append(_unresolved("intent_before_global_risk_response", key, status, "weighted_voting.runtime.recovery.intent_without_global_risk_response"))
            elif key.startswith("weighted_voting.decisions."):
                decision_id = str(payload.get("decision_id") or payload.get("decisionId") or key.rsplit(".", 1)[-1])
                if not any(isinstance(item, dict) and decision_id in str(item) for _, item in _store_items(self.store) if _.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.")):
                    unresolved.append(_unresolved("decision_before_risk_response", key, "DECISION_PERSISTED", "weighted_voting.runtime.recovery.decision_without_risk_evidence"))
        for key, payload in _store_items(self.store):
            if key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result."):
                client_order_id = key.removeprefix(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.")
                fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else None
                if fill and int(fill.get("filledQuantity") or 0) > 0 and client_order_id not in reconciliations:
                    unresolved.append(_unresolved("fill_before_inventory_update", key, str(payload.get("status") or "FILLED"), "weighted_voting.runtime.recovery.fill_requires_inventory_reconciliation"))
        return _dedupe_unresolved(unresolved)

    def _detect_unprotected_positions(self) -> list[dict[str, Any]]:
        try:
            snapshot = self.inventory_repository.current_snapshot(now=_now())
        except Exception as exc:
            return [_unresolved("inventory_version_conflict", CURRENT_SNAPSHOT_KEY, "CORRUPT", "weighted_voting.runtime.recovery.inventory_unavailable", error=str(exc))]
        unresolved: list[dict[str, Any]] = []
        for position in snapshot.open_positions:
            protection_key = f"weighted_voting.position_manager.protection.{position.client_order_id}"
            if _read_optional(self.store, protection_key) is None:
                unresolved.append(_unresolved("protective_orders_being_created", protection_key, "MISSING", "weighted_voting.runtime.recovery.protective_order_restore_required"))
        return unresolved

    def _quarantine_snapshot(
        self,
        key: str,
        payload: dict[str, Any],
        reason_code: str,
        error: str,
        *,
        now: datetime,
        quarantined: list[dict[str, Any]],
    ) -> None:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "originalKey": key,
            "payload": _json_ready(payload),
            "error": error,
            "quarantinedAt": now.isoformat(),
            "reasonCodes": (reason_code,),
        }
        self.store.write_snapshot(f"{RUNTIME_QUARANTINE_PREFIX}{_hash_payload({'key': key, 'payload': payload, 'at': now.isoformat()})}", record)
        quarantined.append({"key": key, "reasonCode": reason_code, "error": error})

    def _write_recovery_state(self, state: dict[str, Any]) -> None:
        try:
            self.store.write_snapshot(RUNTIME_RECOVERY_STATE_KEY, state)
            self.store.write_snapshot(f"{RUNTIME_RECOVERY_EVENT_PREFIX}{_hash_payload(state)}", state)
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingRecoveryPersistence: {exc}"
            self.metrics.automatic_order_creation_paused = True
            self.metrics.recovery_required = True

    def write_heartbeat(self) -> None:
        payload = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "recorded_at": _now().isoformat(),
            "queue_depth": self.event_bus.depth(),
            "workers": sorted(self.tasks),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "paused": self.metrics.paused,
            "reason_codes": ("weighted_voting.runtime.heartbeat",),
        }
        self.store.write_snapshot(RUNTIME_HEARTBEAT_KEY, payload)

    def health(self) -> dict[str, Any]:
        self.metrics.queue_depth = self.event_bus.depth()
        self.metrics.risk_queue_depth = self.risk_queue.qsize()
        self.metrics.execution_queue_depth = self.execution_queue.qsize()
        inventory = None
        inventory_error = None
        try:
            inventory = self.inventory_repository.current_snapshot(now=_now())
        except Exception as exc:
            inventory_error = str(exc)
        active_weight = _read_optional(self.store, ACTIVE_WEIGHT_STATE_KEY) or {}
        settings = _read_optional(self.store, WEIGHTED_VOTING_SETTINGS_KEY) or {}
        lifecycle = _read_optional(self.store, WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY) or {}
        broker_connectivity = self._paper_broker_connectivity_status()
        account_mode = self._paper_account_mode_status()
        protective_health = self._protective_order_health(inventory)
        execution_mode = str(getattr(self.paper_gateway, "execution_mode", self.config.paper_execution_mode))
        broker_kind = str(getattr(getattr(self.paper_gateway, "broker", None), "broker_kind", "unavailable"))
        alpaca_dependency = execution_mode != "LOCAL_PAPER" or broker_kind != "weighted_voting_local_paper"
        local_inventory_status = _runtime_inventory_status(inventory=inventory, error=inventory_error)
        current_position = _json_ready(asdict(inventory.open_positions[0])) if inventory and inventory.open_positions else None
        control = self._runtime_control()
        self._evaluate_circuit_breaker_conditions(
            inventory=inventory,
            broker_connectivity=broker_connectivity,
            account_mode=account_mode,
            protective_health=protective_health,
            control=control,
        )
        operational_status = {
            "supervisorState": "paused" if self.metrics.paused else ("running" if self.metrics.supervisor_started else "stopped"),
            "paperToggleState": control.as_dict(),
            "workerState": self._worker_state(),
            "queueDepth": self.metrics.queue_depth,
            "riskQueueDepth": self.metrics.risk_queue_depth,
            "executionQueueDepth": self.metrics.execution_queue_depth,
            "lastFinalisedBarReceived": self.metrics.last_finalised_bar_received,
            "lastFinalizedBarProducerResult": self.metrics.last_finalized_bar_producer_result,
            "lastBarProcessed": self.metrics.last_bar_processed,
            "processingLagSeconds": self.metrics.processing_lag_seconds,
            "queueLagSeconds": self.metrics.queue_lag_seconds,
            "paperBrokerConnectivity": broker_connectivity,
            "accountModeVerification": account_mode,
            "executionMode": execution_mode,
            "brokerKind": broker_kind,
            "alpacaDependency": alpaca_dependency,
            "inventory": local_inventory_status,
            "lastDecision": self.metrics.last_decision or {"decisionId": self.metrics.last_decision_id},
            "lastLocalGateResult": self.metrics.last_local_gate_result,
            "lastAcceptedProposal": self.metrics.last_accepted_proposal,
            "lastGlobalRiskResponse": self.metrics.last_global_risk_response,
            "lastIntent": self.metrics.last_order_intent,
            "lastOrderSubmission": self.metrics.last_order_submission,
            "lastSubmission": self.metrics.last_order_submission,
            "lastAcknowledgement": self.metrics.last_acknowledgement,
            "lastFill": self.metrics.last_fill,
            "lastReconciliation": self.metrics.last_reconciliation,
            "openPositions": [_json_ready(asdict(position)) for position in inventory.open_positions] if inventory else [],
            "currentPosition": current_position,
            "pendingOrders": [_json_ready(asdict(order)) for order in inventory.pending_orders] if inventory else [],
            "protectiveOrderHealth": protective_health,
            "inventoryVersion": inventory.inventory_version if inventory else None,
            "inventorySnapshotVersion": inventory.snapshot_version if inventory else None,
            "settingsVersion": settings.get("settings_version") or settings.get("settingsVersion"),
            "weightVersion": active_weight.get("weight_version") or active_weight.get("weightVersion"),
            "catalogueVersion": lifecycle.get("catalog_version") or lifecycle.get("catalogVersion"),
            "dailyTradeCount": inventory.daily_trade_count if inventory else None,
            "dailyPnL": round(inventory.daily_realised_pnl + inventory.daily_unrealised_pnl, 10) if inventory else None,
            "dailyLoss": inventory.daily_loss if inventory else None,
            "dailyLossPercent": inventory.daily_loss_percent if inventory else None,
            "remainingDailyRisk": inventory.remaining_daily_risk if inventory else None,
            "automaticSubmissionRolloutState": _rollout_state(self.rollout_flags, self.rollout_validation, self.store),
            "pauseReason": self.metrics.pause_reason,
            "lastError": self.metrics.last_error or inventory_error,
            "recoveryRequired": self.metrics.recovery_required,
            "recoveryState": dict(self.metrics.recovery_state),
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "circuitBreakerState": self.metrics.last_circuit_breaker,
        }
        readiness = self.auto_paper_readiness(control=control)
        operational_status["readinessState"] = readiness.as_dict()
        runtime_metrics = {
            "decisionLatencyMs": self.metrics.decision_latency_ms,
            "riskServiceLatencyMs": self.metrics.risk_service_latency_ms,
            "brokerLatencyMs": self.metrics.broker_latency_ms,
            "decisionRiskBrokerLatencyMs": {
                "decision": self.metrics.decision_latency_ms,
                "risk": self.metrics.risk_service_latency_ms,
                "broker": self.metrics.broker_latency_ms,
            },
            "eventBacklog": self.metrics.queue_depth,
            "riskQueueDepth": self.metrics.risk_queue_depth,
            "queueLagSeconds": self.metrics.queue_lag_seconds,
            "finalizedBarEventsPublished": self.metrics.finalized_bar_events_published,
            "finalizedBarEventGaps": self.metrics.finalized_bar_event_gaps,
            "lastFinalizedBarProducerResult": self.metrics.last_finalized_bar_producer_result,
            "staleEventDrops": self.metrics.stale_events,
            "duplicateEventDrops": self.metrics.duplicate_events,
            "gateRejectionCounts": dict(self.metrics.gate_rejection_counts),
            "strategyOpportunityCounts": dict(self.metrics.strategy_opportunity_counts),
            "strategySignalCounts": _copy_nested_counts(self.metrics.strategy_signal_counts),
            "proposedVsAllowedQuantity": dict(self.metrics.proposed_vs_allowed_quantity),
            "fillQuality": dict(self.metrics.fill_quality),
            "slippage": dict(self.metrics.slippage),
            "reconciliationDiscrepancies": self.metrics.reconciliation_discrepancies,
            "quarantinedSnapshots": self.metrics.quarantined_snapshots,
            "recoveryRequired": self.metrics.recovery_required,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "circuitBreakerState": self.metrics.last_circuit_breaker,
            "workerRestarts": dict(self.metrics.worker_restarts),
        }
        payload = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "executionMode": execution_mode,
            "brokerKind": broker_kind,
            "alpacaDependency": alpaca_dependency,
            "inventory": local_inventory_status,
            "started": self.metrics.supervisor_started,
            "paused": self.metrics.paused,
            "automaticOrderCreationPaused": self.metrics.automatic_order_creation_paused,
            "queueDepth": self.metrics.queue_depth,
            "riskQueueDepth": self.metrics.risk_queue_depth,
            "executionQueueDepth": self.metrics.execution_queue_depth,
            "queueMaxsize": self.config.queue_maxsize,
            "droppedEvents": self.event_bus.dropped_events,
            "processedEvents": self.metrics.processed_events,
            "duplicateEvents": self.metrics.duplicate_events,
            "rejectedEvents": self.metrics.rejected_events,
            "staleEvents": self.metrics.stale_events,
            "outOfOrderEvents": self.metrics.out_of_order_events,
            "persistedDecisions": self.metrics.persisted_decisions,
            "enqueuedOrders": self.metrics.enqueued_orders,
            "submittedOrders": self.metrics.submitted_orders,
            "rejectedExecutionEvents": self.metrics.rejected_execution_events,
            "entryCreationPausedForReconciliation": self.metrics.entry_creation_paused_for_reconciliation,
            "inventoryReconciled": self.metrics.inventory_reconciled,
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "workerFailures": dict(self.metrics.worker_failures),
            "workerRestarts": dict(self.metrics.worker_restarts),
            "workers": {worker_id: not task.done() for worker_id, task in self.tasks.items()},
            "lastEventTimestampBySymbol": dict(self.metrics.last_event_timestamp_by_symbol),
            "lastCheckpointBySymbol": dict(self.metrics.last_checkpoint_by_symbol),
            "lastDecisionId": self.metrics.last_decision_id,
            "lastError": self.metrics.last_error,
            "recoveryRequired": self.metrics.recovery_required,
            "circuitBreakerOpen": self.metrics.circuit_breaker_open,
            "circuitBreakerState": self.metrics.last_circuit_breaker,
            "operationalStatus": operational_status,
            "runtimeControl": control.as_dict(),
            "autoPaperReadiness": readiness.as_dict(),
            "runtimeStatus": readiness.runtime_status,
            "metrics": runtime_metrics,
            "reasonCodes": ("weighted_voting.runtime.health.ready" if readiness.ready else "weighted_voting.runtime.health.not_ready",),
        }
        sanitized = _sanitize_for_observability(payload)
        self._persist_runtime_observability(sanitized)
        return sanitized

    def _paper_broker_connectivity_status(self) -> dict[str, Any]:
        connected = bool(self.paper_gateway is not None)
        try:
            endpoint_verified = bool(self.paper_gateway is not None and _verify_weighted_voting_paper_endpoint(self.paper_gateway))
        except Exception:
            endpoint_verified = False
        return {
            "connected": connected,
            "endpointIsPaper": endpoint_verified,
            "liveTradingRejected": not endpoint_verified if connected else False,
            "reasonCodes": (
                "weighted_voting.observability.paper_broker_connected",
            )
            if connected and endpoint_verified
            else ("weighted_voting.observability.paper_broker_unavailable_or_unverified",),
        }

    def _paper_account_mode_status(self) -> dict[str, Any]:
        if self.paper_gateway is None:
            return {
                "verified": False,
                "mode": "UNKNOWN",
                "paperOnly": False,
                "reasonCodes": ("weighted_voting.observability.paper_account_gateway_missing",),
            }
        verifier = getattr(self.paper_gateway.broker, "verify_paper_account", None)
        try:
            verified = bool(callable(verifier) and verifier())
        except Exception:
            verified = False
        return {
            "verified": verified,
            "mode": "PAPER" if verified else "UNKNOWN",
            "paperOnly": verified,
            "reasonCodes": (
                "weighted_voting.observability.paper_account_verified",
            )
            if verified
            else ("weighted_voting.observability.paper_account_unverified",),
        }

    def _protective_order_health(self, inventory: Any) -> dict[str, Any]:
        try:
            unprotected = self._detect_unprotected_positions()
        except Exception as exc:
            return {
                "healthy": False,
                "unprotectedPositionCount": None,
                "protectiveMismatchCount": None,
                "error": str(exc),
                "reasonCodes": ("weighted_voting.observability.protective_order_health_unavailable",),
            }
        last_reconciliation = self.metrics.last_reconciliation if isinstance(self.metrics.last_reconciliation, dict) else {}
        discrepancies = last_reconciliation.get("discrepancies") if isinstance(last_reconciliation.get("discrepancies"), list) else []
        protective_mismatches = [
            discrepancy
            for discrepancy in discrepancies
            if isinstance(discrepancy, dict)
            and (
                "protective" in str(discrepancy.get("kind") or discrepancy.get("reasonCode") or discrepancy.get("reasonCodes") or "").lower()
                or bool(_deep_get(discrepancy, ("details", "riskReductionPriority")))
            )
        ]
        open_count = len(inventory.open_positions) if inventory is not None else None
        healthy = len(unprotected) == 0 and len(protective_mismatches) == 0
        return {
            "healthy": healthy,
            "openPositionCount": open_count,
            "unprotectedPositionCount": len(unprotected),
            "protectiveMismatchCount": len(protective_mismatches),
            "riskReductionPriority": bool(protective_mismatches),
            "reasonCodes": (
                "weighted_voting.observability.protective_orders_healthy",
            )
            if healthy
            else ("weighted_voting.observability.protective_order_attention_required",),
        }

    def _trip_circuit_breaker(
        self,
        reason_code: str,
        *,
        trigger: str,
        details: dict[str, Any] | None = None,
        broker_available: bool = True,
    ) -> dict[str, Any]:
        prior = self._admin_state()
        now = _now()
        self.metrics.circuit_breaker_open = True
        self.metrics.automatic_order_creation_paused = True
        self.metrics.recovery_required = True
        self.metrics.pause_reason = reason_code
        if not broker_available:
            self.metrics.risk_reducing_exits_allowed = False
        record = _sanitize_for_observability(
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "status": "OPEN",
                "newEntriesBlocked": True,
                "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                "protectiveExitsBlockedByBrokerUnavailable": not broker_available,
                "requiresHealthyStateCheck": True,
                "requiresAuditedAdministratorAction": True,
                "trigger": trigger,
                "details": details or {},
                "openedAt": now.isoformat(),
                "reasonCodes": (reason_code,),
            }
        )
        self.metrics.last_circuit_breaker = record
        self.metrics.recovery_state = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "recoveryRequired": True,
            "newEntriesBlocked": True,
            "protectiveExitsMayContinue": broker_available,
            "unresolvedBoundaries": [
                {
                    "boundary": trigger,
                    "reasonCode": reason_code,
                    "details": details or {},
                }
            ],
            "checkedAt": now.isoformat(),
            "reasonCodes": (reason_code,),
        }
        try:
            self.store.write_snapshot(RUNTIME_CIRCUIT_BREAKER_KEY, record)
            self.store.write_snapshot(f"{RUNTIME_CIRCUIT_BREAKER_PREFIX}{now.isoformat()}.{_hash_payload(record)}", record)
            self.store.write_snapshot(RUNTIME_RECOVERY_STATE_KEY, self.metrics.recovery_state)
            self._write_admin_audit(
                "circuit_breaker_opened",
                actor="weighted_voting.runtime",
                prior_state=prior,
                new_state=self._admin_state(),
                reason_codes=("weighted_voting.runtime.circuit_breaker.opened", reason_code),
                details=record,
            )
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingCircuitBreakerPersistence: {exc}"
        return record

    def _close_circuit_breaker(self, *, actor: str, reason: str, healthy_state: dict[str, Any]) -> dict[str, Any]:
        prior = self._admin_state()
        now = _now()
        self.metrics.circuit_breaker_open = False
        self.metrics.recovery_required = False
        self.metrics.risk_reducing_exits_allowed = True
        self.metrics.last_error = None
        record = _sanitize_for_observability(
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "status": "CLOSED",
                "newEntriesBlocked": self.metrics.automatic_order_creation_paused,
                "riskReducingExitsAllowed": True,
                "closedBy": actor,
                "closedAt": now.isoformat(),
                "healthyStateHash": _hash_payload(healthy_state),
                "reasonCodes": ("weighted_voting.runtime.circuit_breaker.closed_after_healthy_state_check", reason),
            }
        )
        self.metrics.last_circuit_breaker = record
        try:
            self.store.write_snapshot(RUNTIME_CIRCUIT_BREAKER_KEY, record)
            self.store.write_snapshot(f"{RUNTIME_CIRCUIT_BREAKER_PREFIX}{now.isoformat()}.{_hash_payload(record)}", record)
            self._write_admin_audit(
                "circuit_breaker_closed",
                actor=actor,
                prior_state=prior,
                new_state=self._admin_state(),
                reason_codes=("weighted_voting.runtime.admin.circuit_breaker_closed", reason),
                details=record,
            )
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingCircuitBreakerClosePersistence: {exc}"
            record["status"] = "CLOSE_PERSISTENCE_FAILED"
        return record

    def _evaluate_circuit_breaker_conditions(
        self,
        *,
        inventory: Any,
        broker_connectivity: dict[str, Any],
        account_mode: dict[str, Any],
        protective_health: dict[str, Any],
        control: WeightedVotingRuntimeControl,
    ) -> None:
        if self.metrics.circuit_breaker_open:
            return
        if self.metrics.queue_lag_seconds is not None and self.metrics.queue_lag_seconds > self.config.max_queue_lag_seconds:
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.queue_lag_exceeded",
                trigger="queue_lag",
                details={"queueLagSeconds": self.metrics.queue_lag_seconds, "maxQueueLagSeconds": self.config.max_queue_lag_seconds},
            )
            return
        if self.metrics.finalized_bar_event_gaps > self.config.finalized_bar_gap_tolerance:
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.finalized_bar_gap_tolerance_exceeded",
                trigger="finalized_bar_sequence_gap",
                details={"gapCount": self.metrics.finalized_bar_event_gaps, "tolerance": self.config.finalized_bar_gap_tolerance},
            )
            return
        if inventory is not None and float(inventory.daily_loss_percent or 0.0) >= float(self.weighted_config.maximum_weighted_daily_loss_percent):
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.control.daily_loss_limit_reached"
            self.metrics.risk_reducing_exits_allowed = True
        if inventory is not None and int(inventory.daily_trade_count or 0) >= int(self.weighted_config.maximum_weighted_daily_trades):
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.control.daily_trade_limit_reached"
            self.metrics.risk_reducing_exits_allowed = True
        if protective_health.get("healthy") is False and _safe_int(protective_health.get("unprotectedPositionCount")) > 0:
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.unprotected_position_exists",
                trigger="unprotected_position",
                details=protective_health,
            )
            return
        if control.paper_trading_enabled or control.automatic_entries_enabled:
            if not broker_connectivity.get("connected") or not broker_connectivity.get("endpointIsPaper"):
                self._trip_circuit_breaker(
                    "weighted_voting.runtime.circuit_breaker.broker_disconnected_or_unverified",
                    trigger="broker_connectivity",
                    details=broker_connectivity,
                    broker_available=False,
                )
                return
            if not account_mode.get("verified"):
                self._trip_circuit_breaker(
                    "weighted_voting.runtime.circuit_breaker.paper_account_unverified",
                    trigger="paper_account_verification",
                    details=account_mode,
                    broker_available=True,
                )

    def _persist_runtime_observability(self, health: dict[str, Any]) -> None:
        try:
            snapshot = {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "observabilityVersion": "weighted_voting_runtime_observability_v1",
                "recordedAt": _now().isoformat(),
                "health": health,
                "secretRedaction": {
                    "enabled": True,
                    "forbiddenFields": tuple(sorted(SECRET_FIELD_MARKERS)),
                },
                "reasonCodes": ("weighted_voting.observability.runtime_snapshot_persisted",),
            }
            self.store.write_snapshot(RUNTIME_OBSERVABILITY_KEY, snapshot)
            self.store.write_snapshot(f"{RUNTIME_OBSERVABILITY_PREFIX}{_hash_payload(snapshot)}", snapshot)
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingRuntimeObservability: {exc}"

    def _process_finalised_bar_event_locked(self, event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
        if self.metrics.paused:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "paused", None, ("weighted_voting.runtime.paused",))
        self.metrics.last_finalised_bar_received = _bar_summary(event)
        snapshot = build_weighted_voting_market_snapshot(event.market_payload)
        if snapshot.symbol.upper() != event.symbol.upper() or snapshot.data_timestamp != event.finalised_candle_timestamp:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_conflicting_event", None, ("weighted_voting.runtime.conflicting_event_payload",))
        degradation_reasons = _event_degradation_reasons(event, snapshot, max_lag_seconds=self.config.max_queue_lag_seconds)
        if degradation_reasons:
            self.metrics.rejected_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.degradation.new_entries_blocked"
            if any("clock_skew" in code for code in degradation_reasons):
                self.metrics.last_error = "WeightedVotingRuntime: clock skew detected"
            trigger = "clock_skew" if any("clock_skew" in code for code in degradation_reasons) else "stale_market_data"
            reason_code = (
                "weighted_voting.runtime.circuit_breaker.clock_skew_exceeded"
                if trigger == "clock_skew"
                else "weighted_voting.runtime.circuit_breaker.market_data_stale"
            )
            self._trip_circuit_breaker(
                reason_code,
                trigger=trigger,
                details={"eventId": event.event_id, "degradationReasonCodes": degradation_reasons},
            )
            status = "safe_degradation_no_order"
            record = self._write_event_record(event, status, None, tuple(degradation_reasons))
            return record
        session_evidence = self._authoritative_session_evidence(snapshot.data_timestamp)
        if not session_evidence["sessionAllowed"] and not event.replay_recovery:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "closed_session_skipped", None, tuple(session_evidence["reasonCodes"]))
        checkpoint = _read_optional(self.store, _checkpoint_key(event.symbol))
        last_timestamp = _parse_optional_datetime(checkpoint.get("finalised_candle_timestamp") if checkpoint else None)
        if last_timestamp and snapshot.data_timestamp < last_timestamp and not event.replay_recovery:
            self.metrics.out_of_order_events += 1
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_out_of_order", None, ("weighted_voting.runtime.out_of_order_event_rejected",))
        if last_timestamp and snapshot.data_timestamp == last_timestamp and checkpoint and checkpoint.get("data_manifest_hash") != event.data_manifest_hash and not event.replay_recovery:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_conflicting_revision", None, ("weighted_voting.runtime.conflicting_revision_rejected",))
        market_event_id = weighted_voting_market_event_id(
            symbol=event.symbol,
            bar_end=event.bar_end or snapshot.data_timestamp + timedelta(minutes=1),
            source=event.data_source,
            source_sequence=int(event.source_sequence or 0),
        )
        if event.event_id != market_event_id and not event.replay_recovery:
            self.metrics.rejected_events += 1
            return self._write_event_record(event, "rejected_market_event_identity_mismatch", None, ("weighted_voting.runtime.market_event_identity_mismatch",))
        if _read_optional(self.store, _event_key(market_event_id)):
            self.metrics.duplicate_events += 1
            return self._write_duplicate_event_record(event, market_event_id, ("weighted_voting.runtime.duplicate_event_noop",))
        previous_sequence = _safe_int(checkpoint.get("source_sequence")) if checkpoint and checkpoint.get("source_sequence") is not None else None
        if previous_sequence is not None and event.source_sequence is not None and event.source_sequence != previous_sequence + 1 and not event.replay_recovery:
            self.metrics.rejected_events += 1
            self.metrics.finalized_bar_event_gaps += 1
            if self.metrics.finalized_bar_event_gaps > self.config.finalized_bar_gap_tolerance:
                self._trip_circuit_breaker(
                    "weighted_voting.runtime.circuit_breaker.finalized_bar_gap_tolerance_exceeded",
                    trigger="finalized_bar_sequence_gap",
                    details={"previousSequence": previous_sequence, "receivedSequence": event.source_sequence, "gapCount": self.metrics.finalized_bar_event_gaps, "tolerance": self.config.finalized_bar_gap_tolerance},
                )
            return self._write_event_record(event, "rejected_source_sequence_gap", None, ("weighted_voting.runtime.source_sequence_gap_rejected",))
        self.store.write_snapshot(
            _event_key(market_event_id),
            {
                **event.as_dict(),
                "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "status": "claimed_before_decision",
                "market_event_id": market_event_id,
                "marketEventId": market_event_id,
                "claimed_at": _now().isoformat(),
                "reason_codes": ("weighted_voting.runtime.market_event_idempotency_claimed",),
            },
        )
        self._mark_inventory_from_market_snapshot(snapshot, market_event_id=market_event_id)
        weight_state = self.service.active_weight_state()
        condition = classify_market_condition(snapshot, config=self.weighted_config)
        effective = self._active_effective_settings()
        queue_lag = max(0.0, (_now() - event.published_at).total_seconds())
        self.metrics.processing_lag_seconds = queue_lag
        if queue_lag > self.config.max_queue_lag_seconds:
            self.metrics.stale_events += 1
            self.metrics.rejected_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.queue_lag_exceeded",
                trigger="finalized_bar_processing_queue_lag",
                details={"queueLagSeconds": queue_lag, "maxQueueLagSeconds": self.config.max_queue_lag_seconds, "eventId": event.event_id},
            )
            record = self._write_event_record(event, "stale_no_order", market_event_id, ("weighted_voting.runtime.stale_queued_event_rejected",))
            self._write_checkpoint(event, market_event_id, decision_id=None, status="stale_no_order")
            return record
        decision_started = _now()
        context = self.build_runtime_context_from_finalised_bar(
            snapshot=snapshot,
            active_weight_state=weight_state,
            effective_settings=effective,
            market_condition=condition,
            observed_at=snapshot.data_timestamp,
            session_evidence=session_evidence,
        )
        result = self.service.evaluate_context(context)
        self.metrics.decision_latency_ms = round((_now() - decision_started).total_seconds() * 1000, 3)
        self._capture_decision_observability_metrics(result)
        decision_id = str(result["decision"]["decision_id"])
        decision_idempotency_key = weighted_voting_decision_idempotency_key(
            market_event_id=market_event_id,
            settings_version=effective.settings_version,
            weight_version=weight_state.weight_version,
            inventory_version=context.inventory_snapshot.snapshot_version,
            decision_kernel_version=WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
        )
        self.store.write_snapshot(
            f"{RUNTIME_DECISION_IDEMPOTENCY_PREFIX}{decision_idempotency_key}",
            {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "marketEventId": market_event_id,
                "decisionId": decision_id,
                "decisionIdempotencyKey": decision_idempotency_key,
                "settingsVersion": effective.settings_version,
                "weightVersion": weight_state.weight_version,
                "inventoryVersion": context.inventory_snapshot.snapshot_version,
                "decisionKernelVersion": WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
                "recordedAt": _now().isoformat(),
                "reasonCodes": ("weighted_voting.runtime.decision_idempotency_persisted",),
            },
        )
        self.metrics.last_bar_processed = _bar_summary(event)
        self._enqueue_risk_from_result(
            result,
            idempotency_key=market_event_id,
            evaluated_at=snapshot.data_timestamp,
            context=context,
        )
        self.metrics.processed_events += 1
        self.metrics.persisted_decisions += 1
        self.metrics.last_decision_id = decision_id
        self.metrics.last_decision = _last_decision_observation(result)
        self.metrics.last_local_gate_result = _sanitize_for_observability(result.get("gateResult") if isinstance(result.get("gateResult"), dict) else None)
        record = self._write_event_record(event, "decision_persisted", market_event_id, ("weighted_voting.runtime.decision_persisted",), decision_id=decision_id)
        self._write_checkpoint(event, market_event_id, decision_id=decision_id, status="decision_persisted")
        return record

    def _active_effective_settings(self) -> WeightedEffectiveSettings:
        try:
            return load_effective_settings(self.store)
        except KeyError:
            effective = resolve_effective_settings(
                baseline_config=self.weighted_config,
                source_evidence=("weighted_voting.runtime.stable_bootstrap_settings",),
            )
            persist_effective_settings(self.store, effective)
            return effective

    def build_runtime_context_from_finalised_bar(
        self,
        *,
        snapshot: WeightedMarketSnapshot,
        active_weight_state: WeightedWeightState,
        effective_settings: WeightedEffectiveSettings,
        market_condition: Any,
        observed_at: datetime,
        session_evidence: dict[str, Any] | None = None,
    ) -> WeightedVotingRuntimeContext:
        session_evidence = session_evidence or self._authoritative_session_evidence(snapshot.data_timestamp)
        context = WeightedVotingRuntimeContextBuilder(
            market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
            inventory_repository=self.inventory_repository,
            account_port=self.account_port,
            global_risk_port=self.global_risk_port,
            effective_settings=effective_settings,
            active_weight_state=active_weight_state,
            observed_at=observed_at,
            mode="production",
            exchange_session_state=WeightedVotingExchangeSessionState(
                session_date=_parse_session_date(session_evidence.get("sessionDate")) or snapshot.data_timestamp.date(),
                session_phase=str(session_evidence.get("phase") or snapshot.session_phase),
                session_allowed=bool(session_evidence.get("sessionAllowed")),
                is_exchange_open=bool(session_evidence.get("sessionAllowed")),
                reason_codes=tuple(str(code) for code in session_evidence.get("reasonCodes") or ("weighted_voting.runtime_context.session_state_from_authoritative_exchange_calendar",)),
            ),
            cost_estimate=_runtime_cost_estimate(
                effective_settings=effective_settings,
                weighted_config=self.weighted_config,
                observed_at=observed_at,
            ),
            market_condition=market_condition,
        ).build()
        self.store.write_snapshot(
            f"weighted_voting.runtime.contexts.{context.finalised_one_minute_market_snapshot.symbol.upper()}.{context.finalised_one_minute_market_snapshot.data_timestamp.isoformat()}",
            {
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "context_version": context.context_version,
                "manifest_hash": context.manifest_hash,
                "symbol": context.finalised_one_minute_market_snapshot.symbol.upper(),
                "data_timestamp": context.finalised_one_minute_market_snapshot.data_timestamp.isoformat(),
                "one_minute_candle_count": len(context.finalised_one_minute_market_snapshot.one_minute_candles),
                "five_minute_candle_count": len(context.five_minute_candles),
                "five_minute_alignment": _enum_value(context.five_minute_alignment),
                "settings_version": context.effective_settings.settings_version,
                "weight_version": context.active_weight_state.weight_version,
                "inventory_snapshot_version": context.inventory_snapshot.snapshot_version,
                "inventory_available": context.inventory_available,
                "current_position_quantity": context.current_position.quantity if context.current_position else 0,
                "pending_order_count": len(context.pending_orders),
                "algorithm_daily_pnl": context.algorithm_daily_pnl,
                "algorithm_daily_trade_count": context.algorithm_daily_trade_count,
                "remaining_algorithm_daily_risk": context.remaining_algorithm_daily_risk,
                "remaining_algorithm_capital_partition": context.remaining_algorithm_capital_partition,
                "read_only_account_equity_available": context.read_only_account_equity is not None,
                "read_only_broker_buying_power_available": context.read_only_broker_buying_power is not None,
                "current_spread": context.current_spread,
                "estimated_slippage": context.estimated_slippage,
                "estimated_fees": context.estimated_fees,
                "global_risk_service_available": context.global_risk_state.service_available,
                "global_available_risk": context.global_risk_state.global_available_risk,
                "global_max_shares": context.global_risk_state.global_max_shares,
                "reason_codes": ("weighted_voting.runtime.full_context_built_from_finalised_bar",),
            },
        )
        return context

    def _mark_inventory_from_market_snapshot(self, snapshot: WeightedMarketSnapshot, *, market_event_id: str) -> None:
        price = _market_snapshot_mark_price(snapshot)
        if price is None:
            return
        self.inventory_repository.mark_to_market(
            symbol=snapshot.symbol,
            price=price,
            occurred_at=snapshot.data_timestamp,
            market_event_id=market_event_id,
            source="weighted_voting.runtime.finalized_bar_mark_to_market",
        )
        broker = getattr(self.paper_gateway, "broker", None)
        processor = getattr(broker, "process_market_data", None)
        if callable(processor):
            processor(
                symbol=snapshot.symbol,
                market_data=_local_paper_market_data_from_snapshot(snapshot),
                observed_at=snapshot.data_timestamp,
            )

    def process_execution_queue_item(self, item: WeightedVotingExecutionQueueItem) -> dict[str, Any]:
        self.metrics.execution_queue_depth = self.execution_queue.qsize()
        outbox = self._read_execution_outbox_record(item.command.order_intent_id)
        if str((outbox or {}).get("status") or "") in WEIGHTED_VOTING_EXECUTION_OUTBOX_TERMINAL_STATES:
            return self._write_execution_record(
                item,
                status="outbox_terminal_noop",
                reason_codes=("weighted_voting.runtime.execution_outbox.terminal_noop",),
                result=outbox,
            )
        if self.metrics.recovery_required or self.metrics.circuit_breaker_open:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._write_execution_outbox_record(
                item,
                status="RECONCILIATION_REQUIRED",
                reason_codes=("weighted_voting.runtime.execution_outbox.recovery_blocks_submission",),
            )
            return self._write_execution_record(
                item,
                status="recovery_blocked",
                reason_codes=("weighted_voting.runtime.recovery_blocks_submission",),
            )
        if self.paper_gateway is None:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = "WeightedVotingExecution: paper gateway unavailable"
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.broker_disconnected",
                trigger="execution_broker_unavailable",
                details={"orderIntentId": item.command.order_intent_id},
                broker_available=False,
            )
            self._write_execution_outbox_record(
                item,
                status="RECONCILIATION_REQUIRED",
                reason_codes=("weighted_voting.runtime.execution_outbox.paper_gateway_unavailable",),
            )
            return self._write_execution_record(
                item,
                status="gateway_unavailable",
                reason_codes=("weighted_voting.runtime.paper_gateway_unavailable",),
            )
        if not _verify_weighted_voting_paper_endpoint(self.paper_gateway):
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = "WeightedVotingExecution: live or unverified broker endpoint rejected"
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.broker_disconnected_or_unverified",
                trigger="execution_broker_endpoint_unverified",
                details={"orderIntentId": item.command.order_intent_id},
                broker_available=False,
            )
            self._write_execution_outbox_record(
                item,
                status="REJECTED",
                reason_codes=("weighted_voting.runtime.execution_outbox.live_gateway_rejected", "paper_gateway.paper_endpoint_unverified"),
            )
            return self._write_execution_record(
                item,
                status="paper_endpoint_unverified",
                reason_codes=("weighted_voting.runtime.paper_endpoint_unverified",),
            )
        verifier = getattr(self.paper_gateway.broker, "verify_paper_account", None)
        try:
            paper_account_verified = bool(callable(verifier) and verifier())
        except Exception:
            paper_account_verified = False
        if not paper_account_verified:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = "WeightedVotingExecution: paper account verification failed"
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.paper_account_unverified",
                trigger="execution_paper_account_verification",
                details={"orderIntentId": item.command.order_intent_id},
                broker_available=True,
            )
            self._write_execution_outbox_record(
                item,
                status="REJECTED",
                reason_codes=("weighted_voting.runtime.execution_outbox.paper_account_unverified",),
            )
            return self._write_execution_record(
                item,
                status="paper_account_unverified",
                reason_codes=("weighted_voting.runtime.paper_account_unverified",),
            )
        if _runtime_order_type_is_market(item.command.order_type):
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._write_execution_outbox_record(
                item,
                status="REJECTED",
                reason_codes=("weighted_voting.runtime.execution_outbox.market_entry_rejected",),
            )
            return self._write_execution_record(
                item,
                status="market_entry_rejected",
                reason_codes=("weighted_voting.runtime.market_entry_rejected",),
            )
        ready, readiness_reason = self._automatic_entry_queue_readiness()
        if not ready:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._write_execution_outbox_record(
                item,
                status="CANCELLED",
                reason_codes=("weighted_voting.runtime.execution_outbox.control_cancelled_unsubmitted_entry", readiness_reason),
            )
            return self._write_execution_record(
                item,
                status="control_blocked",
                reason_codes=("weighted_voting.runtime.control.blocks_automatic_submission", readiness_reason),
            )
        outbox = self._read_execution_outbox_record(item.command.order_intent_id)
        if self._outbox_requires_broker_lookup_before_submit(outbox):
            broker_state = self._broker_lookup_for_retry(item, reason_code="weighted_voting.runtime.execution_outbox.retry_broker_lookup_before_submit")
            if broker_state is not None:
                outbox_status = _outbox_status_from_broker_state(broker_state)
                self._write_execution_outbox_record(
                    item,
                    status=outbox_status,
                    reason_codes=("weighted_voting.runtime.execution_outbox.retry_resolved_by_broker_lookup",),
                    broker_lookup=broker_state,
                )
                return self._write_execution_record(
                    item,
                    status="broker_lookup_reconciled",
                    reason_codes=("weighted_voting.runtime.execution.retry_resolved_by_broker_lookup",),
                    result={"brokerLookup": broker_state, "outboxStatus": outbox_status},
                )
        broker_started = _now()
        attempt_no = self._next_execution_attempt_number(item.command.order_intent_id)
        self._write_execution_outbox_record(
            item,
            status="SUBMITTING",
            reason_codes=("weighted_voting.runtime.execution_outbox.submitting_paper_order",),
            submission_attempt_no=attempt_no,
        )
        self._append_execution_attempt(
            item,
            attempt_no=attempt_no,
            status="SUBMITTING",
            reason_codes=("weighted_voting.runtime.execution_outbox.submission_attempt_started",),
        )
        try:
            result = submit_queued_weighted_voting_paper_order(
                gateway=self.paper_gateway,
                queue_item=item,
                inventory_repository=self.inventory_repository,
                evaluated_at=item.enqueued_at,
                rollout_flags=self.rollout_flags,
                rollout_validation=self.rollout_validation,
            )
        except Exception as exc:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = f"WeightedVotingExecution: {exc}"
            self._trip_circuit_breaker(
                _circuit_breaker_reason_from_exception(exc, default="weighted_voting.runtime.circuit_breaker.broker_submission_failed"),
                trigger="execution_submission_exception",
                details={"orderIntentId": item.command.order_intent_id, "error": str(exc)},
                broker_available=not _submission_exception_is_broker_disconnect(exc),
            )
            outbox_status, outbox_reason = _outbox_status_from_submission_exception(exc)
            self._append_execution_attempt(
                item,
                attempt_no=attempt_no,
                status=outbox_status,
                reason_codes=(outbox_reason,),
                result={"error": str(exc), "submitted": False},
            )
            self._write_execution_outbox_record(
                item,
                status=outbox_status,
                reason_codes=(outbox_reason,),
                result={"error": str(exc), "submitted": False},
                submission_attempt_no=attempt_no,
            )
            if outbox_status == "RECONCILIATION_REQUIRED":
                self.reconcile_broker_inventory(
                    startup=False,
                    reason=outbox_reason,
                    trigger="submission_error",
                )
                self.perform_recovery_safety_check(reason="weighted_voting.runtime.execution_exception_recovery_required")
            return self._write_execution_record(
                item,
                status="submission_failed_safe_degradation",
                reason_codes=("weighted_voting.runtime.execution_exception_blocks_new_entries",),
                result={"error": str(exc), "submitted": False},
            )
        self.metrics.broker_latency_ms = round((_now() - broker_started).total_seconds() * 1000, 3)
        result_payload = result.model_dump(mode="json")
        acknowledgement = result_payload.get("brokerAck") if isinstance(result_payload.get("brokerAck"), dict) else {}
        self.metrics.last_order_intent = _sanitize_for_observability(item.command.as_dict())
        self.metrics.last_acknowledgement = _sanitize_for_observability(acknowledgement) if acknowledgement else None
        self.metrics.last_order_submission = {
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "submitted": bool(result.submitted),
            "status": result_payload.get("status"),
            "acknowledgementStatus": acknowledgement.get("status") if acknowledgement else None,
            "recordedAt": _now().isoformat(),
        }
        fill_payload = _deep_get(result_payload, ("reconciliation", "fill"))
        if isinstance(fill_payload, dict):
            self.metrics.last_fill = fill_payload
            self.metrics.fill_quality = {
                "clientOrderId": fill_payload.get("clientOrderId"),
                "filledQuantity": fill_payload.get("filledQuantity"),
                "averageFillPrice": fill_payload.get("averageFillPrice"),
                "status": fill_payload.get("status"),
            }
            if item.command.limit_price and fill_payload.get("averageFillPrice"):
                self.metrics.slippage = {
                    "clientOrderId": item.command.client_order_id,
                    "limitPrice": item.command.limit_price,
                    "averageFillPrice": fill_payload.get("averageFillPrice"),
                    "signedDifference": round(float(fill_payload["averageFillPrice"]) - float(item.command.limit_price), 10),
                }
        if result.submitted:
            self.metrics.submitted_orders += 1
            self.metrics.consecutive_order_rejections = 0
        else:
            self.metrics.rejected_execution_events += 1
            self.metrics.consecutive_order_rejections += 1
            if self.metrics.consecutive_order_rejections >= self.config.repeated_order_rejection_threshold:
                self._trip_circuit_breaker(
                    "weighted_voting.runtime.circuit_breaker.repeated_order_rejections",
                    trigger="execution_repeated_order_rejection",
                    details={
                        "orderIntentId": item.command.order_intent_id,
                        "consecutiveOrderRejections": self.metrics.consecutive_order_rejections,
                        "threshold": self.config.repeated_order_rejection_threshold,
                        "resultStatus": result_payload.get("status"),
                        "reasonCodes": result_payload.get("reasonCodes"),
                    },
                )
        outbox_status = _outbox_status_from_gateway_result(result_payload)
        self._append_execution_attempt(
            item,
            attempt_no=attempt_no,
            status=outbox_status,
            reason_codes=tuple(result.reasonCodes),
            result=result_payload,
        )
        self._write_execution_outbox_record(
            item,
            status=outbox_status,
            reason_codes=tuple(result.reasonCodes),
            result=result_payload,
            submission_attempt_no=attempt_no,
        )
        return self._write_execution_record(
            item,
            status="submitted" if result.submitted else "not_submitted",
            reason_codes=tuple(result.reasonCodes),
            result=result_payload,
        )

    def reconcile_broker_inventory(
        self,
        *,
        startup: bool = False,
        reason: str = "weighted_voting.runtime.reconciliation.requested",
        trigger: str | None = None,
    ) -> None:
        if self.paper_gateway is None:
            self.metrics.inventory_reconciled = False
            self.metrics.entry_creation_paused_for_reconciliation = True
            self.metrics.automatic_order_creation_paused = True
            self._trip_circuit_breaker(
                "weighted_voting.runtime.circuit_breaker.broker_disconnected",
                trigger="reconciliation_broker_unavailable",
                details={"startup": startup, "requestedReason": reason},
                broker_available=False,
            )
            self.metrics.last_reconciliation = {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "status": "unavailable",
                "entriesPaused": True,
                "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
                "trigger": trigger or ("startup" if startup else "runtime"),
                "recordedAt": _now().isoformat(),
                "reasonCodes": ("weighted_voting.runtime.reconciliation.paper_gateway_unavailable", reason),
            }
            self._write_status("reconciliation_unavailable", ("weighted_voting.runtime.reconciliation.paper_gateway_unavailable",))
            return
        try:
            orders, fills, positions = _broker_observations_from_gateway(self.paper_gateway, self.store, observed_at=_now())
            result = reconcile_weighted_voting_broker_observations(
                store=self.store,
                inventory_repository=self.inventory_repository,
                orders=orders,
                fills=fills,
                positions=positions,
                reconciled_at=_now(),
            )
            self.metrics.inventory_reconciled = result.inventory_reconciled
            self.metrics.entry_creation_paused_for_reconciliation = result.entries_paused
            self.metrics.risk_reducing_exits_allowed = result.risk_reducing_exits_allowed
            self.metrics.reconciliation_discrepancies = len(result.discrepancies)
            self.metrics.last_reconciliation = {
                **result.as_dict(),
                "trigger": trigger or ("startup" if startup else "runtime"),
                "requestedReason": reason,
                "compared": {
                    "brokerOrders": len(orders),
                    "brokerFills": len(fills),
                    "brokerPositions": len(positions),
                },
            }
            if result.entries_paused:
                self.metrics.automatic_order_creation_paused = True
                breaker_reason = _reconciliation_circuit_breaker_reason(result.as_dict())
                self._trip_circuit_breaker(
                    breaker_reason,
                    trigger="broker_reconciliation_discrepancy",
                    details={"discrepancies": [item.as_dict() for item in result.discrepancies], "requestedReason": reason},
                )
                if any(bool(item.details.get("riskReductionPriority")) for item in result.discrepancies):
                    self.metrics.pause_reason = "weighted_voting.runtime.reconciliation.protective_order_mismatch_prioritize_risk_reduction"
            self._write_status(
                "startup_reconciled" if startup else "reconciled",
                tuple(dict.fromkeys((*result.reason_codes, reason))),
            )
        except Exception as exc:
            self.metrics.inventory_reconciled = False
            self.metrics.entry_creation_paused_for_reconciliation = True
            self.metrics.automatic_order_creation_paused = True
            self.metrics.last_error = f"WeightedVotingReconciliation: {exc}"
            self._trip_circuit_breaker(
                _circuit_breaker_reason_from_exception(exc, default="weighted_voting.runtime.circuit_breaker.reconciliation_failed"),
                trigger="broker_reconciliation_exception",
                details={"error": str(exc), "requestedReason": reason},
            )
            self._write_status("reconciliation_failed", ("weighted_voting.runtime.reconciliation.failed_entries_paused",))

    def manage_positions_once(self, *, trigger: str = "runtime", managed_at: datetime | None = None) -> dict[str, Any]:
        managed_at = _require_utc_datetime(managed_at or _now())
        effective = self._active_effective_settings()
        snapshot = self.inventory_repository.current_snapshot(now=managed_at)
        current_price = self._latest_position_management_price(snapshot)
        emergency = _has_pending_emergency_flatten(self.store)
        end_of_day = self.calendar.should_flatten(managed_at, self.weighted_config)
        eod_transition: dict[str, Any] | None = None
        if self.calendar.should_cancel_entries(managed_at, self.weighted_config):
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.session.entry_cutoff_or_eod"
            eod_transition = self._disable_paper_entries(
                updated_at=managed_at,
                reason="weighted_voting.runtime.session.entry_cutoff_or_eod",
            )
        protected: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        for position in snapshot.open_positions:
            if str(getattr(position, "algorithm_id", "")) != WEIGHTED_VOTING_ALGORITHM_ID:
                mismatches.append(
                    {
                        "algorithmId": getattr(position, "algorithm_id", None),
                        "positionId": getattr(position, "position_id", None),
                        "reasonCode": "weighted_voting.runtime.position_manager.foreign_position_ignored",
                    }
                )
                continue
            management = self.position_manager.ensure_position_protection(
                position=position,
                effective_settings=effective,
                entry_order_id=position.client_order_id,
                protected_at=managed_at,
            )
            protected.append(management["protection"])
            if management.get("mismatch"):
                mismatches.append(management["mismatch"])
            if current_price is None:
                continue
            trade = self.position_manager.monitor_position(
                position=position,
                current_price=current_price,
                observed_at=managed_at,
                end_of_day=end_of_day,
                global_emergency_exit=emergency,
                signal_decay_exit=bool(self.metrics.strategy_opportunity_counts and self.metrics.automatic_order_creation_paused),
                realised_exit_costs=0.0,
            )
            if trade is not None:
                trades.append(trade.as_dict())
        if mismatches:
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.position_manager.protective_order_mismatch_prioritize_risk_reduction"
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "trigger": trigger,
            "managedAt": managed_at.isoformat(),
            "currentPrice": current_price,
            "endOfDayExitDue": end_of_day,
            "entryCancellationTransition": eod_transition,
            "openPositionCount": len(snapshot.open_positions),
            "protectedCount": len(protected),
            "closedTradeCount": len(trades),
            "protectiveMismatches": mismatches,
            "entriesPaused": self.metrics.automatic_order_creation_paused,
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "reasonCodes": ("weighted_voting.runtime.position_manager.managed_positions",),
        }
        self.store.write_snapshot(f"weighted_voting.runtime.position_manager.checks.{managed_at.isoformat()}.{_hash_payload(record)}", record)
        return record

    def run_daily_update_if_due(self, *, trigger: str = "runtime", now: datetime | None = None) -> dict[str, Any]:
        checked_at = _require_utc_datetime(now or _now())
        activation = self.activate_published_weights_if_due(now=checked_at)
        try:
            clock = self.calendar.session_clock(checked_at)
        except Exception as exc:
            record = self._write_daily_update_runtime_record(
                status="calendar_unavailable_fail_closed",
                checked_at=checked_at,
                trigger=trigger,
                reason_codes=("weighted_voting.runtime.daily_update.exchange_calendar_unavailable",),
                details={"error": str(exc), "activation": activation},
            )
            return record
        if not clock.session_date or not clock.exchange_close or checked_at < clock.exchange_close:
            return self._write_daily_update_runtime_record(
                status="skipped_intraday_weights_frozen",
                checked_at=checked_at,
                trigger=trigger,
                reason_codes=("weighted_voting.runtime.daily_update.intraday_weights_frozen",),
                details={"sessionClock": clock.as_dict(), "activation": activation},
            )
        session_date = date.fromisoformat(clock.session_date)
        return self.run_daily_update(session_date=session_date, completed_at=checked_at, trigger=trigger, session_clock=clock, activation=activation)

    def activate_published_weights_if_due(self, *, now: datetime | None = None) -> dict[str, object]:
        activated_at = _require_utc_datetime(now or _now())
        try:
            clock = self.calendar.session_clock(activated_at)
            if not clock.session_date:
                raise ValueError("missing exchange session date")
            session_date = date.fromisoformat(clock.session_date)
        except Exception as exc:
            return {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "status": "calendar_unavailable_fail_closed",
                "activatedAt": activated_at.isoformat(),
                "reasonCodes": ("weighted_voting.runtime.daily_update.activation_calendar_unavailable",),
                "error": str(exc),
            }
        result = activate_published_weight_for_session(
            store=self.store,
            session_date=session_date,
            activated_at=activated_at,
            exchange_calendar=getattr(self.calendar, "exchange_calendar", None),
        )
        self.store.write_snapshot(f"weighted_voting.runtime.daily_update.activation.{session_date.isoformat()}", dict(result))
        return result

    def run_daily_update(
        self,
        *,
        session_date: date,
        completed_at: datetime | None = None,
        trigger: str = "runtime",
        session_clock: WeightedVotingSessionClock | None = None,
        activation: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        completed = _require_utc_datetime(completed_at or _now())
        self.metrics.automatic_order_creation_paused = True
        self.metrics.pause_reason = "weighted_voting.runtime.daily_update.entries_closed_after_session"
        stale_cancellations = self._disable_paper_entries(
            updated_at=completed,
            reason="weighted_voting.runtime.daily_update.cancel_stale_entries_after_session",
        )
        position_check = self.manage_positions_once(trigger="daily_update_flatten_check", managed_at=completed)
        self.reconcile_broker_inventory(reason="weighted_voting.runtime.daily_update.reconcile_broker_inventory", trigger="daily_update")
        inventory = self.inventory_repository.current_snapshot(now=completed)
        if inventory.open_positions:
            return self._write_daily_update_runtime_record(
                status="flattening_required",
                checked_at=completed,
                trigger=trigger,
                reason_codes=("weighted_voting.runtime.daily_update.open_positions_block_weight_update",),
                details={
                    "sessionDate": session_date.isoformat(),
                    "sessionClock": session_clock.as_dict() if session_clock else None,
                    "activation": activation,
                    "staleCancellations": stale_cancellations,
                    "positionCheck": position_check,
                    "openPositionCount": len(inventory.open_positions),
                    "lastReconciliation": self.metrics.last_reconciliation,
                },
            )
        provider = WeightedVotingRuntimeFinalizedBarDatasetProvider(self.store, symbol=self.config.symbols[0])
        result = run_after_market_daily_weight_update(
            session_date=session_date,
            store=self.store,
            dataset_provider=provider,
            completed_at=completed,
            config=WeightedVotingDailySchedulerConfig(
                symbol=self.config.symbols[0],
                weighted_config=self.weighted_config,
                exchange_calendar=getattr(self.calendar, "exchange_calendar", None),
            ),
        )
        payload = _json_ready(asdict(result))
        runtime_record = self._write_daily_update_runtime_record(
            status=str(payload.get("status") or "unknown"),
            checked_at=completed,
            trigger=trigger,
            reason_codes=tuple(payload.get("reason_codes") or payload.get("reasonCodes") or ()),
            details={
                "sessionDate": session_date.isoformat(),
                "sessionClock": session_clock.as_dict() if session_clock else None,
                "activation": activation,
                "staleCancellations": stale_cancellations,
                "positionCheck": position_check,
                "lastReconciliation": self.metrics.last_reconciliation,
                "dailyUpdate": payload,
            },
        )
        self.store.write_snapshot("weighted_voting.daily_update.latest", runtime_record)
        return runtime_record

    def _write_daily_update_runtime_record(
        self,
        *,
        status: str,
        checked_at: datetime,
        trigger: str,
        reason_codes: tuple[str, ...],
        details: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "trigger": trigger,
            "checkedAt": checked_at.isoformat(),
            "weightsFrozenDuringSession": True,
            "intradayWeightMutationAllowed": False,
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
            "details": details,
        }
        self.store.write_snapshot(f"weighted_voting.runtime.daily_update.{checked_at.isoformat()}.{_hash_payload(record)}", record)
        self.store.write_snapshot("weighted_voting.runtime.daily_update.latest", record)
        return record

    def _latest_position_management_price(self, snapshot: Any) -> float | None:
        last_bar = self.metrics.last_bar_processed if isinstance(self.metrics.last_bar_processed, dict) else {}
        price = _optional_float(_first_present(last_bar.get("close"), _deep_get(last_bar, ("ohlcv", "close"))))
        if price and price > 0:
            return price
        for position in snapshot.open_positions:
            if position.mark_price and position.mark_price > 0:
                return float(position.mark_price)
            if position.average_entry_price > 0:
                return float(position.average_entry_price)
        return None

    def _authoritative_session_evidence(self, timestamp: datetime | None) -> dict[str, Any]:
        if timestamp is None:
            return {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "sessionAllowed": False,
                "insideEntryDecisionWindow": False,
                "reasonCodes": ("weighted_voting.runtime.session.timestamp_missing_fail_closed",),
            }
        reason_codes: list[str] = ["weighted_voting.runtime.session.authoritative_exchange_calendar_checked"]
        try:
            clock = self.calendar.session_clock(timestamp) if hasattr(self.calendar, "session_clock") else None
        except Exception as exc:
            return {
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "sessionAllowed": False,
                "insideEntryDecisionWindow": False,
                "error": str(exc),
                "reasonCodes": ("weighted_voting.runtime.session.exchange_calendar_unavailable_fail_closed",),
            }
        if clock is not None:
            clock_payload = clock.as_dict()
            calendar_open = bool(clock.regular_session)
            phase = str(getattr(clock.current_phase, "value", clock.current_phase))
            session_date = clock.session_date
            early_close = bool(clock.early_close)
            minute_from_open = clock.minute_from_open
            minutes_until_close = clock.minutes_until_close
        else:
            calendar_open = bool(self.calendar.is_trading_session(timestamp, None))
            phase = "regular_session" if calendar_open else "closed"
            session_date = _require_utc_datetime(timestamp).astimezone(ZoneInfo("America/New_York")).date().isoformat()
            early_close = False
            minute_from_open = None
            minutes_until_close = None
            clock_payload = {"source": "weighted_voting.calendar_compatibility_adapter"}
        broker_clock = self._broker_market_clock_evidence()
        broker_open = broker_clock.get("isOpen") if broker_clock else None
        if broker_open is False:
            reason_codes.append("weighted_voting.runtime.session.broker_market_clock_closed_veto")
        if not calendar_open:
            reason_codes.append("weighted_voting.runtime.session.exchange_calendar_closed")
        session_allowed = bool(calendar_open and broker_open is not False)
        if hasattr(self.calendar, "inside_entry_decision_window"):
            inside_window = bool(session_allowed and self.calendar.inside_entry_decision_window(timestamp, self.weighted_config))
        else:
            inside_window = bool(session_allowed and _inside_entry_decision_window(timestamp, self.weighted_config))
        if not inside_window:
            reason_codes.append("weighted_voting.runtime.session.outside_entry_decision_window")
        return {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "sessionAllowed": session_allowed,
            "insideEntryDecisionWindow": inside_window,
            "phase": phase,
            "sessionDate": session_date,
            "earlyClose": early_close,
            "minuteFromOpen": minute_from_open,
            "minutesUntilClose": minutes_until_close,
            "exchangeClock": clock_payload,
            "brokerMarketClock": broker_clock,
            "source": "weighted_voting.runtime.authoritative_session_calendar",
            "payloadSessionIgnored": True,
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }

    def _broker_market_clock_evidence(self) -> dict[str, Any] | None:
        if self.paper_gateway is None:
            return None
        reader = getattr(self.paper_gateway.broker, "refresh_market_clock", None)
        if not callable(reader):
            return None
        try:
            payload = reader()
        except Exception as exc:
            return {
                "available": False,
                "isOpen": False,
                "error": str(exc),
                "reasonCodes": ("weighted_voting.runtime.session.broker_market_clock_unavailable_fail_closed",),
            }
        if not isinstance(payload, dict):
            payload = _runtime_model_payload(payload)
        is_open = _bool_payload_value(_first_present(payload.get("isOpen"), payload.get("is_open"), payload.get("open")))
        return {
            **payload,
            "available": True,
            "isOpen": is_open,
            "source": payload.get("source") or "broker_market_clock",
            "reasonCodes": tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ("weighted_voting.runtime.session.broker_market_clock_checked",)),
        }

    def _capture_decision_observability_metrics(self, result: dict[str, Any]) -> None:
        self.metrics.last_global_risk_response = result.get("globalRiskResponse") if isinstance(result.get("globalRiskResponse"), dict) else None
        proposal = result.get("globalOrderProposal") if isinstance(result.get("globalOrderProposal"), dict) else {}
        application = result.get("globalGateApplication") if isinstance(result.get("globalGateApplication"), dict) else {}
        proposed_quantity = _safe_int(proposal.get("quantity") or proposal.get("proposedQuantity"))
        allowed_quantity = _safe_int(application.get("globallyAllowedQuantity") or application.get("maximumQuantity"))
        self.metrics.proposed_vs_allowed_quantity = {
            "proposalId": proposal.get("proposalId") or proposal.get("proposal_id"),
            "proposedQuantity": proposed_quantity,
            "allowedQuantity": allowed_quantity,
        }
        if proposed_quantity > 0 and allowed_quantity > 0:
            self.metrics.last_accepted_proposal = {
                "proposalId": proposal.get("proposalId") or proposal.get("proposal_id"),
                "decisionId": proposal.get("decisionId") or proposal.get("decision_id"),
                "symbol": proposal.get("symbol"),
                "proposedQuantity": proposed_quantity,
                "allowedQuantity": allowed_quantity,
            }
        for code in _reason_codes_from_result(result):
            if ".gate." in code or "gate" in code:
                self.metrics.gate_rejection_counts[code] = self.metrics.gate_rejection_counts.get(code, 0) + 1
        for signal in _signals_from_result(result):
            strategy_id = str(signal.get("strategyId") or signal.get("strategy_id") or "")
            if not strategy_id:
                continue
            self.metrics.strategy_opportunity_counts[strategy_id] = self.metrics.strategy_opportunity_counts.get(strategy_id, 0) + 1
            lifecycle = "shadow" if bool(signal.get("shadowRecordsOnly") or signal.get("shadow_records_only")) else "active"
            if lifecycle not in self.metrics.strategy_signal_counts:
                self.metrics.strategy_signal_counts[lifecycle] = {}
            self.metrics.strategy_signal_counts[lifecycle][strategy_id] = self.metrics.strategy_signal_counts[lifecycle].get(strategy_id, 0) + 1
        self._record_shadow_evidence(result)

    def _record_shadow_evidence(self, result: dict[str, Any]) -> None:
        """Persist this decision's per-strategy signals so shadow evidence can accrue.

        The counters above live on an in-memory metrics object and die with the process.
        Promotion evidence has to survive a restart, so the signals themselves are
        written to each strategy's own key. Never allowed to disturb the decision loop.
        """
        store = getattr(self, "store", None)
        if store is None:
            return
        try:
            record_shadow_observations(
                store,
                _signals_from_result(result),
                session_label=_shadow_session_label(result),
                regime_label=_shadow_regime_label(result),
            )
        except Exception:
            return

    def _worker_state(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for worker in self.workers:
            task = self.tasks.get(worker.worker_id)
            state[worker.worker_id] = {
                "running": bool(task and not task.done()),
                "done": bool(task and task.done()),
                "failures": self.metrics.worker_failures.get(worker.worker_id, 0),
                "restarts": self.metrics.worker_restarts.get(worker.worker_id, 0),
            }
        return state

    def _admin_state(self) -> dict[str, Any]:
        return {
            "paused": self.metrics.paused,
            "automaticOrderCreationPaused": self.metrics.automatic_order_creation_paused,
            "entryCreationPausedForReconciliation": self.metrics.entry_creation_paused_for_reconciliation,
            "riskReducingExitsAllowed": self.metrics.risk_reducing_exits_allowed,
            "pauseReason": self.metrics.pause_reason,
        }

    def _write_admin_audit(
        self,
        action: str,
        *,
        actor: str,
        prior_state: dict[str, Any],
        new_state: dict[str, Any],
        reason_codes: tuple[str, ...],
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recorded_at = _now()
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "action": action,
            "actor": actor,
            "recordedAt": recorded_at.isoformat(),
            "priorState": prior_state,
            "newState": new_state,
            "details": details or {},
            "reasonCodes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_ADMIN_AUDIT_PREFIX}{recorded_at.isoformat()}.{_hash_payload(record)}", record)
        return record

    def restore_position_management(self) -> None:
        try:
            restored = self.position_manager.restore_protective_management(effective_settings_by_version={}, restored_at=_now())
            self.store.write_snapshot(
                "weighted_voting.runtime.position_manager.restore",
                {
                    "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                    "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                    "restored_count": len(restored),
                    "dashboard_required": False,
                    "updated_at": _now().isoformat(),
                    "reason_codes": ("weighted_voting.runtime.position_manager_restored",),
                },
            )
        except Exception as exc:
            self.metrics.last_error = f"WeightedVotingPositionManager: {exc}"
            self._write_status("position_manager_restore_failed", ("weighted_voting.runtime.position_manager_restore_failed",))

    def _enqueue_risk_from_result(
        self,
        result: dict[str, Any],
        *,
        idempotency_key: str,
        evaluated_at: datetime,
        context: WeightedVotingRuntimeContext,
    ) -> WeightedVotingRiskQueueItem | None:
        try:
            proposal = GlobalOrderProposal.model_validate(result["globalOrderProposal"])
            local_gate_result = _local_gate_result_from_payload(result.get("gateResult") or {})
            item = WeightedVotingRiskQueueItem(
                algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
                risk_item_id=f"{RUNTIME_RISK_PREFIX}queue.{proposal.orderIntentId}.{idempotency_key}",
                idempotency_key=idempotency_key,
                decision_id=proposal.decisionId,
                order_intent_id=proposal.orderIntentId,
                proposal=proposal,
                local_gate_result=local_gate_result,
                evaluated_at=evaluated_at,
                inventory_snapshot_version=context.inventory_snapshot.snapshot_version,
                current_algorithm_exposure=context.inventory_snapshot.gross_exposure,
                current_account_exposure=_runtime_current_account_exposure(context),
                daily_algorithm_pnl=context.algorithm_daily_pnl,
                account_level_risk_observations=_runtime_account_level_risk_observations(context),
                settings_version=context.effective_settings.settings_version,
                source_result=dict(result),
            )
            self._persist_runtime_order_intent(item, status="PENDING_GLOBAL_RISK")
            self.store.write_snapshot(f"{RUNTIME_RISK_PREFIX}queue.{item.order_intent_id}", item.as_dict())
        except Exception as exc:
            self.metrics.rejected_execution_events += 1
            self.metrics.last_error = f"WeightedVotingRiskQueue: {exc}"
            self.metrics.automatic_order_creation_paused = True
            self.metrics.recovery_required = True
            self._trip_circuit_breaker(
                _circuit_breaker_reason_from_exception(exc, default="weighted_voting.runtime.circuit_breaker.persistence_failed"),
                trigger="risk_queue_persistence_exception",
                details={"error": str(exc)},
            )
            return None

        if self._risk_worker_running():
            try:
                self.risk_queue.put_nowait(item)
                self.metrics.risk_queue_depth = self.risk_queue.qsize()
                return item
            except asyncio.QueueFull:
                self.metrics.rejected_execution_events += 1
                self.metrics.automatic_order_creation_paused = True
                self._write_risk_record(
                    item,
                    status="risk_queue_full",
                    reason_codes=("weighted_voting.runtime.risk_queue_full",),
                    final_allowed_quantity=0,
                )
                return None

        self.process_risk_queue_item(item)
        return item

    def process_risk_queue_item(self, item: WeightedVotingRiskQueueItem) -> dict[str, Any]:
        self.metrics.risk_queue_depth = self.risk_queue.qsize()
        request = build_weighted_voting_global_risk_request(
            proposal=item.proposal,
            inventory_version=item.inventory_snapshot_version,
            current_algorithm_exposure=item.current_algorithm_exposure,
            current_account_exposure=item.current_account_exposure,
            daily_algorithm_pnl=item.daily_algorithm_pnl,
            account_level_risk_observations=item.account_level_risk_observations,
            settings_version=item.settings_version,
            requested_at=item.evaluated_at,
        )
        self.store.write_snapshot(f"{RUNTIME_RISK_PREFIX}requests.{request.request_id}", request.model_dump(mode="json"))
        self.store.write_snapshot(f"weighted_voting.global_risk_requests.{request.request_id}", request.model_dump(mode="json"))

        started = _now()
        raw_response = self._risk_response_for_item(item, request)
        self.metrics.risk_service_latency_ms = round((_now() - started).total_seconds() * 1000, 3)
        response, validation_reasons = validate_weighted_voting_global_risk_response(
            request=request,
            response=raw_response,
            now=item.evaluated_at,
        )
        response = self._enforce_runtime_global_risk_actions(request, response, evaluated_at=item.evaluated_at)
        response, validation_reasons = validate_weighted_voting_global_risk_response(
            request=request,
            response=response,
            now=item.evaluated_at,
        )
        response_payload = response.model_dump(mode="json")
        self.store.write_snapshot(f"{RUNTIME_RISK_PREFIX}responses.{request.request_id}", response_payload)
        self.store.write_snapshot(f"weighted_voting.global_risk_responses.{request.request_id}", response_payload)

        global_response = global_gate_response_from_weighted_voting_risk(response)
        try:
            application = apply_global_response_to_weighted_voting_proposal(item.proposal, global_response)
        except Exception as exc:
            response = fail_closed_global_risk_response(
                request,
                reason_codes=("weighted_voting.runtime.global_risk.application_invalid_reject",),
                evaluated_at=item.evaluated_at,
            )
            response_payload = response.model_dump(mode="json")
            self.store.write_snapshot(f"{RUNTIME_RISK_PREFIX}responses.{request.request_id}", response_payload)
            self.store.write_snapshot(f"weighted_voting.global_risk_responses.{request.request_id}", response_payload)
            global_response = global_gate_response_from_weighted_voting_risk(response)
            application = apply_global_response_to_weighted_voting_proposal(item.proposal, global_response)
            validation_reasons = tuple(dict.fromkeys((*validation_reasons, "weighted_voting.runtime.global_risk.application_invalid_reject", str(exc))))

        final_allowed_quantity = int(application.globallyAllowedQuantity)
        self.metrics.last_global_risk_response = response_payload
        self.metrics.proposed_vs_allowed_quantity = {
            "proposalId": item.proposal.orderIntentId,
            "proposedQuantity": item.proposal.quantity,
            "allowedQuantity": final_allowed_quantity,
        }
        risk_record = self._write_risk_record(
            item,
            status="approved_for_execution" if final_allowed_quantity > 0 else "rejected_by_global_risk",
            reason_codes=tuple(dict.fromkeys(("weighted_voting.runtime.global_risk.completed", *validation_reasons, *response.reason_codes))),
            request=request.model_dump(mode="json"),
            response=response_payload,
            application=application.model_dump(mode="json"),
            final_allowed_quantity=final_allowed_quantity,
        )
        if final_allowed_quantity <= 0:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            if _global_risk_infrastructure_failure(response.reason_codes):
                self.metrics.recovery_required = True
                self.metrics.pause_reason = "weighted_voting.runtime.global_risk_service_unavailable"
                self._trip_circuit_breaker(
                    "weighted_voting.runtime.circuit_breaker.global_risk_service_failed",
                    trigger="global_risk_service",
                    details={"reasonCodes": response.reason_codes, "requestId": request.request_id},
                )
            self._persist_runtime_order_intent(item, status="REJECTED_BY_GLOBAL_RISK", risk_record=risk_record)
            return risk_record

        result = {
            **item.source_result,
            "globalRiskRequest": request.model_dump(mode="json"),
            "globalRiskResponse": response_payload,
            "globalGateResponse": global_response.model_dump(mode="json"),
            "globalGateApplication": application.model_dump(mode="json"),
        }
        self._write_execution_outbox_from_risk_item(
            item,
            status="CREATED",
            reason_codes=("weighted_voting.runtime.execution_outbox.created_before_runtime_queue",),
            risk_record=risk_record,
            risk_request_id=request.request_id,
            final_allowed_quantity=final_allowed_quantity,
        )
        self._write_execution_outbox_from_risk_item(
            item,
            status="RISK_APPROVED",
            reason_codes=("weighted_voting.runtime.execution_outbox.risk_approved_before_runtime_queue",),
            risk_record=risk_record,
            risk_request_id=request.request_id,
            final_allowed_quantity=final_allowed_quantity,
        )
        queued_item = self._enqueue_execution_from_result(
            result,
            idempotency_key=item.idempotency_key,
            evaluated_at=item.evaluated_at,
            inventory_snapshot_version=item.inventory_snapshot_version,
        )
        if queued_item is not None:
            self._write_execution_outbox_record(
                queued_item,
                status="READY_TO_SUBMIT",
                reason_codes=("weighted_voting.runtime.execution_outbox.ready_to_submit_after_global_risk",),
                risk_record=risk_record,
                risk_request_id=request.request_id,
                final_allowed_quantity=final_allowed_quantity,
            )
        else:
            self._write_execution_outbox_from_risk_item(
                item,
                status="REJECTED",
                reason_codes=("weighted_voting.runtime.execution_outbox.execution_queue_not_reserved",),
                risk_record=risk_record,
                risk_request_id=request.request_id,
                final_allowed_quantity=final_allowed_quantity,
            )
        self._persist_runtime_order_intent(item, status="EXECUTION_OUTBOX_READY_TO_SUBMIT" if queued_item is not None else "EXECUTION_OUTBOX_REJECTED", risk_record=risk_record)
        return risk_record

    def _risk_response_for_item(self, item: WeightedVotingRiskQueueItem, request: Any) -> WeightedVotingGlobalRiskResponse | None:
        embedded = item.source_result.get("globalRiskResponse")
        if isinstance(embedded, dict):
            return _runtime_global_risk_response_from_payload(embedded, request=request, evaluated_at=item.evaluated_at)
        service = getattr(self.service, "central_risk_service", None)
        if service is None:
            return None
        try:
            return _runtime_global_risk_response_from_payload(service.evaluate(request), request=request, evaluated_at=item.evaluated_at)
        except TimeoutError:
            return fail_closed_global_risk_response(request, reason_codes=("weighted_voting.global_risk.timeout_reject",), evaluated_at=item.evaluated_at)
        except Exception:
            return fail_closed_global_risk_response(request, reason_codes=("weighted_voting.global_risk.service_failure_reject",), evaluated_at=item.evaluated_at)

    def _enforce_runtime_global_risk_actions(self, request: Any, response: WeightedVotingGlobalRiskResponse, *, evaluated_at: datetime) -> WeightedVotingGlobalRiskResponse:
        if response.action not in {"ALLOW", "REDUCE", "REJECT"}:
            return fail_closed_global_risk_response(
                request,
                reason_codes=("weighted_voting.runtime.global_risk.disallowed_action_reject",),
                evaluated_at=evaluated_at,
            )
        return response

    def _persist_runtime_order_intent(self, item: WeightedVotingRiskQueueItem, *, status: str, risk_record: dict[str, Any] | None = None) -> None:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "decisionId": item.decision_id,
            "orderIntentId": item.order_intent_id,
            "marketEventId": item.idempotency_key,
            "proposal": item.proposal.model_dump(mode="json"),
            "inventorySnapshotVersion": item.inventory_snapshot_version,
            "riskRecord": risk_record,
            "recordedAt": _now().isoformat(),
            "reasonCodes": ("weighted_voting.runtime.order_intent.persisted_before_global_risk",),
        }
        self.store.write_snapshot(f"{RUNTIME_ORDER_INTENT_PREFIX}{item.order_intent_id}", record)

    def _write_risk_record(
        self,
        item: WeightedVotingRiskQueueItem,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        final_allowed_quantity: int,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        application: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "decisionId": item.decision_id,
            "orderIntentId": item.order_intent_id,
            "marketEventId": item.idempotency_key,
            "originalProposal": item.proposal.model_dump(mode="json"),
            "globalRiskRequest": request,
            "globalRiskResponse": response,
            "globalGateApplication": application,
            "finalAllowedQuantity": final_allowed_quantity,
            "recordedAt": _now().isoformat(),
            "reasonCodes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_RISK_PREFIX}decisions.{item.order_intent_id}", record)
        return record

    def _risk_worker_running(self) -> bool:
        task = self.tasks.get("WeightedVotingRiskWorker")
        return bool(task and not task.done())

    def _enqueue_execution_from_result(self, result: dict[str, Any], *, idempotency_key: str, evaluated_at: datetime, inventory_snapshot_version: int) -> WeightedVotingExecutionQueueItem | None:
        try:
            proposal = GlobalOrderProposal.model_validate(result["globalOrderProposal"])
            global_application = AppliedGlobalGateDecision.model_validate(result["globalGateApplication"])
            local_gate_result = _local_gate_result_from_payload(result.get("gateResult") or {})
            if self.metrics.entry_creation_paused_for_reconciliation and proposal.intent == "new_entry":
                self.metrics.rejected_execution_events += 1
                self.metrics.automatic_order_creation_paused = True
                self.store.write_snapshot(
                    f"{RUNTIME_EXECUTION_PREFIX}blocked.{proposal.orderIntentId}",
                    {
                        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                        "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                        "decision_id": proposal.decisionId,
                        "order_intent_id": proposal.orderIntentId,
                        "status": "entry_creation_paused_for_reconciliation",
                        "risk_reducing_exits_allowed": self.metrics.risk_reducing_exits_allowed,
                        "recorded_at": _now().isoformat(),
                        "reason_codes": ("weighted_voting.runtime.reconciliation_blocks_new_entries",),
                    },
                )
                return None
            if proposal.intent == "new_entry":
                ready, readiness_reason = self._automatic_entry_queue_readiness()
                if ready:
                    self.metrics.automatic_order_creation_paused = False
                    self.metrics.pause_reason = None
                else:
                    status = "automatic_order_creation_paused" if self.metrics.automatic_order_creation_paused else "automatic_entry_readiness_blocked"
                    reason_code = "weighted_voting.runtime.automatic_entries_paused" if self.metrics.automatic_order_creation_paused else "weighted_voting.runtime.automatic_entry_readiness_blocked"
                    self.metrics.rejected_execution_events += 1
                    self.metrics.automatic_order_creation_paused = True
                    self.metrics.pause_reason = readiness_reason
                    self.store.write_snapshot(
                        f"{RUNTIME_EXECUTION_PREFIX}blocked.{proposal.orderIntentId}",
                        {
                            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                            "decision_id": proposal.decisionId,
                            "order_intent_id": proposal.orderIntentId,
                            "status": status,
                            "risk_reducing_exits_allowed": self.metrics.risk_reducing_exits_allowed,
                            "recorded_at": _now().isoformat(),
                            "reason_codes": (reason_code, readiness_reason),
                        },
                    )
                    return None
            item = enqueue_weighted_voting_execution_order(
                store=self.store,
                proposal=proposal,
                global_application=global_application,
                local_gate_result=local_gate_result,
                enqueued_at=evaluated_at,
                idempotency_key=idempotency_key,
                inventory_snapshot_version=inventory_snapshot_version,
            )
        except Exception as exc:
            self.metrics.rejected_execution_events += 1
            self.metrics.last_error = f"WeightedVotingExecutionQueue: {exc}"
            self.metrics.automatic_order_creation_paused = True
            self.metrics.recovery_required = True
            self.metrics.pause_reason = "weighted_voting.runtime.global_risk_or_execution_queue_unavailable"
            self._trip_circuit_breaker(
                _circuit_breaker_reason_from_exception(exc, default="weighted_voting.runtime.circuit_breaker.persistence_failed"),
                trigger="execution_queue_persistence_exception",
                details={"error": str(exc)},
            )
            return None
        if item is None:
            return None
        try:
            self.execution_queue.put_nowait(item)
            self.metrics.enqueued_orders += 1
            self.metrics.execution_queue_depth = self.execution_queue.qsize()
            self._write_execution_record(item, status="enqueued", reason_codes=("weighted_voting.runtime.execution_enqueued",))
            return item
        except asyncio.QueueFull:
            self.metrics.rejected_execution_events += 1
            self.metrics.automatic_order_creation_paused = True
            self._write_execution_record(item, status="rejected_backpressure", reason_codes=("weighted_voting.runtime.execution_queue_full",))
            return None

    def _automatic_entry_queue_readiness(self) -> tuple[bool, str]:
        control = self._runtime_control()
        readiness = self.auto_paper_readiness(control=control)
        if not control.automatic_entries_enabled and control.paper_trading_enabled:
            if readiness.blocking_reason_codes:
                return False, readiness.blocking_reason_codes[0]
            control = WeightedVotingRuntimeControl(
                paper_trading_enabled=True,
                automatic_entries_enabled=True,
                updated_at=control.updated_at,
                updated_by=control.updated_by,
                reason="weighted_voting.runtime.control.automatic_entries_armed_after_readiness",
                reason_codes=("weighted_voting.runtime.control.automatic_entries_armed_after_readiness",),
            )
            self._persist_runtime_control(control)
            readiness = self.auto_paper_readiness(control=control)
        if not readiness.entry_submission_allowed:
            reason = readiness.blocking_reason_codes[0] if readiness.blocking_reason_codes else "weighted_voting.runtime.auto_paper_readiness.fail_closed"
            return False, reason
        return True, "weighted_voting.runtime.automatic_paper_readiness_validated"

    def _runtime_control(self) -> WeightedVotingRuntimeControl:
        payload = _read_optional(self.store, RUNTIME_CONTROL_KEY)
        if payload is None:
            control = WeightedVotingRuntimeControl(updated_at=_now())
            try:
                self._persist_runtime_control(control)
            except Exception as exc:
                self.metrics.last_error = f"WeightedVotingRuntimeControlPersistence: {exc}"
                self.metrics.automatic_order_creation_paused = True
                self.metrics.recovery_required = True
            return control
        try:
            return WeightedVotingRuntimeControl.from_payload(payload)
        except Exception:
            control = WeightedVotingRuntimeControl(
                updated_at=_now(),
                reason="weighted_voting.runtime.control.corrupt_record_fail_closed",
                reason_codes=("weighted_voting.runtime.control.corrupt_record_fail_closed",),
            )
            self.metrics.automatic_order_creation_paused = True
            self.metrics.pause_reason = "weighted_voting.runtime.control.corrupt_record_fail_closed"
            try:
                self._persist_runtime_control(control)
            except Exception as exc:
                self.metrics.last_error = f"WeightedVotingRuntimeControlPersistence: {exc}"
                self.metrics.recovery_required = True
            return control

    def _persist_runtime_control(self, control: WeightedVotingRuntimeControl) -> None:
        self.store.write_snapshot(RUNTIME_CONTROL_KEY, control.as_dict())

    def _control_readiness_snapshot(self) -> dict[str, Any]:
        return self.auto_paper_readiness().as_dict()

    def _auto_paper_readiness(self, *, control: WeightedVotingRuntimeControl) -> WeightedVotingAutoPaperReadiness:
        now = _now()
        dependency_health: dict[str, dict[str, Any]] = {}
        blockers: list[str] = []
        warnings: list[str] = []

        def add(
            name: str,
            healthy: bool,
            reason_code: str,
            *,
            warning: bool = False,
            details: dict[str, Any] | None = None,
        ) -> None:
            healthy = bool(healthy)
            dependency_health[name] = {
                "healthy": healthy,
                "reasonCodes": [] if healthy else [reason_code],
                "checkedAt": now.isoformat(),
                "details": details or {},
            }
            if healthy:
                return
            if warning:
                warnings.append(reason_code)
            else:
                blockers.append(reason_code)

        settings_weights = self._settings_and_weights_valid()

        endpoint_verified = False
        if self.paper_gateway is not None:
            try:
                endpoint_verified = bool(_verify_weighted_voting_paper_endpoint(self.paper_gateway))
            except Exception:
                endpoint_verified = False
        paper_account_verified = False
        paper_account_checked = False
        if self.paper_gateway is not None:
            try:
                paper_account_verified = bool(self.paper_gateway.broker.verify_paper_account())
                paper_account_checked = True
            except Exception:
                paper_account_verified = False
                paper_account_checked = False
        paper_gateway_connected = bool(self.paper_gateway is not None and paper_account_checked)
        if not paper_account_verified:
            pass
        account = None
        try:
            account = self.account_port.account_observation(as_of=now)
        except Exception:
            account = None
        account_available = bool(account and account.available and account.account_equity is not None and account.broker_buying_power is not None)

        active_rollout_flags = self.rollout_flags or rollout_feature_flags()
        rollout_allowed = automatic_submission_allowed(
            flags=active_rollout_flags,
            validation=self.rollout_validation,
            store=self.store if self.rollout_validation is None else None,
        )
        if bool(active_rollout_flags.shadow_mode):
            rollout_allowed = False
        last_bar = self.metrics.last_bar_processed if isinstance(self.metrics.last_bar_processed, dict) else {}
        last_received = self.metrics.last_finalised_bar_received if isinstance(self.metrics.last_finalised_bar_received, dict) else {}
        bar_timestamp = _parse_optional_datetime(last_bar.get("finalisedCandleTimestamp") or last_bar.get("finalised_candle_timestamp"))
        received_timestamp = _parse_optional_datetime(last_received.get("finalisedCandleTimestamp") or last_received.get("finalised_candle_timestamp"))
        bar_phase = str(last_bar.get("sessionPhase") or last_bar.get("session_phase") or last_received.get("sessionPhase") or last_received.get("session_phase") or "")
        freshness_seconds = _optional_float(
            _first_present(
                last_bar.get("dataFreshnessSeconds"),
                last_bar.get("data_freshness_seconds"),
                last_received.get("dataFreshnessSeconds"),
                last_received.get("data_freshness_seconds"),
            )
        )
        market_data_fresh = freshness_seconds is not None and freshness_seconds <= float(self.config.max_queue_lag_seconds)
        finalized_pipeline_healthy = bool(
            bar_timestamp
            and (self.metrics.processing_lag_seconds is None or self.metrics.processing_lag_seconds <= float(self.config.max_queue_lag_seconds))
            and (received_timestamp is None or bar_timestamp >= received_timestamp)
        )
        session_evidence = self._authoritative_session_evidence(bar_timestamp) if bar_timestamp else {}
        exchange_session_open = bool(session_evidence.get("sessionAllowed"))
        inside_entry_window = bool(session_evidence.get("insideEntryDecisionWindow"))

        inventory = None
        inventory_loaded = False
        try:
            inventory = self.inventory_repository.current_snapshot(now=now)
            inventory_loaded = True
        except Exception:
            inventory = None
            inventory_loaded = False
        allocated_positive = bool(inventory and float(inventory.allocated_capital or 0.0) > 0.0)
        daily_loss_ok = bool(inventory and float(inventory.daily_loss_percent or 0.0) < float(self.weighted_config.maximum_weighted_daily_loss_percent))
        daily_trade_ok = bool(inventory and int(inventory.daily_trade_count or 0) < int(self.weighted_config.maximum_weighted_daily_trades))
        remaining_risk_positive = bool(inventory and float(inventory.remaining_daily_risk or 0.0) > 0.0 and float(inventory.remaining_capital_partition or 0.0) > 0.0)
        inventory_reconciled = bool(inventory_loaded and self.metrics.inventory_reconciled and not self.metrics.entry_creation_paused_for_reconciliation)
        last_reconciliation = self.metrics.last_reconciliation if isinstance(self.metrics.last_reconciliation, dict) else {}
        broker_orders_reconciled = bool(
            inventory_reconciled
            and last_reconciliation
            and last_reconciliation.get("inventoryReconciled") is True
            and last_reconciliation.get("entriesPaused") is not True
        )
        no_unprotected_position = False
        try:
            no_unprotected_position = len(self._detect_unprotected_positions()) == 0
        except Exception:
            no_unprotected_position = False
        no_pending_recovery = bool(not self.metrics.recovery_required)
        no_algorithm_halt = bool(not self.metrics.paused and not self.metrics.circuit_breaker_open)
        global_risk = self.metrics.last_global_risk_response if isinstance(self.metrics.last_global_risk_response, dict) else {}
        global_action = str(global_risk.get("action") or "").upper()
        no_global_halt = global_action in {"ALLOW", "REDUCE_QUANTITY"}
        runtime_supervisor_healthy = bool(
            self.metrics.supervisor_started
            and not self.metrics.paused
            and not self.metrics.recovery_required
            and not self.metrics.circuit_breaker_open
            and not self.metrics.last_error
        )

        add("weighted_voting_enabled", not self.metrics.paused, "weighted_voting.runtime.auto_paper.weighted_voting_disabled")
        add("paper_trading_enabled", control.paper_trading_enabled, "weighted_voting.runtime.control.paper_trading_disabled")
        add("automatic_entries_enabled", control.automatic_entries_enabled, "weighted_voting.runtime.control.automatic_entries_disabled")
        add("broker_endpoint_is_paper", endpoint_verified, "weighted_voting.runtime.control.paper_endpoint_unverified")
        add("paper_account_verified", paper_account_verified, "weighted_voting.runtime.control.paper_account_unverified")
        add("paper_account_snapshot_available", account_available, "weighted_voting.runtime.control.paper_account_snapshot_unavailable")
        add("paper_gateway_connected", paper_gateway_connected, "weighted_voting.runtime.paper_gateway_unavailable")
        add("automatic_submission_rollout_passed", rollout_allowed, "weighted_voting.rollout.auto_submit_blocked")
        add("runtime_supervisor_healthy", runtime_supervisor_healthy, "weighted_voting.runtime.supervisor_not_healthy", details={"supervisorStarted": self.metrics.supervisor_started, "lastError": self.metrics.last_error})
        add("finalized_bar_pipeline_healthy", finalized_pipeline_healthy, "weighted_voting.runtime.finalized_bar_pipeline_unhealthy", details={"lastBarProcessed": last_bar, "lastFinalisedBarReceived": last_received})
        add("market_data_fresh", market_data_fresh, "weighted_voting.runtime.market_data_stale_or_unknown", details={"dataFreshnessSeconds": freshness_seconds, "maxFreshnessSeconds": self.config.max_queue_lag_seconds})
        add("exchange_session_open", exchange_session_open, "weighted_voting.runtime.exchange_session_closed_or_unknown", details={"sessionPhase": session_evidence.get("phase") or bar_phase, "barTimestamp": bar_timestamp.isoformat() if bar_timestamp else None, "sessionEvidence": session_evidence})
        add("inside_entry_decision_window", inside_entry_window, "weighted_voting.runtime.outside_entry_decision_window", details={"barTimestamp": bar_timestamp.isoformat() if bar_timestamp else None, "decisionSessionWindow": self.weighted_config.decision_session_window, "sessionEvidence": session_evidence})
        add("settings_loaded_and_valid", bool(settings_weights["settingsValid"]), "weighted_voting.runtime.control.settings_invalid", details={"settingsVersion": settings_weights.get("settingsVersion")})
        add("active_weights_loaded_and_frozen", bool(settings_weights["weightsFrozen"]), "weighted_voting.runtime.control.active_weights_invalid_or_unfrozen", details={"weightVersion": settings_weights.get("weightVersion"), "weightStatus": settings_weights.get("weightStatus")})
        add("algorithm_capital_allocation_positive", allocated_positive, "weighted_voting.runtime.control.inventory_capital_unallocated")
        add("inventory_loaded", inventory_loaded, "weighted_voting.runtime.inventory_unavailable")
        add("inventory_reconciled", inventory_reconciled, "weighted_voting.runtime.control.inventory_not_reconciled")
        add("broker_orders_reconciled", broker_orders_reconciled, "weighted_voting.runtime.control.broker_orders_not_reconciled")
        add("no_unprotected_position", no_unprotected_position, "weighted_voting.runtime.recovery.protective_order_restore_required")
        add("no_pending_recovery", no_pending_recovery, "weighted_voting.runtime.control.recovery_required")
        add("no_algorithm_halt", no_algorithm_halt, "weighted_voting.runtime.control.algorithm_halt_active")
        add("no_global_halt", no_global_halt, "weighted_voting.runtime.control.global_halt_or_unknown")
        add("daily_loss_limit_not_reached", daily_loss_ok, "weighted_voting.runtime.control.daily_loss_limit_reached", details={"dailyLossPercent": inventory.daily_loss_percent if inventory else None})
        add("daily_trade_limit_not_reached", daily_trade_ok, "weighted_voting.runtime.control.daily_trade_limit_reached", details={"dailyTradeCount": inventory.daily_trade_count if inventory else None})
        add("remaining_algorithm_risk_positive", remaining_risk_positive, "weighted_voting.runtime.control.remaining_algorithm_risk_exhausted")

        blocking_codes = tuple(dict.fromkeys(blockers))
        warning_codes = tuple(dict.fromkeys(warnings))
        ready = len(blocking_codes) == 0
        risk_reducing_exits_allowed = bool(
            endpoint_verified
            and paper_account_verified
            and paper_gateway_connected
            and inventory_loaded
            and no_pending_recovery
            and no_algorithm_halt
        )
        runtime_status = _derive_auto_paper_runtime_status(
            control=control,
            ready=ready,
            warnings=warning_codes,
            dependency_health=dependency_health,
            metrics=self.metrics,
            rollout_flags=self.rollout_flags,
        )
        return WeightedVotingAutoPaperReadiness(
            ready=ready,
            entry_submission_allowed=ready,
            risk_reducing_exits_allowed=risk_reducing_exits_allowed,
            blocking_reason_codes=blocking_codes,
            warning_reason_codes=warning_codes,
            checked_at=now,
            dependency_health=dependency_health,
            runtime_status=runtime_status,
        )

    def _settings_and_weights_valid(self) -> dict[str, Any]:
        try:
            settings = self._active_effective_settings()
            settings_valid = bool(settings.settings_version and settings.configuration_hash)
        except Exception:
            settings = None
            settings_valid = False
        try:
            weights = self.service.active_weight_state()
            weights_valid = bool(weights.weight_version and weights.input_data_hash and weights.output_hash)
            weights_frozen = weights_valid and str(getattr(weights.state_status, "value", weights.state_status)) in {
                "UNSEEDED_EQUAL_WEIGHTS",
                "BACKTEST_SEEDED",
                "FROZEN_INSUFFICIENT_DATA",
                "LIVE_ADAPTING",
            }
        except Exception:
            weights = None
            weights_valid = False
            weights_frozen = False
        return {
            "settingsValid": settings_valid,
            "weightsValid": weights_valid,
            "weightsFrozen": weights_frozen,
            "settingsVersion": getattr(settings, "settings_version", None),
            "weightVersion": getattr(weights, "weight_version", None),
            "weightStatus": str(getattr(getattr(weights, "state_status", None), "value", getattr(weights, "state_status", None))),
        }

    def _disable_paper_entries(self, *, updated_at: datetime, reason: str) -> dict[str, Any]:
        cancelled_queue_items = []
        for key, payload in _store_items(self.store):
            if not key.startswith(f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue."):
                continue
            if str(payload.get("algorithmId") or payload.get("algorithm_id") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
                continue
            status = str(payload.get("status") or "")
            if status not in {"PENDING", "PENDING_SUBMISSION", ""}:
                continue
            command = payload.get("command") if isinstance(payload.get("command"), dict) else {}
            proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
            if str(command.get("algorithmId") or command.get("algorithm_id") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
                continue
            if str(proposal.get("algorithmId") or proposal.get("algorithm_id") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
                continue
            if str(proposal.get("intent") or "new_entry") != "new_entry":
                continue
            client_order_id = str(command.get("clientOrderId") or payload.get("clientOrderId") or key.rsplit(".", 1)[-1])
            updated = {
                **payload,
                "status": "CANCELLED",
                "cancelledAt": updated_at.isoformat(),
                "reasonCodes": list(dict.fromkeys([*(payload.get("reasonCodes") or payload.get("reason_codes") or ()), "weighted_voting.runtime.control.unsubmitted_entry_cancelled", reason])),
            }
            self.store.write_snapshot(key, updated)
            self.store.write_snapshot(
                f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.{client_order_id}.latest",
                {
                    "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                    "clientOrderId": client_order_id,
                    "orderIntentId": command.get("orderIntentId"),
                    "decisionId": command.get("decisionId"),
                    "status": "CANCELLED",
                    "recordedAt": updated_at.isoformat(),
                    "reasonCodes": ("weighted_voting.runtime.control.unsubmitted_entry_cancelled", reason),
                },
            )
            cancelled_queue_items.append(client_order_id)
        stale_cancellations = []
        if self.paper_gateway is not None:
            try:
                stale_cancellations = [item.model_dump(mode="json") for item in self.paper_gateway.cancel_stale_orders(evaluated_at=updated_at)]
            except Exception as exc:
                self.metrics.last_error = f"WeightedVotingControlCancelStale: {exc}"
        return {
            "cancelledUnsubmittedEntryClientOrderIds": cancelled_queue_items,
            "staleWorkingOrderCancellations": stale_cancellations,
            "riskReducingExitsEnabled": True,
            "protectiveOrdersEnabled": True,
            "reconciliationContinues": True,
        }

    def _write_runtime_control_audit(
        self,
        *,
        prior: WeightedVotingRuntimeControl,
        control: WeightedVotingRuntimeControl,
        readiness: dict[str, Any],
        transition: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "priorControl": prior.as_dict(),
            "newControl": control.as_dict(),
            "readiness": readiness,
            "transition": transition,
            "recordedAt": _now().isoformat(),
            "reasonCodes": list(control.reason_codes),
        }
        self.store.write_snapshot(f"{RUNTIME_CONTROL_AUDIT_PREFIX}{control.updated_at.isoformat()}.{_hash_payload(record)}", record)
        return record

    def _start_worker(self, worker: WeightedVotingRuntimeWorker) -> None:
        self.tasks[worker.worker_id] = asyncio.create_task(self._run_worker(worker), name=worker.worker_id)

    async def _run_worker(self, worker: WeightedVotingRuntimeWorker) -> None:
        while not self.stop_event.is_set():
            try:
                await worker.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures = self.metrics.worker_failures.get(worker.worker_id, 0) + 1
                self.metrics.worker_failures[worker.worker_id] = failures
                self.metrics.last_error = f"{worker.worker_id}: {exc}"
                if failures >= self.config.worker_restart_failure_threshold:
                    self._trip_circuit_breaker(
                        "weighted_voting.runtime.circuit_breaker.repeated_worker_crashes",
                        trigger="worker_crash_threshold",
                        details={"workerId": worker.worker_id, "failures": failures, "threshold": self.config.worker_restart_failure_threshold, "error": str(exc)},
                    )
                    self._write_status("degraded", ("weighted_voting.runtime.worker_failure_threshold_pause",))
                    await asyncio.sleep(self.config.maintenance_interval_seconds)
                    return
                self.metrics.worker_restarts[worker.worker_id] = self.metrics.worker_restarts.get(worker.worker_id, 0) + 1
                await asyncio.sleep(0)

    def _write_event_record(
        self,
        event: WeightedVotingFinalisedBarEvent,
        status: str,
        idempotency_key: str | None,
        reason_codes: tuple[str, ...],
        *,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            **event.as_dict(),
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "idempotency_key": idempotency_key,
            "decision_id": decision_id,
            "queue_depth": self.event_bus.depth(),
            "recorded_at": _now().isoformat(),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "reason_codes": reason_codes,
        }
        key = _event_key(idempotency_key or event.event_id)
        self.store.write_snapshot(key, record)
        self._write_status("running" if self.metrics.supervisor_started else "stopped", reason_codes)
        return record

    def _write_duplicate_event_record(
        self,
        event: WeightedVotingFinalisedBarEvent,
        market_event_id: str,
        reason_codes: tuple[str, ...],
    ) -> dict[str, Any]:
        record = {
            **event.as_dict(),
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": "duplicate_noop",
            "idempotency_key": market_event_id,
            "market_event_id": market_event_id,
            "queue_depth": self.event_bus.depth(),
            "recorded_at": _now().isoformat(),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "reason_codes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_EVENT_PREFIX}duplicate.{market_event_id}.{_hash_payload(record)}", record)
        self._write_status("running" if self.metrics.supervisor_started else "stopped", reason_codes)
        return record

    def _write_execution_record(
        self,
        item: WeightedVotingExecutionQueueItem,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "queue_id": item.queue_id,
            "idempotency_key": item.idempotency_key,
            "decision_id": item.command.decision_id,
            "order_intent_id": item.command.order_intent_id,
            "client_order_id": item.command.client_order_id,
            "status": status,
            "result": result,
            "recorded_at": _now().isoformat(),
            "automatic_order_creation_paused": self.metrics.automatic_order_creation_paused,
            "reason_codes": reason_codes,
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_PREFIX}{item.command.client_order_id}.{status}", record)
        self._write_status("running" if self.metrics.supervisor_started else "stopped", reason_codes)
        return record

    def _read_execution_outbox_record(self, order_intent_id: str) -> dict[str, Any] | None:
        return _read_optional(self.store, f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}{order_intent_id}")

    def _write_execution_outbox_from_risk_item(
        self,
        item: WeightedVotingRiskQueueItem,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        risk_record: dict[str, Any] | None = None,
        risk_request_id: str | None = None,
        final_allowed_quantity: int | None = None,
    ) -> dict[str, Any]:
        prior = self._read_execution_outbox_record(item.order_intent_id) or {}
        record = {
            **prior,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "previousStatus": prior.get("status"),
            "mode": "PAPER",
            "liveTradingEnabled": False,
            "paperOnly": True,
            "decisionId": item.decision_id,
            "orderIntentId": item.order_intent_id,
            "marketEventId": item.idempotency_key,
            "riskRequestId": risk_request_id or prior.get("riskRequestId"),
            "finalAllowedQuantity": final_allowed_quantity if final_allowed_quantity is not None else prior.get("finalAllowedQuantity"),
            "originalProposal": item.proposal.model_dump(mode="json"),
            "riskRecord": risk_record if risk_record is not None else prior.get("riskRecord"),
            "executionQueueItem": prior.get("executionQueueItem"),
            "submissionAttemptCount": int(prior.get("submissionAttemptCount") or 0),
            "attemptRecords": list(prior.get("attemptRecords") or ()),
            "recordedAt": _now().isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}{item.order_intent_id}", record)
        return record

    def _write_execution_outbox_record(
        self,
        item: WeightedVotingExecutionQueueItem,
        *,
        status: str,
        reason_codes: tuple[str, ...],
        risk_record: dict[str, Any] | None = None,
        risk_request_id: str | None = None,
        final_allowed_quantity: int | None = None,
        result: dict[str, Any] | None = None,
        broker_lookup: dict[str, Any] | None = None,
        submission_attempt_no: int | None = None,
    ) -> dict[str, Any]:
        prior = self._read_execution_outbox_record(item.command.order_intent_id) or {}
        attempt_count = int(prior.get("submissionAttemptCount") or 0)
        if submission_attempt_no is not None:
            attempt_count = max(attempt_count, int(submission_attempt_no))
        record = {
            **prior,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "previousStatus": prior.get("status"),
            "mode": "PAPER",
            "liveTradingEnabled": False,
            "paperOnly": True,
            "decisionId": item.command.decision_id,
            "orderIntentId": item.command.order_intent_id,
            "clientOrderId": item.command.client_order_id,
            "deterministicClientOrderId": item.command.client_order_id,
            "queueId": item.queue_id,
            "marketEventId": item.idempotency_key,
            "riskRequestId": risk_request_id or prior.get("riskRequestId"),
            "finalAllowedQuantity": final_allowed_quantity if final_allowed_quantity is not None else prior.get("finalAllowedQuantity"),
            "orderType": item.command.order_type,
            "limitPrice": item.command.limit_price,
            "stopPrice": item.command.stop_price,
            "quantity": item.command.quantity,
            "side": item.command.side,
            "expiresAt": item.command.expires_at.isoformat(),
            "inventorySnapshotVersion": item.inventory_snapshot_version,
            "executionQueueItem": item.as_dict(),
            "riskRecord": risk_record if risk_record is not None else prior.get("riskRecord"),
            "result": result if result is not None else prior.get("result"),
            "brokerLookup": broker_lookup if broker_lookup is not None else prior.get("brokerLookup"),
            "submissionAttemptCount": attempt_count,
            "lastSubmissionAttemptNo": submission_attempt_no if submission_attempt_no is not None else prior.get("lastSubmissionAttemptNo"),
            "attemptRecords": list(prior.get("attemptRecords") or ()),
            "recordedAt": _now().isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}{item.command.order_intent_id}", record)
        return record

    def _write_execution_outbox_from_payload(
        self,
        payload: dict[str, Any],
        *,
        status: str,
        reason_codes: tuple[str, ...],
    ) -> dict[str, Any]:
        order_intent_id = str(payload.get("orderIntentId") or payload.get("order_intent_id") or _hash_payload(payload))
        prior = self._read_execution_outbox_record(order_intent_id) or payload
        record = {
            **prior,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "status": status,
            "previousStatus": prior.get("status"),
            "mode": "PAPER",
            "liveTradingEnabled": False,
            "paperOnly": True,
            "recordedAt": _now().isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}{order_intent_id}", record)
        return record

    def _append_execution_attempt(
        self,
        item: WeightedVotingExecutionQueueItem,
        *,
        attempt_no: int,
        status: str,
        reason_codes: tuple[str, ...],
        result: dict[str, Any] | None = None,
        broker_lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtimeVersion": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "attemptNo": int(attempt_no),
            "status": status,
            "mode": "PAPER",
            "liveTradingEnabled": False,
            "paperOnly": True,
            "decisionId": item.command.decision_id,
            "orderIntentId": item.command.order_intent_id,
            "clientOrderId": item.command.client_order_id,
            "queueId": item.queue_id,
            "brokerLookup": broker_lookup,
            "result": result,
            "recordedAt": _now().isoformat(),
            "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        }
        key = f"{RUNTIME_EXECUTION_OUTBOX_ATTEMPT_PREFIX}{item.command.client_order_id}.{attempt_no}.{status}.{_hash_payload(record)}"
        self.store.write_snapshot(key, record)
        outbox = self._read_execution_outbox_record(item.command.order_intent_id) or {}
        attempt_records = list(outbox.get("attemptRecords") or ())
        attempt_records.append(
            {
                "attemptNo": int(attempt_no),
                "status": status,
                "recordKey": key,
                "recordedAt": record["recordedAt"],
                "reasonCodes": record["reasonCodes"],
            }
        )
        outbox.update(
            {
                "attemptRecords": attempt_records,
                "submissionAttemptCount": max(int(outbox.get("submissionAttemptCount") or 0), int(attempt_no)),
                "lastSubmissionAttemptNo": int(attempt_no),
                "recordedAt": _now().isoformat(),
            }
        )
        self.store.write_snapshot(f"{RUNTIME_EXECUTION_OUTBOX_PREFIX}{item.command.order_intent_id}", outbox)
        return record

    def _next_execution_attempt_number(self, order_intent_id: str) -> int:
        outbox = self._read_execution_outbox_record(order_intent_id) or {}
        attempts = [int(record.get("attemptNo") or 0) for record in outbox.get("attemptRecords") or () if isinstance(record, dict)]
        attempts.append(int(outbox.get("submissionAttemptCount") or 0))
        return max(attempts) + 1

    def _outbox_requires_broker_lookup_before_submit(self, outbox: dict[str, Any] | None) -> bool:
        if not isinstance(outbox, dict):
            return False
        status = str(outbox.get("status") or "")
        attempts = int(outbox.get("submissionAttemptCount") or 0)
        return attempts > 0 or status in {"SUBMITTING", "RECONCILIATION_REQUIRED"}

    def _broker_lookup_for_retry(self, item: WeightedVotingExecutionQueueItem, *, reason_code: str) -> dict[str, Any] | None:
        if self.paper_gateway is None or not _verify_weighted_voting_paper_endpoint(self.paper_gateway):
            return None
        refresher = getattr(self.paper_gateway.broker, "refresh_order", None)
        if not callable(refresher):
            return {
                "status": "RECONCILIATION_REQUIRED",
                "clientOrderId": item.command.client_order_id,
                "reasonCodes": ("weighted_voting.runtime.execution_outbox.broker_lookup_unavailable", reason_code),
            }
        try:
            broker_state = refresher(item.command.client_order_id)
        except Exception as exc:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "clientOrderId": item.command.client_order_id,
                "error": str(exc),
                "reasonCodes": ("weighted_voting.runtime.execution_outbox.broker_lookup_failed", reason_code),
            }
        if broker_state is None:
            return None
        payload = _runtime_model_payload(broker_state)
        payload.setdefault("clientOrderId", item.command.client_order_id)
        payload.setdefault("reasonCodes", ("weighted_voting.runtime.execution_outbox.broker_lookup_found_existing_order", reason_code))
        if payload.get("algorithmId") not in {None, WEIGHTED_VOTING_ALGORITHM_ID}:
            payload["status"] = "RECONCILIATION_REQUIRED"
            payload["reasonCodes"] = ("weighted_voting.runtime.execution_outbox.broker_lookup_cross_algorithm_conflict", reason_code)
        if payload.get("orderIntentId") not in {None, item.command.order_intent_id}:
            payload["status"] = "RECONCILIATION_REQUIRED"
            payload["reasonCodes"] = ("weighted_voting.runtime.execution_outbox.broker_lookup_intent_mismatch", reason_code)
        return payload

    def _write_checkpoint(self, event: WeightedVotingFinalisedBarEvent, idempotency_key: str, *, decision_id: str | None, status: str) -> None:
        checkpoint = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
            "symbol": event.symbol.upper(),
            "finalised_candle_timestamp": event.finalised_candle_timestamp.isoformat(),
            "data_manifest_hash": event.data_manifest_hash,
            "bar_start": event.bar_start.isoformat() if event.bar_start else None,
            "bar_end": event.bar_end.isoformat() if event.bar_end else None,
            "source_sequence": event.source_sequence,
            "idempotency_key": idempotency_key,
            "decision_id": decision_id,
            "status": status,
            "updated_at": _now().isoformat(),
            "reason_codes": ("weighted_voting.runtime.checkpoint_persisted",),
        }
        self.store.write_snapshot(_checkpoint_key(event.symbol), checkpoint)
        self.metrics.last_event_timestamp_by_symbol[event.symbol.upper()] = checkpoint["finalised_candle_timestamp"]
        self.metrics.last_checkpoint_by_symbol[event.symbol.upper()] = idempotency_key

    def _write_status(self, status: str, reason_codes: tuple[str, ...]) -> None:
        self.store.write_snapshot(
            RUNTIME_STATUS_KEY,
            {
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "runtime_version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
                "status": status,
                "health": self.health(),
                "updated_at": _now().isoformat(),
                "reason_codes": reason_codes,
            },
        )


_DEFAULT_SUPERVISOR: WeightedVotingRuntimeSupervisor | None = None


def get_weighted_voting_runtime_supervisor() -> WeightedVotingRuntimeSupervisor:
    global _DEFAULT_SUPERVISOR
    if _DEFAULT_SUPERVISOR is None:
        _DEFAULT_SUPERVISOR = WeightedVotingRuntimeSupervisor()
    return _DEFAULT_SUPERVISOR


async def publish_weighted_voting_finalised_bar_event(payload: dict[str, Any], *, replay_recovery: bool = False) -> bool:
    supervisor = get_weighted_voting_runtime_supervisor()
    snapshot = build_weighted_voting_market_snapshot(payload)
    bar_start = snapshot.data_timestamp
    bar_end = bar_start + timedelta(minutes=1)
    data_source = str(payload.get("data_source") or payload.get("dataSource") or "weighted_voting.runtime.payload_publisher")
    source_sequence = _safe_int(payload.get("source_sequence") or payload.get("sourceSequence") or int(bar_end.timestamp() // 60))
    event = WeightedVotingFinalisedBarEvent(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=snapshot.symbol,
        finalised_candle_timestamp=bar_start,
        bar_start=bar_start,
        bar_end=bar_end,
        open=snapshot.one_minute_candles[-1].open,
        high=snapshot.one_minute_candles[-1].high,
        low=snapshot.one_minute_candles[-1].low,
        close=snapshot.one_minute_candles[-1].close,
        volume=int(snapshot.one_minute_candles[-1].volume),
        data_source=data_source,
        source_sequence=source_sequence,
        finalized=True,
        data_manifest_hash=str(payload.get("data_manifest_hash") or payload.get("dataManifestHash") or snapshot.data_manifest_hash),
        market_payload=payload,
        published_at=_now(),
        replay_recovery=replay_recovery,
    )
    return await supervisor.publish_finalised_bar(event)


def weighted_voting_market_event_id(
    *,
    symbol: str,
    bar_end: datetime,
    source: str,
    source_sequence: int,
) -> str:
    payload = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "symbol": symbol.upper(),
        "bar_end": _require_utc_datetime(bar_end).isoformat(),
        "source": source,
        "source_sequence": int(source_sequence),
    }
    return "weighted_voting.market_event." + _hash_payload(payload)


def weighted_voting_decision_idempotency_key(
    *,
    market_event_id: str,
    settings_version: str,
    weight_version: str,
    inventory_version: int | str,
    decision_kernel_version: str = WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
) -> str:
    payload = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "market_event_id": market_event_id,
        "settings_version": settings_version,
        "weight_version": weight_version,
        "inventory_version": str(inventory_version),
        "decision_kernel_version": decision_kernel_version,
    }
    return "weighted_voting.decision_idempotency." + _hash_payload(payload)


def weighted_voting_order_intent_idempotency_key(
    *,
    decision_id: str,
    intent_revision: int | str = 1,
) -> str:
    payload = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "decision_id": decision_id,
        "intent_revision": str(intent_revision),
    }
    return "weighted_voting.order_intent_idempotency." + _hash_payload(payload)


def weighted_voting_bar_event_idempotency_key(
    *,
    symbol: str,
    finalised_candle_timestamp: datetime,
    data_manifest_hash: str,
    settings_version: str,
    weight_version: str,
) -> str:
    bar_end = _require_utc_datetime(finalised_candle_timestamp) + timedelta(minutes=1)
    return weighted_voting_market_event_id(
        symbol=symbol,
        bar_end=bar_end,
        source="legacy.weighted_voting_bar_event_idempotency_key",
        source_sequence=int(bar_end.timestamp() // 60),
    )


def _ensure_weighted_voting_local_paper_session(
    *,
    store: WeightedVotingStateStore,
    inventory_repository: WeightedVotingInventoryRepository,
    initial_capital: float,
) -> None:
    try:
        store.read_snapshot(CURRENT_SNAPSHOT_KEY)
        return
    except KeyError:
        pass
    timestamp = datetime.now(UTC)
    inventory_repository.initialize_session(
        session_date=timestamp.date(),
        allocated_capital=float(initial_capital),
        cash_available=float(initial_capital),
        occurred_at=timestamp,
        expected_snapshot_version=0,
        event_id="weighted_voting.local_paper.initial_capital.session",
    )


def runtime_supervisor_status() -> dict[str, Any]:
    return {
        "version": WEIGHTED_VOTING_RUNTIME_SUPERVISOR_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "startsWithBackend": True,
        "dashboardRequired": False,
        "boundedQueues": True,
        "boundedExecutionQueue": True,
        "sequentialPerSymbol": True,
        "dashboardSubmitsOrders": False,
        "automaticSubmissionRolloutGated": True,
        "idempotencyFields": (
            "algorithm_id",
            "symbol",
            "finalised_candle_timestamp",
            "data_manifest_hash",
            "settings_version",
            "weight_version",
        ),
        "workers": (
            "WeightedVotingBarEventWorker",
            "WeightedVotingDecisionWorker",
            "WeightedVotingRiskWorker",
            "WeightedVotingExecutionWorker",
            "WeightedVotingReconciliationWorker",
            "WeightedVotingPositionManager",
            "WeightedVotingDailyUpdateWorker",
            "WeightedVotingRecoveryWorker",
            "WeightedVotingHeartbeatWorker",
        ),
        "reasonCodes": ("weighted_voting.runtime_supervisor.contract.ready",),
    }


def _market_holidays(year: int) -> set[date]:
    return {
        date(year, 1, 1),
        date(year, 7, 4),
        date(year, 12, 25),
    }


def _event_key(idempotency_key: str) -> str:
    return f"{RUNTIME_EVENT_PREFIX}{idempotency_key}"


def _checkpoint_key(symbol: str) -> str:
    return f"{RUNTIME_CHECKPOINT_PREFIX}{symbol.upper()}"


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _parse_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _require_utc_datetime(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("UTC datetime is required")
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_degradation_reasons(
    event: WeightedVotingFinalisedBarEvent,
    snapshot: Any,
    *,
    max_lag_seconds: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    freshness = event.market_payload.get("data_freshness_seconds")
    if freshness is not None and _safe_float(freshness) > max_lag_seconds:
        reasons.append("weighted_voting.runtime.recovery.stale_market_data_feed")
    quote_timestamp = _parse_optional_datetime(event.market_payload.get("quote_timestamp") or event.market_payload.get("quoteTimestamp"))
    if quote_timestamp is not None:
        quote_lag = abs((snapshot.data_timestamp - quote_timestamp).total_seconds())
        if quote_lag > max_lag_seconds:
            reasons.append("weighted_voting.runtime.recovery.stale_quote_feed")
    if event.finalised_candle_timestamp > _now() + timedelta(seconds=max_lag_seconds):
        reasons.append("weighted_voting.runtime.recovery.clock_skew_future_bar")
    if event.published_at > _now() + timedelta(seconds=max_lag_seconds):
        reasons.append("weighted_voting.runtime.recovery.clock_skew_future_publish")
    return tuple(dict.fromkeys(reasons))


def _valid_weighted_voting_candles(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    feed: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid = []
    invalid = []
    for row in rows:
        candidate = {**dict(row), "symbol": symbol.upper(), "timeframe": timeframe, "feed": feed, "provider": str(row.get("provider") or "market_data")}
        reasons = _weighted_voting_candle_rejection_reasons(candidate, now=now)
        if reasons:
            invalid.append({"timestamp": candidate.get("timestamp"), "reasonCodes": reasons})
        else:
            valid.append(_normalize_weighted_voting_candle(candidate))
    return sorted(valid, key=lambda item: item["timestamp"]), invalid


def _weighted_voting_candle_rejection_reasons(candle: dict[str, Any], *, now: datetime) -> tuple[str, ...]:
    reasons: list[str] = []
    timestamp = _parse_optional_datetime(candle.get("timestamp"))
    if timestamp is None:
        reasons.append("weighted_voting.market_data.timestamp_required")
    elif timestamp > now:
        reasons.append("weighted_voting.market_data.future_timestamp_rejected")
    if str(candle.get("symbol") or "").upper() != "SPY":
        reasons.append("weighted_voting.market_data.non_spy_candle_rejected")
    if str(candle.get("timeframe") or "") != "1Min":
        reasons.append("weighted_voting.market_data.non_one_minute_candle_rejected")
    if candle.get("finalized") is False or candle.get("finalised") is False:
        reasons.append("weighted_voting.market_data.partial_candle_rejected")
    try:
        open_, high, low, close = (float(candle[key]) for key in ("open", "high", "low", "close"))
        volume = float(candle.get("volume") or 0)
    except Exception:
        return tuple(dict.fromkeys([*reasons, "weighted_voting.market_data.ohlcv_required"]))
    if not all(math.isfinite(value) for value in (open_, high, low, close, volume)):
        reasons.append("weighted_voting.market_data.non_finite_ohlcv_rejected")
    if min(open_, high, low, close) <= 0 or volume < 0:
        reasons.append("weighted_voting.market_data.invalid_ohlcv_rejected")
    if high < max(open_, close, low) or low > min(open_, close, high):
        reasons.append("weighted_voting.market_data.invalid_ohlc_geometry_rejected")
    return tuple(dict.fromkeys(reasons))


def _normalize_weighted_voting_candle(candle: dict[str, Any]) -> dict[str, Any]:
    timestamp = _require_utc_datetime(_parse_optional_datetime(candle["timestamp"]))
    return {
        "provider": str(candle.get("provider") or "market_data"),
        "feed": str(candle.get("feed") or "iex"),
        "symbol": str(candle.get("symbol") or "SPY").upper(),
        "timeframe": str(candle.get("timeframe") or "1Min"),
        "timestamp": timestamp.isoformat(),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": int(float(candle.get("volume") or 0)),
        "trade_count": int(candle["trade_count"]) if candle.get("trade_count") is not None else None,
        "vwap": float(candle["vwap"]) if candle.get("vwap") is not None else None,
        "finalized": True,
    }


def _is_weighted_voting_finalized_bar(candle: dict[str, Any], *, now: datetime, finalization_delay_seconds: int) -> bool:
    timestamp = _parse_optional_datetime(candle.get("timestamp"))
    if timestamp is None:
        return False
    bar_end = timestamp + timedelta(minutes=1)
    return bar_end <= now - timedelta(seconds=max(0, finalization_delay_seconds))


def _weighted_voting_history_quality(history: list[dict[str, Any]], *, bar_start: datetime) -> dict[str, Any]:
    if not history:
        return {"status": "SEQUENCE_EMPTY", "reasonCodes": ("weighted_voting.market_data.sequence_empty",)}
    timestamps = [_require_utc_datetime(_parse_optional_datetime(row.get("timestamp"))) for row in history]
    if timestamps[-1] != bar_start:
        return {"status": "LATEST_BAR_MISMATCH", "reasonCodes": ("weighted_voting.market_data.latest_bar_mismatch",)}
    gaps = []
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != timedelta(minutes=1):
            gaps.append({"previous": previous.isoformat(), "current": current.isoformat()})
    if gaps:
        return {"status": "SEQUENCE_GAP", "gaps": gaps, "reasonCodes": ("weighted_voting.market_data.sequence_gap_detected",)}
    return {"status": "OK", "reasonCodes": ("weighted_voting.market_data.sequence_ok",)}


def _weighted_voting_event_from_candle(
    candle: dict[str, Any],
    *,
    history: list[dict[str, Any]],
    received_at: datetime,
    source_sequence: int,
    source_authority: str,
) -> WeightedVotingFinalisedBarEvent:
    bar_start = _require_utc_datetime(_parse_optional_datetime(candle["timestamp"]))
    bar_end = bar_start + timedelta(minutes=1)
    one_minute = [_candle_payload(row) for row in history]
    five_minute = _aggregate_finalized_candles(one_minute, minutes=5, timeframe="5Min", end=bar_start)
    fifteen_minute = _aggregate_finalized_candles(one_minute, minutes=15, timeframe="15Min", end=bar_start)
    data_manifest_hash = _hash_payload(
        {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "symbol": candle["symbol"],
            "barStart": bar_start,
            "barEnd": bar_end,
            "ohlcv": {key: candle[key] for key in ("open", "high", "low", "close", "volume")},
            "dataSource": source_authority,
            "sourceSequence": source_sequence,
            "historyStart": one_minute[0]["timestamp"] if one_minute else None,
            "historyEnd": one_minute[-1]["timestamp"] if one_minute else None,
        }
    )
    payload = {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "symbol": str(candle["symbol"]).upper(),
        "data_timestamp": bar_start.isoformat(),
        "bar_start": bar_start.isoformat(),
        "bar_end": bar_end.isoformat(),
        "data_source": source_authority,
        "source_sequence": source_sequence,
        "data_manifest_hash": data_manifest_hash,
        "candles": one_minute,
        "one_minute_candles": one_minute,
        "five_minute_candles": five_minute,
        "fifteen_minute_candles": fifteen_minute,
        "bid": max(0.01, float(candle["close"]) - 0.01),
        "ask": float(candle["close"]) + 0.01,
        "session_phase": _session_phase_for_bar_start(bar_start),
        "data_freshness_seconds": max(0.0, (received_at - bar_end).total_seconds()),
    }
    return WeightedVotingFinalisedBarEvent(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=str(candle["symbol"]).upper(),
        finalised_candle_timestamp=bar_start,
        bar_start=bar_start,
        bar_end=bar_end,
        open=float(candle["open"]),
        high=float(candle["high"]),
        low=float(candle["low"]),
        close=float(candle["close"]),
        volume=int(candle["volume"]),
        data_source=source_authority,
        source_sequence=source_sequence,
        finalized=True,
        data_manifest_hash=data_manifest_hash,
        market_payload=payload,
        published_at=received_at,
        event_id=weighted_voting_market_event_id(
            symbol=str(candle["symbol"]).upper(),
            bar_end=bar_end,
            source=source_authority,
            source_sequence=source_sequence,
        ),
    )


def _candle_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _require_utc_datetime(_parse_optional_datetime(row["timestamp"])).isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": int(row.get("volume") or 0),
        "finalized": True,
    }


def _aggregate_finalized_candles(rows: list[dict[str, Any]], *, minutes: int, timeframe: str, end: datetime) -> list[dict[str, Any]]:
    by_timestamp = {_require_utc_datetime(_parse_optional_datetime(row["timestamp"])): row for row in rows}
    derived = []
    for window_end in sorted(timestamp for timestamp in by_timestamp if timestamp <= end):
        if window_end.minute % minutes != 0:
            continue
        interval = [window_end - timedelta(minutes=offset) for offset in range(minutes - 1, -1, -1)]
        if any(timestamp not in by_timestamp for timestamp in interval):
            continue
        window = [by_timestamp[timestamp] for timestamp in interval]
        derived.append(
            {
                "timestamp": window_end.isoformat(),
                "timeframe": timeframe,
                "open": float(window[0]["open"]),
                "high": max(float(row["high"]) for row in window),
                "low": min(float(row["low"]) for row in window),
                "close": float(window[-1]["close"]),
                "volume": sum(int(row.get("volume") or 0) for row in window),
                "finalized": True,
            }
        )
    return derived


def _session_phase_for_bar_start(timestamp: datetime) -> str:
    eastern = timestamp.astimezone(ZoneInfo("America/New_York"))
    minute = eastern.hour * 60 + eastern.minute
    if minute < 9 * 60 + 30 or minute >= 16 * 60:
        return "outside_session"
    if minute < 10 * 60 + 30:
        return "morning"
    if minute < 15 * 60:
        return "midday"
    return "late"


def _unresolved(boundary: str, key: str, status: str, reason_code: str, *, error: str | None = None) -> dict[str, Any]:
    payload = {
        "boundary": boundary,
        "key": key,
        "status": status,
        "reasonCode": reason_code,
    }
    if error:
        payload["error"] = error
    return payload


def _dedupe_unresolved(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        deduped[f"{item.get('boundary')}:{item.get('key')}:{item.get('status')}"] = item
    return list(deduped.values())


def _local_gate_result_from_payload(payload: dict[str, Any]) -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=bool(payload.get("permission_granted", payload.get("permissionGranted", False))),
        mode=str(payload.get("mode") or "automatic"),
        gate_results=(),
        reason_codes=tuple(str(code) for code in payload.get("reason_codes", payload.get("reasonCodes", ()))),
        explanation=str(payload.get("explanation") or "Weighted Voting runtime restored persisted local gate result for automatic execution."),
    )


def _broker_observations_from_gateway(
    gateway: PaperOrderGateway,
    store: WeightedVotingStateStore,
    *,
    observed_at: datetime,
) -> tuple[
    tuple[WeightedVotingBrokerOrderObservation, ...],
    tuple[WeightedVotingBrokerFillObservation, ...],
    tuple[WeightedVotingBrokerPositionObservation, ...],
]:
    if getattr(gateway, "execution_mode", None) == "LOCAL_PAPER" and getattr(getattr(gateway, "broker", None), "broker_kind", None) == "weighted_voting_local_paper":
        return _local_paper_observations_from_store(gateway, store, observed_at=observed_at)
    orders: list[WeightedVotingBrokerOrderObservation] = []
    fills: list[WeightedVotingBrokerFillObservation] = []
    listed_orders = _broker_order_payloads(gateway.broker)
    for order_payload in listed_orders:
        order = _broker_order_observation_from_payload(order_payload, observed_at=observed_at)
        if order is not None:
            orders.append(order)
    for key, payload in _store_items(store):
        if not key.startswith("weighted_voting.execution_gateway.command."):
            continue
        client_order_id = str(payload.get("clientOrderId") or "")
        if not client_order_id:
            continue
        try:
            fill = gateway.broker.refresh_order(client_order_id)
        except Exception:
            fill = None
        if fill is None or int(fill.filledQuantity) <= 0 or fill.averageFillPrice is None:
            continue
        if not any(order.client_order_id == client_order_id for order in orders):
            orders.append(
                WeightedVotingBrokerOrderObservation(
                    client_order_id=client_order_id,
                    algorithm_id=str(fill.algorithmId),
                    symbol=str(fill.symbol),
                    side=str(fill.side.value if hasattr(fill.side, "value") else fill.side),
                    status=str(fill.status),
                    quantity=int(payload.get("quantity") or 0),
                    filled_quantity=int(fill.filledQuantity),
                    average_fill_price=float(fill.averageFillPrice),
                    observed_at=observed_at,
                    broker_order_id=None,
                    protective=False,
                )
            )
        fills.append(
            WeightedVotingBrokerFillObservation(
                fill_id=f"{client_order_id}.{fill.status}.{fill.filledQuantity}.{fill.filledAt.isoformat()}",
                client_order_id=client_order_id,
                algorithm_id=str(fill.algorithmId),
                symbol=str(fill.symbol),
                side=str(fill.side.value if hasattr(fill.side, "value") else fill.side),
                quantity=int(fill.filledQuantity),
                average_fill_price=float(fill.averageFillPrice),
                filled_at=fill.filledAt,
            )
        )
    positions = []
    try:
        broker_positions = gateway.broker.refresh_positions()
    except Exception:
        broker_positions = ()
    for position in broker_positions:
        if not isinstance(position, dict):
            continue
        positions.append(
            WeightedVotingBrokerPositionObservation(
                client_order_id=position.get("clientOrderId"),
                algorithm_id=position.get("algorithmId"),
                symbol=str(position.get("symbol") or "SPY"),
                quantity=int(position.get("quantity") or 0),
                average_entry_price=float(position.get("averageEntryPrice") or position.get("average_entry_price") or 0.01),
                observed_at=observed_at,
                broker_position_id=position.get("positionId") or position.get("brokerPositionId"),
                unrealised_pnl=_optional_float(_first_present(position.get("unrealisedPnl"), position.get("unrealizedPnl"), position.get("unrealised_pnl"), position.get("unrealized_pnl"))),
                realised_pnl=_optional_float(_first_present(position.get("realisedPnl"), position.get("realizedPnl"), position.get("realised_pnl"), position.get("realized_pnl"))),
            )
        )
    return tuple(orders), tuple(fills), tuple(positions)


def _local_paper_observations_from_store(
    gateway: PaperOrderGateway,
    store: WeightedVotingStateStore,
    *,
    observed_at: datetime,
) -> tuple[
    tuple[WeightedVotingBrokerOrderObservation, ...],
    tuple[WeightedVotingBrokerFillObservation, ...],
    tuple[WeightedVotingBrokerPositionObservation, ...],
]:
    orders: list[WeightedVotingBrokerOrderObservation] = []
    fills: list[WeightedVotingBrokerFillObservation] = []
    for key, payload in _store_items(store):
        if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.orders.") or key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders."):
            order_payload = dict(payload)
            if key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.") and order_payload.get("parentClientOrderId"):
                order_payload["clientOrderId"] = order_payload["parentClientOrderId"]
                order_payload["protective"] = True
            order = _broker_order_observation_from_payload(order_payload, observed_at=observed_at)
            if order is not None and order.algorithm_id == WEIGHTED_VOTING_ALGORITHM_ID:
                orders.append(order)
        elif key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills."):
            fill = _local_paper_fill_observation_from_payload(key, payload, observed_at=observed_at)
            if fill is not None:
                fills.append(fill)

    positions: list[WeightedVotingBrokerPositionObservation] = []
    snapshot_getter = getattr(getattr(gateway, "broker", None), "inventory_repository", None)
    inventory_repository = snapshot_getter if snapshot_getter is not None else None
    if inventory_repository is not None:
        try:
            snapshot = inventory_repository.current_snapshot(now=observed_at)
        except Exception:
            snapshot = None
        if snapshot is not None:
            for position in snapshot.open_positions:
                positions.append(
                    WeightedVotingBrokerPositionObservation(
                        client_order_id=position.client_order_id,
                        algorithm_id=position.algorithm_id,
                        symbol=position.symbol,
                        quantity=position.quantity,
                        average_entry_price=position.average_entry_price,
                        observed_at=observed_at,
                        broker_position_id=position.position_id,
                        unrealised_pnl=position.unrealised_pnl,
                        realised_pnl=position.realised_pnl,
                    )
                )
    return tuple(orders), tuple(fills), tuple(positions)


def _local_paper_fill_observation_from_payload(key: str, payload: dict[str, Any], *, observed_at: datetime) -> WeightedVotingBrokerFillObservation | None:
    if payload.get("algorithmId") != WEIGHTED_VOTING_ALGORITHM_ID:
        return None
    client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
    if not client_order_id:
        return None
    filled_at = _parse_optional_datetime(payload.get("filledAt") or payload.get("filled_at")) or observed_at
    quantity = _safe_int(_first_present(payload.get("filledQuantity"), payload.get("filled_quantity"), payload.get("quantity")))
    average_fill_price = _optional_float(_first_present(payload.get("averageFillPrice"), payload.get("average_fill_price")))
    if quantity <= 0 or average_fill_price is None:
        return None
    return WeightedVotingBrokerFillObservation(
        fill_id=str(payload.get("fillId") or f"{key.rsplit('.', 1)[-1]}.{quantity}.{filled_at.isoformat()}"),
        client_order_id=client_order_id,
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=str(payload.get("symbol") or "SPY"),
        side=str(payload.get("side") or "BUY"),
        quantity=quantity,
        average_fill_price=float(average_fill_price),
        filled_at=filled_at,
        broker_order_id=payload.get("brokerOrderId"),
    )


def _broker_order_payloads(broker: Any) -> tuple[dict[str, Any], ...]:
    for method_name in ("refresh_orders", "list_orders", "open_orders", "refresh_open_orders"):
        method = getattr(broker, method_name, None)
        if not callable(method):
            continue
        try:
            rows = method()
        except TypeError:
            try:
                rows = method(algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID)
            except Exception:
                continue
        except Exception:
            continue
        if rows is None:
            return ()
        payloads = []
        for row in rows:
            if isinstance(row, dict):
                payloads.append(row)
            else:
                payloads.append(_runtime_model_payload(row))
        return tuple(payloads)
    return ()


def _broker_order_observation_from_payload(payload: dict[str, Any], *, observed_at: datetime) -> WeightedVotingBrokerOrderObservation | None:
    client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
    if not client_order_id:
        return None
    quantity = _safe_int(_first_present(payload.get("quantity"), payload.get("qty"), payload.get("submittedQuantity"), payload.get("submitted_quantity")))
    filled_quantity = _safe_int(_first_present(payload.get("filledQuantity"), payload.get("filled_quantity"), payload.get("filledQty"), payload.get("filled_qty")))
    return WeightedVotingBrokerOrderObservation(
        client_order_id=client_order_id,
        algorithm_id=payload.get("algorithmId") or payload.get("algorithm_id"),
        symbol=str(payload.get("symbol") or "SPY"),
        side=str(payload.get("side") or payload.get("orderSide") or "BUY"),
        status=str(payload.get("status") or payload.get("orderStatus") or "UNKNOWN"),
        quantity=quantity,
        filled_quantity=filled_quantity,
        average_fill_price=_optional_float(_first_present(payload.get("averageFillPrice"), payload.get("average_fill_price"), payload.get("filledAvgPrice"), payload.get("filled_avg_price"))),
        observed_at=_parse_optional_datetime(payload.get("observedAt") or payload.get("observed_at")) or observed_at,
        broker_order_id=payload.get("brokerOrderId") or payload.get("broker_order_id") or payload.get("id"),
        replaced_by_client_order_id=payload.get("replacedByClientOrderId") or payload.get("replaced_by_client_order_id"),
        protective=bool(payload.get("protective") or payload.get("isProtective") or payload.get("is_protective") or payload.get("parentClientOrderId")),
    )


def _store_items(store: WeightedVotingStateStore) -> tuple[tuple[str, dict[str, Any]], ...]:
    snapshots = getattr(store, "snapshots", None)
    if not isinstance(snapshots, dict):
        return ()
    return tuple((str(key), value) for key, value in snapshots.items() if isinstance(value, dict))


def _candles_from_persisted_market_payload(payload: dict[str, Any], session_date: date) -> tuple[WeightedVotingCandle, ...]:
    rows = payload.get("candles") if isinstance(payload.get("candles"), list) else []
    candles: list[WeightedVotingCandle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = _parse_optional_datetime(row.get("timestamp"))
        if not timestamp or timestamp.astimezone(NEW_YORK).date() != session_date:
            continue
        try:
            candles.append(
                WeightedVotingCandle(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                )
            )
        except Exception:
            continue
    return tuple(sorted(candles, key=lambda candle: candle.timestamp))


def _candle_from_event_payload(payload: dict[str, Any]) -> WeightedVotingCandle | None:
    timestamp = _parse_optional_datetime(payload.get("bar_start") or payload.get("barStart") or payload.get("finalised_candle_timestamp"))
    ohlcv = payload.get("ohlcv") if isinstance(payload.get("ohlcv"), dict) else {}
    try:
        return WeightedVotingCandle(
            timestamp=timestamp or _require_utc_datetime(None),
            open=float(_first_present(payload.get("open"), ohlcv.get("open"))),
            high=float(_first_present(payload.get("high"), ohlcv.get("high"))),
            low=float(_first_present(payload.get("low"), ohlcv.get("low"))),
            close=float(_first_present(payload.get("close"), ohlcv.get("close"))),
            volume=int(_first_present(payload.get("volume"), ohlcv.get("volume"))),
        )
    except Exception:
        return None


def _bar_summary(event: WeightedVotingFinalisedBarEvent) -> dict[str, Any]:
    return {
        "algorithmId": event.algorithm_id,
        "symbol": event.symbol.upper(),
        "barStart": event.bar_start.isoformat() if event.bar_start else None,
        "barEnd": event.bar_end.isoformat() if event.bar_end else None,
        "open": event.open,
        "high": event.high,
        "low": event.low,
        "close": event.close,
        "volume": event.volume,
        "ohlcv": {"open": event.open, "high": event.high, "low": event.low, "close": event.close, "volume": event.volume},
        "sourceSequence": event.source_sequence,
        "dataSource": event.data_source,
        "finalisedCandleTimestamp": event.finalised_candle_timestamp.isoformat(),
        "dataManifestHash": event.data_manifest_hash,
        "dataFreshnessSeconds": event.market_payload.get("data_freshness_seconds", event.market_payload.get("dataFreshnessSeconds")),
        "sessionPhase": event.market_payload.get("session_phase", event.market_payload.get("sessionPhase")),
        "publishedAt": event.published_at.isoformat(),
        "eventId": event.event_id,
    }


def _rollout_state(flags: WeightedVotingRolloutFlags | None, validation: WeightedVotingRolloutValidation | None, store: WeightedVotingStateStore | None = None) -> dict[str, Any]:
    active_flags = flags or rollout_feature_flags()
    persisted_validation = load_persisted_rollout_validation(store) if validation is None else None
    active_validation = validation or persisted_validation
    return {
        "automaticSubmissionEnabled": bool(active_flags.auto_submit_enabled),
        "paperTradingOnly": True,
        "validationPassed": bool(automatic_submission_allowed(flags=active_flags, validation=active_validation, store=store if validation is None else None)),
        "rolloutGatePresent": active_validation is not None,
        "validationSource": "explicit_backend_object" if validation is not None else ("persisted_backend_record" if persisted_validation else "missing"),
        "dynamicIncreaseEnabled": bool(active_flags.dynamic_increase_enabled),
        "dynamicIncreaseRequiredForAutoPaper": False,
    }


def _copy_nested_counts(value: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {str(key): dict(inner) for key, inner in value.items()}


def _deep_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = payload
    for part in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _runtime_model_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return dict(dumper(mode="json"))
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    return {"value": str(value)}


def _runtime_order_type_is_market(order_type: str) -> bool:
    normalized = str(order_type or "").lower()
    return "market" in normalized and "stop" not in normalized


def _outbox_status_from_gateway_result(result_payload: dict[str, Any]) -> str:
    status = str(result_payload.get("status") or "").upper()
    reason_codes = {str(code) for code in result_payload.get("reasonCodes") or ()}
    if status == "FILLED":
        return "FILLED"
    if status == "PARTIALLY_FILLED":
        return "PARTIALLY_FILLED"
    if status == "ACCEPTED":
        return "ACKNOWLEDGED"
    if status in {"CANCELED", "CANCELLED"}:
        return "CANCELLED"
    if status == "EXPIRED" or "weighted_voting.execution.expired_before_submission" in reason_codes:
        return "EXPIRED"
    if status in {"REJECTED", "NOT_SUBMITTED"}:
        return "REJECTED"
    if status in {"DUPLICATE", "RECOVERED", "RECONCILED"}:
        return "RECONCILIATION_REQUIRED"
    if bool(result_payload.get("submitted")):
        return "ACKNOWLEDGED"
    return "REJECTED"


def _outbox_status_from_broker_state(broker_state: dict[str, Any]) -> str:
    status = str(broker_state.get("status") or "").upper()
    if status == "FILLED":
        return "FILLED"
    if status == "PARTIALLY_FILLED":
        return "PARTIALLY_FILLED"
    if status in {"ACCEPTED", "PENDING_SUBMISSION", "SUBMITTED"}:
        return "ACKNOWLEDGED"
    if status in {"CANCELED", "CANCELLED"}:
        return "CANCELLED"
    if status in {"REJECTED", "NOT_SUBMITTED"}:
        return "REJECTED"
    if status == "EXPIRED":
        return "EXPIRED"
    return "RECONCILIATION_REQUIRED"


def _outbox_status_from_submission_exception(exc: Exception) -> tuple[str, str]:
    message = str(exc).lower()
    exc_name = exc.__class__.__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in message or "timeout" in exc_name:
        return "RECONCILIATION_REQUIRED", "weighted_voting.runtime.execution_outbox.submission_timeout_reconciliation_required"
    if "rate" in message or "429" in message or "ratelimit" in exc_name:
        return "READY_TO_SUBMIT", "weighted_voting.runtime.execution_outbox.rate_limited_retry_later"
    if "network" in message or "disconnect" in message or "connection" in message:
        return "RECONCILIATION_REQUIRED", "weighted_voting.runtime.execution_outbox.network_disconnect_reconciliation_required"
    return "RECONCILIATION_REQUIRED", "weighted_voting.runtime.execution_outbox.submission_exception_reconciliation_required"


def _has_pending_emergency_flatten(store: WeightedVotingStateStore) -> bool:
    for key, payload in _store_items(store):
        if not key.startswith(RUNTIME_EMERGENCY_FLATTEN_PREFIX):
            continue
        if str(payload.get("status") or "requested").lower() not in {"completed", "cancelled", "canceled"}:
            return True
    return False


def _position_management_end_of_day(timestamp: datetime, config: WeightedVotingConfig) -> bool:
    local = timestamp.astimezone(ZoneInfo("America/New_York"))
    cutoff_minutes = int(getattr(config, "session_cutoff_minutes", 10) or 10)
    session_close = local.replace(hour=16, minute=0, second=0, microsecond=0)
    return local >= session_close - timedelta(minutes=max(0, cutoff_minutes))


def _shadow_session_label(result: dict[str, Any]) -> str:
    """Session bucket a recorded observation is filed under.

    Session and regime are what the promotion gate reads consistency across, so an
    unknown label is recorded as unknown rather than folded into a real bucket -- that
    would make a strategy look consistent across conditions it was never tested in.
    """
    for path in (("marketCondition", "sessionPhase"), ("marketCondition", "session_phase"), ("sessionPhase",)):
        value = _deep_get(result, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _shadow_regime_label(result: dict[str, Any]) -> str:
    """Regime bucket a recorded observation is filed under."""
    for path in (("marketCondition", "regimeLabel"), ("marketCondition", "regime_label"), ("regimeLabel",)):
        value = _deep_get(result, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _signals_from_result(result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    candidates = (
        result.get("signals"),
        _deep_get(result, ("signalBundle", "signals")),
        _deep_get(result, ("observability", "signals")),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return tuple(item for item in candidate if isinstance(item, dict))
        if isinstance(candidate, tuple):
            return tuple(item for item in candidate if isinstance(item, dict))
    return ()


def _reason_codes_from_result(result: dict[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []
    for key in ("reasonCodes", "reason_codes"):
        value = result.get(key)
        if isinstance(value, (list, tuple)):
            codes.extend(str(item) for item in value)
    gate_result = result.get("gateResult")
    if isinstance(gate_result, dict):
        for key in ("reasonCodes", "reason_codes"):
            value = gate_result.get(key)
            if isinstance(value, (list, tuple)):
                codes.extend(str(item) for item in value)
    decision = result.get("decision")
    if isinstance(decision, dict):
        for key in ("reasonCodes", "reason_codes"):
            value = decision.get(key)
            if isinstance(value, (list, tuple)):
                codes.extend(str(item) for item in value)
    return tuple(codes)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _runtime_current_account_exposure(context: WeightedVotingRuntimeContext) -> float:
    return context.inventory_snapshot.gross_exposure


def _market_snapshot_mark_price(snapshot: WeightedMarketSnapshot) -> float | None:
    if snapshot.one_minute_candles:
        price = float(snapshot.one_minute_candles[-1].close)
        return price if price > 0 else None
    return None


def _local_paper_market_data_from_snapshot(snapshot: WeightedMarketSnapshot) -> dict[str, Any]:
    if snapshot.bid is not None and snapshot.ask is not None:
        return {
            "source": "quote",
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "timestamp": snapshot.data_timestamp.isoformat(),
            "reasonCode": "weighted_voting.runtime.finalized_bar_quote_for_local_paper_protective_exits",
        }
    candle = snapshot.one_minute_candles[-1] if snapshot.one_minute_candles else None
    close = float(candle.close) if candle is not None else _market_snapshot_mark_price(snapshot)
    return {
        "source": "bar",
        "open": float(candle.open) if candle is not None else close,
        "high": float(candle.high) if candle is not None else close,
        "low": float(candle.low) if candle is not None else close,
        "close": close,
        "timestamp": snapshot.data_timestamp.isoformat(),
        "barEndTimestamp": snapshot.data_timestamp.isoformat(),
        "timeframe": "1Min",
        "reasonCode": "weighted_voting.runtime.finalized_bar_close_for_local_paper_protective_exits",
    }


def _runtime_account_level_risk_observations(context: WeightedVotingRuntimeContext) -> dict[str, Any]:
    inventory = context.inventory_snapshot
    return {
        "localEquity": inventory.equity,
        "localCash": inventory.cash_available,
        "localBuyingPower": inventory.buying_power,
        "localReservedCash": inventory.reserved_cash,
        "localReservedBuyingPower": inventory.reserved_buying_power,
        "localPositions": [_json_ready(position) for position in inventory.open_positions],
        "localPendingOrders": [_json_ready(order) for order in inventory.pending_orders],
        "localGrossExposure": inventory.gross_exposure,
        "localNetExposure": inventory.net_exposure,
        "localDailyPnl": inventory.daily_realised_pnl + inventory.daily_unrealised_pnl,
        "localDailyRealizedPnl": inventory.daily_realised_pnl,
        "localDailyUnrealizedPnl": inventory.daily_unrealised_pnl,
        "localDailyLoss": inventory.daily_loss,
        "localDailyTradeCount": inventory.daily_trade_count,
        "localRemainingRisk": inventory.remaining_daily_risk,
        "localRiskUsed": inventory.daily_risk_used,
        "inventorySnapshotVersion": inventory.snapshot_version,
        "globalRiskServiceAvailable": context.global_risk_state.service_available,
        "globalAvailableRisk": context.global_risk_state.global_available_risk,
        "globalMaxShares": context.global_risk_state.global_max_shares,
        "accountExposure": _runtime_current_account_exposure(context),
        "source": "weighted_voting.local_inventory",
    }


def _global_risk_infrastructure_failure(reason_codes: tuple[str, ...]) -> bool:
    return any(
        code
        in {
            "weighted_voting.global_risk.missing_response",
            "weighted_voting.global_risk.timeout_reject",
            "weighted_voting.global_risk.service_failure_reject",
            "weighted_voting.global_risk.malformed_response_reject",
        }
        for code in reason_codes
    )


def _circuit_breaker_reason_from_exception(
    exc: Exception,
    *,
    default: str = "weighted_voting.runtime.circuit_breaker.persistence_failed",
) -> str:
    message = str(exc).lower()
    if "inventory" in message and ("version" in message or "conflict" in message or "stale" in message):
        return "weighted_voting.runtime.circuit_breaker.inventory_version_conflict"
    if "reconciliation" in message:
        return "weighted_voting.runtime.circuit_breaker.reconciliation_failed"
    if _submission_exception_is_broker_disconnect(exc):
        return "weighted_voting.runtime.circuit_breaker.broker_disconnected"
    if isinstance(exc, TimeoutError) or "timeout" in message:
        return "weighted_voting.runtime.circuit_breaker.broker_submission_timeout"
    if "persist" in message or "write" in message or "snapshot" in message or "store" in message:
        return "weighted_voting.runtime.circuit_breaker.persistence_failed"
    return default


def _submission_exception_is_broker_disconnect(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("disconnect", "connection", "network", "unavailable", "gateway"))


def _reconciliation_circuit_breaker_reason(result: dict[str, Any]) -> str:
    discrepancies = result.get("discrepancies") if isinstance(result.get("discrepancies"), list) else []
    reason_text = " ".join(
        str(item.get("reasonCode") or item.get("reason_code") or item.get("discrepancyId") or "")
        for item in discrepancies
        if isinstance(item, dict)
    ).lower()
    if "broker_position_unattributed" in reason_text or "broker_position_missing_local" in reason_text:
        return "weighted_voting.runtime.circuit_breaker.unknown_broker_position"
    if "broker_fill_unattributed" in reason_text or "broker_fill_missing_local" in reason_text:
        return "weighted_voting.runtime.circuit_breaker.unknown_broker_fill"
    if "protective_order" in reason_text:
        return "weighted_voting.runtime.circuit_breaker.unprotected_position_exists"
    return "weighted_voting.runtime.circuit_breaker.reconciliation_failed"


def _runtime_global_risk_response_from_payload(
    value: Any,
    *,
    request: Any,
    evaluated_at: datetime,
) -> WeightedVotingGlobalRiskResponse | None:
    if value is None:
        return None
    try:
        if isinstance(value, WeightedVotingGlobalRiskResponse):
            return value
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        if _runtime_global_risk_payload_contains_mutable_inventory(payload):
            return fail_closed_global_risk_response(
                request,
                reason_codes=("weighted_voting.global_risk.mutable_inventory_payload_rejected",),
                evaluated_at=evaluated_at,
            )
        payload = _normalize_runtime_global_risk_payload(payload, request=request, evaluated_at=evaluated_at)
        return WeightedVotingGlobalRiskResponse.model_validate(payload).with_hash()
    except Exception:
        return fail_closed_global_risk_response(
            request,
            reason_codes=("weighted_voting.global_risk.malformed_response_reject",),
            evaluated_at=evaluated_at,
        )


_RUNTIME_GLOBAL_RISK_MUTABLE_INVENTORY_KEYS = frozenset(
    {
        "account",
        "accountSnapshot",
        "algorithmCash",
        "availableCash",
        "buyingPower",
        "cash",
        "equity",
        "fills",
        "inventory",
        "inventorySnapshot",
        "localPaperAccount",
        "localPaperInventory",
        "mergedInventory",
        "pendingOrders",
        "portfolio",
        "portfolioSnapshot",
        "positions",
        "reservedCash",
        "sharedPaperPortfolio",
        "strategyState",
        "unrealizedPnl",
        "unrealizedPnL",
    }
)


def _runtime_global_risk_payload_contains_mutable_inventory(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(key in _RUNTIME_GLOBAL_RISK_MUTABLE_INVENTORY_KEYS for key in payload)
    return False


def _normalize_runtime_global_risk_payload(payload: dict[str, Any], *, request: Any, evaluated_at: datetime) -> dict[str, Any]:
    normalized = dict(payload)
    action = str(normalized.get("action") or "").upper()
    action_map = {
        "APPROVE": "ALLOW",
        "ALLOW": "ALLOW",
        "REDUCE_QUANTITY": "REDUCE",
        "REDUCE": "REDUCE",
        "REJECT": "REJECT",
    }
    normalized["action"] = action_map.get(action, action)
    normalized.setdefault("algorithm_id", normalized.get("algorithmId", WEIGHTED_VOTING_ALGORITHM_ID))
    normalized.setdefault("request_id", normalized.get("requestId", request.request_id))
    normalized.setdefault("proposal_id", normalized.get("proposalId", request.proposal_id))
    normalized.setdefault("maximum_quantity", normalized.get("maximumQuantity", normalized.get("maximumAllowedQuantity", request.proposed_quantity if normalized["action"] == "ALLOW" else 0)))
    normalized.setdefault("maximum_additional_risk", normalized.get("maximumAdditionalRisk", normalized.get("maximumAdditionalRiskDollars", request.planned_risk if normalized["action"] == "ALLOW" else 0.0)))
    normalized.setdefault("reason_codes", normalized.get("reasonCodes", ()))
    normalized.setdefault("configuration_hash", normalized.get("configurationHash", "weighted_voting.runtime.global_risk.external_response"))
    normalized.setdefault("configuration_version", normalized.get("configurationVersion", "weighted_voting.runtime.global_risk.external_response_v1"))
    normalized.setdefault("evaluated_timestamp", normalized.get("evaluatedTimestamp", normalized.get("evaluatedAt", evaluated_at.isoformat())))
    normalized.setdefault("expiry_timestamp", normalized.get("expiryTimestamp", normalized.get("expiresAt", (evaluated_at + timedelta(seconds=30)).isoformat())))
    normalized.pop("algorithmId", None)
    normalized.pop("requestId", None)
    normalized.pop("proposalId", None)
    normalized.pop("maximumQuantity", None)
    normalized.pop("maximumAllowedQuantity", None)
    normalized.pop("maximumAdditionalRisk", None)
    normalized.pop("maximumAdditionalRiskDollars", None)
    normalized.pop("reasonCodes", None)
    normalized.pop("configurationHash", None)
    normalized.pop("configurationVersion", None)
    normalized.pop("evaluatedTimestamp", None)
    normalized.pop("evaluatedAt", None)
    normalized.pop("expiryTimestamp", None)
    normalized.pop("expiresAt", None)
    normalized.pop("interfaceVersion", None)
    normalized.pop("rejectionReasons", None)
    normalized.pop("emergencyAction", None)
    return normalized


def _runtime_cost_estimate(
    *,
    effective_settings: WeightedEffectiveSettings,
    weighted_config: WeightedVotingConfig,
    observed_at: datetime,
) -> WeightedVotingExecutionCostEstimate:
    return WeightedVotingExecutionCostEstimate(
        slippage_per_share=effective_settings.slippage_allowance_per_share,
        fee_per_share=weighted_config.fee_per_share,
        observed_at=observed_at,
        source_id="weighted_voting.runtime.cost_estimate_from_stable_settings",
        available=True,
        reason_codes=("weighted_voting.runtime.cost_estimate_ignores_bar_payload_settings",),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_payload_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "open"}
    return bool(value)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _inside_entry_decision_window(timestamp: datetime, config: WeightedVotingConfig) -> bool:
    try:
        return WeightedVotingMarketCalendar().inside_entry_decision_window(timestamp, config)
    except Exception:
        return False


def _entry_start_eastern_minutes(config: WeightedVotingConfig) -> int:
    raw = str(getattr(config, "decision_session_window", "") or "")
    head = raw.split("-", 1)[0].strip()
    try:
        hour, minute = head.split(":", 1)
        return int(hour) * 60 + int(minute)
    except Exception:
        return 9 * 60 + 45


def _entry_cutoff_eastern_minutes(config: WeightedVotingConfig) -> int:
    raw = str(getattr(config, "entry_cutoff_time", "") or getattr(config, "decision_session_window", "") or "")
    if "-" in raw and not raw.strip().startswith(tuple(str(hour).zfill(2) for hour in range(24))):
        raw = raw.split("-", 1)[-1]
    elif "-" in raw:
        raw = raw.split("-", 1)[-1]
    head = raw.strip().split(" ", 1)[0]
    try:
        hour, minute = head.split(":", 1)
        return int(hour) * 60 + int(minute)
    except Exception:
        return 15 * 60 + 45


def _parse_session_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _derive_auto_paper_runtime_status(
    *,
    control: WeightedVotingRuntimeControl,
    ready: bool,
    warnings: tuple[str, ...],
    dependency_health: dict[str, dict[str, Any]],
    metrics: WeightedVotingRuntimeMetrics,
    rollout_flags: WeightedVotingRolloutFlags | None,
) -> Literal[
    "OFF",
    "STARTING",
    "RECOVERY_REQUIRED",
    "RECONCILING",
    "SHADOW",
    "PAPER_READY",
    "PAPER_ACTIVE",
    "ENTRY_PAUSED",
    "HALTED",
    "DEGRADED",
]:
    def healthy(name: str) -> bool:
        return bool(dependency_health.get(name, {}).get("healthy"))

    if not healthy("no_algorithm_halt"):
        return "HALTED"
    if metrics.recovery_required or not healthy("no_pending_recovery"):
        return "RECOVERY_REQUIRED"
    if not control.paper_trading_enabled:
        return "OFF"
    if not healthy("runtime_supervisor_healthy") or not healthy("finalized_bar_pipeline_healthy"):
        return "STARTING"
    if not healthy("no_global_halt"):
        return "HALTED"
    if not healthy("inventory_reconciled") or not healthy("broker_orders_reconciled"):
        return "RECONCILING"
    if rollout_flags is not None and bool(getattr(rollout_flags, "shadow_mode", False)):
        return "SHADOW"
    if not control.automatic_entries_enabled or metrics.automatic_order_creation_paused:
        return "ENTRY_PAUSED"
    if ready and metrics.submitted_orders > 0:
        return "PAPER_ACTIVE"
    if ready and warnings:
        return "DEGRADED"
    if ready:
        return "PAPER_READY"
    return "DEGRADED"


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _last_decision_observation(result: dict[str, Any]) -> dict[str, Any]:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    gate = result.get("gateResult") if isinstance(result.get("gateResult"), dict) else {}
    proposal = result.get("globalOrderProposal") if isinstance(result.get("globalOrderProposal"), dict) else {}
    application = result.get("globalGateApplication") if isinstance(result.get("globalGateApplication"), dict) else {}
    reason_codes = tuple(dict.fromkeys(_reason_codes_from_result(result)))
    signal = str(decision.get("signal") or decision.get("side") or "UNKNOWN")
    proposed = _safe_int(_first_present(proposal.get("quantity"), decision.get("proposed_quantity"), decision.get("proposedQuantity")))
    allowed = _safe_int(_first_present(application.get("globallyAllowedQuantity"), application.get("globally_allowed_quantity")))
    no_trade = signal.upper() == "HOLD" or proposed <= 0 or allowed <= 0 or gate.get("permission_granted") is False
    no_trade_reason_codes = reason_codes or ("weighted_voting.runtime.no_trade_reason_unavailable_fail_closed",)
    return _sanitize_for_observability(
        {
            "decisionId": decision.get("decision_id") or decision.get("decisionId"),
            "signal": signal,
            "proposedQuantity": proposed,
            "allowedQuantity": allowed,
            "noTrade": no_trade,
            "reasonCodes": reason_codes,
            "noTradeReasonCodes": no_trade_reason_codes if no_trade else (),
            "settingsVersion": decision.get("settings_version") or decision.get("settingsVersion"),
            "weightVersion": decision.get("weight_version") or decision.get("weightVersion"),
            "dataTimestamp": decision.get("data_timestamp") or decision.get("dataTimestamp"),
        }
    )


def _runtime_inventory_status(*, inventory: Any | None, error: str | None = None) -> dict[str, Any]:
    if inventory is None:
        return {
            "available": False,
            "authoritative": False,
            "error": error,
            "reasonCodes": ("weighted_voting.runtime.inventory_unavailable",),
        }
    return {
        "available": True,
        "authoritative": True,
        "algorithmId": inventory.algorithm_id,
        "cash": inventory.cash,
        "reservedCash": inventory.reserved_cash,
        "availableBuyingPower": inventory.available_cash,
        "buyingPower": inventory.buying_power,
        "equity": inventory.equity,
        "realizedPnl": inventory.realized_pnl,
        "unrealizedPnl": inventory.unrealized_pnl,
        "grossExposure": inventory.gross_exposure,
        "netExposure": inventory.net_exposure,
        "openPositions": [_sanitize_for_observability(asdict(position)) for position in inventory.open_positions],
        "openPositionCount": len(inventory.open_positions),
        "pendingOrders": [_sanitize_for_observability(asdict(order)) for order in inventory.pending_orders],
        "pendingOrderCount": len(inventory.pending_orders),
        "snapshotVersion": inventory.snapshot_version,
        "lastUpdatedAt": inventory.last_updated_at.isoformat(),
        "reasonCodes": ("weighted_voting.runtime.inventory_authoritative",),
    }


def _sanitize_for_observability(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _sanitize_for_observability(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _sanitize_for_observability(asdict(value))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            sanitized[text_key] = "[REDACTED]" if _looks_secret_key(text_key) else _sanitize_for_observability(item)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_observability(item) for item in value]
    return _json_ready(value)


def _looks_secret_key(key: str) -> bool:
    normalized = key.replace("-", "_").replace(" ", "_").lower()
    return any(marker in normalized for marker in SECRET_FIELD_MARKERS)


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)
