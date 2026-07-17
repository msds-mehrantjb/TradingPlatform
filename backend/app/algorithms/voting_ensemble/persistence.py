from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from pydantic import Field, field_validator

from backend.app.backtesting.event_replay import ReplayDecisionSnapshot, ReplayTrade
from backend.app.config import get_settings
from backend.app.database import _sqlite_path
from backend.app.domain.models import DomainModel, _require_utc

from .backtest import VOTING_ENSEMBLE_BACKTEST_VERSION
from .backtest_config import VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION
from .backtesting_adapter import VOTING_ENSEMBLE_BACKTESTING_ADAPTER_VERSION
from .ml_feature_schema import VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH
from .ml_model import VOTING_ENSEMBLE_ML_MODEL_VERSION, VOTING_ENSEMBLE_ML_THRESHOLDS_VERSION, voting_ensemble_ml_configuration_hash
from .model_calibration import VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION
from .position_state import VOTING_ENSEMBLE_ALGORITHM_ID
from .settings import VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION, VOTING_ENSEMBLE_TRADING_PROFILE_VERSION, risk_config_hash


VOTING_ENSEMBLE_PERSISTENCE_VERSION = "voting_ensemble_persistence_v1"
VOTING_ENSEMBLE_PERSISTENCE_MIGRATION_VERSION = "voting_ensemble_persistence_001"
VOTING_ENSEMBLE_DECISION_SNAPSHOT_VERSION = "voting_ensemble_decision_snapshot_v1"
VOTING_ENSEMBLE_STRATEGY_OUTPUT_VERSION = "voting_ensemble_strategy_output_v1"
VOTING_ENSEMBLE_TRADE_VERSION = "voting_ensemble_trade_v1"
VOTING_ENSEMBLE_POSITION_VERSION = "voting_ensemble_position_v1"
VOTING_ENSEMBLE_PERFORMANCE_METRIC_VERSION = "voting_ensemble_performance_metric_v1"
VOTING_ENSEMBLE_SETTINGS_VERSION_RECORD = "voting_ensemble_settings_version_record_v1"
VOTING_ENSEMBLE_MODEL_VERSION_RECORD = "voting_ensemble_model_version_record_v1"
VOTING_ENSEMBLE_BACKTEST_RUN_VERSION = "voting_ensemble_backtest_run_v1"
VOTING_ENSEMBLE_DYNAMIC_PROFILE_HISTORY_VERSION = "voting_ensemble_dynamic_profile_history_v1"


class VotingEnsemblePersistenceInventoryItem(DomainModel):
    name: str = Field(min_length=1)
    tableName: str = Field(min_length=1)
    version: str = Field(min_length=1)
    primaryKey: str = Field(min_length=1)
    ownership: str = Field(default="voting_ensemble", min_length=1)
    description: str = Field(min_length=1)
    reasonCodes: list[str] = Field(default_factory=list)


