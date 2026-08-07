"""Voting Ensemble-owned automatic paper execution boundary."""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Condition, Event, RLock, Thread
from time import sleep
from typing import Any, Literal
from contextlib import contextmanager

import httpx
from dotenv import load_dotenv

from backend.app.algorithms.voting_ensemble.local_paper_account import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VOTING_ENSEMBLE_LOCAL_PAPER_ACCOUNT_VERSION,
    VotingEnsembleInventoryLedger,
)
from backend.app.config import get_settings
from backend.app.algorithms.voting_ensemble.execution_adapter import (
    VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
    VotingEnsembleExecutionAdapter,
    VotingEnsembleExecutionAdapterResult,
    VotingEnsembleExecutionState,
    VotingEnsembleExecutionStateStore,
)
from backend.app.domain.models import OrderPlan, Signal, _require_utc
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway, PaperOrderGatewayResult
from backend.app.execution.broker_reconciliation import BrokerFillUpdate, BrokerOrderAck, PaperBrokerClient
from backend.app.gates import AppliedGlobalGateDecision, BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, GlobalOrderProposal


VotingEnsembleExecutionMode = Literal["LOCAL_PAPER", "BROKER_PAPER"]
VOTING_ENSEMBLE_DEFAULT_EXECUTION_MODE: VotingEnsembleExecutionMode = "LOCAL_PAPER"
VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION = "voting_ensemble_paper_execution_v1"
VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE = "voting_ensemble.paper_execution"
VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE = "voting_ensemble.paper_gateway"
VOTING_ENSEMBLE_EXECUTION_OUTBOX_SCHEMA_VERSION = "voting_ensemble_execution_outbox_v1"
VOTING_ENSEMBLE_DECISION_RECORD_SCHEMA_VERSION = "voting_ensemble_decision_record_v1"
VOTING_ENSEMBLE_EXECUTION_OUTBOX_STATES = {
    "PENDING",
    "CLAIMED",
    "BLOCKED",
    "SUBMITTING",
    "SUBMITTED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELED",
    "EXPIRED",
    "RECONCILIATION_REQUIRED",
}
VOTING_ENSEMBLE_RECOVERABLE_OUTBOX_STATES = {"PENDING", "CLAIMED", "RECONCILIATION_REQUIRED"}
VOTING_ENSEMBLE_UNCERTAIN_OUTBOX_STATES = {"SUBMITTING", "SUBMITTED"}
VOTING_ENSEMBLE_CLIENT_ORDER_PREFIXES = ("ve-", "ve-paper-")
_PAPER_HOST_MARKER = "paper-api.alpaca.markets"
_APPROVED_PAPER_ENDPOINT = "https://paper-api.alpaca.markets/v2"


class VotingEnsemblePaperExecutionNamespaceError(ValueError):
    pass


class VotingEnsembleAlpacaPaperBrokerConfigurationError(ValueError):
    pass


class VotingEnsemblePaperExecutionPersistenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VotingEnsemblePaperOrderIntent:
    algorithmId: str
    orderIntentId: str
    decisionId: str
    correlationId: str
    idempotencyKey: str
    orderPlan: OrderPlan
    localGatePassed: bool
    createdAt: datetime
    sourceJobId: str | None = None
    sourceCommandId: str | None = None

    def __post_init__(self) -> None:
        if self.algorithmId != VOTING_ENSEMBLE_ALGORITHM_ID:
            raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble paper intents must use algorithm_id=voting_ensemble")
        _require_utc(self.createdAt)

    def to_record(self) -> dict[str, Any]:
        return {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "namespace": VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "orderIntentId": self.orderIntentId,
            "decisionId": self.decisionId,
            "correlationId": self.correlationId,
            "idempotencyKey": self.idempotencyKey,
            "sourceJobId": self.sourceJobId,
            "sourceCommandId": self.sourceCommandId,
            "localGatePassed": self.localGatePassed,
            "orderPlan": self.orderPlan.model_dump(mode="json"),
            "createdAt": self.createdAt.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class _LocalPaperEngineIntent:
    algorithmId: str
    capitalPartitionId: str
    accountId: str
    orderIntentId: str
    decisionId: str
    clientOrderId: str
    symbol: str
    side: Signal
    orderType: str
    submittedQuantity: int
    limitPrice: float
    triggerPrice: float | None
    stopPrice: float | None
    targetPrice: float | None
    plannedRiskDollars: float
    createdAt: datetime
    timeInForce: str = "DAY"
    exitReason: str | None = None


class VotingEnsemblePaperExecutionRepository:
    """Algorithm-owned snapshot store used by the paper gateway and execution worker."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else None
        self._lock = RLock()
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.lastPersistenceError: str | None = None
        self.lastPersistenceErrorAt: str | None = None
        self.persistenceFailureCount = 0
        self.inventory_ledger = VotingEnsembleInventoryLedger(self)
        self._transaction_depth = 0
        self._transaction_dirty = False
        self._load()

    def read_snapshot(self, key: str) -> dict[str, Any]:
        normalized = self._key(key)
        with self._lock:
            try:
                return dict(self.snapshots[normalized])
            except KeyError as exc:
                raise KeyError(normalized) from exc

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        normalized = self._key(key)
        with self._lock:
            self.snapshots[normalized] = self._owned_payload(snapshot)
            if self._transaction_depth > 0:
                self._transaction_dirty = True
            else:
                self._save_unlocked()

    @contextmanager
    def transaction(self):
        with self._lock:
            backup = dict(self.snapshots)
            self._transaction_depth += 1
            try:
                yield
            except Exception:
                self.snapshots = backup
                self._transaction_dirty = False
                raise
            finally:
                self._transaction_depth -= 1
            if self._transaction_depth == 0 and self._transaction_dirty:
                self._save_unlocked()
                self._transaction_dirty = False

    @property
    def persistenceHealthy(self) -> bool:
        return self.lastPersistenceError is None

    def require_durable(self, *, reason: str) -> None:
        if self.path is not None:
            return
        self.record_persistence_failure(RuntimeError(f"Voting Ensemble durable repository path required for {reason}"))
        raise VotingEnsemblePaperExecutionPersistenceError("voting_ensemble.paper_execution.durable_repository_required")

    def record_persistence_failure(self, exc: BaseException) -> None:
        with self._lock:
            self._record_persistence_failure_unlocked(exc)

    def runtime_warnings(self) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if self.lastPersistenceError is not None:
            warnings.append(
            {
                "severity": "HIGH",
                "code": "voting_ensemble.paper_execution.persistence_failure_blocks_new_entries",
                "message": self.lastPersistenceError,
                "observedAt": self.lastPersistenceErrorAt,
                "reasonCodes": ["voting_ensemble.paper_execution.persistence_failure_blocks_new_entries"],
            }
            )
        for code in _repository_reconciliation_blocks(self):
            warnings.append(
                {
                    "severity": "HIGH",
                    "code": code,
                    "message": "Voting Ensemble broker/local inventory ambiguity blocks new entries until reconciliation is resolved.",
                    "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "reasonCodes": [code],
                }
            )
        return warnings

    def reserve_decision_and_outbox(self, decision: Mapping[str, Any], intent: VotingEnsemblePaperOrderIntent) -> tuple[dict[str, Any], bool]:
        outbox_key = _outbox_key(intent.orderIntentId)
        decision_key = _decision_key(intent.decisionId)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            existing = self.snapshots.get(outbox_key)
            if existing is not None:
                return dict(existing), False
            decision_record = self._owned_payload(
                {
                    "schemaVersion": VOTING_ENSEMBLE_DECISION_RECORD_SCHEMA_VERSION,
                    "namespace": f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.decisions",
                    "decisionId": intent.decisionId,
                    "orderIntentId": intent.orderIntentId,
                    "correlationId": intent.correlationId,
                    "idempotencyKey": intent.idempotencyKey,
                    "sourceJobId": intent.sourceJobId,
                    "sourceCommandId": intent.sourceCommandId,
                    "sourceCommandKind": "finalized_bar_evaluation",
                    "decision": dict(decision),
                    "persistedAt": now,
                    "reasonCodes": ["voting_ensemble.paper_execution.decision_persisted"],
                }
            )
            outbox_record = self._owned_payload(
                {
                    **intent.to_record(),
                    "schemaVersion": VOTING_ENSEMBLE_EXECUTION_OUTBOX_SCHEMA_VERSION,
                    "status": "PENDING",
                    "state": "PENDING",
                    "decisionRecordKey": decision_key,
                    "sourceCommandIdempotencyKey": intent.idempotencyKey,
                    "executionIdempotencyKey": _execution_idempotency_key(intent),
                    "approvedDecisionSettingsHash": _decision_settings_hash(decision),
                    "clientOrderId": voting_ensemble_gateway_client_order_id(intent),
                    "createdAt": intent.createdAt.isoformat().replace("+00:00", "Z"),
                    "updatedAt": now,
                    "reasonCodes": ["voting_ensemble.paper_execution.intent_persisted_pending"],
                }
            )
            self.snapshots[decision_key] = decision_record
            self.snapshots[outbox_key] = outbox_record
            self._save_unlocked()
            return dict(outbox_record), True

    def pending_intents(self) -> tuple[VotingEnsemblePaperOrderIntent, ...]:
        intents: list[VotingEnsemblePaperOrderIntent] = []
        with self._lock:
            for key, payload in self.snapshots.items():
                if not key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.outbox."):
                    continue
                if str(payload.get("status") or payload.get("state") or "").upper() not in VOTING_ENSEMBLE_RECOVERABLE_OUTBOX_STATES:
                    continue
                intents.append(_intent_from_record(payload))
        return tuple(intents)

    def uncertain_intents(self) -> tuple[VotingEnsemblePaperOrderIntent, ...]:
        intents: list[VotingEnsemblePaperOrderIntent] = []
        with self._lock:
            for key, payload in self.snapshots.items():
                if not key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.outbox."):
                    continue
                if str(payload.get("status") or payload.get("state") or "").upper() not in VOTING_ENSEMBLE_UNCERTAIN_OUTBOX_STATES:
                    continue
                intents.append(_intent_from_record(payload))
        return tuple(intents)

    def claim_intent(self, intent: VotingEnsemblePaperOrderIntent, *, worker_id: str, claimed_at: datetime) -> dict[str, Any] | None:
        key = _outbox_key(intent.orderIntentId)
        now = _require_utc(claimed_at).isoformat().replace("+00:00", "Z")
        with self._lock:
            current = self.snapshots.get(key)
            if current is None:
                return None
            status = str(current.get("status") or current.get("state") or "").upper()
            if status in VOTING_ENSEMBLE_UNCERTAIN_OUTBOX_STATES:
                return dict(current)
            if status not in VOTING_ENSEMBLE_RECOVERABLE_OUTBOX_STATES:
                return None
            updated = {
                **current,
                "status": "CLAIMED",
                "state": "CLAIMED",
                "claimedBy": worker_id,
                "claimedAt": now,
                "updatedAt": now,
                "reasonCodes": [*list(current.get("reasonCodes") or ()), "voting_ensemble.paper_execution.intent_claimed"],
            }
            self.snapshots[key] = self._owned_payload(updated)
            self._save_unlocked()
            return dict(self.snapshots[key])

    def mark_outbox_status(self, intent: VotingEnsemblePaperOrderIntent, status: str, *, result: dict[str, Any] | None = None, reason_codes: tuple[str, ...] = ()) -> None:
        normalized_status = _normalize_outbox_status(status)
        key = _outbox_key(intent.orderIntentId)
        with self._lock:
            current = self.read_snapshot(key)
            self.snapshots[key] = self._owned_payload(
                {
                **current,
                "status": normalized_status,
                "state": normalized_status,
                "result": result,
                "reasonCodes": [*list(current.get("reasonCodes") or ()), *list(reason_codes)],
                "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            self._save_unlocked()

    def upsert_broker_order(self, order: BrokerOrderState, *, observed_at: datetime, reason_codes: tuple[str, ...] = ()) -> None:
        if not _is_voting_ensemble_client_order_id(order.clientOrderId):
            return
        self.write_snapshot(
            f"broker_order.{order.clientOrderId}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "namespace": f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_orders",
                "schemaVersion": "voting_ensemble_broker_order_mirror_v1",
                "executionMode": "BROKER_PAPER",
                "clientOrderId": order.clientOrderId,
                "symbol": order.symbol,
                "side": order.side,
                "status": order.status,
                "quantity": order.quantity,
                "filledQuantity": order.filledQuantity,
                "entryPrice": order.entryPrice,
                "stopPrice": order.stopPrice,
                "submittedAt": order.submittedAt.isoformat().replace("+00:00", "Z"),
                "observedAt": _require_utc(observed_at).isoformat().replace("+00:00", "Z"),
                "sourceAuthority": "alpaca_paper_broker",
                "reasonCodes": ["voting_ensemble.paper_execution.broker_order_mirrored", *reason_codes],
            },
        )

    def upsert_broker_account(self, account: BrokerAccountSnapshot, *, observed_at: datetime) -> None:
        self.write_snapshot(
            "broker_account.latest",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "namespace": f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_account",
                "schemaVersion": "voting_ensemble_broker_account_mirror_v1",
                "executionMode": "BROKER_PAPER",
                "accountId": account.accountId,
                "equity": account.equity,
                "buyingPower": account.buyingPower,
                "realizedPnlToday": account.realizedPnlToday,
                "observedAt": _require_utc(observed_at).isoformat().replace("+00:00", "Z"),
                "sourceAuthority": "alpaca_paper_broker",
                "readOnly": True,
                "reasonCodes": ["voting_ensemble.paper_execution.broker_account_read_only_snapshot"],
            },
        )

    def local_account_snapshot(self, *, observed_at: datetime | None = None) -> dict[str, Any]:
        return self.inventory_ledger.account_snapshot(observed_at=observed_at)

    def recover_local_inventory_from_persistence(self, *, evaluated_at: datetime) -> dict[str, Any]:
        observed = _require_utc(evaluated_at)
        snapshots = dict(self.snapshots)
        account_key = f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_account.latest"
        has_local_inventory = any(
            key.startswith(prefix)
            for key in snapshots
            for prefix in (
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position.",
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_order.",
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.applied_fill.",
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_closed_trade.",
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_realized_pnl.",
                f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_risk_snapshot.",
                f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.",
            )
        )
        if account_key not in snapshots and has_local_inventory:
            failures = ["voting_ensemble.local_paper_recovery.account_missing_with_existing_inventory"]
            self._record_local_fill_migration(
                status="FAILED",
                evaluated_at=observed,
                reason_codes=["voting_ensemble.local_paper_migration.account_missing_cannot_reconstruct_cash"],
            )
            return self._record_local_recovery_failure(failures, evaluated_at=observed, mark_recompute={})
        if account_key not in snapshots:
            self.local_account_snapshot(observed_at=observed)
            snapshots = dict(self.snapshots)
        migration = self._migrate_legacy_fill_derived_state_if_needed(evaluated_at=observed)
        if migration.get("status") == "FAILED":
            return self._record_local_recovery_failure(list(migration.get("reasonCodes") or []), evaluated_at=observed, mark_recompute={})
        snapshots = dict(self.snapshots)
        account = dict(snapshots.get(account_key) or {})
        failures = _validate_local_recovery_state(account=account, snapshots=snapshots)
        if failures:
            return self._record_local_recovery_failure(failures, evaluated_at=observed, mark_recompute={})
        mark_recompute = self._recompute_local_marks_from_persisted_market_data(evaluated_at=observed)
        snapshots = dict(self.snapshots)
        account = dict(snapshots.get(account_key) or {})
        failures = _validate_local_recovery_state(account=account, snapshots=snapshots)
        if failures:
            return self._record_local_recovery_failure(failures, evaluated_at=observed, mark_recompute=mark_recompute)
        self.mark_reconciliation_resolved("local_paper_recovery", reason_code="voting_ensemble.local_paper_recovery.recovered_from_persisted_inventory")
        self.inventory_ledger.persist_inventory_manifest(observed_at=observed)
        inventory = self.inventory_snapshot()
        payload = {
            "schemaVersion": "voting_ensemble_local_paper_recovery_v1",
            "executionMode": "LOCAL_PAPER",
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "status": "RECOVERED",
            "recoveryStatus": "RECOVERED",
            "newEntriesBlocked": False,
            "riskReducingExitsPreserved": True,
            "positionsRecovered": len(inventory.get("positions") or []),
            "openOrdersRecovered": len([order for order in inventory.get("orders") or [] if str(order.get("status") or "").upper() in {"OPEN", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}]),
            "fillsRecovered": len(inventory.get("fills") or []),
            "appliedFillIdsRecovered": len((inventory.get("account") or {}).get("appliedFillIds") or []),
            "closedTradesRecovered": len(inventory.get("closedTrades") or []),
            "cash": (inventory.get("account") or {}).get("cash"),
            "realizedPnl": (inventory.get("account") or {}).get("realizedPnl"),
            "sessionDate": (inventory.get("account") or {}).get("sessionDate"),
            "migration": migration,
            "markRecompute": mark_recompute,
            "evaluatedAt": observed.isoformat().replace("+00:00", "Z"),
            "reasonCodes": ["voting_ensemble.local_paper_recovery.recovered_from_persisted_inventory"],
        }
        self.write_snapshot("local_recovery.latest", payload)
        return self.read_snapshot("local_recovery.latest")

    def _migrate_legacy_fill_derived_state_if_needed(self, *, evaluated_at: datetime) -> dict[str, Any]:
        try:
            existing = self.read_snapshot("local_fill_migration.latest")
        except KeyError:
            existing = {}
        if existing.get("schemaVersion") == "voting_ensemble_local_fill_migration_v1" and existing.get("status") in {"MIGRATED", "SKIPPED"}:
            return existing
        snapshots = dict(self.snapshots)
        local_fills = _local_recovery_fill_records(snapshots)
        canonical_positions = [
            payload
            for key, payload in snapshots.items()
            if key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position.")
            and _safe_int(payload.get("quantity") or payload.get("signedQuantity")) != 0
        ]
        if not local_fills or canonical_positions:
            return self._record_local_fill_migration(
                status="SKIPPED",
                evaluated_at=evaluated_at,
                reason_codes=["voting_ensemble.local_paper_migration.no_legacy_fill_derived_state_to_migrate"],
            )
        account = snapshots.get(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_account.latest")
        if not isinstance(account, Mapping):
            return self._record_local_fill_migration(
                status="FAILED",
                evaluated_at=evaluated_at,
                reason_codes=["voting_ensemble.local_paper_migration.account_missing_cannot_reconstruct_cash"],
            )
        migration_failures = _local_recovery_accounting_failures(account=account, snapshots=snapshots, local_fills=local_fills, require_positions=False)
        if any(reason in migration_failures for reason in ("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", "voting_ensemble.local_paper_recovery.realized_pnl_fill_invariant_failed")):
            return self._record_local_fill_migration(
                status="FAILED",
                evaluated_at=evaluated_at,
                reason_codes=["voting_ensemble.local_paper_migration.account_cash_or_pnl_not_reconstructable", *migration_failures],
            )
        reconstructed = _reconstruct_local_positions_from_fills(local_fills, evaluated_at=evaluated_at)
        if reconstructed.get("failures"):
            return self._record_local_fill_migration(
                status="FAILED",
                evaluated_at=evaluated_at,
                reason_codes=["voting_ensemble.local_paper_migration.fill_replay_not_safe", *list(reconstructed.get("failures") or [])],
            )
        migrated_symbols: list[str] = []
        for symbol, position in (reconstructed.get("positions") or {}).items():
            self.write_snapshot(f"local_position.{symbol}", position)
            migrated_symbols.append(str(symbol))
        self.inventory_ledger.persist_inventory_manifest(observed_at=evaluated_at)
        return self._record_local_fill_migration(
            status="MIGRATED",
            evaluated_at=evaluated_at,
            reason_codes=["voting_ensemble.local_paper_migration.legacy_fill_derived_state_migrated_once"],
            migrated_symbols=sorted(migrated_symbols),
        )

    def _record_local_fill_migration(
        self,
        *,
        status: str,
        evaluated_at: datetime,
        reason_codes: list[str],
        migrated_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schemaVersion": "voting_ensemble_local_fill_migration_v1",
            "version": "voting_ensemble_local_fill_migration_v1",
            "executionMode": "LOCAL_PAPER",
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "status": status,
            "migrationStatus": status,
            "migrationSource": "legacy_local_fill_records",
            "normalRuntimeAuthority": "canonical_local_inventory_positions_not_fill_replay",
            "fillReplayAllowedFor": ["migration", "audit", "recovery_verification"],
            "migratedSymbols": list(migrated_symbols or []),
            "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
            "reasonCodes": list(dict.fromkeys(reason_codes)),
        }
        self.write_snapshot("local_fill_migration.latest", payload)
        return self.read_snapshot("local_fill_migration.latest")

    def _record_local_recovery_failure(self, failures: list[str], *, evaluated_at: datetime, mark_recompute: Mapping[str, Any]) -> dict[str, Any]:
        reason_codes = list(dict.fromkeys(["voting_ensemble.local_paper_recovery.failed", *failures]))
        self.mark_reconciliation_required(
            "local_paper_recovery",
            {
                "sourceAuthority": "voting_ensemble_local_paper_account",
                "recoveryStatus": "RECOVERY_FAILED",
                "newEntriesBlocked": True,
                "riskReducingExitsPreserved": True,
                "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
                "reasonCodes": reason_codes,
            },
        )
        payload = {
            "schemaVersion": "voting_ensemble_local_paper_recovery_v1",
            "executionMode": "LOCAL_PAPER",
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "status": "RECOVERY_FAILED",
            "recoveryStatus": "RECOVERY_FAILED",
            "newEntriesBlocked": True,
            "riskReducingExitsPreserved": True,
            "markRecompute": dict(mark_recompute),
            "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
            "reasonCodes": reason_codes,
        }
        self.write_snapshot("local_recovery.latest", payload)
        return self.read_snapshot("local_recovery.latest")

    def _recompute_local_marks_from_persisted_market_data(self, *, evaluated_at: datetime) -> dict[str, Any]:
        marked: list[str] = []
        stale: list[str] = []
        missing: list[str] = []
        for position in self.inventory_ledger.positions():
            symbol = str(position.get("symbol") or "").upper()
            if not symbol:
                continue
            market_data = self.inventory_ledger.latest_market_data_status(symbol)
            if not market_data:
                missing.append(symbol)
                continue
            quote_timestamp = _parse_time(market_data.get("quoteTimestamp"))
            if quote_timestamp is not None and quote_timestamp > evaluated_at:
                stale.append(symbol)
                continue
            nbbo = {
                "bid": market_data.get("bid"),
                "ask": market_data.get("ask"),
                "bidSize": market_data.get("bidSize"),
                "askSize": market_data.get("askSize"),
                "quoteTimestamp": market_data.get("quoteTimestamp"),
                "marketDataReceiptTimestamp": market_data.get("marketDataReceiptTimestamp"),
            }
            result = self.inventory_ledger.mark_open_positions_from_market_data(symbol=symbol, nbbo=nbbo, observed_at=evaluated_at)
            if result.get("fresh"):
                marked.append(symbol)
            else:
                stale.append(symbol)
        return {
            "symbolsMarked": sorted(set(marked)),
            "symbolsWithStaleMarketData": sorted(set(stale)),
            "symbolsMissingMarketData": sorted(set(missing)),
            "policy": "recompute_from_persisted_fresh_local_market_data_only_no_broker_trading_state",
        }

    def local_broker_account_snapshot(self, *, observed_at: datetime | None = None) -> BrokerAccountSnapshot:
        return self.inventory_ledger.broker_account_snapshot(observed_at=observed_at)

    def mark_local_positions_from_market_data(
        self,
        *,
        symbol: str,
        nbbo: Mapping[str, Any] | None,
        observed_at: datetime,
    ) -> dict[str, Any]:
        return self.inventory_ledger.mark_open_positions_from_market_data(
            symbol=symbol,
            nbbo=nbbo,
            observed_at=observed_at,
        )

    def local_mark_fresh_for_entries(self, symbol: str, *, evaluated_at: datetime) -> bool:
        return self.inventory_ledger.local_mark_is_fresh_for_entries(symbol, evaluated_at=evaluated_at)

    def apply_local_fill(
        self,
        *,
        client_order_id: str,
        order_intent_id: str,
        symbol: str,
        side: Signal | str,
        requested_quantity: int,
        fill_price: float,
        filled_at: datetime,
    ) -> PaperGatewayFill | None:
        return self.inventory_ledger.apply_fill(
            client_order_id=client_order_id,
            order_intent_id=order_intent_id,
            symbol=symbol,
            side=side,
            requested_quantity=requested_quantity,
            fill_price=fill_price,
            filled_at=filled_at,
        )

    def _local_account_payload(
        self,
        *,
        cash: float,
        realized_pnl: float,
        observed_at: datetime,
        reason_codes: list[str],
        equity: float | None = None,
        unrealized_pnl: float = 0.0,
    ) -> dict[str, Any]:
        return self.inventory_ledger._account_payload(
            cash=cash,
            realized_pnl=realized_pnl,
            observed_at=observed_at,
            reason_codes=reason_codes,
            equity=equity,
            unrealized_pnl=unrealized_pnl,
        )

    def _local_risk_snapshot_payload(self, *, observed_at: datetime) -> dict[str, Any]:
        return self.inventory_ledger.risk_snapshot_payload(observed_at=observed_at)

    def upsert_broker_fill(
        self,
        fill: BrokerFillUpdate,
        *,
        order_plan: OrderPlan,
        order_intent_id: str | None,
        observed_at: datetime,
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        if not _is_voting_ensemble_client_order_id(fill.clientOrderId):
            return
        try:
            existing_fill = self.read_snapshot(f"paper_order_gateway.fill.{fill.clientOrderId}")
        except KeyError:
            existing_fill = {}
        previous_quantity = _safe_int(existing_fill.get("filledQuantity") or 0)
        fill_delta = max(0, int(fill.filledQuantity) - previous_quantity)
        if fill_delta > 0 and fill.averageFillPrice:
            self.inventory_ledger.apply_fill(
                client_order_id=fill.clientOrderId,
                applied_fill_id=f"{fill.clientOrderId}:cum:{fill.filledQuantity}",
                order_intent_id=str(order_intent_id or fill.clientOrderId),
                symbol=order_plan.symbol,
                side=order_plan.side,
                requested_quantity=fill_delta,
                fill_price=float(fill.averageFillPrice),
                filled_at=fill.updatedAt,
            )
        self.write_snapshot(
            f"paper_order_gateway.fill.{fill.clientOrderId}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "executionMode": "BROKER_PAPER",
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": str(getattr(order_plan, "accountId", "") or VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID),
                "source": "alpaca_paper_broker",
                "sourceAuthority": "alpaca_paper_broker",
                "orderIntentId": order_intent_id,
                "clientOrderId": fill.clientOrderId,
                "symbol": order_plan.symbol,
                "side": order_plan.side,
                "filledQuantity": fill.filledQuantity,
                "averageFillPrice": fill.averageFillPrice,
                "status": fill.status,
                "filledAt": fill.updatedAt.isoformat().replace("+00:00", "Z"),
                "observedAt": _require_utc(observed_at).isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["voting_ensemble.paper_execution.broker_confirmed_fill_mirrored", *reason_codes],
            },
        )

    def mark_reconciliation_required(self, key: str, payload: Mapping[str, Any]) -> None:
        self.write_snapshot(
            f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.{key}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "namespace": VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
                "schemaVersion": "voting_ensemble_reconciliation_block_v1",
                "reconciliationStatus": "RECONCILIATION_REQUIRED",
                "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **dict(payload),
                "reasonCodes": list(payload.get("reasonCodes") or ["voting_ensemble.paper_execution.reconciliation_required"]),
            },
        )

    def mark_reconciliation_resolved(self, key: str, *, reason_code: str) -> None:
        self.write_snapshot(
            f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.{key}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "namespace": VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
                "schemaVersion": "voting_ensemble_reconciliation_block_v1",
                "reconciliationStatus": "RESOLVED",
                "resolvedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "reasonCodes": [reason_code],
            },
        )

    def inventory_snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshots = dict(self.snapshots)
        order_intents = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.intent.")
        orders = self.inventory_ledger.orders()
        fills = self.inventory_ledger.fills()
        protective_orders = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.protective.")
        broker_orders = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_order.")
        broker_positions = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_position.")
        broker_accounts = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_account.")
        local_accounts = self.inventory_ledger.accounts()
        closed_trades = self.inventory_ledger.closed_trades()
        realized_pnl = self.inventory_ledger.realized_pnl_records()
        risk_snapshots = self.inventory_ledger.risk_snapshots()
        market_data = self.inventory_ledger.market_data_statuses()
        local_inventory_manifests = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_inventory_manifest.")
        local_market_clocks = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_market_clock.")
        local_recoveries = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_recovery.")
        local_fill_migrations = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_fill_migration.")
        local_executions = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_execution.")
        reconciliation_blocks = [
            record
            for record in _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.")
            if str(record.get("reconciliationStatus") or "").upper() in {"RECONCILIATION_REQUIRED", "UNKNOWN_BROKER_STATE"}
        ]
        outbox = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.outbox.")
        decisions = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.decisions.")
        execution_states = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.orders.")
        results = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.result.")
        reconciliations = _records_with_prefix(snapshots, f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.restart_recovery.")
        has_persisted_local_inventory = _has_persisted_local_inventory_without_account(snapshots)
        account_payload = local_accounts[-1] if local_accounts else (None if has_persisted_local_inventory else self.local_account_snapshot())
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "orders": orders,
            "orderIntents": order_intents,
            "fills": fills,
            "positions": self.inventory_ledger.positions(),
            "account": account_payload,
            "accounts": local_accounts,
            "closedTrades": closed_trades,
            "realizedPnlRecords": realized_pnl,
            "riskSnapshots": risk_snapshots,
            "marketData": market_data,
            "localInventoryManifest": local_inventory_manifests[-1] if local_inventory_manifests else None,
            "localInventoryManifests": local_inventory_manifests,
            "localMarketClocks": local_market_clocks,
            "localMarketClock": local_market_clocks[-1] if local_market_clocks else None,
            "localRecovery": local_recoveries[-1] if local_recoveries else None,
            "localRecoveries": local_recoveries,
            "localFillMigration": local_fill_migrations[-1] if local_fill_migrations else None,
            "localFillMigrations": local_fill_migrations,
            "localExecutions": local_executions,
            "brokerOrders": broker_orders,
            "brokerPositions": broker_positions,
            "brokerAccounts": broker_accounts,
            "reconciliationBlocks": reconciliation_blocks,
            "protectiveOrders": protective_orders,
            "outbox": outbox,
            "decisions": decisions,
            "executionStates": execution_states,
            "results": results,
            "reconciliations": reconciliations,
            "persistenceHealthy": self.persistenceHealthy,
            "lastPersistenceError": self.lastPersistenceError,
            "lastPersistenceErrorAt": self.lastPersistenceErrorAt,
            "highSeverityRuntimeWarnings": self.runtime_warnings(),
            "snapshotCount": len(snapshots),
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "reasonCodes": ["voting_ensemble.paper_execution.inventory_reported"],
        }

    def _key(self, key: str) -> str:
        if key.startswith("paper_order_gateway."):
            return f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.{key}"
        if key.startswith(VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE) or key.startswith(VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE) or key.startswith(VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE):
            return key
        return f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.{key}"

    def _owned_payload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot)
        raw_algorithm = payload.get("algorithm_id", payload.get("algorithmId", VOTING_ENSEMBLE_ALGORITHM_ID))
        if raw_algorithm != VOTING_ENSEMBLE_ALGORITHM_ID:
            raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble repository rejected a foreign algorithm record")
        raw_partition = payload.get("capitalPartitionId", VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)
        if raw_partition != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
            raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble repository rejected a foreign capital partition record")
        payload["algorithm_id"] = VOTING_ENSEMBLE_ALGORITHM_ID
        payload["algorithmId"] = VOTING_ENSEMBLE_ALGORITHM_ID
        payload["capitalPartitionId"] = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
        return payload

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._record_persistence_failure_unlocked(exc)
            return
        snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
        if not isinstance(snapshots, dict):
            return
        self.snapshots = {
            str(key): self._owned_payload(dict(value))
            for key, value in snapshots.items()
            if isinstance(value, dict) and value.get("algorithmId", value.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
        }

    def _save_unlocked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "outboxSchemaVersion": VOTING_ENSEMBLE_EXECUTION_OUTBOX_SCHEMA_VERSION,
                "snapshots": self.snapshots,
                "savedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
            indent=2,
            default=str,
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)
        except PermissionError:
            try:
                self.path.write_text(encoded, encoding="utf-8")
            except OSError as exc:
                self._record_persistence_failure_unlocked(exc)
                raise VotingEnsemblePaperExecutionPersistenceError("voting_ensemble.paper_execution.persistence_write_failed") from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError as exc:
            self._record_persistence_failure_unlocked(exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise VotingEnsemblePaperExecutionPersistenceError("voting_ensemble.paper_execution.persistence_write_failed") from exc
        self.lastPersistenceError = None
        self.lastPersistenceErrorAt = None

    def _record_persistence_failure_unlocked(self, exc: BaseException) -> None:
        self.persistenceFailureCount += 1
        self.lastPersistenceError = str(exc) or type(exc).__name__
        self.lastPersistenceErrorAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")


class VotingEnsembleDurableExecutionStateStore(VotingEnsembleExecutionStateStore):
    """Repository-backed execution state store used for automatic paper submissions."""

    def __init__(self, repository: VotingEnsemblePaperExecutionRepository) -> None:
        repository.require_durable(reason="automatic paper execution state")
        super().__init__()
        self.repository = repository
        self._hydrate()

    def get_by_idempotency_key(self, idempotency_key: str) -> VotingEnsembleExecutionState | None:
        state = super().get_by_idempotency_key(idempotency_key)
        if state is not None:
            return state
        self._hydrate()
        return super().get_by_idempotency_key(idempotency_key)

    def get(self, client_order_id: str) -> VotingEnsembleExecutionState | None:
        state = super().get(client_order_id)
        if state is not None:
            return state
        self._hydrate()
        return super().get(client_order_id)

    def put(self, state: VotingEnsembleExecutionState) -> VotingEnsembleExecutionState:
        stored = super().put(state)
        self.repository.write_snapshot(_execution_state_key(state.clientOrderId), _execution_state_record(stored))
        return stored

    def mark_unknown_order_state(self, symbol: str) -> None:
        super().mark_unknown_order_state(symbol)
        normalized = symbol.upper()
        self.repository.write_snapshot(
            f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.{normalized}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "namespace": VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
                "schemaVersion": "voting_ensemble_execution_state_block_v1",
                "symbol": normalized,
                "reconciliationStatus": "UNKNOWN_BROKER_STATE",
                "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["voting_ensemble.execution_adapter.unknown_order_state_reconciliation_required"],
            },
        )

    def _hydrate(self) -> None:
        for key, payload in tuple(self.repository.snapshots.items()):
            if key.startswith(f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.orders."):
                raw_state = payload.get("executionState") if isinstance(payload.get("executionState"), dict) else payload
                try:
                    state = VotingEnsembleExecutionState.model_validate(raw_state)
                except Exception:
                    continue
                super().put(state)
            elif key.startswith(f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state."):
                symbol = str(payload.get("symbol") or "").upper()
                if symbol:
                    self.unknown_symbols.add(symbol)


class VotingEnsemblePaperExecutionQueue:
    def __init__(self) -> None:
        self._queue: deque[VotingEnsemblePaperOrderIntent] = deque()
        self._ids: set[str] = set()
        self._condition = Condition()

    def enqueue(self, intent: VotingEnsemblePaperOrderIntent) -> bool:
        with self._condition:
            if intent.orderIntentId in self._ids:
                return False
            self._queue.append(intent)
            self._ids.add(intent.orderIntentId)
            self._condition.notify()
            return True

    def pop(self, *, timeout: float | None = None) -> VotingEnsemblePaperOrderIntent | None:
        with self._condition:
            if not self._queue:
                self._condition.wait(timeout=timeout)
            if not self._queue:
                return None
            intent = self._queue.popleft()
            self._ids.discard(intent.orderIntentId)
            return intent

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "queueNamespace": f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.queue",
                "depth": len(self._queue),
            }


class VotingEnsemblePaperExecutionWorker:
    def __init__(
        self,
        *,
        queue: VotingEnsemblePaperExecutionQueue,
        repository: VotingEnsemblePaperExecutionRepository,
        paper_gateway: PaperOrderGateway,
        entry_permission_provider: Callable[[], Mapping[str, Any]] | None = None,
        execution_adapter: VotingEnsembleExecutionAdapter | None = None,
        broker_client: PaperBrokerClient | None = None,
        short_trading_enabled: bool = False,
    ) -> None:
        self.queue = queue
        self.repository = repository
        self.paper_gateway = paper_gateway
        self.entry_permission_provider = entry_permission_provider
        self.execution_adapter = execution_adapter or _default_execution_adapter_for_runtime(repository=repository, broker_client=broker_client)
        self.broker_client = broker_client
        self.short_trading_enabled = short_trading_enabled
        self.worker_id = f"voting-ensemble-execution-worker-{id(self):x}"

    def process_once(self, *, timeout: float | None = 0.0, evaluated_at: datetime | None = None) -> dict[str, Any] | None:
        intent = self.queue.pop(timeout=timeout)
        if intent is None:
            pending = self.repository.pending_intents()
            if pending:
                intent = pending[0]
            else:
                uncertain = self.repository.uncertain_intents()
                if not uncertain:
                    return self.reconcile_broker_state(evaluated_at=evaluated_at or datetime.now(UTC))
                return self._mark_uncertain_restart(uncertain[0], evaluated_at=evaluated_at)
        return self.process_intent(intent, evaluated_at=evaluated_at)

    def process_intent(self, intent: VotingEnsemblePaperOrderIntent, *, evaluated_at: datetime | None = None) -> dict[str, Any]:
        now = _require_utc(evaluated_at or datetime.now(UTC))
        claimed = self.repository.claim_intent(intent, worker_id=self.worker_id, claimed_at=now)
        if claimed is None:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "reasonCodes": ["voting_ensemble.paper_execution.intent_not_claimable"],
            }
        if str(claimed.get("status") or claimed.get("state") or "").upper() in VOTING_ENSEMBLE_UNCERTAIN_OUTBOX_STATES:
            return self._mark_uncertain_restart(intent, evaluated_at=now)
        exit_intent = _is_risk_reducing_order_plan(self.repository, intent.orderPlan)
        short_blockers = _short_entry_blockers(
            repository=self.repository,
            order_plan=intent.orderPlan,
            short_trading_enabled=self.short_trading_enabled,
        )
        if short_blockers:
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "reasonCodes": short_blockers,
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        permission = self._entry_permission()
        if not exit_intent and not bool(permission.get("newEntriesAllowed", permission.get("effectivePaperTradingEnabled", False))):
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "control": dict(permission),
                "reasonCodes": ["voting_ensemble.paper_execution.control_blocked_before_broker_submission", *list(permission.get("reasonCodes") or permission.get("blockers") or ())],
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        mark_blockers = _local_mark_blocks_new_entry(self.repository, intent.orderPlan, evaluated_at=now)
        if mark_blockers:
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "reasonCodes": list(mark_blockers),
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        executable_quantity = _executable_quantity_for_order_plan(self.repository, intent.orderPlan)
        proposal = proposal_from_order_plan(intent, quantity_override=executable_quantity)
        if exit_intent:
            proposal = proposal.model_copy(update={"intent": "risk_reducing"})
        application = allow_all_global_application(proposal, evaluated_at=now)
        try:
            self.repository.mark_outbox_status(
                intent,
                "SUBMITTING",
                reason_codes=("voting_ensemble.paper_execution.intent_submitting",),
            )
        except VotingEnsemblePaperExecutionPersistenceError:
            return _persistence_failure_result(intent, evaluated_at=now)
        if self.broker_client is not None:
            return self._process_with_execution_adapter(intent, claimed, evaluated_at=now)
        result = self.paper_gateway.submit(
            proposal=proposal,
            global_application=application,
            local_gate_passed=intent.localGatePassed,
            mode="automatic",
            evaluated_at=now,
        )
        self.repository.mark_outbox_status(
            intent,
            _outbox_status_from_gateway_result(result),
            result=result.model_dump(mode="json"),
            reason_codes=tuple(result.reasonCodes),
        )
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "orderIntentId": intent.orderIntentId,
            "clientOrderId": result.clientOrderId,
            "submitted": result.submitted,
            "status": result.status,
            "gatewayResult": result.model_dump(mode="json"),
            "reasonCodes": ["voting_ensemble.paper_execution.worker.processed", *result.reasonCodes],
        }

    def reconcile_broker_state(self, *, evaluated_at: datetime) -> dict[str, Any] | None:
        if self.broker_client is None:
            return None
        now = _require_utc(evaluated_at)
        updates: list[dict[str, Any]] = []
        try:
            account = self.broker_client.refresh_account_snapshot()
            open_orders = [_order for _order in self.broker_client.refresh_open_orders() if _is_voting_ensemble_client_order_id(_order.clientOrderId)]
            account_positions = list(self.broker_client.refresh_positions())
        except Exception:
            self.repository.mark_reconciliation_required(
                "broker_refresh_failed",
                {
                    "sourceAuthority": "alpaca_paper_broker",
                    "reasonCodes": ["voting_ensemble.paper_execution.broker_reconciliation_refresh_failed"],
                },
            )
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "status": "RECONCILIATION_REQUIRED",
                "submitted": False,
                "reasonCodes": ["voting_ensemble.paper_execution.broker_reconciliation_refresh_failed"],
            }
        self.repository.upsert_broker_account(account, observed_at=now)
        self.repository.mark_reconciliation_resolved("broker_refresh_failed", reason_code="voting_ensemble.paper_execution.broker_reconciliation_refresh_recovered")
        for order in open_orders:
            self.repository.upsert_broker_order(order, observed_at=now)
        broker_positions: list[BrokerPositionState] = []
        active_unattributed_keys: set[str] = set()
        for position in account_positions:
            if _position_attributed_to_voting_ensemble(self.repository, position, open_orders):
                broker_positions.append(position)
            elif position.quantity > 0:
                block_key = f"unattributed_position.{position.symbol}.{Signal(position.side).value}"
                active_unattributed_keys.add(block_key)
                self.repository.mark_reconciliation_required(
                    block_key,
                    {
                        "symbol": position.symbol,
                        "side": position.side,
                        "quantity": position.quantity,
                        "sourceAuthority": "alpaca_paper_broker",
                        "reasonCodes": ["voting_ensemble.paper_execution.unattributed_broker_position_not_claimed"],
                    },
                )
                updates.append(
                    {
                        "symbol": position.symbol,
                        "status": "RECONCILIATION_REQUIRED",
                        "reasonCodes": ["voting_ensemble.paper_execution.unattributed_broker_position_not_claimed"],
                    }
                )
        _resolve_absent_unattributed_position_blocks(self.repository, active_unattributed_keys)
        _record_local_broker_divergence_blocks(self.repository, broker_positions)
        for position in broker_positions:
            self.repository.write_snapshot(
                f"broker_position.{position.symbol}.{Signal(position.side).value}",
                {
                    "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "namespace": f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.broker_positions",
                    "schemaVersion": "voting_ensemble_broker_position_mirror_v1",
                    "symbol": position.symbol,
                    "side": position.side,
                    "quantity": position.quantity,
                    "averageEntryPrice": position.averageEntryPrice,
                    "markPrice": position.markPrice,
                    "realizedPnlToday": position.realizedPnlToday,
                    "openedAt": position.openedAt.isoformat().replace("+00:00", "Z") if position.openedAt else None,
                    "observedAt": now.isoformat().replace("+00:00", "Z"),
                    "sourceAuthority": "alpaca_paper_broker",
                    "reasonCodes": ["voting_ensemble.paper_execution.broker_position_mirrored"],
                },
            )
        fills_reader = getattr(self.broker_client, "retrieve_fills", None)
        if callable(fills_reader):
            for fill in fills_reader(after=now - timedelta(minutes=30)):
                if not _is_voting_ensemble_client_order_id(fill.clientOrderId):
                    continue
                state = self.execution_adapter.state_store.get(fill.clientOrderId)
                if state is None:
                    self.execution_adapter.state_store.mark_unknown_order_state("SPY")
                    updates.append({"clientOrderId": fill.clientOrderId, "status": "RECONCILIATION_REQUIRED", "reasonCodes": ["voting_ensemble.paper_execution.unknown_fill_requires_reconciliation"]})
                    continue
                order_plan = OrderPlan.model_validate(state.orderPlan)
                self.repository.upsert_broker_fill(
                    fill,
                    order_plan=order_plan,
                    order_intent_id=state.parentDecisionId,
                    observed_at=now,
                    reason_codes=("voting_ensemble.paper_execution.broker_fill_activity_mirrored",),
                )
        for state in tuple(self.execution_adapter.state_store.records_by_client_order_id.values()):
            if not _is_voting_ensemble_client_order_id(state.clientOrderId):
                continue
            updates.extend(self._refresh_state_from_broker(state, observed_at=now, open_orders=open_orders))
        updates.extend(self._expire_or_flatten_positions(now=now, broker_positions=broker_positions, open_orders=open_orders))
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "status": "RECONCILED" if not any(item.get("status") == "RECONCILIATION_REQUIRED" for item in updates) else "RECONCILIATION_REQUIRED",
            "ordersObserved": len(open_orders),
            "accountPositionsObserved": len(account_positions),
            "positionsObserved": len(broker_positions),
            "updates": updates,
            "reasonCodes": ["voting_ensemble.paper_execution.broker_authoritative_reconciliation_completed"],
        }

    def _refresh_state_from_broker(self, state: VotingEnsembleExecutionState, *, observed_at: datetime, open_orders: list[BrokerOrderState]) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        order_plan = OrderPlan.model_validate(state.orderPlan)
        status_reader = getattr(self.broker_client, "refresh_order_status", None)
        broker_status = status_reader(state.clientOrderId) if callable(status_reader) else None
        fill = self.broker_client.refresh_order(state.clientOrderId)
        if fill and fill.filledQuantity > 0:
            updated = self.execution_adapter.process_fill_event(
                clientOrderId=state.clientOrderId,
                fillUpdate=fill,
                evaluatedAt=observed_at,
                entriesBlockedByProfile=not bool(self._entry_permission().get("newEntriesAllowed", False)),
            )
            self.repository.upsert_broker_fill(
                fill,
                order_plan=order_plan,
                order_intent_id=updated.parentDecisionId,
                observed_at=observed_at,
                reason_codes=("voting_ensemble.paper_execution.position_created_only_from_broker_fill",),
            )
            self.repository.mark_reconciliation_resolved(state.symbol.upper(), reason_code="voting_ensemble.paper_execution.unknown_order_state_resolved_by_broker_fill")
            protective = self._ensure_protective_orders(updated, fill, observed_at=observed_at)
            updates.append({"clientOrderId": state.clientOrderId, "status": updated.status, "filledQuantity": fill.filledQuantity, "protective": protective})
        elif broker_status in {"REJECTED", "CANCELED", "EXPIRED"}:
            updated = self.execution_adapter.state_store.put(
                state.model_copy(
                    update={
                        "status": broker_status,
                        "entryOrderStatus": broker_status,
                        "reconciliationStatus": "RECONCILED",
                        "updatedAt": observed_at,
                        "reasonCodes": [*state.reasonCodes, f"voting_ensemble.paper_execution.broker_{str(broker_status).lower()}_mirrored"],
                        "completeReasonCodes": [*state.completeReasonCodes, f"voting_ensemble.paper_execution.broker_{str(broker_status).lower()}_mirrored"],
                    }
                )
            )
            self.repository.mark_reconciliation_resolved(state.symbol.upper(), reason_code="voting_ensemble.paper_execution.unknown_order_state_resolved_by_terminal_broker_status")
            updates.append({"clientOrderId": state.clientOrderId, "status": updated.status})
        if state.status in {"PLANNED", "SUBMITTED", "ACCEPTED"} and observed_at - state.createdAt > timedelta(seconds=60):
            cancel_order = getattr(self.broker_client, "cancel_order", None)
            if callable(cancel_order) and cancel_order(state.clientOrderId):
                updated = self.execution_adapter.state_store.put(
                    state.model_copy(
                        update={
                            "status": "EXPIRED",
                            "entryOrderStatus": "EXPIRED",
                            "reconciliationStatus": "RECONCILED",
                            "updatedAt": observed_at,
                            "reasonCodes": [*state.reasonCodes, "voting_ensemble.paper_execution.expired_entry_order_canceled_at_broker"],
                            "completeReasonCodes": [*state.completeReasonCodes, "voting_ensemble.paper_execution.expired_entry_order_canceled_at_broker"],
                        }
                    )
                )
                updates.append({"clientOrderId": state.clientOrderId, "status": updated.status})
        if state.clientOrderId not in {order.clientOrderId for order in open_orders} and state.status in {"SUBMITTING", "SUBMITTED", "ACCEPTED"} and not fill:
            self.execution_adapter.state_store.mark_unknown_order_state(state.symbol)
            updates.append({"clientOrderId": state.clientOrderId, "status": "RECONCILIATION_REQUIRED", "reasonCodes": ["voting_ensemble.paper_execution.broker_local_divergence_unknown_order_state"]})
        return updates

    def _ensure_protective_orders(self, state: VotingEnsembleExecutionState, fill: BrokerFillUpdate, *, observed_at: datetime) -> dict[str, Any]:
        if fill.filledQuantity <= 0:
            return {"submitted": False, "reasonCodes": ["voting_ensemble.paper_execution.no_fill_no_protection"]}
        order_plan = OrderPlan.model_validate(state.orderPlan)
        desired_quantity = min(fill.filledQuantity, state.requestedQuantity or fill.filledQuantity)
        existing_ids = list(state.protectiveOrderIds)
        primary_id = existing_ids[0] if existing_ids else f"{state.clientOrderId}-protective"
        actual_protective_exists = _snapshot_exists(self.repository, f"paper_order_gateway.protective.{primary_id}")
        replace_order = getattr(self.broker_client, "replace_order", None)
        submit_protective = getattr(self.broker_client, "submit_protective_order", None)
        ack: BrokerOrderAck | None = None
        replaced: dict[str, Any] | None = None
        if actual_protective_exists and callable(replace_order):
            replaced = replace_order(primary_id, quantity=desired_quantity, stop_price=order_plan.stopPrice)
        if not actual_protective_exists and callable(submit_protective):
            ack = submit_protective(
                symbol=order_plan.symbol,
                side=order_plan.side,
                quantity=desired_quantity,
                stop_price=order_plan.stopPrice,
                target_price=order_plan.targetPrice,
                client_order_id=primary_id,
            )
        if ack is None and replaced is None:
            return {"submitted": False, "replaced": False, "clientOrderId": primary_id, "quantity": desired_quantity, "reasonCodes": ["voting_ensemble.paper_execution.protective_order_broker_api_unavailable"]}
        protective_ids = list(dict.fromkeys([*existing_ids, primary_id if ack or replaced is not None else ""]))
        updated = self.execution_adapter.state_store.put(
            state.model_copy(
                update={
                    "protectiveOrderIds": [value for value in protective_ids if value],
                    "protectiveOrder": {
                        "clientOrderId": primary_id,
                        "parentClientOrderId": state.clientOrderId,
                        "quantity": desired_quantity,
                        "stopPrice": order_plan.stopPrice,
                        "targetPrice": order_plan.targetPrice,
                        "reasonCodes": ["broker.protective_quantity_matches_actual_fill"],
                    },
                    "updatedAt": observed_at,
                    "reasonCodes": [*state.reasonCodes, "voting_ensemble.paper_execution.actual_protective_order_confirmed_or_updated"],
                    "completeReasonCodes": [*state.completeReasonCodes, "voting_ensemble.paper_execution.actual_protective_order_confirmed_or_updated"],
                }
            )
        )
        self.repository.write_snapshot(
            f"paper_order_gateway.protective.{primary_id}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "source": "alpaca_paper_broker",
                "sourceAuthority": "alpaca_paper_broker",
                "clientOrderId": primary_id,
                "parentClientOrderId": state.clientOrderId,
                "brokerOrderId": ack.brokerOrderId if ack else (replaced or {}).get("id"),
                "status": ack.status if ack else str((replaced or {}).get("status") or "ACCEPTED").upper(),
                "quantity": desired_quantity,
                "stopPrice": order_plan.stopPrice,
                "targetPrice": order_plan.targetPrice,
                "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
                "reasonCodes": [
                    "voting_ensemble.paper_execution.protective_order_actual_broker_order",
                    "voting_ensemble.paper_execution.protective_quantity_capped_to_confirmed_fill",
                ],
            },
        )
        return {"submitted": ack is not None, "replaced": replaced is not None, "clientOrderId": primary_id, "quantity": desired_quantity, "stateUpdatedAt": updated.updatedAt.isoformat().replace("+00:00", "Z")}

    def _expire_or_flatten_positions(self, *, now: datetime, broker_positions: list[BrokerPositionState], open_orders: list[BrokerOrderState]) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        for position in broker_positions:
            if position.quantity <= 0:
                continue
            entry_state = _oldest_state_for_symbol(self.execution_adapter.state_store, position.symbol, side=position.side)
            max_minutes = _maximum_holding_minutes(entry_state)
            if _has_open_exit_for_position(open_orders, position):
                continue
            if entry_state and now - entry_state.createdAt > timedelta(minutes=max_minutes):
                updates.append(self._submit_position_reducing_exit(position, reason_code="voting_ensemble.paper_execution.maximum_holding_time_exit_submitted", observed_at=now))
            clock_reader = getattr(self.broker_client, "refresh_market_clock", None)
            clock = clock_reader() if callable(clock_reader) else {}
            if _clock_requires_eod_flatten(clock, now=now):
                updates.append(self._submit_position_reducing_exit(position, reason_code="voting_ensemble.paper_execution.end_of_day_flattening_exit_submitted", observed_at=now))
        return updates

    def _submit_position_reducing_exit(self, position: BrokerPositionState, *, reason_code: str, observed_at: datetime) -> dict[str, Any]:
        client_order_id = f"ve-exit-{_hash({'symbol': position.symbol, 'side': position.side, 'quantity': position.quantity, 'reason': reason_code, 'at': observed_at.isoformat()})[:20]}"
        side = Signal.SELL if Signal(position.side) == Signal.BUY else Signal.BUY
        limit_price = max(0.01, position.markPrice)
        submit_exit = getattr(self.broker_client, "submit_position_exit_order", None)
        if callable(submit_exit):
            ack = submit_exit(symbol=position.symbol, side=side, quantity=position.quantity, limit_price=limit_price, client_order_id=client_order_id)
        else:
            stop_price, target_price = _entry_geometry_for_exit_fallback(side, limit_price)
            fallback_plan = OrderPlan(
                orderPlanId=client_order_id,
                candidateId=client_order_id,
                symbol=position.symbol,
                side=side,
                orderType="LIMIT",
                quantity=position.quantity,
                entryPrice=limit_price,
                stopPrice=stop_price,
                targetPrice=target_price,
                limitPrice=limit_price,
                maximumHoldingMinutes=1,
                timeInForce="DAY",
                eligible=True,
                validationErrors=[],
                explanation="Voting Ensemble broker-authoritative risk-reducing exit.",
                generatedAt=observed_at,
                sessionDate=observed_at.date(),
                configurationHash=_hash({"reason": reason_code, "position": position.model_dump(mode="json")}),
            )
            ack = self.broker_client.submit_order(fallback_plan, client_order_id)
        plan_payload = {
            "orderPlanId": client_order_id,
            "symbol": position.symbol,
            "side": side,
            "orderType": "LIMIT",
            "quantity": position.quantity,
            "limitPrice": limit_price,
            "intent": "position_reducing_exit",
            "configurationHash": _hash({"reason": reason_code, "position": position.model_dump(mode="json")}),
        }
        self.repository.write_snapshot(
            f"paper_order_gateway.intent.{client_order_id}",
            {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "source": "alpaca_paper_broker",
                "sourceAuthority": "alpaca_paper_broker",
                "clientOrderId": client_order_id,
                "status": ack.status,
                "submitted": ack.status in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED"},
                "brokerAck": ack.model_dump(mode="json"),
                "orderPlan": plan_payload,
                "createdAt": observed_at.isoformat().replace("+00:00", "Z"),
                "updatedAt": observed_at.isoformat().replace("+00:00", "Z"),
                "reasonCodes": [reason_code, "voting_ensemble.paper_execution.risk_reducing_exit_allowed_when_entries_blocked"],
            },
        )
        return {"clientOrderId": client_order_id, "status": ack.status, "reasonCodes": [reason_code]}

    def _process_with_execution_adapter(
        self,
        intent: VotingEnsemblePaperOrderIntent,
        outbox_record: Mapping[str, Any],
        *,
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        adapter_result: VotingEnsembleExecutionAdapterResult | None = None
        executable_quantity = _executable_quantity_for_order_plan(self.repository, intent.orderPlan)
        executable_order_plan = intent.orderPlan.model_copy(update={"quantity": executable_quantity})
        try:
            adapter_result = self.execution_adapter.submit_order_once(
                orderPlan=executable_order_plan,
                broker=self.broker_client,
                idempotencyKey=str(outbox_record.get("executionIdempotencyKey") or intent.idempotencyKey),
                evaluatedAt=evaluated_at,
                approvedSettingsHash=_approved_settings_hash(outbox_record),
                parentDecisionId=intent.decisionId,
                parentEventId=intent.sourceCommandId or intent.sourceJobId,
            )
            if adapter_result.fillUpdate and adapter_result.fillUpdate.filledQuantity > 0:
                state = self.execution_adapter.state_store.get(adapter_result.clientOrderId)
                if state is not None:
                    self._ensure_protective_orders(state, adapter_result.fillUpdate, observed_at=evaluated_at)
            self._capture_adapter_result(intent, adapter_result)
            self.repository.mark_outbox_status(
                intent,
                _outbox_status_from_adapter_result(adapter_result),
                result=adapter_result.model_dump(mode="json"),
                reason_codes=tuple(adapter_result.reasonCodes),
            )
        except VotingEnsemblePaperExecutionPersistenceError as exc:
            self.repository.record_persistence_failure(exc)
            return _persistence_failure_result(
                intent,
                evaluated_at=evaluated_at,
                submitted=bool(adapter_result and adapter_result.submitted),
                client_order_id=adapter_result.clientOrderId if adapter_result else None,
            )
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "orderIntentId": intent.orderIntentId,
            "clientOrderId": adapter_result.clientOrderId,
            "submitted": adapter_result.submitted,
            "status": adapter_result.status,
            "adapterResult": adapter_result.model_dump(mode="json"),
            "reasonCodes": ["voting_ensemble.paper_execution.adapter_worker.processed", *adapter_result.reasonCodes],
        }

    def _capture_adapter_result(self, intent: VotingEnsemblePaperOrderIntent, result: VotingEnsembleExecutionAdapterResult) -> None:
        order_payload = {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "source": "voting_ensemble.execution_adapter",
            "orderIntentId": intent.orderIntentId,
            "decisionId": intent.decisionId,
            "clientOrderId": result.clientOrderId,
            "status": result.status,
            "submitted": result.submitted,
            "brokerAccepted": result.brokerAccepted,
            "orderPlan": result.orderPlan.model_dump(mode="json"),
            "brokerAck": result.brokerAck.model_dump(mode="json") if result.brokerAck else None,
            "createdAt": result.evaluatedAt.isoformat().replace("+00:00", "Z"),
            "updatedAt": result.evaluatedAt.isoformat().replace("+00:00", "Z"),
            "reasonCodes": list(result.reasonCodes),
        }
        self.repository.write_snapshot(f"paper_order_gateway.intent.{intent.orderIntentId}", order_payload)
        if result.fillUpdate:
            self.repository.upsert_broker_fill(
                BrokerFillUpdate(
                    clientOrderId=result.clientOrderId,
                    filledQuantity=result.fillUpdate.filledQuantity,
                    averageFillPrice=result.fillUpdate.averageFillPrice,
                    status=result.fillUpdate.status,
                    updatedAt=result.fillUpdate.updatedAt,
                ),
                order_plan=result.orderPlan,
                order_intent_id=intent.orderIntentId,
                observed_at=result.evaluatedAt,
                reason_codes=tuple(result.reasonCodes),
            )

    def _mark_uncertain_restart(self, intent: VotingEnsemblePaperOrderIntent, *, evaluated_at: datetime | None = None) -> dict[str, Any]:
        now = _require_utc(evaluated_at or datetime.now(UTC))
        result = {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "orderIntentId": intent.orderIntentId,
            "clientOrderId": voting_ensemble_gateway_client_order_id(intent),
            "submitted": False,
            "status": "RECONCILIATION_REQUIRED",
            "evaluatedAt": now.isoformat().replace("+00:00", "Z"),
            "reasonCodes": ["voting_ensemble.paper_execution.uncertain_submission_state_reconciliation_required"],
        }
        self.repository.mark_outbox_status(
            intent,
            "RECONCILIATION_REQUIRED",
            result=result,
            reason_codes=tuple(result["reasonCodes"]),
        )
        return result

    def _entry_permission(self) -> Mapping[str, Any]:
        repository_block = _repository_entry_blocks(self.repository)
        if self.entry_permission_provider is None:
            permission = {
                "newEntriesAllowed": True,
                "effectivePaperTradingEnabled": True,
                "reasonCodes": ["voting_ensemble.paper_execution.no_control_provider_test_mode"],
            }
        else:
            permission = self.entry_permission_provider()
        return _merge_permission_with_persistence(permission, repository_block)


class VotingEnsemblePaperExecutionRuntime:
    def __init__(
        self,
        *,
        repository: VotingEnsemblePaperExecutionRepository | None = None,
        queue: VotingEnsemblePaperExecutionQueue | None = None,
        paper_gateway: PaperOrderGateway | None = None,
        broker_client: PaperBrokerClient | None = None,
        execution_adapter: VotingEnsembleExecutionAdapter | None = None,
        entry_permission_provider: Callable[[], Mapping[str, Any]] | None = None,
        short_trading_enabled: bool = False,
        execution_mode: VotingEnsembleExecutionMode = VOTING_ENSEMBLE_DEFAULT_EXECUTION_MODE,
        auto_start: bool = False,
    ) -> None:
        provided_gateway = paper_gateway is not None
        self.repository = repository or VotingEnsemblePaperExecutionRepository()
        self.queue = queue or VotingEnsemblePaperExecutionQueue()
        self.execution_mode: VotingEnsembleExecutionMode = _normalize_execution_mode(execution_mode)
        if self.execution_mode == "LOCAL_PAPER" and broker_client is not None:
            raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble LOCAL_PAPER mode cannot be constructed with a broker trading client")
        if self.execution_mode == "LOCAL_PAPER" and paper_gateway is not None and not _gateway_is_local_paper(paper_gateway):
            raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble LOCAL_PAPER mode requires a local paper gateway")
        if self.execution_mode == "BROKER_PAPER" and broker_client is None and not provided_gateway:
            broker_client = _default_paper_broker_client()
        self.paper_gateway = paper_gateway or PaperOrderGateway(
            VotingEnsembleLocalPaperBroker(self.repository) if self.execution_mode == "LOCAL_PAPER" else _default_paper_broker(),
            self.repository,
            execution_mode=self.execution_mode,
        )
        self.broker_client = broker_client if self.execution_mode == "BROKER_PAPER" else None
        self.execution_adapter = execution_adapter or _default_execution_adapter_for_runtime(repository=self.repository, broker_client=self.broker_client)
        self.entry_permission_provider = entry_permission_provider
        self.short_trading_enabled = short_trading_enabled
        self.worker = VotingEnsemblePaperExecutionWorker(
            queue=self.queue,
            repository=self.repository,
            paper_gateway=self.paper_gateway,
            entry_permission_provider=self._entry_permission,
            execution_adapter=self.execution_adapter,
            broker_client=self.broker_client,
            short_trading_enabled=self.short_trading_enabled,
        )
        self._thread: VotingEnsemblePaperExecutionWorkerThread | None = None
        self.autoManageWorker = auto_start
        if auto_start:
            self._requeue_recoverable_intents()
            self.start()

    def enqueue_from_decision(
        self,
        decision: Mapping[str, Any],
        *,
        correlation_id: str,
        idempotency_key: str,
        source_job_id: str | None,
        source_command_id: str | None,
        evaluated_at: datetime,
        source_command_kind: str = "finalized_bar_evaluation",
    ) -> dict[str, Any]:
        if source_command_kind != "finalized_bar_evaluation":
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "reasonCodes": ["voting_ensemble.paper_execution.non_finalized_command_cannot_create_automatic_intent"],
            }
        order_plan = eligible_order_plan_from_decision(decision)
        if order_plan is None:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "reasonCodes": ["voting_ensemble.paper_execution.no_eligible_order_plan"],
            }
        exit_intent = _is_risk_reducing_order_plan(self.repository, order_plan)
        short_blockers = _short_entry_blockers(
            repository=self.repository,
            order_plan=order_plan,
            short_trading_enabled=self.short_trading_enabled,
        )
        if short_blockers:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "reasonCodes": short_blockers,
            }
        permission = self._entry_permission()
        if not exit_intent and not bool(permission.get("newEntriesAllowed", permission.get("effectivePaperTradingEnabled", False))):
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "reasonCodes": ["voting_ensemble.paper_execution.control_blocked_before_intent", *list(permission.get("reasonCodes") or permission.get("blockers") or ())],
                "control": dict(permission),
            }
        mark_blockers = _local_mark_blocks_new_entry(self.repository, order_plan, evaluated_at=evaluated_at)
        if mark_blockers:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "reasonCodes": list(mark_blockers),
            }
        intent = VotingEnsemblePaperOrderIntent(
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            orderIntentId=_order_intent_id(order_plan, idempotency_key),
            decisionId=str(decision.get("decision_id") or order_plan.orderPlanId),
            correlationId=correlation_id,
            idempotencyKey=idempotency_key,
            orderPlan=order_plan,
            localGatePassed=not bool(decision.get("safety_gate_failed")),
            createdAt=_require_utc(evaluated_at),
            sourceJobId=source_job_id,
            sourceCommandId=source_command_id,
        )
        try:
            outbox_record, inserted = self.repository.reserve_decision_and_outbox(decision, intent)
        except VotingEnsemblePaperExecutionPersistenceError:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "enqueued": False,
                "persistenceHealthy": False,
                "highSeverityRuntimeWarnings": self.repository.runtime_warnings(),
                "reasonCodes": ["voting_ensemble.paper_execution.persistence_failure_blocks_new_entries"],
            }
        if not inserted:
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "orderIntentId": intent.orderIntentId,
                "enqueued": False,
                "deduplicated": True,
                "outboxStatus": outbox_record.get("status"),
                "executionIdempotencyKey": outbox_record.get("executionIdempotencyKey"),
                "reasonCodes": ["voting_ensemble.paper_execution.intent_deduplicated"],
            }
        accepted = self.queue.enqueue(intent)
        if self.autoManageWorker:
            self.start()
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "orderIntentId": intent.orderIntentId,
            "enqueued": accepted,
            "deduplicated": not accepted,
            "outboxStatus": outbox_record.get("status"),
            "executionIdempotencyKey": outbox_record.get("executionIdempotencyKey"),
            "reasonCodes": ["voting_ensemble.paper_execution.decision_and_intent_persisted", "voting_ensemble.paper_execution.intent_enqueued"],
        }

    def process_once(self, *, timeout: float | None = 0.0, evaluated_at: datetime | None = None) -> dict[str, Any] | None:
        intent = self.queue.pop(timeout=timeout)
        if intent is None:
            pending = self.repository.pending_intents()
            if pending:
                intent = pending[0]
            else:
                uncertain = self.repository.uncertain_intents()
                if not uncertain:
                    return self.reconcile_broker_state(evaluated_at=evaluated_at or datetime.now(UTC))
                return self.worker._mark_uncertain_restart(uncertain[0], evaluated_at=evaluated_at)
        now = _require_utc(evaluated_at or datetime.now(UTC))
        claimed = self.repository.claim_intent(intent, worker_id=self.worker.worker_id, claimed_at=now)
        if claimed is None:
            return None
        if str(claimed.get("status") or claimed.get("state") or "").upper() in VOTING_ENSEMBLE_UNCERTAIN_OUTBOX_STATES:
            return self.worker._mark_uncertain_restart(intent, evaluated_at=now)
        exit_intent = _is_risk_reducing_order_plan(self.repository, intent.orderPlan)
        short_blockers = _short_entry_blockers(
            repository=self.repository,
            order_plan=intent.orderPlan,
            short_trading_enabled=self.short_trading_enabled,
        )
        if short_blockers:
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "reasonCodes": short_blockers,
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        permission = self._entry_permission()
        if not exit_intent and not bool(permission.get("newEntriesAllowed", permission.get("effectivePaperTradingEnabled", False))):
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "control": dict(permission),
                "reasonCodes": ["voting_ensemble.paper_execution.control_blocked_before_broker_submission", *list(permission.get("reasonCodes") or permission.get("blockers") or ())],
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        mark_blockers = _local_mark_blocks_new_entry(self.repository, intent.orderPlan, evaluated_at=now)
        if mark_blockers:
            result = {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
                "orderIntentId": intent.orderIntentId,
                "submitted": False,
                "status": "BLOCKED",
                "reasonCodes": list(mark_blockers),
            }
            self.repository.mark_outbox_status(intent, "BLOCKED", result=result, reason_codes=tuple(result["reasonCodes"]))
            return result
        return self.worker.process_intent(intent, evaluated_at=evaluated_at)

    def reconcile_broker_state(self, *, evaluated_at: datetime | None = None) -> dict[str, Any] | None:
        now = evaluated_at or datetime.now(UTC)
        if self.execution_mode == "LOCAL_PAPER":
            recovery = self.repository.recover_local_inventory_from_persistence(evaluated_at=_require_utc(now))
            evaluator = getattr(self.paper_gateway.broker, "evaluate_open_protective_orders", None)
            protective_fills = evaluator() if callable(evaluator) else []
            eod_updates = self._local_end_of_day_updates(evaluated_at=_require_utc(now))
            inventory = self.repository.inventory_snapshot()
            status = "RECONCILED" if recovery.get("status") == "RECOVERED" and not inventory.get("reconciliationBlocks") else "RECONCILIATION_REQUIRED"
            return {
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "executionMode": "LOCAL_PAPER",
                "status": status,
                "recovery": recovery,
                "ordersObserved": len(inventory.get("orders") or []),
                "positionsObserved": len(inventory.get("positions") or []),
                "protectiveFillsObserved": len(protective_fills),
                "eodUpdates": eod_updates,
                "eodExitsSubmitted": len([item for item in eod_updates if item.get("submitted")]),
                "brokerPositionsObserved": 0,
                "evaluatedAt": _require_utc(now).isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["voting_ensemble.local_paper.reconciliation_uses_local_inventory_only"],
            }
        result = self.worker.reconcile_broker_state(evaluated_at=now)
        if result is not None:
            return result
        return self.paper_gateway.recover_from_restart(evaluated_at=now)

    def mark_to_market_from_payload(self, payload: Mapping[str, Any] | None, *, observed_at: datetime | None = None) -> dict[str, Any] | None:
        if self.execution_mode != "LOCAL_PAPER" or not isinstance(payload, Mapping):
            return None
        clock = _extract_market_clock_payload(payload)
        if clock:
            self.update_local_market_clock(clock, observed_at=observed_at)
        nbbo = _extract_nbbo_payload(payload)
        symbol = str(payload.get("symbol") or _nested_value(payload, ("marketEvent", "symbol")) or "SPY").upper()
        point_in_time = (
            _parse_time(payload.get("data_timestamp"))
            or _parse_time(_nested_value(payload, ("market_context", "data_timestamp")))
            or _parse_time(_nested_value(payload, ("marketEvent", "receivedAt")))
            or observed_at
            or datetime.now(UTC)
        )
        return self.repository.mark_local_positions_from_market_data(
            symbol=symbol,
            nbbo=nbbo,
            observed_at=_require_utc(point_in_time),
        )

    def update_local_market_clock(self, clock: Mapping[str, Any], *, observed_at: datetime | None = None) -> dict[str, Any]:
        observed = _require_utc(observed_at or datetime.now(UTC))
        due = _clock_requires_eod_flatten(clock, now=observed)
        payload = {
            "schemaVersion": "voting_ensemble_local_market_clock_v1",
            "executionMode": "LOCAL_PAPER",
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "sourceAuthority": str(clock.get("sourceAuthority") or clock.get("source") or "local_market_data_clock"),
            "isOpen": bool(clock.get("isOpen", clock.get("is_open", True))),
            "forceClose": bool(clock.get("forceClose") or clock.get("requiresEodFlatten")),
            "requiresEodFlatten": bool(due),
            "nextClose": _iso_or_none(_parse_time(clock.get("nextClose") or clock.get("next_close"))),
            "timestamp": _iso_or_none(_parse_time(clock.get("timestamp"))) or observed.isoformat().replace("+00:00", "Z"),
            "observedAt": observed.isoformat().replace("+00:00", "Z"),
            "reasonCodes": [
                "voting_ensemble.local_paper.market_clock_eod_flatten_due"
                if due
                else "voting_ensemble.local_paper.market_clock_recorded"
            ],
        }
        self.repository.write_snapshot("local_market_clock.latest", payload)
        return payload

    def _local_end_of_day_updates(self, *, evaluated_at: datetime) -> list[dict[str, Any]]:
        clock = self._local_market_clock_snapshot()
        if not _clock_requires_eod_flatten(clock, now=evaluated_at):
            return []
        self.repository.write_snapshot(
            "local_entry_control.end_of_day",
            {
                "schemaVersion": "voting_ensemble_local_entry_control_v1",
                "executionMode": "LOCAL_PAPER",
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "newEntriesAllowed": False,
                "positionReducingExitsAllowed": True,
                "observedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["voting_ensemble.local_paper.end_of_day_blocks_new_entries"],
            },
        )
        broker = getattr(self.paper_gateway, "broker", None)
        submit_eod = getattr(broker, "submit_end_of_day_liquidation", None)
        if not callable(submit_eod):
            return [
                {
                    "status": "RECONCILIATION_REQUIRED",
                    "submitted": False,
                    "reasonCodes": ["voting_ensemble.local_paper.end_of_day_local_liquidation_engine_unavailable"],
                }
            ]
        return submit_eod(evaluated_at=evaluated_at)

    def _local_market_clock_snapshot(self) -> dict[str, Any]:
        try:
            return self.repository.read_snapshot("local_market_clock.latest")
        except KeyError:
            return {}

    def _entry_permission(self) -> Mapping[str, Any]:
        repository_block = _repository_entry_blocks(self.repository)
        if self.entry_permission_provider is None:
            permission = {
                "newEntriesAllowed": True,
                "effectivePaperTradingEnabled": True,
                "reasonCodes": ["voting_ensemble.paper_execution.no_control_provider_test_mode"],
            }
        else:
            permission = self.entry_permission_provider()
        return _merge_permission_with_persistence(permission, repository_block)

    def start(self) -> None:
        self._requeue_recoverable_intents()
        if self.execution_mode == "LOCAL_PAPER":
            self.repository.recover_local_inventory_from_persistence(evaluated_at=datetime.now(UTC))
        if self.broker_client is not None:
            self.reconcile_broker_state(evaluated_at=datetime.now(UTC))
        if self._thread is None or not self._thread.is_alive():
            self._thread = VotingEnsemblePaperExecutionWorkerThread(self.worker)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread = None

    def summary(self) -> dict[str, Any]:
        thread = self._thread.snapshot() if self._thread is not None else {"alive": False}
        inventory = self.repository.inventory_snapshot()
        outbox_status_counts: dict[str, int] = {}
        for record in inventory.get("outbox") or []:
            status = str(record.get("status") or record.get("state") or "UNKNOWN").upper()
            outbox_status_counts[status] = outbox_status_counts.get(status, 0) + 1
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "executionMode": self.execution_mode,
            "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
            "executionNamespace": VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE,
            "gatewayNamespace": VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "workerAlive": bool(thread.get("alive")),
            "workerThread": thread,
            "queue": self.queue.snapshot(),
            "storedSnapshots": len(self.repository.snapshots),
            "outboxStatusCounts": outbox_status_counts,
            "persistenceHealthy": self.repository.persistenceHealthy,
            "persistencePath": str(self.repository.path) if self.repository.path is not None else None,
            "lastPersistenceError": self.repository.lastPersistenceError,
            "lastPersistenceErrorAt": self.repository.lastPersistenceErrorAt,
            "highSeverityRuntimeWarnings": self.repository.runtime_warnings(),
            "durableExecutionStateRequired": self.execution_mode == "BROKER_PAPER",
            "durableExecutionStateActive": isinstance(self.execution_adapter.state_store, VotingEnsembleDurableExecutionStateStore),
            "executionStateCount": len(inventory.get("executionStates") or []),
            "localRecovery": inventory.get("localRecovery"),
        }

    def inventory_snapshot(self) -> dict[str, Any]:
        return self.repository.inventory_snapshot()

    def _requeue_recoverable_intents(self) -> None:
        for intent in self.repository.pending_intents():
            self.queue.enqueue(intent)


class VotingEnsemblePaperExecutionWorkerThread:
    def __init__(self, worker: VotingEnsemblePaperExecutionWorker) -> None:
        self.worker = worker
        self._stop = Event()
        self._thread = Thread(target=self._run, name="voting-ensemble-paper-execution-worker", daemon=True)
        self.startedAt: str | None = None
        self.lastError: str | None = None
        self.lastErrorAt: str | None = None

    def start(self) -> None:
        if not self._thread.is_alive():
            self.startedAt = datetime.now(UTC).isoformat()
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        return {
            "alive": self.is_alive(),
            "startedAt": self.startedAt,
            "lastError": self.lastError,
            "lastErrorAt": self.lastErrorAt,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.worker.process_once(timeout=0.25)
            except Exception as exc:  # pragma: no cover - defensive worker guard
                self.lastError = str(exc) or type(exc).__name__
                self.lastErrorAt = datetime.now(UTC).isoformat()
                sleep(0.25)


class VotingEnsembleLocalPaperBroker:
    """Voting Ensemble-owned local paper broker.

    This broker never submits to Alpaca or any external account. It simulates
    orders and fills against the repository-owned Voting Ensemble account only.
    """

    configured = True
    broker_kind = "voting_ensemble_local_paper"
    paper_endpoint = True

    def __init__(self, repository: VotingEnsemblePaperExecutionRepository) -> None:
        self.repository = repository
        self.engine = VotingEnsembleLocalPaperExecutionEngine(repository)

    def verify_paper_account(self) -> bool:
        return self.engine.verify_account()

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        return self.engine.submit_order(intent)

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        return self.engine.refresh_order(client_order_id)

    def cancel_order(self, client_order_id: str) -> bool:
        return self.engine.cancel_order(client_order_id)

    def refresh_positions(self) -> list[dict[str, Any]]:
        return self.engine.refresh_positions()

    def evaluate_open_protective_orders(self) -> list[PaperGatewayFill]:
        return self.engine.evaluate_open_protective_orders()

    def submit_end_of_day_liquidation(self, *, evaluated_at: datetime) -> list[dict[str, Any]]:
        return self.engine.submit_end_of_day_liquidation(evaluated_at=evaluated_at)


class VotingEnsembleLocalPaperExecutionEngine:
    """Authoritative LOCAL_PAPER execution engine for Voting Ensemble.

    The engine accepts only Voting Ensemble-owned gateway intents, validates
    local inventory/cash/risk, creates local orders, simulates fills, applies
    those fills atomically to the canonical inventory ledger, and persists every
    order/fill/status transition. It never calls Alpaca trading endpoints.
    """

    execution_mode = "LOCAL_PAPER"

    def __init__(self, repository: VotingEnsemblePaperExecutionRepository) -> None:
        self.repository = repository

    def verify_account(self) -> bool:
        account = self.repository.local_account_snapshot()
        return (
            account.get("algorithmId") == VOTING_ENSEMBLE_ALGORITHM_ID
            and account.get("capitalPartitionId") == VOTING_ENSEMBLE_CAPITAL_PARTITION_ID
            and account.get("accountId") == VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID
            and account.get("buyingPowerModel") == "LOCAL_CASH_NO_MARGIN_LONG_ONLY"
            and account.get("allowMargin") is False
            and account.get("allowShorts") is False
        )

    def submit_order(self, intent: Any) -> PaperGatewayBrokerAck:
        reason = self._submission_rejection_reason(intent)
        if reason is not None:
            self._record_rejected_order(intent, reason)
            return PaperGatewayBrokerAck(
                clientOrderId=str(getattr(intent, "clientOrderId", "") or "voting-ensemble-local-rejected"),
                brokerOrderId=None,
                status="REJECTED",
                rejectedReason=reason,
            )
        observed_at = _require_utc(getattr(intent, "createdAt", None) or datetime.now(UTC))
        self.repository.inventory_ledger.create_order(intent, observed_at=observed_at)
        self.repository.write_snapshot(
            f"local_execution.{intent.clientOrderId}",
            {
                "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                "executionMode": "LOCAL_PAPER",
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "clientOrderId": intent.clientOrderId,
                "orderIntentId": intent.orderIntentId,
                "status": "OPEN",
                "simulated": True,
                "createdAt": observed_at.isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["voting_ensemble.local_paper_execution_engine.order_opened"],
            },
        )
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"local-{intent.clientOrderId}",
            status="OPEN",
            acceptedAt=observed_at,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            order = self.repository.read_snapshot(f"local_order.{client_order_id}")
        except KeyError:
            try:
                existing = self.repository.read_snapshot(f"paper_order_gateway.fill.{client_order_id}")
                return _paper_gateway_fill_from_payload(existing)
            except Exception:
                return None
        if str(order.get("status") or "").upper() == "FILLED":
            try:
                return _paper_gateway_fill_from_payload(self.repository.read_snapshot(f"paper_order_gateway.fill.{client_order_id}"))
            except Exception:
                return None
        transaction = getattr(self.repository, "transaction", None)
        if callable(transaction):
            with transaction():
                fill = self._simulate_and_apply_fill(order)
        else:
            fill = self._simulate_and_apply_fill(order)
        return fill

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            order = self.repository.read_snapshot(f"local_order.{client_order_id}")
        except KeyError:
            return False
        if int(order.get("filledQuantity") or 0) > 0:
            return False
        canceled = self.repository.inventory_ledger.cancel_order(client_order_id, canceled_at=datetime.now(UTC))
        if canceled:
            self.repository.write_snapshot(
                f"local_execution.{client_order_id}",
                {
                    "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                    "executionMode": "LOCAL_PAPER",
                    "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                    "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                    "clientOrderId": client_order_id,
                    "orderIntentId": order.get("orderIntentId"),
                    "status": "CANCELED",
                    "simulated": True,
                    "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "reasonCodes": ["voting_ensemble.local_paper_execution_engine.order_canceled"],
                },
            )
        return canceled

    def refresh_positions(self) -> list[dict[str, Any]]:
        self.evaluate_open_protective_orders()
        return list(self.repository.inventory_snapshot().get("positions") or [])

    def evaluate_open_protective_orders(self) -> list[PaperGatewayFill]:
        fills: list[PaperGatewayFill] = []
        for order in self.repository.inventory_ledger.orders():
            if not order.get("protectiveKind"):
                continue
            if str(order.get("status") or "").upper() not in {"OPEN", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}:
                continue
            fill = self.refresh_order(str(order.get("clientOrderId") or ""))
            if fill is not None:
                fills.append(fill)
        return fills

    def submit_end_of_day_liquidation(self, *, evaluated_at: datetime) -> list[dict[str, Any]]:
        observed = _require_utc(evaluated_at)
        updates: list[dict[str, Any]] = []
        for position in self.repository.inventory_ledger.positions():
            symbol = str(position.get("symbol") or "").upper()
            quantity = max(0, int(position.get("quantity") or position.get("signedQuantity") or 0))
            if not symbol or quantity <= 0:
                continue
            if self._has_open_local_exit(symbol, reason="END_OF_DAY_LIQUIDATION"):
                updates.append(
                    {
                        "symbol": symbol,
                        "submitted": False,
                        "status": "OPEN",
                        "reasonCodes": ["voting_ensemble.local_paper.end_of_day_exit_already_open"],
                    }
                )
                continue
            limit_price = max(0.01, _float(position.get("markPrice") or position.get("averageEntryPrice") or position.get("averagePrice")))
            client_order_id = f"ve-eod-{_hash({'symbol': symbol, 'quantity': quantity, 'at': observed.isoformat()})[:20]}"
            intent = _LocalPaperEngineIntent(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                accountId=VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                orderIntentId=client_order_id,
                decisionId=client_order_id,
                clientOrderId=client_order_id,
                symbol=symbol,
                side=Signal.SELL,
                orderType="LIMIT",
                submittedQuantity=quantity,
                limitPrice=limit_price,
                triggerPrice=limit_price,
                stopPrice=None,
                targetPrice=None,
                plannedRiskDollars=0.0,
                createdAt=observed,
                timeInForce="DAY",
                exitReason="END_OF_DAY_LIQUIDATION",
            )
            ack = self.submit_order(intent)
            fill = self.refresh_order(client_order_id) if ack.status != "REJECTED" else None
            updates.append(
                {
                    "symbol": symbol,
                    "clientOrderId": client_order_id,
                    "submitted": ack.status != "REJECTED",
                    "status": fill.status if fill else ack.status,
                    "quantity": quantity,
                    "filledQuantity": fill.filledQuantity if fill else 0,
                    "averageFillPrice": fill.averageFillPrice if fill else None,
                    "reasonCodes": [
                        "voting_ensemble.local_paper.end_of_day_flattening_exit_submitted",
                        "voting_ensemble.paper_execution.risk_reducing_exit_allowed_when_entries_blocked",
                    ],
                }
            )
        return updates

    def _simulate_and_apply_fill(self, order: Mapping[str, Any]) -> PaperGatewayFill | None:
        client_order_id = str(order.get("clientOrderId") or "")
        ownership_reason = self._stored_order_ownership_rejection_reason(order)
        if ownership_reason is not None:
            self.repository.write_snapshot(
                f"local_execution.{client_order_id}",
                {
                    "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                    "executionMode": "LOCAL_PAPER",
                    "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                    "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                    "clientOrderId": client_order_id,
                    "orderIntentId": order.get("orderIntentId"),
                    "status": "REJECTED",
                    "simulated": True,
                    "rejectedReason": ownership_reason,
                    "reasonCodes": [ownership_reason, "voting_ensemble.local_paper_execution_engine.stored_order_owner_rejected"],
                },
            )
            return None
        order = self._order_sized_to_owned_position(order)
        if int(order.get("quantity") or 0) <= int(order.get("filledQuantity") or 0):
            self._record_open_order_status(order, status="CANCELED", reason_code="voting_ensemble.local_paper_execution_engine.exit_has_no_remaining_owned_quantity")
            self._cancel_competing_oco_exit(order, reason_code="voting_ensemble.local_paper_execution_engine.exit_has_no_remaining_owned_quantity")
            return None
        fill_plan = self._local_fill_plan(order)
        if fill_plan["status"] != "FILLABLE":
            self._record_open_order_status(order, status=str(fill_plan["orderStatus"]), reason_code=str(fill_plan["reasonCode"]))
            return None
        fill = self.repository.apply_local_fill(
            client_order_id=client_order_id,
            order_intent_id=str(order.get("orderIntentId") or client_order_id),
            symbol=str(order.get("symbol") or "SPY"),
            side=Signal(order.get("side") or Signal.BUY),
            requested_quantity=int(fill_plan["quantity"]),
            fill_price=float(fill_plan["fillPrice"]),
            filled_at=_parse_time(order.get("submittedAt")) or datetime.now(UTC),
        )
        if fill is None:
            self.repository.write_snapshot(
                f"local_execution.{client_order_id}",
                {
                    "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                    "executionMode": "LOCAL_PAPER",
                    "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                    "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                    "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                    "clientOrderId": client_order_id,
                    "orderIntentId": order.get("orderIntentId"),
                    "status": "REJECTED",
                    "simulated": True,
                    "reasonCodes": ["voting_ensemble.local_paper_execution_engine.fill_rejected_by_inventory"],
                },
            )
            return None
        self.repository.inventory_ledger.mark_order_filled(client_order_id, fill)
        updated_order = self.repository.read_snapshot(f"local_order.{client_order_id}")
        self._after_local_fill(order=updated_order, fill=fill)
        self.repository.write_snapshot(
            f"local_execution.{client_order_id}",
            {
                "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                "executionMode": "LOCAL_PAPER",
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "clientOrderId": client_order_id,
                "orderIntentId": order.get("orderIntentId"),
                "status": updated_order.get("status") or fill.status,
                "filledQuantity": updated_order.get("filledQuantity"),
                "lastFillQuantity": fill.filledQuantity,
                "averageFillPrice": fill.averageFillPrice,
                "filledAt": fill.filledAt.isoformat().replace("+00:00", "Z"),
                "fillPolicy": fill_plan.get("fillPolicy"),
                "simulated": True,
                "reasonCodes": ["voting_ensemble.local_paper_execution_engine.fill_simulated_and_applied"],
            },
        )
        return fill

    def _after_local_fill(self, *, order: Mapping[str, Any], fill: PaperGatewayFill) -> None:
        if not order.get("protectiveKind") and fill.side == Signal.BUY and fill.filledQuantity > 0:
            self._ensure_local_protective_oco(order, fill)
            return
        if order.get("protectiveKind"):
            remaining = self.repository.inventory_ledger.quantity_for_symbol(str(order.get("symbol") or fill.symbol).upper())
            if remaining <= 0 or str(order.get("status") or "").upper() == "FILLED":
                self._cancel_competing_oco_exit(order, reason_code="voting_ensemble.local_paper_execution_engine.oco_sibling_canceled_after_exit_fill")
            else:
                self._resize_open_oco_siblings(order, remaining_quantity=remaining)
            return
        if fill.side == Signal.SELL:
            symbol = str(order.get("symbol") or fill.symbol).upper()
            remaining = self.repository.inventory_ledger.quantity_for_symbol(symbol)
            if remaining <= 0:
                self._cancel_open_local_exits_for_symbol(
                    symbol,
                    exclude_client_order_id=str(order.get("clientOrderId") or fill.clientOrderId),
                    reason_code="voting_ensemble.local_paper_execution_engine.local_exit_siblings_canceled_after_flatten",
                )
            else:
                self._resize_open_exit_orders_for_symbol(
                    symbol,
                    remaining_quantity=remaining,
                    exclude_client_order_id=str(order.get("clientOrderId") or fill.clientOrderId),
                )

    def _ensure_local_protective_oco(self, entry_order: Mapping[str, Any], fill: PaperGatewayFill) -> None:
        if fill.side != Signal.BUY:
            return
        symbol = str(entry_order.get("symbol") or fill.symbol).upper()
        remaining = self.repository.inventory_ledger.quantity_for_symbol(symbol)
        if remaining <= 0:
            return
        stop_price = _positive_float(entry_order.get("stopPrice"))
        target_price = _positive_float(entry_order.get("targetPrice"))
        if stop_price is None and target_price is None:
            return
        parent_id = str(entry_order.get("clientOrderId") or fill.clientOrderId)
        oco_group_id = f"{parent_id}-oco"
        if stop_price is not None:
            self._upsert_local_protective_order(
                parent_order=entry_order,
                client_order_id=f"{parent_id}-stop",
                protective_kind="STOP_LOSS",
                quantity=remaining,
                order_type="STOP_LIMIT",
                trigger_price=stop_price,
                limit_price=stop_price,
                oco_group_id=oco_group_id,
            )
        if target_price is not None:
            self._upsert_local_protective_order(
                parent_order=entry_order,
                client_order_id=f"{parent_id}-target",
                protective_kind="PROFIT_TARGET",
                quantity=remaining,
                order_type="LIMIT",
                trigger_price=None,
                limit_price=target_price,
                oco_group_id=oco_group_id,
            )

    def _upsert_local_protective_order(
        self,
        *,
        parent_order: Mapping[str, Any],
        client_order_id: str,
        protective_kind: str,
        quantity: int,
        order_type: str,
        trigger_price: float | None,
        limit_price: float,
        oco_group_id: str,
    ) -> None:
        try:
            existing = self.repository.read_snapshot(f"local_order.{client_order_id}")
        except KeyError:
            existing = {}
        if str(existing.get("status") or "").upper() in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            return
        submitted_at = _parse_time(parent_order.get("submittedAt")) or datetime.now(UTC)
        payload = {
            **existing,
            "schemaVersion": "voting_ensemble_local_order_v1",
            "clientOrderId": client_order_id,
            "parentClientOrderId": parent_order.get("clientOrderId"),
            "orderIntentId": parent_order.get("orderIntentId"),
            "decisionId": parent_order.get("decisionId"),
            "symbol": str(parent_order.get("symbol") or "SPY").upper(),
            "side": Signal.SELL.value,
            "orderType": order_type,
            "quantity": int(quantity),
            "filledQuantity": min(int(existing.get("filledQuantity") or 0), int(quantity)),
            "entryPrice": float(limit_price),
            "triggerPrice": trigger_price,
            "limitPrice": float(limit_price),
            "stopPrice": trigger_price if protective_kind == "STOP_LOSS" else None,
            "targetPrice": limit_price if protective_kind == "PROFIT_TARGET" else None,
            "submittedAt": submitted_at.isoformat().replace("+00:00", "Z"),
            "status": "OPEN",
            "protectiveKind": protective_kind,
            "ocoGroupId": oco_group_id,
            "positionOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
            "exitOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "executionMode": "LOCAL_PAPER",
            "sourceAuthority": "voting_ensemble_local_paper_account",
            "reasonCodes": [*list(existing.get("reasonCodes") or ()), "voting_ensemble.local_paper_execution_engine.protective_oco_order_upserted"],
        }
        self.repository.write_snapshot(f"local_order.{client_order_id}", payload)

    def _order_sized_to_owned_position(self, order: Mapping[str, Any]) -> Mapping[str, Any]:
        if not order.get("protectiveKind") and Signal(order.get("side") or Signal.BUY) != Signal.SELL:
            return order
        if Signal(order.get("side") or Signal.BUY) != Signal.SELL:
            return order
        position = self.repository.inventory_ledger.position_for_symbol(str(order.get("symbol") or "SPY").upper())
        owned = max(0, int(position.get("quantity") or 0))
        quantity = min(int(order.get("quantity") or 0), owned + int(order.get("filledQuantity") or 0))
        if quantity == int(order.get("quantity") or 0):
            return order
        updated = {**dict(order), "quantity": quantity, "reasonCodes": [*list(order.get("reasonCodes") or ()), "voting_ensemble.local_paper_execution_engine.exit_quantity_sized_to_owned_position"]}
        self.repository.write_snapshot(f"local_order.{order.get('clientOrderId')}", updated)
        return updated

    def _resize_open_oco_siblings(self, order: Mapping[str, Any], *, remaining_quantity: int) -> None:
        oco_group_id = order.get("ocoGroupId")
        if not oco_group_id:
            return
        for sibling in self.repository.inventory_ledger.orders():
            if sibling.get("ocoGroupId") != oco_group_id or sibling.get("clientOrderId") == order.get("clientOrderId"):
                continue
            if str(sibling.get("status") or "").upper() not in {"OPEN", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}:
                continue
            self.repository.write_snapshot(
                f"local_order.{sibling['clientOrderId']}",
                {
                    **sibling,
                    "quantity": int(remaining_quantity) + int(sibling.get("filledQuantity") or 0),
                    "reasonCodes": [*list(sibling.get("reasonCodes") or ()), "voting_ensemble.local_paper_execution_engine.oco_sibling_resized_to_remaining_position"],
                },
            )

    def _cancel_competing_oco_exit(self, order: Mapping[str, Any], *, reason_code: str) -> None:
        oco_group_id = order.get("ocoGroupId")
        if not oco_group_id:
            return
        for sibling in self.repository.inventory_ledger.orders():
            if sibling.get("ocoGroupId") != oco_group_id or sibling.get("clientOrderId") == order.get("clientOrderId"):
                continue
            if str(sibling.get("status") or "").upper() in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                continue
            self.repository.write_snapshot(
                f"local_order.{sibling['clientOrderId']}",
                {
                    **sibling,
                    "status": "CANCELED",
                    "canceledAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "reasonCodes": [*list(sibling.get("reasonCodes") or ()), reason_code],
                },
            )

    def _has_open_local_exit(self, symbol: str, *, reason: str | None = None) -> bool:
        for order in self.repository.inventory_ledger.orders():
            if str(order.get("symbol") or "").upper() != symbol.upper():
                continue
            if Signal(order.get("side") or Signal.BUY) != Signal.SELL:
                continue
            if str(order.get("status") or "").upper() not in {"OPEN", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}:
                continue
            if reason is None or order.get("exitReason") == reason:
                return True
        return False

    def _cancel_open_local_exits_for_symbol(self, symbol: str, *, exclude_client_order_id: str, reason_code: str) -> None:
        for sibling in self.repository.inventory_ledger.orders():
            if sibling.get("clientOrderId") == exclude_client_order_id:
                continue
            if str(sibling.get("symbol") or "").upper() != symbol.upper():
                continue
            if Signal(sibling.get("side") or Signal.BUY) != Signal.SELL:
                continue
            if str(sibling.get("status") or "").upper() in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                continue
            self.repository.write_snapshot(
                f"local_order.{sibling['clientOrderId']}",
                {
                    **sibling,
                    "status": "CANCELED",
                    "canceledAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "reasonCodes": [*list(sibling.get("reasonCodes") or ()), reason_code],
                },
            )

    def _resize_open_exit_orders_for_symbol(self, symbol: str, *, remaining_quantity: int, exclude_client_order_id: str) -> None:
        for sibling in self.repository.inventory_ledger.orders():
            if sibling.get("clientOrderId") == exclude_client_order_id:
                continue
            if str(sibling.get("symbol") or "").upper() != symbol.upper():
                continue
            if Signal(sibling.get("side") or Signal.BUY) != Signal.SELL:
                continue
            if str(sibling.get("status") or "").upper() not in {"OPEN", "PARTIALLY_FILLED", "NEW", "ACCEPTED"}:
                continue
            self.repository.write_snapshot(
                f"local_order.{sibling['clientOrderId']}",
                {
                    **sibling,
                    "quantity": int(remaining_quantity) + int(sibling.get("filledQuantity") or 0),
                    "reasonCodes": [*list(sibling.get("reasonCodes") or ()), "voting_ensemble.local_paper_execution_engine.exit_order_resized_to_remaining_position"],
                },
            )

    def _local_fill_plan(self, order: Mapping[str, Any]) -> dict[str, Any]:
        side = Signal(order.get("side") or Signal.BUY)
        symbol = str(order.get("symbol") or "SPY").upper()
        quote = self.repository.inventory_ledger.latest_market_data_status(symbol)
        if not quote or not bool(quote.get("fresh")):
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.no_fresh_quote"}
        quote_timestamp = _parse_time(quote.get("quoteTimestamp"))
        if quote_timestamp is None:
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.quote_timestamp_missing"}
        age_ms = max(0.0, (datetime.now(UTC) - quote_timestamp).total_seconds() * 1000.0)
        max_age_ms = _local_paper_env_float("VOTING_ENSEMBLE_LOCAL_PAPER_MAX_QUOTE_AGE_MS", 5000.0)
        if age_ms > max_age_ms and quote.get("observedAt"):
            observed = _parse_time(quote.get("observedAt"))
            age_ms = max(0.0, ((observed or datetime.now(UTC)) - quote_timestamp).total_seconds() * 1000.0)
        if age_ms > max_age_ms:
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.stale_quote"}
        order_type = str(order.get("orderType") or "LIMIT").upper()
        trigger = _positive_float(order.get("triggerPrice"))
        if "STOP" in order_type and not bool(order.get("stopTriggered")):
            triggered = (side == Signal.BUY and trigger is not None and _float(quote.get("ask")) >= trigger) or (side == Signal.SELL and trigger is not None and _float(quote.get("bid")) <= trigger)
            if not triggered:
                return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.stop_not_triggered"}
            self.repository.write_snapshot(
                f"local_order.{order['clientOrderId']}",
                {
                    **dict(order),
                    "stopTriggered": True,
                    "status": "OPEN",
                    "reasonCodes": [*list(order.get("reasonCodes") or ()), "voting_ensemble.local_paper_execution_engine.stop_triggered_limit_active"],
                },
            )
        limit = _positive_float(order.get("limitPrice") or order.get("entryPrice"))
        if limit is None:
            return {"status": "NOT_FILLABLE", "orderStatus": "REJECTED", "reasonCode": "voting_ensemble.local_paper_execution_engine.limit_price_missing"}
        executable_price = _float(quote.get("ask" if side == Signal.BUY else "bid"))
        if side == Signal.BUY and executable_price > limit:
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.buy_limit_not_executable"}
        if side == Signal.SELL and executable_price < limit:
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.sell_limit_not_executable"}
        slippage_bps = _local_paper_env_float("VOTING_ENSEMBLE_LOCAL_PAPER_SLIPPAGE_BPS", 0.0)
        fill_price = executable_price * (1.0 + slippage_bps / 10000.0) if side == Signal.BUY else executable_price * (1.0 - slippage_bps / 10000.0)
        fill_price = min(fill_price, limit) if side == Signal.BUY else max(fill_price, limit)
        remaining = max(0, int(order.get("quantity") or 0) - int(order.get("filledQuantity") or 0))
        quote_size = _float(quote.get("askSize" if side == Signal.BUY else "bidSize"))
        participation = max(0.0, min(100.0, _local_paper_env_float("VOTING_ENSEMBLE_LOCAL_PAPER_MAX_PARTICIPATION_PCT", 100.0))) / 100.0
        fill_quantity = min(remaining, max(0, int(quote_size * participation)))
        if fill_quantity <= 0:
            return {"status": "NOT_FILLABLE", "orderStatus": "OPEN", "reasonCode": "voting_ensemble.local_paper_execution_engine.no_quote_size_available"}
        return {
            "status": "FILLABLE",
            "quantity": fill_quantity,
            "fillPrice": round(fill_price, 6),
            "fillPolicy": "limit_quote_size_participation_slippage_capped_at_limit",
        }

    def _record_open_order_status(self, order: Mapping[str, Any], *, status: str, reason_code: str) -> None:
        client_order_id = str(order.get("clientOrderId") or "")
        updated = {**dict(order), "status": status, "reasonCodes": [*list(order.get("reasonCodes") or ()), reason_code]}
        self.repository.write_snapshot(f"local_order.{client_order_id}", updated)
        self.repository.write_snapshot(
            f"local_execution.{client_order_id}",
            {
                "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                "executionMode": "LOCAL_PAPER",
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "clientOrderId": client_order_id,
                "orderIntentId": order.get("orderIntentId"),
                "status": status,
                "filledQuantity": int(order.get("filledQuantity") or 0),
                "simulated": True,
                "reasonCodes": [reason_code],
            },
        )

    def _submission_rejection_reason(self, intent: Any) -> str | None:
        if getattr(intent, "algorithmId", None) != VOTING_ENSEMBLE_ALGORITHM_ID:
            return "voting_ensemble.local_paper.foreign_algorithm_rejected"
        if getattr(intent, "capitalPartitionId", None) != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
            return "voting_ensemble.local_paper.foreign_capital_partition_rejected"
        quantity = int(getattr(intent, "submittedQuantity", 0) or 0)
        if quantity <= 0:
            return "voting_ensemble.local_paper.zero_quantity"
        side = Signal(getattr(intent, "side"))
        account = self.repository.local_account_snapshot()
        if account.get("allowMargin") is not False or account.get("allowShorts") is not False:
            return "voting_ensemble.local_paper.margin_or_shorts_not_enabled"
        notional = quantity * float(getattr(intent, "limitPrice", None) or getattr(intent, "triggerPrice", None) or 0.0)
        if side == Signal.BUY and float(account.get("usableEntryBuyingPower") or account.get("buyingPower") or 0.0) < notional:
            return "voting_ensemble.local_paper.insufficient_buying_power"
        planned_risk = float(getattr(intent, "plannedRiskDollars", 0.0) or 0.0)
        if side == Signal.BUY and planned_risk > max(0.0, float(account.get("equity") or 0.0)):
            return "voting_ensemble.local_paper.local_risk_limit_exceeded"
        if side == Signal.SELL:
            position = self.repository.inventory_ledger.position_for_symbol(str(getattr(intent, "symbol", "SPY")).upper())
            owned = int(position.get("quantity") or 0)
            if owned <= 0:
                return "voting_ensemble.local_paper.sell_cannot_mutate_foreign_or_absent_position"
            if quantity > owned:
                return "voting_ensemble.local_paper.sell_quantity_exceeds_owned_inventory"
        return None

    def _stored_order_ownership_rejection_reason(self, order: Mapping[str, Any]) -> str | None:
        if order.get("algorithmId", order.get("algorithm_id")) != VOTING_ENSEMBLE_ALGORITHM_ID:
            return "voting_ensemble.local_paper.stored_order_foreign_algorithm_rejected"
        if order.get("capitalPartitionId") != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
            return "voting_ensemble.local_paper.stored_order_foreign_capital_partition_rejected"
        if order.get("accountId", VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID) != VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID:
            return "voting_ensemble.local_paper.stored_order_foreign_account_rejected"
        if Signal(order.get("side") or Signal.BUY) == Signal.SELL:
            position = self.repository.inventory_ledger.position_for_symbol(str(order.get("symbol") or "SPY").upper())
            if not position or int(position.get("quantity") or 0) <= 0:
                return "voting_ensemble.local_paper.sell_cannot_mutate_foreign_or_absent_position"
        return None

    def _record_rejected_order(self, intent: Any, reason: str) -> None:
        client_order_id = str(getattr(intent, "clientOrderId", "") or "voting-ensemble-local-rejected")
        self.repository.write_snapshot(
            f"local_execution.{client_order_id}",
            {
                "schemaVersion": "voting_ensemble_local_execution_engine_v1",
                "executionMode": "LOCAL_PAPER",
                "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                "clientOrderId": client_order_id,
                "orderIntentId": getattr(intent, "orderIntentId", None),
                "status": "REJECTED",
                "simulated": True,
                "rejectedReason": reason,
                "reasonCodes": [reason, "voting_ensemble.local_paper_execution_engine.order_rejected"],
            },
        )


class VotingEnsembleAlpacaPaperBroker:
    """Voting Ensemble adapter that can only submit to Alpaca Paper."""

    broker_kind = "alpaca_paper"

    def __init__(self, settings: Any | None = None, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0) -> None:
        self.settings = settings or _paper_settings_from_env()
        self.base_url = str(self.settings.alpaca_trading_base_url).rstrip("/")
        if not is_approved_alpaca_paper_endpoint(self.base_url):
            raise VotingEnsembleAlpacaPaperBrokerConfigurationError("voting_ensemble.alpaca_paper.paper_endpoint_required")
        if not self.settings.has_alpaca_credentials:
            raise VotingEnsembleAlpacaPaperBrokerConfigurationError("voting_ensemble.alpaca_paper.credentials_required")
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)), trust_env=False)
        self.client = http_client or self._owned_client

    @property
    def configured(self) -> bool:
        return True

    @property
    def paper_endpoint(self) -> bool:
        return is_approved_alpaca_paper_endpoint(self.base_url)

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_account(self) -> bool:
        try:
            response = self.client.get(f"{self.base_url}/account", headers=self._headers())
            response.raise_for_status()
        except (httpx.HTTPError, AttributeError):
            return False
        payload = response.json()
        return bool(payload.get("id") or payload.get("account_number"))

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        body: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": str(int(intent.submittedQuantity)),
            "side": "buy" if intent.side == Signal.BUY else "sell",
            "type": _alpaca_order_type(getattr(intent, "orderType", None), limit_price=intent.limitPrice),
            "time_in_force": str(getattr(intent, "timeInForce", "DAY")).lower(),
            "client_order_id": intent.clientOrderId,
        }
        if body["type"] in {"limit", "stop_limit"} and intent.limitPrice:
            body["limit_price"] = str(intent.limitPrice)
        if body["type"] in {"stop", "stop_limit"} and intent.stopPrice:
            body["stop_price"] = str(intent.stopPrice)
        if intent.stopPrice or intent.targetPrice:
            body["order_class"] = "bracket"
            if intent.stopPrice:
                body["stop_loss"] = {"stop_price": str(intent.stopPrice)}
                if intent.stopLimitPrice:
                    body["stop_loss"]["limit_price"] = str(intent.stopLimitPrice)
            if intent.targetPrice:
                body["take_profit"] = {"limit_price": str(intent.targetPrice)}
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("voting_ensemble.alpaca_paper.submission_timeout") from exc
        except httpx.HTTPStatusError as exc:
            try:
                reason = str(exc.response.json().get("message") or exc.response.text)
            except Exception:
                reason = str(exc)
            return PaperGatewayBrokerAck(clientOrderId=intent.clientOrderId, brokerOrderId=None, status="REJECTED", rejectedReason=reason[:300])
        payload = response.json()
        return PaperGatewayBrokerAck(
            clientOrderId=str(payload.get("client_order_id") or intent.clientOrderId),
            brokerOrderId=str(payload.get("id") or ""),
            status=_ack_status(payload),
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
            rejectedReason=str(payload.get("reject_reason") or "") or None,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            response = self.client.get(f"{self.base_url}/orders:by_client_order_id", headers=self._headers(), params={"client_order_id": client_order_id})
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        payload = response.json()
        filled = float(payload.get("filled_qty") or 0.0)
        if filled <= 0:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            orderIntentId=str(payload.get("client_order_id") or client_order_id),
            symbol=str(payload.get("symbol") or "UNKNOWN").upper(),
            side=Signal.SELL if str(payload.get("side") or "").lower() == "sell" else Signal.BUY,
            filledQuantity=int(filled),
            averageFillPrice=float(payload.get("filled_avg_price") or 0.01),
            status=_broker_status(str(payload.get("status") or "filled")),
            filledAt=_parse_time(payload.get("filled_at")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            response = self.client.delete(f"{self.base_url}/orders:by_client_order_id", headers=self._headers(), params={"client_order_id": client_order_id})
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    def refresh_positions(self) -> list[dict[str, Any]]:
        try:
            response = self.client.get(f"{self.base_url}/positions", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return [dict(item) for item in payload] if isinstance(payload, list) else []

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }


class AlpacaPaperBrokerClient:
    """Real Alpaca Paper client for Voting Ensemble execution-adapter submissions."""

    broker_kind = "alpaca_paper_client"

    def __init__(self, settings: Any | None = None, *, http_client: httpx.Client | None = None, timeout_seconds: float = 10.0) -> None:
        self.settings = settings or _paper_settings_from_env()
        self.base_url = str(self.settings.alpaca_trading_base_url).rstrip("/")
        if not is_approved_alpaca_paper_endpoint(self.base_url):
            raise VotingEnsembleAlpacaPaperBrokerConfigurationError("voting_ensemble.alpaca_paper.approved_paper_endpoint_required")
        if not self.settings.has_alpaca_credentials:
            raise VotingEnsembleAlpacaPaperBrokerConfigurationError("voting_ensemble.alpaca_paper.credentials_required")
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)), trust_env=False)
        self.client = http_client or self._owned_client
        self._latest_account: BrokerAccountSnapshot | None = None

    @property
    def configured(self) -> bool:
        return True

    @property
    def paper_endpoint(self) -> bool:
        return is_approved_alpaca_paper_endpoint(self.base_url)

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_environment(self) -> bool:
        return self.paper_endpoint and self.verify_paper_account()

    def refresh_market_clock(self) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}/clock", headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        return {
            "isOpen": bool(payload.get("is_open")),
            "status": "open" if payload.get("is_open") else "closed",
            "timestamp": payload.get("timestamp"),
            "nextOpen": payload.get("next_open"),
            "nextClose": payload.get("next_close"),
            "sourceAuthority": "alpaca_paper_clock",
        }

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        response = self.client.get(f"{self.base_url}/account", headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        observed_at = datetime.now(UTC)
        snapshot = BrokerAccountSnapshot(
            accountId=str(payload.get("id") or payload.get("account_number") or "alpaca-paper-account"),
            equity=_float(payload.get("equity") or payload.get("portfolio_value")),
            buyingPower=_float(payload.get("buying_power")),
            realizedPnlToday=0.0,
            positions=[],
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=observed_at,
            sessionDate=observed_at.date(),
            sourceAuthority="broker",
            positionsReconciled=True,
            openOrdersReconciled=True,
        )
        self._latest_account = snapshot
        return snapshot

    def verify_paper_account(self) -> bool:
        try:
            self.refresh_account_snapshot()
        except Exception:
            return False
        return self.paper_endpoint

    def verify_symbol_tradable(self, symbol: str) -> bool:
        try:
            response = self.client.get(f"{self.base_url}/assets/{symbol.upper()}", headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False
        return bool(payload.get("tradable")) and str(payload.get("status") or "").lower() == "active"

    def verify_buying_power(self, order_plan: OrderPlan) -> bool:
        try:
            account = self._latest_account or self.refresh_account_snapshot()
        except Exception:
            return False
        notional = order_plan.quantity * float(order_plan.limitPrice or order_plan.entryPrice)
        return account.buyingPower >= notional

    def submit_order(self, order_plan: OrderPlan, client_order_id: str) -> BrokerOrderAck:
        if order_plan.orderType not in {"LIMIT", "STOP_LIMIT"}:
            return BrokerOrderAck(clientOrderId=client_order_id, status="REJECTED", rejectedReason="voting_ensemble.alpaca_paper.unsupported_order_type")
        body = _alpaca_order_body(order_plan, client_order_id)
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("voting_ensemble.alpaca_paper.submission_timeout") from exc
        except httpx.HTTPStatusError as exc:
            try:
                reason = str(exc.response.json().get("message") or exc.response.text)
            except Exception:
                reason = str(exc)
            return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=None, status="REJECTED", rejectedReason=reason[:300])
        payload = response.json()
        status = _broker_status(str(payload.get("status") or "accepted"))
        return BrokerOrderAck(
            clientOrderId=str(payload.get("client_order_id") or client_order_id),
            brokerOrderId=str(payload.get("id") or "") or None,
            status=status,
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
            rejectedReason=str(payload.get("reject_reason") or "") or None,
        )

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            response = self.client.get(f"{self.base_url}/orders:by_client_order_id", headers=self._headers(), params={"client_order_id": client_order_id})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return dict(response.json())

    def refresh_order_status(self, client_order_id: str) -> str | None:
        order = self.get_order_by_client_order_id(client_order_id)
        return _broker_status(str(order.get("status") or "")) if order else None

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        order = self.get_order_by_client_order_id(client_order_id)
        if not order:
            return None
        filled = int(float(order.get("filled_qty") or 0))
        if filled <= 0:
            return None
        return BrokerFillUpdate(
            clientOrderId=client_order_id,
            filledQuantity=filled,
            averageFillPrice=_positive_float(order.get("filled_avg_price")),
            status=_broker_status(str(order.get("status") or "filled")),
            updatedAt=_parse_time(order.get("filled_at") or order.get("updated_at")) or datetime.now(UTC),
        )

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        try:
            response = self.client.get(f"{self.base_url}/orders", headers=self._headers(), params={"status": "open", "limit": 500, "nested": "true"})
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return [_broker_order_state(row) for row in payload if isinstance(row, dict)]

    def refresh_positions(self) -> list[BrokerPositionState]:
        try:
            response = self.client.get(f"{self.base_url}/positions", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return [_broker_position_state(row) for row in payload if isinstance(row, dict)]

    def cancel_stale_orders(self, *, older_than: datetime) -> list[dict[str, Any]]:
        canceled: list[dict[str, Any]] = []
        for order in self.refresh_open_orders():
            if _require_utc(order.submittedAt) > _require_utc(older_than):
                continue
            if not order.clientOrderId:
                continue
            if self.cancel_order(order.clientOrderId):
                canceled.append(order.model_dump(mode="json"))
        return canceled

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            response = self.client.delete(f"{self.base_url}/orders:by_client_order_id", headers=self._headers(), params={"client_order_id": client_order_id})
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    def replace_order(self, client_order_id: str, *, limit_price: float | None = None, quantity: int | None = None, stop_price: float | None = None) -> dict[str, Any] | None:
        order = self.get_order_by_client_order_id(client_order_id)
        if not order or str(order.get("status") or "").lower() not in {"new", "accepted", "partially_filled"}:
            return None
        broker_id = str(order.get("id") or "")
        if not broker_id:
            return None
        body: dict[str, Any] = {}
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if quantity is not None:
            body["qty"] = str(quantity)
        if not body:
            return dict(order)
        response = self.client.patch(f"{self.base_url}/orders/{broker_id}", headers=self._headers(), json=body)
        response.raise_for_status()
        return dict(response.json())

    def submit_protective_order(self, *, symbol: str, side: Signal | str, quantity: int, stop_price: float | None, target_price: float | None, client_order_id: str) -> BrokerOrderAck:
        body: dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": str(max(0, int(quantity))),
            "side": "sell" if Signal(side) == Signal.BUY else "buy",
            "type": "limit" if target_price and not stop_price else "stop_limit" if stop_price and target_price else "stop",
            "time_in_force": "day",
            "client_order_id": client_order_id,
        }
        if target_price:
            body["limit_price"] = str(target_price)
        if stop_price:
            body["stop_price"] = str(stop_price)
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=None, status="REJECTED", rejectedReason=str(exc.response.text)[:300])
        payload = response.json()
        return BrokerOrderAck(
            clientOrderId=str(payload.get("client_order_id") or client_order_id),
            brokerOrderId=str(payload.get("id") or "") or None,
            status=_broker_status(str(payload.get("status") or "accepted")),
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
        )

    def submit_position_exit_order(self, *, symbol: str, side: Signal | str, quantity: int, limit_price: float, client_order_id: str) -> BrokerOrderAck:
        body: dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": str(max(0, int(quantity))),
            "side": "buy" if Signal(side) == Signal.BUY else "sell",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": str(max(0.01, float(limit_price))),
            "client_order_id": client_order_id,
        }
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=None, status="REJECTED", rejectedReason=str(exc.response.text)[:300])
        payload = response.json()
        return BrokerOrderAck(
            clientOrderId=str(payload.get("client_order_id") or client_order_id),
            brokerOrderId=str(payload.get("id") or "") or None,
            status=_broker_status(str(payload.get("status") or "accepted")),
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
        )

    def retrieve_fills(self, *, after: datetime | None = None) -> list[BrokerFillUpdate]:
        params: dict[str, str] = {}
        if after is not None:
            params["after"] = _require_utc(after).isoformat().replace("+00:00", "Z")
        try:
            response = self.client.get(f"{self.base_url}/account/activities/FILL", headers=self._headers(), params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return [_fill_update_from_activity(row) for row in payload if isinstance(row, dict)]

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }


class VotingEnsembleUnavailablePaperBroker:
    configured = False
    broker_kind = "unavailable"
    paper_endpoint = False

    def verify_paper_account(self) -> bool:
        return False

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=None,
            status="REJECTED",
            rejectedReason="voting_ensemble.alpaca_paper.unconfigured",
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


def eligible_order_plan_from_decision(decision: Mapping[str, Any]) -> OrderPlan | None:
    if decision.get("algorithm_id") not in {None, VOTING_ENSEMBLE_ALGORITHM_ID}:
        raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble execution rejected a foreign decision")
    raw = decision.get("order_plan")
    if not isinstance(raw, Mapping):
        return None
    plan = OrderPlan.model_validate(dict(raw))
    if not plan.eligible or plan.orderType == "NO_ORDER" or plan.quantity <= 0:
        return None
    return plan


def proposal_from_order_plan(intent: VotingEnsemblePaperOrderIntent, *, quantity_override: int | None = None) -> GlobalOrderProposal:
    order = intent.orderPlan
    quantity = max(0, int(quantity_override if quantity_override is not None else order.quantity))
    return GlobalOrderProposal(
        algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
        capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
        decisionId=intent.decisionId,
        orderIntentId=intent.orderIntentId,
        intent="new_entry",
        symbol=order.symbol,
        side=order.side,
        quantity=quantity,
        triggerPrice=order.entryPrice,
        limitPrice=order.limitPrice,
        stopPrice=order.stopPrice,
        targetPrice=order.targetPrice,
        plannedRiskDollars=max(0.0, abs(order.entryPrice - (order.stopPrice or order.entryPrice)) * quantity),
        settingsSnapshot={
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "clientOrderId": voting_ensemble_gateway_client_order_id(intent),
            "timeInForce": order.timeInForce,
            "maximumHoldingMinutes": order.maximumHoldingMinutes,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "maximumOrderAgeSeconds": 60,
        },
        entryFormula={"orderType": order.orderType, "timeInForce": order.timeInForce},
        stopFormula={"stopPrice": order.stopPrice},
        targetFormula={"targetPrice": order.targetPrice, "orderType": "LIMIT"},
        strategyStateHash=order.configurationHash,
        proposedAt=order.generatedAt,
        sessionDate=order.sessionDate,
        configurationHash=_hash({"orderPlanId": order.orderPlanId, "configurationHash": order.configurationHash}),
    )


def allow_all_global_application(proposal: GlobalOrderProposal, *, evaluated_at: datetime) -> AppliedGlobalGateDecision:
    return AppliedGlobalGateDecision(
        algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
        decisionId=proposal.decisionId,
        orderIntentId=proposal.orderIntentId,
        action="ALLOW",
        side=proposal.side,
        proposedQuantity=proposal.quantity,
        globallyAllowedQuantity=proposal.quantity,
        proposedPlannedRiskDollars=proposal.plannedRiskDollars,
        maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
        quantityReduced=False,
        riskReducingExitAllowed=True,
        rejectionReasons=(),
        immutableChecks=("voting_ensemble.paper_execution.paper_only",),
        proposalHash=_hash(proposal.model_dump(mode="json")),
        responseHash=_hash({"action": "ALLOW", "quantity": proposal.quantity}),
        evaluatedAt=_require_utc(evaluated_at),
        explanation="Voting Ensemble local order intent passed to shared global paper risk for final quantity/risk checks.",
    )


def is_approved_alpaca_paper_endpoint(value: Any) -> bool:
    return str(value or "").rstrip("/").lower() == _APPROVED_PAPER_ENDPOINT


def voting_ensemble_gateway_client_order_id(intent: VotingEnsemblePaperOrderIntent) -> str:
    return "ve-paper-" + _hash(
        {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "orderIntentId": intent.orderIntentId,
            "orderPlanId": intent.orderPlan.orderPlanId,
            "idempotencyKey": intent.idempotencyKey,
        }
    )[:20]


def _is_voting_ensemble_client_order_id(value: str | None) -> bool:
    return bool(value) and str(value).startswith(VOTING_ENSEMBLE_CLIENT_ORDER_PREFIXES)


def _positions_by_symbol(repository: VotingEnsemblePaperExecutionRepository) -> dict[str, int]:
    positions: dict[str, int] = {}
    for position in repository.inventory_ledger.positions():
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            continue
        positions[symbol] = positions.get(symbol, 0) + int(position.get("quantity") or 0)
    return positions


def _position_attributed_to_voting_ensemble(
    repository: VotingEnsemblePaperExecutionRepository,
    position: BrokerPositionState,
    open_orders: list[BrokerOrderState],
) -> bool:
    symbol = position.symbol.upper()
    if _positions_by_symbol(repository).get(symbol, 0) != 0:
        return True
    if any(order.symbol.upper() == symbol for order in open_orders if _is_voting_ensemble_client_order_id(order.clientOrderId)):
        return True
    for payload in repository.snapshots.values():
        if payload.get("algorithmId", payload.get("algorithm_id")) != VOTING_ENSEMBLE_ALGORITHM_ID:
            continue
        if str(payload.get("symbol") or "").upper() != symbol:
            continue
        execution_state = payload.get("executionState") if isinstance(payload.get("executionState"), dict) else {}
        filled = _safe_int(payload.get("filledQuantity") or execution_state.get("filledQuantity") or 0)
        if filled > 0 and (
            str(payload.get("clientOrderId") or "").startswith(VOTING_ENSEMBLE_CLIENT_ORDER_PREFIXES)
            or str(execution_state.get("clientOrderId") or "").startswith(VOTING_ENSEMBLE_CLIENT_ORDER_PREFIXES)
        ):
            return True
    return False


def _record_local_broker_divergence_blocks(
    repository: VotingEnsemblePaperExecutionRepository,
    broker_positions: list[BrokerPositionState],
) -> None:
    local_positions = repository.inventory_snapshot().get("positions") or []
    broker_by_symbol = {position.symbol.upper(): position for position in broker_positions}
    for local in local_positions:
        symbol = str(local.get("symbol") or "").upper()
        if not symbol:
            continue
        local_quantity = int(local.get("quantity") or 0)
        broker_quantity = broker_by_symbol.get(symbol).quantity if symbol in broker_by_symbol else 0
        key = f"local_broker_position.{symbol}"
        if local_quantity and abs(broker_quantity) < abs(local_quantity):
            repository.mark_reconciliation_required(
                key,
                {
                    "symbol": symbol,
                    "localQuantity": local_quantity,
                    "brokerQuantity": broker_quantity,
                    "sourceAuthority": "alpaca_paper_broker",
                    "reasonCodes": ["voting_ensemble.paper_execution.local_broker_position_divergence"],
                },
            )
        else:
            repository.mark_reconciliation_resolved(key, reason_code="voting_ensemble.paper_execution.local_broker_position_divergence_resolved")


def _resolve_absent_unattributed_position_blocks(repository: VotingEnsemblePaperExecutionRepository, active_keys: set[str]) -> None:
    prefix = f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.unattributed_position."
    for key, payload in tuple(repository.snapshots.items()):
        if not key.startswith(prefix):
            continue
        short_key = key.removeprefix(f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state.")
        if short_key in active_keys:
            continue
        if str(payload.get("reconciliationStatus") or "").upper() in {"RECONCILIATION_REQUIRED", "UNKNOWN_BROKER_STATE"}:
            repository.mark_reconciliation_resolved(short_key, reason_code="voting_ensemble.paper_execution.unattributed_broker_position_resolved")


def _repository_reconciliation_blocks(repository: VotingEnsemblePaperExecutionRepository) -> list[str]:
    blocks: list[str] = []
    for key, payload in repository.snapshots.items():
        if not key.startswith(f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.unknown_state."):
            continue
        status = str(payload.get("reconciliationStatus") or "").upper()
        if status in {"RECONCILIATION_REQUIRED", "UNKNOWN_BROKER_STATE"}:
            blocks.extend(list(payload.get("reasonCodes") or ["voting_ensemble.paper_execution.reconciliation_required"]))
    for key, payload in repository.snapshots.items():
        if not key.startswith(f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.orders."):
            continue
        if str(payload.get("reconciliationStatus") or payload.get("status") or "").upper() == "RECONCILIATION_REQUIRED":
            blocks.append("voting_ensemble.paper_execution.execution_state_reconciliation_required")
    return sorted(set(blocks))


def _has_persisted_local_inventory_without_account(snapshots: Mapping[str, Mapping[str, Any]]) -> bool:
    if f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_account.latest" in snapshots:
        return False
    return any(
        key.startswith(prefix)
        for key in snapshots
        for prefix in (
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_order.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.applied_fill.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_closed_trade.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_realized_pnl.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_risk_snapshot.",
            f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.",
        )
    )


def _validate_local_recovery_state(*, account: Mapping[str, Any], snapshots: Mapping[str, Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    failures.extend(_local_recovery_account_failures(account))
    for key, payload in snapshots.items():
        if not _is_local_recovery_validated_key(key):
            continue
        if payload.get("algorithmId", payload.get("algorithm_id")) != VOTING_ENSEMBLE_ALGORITHM_ID:
            failures.append("voting_ensemble.local_paper_recovery.foreign_algorithm_record")
        if payload.get("capitalPartitionId") != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
            failures.append("voting_ensemble.local_paper_recovery.foreign_capital_partition_record")
        if not payload.get("schemaVersion"):
            failures.append("voting_ensemble.local_paper_recovery.schema_version_missing")
        if key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position."):
            failures.extend(_local_recovery_position_failures(payload))
    local_fills = _local_recovery_fill_records(snapshots)
    applied_ids = {
        str(payload.get("appliedFillId"))
        for key, payload in snapshots.items()
        if key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.applied_fill.") and payload.get("appliedFillId")
    }
    account_applied_ids = {str(item) for item in account.get("appliedFillIds") or []}
    for fill in local_fills:
        applied_fill_id = str(fill.get("appliedFillId") or "")
        if not applied_fill_id:
            failures.append("voting_ensemble.local_paper_recovery.fill_applied_id_missing")
            continue
        if applied_fill_id not in applied_ids:
            failures.append("voting_ensemble.local_paper_recovery.applied_fill_record_missing")
        if applied_fill_id not in account_applied_ids:
            failures.append("voting_ensemble.local_paper_recovery.account_applied_fill_id_missing")
    failures.extend(_local_recovery_accounting_failures(account=account, snapshots=snapshots, local_fills=local_fills))
    return sorted(set(failures))


def _local_recovery_account_failures(account: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    required = {
        "schemaVersion",
        "version",
        "initialCash",
        "cash",
        "equity",
        "buyingPower",
        "realizedPnl",
        "realizedPnlToday",
        "dailyNetPnl",
        "tradesToday",
        "sessionDate",
        "appliedFillIds",
        "algorithmId",
        "capitalPartitionId",
        "accountId",
    }
    missing = [key for key in required if key not in account]
    if missing:
        failures.append("voting_ensemble.local_paper_recovery.account_required_field_missing")
    if account.get("algorithmId", account.get("algorithm_id")) != VOTING_ENSEMBLE_ALGORITHM_ID:
        failures.append("voting_ensemble.local_paper_recovery.account_foreign_algorithm")
    if account.get("capitalPartitionId") != VOTING_ENSEMBLE_CAPITAL_PARTITION_ID:
        failures.append("voting_ensemble.local_paper_recovery.account_foreign_capital_partition")
    if account.get("accountId") != VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID:
        failures.append("voting_ensemble.local_paper_recovery.account_id_mismatch")
    if str(account.get("version") or "") != VOTING_ENSEMBLE_LOCAL_PAPER_ACCOUNT_VERSION:
        failures.append("voting_ensemble.local_paper_recovery.account_schema_version_mismatch")
    return failures


def _local_recovery_position_failures(payload: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if payload.get("accountId", VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID) != VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID:
        failures.append("voting_ensemble.local_paper_recovery.position_account_mismatch")
    if payload.get("positionOwner", VOTING_ENSEMBLE_ALGORITHM_ID) != VOTING_ENSEMBLE_ALGORITHM_ID:
        failures.append("voting_ensemble.local_paper_recovery.position_owner_mismatch")
    if payload.get("exitOwner", VOTING_ENSEMBLE_ALGORITHM_ID) != VOTING_ENSEMBLE_ALGORITHM_ID:
        failures.append("voting_ensemble.local_paper_recovery.exit_owner_mismatch")
    if int(payload.get("quantity") or 0) != int(payload.get("signedQuantity", payload.get("quantity")) or 0):
        failures.append("voting_ensemble.local_paper_recovery.position_quantity_mismatch")
    if _float(payload.get("averageEntryPrice") or payload.get("averagePrice")) < 0:
        failures.append("voting_ensemble.local_paper_recovery.position_average_price_invalid")
    return failures


def _local_recovery_accounting_failures(
    *,
    account: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    local_fills: list[Mapping[str, Any]],
    require_positions: bool = True,
) -> list[str]:
    failures: list[str] = []
    expected_cash = _float(account.get("initialCash"))
    expected_realized = 0.0
    expected_quantities: dict[str, int] = {}
    for fill in sorted(local_fills, key=lambda item: str(item.get("filledAt") or "")):
        symbol = str(fill.get("symbol") or "").upper()
        side = Signal(fill.get("side") or Signal.BUY)
        quantity = _safe_int(fill.get("filledQuantity"))
        price = _float(fill.get("averageFillPrice"))
        fee = _float(fill.get("feeAmount"))
        if quantity <= 0 or price <= 0 or not symbol:
            failures.append("voting_ensemble.local_paper_recovery.fill_required_field_invalid")
            continue
        if side == Signal.BUY:
            expected_cash -= quantity * price + fee
            expected_quantities[symbol] = expected_quantities.get(symbol, 0) + quantity
        else:
            expected_cash += quantity * price - fee
            expected_quantities[symbol] = expected_quantities.get(symbol, 0) - quantity
        expected_realized += _float(fill.get("realizedPnl"))
    if local_fills and abs(expected_cash - _float(account.get("cash"))) > 0.02:
        failures.append("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed")
    if local_fills and abs(expected_realized - _float(account.get("realizedPnl"))) > 0.02:
        failures.append("voting_ensemble.local_paper_recovery.realized_pnl_fill_invariant_failed")
    persisted_quantities: dict[str, int] = {}
    for key, payload in snapshots.items():
        if not key.startswith(f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position."):
            continue
        symbol = str(payload.get("symbol") or key.rsplit(".", 1)[-1]).upper()
        persisted_quantities[symbol] = int(payload.get("quantity") or payload.get("signedQuantity") or 0)
    if require_positions:
        for symbol, expected_quantity in expected_quantities.items():
            if expected_quantity < 0:
                failures.append("voting_ensemble.local_paper_recovery.short_position_not_enabled")
            if persisted_quantities.get(symbol, 0) != expected_quantity:
                failures.append("voting_ensemble.local_paper_recovery.position_quantity_fill_invariant_failed")
        for symbol, persisted_quantity in persisted_quantities.items():
            if symbol not in expected_quantities and persisted_quantity != 0:
                failures.append("voting_ensemble.local_paper_recovery.position_without_fill_history")
    return failures


def _reconstruct_local_positions_from_fills(local_fills: list[Mapping[str, Any]], *, evaluated_at: datetime) -> dict[str, Any]:
    states: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for fill in sorted(local_fills, key=lambda item: str(item.get("filledAt") or "")):
        symbol = str(fill.get("symbol") or "").upper()
        side = Signal(fill.get("side") or Signal.BUY)
        quantity = _safe_int(fill.get("filledQuantity"))
        price = _float(fill.get("averageFillPrice"))
        filled_at = _parse_time(fill.get("filledAt")) or evaluated_at
        if not symbol or quantity <= 0 or price <= 0:
            failures.append("voting_ensemble.local_paper_migration.fill_required_field_invalid")
            continue
        state = states.setdefault(
            symbol,
            {
                "quantity": 0,
                "averagePrice": 0.0,
                "realizedPnl": 0.0,
                "entryFillIds": [],
                "openedAt": None,
                "entryOrderId": None,
                "lastFillId": None,
                "updatedAt": filled_at,
            },
        )
        current_quantity = int(state["quantity"])
        average = float(state["averagePrice"])
        if side == Signal.BUY:
            next_quantity = current_quantity + quantity
            if next_quantity <= 0:
                failures.append("voting_ensemble.local_paper_migration.position_quantity_invalid")
                continue
            state["averagePrice"] = ((current_quantity * average) + (quantity * price)) / next_quantity if current_quantity > 0 else price
            state["quantity"] = next_quantity
            state["entryFillIds"] = [*list(state["entryFillIds"]), str(fill.get("clientOrderId") or "")]
            state["openedAt"] = state["openedAt"] or filled_at
            state["entryOrderId"] = state["entryOrderId"] or str(fill.get("clientOrderId") or "")
        else:
            if current_quantity <= 0 or quantity > current_quantity:
                failures.append("voting_ensemble.local_paper_migration.sell_would_create_short_or_reverse")
                continue
            state["quantity"] = current_quantity - quantity
            state["realizedPnl"] = float(state["realizedPnl"]) + ((price - average) * quantity)
        state["lastFillId"] = str(fill.get("clientOrderId") or "")
        state["updatedAt"] = filled_at
    positions: dict[str, dict[str, Any]] = {}
    for symbol, state in states.items():
        quantity = int(state["quantity"])
        if quantity <= 0:
            continue
        average = float(state["averagePrice"])
        updated_at = _require_utc(state["updatedAt"])
        positions[symbol] = {
            "schemaVersion": "voting_ensemble_local_position_v1",
            "symbol": symbol,
            "quantity": quantity,
            "signedQuantity": quantity,
            "side": "LONG",
            "averagePrice": round(average, 6),
            "averageEntryPrice": round(average, 6),
            "markPrice": round(average, 6),
            "notional": round(quantity * average, 6),
            "marketValue": round(quantity * average, 6),
            "unrealizedPnl": 0.0,
            "realizedPnl": round(float(state["realizedPnl"]), 6),
            "openedAt": _iso_or_none(state["openedAt"]),
            "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
            "stopPrice": None,
            "profitTargetPrice": None,
            "entryOrderId": state["entryOrderId"],
            "entryFillIds": [fill_id for fill_id in state["entryFillIds"] if fill_id],
            "lastFillId": state["lastFillId"],
            "lastMarkedAt": updated_at.isoformat().replace("+00:00", "Z"),
            "markPricePolicy": "migration_fill_price_until_fresh_nbbo_mark",
            "marketDataFresh": False,
            "quoteAgeSeconds": None,
            "marketDataReceiptAgeSeconds": None,
            "status": "OPEN",
            "source": "voting_ensemble.local_paper_migration.fill_replay",
            "sourceAuthority": "voting_ensemble_local_paper_account",
            "positionOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
            "exitOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
            "executionMode": "LOCAL_PAPER",
            "reasonCodes": ["voting_ensemble.local_paper_migration.position_created_from_legacy_fill_replay"],
        }
    return {"positions": positions, "failures": sorted(set(failures))}


def _is_local_recovery_validated_key(key: str) -> bool:
    return key.startswith(
        (
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_account.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_position.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_order.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.applied_fill.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_closed_trade.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_realized_pnl.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_risk_snapshot.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_inventory_manifest.",
            f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.local_fill_migration.",
            f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.",
        )
    )


def _local_recovery_fill_records(snapshots: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        payload
        for key, payload in snapshots.items()
        if key.startswith(f"{VOTING_ENSEMBLE_PAPER_GATEWAY_NAMESPACE}.paper_order_gateway.fill.")
        and str(payload.get("executionMode") or "").upper() == "LOCAL_PAPER"
    ]


def _snapshot_exists(repository: VotingEnsemblePaperExecutionRepository, key: str) -> bool:
    try:
        repository.read_snapshot(key)
    except KeyError:
        return False
    return True


def _is_risk_reducing_order_plan(repository: VotingEnsemblePaperExecutionRepository, order_plan: OrderPlan) -> bool:
    quantity = _positions_by_symbol(repository).get(order_plan.symbol.upper(), 0)
    side = Signal(order_plan.side)
    return (side == Signal.SELL and quantity > 0) or (side == Signal.BUY and quantity < 0)


def _executable_quantity_for_order_plan(repository: VotingEnsemblePaperExecutionRepository, order_plan: OrderPlan) -> int:
    requested = max(0, int(order_plan.quantity))
    owned = _positions_by_symbol(repository).get(order_plan.symbol.upper(), 0)
    side = Signal(order_plan.side)
    if side == Signal.SELL and owned > 0:
        return min(requested, owned)
    if side == Signal.BUY and owned < 0:
        return min(requested, abs(owned))
    return requested


def _short_entry_blockers(
    *,
    repository: VotingEnsemblePaperExecutionRepository,
    order_plan: OrderPlan,
    short_trading_enabled: bool,
) -> list[str]:
    if Signal(order_plan.side) != Signal.SELL:
        return []
    if _is_risk_reducing_order_plan(repository, order_plan):
        return []
    if short_trading_enabled:
        return []
    return ["voting_ensemble.paper_execution.short_entries_disabled"]


def _oldest_state_for_symbol(
    state_store: VotingEnsembleExecutionStateStore,
    symbol: str,
    *,
    side: Signal | str,
) -> VotingEnsembleExecutionState | None:
    candidates = [
        state
        for state in state_store.records_by_client_order_id.values()
        if state.symbol.upper() == symbol.upper() and Signal(state.side) == Signal(side) and state.filledQuantity > 0
    ]
    return min(candidates, key=lambda state: state.createdAt) if candidates else None


def _maximum_holding_minutes(state: VotingEnsembleExecutionState | None) -> int:
    if state is None:
        return 120
    try:
        plan = OrderPlan.model_validate(state.orderPlan)
    except Exception:
        return 120
    return max(1, int(plan.maximumHoldingMinutes or 120))


def _clock_requires_eod_flatten(clock: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    if bool(clock.get("forceClose") or clock.get("requiresEodFlatten")):
        return True
    next_close = _parse_time(clock.get("nextClose") or clock.get("next_close"))
    if next_close is None:
        return False
    observed = _require_utc(now or datetime.now(UTC))
    return next_close - observed <= timedelta(minutes=5)


def _has_open_exit_for_position(open_orders: list[BrokerOrderState], position: BrokerPositionState) -> bool:
    expected_side = Signal.SELL if Signal(position.side) == Signal.BUY else Signal.BUY
    return any(
        order.symbol.upper() == position.symbol.upper()
        and Signal(order.side) == expected_side
        and _is_voting_ensemble_client_order_id(order.clientOrderId)
        and str(order.clientOrderId or "").startswith("ve-exit-")
        for order in open_orders
    )


def _entry_geometry_for_exit_fallback(side: Signal | str, price: float) -> tuple[float, float]:
    normalized = Signal(side)
    if normalized == Signal.BUY:
        return max(0.01, price - 0.01), price + 0.01
    return price + 0.01, max(0.01, price - 0.01)


def _default_execution_adapter_for_runtime(
    *,
    repository: VotingEnsemblePaperExecutionRepository,
    broker_client: PaperBrokerClient | None,
) -> VotingEnsembleExecutionAdapter:
    if broker_client is None:
        return VotingEnsembleExecutionAdapter()
    return VotingEnsembleExecutionAdapter(state_store=VotingEnsembleDurableExecutionStateStore(repository))


def _normalize_execution_mode(value: str) -> VotingEnsembleExecutionMode:
    normalized = str(value or VOTING_ENSEMBLE_DEFAULT_EXECUTION_MODE).upper()
    if normalized not in {"LOCAL_PAPER", "BROKER_PAPER"}:
        raise VotingEnsemblePaperExecutionNamespaceError("Voting Ensemble paper execution mode must be LOCAL_PAPER or BROKER_PAPER")
    return normalized  # type: ignore[return-value]


def _gateway_is_local_paper(gateway: PaperOrderGateway) -> bool:
    broker = getattr(gateway, "broker", None)
    return getattr(gateway, "execution_mode", None) == "LOCAL_PAPER" and getattr(broker, "broker_kind", None) == "voting_ensemble_local_paper"


def _execution_state_key(client_order_id: str) -> str:
    return f"{VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE}.orders.{client_order_id}"


def _execution_state_record(state: VotingEnsembleExecutionState) -> dict[str, Any]:
    order_plan = _order_plan_payload(state)
    protective_order = state.protectiveOrder if isinstance(state.protectiveOrder, dict) else {}
    protective_ids = list(dict.fromkeys([*state.protectiveOrderIds, str(protective_order.get("clientOrderId") or "")]))
    return {
        "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
        "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
        "namespace": VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
        "schemaVersion": "voting_ensemble_execution_state_v1",
        "clientOrderId": state.clientOrderId,
        "brokerOrderId": state.brokerOrderId,
        "idempotencyKey": state.idempotencyKey,
        "parentDecisionId": state.parentDecisionId,
        "parentEventId": state.parentEventId,
        "settingsHash": state.settingsHash or str(order_plan.get("configurationHash") or ""),
        "symbol": state.symbol.upper(),
        "side": Signal(state.side).value,
        "requestedQuantity": state.requestedQuantity or int(order_plan.get("quantity") or 0),
        "filledQuantity": state.filledQuantity,
        "averageFillPrice": state.averageFillPrice,
        "entryOrderStatus": state.entryOrderStatus or state.status,
        "protectiveOrderIds": [value for value in protective_ids if value],
        "protectiveOrder": state.protectiveOrder,
        "stopPrice": state.stopPrice if state.stopPrice is not None else order_plan.get("stopPrice"),
        "targetPrice": state.targetPrice if state.targetPrice is not None else order_plan.get("targetPrice"),
        "timestamps": {
            "createdAt": _iso_or_none(state.createdAt),
            "updatedAt": _iso_or_none(state.updatedAt),
            "submittedAt": _iso_or_none(state.submittedAt),
            "acceptedAt": _iso_or_none(state.acceptedAt),
            "filledAt": _iso_or_none(state.filledAt),
        },
        "cooldown": {
            "until": _iso_or_none(state.cooldownUntil),
            "active": state.cooldownUntil is not None and datetime.now(UTC) < state.cooldownUntil,
        },
        "rejectionReason": state.rejectionReason,
        "reconciliationStatus": state.reconciliationStatus,
        "status": state.status,
        "completeReasonCodes": list(dict.fromkeys(state.completeReasonCodes or state.reasonCodes)),
        "reasonCodes": list(dict.fromkeys(state.reasonCodes)),
        "orderPlan": order_plan,
        "executionState": state.model_dump(mode="json"),
        "persistedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _order_plan_payload(state: VotingEnsembleExecutionState) -> dict[str, Any]:
    return dict(state.orderPlan) if isinstance(state.orderPlan, dict) else {}


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _repository_entry_blocks(repository: VotingEnsemblePaperExecutionRepository) -> list[str]:
    blocks = [] if repository.persistenceHealthy else ["voting_ensemble.paper_execution.persistence_failure_blocks_new_entries"]
    blocks.extend(_repository_reconciliation_blocks(repository))
    blocks.extend(_repository_local_entry_blocks(repository))
    return sorted(set(blocks))


def _repository_local_entry_blocks(repository: VotingEnsemblePaperExecutionRepository) -> list[str]:
    try:
        control = repository.read_snapshot("local_entry_control.end_of_day")
    except KeyError:
        return []
    if control.get("newEntriesAllowed") is False:
        return list(control.get("reasonCodes") or ["voting_ensemble.local_paper.end_of_day_blocks_new_entries"])
    return []


def _local_mark_blocks_new_entry(repository: VotingEnsemblePaperExecutionRepository, order_plan: OrderPlan, *, evaluated_at: datetime) -> tuple[str, ...]:
    if _is_risk_reducing_order_plan(repository, order_plan):
        return ()
    if repository.local_mark_fresh_for_entries(order_plan.symbol, evaluated_at=evaluated_at):
        return ()
    return ("voting_ensemble.paper_execution.local_mark_stale_blocks_new_entries",)


def _merge_permission_with_persistence(permission: Mapping[str, Any], persistence_block: list[str]) -> dict[str, Any]:
    merged = dict(permission)
    if not persistence_block:
        return merged
    reason_codes = [*list(merged.get("reasonCodes") or ()), *persistence_block]
    merged["newEntriesAllowed"] = False
    merged["effectivePaperTradingEnabled"] = False
    merged["persistenceHealthy"] = False
    merged["reasonCodes"] = list(dict.fromkeys(reason_codes))
    blockers = list(merged.get("blockers") or ())
    merged["blockers"] = list(dict.fromkeys([*blockers, *persistence_block]))
    warnings = list(merged.get("highSeverityRuntimeWarnings") or ())
    warnings.append(
        {
            "severity": "HIGH",
            "code": "voting_ensemble.paper_execution.persistence_failure_blocks_new_entries",
            "reasonCodes": persistence_block,
        }
    )
    merged["highSeverityRuntimeWarnings"] = warnings
    return merged


def _persistence_failure_result(
    intent: VotingEnsemblePaperOrderIntent,
    *,
    evaluated_at: datetime,
    submitted: bool = False,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    return {
        "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
        "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
        "paperExecutionVersion": VOTING_ENSEMBLE_PAPER_EXECUTION_VERSION,
        "orderIntentId": intent.orderIntentId,
        "clientOrderId": client_order_id or voting_ensemble_gateway_client_order_id(intent),
        "submitted": submitted,
        "status": "RECONCILIATION_REQUIRED",
        "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
        "highSeverityRuntimeWarnings": [
            {
                "severity": "HIGH",
                "code": "voting_ensemble.paper_execution.persistence_failure_blocks_new_entries",
                "reasonCodes": ["voting_ensemble.paper_execution.persistence_failure_blocks_new_entries"],
            }
        ],
        "reasonCodes": [
            "voting_ensemble.paper_execution.persistence_failure_blocks_new_entries",
            "voting_ensemble.paper_execution.exit_management_preserved_reconciliation_required",
        ],
    }


def default_paper_execution_store_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "paper_execution.json"


def _decision_key(decision_id: str) -> str:
    return f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.decisions.{decision_id}"


def _execution_idempotency_key(intent: VotingEnsemblePaperOrderIntent) -> str:
    return "voting_ensemble:execution:" + _hash(
        {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "commandIdempotencyKey": intent.idempotencyKey,
            "orderIntentId": intent.orderIntentId,
            "orderPlanId": intent.orderPlan.orderPlanId,
            "sourceJobId": intent.sourceJobId,
            "sourceCommandId": intent.sourceCommandId,
        }
    )[:32]


def _approved_settings_hash(outbox_record: Mapping[str, Any]) -> str | None:
    if outbox_record.get("approvedDecisionSettingsHash"):
        return str(outbox_record["approvedDecisionSettingsHash"])
    raw = outbox_record.get("decision")
    decision = raw if isinstance(raw, Mapping) else {}
    for key in ("settingsHash", "settings_hash", "configurationHash"):
        if decision.get(key):
            return str(decision[key])
    order_plan = outbox_record.get("orderPlan")
    if isinstance(order_plan, Mapping) and order_plan.get("configurationHash"):
        return str(order_plan["configurationHash"])
    return None


def _decision_settings_hash(decision: Mapping[str, Any]) -> str | None:
    for key in ("settingsHash", "settings_hash", "configurationHash"):
        if decision.get(key):
            return str(decision[key])
    source_inputs = decision.get("sourceInputs") if isinstance(decision.get("sourceInputs"), Mapping) else {}
    for key in ("settingsHash", "settings_hash", "configurationHash"):
        if source_inputs.get(key):
            return str(source_inputs[key])
    return None


def _intent_from_record(payload: Mapping[str, Any]) -> VotingEnsemblePaperOrderIntent:
    return VotingEnsemblePaperOrderIntent(
        algorithmId=str(payload.get("algorithm_id") or payload.get("algorithmId")),
        orderIntentId=str(payload["orderIntentId"]),
        decisionId=str(payload["decisionId"]),
        correlationId=str(payload["correlationId"]),
        idempotencyKey=str(payload["idempotencyKey"]),
        orderPlan=OrderPlan.model_validate(payload["orderPlan"]),
        localGatePassed=bool(payload.get("localGatePassed")),
        createdAt=_parse_time(payload.get("createdAt")) or datetime.now(UTC),
        sourceJobId=str(payload["sourceJobId"]) if payload.get("sourceJobId") else None,
        sourceCommandId=str(payload["sourceCommandId"]) if payload.get("sourceCommandId") else None,
    )


def _outbox_key(order_intent_id: str) -> str:
    return f"{VOTING_ENSEMBLE_PAPER_EXECUTION_NAMESPACE}.outbox.{order_intent_id}"


def _normalize_outbox_status(status: str) -> str:
    normalized = str(status or "").upper()
    aliases = {
        "QUEUED": "PENDING",
        "NOT_SUBMITTED": "BLOCKED",
        "DUPLICATE": "RECONCILIATION_REQUIRED",
        "CANCELLED": "CANCELED",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in VOTING_ENSEMBLE_EXECUTION_OUTBOX_STATES:
        return "RECONCILIATION_REQUIRED"
    return normalized


def _outbox_status_from_gateway_result(result: PaperOrderGatewayResult) -> str:
    if result.duplicate:
        return "RECONCILIATION_REQUIRED"
    if not result.submitted:
        return "REJECTED" if result.status == "REJECTED" else "BLOCKED"
    return _normalize_outbox_status(str(result.status))


def _outbox_status_from_adapter_result(result: VotingEnsembleExecutionAdapterResult) -> str:
    if result.duplicate:
        return "RECONCILIATION_REQUIRED"
    if result.status == "BLOCKED":
        return "BLOCKED"
    return _normalize_outbox_status(result.status)


def _order_intent_id(order_plan: OrderPlan, idempotency_key: str) -> str:
    return "ve-intent-" + _hash({"orderPlanId": order_plan.orderPlanId, "idempotencyKey": idempotency_key})[:20]


def _default_paper_broker() -> VotingEnsembleAlpacaPaperBroker | VotingEnsembleUnavailablePaperBroker:
    return VotingEnsembleUnavailablePaperBroker()


def _default_paper_broker_client() -> AlpacaPaperBrokerClient | None:
    try:
        return AlpacaPaperBrokerClient()
    except VotingEnsembleAlpacaPaperBrokerConfigurationError:
        return None


def _paper_settings_from_env() -> Any:
    load_dotenv()
    settings = get_settings()
    if settings.has_alpaca_credentials:
        return settings
    backend_env = os.getenv("VOTING_ENSEMBLE_DOTENV_PATH") or str(Path(__file__).resolve().parents[3] / ".env")
    load_dotenv(backend_env, override=False)
    return get_settings()


def _alpaca_order_type(order_type: Any, *, limit_price: float | None) -> str:
    normalized = str(order_type or "LIMIT").lower()
    if normalized == "stop_limit":
        return "stop_limit"
    if normalized == "market" and limit_price is None:
        return "market"
    return "limit"


def _alpaca_order_body(order_plan: OrderPlan, client_order_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": order_plan.symbol.upper(),
        "qty": str(int(order_plan.quantity)),
        "side": "buy" if Signal(order_plan.side) == Signal.BUY else "sell",
        "type": _alpaca_order_type(order_plan.orderType, limit_price=order_plan.limitPrice),
        "time_in_force": str(order_plan.timeInForce).lower(),
        "client_order_id": client_order_id,
    }
    if body["type"] in {"limit", "stop_limit"} and order_plan.limitPrice:
        body["limit_price"] = str(order_plan.limitPrice)
    if body["type"] == "stop_limit":
        body["stop_price"] = str(order_plan.entryPrice)
    if order_plan.stopPrice or order_plan.targetPrice:
        body["order_class"] = "bracket"
        if order_plan.stopPrice:
            body["stop_loss"] = {"stop_price": str(order_plan.stopPrice)}
        if order_plan.targetPrice:
            body["take_profit"] = {"limit_price": str(order_plan.targetPrice)}
    return body


def _ack_status(payload: Mapping[str, Any]) -> str:
    status = _broker_status(str(payload.get("status") or "accepted"))
    return "ACCEPTED" if status in {"ACCEPTED", "OPEN"} else status


def _broker_status(value: str) -> str:
    normalized = value.lower()
    if normalized in {"accepted", "new", "pending_new", "open"}:
        return "ACCEPTED"
    if normalized == "partially_filled":
        return "PARTIALLY_FILLED"
    if normalized == "filled":
        return "FILLED"
    if normalized in {"canceled", "cancelled", "expired"}:
        return "CANCELED"
    if normalized == "rejected":
        return "REJECTED"
    return "ACCEPTED"


def _broker_order_state(payload: Mapping[str, Any]) -> BrokerOrderState:
    submitted_at = _parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC)
    filled = int(float(payload.get("filled_qty") or 0))
    quantity = int(float(payload.get("qty") or filled or 0))
    return BrokerOrderState(
        algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
        capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
        orderIntentId=str(payload.get("client_order_id") or ""),
        symbol=str(payload.get("symbol") or "SPY").upper(),
        side=Signal.SELL if str(payload.get("side") or "").lower() == "sell" else Signal.BUY,
        clientOrderId=str(payload.get("client_order_id") or ""),
        orderType=str(payload.get("type") or "limit").upper(),
        status="PARTIALLY_FILLED" if filled > 0 else "ACCEPTED",
        quantity=quantity,
        filledQuantity=filled,
        entryPrice=_positive_float(payload.get("limit_price") or payload.get("stop_price") or payload.get("filled_avg_price")) or 0.01,
        stopPrice=_positive_float(payload.get("stop_price")),
        submittedAt=submitted_at,
    )


def _broker_position_state(payload: Mapping[str, Any]) -> BrokerPositionState:
    quantity = abs(int(float(payload.get("qty") or 0)))
    average = _positive_float(payload.get("avg_entry_price")) or 0.01
    mark = _positive_float(payload.get("current_price") or payload.get("market_value")) or average
    return BrokerPositionState(
        algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
        capitalPartitionId=VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
        symbol=str(payload.get("symbol") or "SPY").upper(),
        side=Signal.SELL if str(payload.get("side") or "").lower() == "short" else Signal.BUY,
        quantity=quantity,
        averageEntryPrice=average,
        markPrice=mark,
        realizedPnlToday=0.0,
        openedAt=None,
    )


def _fill_update_from_activity(payload: Mapping[str, Any]) -> BrokerFillUpdate:
    return BrokerFillUpdate(
        clientOrderId=str(payload.get("client_order_id") or payload.get("order_id") or "unknown-fill"),
        filledQuantity=int(float(payload.get("qty") or 0)),
        averageFillPrice=_positive_float(payload.get("price")),
        status="FILLED",
        updatedAt=_parse_time(payload.get("transaction_time") or payload.get("date")) or datetime.now(UTC),
    )


def _paper_gateway_fill_from_payload(payload: Mapping[str, Any]) -> PaperGatewayFill:
    return PaperGatewayFill(
        executionMode=_normalize_execution_mode(payload.get("executionMode") or "LOCAL_PAPER"),
        clientOrderId=str(payload.get("clientOrderId") or ""),
        algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
        capitalPartitionId=str(payload.get("capitalPartitionId") or VOTING_ENSEMBLE_CAPITAL_PARTITION_ID),
        accountId=str(payload.get("accountId") or VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID),
        orderIntentId=str(payload.get("orderIntentId") or payload.get("clientOrderId") or ""),
        symbol=str(payload.get("symbol") or "SPY").upper(),
        side=Signal(payload.get("side") or Signal.BUY),
        filledQuantity=int(payload.get("filledQuantity") or 0),
        averageFillPrice=_positive_float(payload.get("averageFillPrice")),
        status=_broker_status(str(payload.get("status") or "FILLED")),  # type: ignore[arg-type]
        filledAt=_parse_time(payload.get("filledAt")) or datetime.now(UTC),
    )


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, parsed)


def _local_paper_env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


def _extract_nbbo_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = payload.get("nbbo")
    if isinstance(direct, Mapping):
        return direct
    context = payload.get("market_context")
    if isinstance(context, Mapping):
        context_nbbo = context.get("nbbo")
        if isinstance(context_nbbo, Mapping):
            return context_nbbo
        automatic = context.get("automaticRuntimeSnapshot")
        if isinstance(automatic, Mapping):
            automatic_nbbo = automatic.get("nbbo")
            if isinstance(automatic_nbbo, Mapping):
                return automatic_nbbo
    automatic = payload.get("automaticRuntimeSnapshot")
    if isinstance(automatic, Mapping):
        automatic_nbbo = automatic.get("nbbo")
        if isinstance(automatic_nbbo, Mapping):
            return automatic_nbbo
    return None


def _extract_market_clock_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("marketClock", "market_clock", "clock"):
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    context = payload.get("market_context")
    if isinstance(context, Mapping):
        for key in ("marketClock", "market_clock", "clock"):
            candidate = context.get(key)
            if isinstance(candidate, Mapping):
                return candidate
        automatic = context.get("automaticRuntimeSnapshot")
        if isinstance(automatic, Mapping):
            for key in ("marketClock", "market_clock", "clock"):
                candidate = automatic.get(key)
                if isinstance(candidate, Mapping):
                    return candidate
    automatic = payload.get("automaticRuntimeSnapshot")
    if isinstance(automatic, Mapping):
        for key in ("marketClock", "market_clock", "clock"):
            candidate = automatic.get(key)
            if isinstance(candidate, Mapping):
                return candidate
    return None


def _nested_value(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _records_with_prefix(snapshots: Mapping[str, Mapping[str, Any]], prefix: str) -> list[dict[str, Any]]:
    return [
        {"snapshotKey": key, **dict(payload)}
        for key, payload in sorted(snapshots.items())
        if key.startswith(prefix) and payload.get("algorithmId", payload.get("algorithm_id")) == VOTING_ENSEMBLE_ALGORITHM_ID
    ]


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME = VotingEnsemblePaperExecutionRuntime(
    repository=VotingEnsemblePaperExecutionRepository(default_paper_execution_store_path()),
    auto_start=False,
)