class VotingEnsembleDecisionSnapshotEnvelope(DomainModel):
    snapshotVersion: str = VOTING_ENSEMBLE_DECISION_SNAPSHOT_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    snapshotId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    sessionDate: date
    runId: str = Field(min_length=1)
    engineVersion: str = Field(min_length=1)
    configurationHash: str = Field(min_length=1)
    contentHash: str = Field(min_length=1)
    replaySnapshot: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("decisionTimestampUtc")
    @classmethod
    def decision_timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleStrategyOutputEnvelope(DomainModel):
    outputVersion: str = VOTING_ENSEMBLE_STRATEGY_OUTPUT_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    outputId: str = Field(min_length=1)
    snapshotId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    sessionDate: date
    runId: str = Field(min_length=1)
    strategyId: str = Field(min_length=1)
    family: str | None = None
    signal: str | None = None
    contentHash: str = Field(min_length=1)
    strategyOutput: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("decisionTimestampUtc")
    @classmethod
    def decision_timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleTradeEnvelope(DomainModel):
    tradeVersion: str = VOTING_ENSEMBLE_TRADE_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    tradeId: str = Field(min_length=1)
    decisionSnapshotId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str = Field(min_length=1)
    submittedAt: datetime
    filledAt: datetime | None = None
    exitAt: datetime | None = None
    runId: str = Field(min_length=1)
    side: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    pnl: float
    contentHash: str = Field(min_length=1)
    replayTrade: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("submittedAt", "filledAt", "exitAt")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class VotingEnsemblePositionEnvelope(DomainModel):
    positionVersion: str = VOTING_ENSEMBLE_POSITION_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    positionId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str = Field(min_length=1)
    observedAt: datetime
    sessionDate: date | None = None
    runId: str = Field(min_length=1)
    source: str = Field(min_length=1)
    sourcePositionId: str | None = None
    sourceTradeId: str | None = None
    decisionSnapshotId: str | None = None
    side: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    averageEntryPrice: float | None = None
    markPrice: float | None = None
    unrealizedPnl: float | None = None
    realizedPnl: float | None = None
    contentHash: str = Field(min_length=1)
    position: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsemblePerformanceMetricEnvelope(DomainModel):
    metricVersion: str = VOTING_ENSEMBLE_PERFORMANCE_METRIC_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    metricId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str | None = None
    observedAt: datetime
    sessionDate: date | None = None
    runId: str = Field(min_length=1)
    source: str = Field(min_length=1)
    metricScope: str = Field(min_length=1)
    metricName: str = Field(min_length=1)
    metricValue: float | None = None
    contentHash: str = Field(min_length=1)
    metrics: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleSettingsVersionEnvelope(DomainModel):
    settingsRecordVersion: str = VOTING_ENSEMBLE_SETTINGS_VERSION_RECORD
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    settingsId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    symbol: str | None = None
    observedAt: datetime
    sessionDate: date | None = None
    runId: str = Field(min_length=1)
    source: str = Field(min_length=1)
    settingsVersion: str = Field(min_length=1)
    profileVersion: str | None = None
    profileId: str | None = None
    configurationHash: str = Field(min_length=1)
    contentHash: str = Field(min_length=1)
    settings: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleModelVersionEnvelope(DomainModel):
    modelRecordVersion: str = VOTING_ENSEMBLE_MODEL_VERSION_RECORD
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    modelRecordId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    observedAt: datetime
    runId: str = Field(min_length=1)
    source: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    thresholdsVersion: str = Field(min_length=1)
    calibrationVersion: str = Field(min_length=1)
    featureSchemaHash: str = Field(min_length=1)
    configurationHash: str = Field(min_length=1)
    artifactId: str | None = None
    contentHash: str = Field(min_length=1)
    model: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleDynamicProfileHistoryEnvelope(DomainModel):
    historyVersion: str = VOTING_ENSEMBLE_DYNAMIC_PROFILE_HISTORY_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    profileHistoryId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    observedAt: datetime
    sessionDate: date | None = None
    runId: str = Field(min_length=1)
    source: str = Field(min_length=1)
    profileVersion: str = Field(min_length=1)
    profileId: str = Field(min_length=1)
    activeOverlays: list[str] = Field(default_factory=list)
    riskMultiplier: float | None = None
    allocationMultiplier: float | None = None
    dailyAllocationMultiplier: float | None = None
    maxTradesMultiplier: float | None = None
    slippageMultiplier: float | None = None
    entriesBlocked: bool
    contentHash: str = Field(min_length=1)
    profile: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleBacktestRunEnvelope(DomainModel):
    backtestRunVersion: str = VOTING_ENSEMBLE_BACKTEST_RUN_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION
    backtestRunId: str = Field(min_length=1)
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    runId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    startedAt: datetime | None = None
    completedAt: datetime
    source: str = Field(min_length=1)
    status: str = Field(min_length=1)
    backtestVersion: str = Field(min_length=1)
    backtestConfigVersion: str = Field(min_length=1)
    adapterVersion: str | None = None
    configurationHash: str = Field(min_length=1)
    resultHash: str = Field(min_length=1)
    totalTrades: int = Field(ge=0)
    totalPnl: float
    result: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(persistence_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("startedAt", "completedAt")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


@dataclass(frozen=True)
class VotingEnsemblePersistenceSummary:
    tableCounts: dict[str, int]
    migrationVersion: str = VOTING_ENSEMBLE_PERSISTENCE_MIGRATION_VERSION
    persistenceVersion: str = VOTING_ENSEMBLE_PERSISTENCE_VERSION


class VotingEnsemblePersistenceStore(Protocol):
    def write_decision_snapshot(
        self,
        snapshot: ReplayDecisionSnapshot | VotingEnsembleDecisionSnapshotEnvelope,
        *,
        run_id: str,
        engine_version: str,
        configuration_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> VotingEnsembleDecisionSnapshotEnvelope:
        ...

    def read_decision_snapshot(self, snapshot_id: str) -> VotingEnsembleDecisionSnapshotEnvelope | None:
        ...

    def list_decision_snapshots(
        self,
        *,
        symbol: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleDecisionSnapshotEnvelope, ...]:
        ...

    def write_strategy_outputs(
        self,
        snapshot: ReplayDecisionSnapshot,
        *,
        run_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[VotingEnsembleStrategyOutputEnvelope, ...]:
        ...

    def list_strategy_outputs(
        self,
        *,
        snapshot_id: str | None = None,
        strategy_id: str | None = None,
        symbol: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleStrategyOutputEnvelope, ...]:
        ...

    def write_trades(
        self,
        trades: list[ReplayTrade] | tuple[ReplayTrade, ...],
        *,
        run_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[VotingEnsembleTradeEnvelope, ...]:
        ...

    def read_trade(self, trade_id: str) -> VotingEnsembleTradeEnvelope | None:
        ...

    def list_trades(
        self,
        *,
        decision_snapshot_id: str | None = None,
        symbol: str | None = None,
        run_id: str | None = None,
        side: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleTradeEnvelope, ...]:
        ...

    def write_positions(self, positions: list[VotingEnsemblePositionEnvelope] | tuple[VotingEnsemblePositionEnvelope, ...]) -> tuple[VotingEnsemblePositionEnvelope, ...]:
        ...

    def read_position(self, position_id: str) -> VotingEnsemblePositionEnvelope | None:
        ...

    def list_positions(
        self,
        *,
        symbol: str | None = None,
        run_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsemblePositionEnvelope, ...]:
        ...

    def write_performance_metrics(self, metrics: list[VotingEnsemblePerformanceMetricEnvelope] | tuple[VotingEnsemblePerformanceMetricEnvelope, ...]) -> tuple[VotingEnsemblePerformanceMetricEnvelope, ...]:
        ...

    def read_performance_metric(self, metric_id: str) -> VotingEnsemblePerformanceMetricEnvelope | None:
        ...

    def list_performance_metrics(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        metric_scope: str | None = None,
        metric_name: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsemblePerformanceMetricEnvelope, ...]:
        ...

    def write_settings_versions(self, settings_versions: list[VotingEnsembleSettingsVersionEnvelope] | tuple[VotingEnsembleSettingsVersionEnvelope, ...]) -> tuple[VotingEnsembleSettingsVersionEnvelope, ...]:
        ...

    def read_settings_version(self, settings_id: str) -> VotingEnsembleSettingsVersionEnvelope | None:
        ...

    def list_settings_versions(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        settings_version: str | None = None,
        profile_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleSettingsVersionEnvelope, ...]:
        ...

    def write_model_versions(self, model_versions: list[VotingEnsembleModelVersionEnvelope] | tuple[VotingEnsembleModelVersionEnvelope, ...]) -> tuple[VotingEnsembleModelVersionEnvelope, ...]:
        ...

    def read_model_version(self, model_record_id: str) -> VotingEnsembleModelVersionEnvelope | None:
        ...

    def list_model_versions(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        model_version: str | None = None,
        artifact_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleModelVersionEnvelope, ...]:
        ...

    def write_dynamic_profile_history(self, history: list[VotingEnsembleDynamicProfileHistoryEnvelope] | tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]) -> tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]:
        ...

    def read_dynamic_profile_history(self, profile_history_id: str) -> VotingEnsembleDynamicProfileHistoryEnvelope | None:
        ...

    def list_dynamic_profile_history(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        profile_id: str | None = None,
        profile_version: str | None = None,
        entries_blocked: bool | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]:
        ...

    def write_backtest_runs(self, runs: list[VotingEnsembleBacktestRunEnvelope] | tuple[VotingEnsembleBacktestRunEnvelope, ...]) -> tuple[VotingEnsembleBacktestRunEnvelope, ...]:
        ...

    def read_backtest_run(self, backtest_run_id: str) -> VotingEnsembleBacktestRunEnvelope | None:
        ...

    def list_backtest_runs(
        self,
        *,
        run_id: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
        status: str | None = None,
        timeframe: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleBacktestRunEnvelope, ...]:
        ...

    def table_counts(self) -> VotingEnsemblePersistenceSummary:
        ...


class VotingEnsembleSqlitePersistenceRepository:
    def __init__(self, database_url: str | None = None, path: str | Path | None = None) -> None:
        if path is not None:
            self.path = Path(path).resolve()
        else:
            self.path = _sqlite_path(database_url or get_settings().database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            apply_voting_ensemble_persistence_migrations(conn)

    def write_decision_snapshot(
        self,
        snapshot: ReplayDecisionSnapshot | VotingEnsembleDecisionSnapshotEnvelope,
        *,
        run_id: str,
        engine_version: str,
        configuration_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> VotingEnsembleDecisionSnapshotEnvelope:
        envelope = (
            snapshot
            if isinstance(snapshot, VotingEnsembleDecisionSnapshotEnvelope)
            else build_voting_ensemble_decision_snapshot(
                snapshot,
                run_id=run_id,
                engine_version=engine_version,
                configuration_hash=configuration_hash,
                metadata=metadata,
            )
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO voting_ensemble_decision_snapshots (
                    snapshot_id, algorithm_id, symbol, timestamp, session_date,
                    run_id, engine_version, configuration_hash, snapshot_version,
                    content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.snapshotId,
                    envelope.algorithmId,
                    envelope.symbol,
                    envelope.decisionTimestampUtc.isoformat(),
                    envelope.sessionDate.isoformat(),
                    envelope.runId,
                    envelope.engineVersion,
                    envelope.configurationHash,
                    envelope.snapshotVersion,
                    envelope.contentHash,
                    envelope.model_dump_json(),
                ),
            )
        return envelope

    def read_decision_snapshot(self, snapshot_id: str) -> VotingEnsembleDecisionSnapshotEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_decision_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleDecisionSnapshotEnvelope.model_validate_json(row["payload_json"])

    def list_decision_snapshots(
        self,
        *,
        symbol: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleDecisionSnapshotEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_decision_snapshots
                {where}
                ORDER BY timestamp DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleDecisionSnapshotEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_strategy_outputs(
        self,
        snapshot: ReplayDecisionSnapshot,
        *,
        run_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[VotingEnsembleStrategyOutputEnvelope, ...]:
        outputs = build_voting_ensemble_strategy_outputs(snapshot, run_id=run_id, metadata=metadata)
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_strategy_outputs (
                    output_id, snapshot_id, algorithm_id, symbol, timestamp, session_date,
                    run_id, strategy_id, family, signal, output_version, content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        output.outputId,
                        output.snapshotId,
                        output.algorithmId,
                        output.symbol,
                        output.decisionTimestampUtc.isoformat(),
                        output.sessionDate.isoformat(),
                        output.runId,
                        output.strategyId,
                        output.family,
                        output.signal,
                        output.outputVersion,
                        output.contentHash,
                        output.model_dump_json(),
                    )
                    for output in outputs
                ],
            )
        return outputs

    def list_strategy_outputs(
        self,
        *,
        snapshot_id: str | None = None,
        strategy_id: str | None = None,
        symbol: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleStrategyOutputEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if snapshot_id is not None:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_strategy_outputs
                {where}
                ORDER BY timestamp DESC, strategy_id, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleStrategyOutputEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_trades(
        self,
        trades: list[ReplayTrade] | tuple[ReplayTrade, ...],
        *,
        run_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[VotingEnsembleTradeEnvelope, ...]:
        envelopes = build_voting_ensemble_trades(trades, run_id=run_id, metadata=metadata)
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_trades (
                    trade_id, decision_snapshot_id, algorithm_id, symbol, submitted_at,
                    filled_at, exit_at, run_id, side, quantity, pnl, trade_version,
                    content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        trade.tradeId,
                        trade.decisionSnapshotId,
                        trade.algorithmId,
                        trade.symbol,
                        trade.submittedAt.isoformat(),
                        trade.filledAt.isoformat() if trade.filledAt else None,
                        trade.exitAt.isoformat() if trade.exitAt else None,
                        trade.runId,
                        trade.side,
                        trade.quantity,
                        trade.pnl,
                        trade.tradeVersion,
                        trade.contentHash,
                        trade.model_dump_json(),
                    )
                    for trade in envelopes
                ],
            )
        return envelopes

    def read_trade(self, trade_id: str) -> VotingEnsembleTradeEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleTradeEnvelope.model_validate_json(row["payload_json"])

    def list_trades(
        self,
        *,
        decision_snapshot_id: str | None = None,
        symbol: str | None = None,
        run_id: str | None = None,
        side: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleTradeEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if decision_snapshot_id is not None:
            clauses.append("decision_snapshot_id = ?")
            params.append(decision_snapshot_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if side is not None:
            clauses.append("side = ?")
            params.append(side)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_trades
                {where}
                ORDER BY submitted_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleTradeEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_positions(self, positions: list[VotingEnsemblePositionEnvelope] | tuple[VotingEnsemblePositionEnvelope, ...]) -> tuple[VotingEnsemblePositionEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_positions (
                    position_id, algorithm_id, symbol, observed_at, session_date,
                    run_id, source, source_position_id, source_trade_id,
                    decision_snapshot_id, side, quantity, average_entry_price,
                    mark_price, unrealized_pnl, realized_pnl, position_version,
                    content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        position.positionId,
                        position.algorithmId,
                        position.symbol,
                        position.observedAt.isoformat(),
                        position.sessionDate.isoformat() if position.sessionDate else None,
                        position.runId,
                        position.source,
                        position.sourcePositionId,
                        position.sourceTradeId,
                        position.decisionSnapshotId,
                        position.side,
                        position.quantity,
                        position.averageEntryPrice,
                        position.markPrice,
                        position.unrealizedPnl,
                        position.realizedPnl,
                        position.positionVersion,
                        position.contentHash,
                        position.model_dump_json(),
                    )
                    for position in positions
                ],
            )
        return tuple(positions)

    def read_position(self, position_id: str) -> VotingEnsemblePositionEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_positions WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsemblePositionEnvelope.model_validate_json(row["payload_json"])

    def list_positions(
        self,
        *,
        symbol: str | None = None,
        run_id: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsemblePositionEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_positions
                {where}
                ORDER BY observed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsemblePositionEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_performance_metrics(self, metrics: list[VotingEnsemblePerformanceMetricEnvelope] | tuple[VotingEnsemblePerformanceMetricEnvelope, ...]) -> tuple[VotingEnsemblePerformanceMetricEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_performance_metrics (
                    metric_id, algorithm_id, symbol, observed_at, session_date,
                    run_id, source, metric_scope, metric_name, metric_value,
                    metric_version, content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        metric.metricId,
                        metric.algorithmId,
                        metric.symbol,
                        metric.observedAt.isoformat(),
                        metric.sessionDate.isoformat() if metric.sessionDate else None,
                        metric.runId,
                        metric.source,
                        metric.metricScope,
                        metric.metricName,
                        metric.metricValue,
                        metric.metricVersion,
                        metric.contentHash,
                        metric.model_dump_json(),
                    )
                    for metric in metrics
                ],
            )
        return tuple(metrics)

    def read_performance_metric(self, metric_id: str) -> VotingEnsemblePerformanceMetricEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_performance_metrics WHERE metric_id = ?",
                (metric_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsemblePerformanceMetricEnvelope.model_validate_json(row["payload_json"])

    def list_performance_metrics(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        metric_scope: str | None = None,
        metric_name: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsemblePerformanceMetricEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if metric_scope is not None:
            clauses.append("metric_scope = ?")
            params.append(metric_scope)
        if metric_name is not None:
            clauses.append("metric_name = ?")
            params.append(metric_name)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_performance_metrics
                {where}
                ORDER BY observed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsemblePerformanceMetricEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_settings_versions(self, settings_versions: list[VotingEnsembleSettingsVersionEnvelope] | tuple[VotingEnsembleSettingsVersionEnvelope, ...]) -> tuple[VotingEnsembleSettingsVersionEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_settings_versions (
                    settings_id, algorithm_id, symbol, observed_at, session_date,
                    run_id, source, settings_version, profile_version, profile_id,
                    configuration_hash, content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        settings.settingsId,
                        settings.algorithmId,
                        settings.symbol,
                        settings.observedAt.isoformat(),
                        settings.sessionDate.isoformat() if settings.sessionDate else None,
                        settings.runId,
                        settings.source,
                        settings.settingsVersion,
                        settings.profileVersion,
                        settings.profileId,
                        settings.configurationHash,
                        settings.contentHash,
                        settings.model_dump_json(),
                    )
                    for settings in settings_versions
                ],
            )
        return tuple(settings_versions)

    def read_settings_version(self, settings_id: str) -> VotingEnsembleSettingsVersionEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_settings_versions WHERE settings_id = ?",
                (settings_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleSettingsVersionEnvelope.model_validate_json(row["payload_json"])

    def list_settings_versions(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        settings_version: str | None = None,
        profile_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleSettingsVersionEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if settings_version is not None:
            clauses.append("settings_version = ?")
            params.append(settings_version)
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_settings_versions
                {where}
                ORDER BY observed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleSettingsVersionEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_model_versions(self, model_versions: list[VotingEnsembleModelVersionEnvelope] | tuple[VotingEnsembleModelVersionEnvelope, ...]) -> tuple[VotingEnsembleModelVersionEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_model_versions (
                    model_record_id, algorithm_id, observed_at, run_id, source,
                    model_version, thresholds_version, calibration_version,
                    feature_schema_hash, configuration_hash, artifact_id,
                    content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        model.modelRecordId,
                        model.algorithmId,
                        model.observedAt.isoformat(),
                        model.runId,
                        model.source,
                        model.modelVersion,
                        model.thresholdsVersion,
                        model.calibrationVersion,
                        model.featureSchemaHash,
                        model.configurationHash,
                        model.artifactId,
                        model.contentHash,
                        model.model_dump_json(),
                    )
                    for model in model_versions
                ],
            )
        return tuple(model_versions)

    def read_model_version(self, model_record_id: str) -> VotingEnsembleModelVersionEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_model_versions WHERE model_record_id = ?",
                (model_record_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleModelVersionEnvelope.model_validate_json(row["payload_json"])

    def list_model_versions(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        model_version: str | None = None,
        artifact_id: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleModelVersionEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if model_version is not None:
            clauses.append("model_version = ?")
            params.append(model_version)
        if artifact_id is not None:
            clauses.append("artifact_id = ?")
            params.append(artifact_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_model_versions
                {where}
                ORDER BY observed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleModelVersionEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_dynamic_profile_history(self, history: list[VotingEnsembleDynamicProfileHistoryEnvelope] | tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]) -> tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_dynamic_profile_history (
                    profile_history_id, algorithm_id, observed_at, session_date,
                    run_id, source, profile_version, profile_id, active_overlays_json,
                    risk_multiplier, allocation_multiplier, daily_allocation_multiplier,
                    max_trades_multiplier, slippage_multiplier, entries_blocked,
                    content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.profileHistoryId,
                        item.algorithmId,
                        item.observedAt.isoformat(),
                        item.sessionDate.isoformat() if item.sessionDate else None,
                        item.runId,
                        item.source,
                        item.profileVersion,
                        item.profileId,
                        json.dumps(item.activeOverlays, sort_keys=True, separators=(",", ":")),
                        item.riskMultiplier,
                        item.allocationMultiplier,
                        item.dailyAllocationMultiplier,
                        item.maxTradesMultiplier,
                        item.slippageMultiplier,
                        1 if item.entriesBlocked else 0,
                        item.contentHash,
                        item.model_dump_json(),
                    )
                    for item in history
                ],
            )
        return tuple(history)

    def read_dynamic_profile_history(self, profile_history_id: str) -> VotingEnsembleDynamicProfileHistoryEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_dynamic_profile_history WHERE profile_history_id = ?",
                (profile_history_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleDynamicProfileHistoryEnvelope.model_validate_json(row["payload_json"])

    def list_dynamic_profile_history(
        self,
        *,
        run_id: str | None = None,
        source: str | None = None,
        profile_id: str | None = None,
        profile_version: str | None = None,
        entries_blocked: bool | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleDynamicProfileHistoryEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if profile_version is not None:
            clauses.append("profile_version = ?")
            params.append(profile_version)
        if entries_blocked is not None:
            clauses.append("entries_blocked = ?")
            params.append(1 if entries_blocked else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_dynamic_profile_history
                {where}
                ORDER BY observed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleDynamicProfileHistoryEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def write_backtest_runs(self, runs: list[VotingEnsembleBacktestRunEnvelope] | tuple[VotingEnsembleBacktestRunEnvelope, ...]) -> tuple[VotingEnsembleBacktestRunEnvelope, ...]:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO voting_ensemble_backtest_runs (
                    backtest_run_id, algorithm_id, run_id, symbol, timeframe,
                    started_at, completed_at, source, status, backtest_version,
                    backtest_config_version, adapter_version, configuration_hash,
                    result_hash, total_trades, total_pnl, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run.backtestRunId,
                        run.algorithmId,
                        run.runId,
                        run.symbol,
                        run.timeframe,
                        run.startedAt.isoformat() if run.startedAt else None,
                        run.completedAt.isoformat(),
                        run.source,
                        run.status,
                        run.backtestVersion,
                        run.backtestConfigVersion,
                        run.adapterVersion,
                        run.configurationHash,
                        run.resultHash,
                        run.totalTrades,
                        run.totalPnl,
                        run.model_dump_json(),
                    )
                    for run in runs
                ],
            )
        return tuple(runs)

    def read_backtest_run(self, backtest_run_id: str) -> VotingEnsembleBacktestRunEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voting_ensemble_backtest_runs WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ).fetchone()
        if row is None:
            return None
        return VotingEnsembleBacktestRunEnvelope.model_validate_json(row["payload_json"])

    def list_backtest_runs(
        self,
        *,
        run_id: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
        status: str | None = None,
        timeframe: str | None = None,
        limit: int = 100,
    ) -> tuple[VotingEnsembleBacktestRunEnvelope, ...]:
        clauses: list[str] = []
        params: list[str | int] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM voting_ensemble_backtest_runs
                {where}
                ORDER BY completed_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return tuple(VotingEnsembleBacktestRunEnvelope.model_validate_json(row["payload_json"]) for row in rows)

    def table_counts(self) -> VotingEnsemblePersistenceSummary:
        with self.connect() as conn:
            counts = {
                item.tableName: int(conn.execute(f"SELECT COUNT(*) AS count FROM {item.tableName}").fetchone()["count"] or 0)
                for item in voting_ensemble_persistence_inventory()
            }
        return VotingEnsemblePersistenceSummary(tableCounts=counts)


def apply_voting_ensemble_persistence_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS voting_ensemble_decision_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            session_date TEXT NOT NULL,
            run_id TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            snapshot_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_decision_snapshots_lookup
        ON voting_ensemble_decision_snapshots (symbol, run_id, timestamp);

        CREATE TABLE IF NOT EXISTS voting_ensemble_strategy_outputs (
            output_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            session_date TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            family TEXT,
            signal TEXT,
            output_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_strategy_outputs_lookup
        ON voting_ensemble_strategy_outputs (symbol, run_id, snapshot_id, strategy_id);

        CREATE TABLE IF NOT EXISTS voting_ensemble_trades (
            trade_id TEXT PRIMARY KEY,
            decision_snapshot_id TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            filled_at TEXT,
            exit_at TEXT,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            pnl REAL NOT NULL,
            trade_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_trades_lookup
        ON voting_ensemble_trades (symbol, run_id, decision_snapshot_id, submitted_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_positions (
            position_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            session_date TEXT,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_position_id TEXT,
            source_trade_id TEXT,
            decision_snapshot_id TEXT,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            average_entry_price REAL,
            mark_price REAL,
            unrealized_pnl REAL,
            realized_pnl REAL,
            position_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_positions_lookup
        ON voting_ensemble_positions (symbol, run_id, source, observed_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_performance_metrics (
            metric_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT,
            observed_at TEXT NOT NULL,
            session_date TEXT,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            metric_scope TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            metric_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_performance_metrics_lookup
        ON voting_ensemble_performance_metrics (run_id, source, metric_scope, metric_name, observed_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_settings_versions (
            settings_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT,
            observed_at TEXT NOT NULL,
            session_date TEXT,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            profile_version TEXT,
            profile_id TEXT,
            configuration_hash TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_settings_versions_lookup
        ON voting_ensemble_settings_versions (run_id, source, settings_version, profile_id, observed_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_model_versions (
            model_record_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            model_version TEXT NOT NULL,
            thresholds_version TEXT NOT NULL,
            calibration_version TEXT NOT NULL,
            feature_schema_hash TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            artifact_id TEXT,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_model_versions_lookup
        ON voting_ensemble_model_versions (run_id, source, model_version, artifact_id, observed_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_dynamic_profile_history (
            profile_history_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            session_date TEXT,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            active_overlays_json TEXT NOT NULL,
            risk_multiplier REAL,
            allocation_multiplier REAL,
            daily_allocation_multiplier REAL,
            max_trades_multiplier REAL,
            slippage_multiplier REAL,
            entries_blocked INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_dynamic_profile_history_lookup
        ON voting_ensemble_dynamic_profile_history (run_id, source, profile_id, entries_blocked, observed_at);

        CREATE TABLE IF NOT EXISTS voting_ensemble_backtest_runs (
            backtest_run_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            backtest_version TEXT NOT NULL,
            backtest_config_version TEXT NOT NULL,
            adapter_version TEXT,
            configuration_hash TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            total_trades INTEGER NOT NULL,
            total_pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_voting_ensemble_backtest_runs_lookup
        ON voting_ensemble_backtest_runs (symbol, run_id, source, status, completed_at);
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        (VOTING_ENSEMBLE_PERSISTENCE_MIGRATION_VERSION,),
    )


def voting_ensemble_persistence_inventory() -> tuple[VotingEnsemblePersistenceInventoryItem, ...]:
    return (
        VotingEnsemblePersistenceInventoryItem(
            name="decision_snapshots",
            tableName="voting_ensemble_decision_snapshots",
            version=VOTING_ENSEMBLE_DECISION_SNAPSHOT_VERSION,
            primaryKey="snapshot_id",
            description=(
                "Voting Ensemble-owned decision snapshots persisted separately from shared replay "
                "snapshots, WCA decisions, and other algorithm storage."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="strategy_outputs",
            tableName="voting_ensemble_strategy_outputs",
            version=VOTING_ENSEMBLE_STRATEGY_OUTPUT_VERSION,
            primaryKey="output_id",
            description=(
                "Voting Ensemble-owned per-strategy output records persisted separately from "
                "decision snapshots, WCA strategy outputs, and shared strategy contracts."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="trades",
            tableName="voting_ensemble_trades",
            version=VOTING_ENSEMBLE_TRADE_VERSION,
            primaryKey="trade_id",
            description=(
                "Voting Ensemble-owned trade ledger persisted separately from shared replay "
                "trades, WCA trade ledger, and other algorithm execution records."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="positions",
            tableName="voting_ensemble_positions",
            version=VOTING_ENSEMBLE_POSITION_VERSION,
            primaryKey="position_id",
            description=(
                "Voting Ensemble-owned position snapshots persisted separately from broker "
                "position state, virtual ownership ledgers, and other algorithm position records."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="performance_metrics",
            tableName="voting_ensemble_performance_metrics",
            version=VOTING_ENSEMBLE_PERFORMANCE_METRIC_VERSION,
            primaryKey="metric_id",
            description=(
                "Voting Ensemble-owned performance metrics persisted separately from shared "
                "backtest outputs, WCA strategy performance, and artifact/report envelopes."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="settings_versions",
            tableName="voting_ensemble_settings_versions",
            version=VOTING_ENSEMBLE_SETTINGS_VERSION_RECORD,
            primaryKey="settings_id",
            description=(
                "Voting Ensemble-owned settings version snapshots persisted separately from "
                "application config, WCA configuration versions, and other algorithm settings."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="model_versions",
            tableName="voting_ensemble_model_versions",
            version=VOTING_ENSEMBLE_MODEL_VERSION_RECORD,
            primaryKey="model_record_id",
            description=(
                "Voting Ensemble-owned ML model version records persisted separately from "
                "generic ML artifacts, settings versions, and other algorithm model state."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="dynamic_profile_history",
            tableName="voting_ensemble_dynamic_profile_history",
            version=VOTING_ENSEMBLE_DYNAMIC_PROFILE_HISTORY_VERSION,
            primaryKey="profile_history_id",
            description=(
                "Voting Ensemble-owned dynamic trading-profile history persisted separately "
                "from settings versions, WCA dynamic profiles, and other algorithm profile state."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
        VotingEnsemblePersistenceInventoryItem(
            name="backtest_runs",
            tableName="voting_ensemble_backtest_runs",
            version=VOTING_ENSEMBLE_BACKTEST_RUN_VERSION,
            primaryKey="backtest_run_id",
            description=(
                "Voting Ensemble-owned backtest run records persisted separately from WCA "
                "backtest runs, artifacts, performance metrics, and shared backtest outputs."
            ),
            reasonCodes=list(persistence_reason_codes()),
        ),
    )


def build_voting_ensemble_decision_snapshot(
    snapshot: ReplayDecisionSnapshot,
    *,
    run_id: str,
    engine_version: str,
    configuration_hash: str,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsembleDecisionSnapshotEnvelope:
    payload = snapshot.model_dump(mode="json")
    content_hash = voting_ensemble_decision_snapshot_hash(payload)
    reason_codes = sorted(set([*snapshot.reasonCodes, *persistence_reason_codes()]))
    return VotingEnsembleDecisionSnapshotEnvelope(
        snapshotId=snapshot.snapshotId,
        symbol=snapshot.symbol,
        decisionTimestampUtc=snapshot.decisionTimestampUtc.astimezone(UTC),
        sessionDate=snapshot.sessionDate,
        runId=run_id,
        engineVersion=engine_version,
        configurationHash=configuration_hash,
        contentHash=content_hash,
        replaySnapshot=payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble decision snapshot is persisted in an algorithm-owned envelope and table namespace.",
    )


def build_voting_ensemble_strategy_outputs(
    snapshot: ReplayDecisionSnapshot,
    *,
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[VotingEnsembleStrategyOutputEnvelope, ...]:
    outputs: list[VotingEnsembleStrategyOutputEnvelope] = []
    for index, raw_output in enumerate(snapshot.strategyOutputs):
        strategy_output = dict(raw_output)
        content_hash = voting_ensemble_strategy_output_hash(strategy_output)
        strategy_id = _strategy_output_value(strategy_output, "strategyId", "strategy", "id") or f"unknown_strategy_{index}"
        family = _strategy_output_value(strategy_output, "family", "strategyFamily")
        signal = _strategy_output_value(strategy_output, "signal", "direction", "vote")
        output_id = f"ve-strategy-output-{snapshot.snapshotId}-{index}-{strategy_id}-{content_hash}"
        reason_codes = sorted(set([*snapshot.reasonCodes, *persistence_reason_codes(), VOTING_ENSEMBLE_STRATEGY_OUTPUT_VERSION]))
        outputs.append(
            VotingEnsembleStrategyOutputEnvelope(
                outputId=output_id,
                snapshotId=snapshot.snapshotId,
                symbol=snapshot.symbol,
                decisionTimestampUtc=snapshot.decisionTimestampUtc.astimezone(UTC),
                sessionDate=snapshot.sessionDate,
                runId=run_id,
                strategyId=strategy_id,
                family=family,
                signal=signal,
                contentHash=content_hash,
                strategyOutput=strategy_output,
                metadata=metadata or {},
                reasonCodes=reason_codes,
                explanation="Voting Ensemble strategy output is persisted in an algorithm-owned envelope and table namespace.",
            )
        )
    return tuple(outputs)


def build_voting_ensemble_trades(
    trades: list[ReplayTrade] | tuple[ReplayTrade, ...],
    *,
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[VotingEnsembleTradeEnvelope, ...]:
    envelopes: list[VotingEnsembleTradeEnvelope] = []
    for trade in trades:
        payload = trade.model_dump(mode="json")
        content_hash = voting_ensemble_trade_hash(payload)
        reason_codes = sorted(set([*trade.reasonCodes, *persistence_reason_codes(), VOTING_ENSEMBLE_TRADE_VERSION]))
        envelopes.append(
            VotingEnsembleTradeEnvelope(
                tradeId=trade.tradeId,
                decisionSnapshotId=trade.decisionSnapshotId,
                symbol=trade.symbol,
                submittedAt=trade.submittedAt.astimezone(UTC),
                filledAt=trade.filledAt.astimezone(UTC) if trade.filledAt else None,
                exitAt=trade.exitAt.astimezone(UTC) if trade.exitAt else None,
                runId=run_id,
                side=trade.side.value if hasattr(trade.side, "value") else str(trade.side),
                quantity=trade.quantity,
                pnl=trade.pnl,
                contentHash=content_hash,
                replayTrade=payload,
                metadata=metadata or {},
                reasonCodes=reason_codes,
                explanation="Voting Ensemble trade is persisted in an algorithm-owned ledger envelope and table namespace.",
            )
        )
    return tuple(envelopes)


def build_voting_ensemble_position(
    position: dict[str, Any],
    *,
    run_id: str,
    observed_at: datetime,
    source: str,
    position_id: str | None = None,
    session_date: date | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsemblePositionEnvelope:
    payload = dict(position)
    content_hash = voting_ensemble_position_hash(payload)
    symbol = str(payload.get("symbol") or "")
    side = str(payload.get("side") or "")
    quantity = int(payload.get("quantity") or 0)
    source_position_id = _optional_string(payload.get("positionId") or payload.get("position_id") or payload.get("id"))
    source_trade_id = _optional_string(payload.get("tradeId") or payload.get("trade_id"))
    decision_snapshot_id = _optional_string(payload.get("decisionSnapshotId") or payload.get("decision_snapshot_id") or payload.get("decisionId"))
    resolved_position_id = position_id or f"ve-position-{source}-{source_position_id or source_trade_id or content_hash}-{content_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_POSITION_VERSION]))
    return VotingEnsemblePositionEnvelope(
        positionId=resolved_position_id,
        symbol=symbol,
        observedAt=observed_at.astimezone(UTC),
        sessionDate=session_date,
        runId=run_id,
        source=source,
        sourcePositionId=source_position_id,
        sourceTradeId=source_trade_id,
        decisionSnapshotId=decision_snapshot_id,
        side=side,
        quantity=quantity,
        averageEntryPrice=_optional_float(payload.get("averageEntryPrice") or payload.get("entryPrice")),
        markPrice=_optional_float(payload.get("markPrice")),
        unrealizedPnl=_optional_float(payload.get("unrealizedPnl")),
        realizedPnl=_optional_float(payload.get("realizedPnl") or payload.get("realizedPnlToday")),
        contentHash=content_hash,
        position=payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble position is persisted in an algorithm-owned position envelope and table namespace.",
    )


def build_voting_ensemble_positions_from_trades(
    trades: list[ReplayTrade] | tuple[ReplayTrade, ...],
    *,
    run_id: str,
    observed_at: datetime,
    mark_price: float,
    session_date: date | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[VotingEnsemblePositionEnvelope, ...]:
    positions: list[VotingEnsemblePositionEnvelope] = []
    for trade in trades:
        if trade.exitAt is not None and trade.exitAt <= observed_at:
            continue
        side = trade.side.value if hasattr(trade.side, "value") else str(trade.side)
        entry_price = trade.entryPrice or mark_price
        direction = 1 if side == "BUY" else -1
        unrealized_pnl = (mark_price - entry_price) * trade.quantity * direction
        payload = {
            "tradeId": trade.tradeId,
            "decisionSnapshotId": trade.decisionSnapshotId,
            "symbol": trade.symbol,
            "side": side,
            "quantity": trade.quantity,
            "averageEntryPrice": entry_price,
            "markPrice": mark_price,
            "unrealizedPnl": unrealized_pnl,
            "openedAt": trade.filledAt.isoformat() if trade.filledAt else None,
        }
        positions.append(
            build_voting_ensemble_position(
                payload,
                run_id=run_id,
                observed_at=observed_at,
                source="replay_trade",
                position_id=f"ve-position-replay-{trade.tradeId}-{voting_ensemble_position_hash(payload)}",
                session_date=session_date,
                metadata=metadata,
            )
        )
    return tuple(positions)


def build_voting_ensemble_performance_metric(
    metrics: dict[str, Any] | Any,
    *,
    run_id: str,
    observed_at: datetime,
    source: str,
    metric_scope: str = "aggregate",
    metric_name: str = "performance",
    symbol: str | None = None,
    session_date: date | None = None,
    metric_value: float | None = None,
    metric_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsemblePerformanceMetricEnvelope:
    payload = _plain_payload(metrics)
    content_hash = voting_ensemble_performance_metric_hash(payload)
    resolved_metric_value = metric_value if metric_value is not None else _first_numeric_metric(payload, metric_name)
    resolved_metric_id = metric_id or f"ve-performance-{run_id}-{source}-{metric_scope}-{metric_name}-{content_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_PERFORMANCE_METRIC_VERSION]))
    return VotingEnsemblePerformanceMetricEnvelope(
        metricId=resolved_metric_id,
        symbol=symbol,
        observedAt=observed_at.astimezone(UTC),
        sessionDate=session_date,
        runId=run_id,
        source=source,
        metricScope=metric_scope,
        metricName=metric_name,
        metricValue=resolved_metric_value,
        contentHash=content_hash,
        metrics=payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble performance metrics are persisted in an algorithm-owned metric envelope and table namespace.",
    )


def build_voting_ensemble_settings_version(
    settings: dict[str, Any] | Any,
    *,
    run_id: str,
    observed_at: datetime,
    source: str = "effective_settings",
    symbol: str | None = None,
    session_date: date | None = None,
    settings_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsembleSettingsVersionEnvelope:
    payload = _plain_payload(settings)
    profile = payload.get("tradingProfile") if isinstance(payload.get("tradingProfile"), dict) else {}
    settings_version = str(payload.get("settingsVersion") or payload.get("baselineSettingsVersion") or VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION)
    profile_version = _optional_string(payload.get("profileVersion") or profile.get("profileVersion") or VOTING_ENSEMBLE_TRADING_PROFILE_VERSION)
    profile_id = _optional_string(payload.get("profileId") or profile.get("profileId"))
    configuration_hash = str(payload.get("configurationHash") or risk_config_hash(payload))
    content_hash = voting_ensemble_settings_version_hash(payload)
    resolved_settings_id = settings_id or f"ve-settings-{run_id}-{source}-{settings_version}-{profile_id or 'no_profile'}-{configuration_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_SETTINGS_VERSION_RECORD]))
    return VotingEnsembleSettingsVersionEnvelope(
        settingsId=resolved_settings_id,
        symbol=symbol,
        observedAt=observed_at.astimezone(UTC),
        sessionDate=session_date,
        runId=run_id,
        source=source,
        settingsVersion=settings_version,
        profileVersion=profile_version,
        profileId=profile_id,
        configurationHash=configuration_hash,
        contentHash=content_hash,
        settings=payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble settings version is persisted in an algorithm-owned settings envelope and table namespace.",
    )


def build_voting_ensemble_model_version(
    model: dict[str, Any] | Any,
    *,
    run_id: str,
    observed_at: datetime,
    source: str = "ml_model",
    model_record_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsembleModelVersionEnvelope:
    payload = _plain_payload(model)
    calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
    model_version = str(payload.get("mlModelVersion") or payload.get("modelVersion") or VOTING_ENSEMBLE_ML_MODEL_VERSION)
    thresholds_version = str(payload.get("thresholdsVersion") or payload.get("mlThresholdsVersion") or VOTING_ENSEMBLE_ML_THRESHOLDS_VERSION)
    calibration_version = str(calibration.get("calibrationVersion") or payload.get("calibrationVersion") or VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION)
    feature_schema_hash = str(payload.get("expectedFeatureSchemaHash") or payload.get("featureSchemaHash") or VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH)
    configuration_hash = str(payload.get("configurationHash") or voting_ensemble_ml_configuration_hash())
    artifact_id = _optional_string(payload.get("artifactId") or payload.get("modelArtifactId") or payload.get("id"))
    content_hash = voting_ensemble_model_version_hash(payload)
    resolved_model_record_id = model_record_id or f"ve-model-{run_id}-{source}-{model_version}-{artifact_id or 'no_artifact'}-{content_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_MODEL_VERSION_RECORD]))
    return VotingEnsembleModelVersionEnvelope(
        modelRecordId=resolved_model_record_id,
        observedAt=observed_at.astimezone(UTC),
        runId=run_id,
        source=source,
        modelVersion=model_version,
        thresholdsVersion=thresholds_version,
        calibrationVersion=calibration_version,
        featureSchemaHash=feature_schema_hash,
        configurationHash=configuration_hash,
        artifactId=artifact_id,
        contentHash=content_hash,
        model=payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble model version is persisted in an algorithm-owned model envelope and table namespace.",
    )


def build_voting_ensemble_dynamic_profile_history(
    profile: dict[str, Any] | Any,
    *,
    run_id: str,
    observed_at: datetime,
    source: str = "dynamic_trading_profile",
    session_date: date | None = None,
    profile_history_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsembleDynamicProfileHistoryEnvelope:
    payload = _plain_payload(profile)
    profile_payload = dict(payload.get("tradingProfile")) if isinstance(payload.get("tradingProfile"), dict) else payload
    profile_version = str(profile_payload.get("profileVersion") or payload.get("profileVersion") or VOTING_ENSEMBLE_TRADING_PROFILE_VERSION)
    profile_id = str(profile_payload.get("profileId") or payload.get("profileId") or "unknown_profile")
    active_overlays = [str(item) for item in _list_value(profile_payload.get("activeOverlays"))]
    entries_blocked = bool(profile_payload.get("blockNewEntries") or payload.get("entriesBlocked") or payload.get("blockNewEntries"))
    content_hash = voting_ensemble_dynamic_profile_history_hash(profile_payload)
    resolved_profile_history_id = profile_history_id or f"ve-profile-history-{run_id}-{source}-{profile_id}-{content_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_DYNAMIC_PROFILE_HISTORY_VERSION]))
    return VotingEnsembleDynamicProfileHistoryEnvelope(
        profileHistoryId=resolved_profile_history_id,
        observedAt=observed_at.astimezone(UTC),
        sessionDate=session_date,
        runId=run_id,
        source=source,
        profileVersion=profile_version,
        profileId=profile_id,
        activeOverlays=active_overlays,
        riskMultiplier=_optional_float(profile_payload.get("riskMultiplier")),
        allocationMultiplier=_optional_float(profile_payload.get("allocationMultiplier")),
        dailyAllocationMultiplier=_optional_float(profile_payload.get("dailyAllocationMultiplier")),
        maxTradesMultiplier=_optional_float(profile_payload.get("maxTradesMultiplier")),
        slippageMultiplier=_optional_float(profile_payload.get("slippageMultiplier")),
        entriesBlocked=entries_blocked,
        contentHash=content_hash,
        profile=profile_payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble dynamic trading profile is persisted in an algorithm-owned history envelope and table namespace.",
    )


def build_voting_ensemble_backtest_run(
    result: dict[str, Any] | Any,
    *,
    run_id: str,
    completed_at: datetime,
    source: str = "backtesting_adapter",
    started_at: datetime | None = None,
    config: dict[str, Any] | Any | None = None,
    backtest_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> VotingEnsembleBacktestRunEnvelope:
    payload = _plain_payload(result)
    config_payload = _plain_payload(config or {})
    result_hash = voting_ensemble_backtest_run_hash(payload)
    symbol = str(payload.get("symbol") or config_payload.get("symbol") or "SPY").upper()
    timeframe = str(payload.get("timeframe") or config_payload.get("timeframe") or "unknown")
    status = str(payload.get("status") or "completed")
    backtest_version = str(payload.get("backtestVersion") or VOTING_ENSEMBLE_BACKTEST_VERSION)
    backtest_config_version = str(payload.get("backtestConfigVersion") or config_payload.get("configVersion") or VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION)
    adapter_version = _optional_string(payload.get("backtestingAdapterVersion") or VOTING_ENSEMBLE_BACKTESTING_ADAPTER_VERSION if source == "backtesting_adapter" else payload.get("backtestingAdapterVersion"))
    configuration_hash = str(
        payload.get("configurationHash")
        or config_payload.get("configurationHash")
        or voting_ensemble_backtest_run_hash({"result": payload, "config": config_payload})
    )
    total_trades = int(_optional_float(payload.get("totalTrades")) or len(payload.get("trades") if isinstance(payload.get("trades"), list) else []))
    total_pnl = _optional_float(payload.get("totalPnl") or payload.get("totalPnL")) or 0.0
    resolved_backtest_run_id = backtest_run_id or f"ve-backtest-{run_id}-{symbol}-{timeframe}-{result_hash}"
    reason_codes = sorted(set([*persistence_reason_codes(), VOTING_ENSEMBLE_BACKTEST_RUN_VERSION]))
    return VotingEnsembleBacktestRunEnvelope(
        backtestRunId=resolved_backtest_run_id,
        runId=run_id,
        symbol=symbol,
        timeframe=timeframe,
        startedAt=started_at.astimezone(UTC) if started_at else None,
        completedAt=completed_at.astimezone(UTC),
        source=source,
        status=status,
        backtestVersion=backtest_version,
        backtestConfigVersion=backtest_config_version,
        adapterVersion=adapter_version,
        configurationHash=configuration_hash,
        resultHash=result_hash,
        totalTrades=total_trades,
        totalPnl=total_pnl,
        result=payload,
        config=config_payload,
        metadata=metadata or {},
        reasonCodes=reason_codes,
        explanation="Voting Ensemble backtest run is persisted in an algorithm-owned run envelope and table namespace.",
    )


def voting_ensemble_decision_snapshot_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_strategy_output_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_trade_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_position_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_performance_metric_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_settings_version_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_model_version_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_dynamic_profile_history_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def voting_ensemble_backtest_run_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _strategy_output_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _optional_string(value: Any) -> str | None:
    if value is None or str(value) == "":
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _plain_payload(value: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"value": value}


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _first_numeric_metric(payload: dict[str, Any], metric_name: str) -> float | None:
    candidates = (payload.get(metric_name), payload.get("metricValue"), payload.get("value"))
    for candidate in candidates:
        number = _optional_float(candidate)
        if number is not None:
            return number
    return None


def persistence_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_PERSISTENCE_VERSION,
        VOTING_ENSEMBLE_DECISION_SNAPSHOT_VERSION,
        VOTING_ENSEMBLE_STRATEGY_OUTPUT_VERSION,
        VOTING_ENSEMBLE_TRADE_VERSION,
        VOTING_ENSEMBLE_POSITION_VERSION,
        VOTING_ENSEMBLE_PERFORMANCE_METRIC_VERSION,
        VOTING_ENSEMBLE_SETTINGS_VERSION_RECORD,
        VOTING_ENSEMBLE_MODEL_VERSION_RECORD,
        VOTING_ENSEMBLE_DYNAMIC_PROFILE_HISTORY_VERSION,
        VOTING_ENSEMBLE_BACKTEST_RUN_VERSION,
        "voting_ensemble.persistence.algorithm_owned",
        "voting_ensemble.persistence.decision_snapshots",
        "voting_ensemble.persistence.strategy_outputs",
        "voting_ensemble.persistence.trades",
        "voting_ensemble.persistence.positions",
        "voting_ensemble.persistence.performance_metrics",
        "voting_ensemble.persistence.settings_versions",
        "voting_ensemble.persistence.model_versions",
        "voting_ensemble.persistence.dynamic_profile_history",
        "voting_ensemble.persistence.backtest_runs",
        "voting_ensemble.persistence.sqlite_namespace",
    )
