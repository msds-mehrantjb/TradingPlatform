"""Dedicated, versioned Meta-Strategy settings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.session import MetaStrategySession, canonical_session
from backend.app.algorithms.meta_strategy.strategy_registry import (
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
)
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_CONFIGURATION_VERSION


META_STRATEGY_SETTINGS_NAMESPACE = "meta_strategy.settings"
META_STRATEGY_DEFAULT_SETTINGS_VERSION = "meta_strategy_settings_v1"
META_STRATEGY_SETTINGS_STORE_SCHEMA_VERSION = "meta_strategy_settings_store_v1"


class MetaStrategySettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetaStrategyStrategySettings(MetaStrategySettingsModel):
    enabled: bool = True
    minimum_warmup: int = Field(default=0, ge=0)
    buy_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    sell_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class MetaStrategyContextSettings(MetaStrategySettingsModel):
    enabled: bool = True
    max_confidence_adjustment: float = Field(default=0.25, ge=0.0, le=1.0)
    evidence_multiplier: float = Field(default=1.0, ge=0.0, le=10.0)


class MetaStrategyRegimeSettings(MetaStrategySettingsModel):
    enabled: bool = True
    minimum_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MetaStrategySafetyGateSettings(MetaStrategySettingsModel):
    enabled: bool = True
    min_cash_available: float = Field(default=500.0, ge=0.0)
    max_spread_bps: float = Field(default=12.0, ge=0.0)
    min_liquidity_score: float = Field(default=0.35, ge=0.0, le=1.0)
    max_age_seconds: int = Field(default=60, ge=0)
    max_atr_percent: float = Field(default=0.05, ge=0.0)
    max_relative_volume: float = Field(default=8.0, ge=0.0)
    blackout_window_minutes: float = Field(default=15.0, ge=0.0)
    supported_sessions: tuple[str, ...] = (
        MetaStrategySession.OPENING.value,
        MetaStrategySession.MORNING.value,
        MetaStrategySession.MIDDAY.value,
        MetaStrategySession.AFTERNOON.value,
    )

    @field_validator("supported_sessions")
    @classmethod
    def supported_sessions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(canonical_session(item).value for item in value)


class MetaStrategyCandidateAggregationSettings(MetaStrategySettingsModel):
    minimum_active_strategies: int = Field(default=2, ge=0)
    minimum_independent_families: int = Field(default=2, ge=0)
    maximum_abstention_rate: float = Field(default=0.85, ge=0.0, le=1.0)
    minimum_conflict_edge: float = Field(default=0.0, ge=0.0, le=1.0)
    block_new_entries_on_safety_failure: bool = True


class MetaStrategyCorrelationSettings(MetaStrategySettingsModel):
    family_contribution_cap: float = Field(default=1.0, ge=0.0, le=1.0)
    strategy_contribution_cap: float = Field(default=1.0, ge=0.0, le=1.0)
    correlation_group_cap: float = Field(default=1.0, ge=0.0, le=1.0)


class MetaStrategyLocalRiskSettings(MetaStrategySettingsModel):
    risk_percentage: float = Field(default=0.005, ge=0.0, le=1.0)
    spread_limit_bps: float = Field(default=15.0, ge=0.0)
    liquidity_requirement: float = Field(default=50_000.0, ge=0.0)
    trade_count_limit: int = Field(default=5, ge=0)
    allow_long: bool = True
    allow_short: bool = True


class MetaStrategyPositionSizingSettings(MetaStrategySettingsModel):
    position_cap: float = Field(default=0.10, ge=0.0, le=1.0)
    maximum_share_quantity: int = Field(default=10_000, ge=0)
    liquidity_participation_rate: float = Field(default=0.10, ge=0.0, le=1.0)


class MetaStrategyEntryExitSettings(MetaStrategySettingsModel):
    entry_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    stop_multiplier: float = Field(default=1.0, gt=0.0)
    target_multiplier: float = Field(default=2.0, gt=0.0)
    maximum_holding_minutes: int = Field(default=30, gt=0)


class MetaStrategyOrderConstructionSettings(MetaStrategySettingsModel):
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    time_in_force: str = Field(default="DAY", min_length=1)
    limit_offset_bps: float = Field(default=0.0, ge=0.0)


class MetaStrategyPaperExecutionSettings(MetaStrategySettingsModel):
    enabled: bool = True
    synthetic_immediate_fills_allowed: bool = False
    local_diagnostics_only: bool = True


class MetaStrategyMLInferenceSettings(MetaStrategySettingsModel):
    mode: Literal["DISABLED", "SHADOW", "FILTER", "ACTIVE"] = "FILTER"
    model_probability_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    fallback_behavior: Literal["NO_TRADE", "DETERMINISTIC_BASELINE"] = "NO_TRADE"


class MetaStrategyTrainingPromotionSettings(MetaStrategySettingsModel):
    minimum_training_rows: int = Field(default=100, ge=0)
    require_validation_evidence: bool = True
    promotion_requires_paper_stability: bool = True


class MetaStrategySessionSettings(MetaStrategySettingsModel):
    allowed_sessions: tuple[str, ...] = (
        MetaStrategySession.OPENING.value,
        MetaStrategySession.MORNING.value,
        MetaStrategySession.MIDDAY.value,
        MetaStrategySession.AFTERNOON.value,
    )

    @field_validator("allowed_sessions")
    @classmethod
    def allowed_sessions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(canonical_session(item).value for item in value)


class MetaStrategyDynamicOverlaySettings(MetaStrategySettingsModel):
    disable_trading: bool = False
    risk_multiplier: float = Field(default=1.0, ge=0.0)
    position_size_multiplier: float = Field(default=1.0, ge=0.0)
    trade_count_limit: int | None = Field(default=None, ge=0)
    allowed_sessions: tuple[str, ...] | None = None
    evidence_threshold_increase: float = Field(default=0.0, ge=0.0)
    spread_limit_bps: float | None = Field(default=None, ge=0.0)
    reason: str = Field(default="baseline", min_length=1)

    @field_validator("allowed_sessions")
    @classmethod
    def overlay_sessions_are_canonical(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return None if value is None else tuple(canonical_session(item).value for item in value)


class MetaStrategySettings(MetaStrategySettingsModel):
    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    settings_namespace: str = META_STRATEGY_SETTINGS_NAMESPACE
    settings_version: str = Field(default=META_STRATEGY_DEFAULT_SETTINGS_VERSION, min_length=1)
    configuration_version: str = META_STRATEGY_CONFIGURATION_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["BASELINE", "DRAFT", "ACTIVE", "EFFECTIVE"] = "BASELINE"
    baseline_settings_version: str | None = None
    baseline_settings_hash: str | None = None
    directional_strategies: dict[str, MetaStrategyStrategySettings]
    context_strategies: dict[str, MetaStrategyContextSettings]
    regime_classification: dict[str, MetaStrategyRegimeSettings]
    safety_gates: dict[str, MetaStrategySafetyGateSettings]
    candidate_aggregation: MetaStrategyCandidateAggregationSettings = Field(default_factory=MetaStrategyCandidateAggregationSettings)
    correlation_controls: MetaStrategyCorrelationSettings = Field(default_factory=MetaStrategyCorrelationSettings)
    local_risk: MetaStrategyLocalRiskSettings = Field(default_factory=MetaStrategyLocalRiskSettings)
    position_sizing: MetaStrategyPositionSizingSettings = Field(default_factory=MetaStrategyPositionSizingSettings)
    entry_exit_management: MetaStrategyEntryExitSettings = Field(default_factory=MetaStrategyEntryExitSettings)
    order_construction: MetaStrategyOrderConstructionSettings = Field(default_factory=MetaStrategyOrderConstructionSettings)
    paper_execution: MetaStrategyPaperExecutionSettings = Field(default_factory=MetaStrategyPaperExecutionSettings)
    ml_inference: MetaStrategyMLInferenceSettings = Field(default_factory=MetaStrategyMLInferenceSettings)
    training_promotion: MetaStrategyTrainingPromotionSettings = Field(default_factory=MetaStrategyTrainingPromotionSettings)
    sessions: MetaStrategySessionSettings = Field(default_factory=MetaStrategySessionSettings)
    dynamic_overlays: tuple[MetaStrategyDynamicOverlaySettings, ...] = ()
    reason_codes: tuple[str, ...] = ("meta_strategy.settings.baseline",)

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("meta_strategy.settings.created_at_must_be_timezone_aware")
        return value

    @model_validator(mode="after")
    def strategy_settings_cover_registry(self) -> MetaStrategySettings:
        _require_keys("directional", self.directional_strategies, tuple(entry.strategy_id for entry in DIRECTIONAL_STRATEGIES))
        _require_keys("context", self.context_strategies, tuple(entry.strategy_id for entry in CONTEXT_STRATEGIES))
        _require_keys("regime", self.regime_classification, tuple(entry.strategy_id for entry in REGIME_STRATEGIES))
        _require_keys("safety", self.safety_gates, tuple(entry.strategy_id for entry in SAFETY_STRATEGIES))
        return self

    @property
    def settings_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"reason_codes"})
        return _stable_hash(payload)

    @property
    def effective_settings_hash(self) -> str:
        return self.settings_hash

    def to_baseline_settings(self) -> MetaStrategyBaselineSettings:
        return MetaStrategyBaselineSettings(
            configuration_version=self.configuration_version,
            entry_threshold=self.entry_exit_management.entry_threshold,
            model_probability_threshold=self.ml_inference.model_probability_threshold,
            risk_percentage=self.local_risk.risk_percentage,
            position_cap=self.position_sizing.position_cap,
            stop_multiplier=self.entry_exit_management.stop_multiplier,
            target_multiplier=self.entry_exit_management.target_multiplier,
            maximum_holding_minutes=self.entry_exit_management.maximum_holding_minutes,
            spread_limit_bps=self.local_risk.spread_limit_bps,
            liquidity_requirement=self.local_risk.liquidity_requirement,
            trade_count_limit=self.local_risk.trade_count_limit,
            allow_long=self.local_risk.allow_long,
            allow_short=self.local_risk.allow_short,
        )


class MetaStrategySettingsLifecycleRecord(MetaStrategySettingsModel):
    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    event_type: str
    settings_version: str
    actor: str
    recorded_at: datetime
    reason_codes: tuple[str, ...]


class MetaStrategyPromotionRecord(MetaStrategySettingsModel):
    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    promoted_settings_version: str
    actor: str
    promoted_at: datetime
    validation_evidence: dict[str, Any]
    reason_codes: tuple[str, ...]


class MetaStrategyRollbackRecord(MetaStrategySettingsModel):
    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    previous_settings_version: str
    restored_settings_version: str
    actor: str
    rolled_back_at: datetime
    reason: str
    reason_codes: tuple[str, ...]


def build_meta_strategy_settings(
    *,
    settings_version: str = META_STRATEGY_DEFAULT_SETTINGS_VERSION,
    created_at: datetime | None = None,
    status: Literal["BASELINE", "DRAFT", "ACTIVE", "EFFECTIVE"] = "BASELINE",
    directional_strategies: dict[str, Any] | None = None,
    context_strategies: dict[str, Any] | None = None,
    regime_classification: dict[str, Any] | None = None,
    safety_gates: dict[str, Any] | None = None,
    candidate_aggregation: dict[str, Any] | MetaStrategyCandidateAggregationSettings | None = None,
    correlation_controls: dict[str, Any] | MetaStrategyCorrelationSettings | None = None,
    local_risk: dict[str, Any] | MetaStrategyLocalRiskSettings | None = None,
    position_sizing: dict[str, Any] | MetaStrategyPositionSizingSettings | None = None,
    entry_exit_management: dict[str, Any] | MetaStrategyEntryExitSettings | None = None,
    order_construction: dict[str, Any] | MetaStrategyOrderConstructionSettings | None = None,
    paper_execution: dict[str, Any] | MetaStrategyPaperExecutionSettings | None = None,
    ml_inference: dict[str, Any] | MetaStrategyMLInferenceSettings | None = None,
    training_promotion: dict[str, Any] | MetaStrategyTrainingPromotionSettings | None = None,
    sessions: dict[str, Any] | MetaStrategySessionSettings | None = None,
    dynamic_overlays: tuple[MetaStrategyDynamicOverlaySettings, ...] = (),
) -> MetaStrategySettings:
    return MetaStrategySettings(
        settings_version=settings_version,
        created_at=created_at or datetime.now(UTC),
        status=status,
        directional_strategies=_merge_strategy_settings(_default_directional_settings(), directional_strategies, MetaStrategyStrategySettings),
        context_strategies=_merge_strategy_settings(_default_context_settings(), context_strategies, MetaStrategyContextSettings),
        regime_classification=_merge_strategy_settings(_default_regime_settings(), regime_classification, MetaStrategyRegimeSettings),
        safety_gates=_merge_strategy_settings(_default_safety_settings(), safety_gates, MetaStrategySafetyGateSettings),
        candidate_aggregation=_coerce(candidate_aggregation, MetaStrategyCandidateAggregationSettings),
        correlation_controls=_coerce(correlation_controls, MetaStrategyCorrelationSettings),
        local_risk=_coerce(local_risk, MetaStrategyLocalRiskSettings),
        position_sizing=_coerce(position_sizing, MetaStrategyPositionSizingSettings),
        entry_exit_management=_coerce(entry_exit_management, MetaStrategyEntryExitSettings),
        order_construction=_coerce(order_construction, MetaStrategyOrderConstructionSettings),
        paper_execution=_coerce(paper_execution, MetaStrategyPaperExecutionSettings),
        ml_inference=_coerce(ml_inference, MetaStrategyMLInferenceSettings),
        training_promotion=_coerce(training_promotion, MetaStrategyTrainingPromotionSettings),
        sessions=_coerce(sessions, MetaStrategySessionSettings),
        dynamic_overlays=dynamic_overlays,
    )


def resolve_meta_strategy_effective_settings(
    baseline: MetaStrategySettings,
    overlay: MetaStrategyDynamicOverlaySettings | None = None,
    *,
    calculated_at: datetime | None = None,
) -> MetaStrategySettings:
    active_overlay = overlay or MetaStrategyDynamicOverlaySettings()
    _validate_overlay_bounds(baseline, active_overlay)
    local_risk = baseline.local_risk.model_copy(
        update={
            "risk_percentage": 0.0 if active_overlay.disable_trading else baseline.local_risk.risk_percentage * active_overlay.risk_multiplier,
            "trade_count_limit": 0 if active_overlay.disable_trading else (active_overlay.trade_count_limit if active_overlay.trade_count_limit is not None else baseline.local_risk.trade_count_limit),
            "spread_limit_bps": active_overlay.spread_limit_bps if active_overlay.spread_limit_bps is not None else baseline.local_risk.spread_limit_bps,
            "allow_long": baseline.local_risk.allow_long and not active_overlay.disable_trading,
            "allow_short": baseline.local_risk.allow_short and not active_overlay.disable_trading,
        }
    )
    position_sizing = baseline.position_sizing.model_copy(
        update={
            "position_cap": 0.0 if active_overlay.disable_trading else baseline.position_sizing.position_cap * active_overlay.position_size_multiplier,
        }
    )
    sessions = baseline.sessions.model_copy(
        update={
            "allowed_sessions": () if active_overlay.disable_trading else (active_overlay.allowed_sessions or baseline.sessions.allowed_sessions),
        }
    )
    entry_exit = baseline.entry_exit_management.model_copy(
        update={
            "entry_threshold": min(1.0, baseline.entry_exit_management.entry_threshold + active_overlay.evidence_threshold_increase),
        }
    )
    return baseline.model_copy(
        update={
            "settings_version": f"{baseline.settings_version}.effective.{_stable_hash(active_overlay.model_dump(mode='json'))}",
            "status": "EFFECTIVE",
            "created_at": calculated_at or datetime.now(UTC),
            "baseline_settings_version": baseline.settings_version,
            "baseline_settings_hash": baseline.settings_hash,
            "local_risk": local_risk,
            "position_sizing": position_sizing,
            "sessions": sessions,
            "entry_exit_management": entry_exit,
            "dynamic_overlays": (active_overlay,),
            "reason_codes": ("meta_strategy.settings.effective_resolved",),
        }
    )


class MetaStrategySettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def create_baseline(self, settings: MetaStrategySettings, *, actor: str) -> MetaStrategySettings:
        self._reject_foreign(settings)
        stored = settings.model_copy(update={"status": "BASELINE"})
        self._write_settings(stored, table="meta_strategy_settings_versions")
        self._record_lifecycle("baseline_created", stored.settings_version, actor, ("meta_strategy.settings.baseline_created",))
        return stored

    def activate_settings(self, settings_version: str, *, actor: str) -> MetaStrategySettings:
        settings = self.get_settings(settings_version).model_copy(update={"status": "ACTIVE"})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta_strategy_settings_active_pointer(algorithm_id, active_settings_version, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(algorithm_id) DO UPDATE SET
                    active_settings_version=excluded.active_settings_version,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (ALGORITHM_ID, settings.settings_version, datetime.now(UTC).isoformat(), actor),
            )
        self._write_settings(settings, table="meta_strategy_settings_versions")
        self._record_lifecycle("settings_activated", settings.settings_version, actor, ("meta_strategy.settings.activated",))
        return settings

    def create_draft(self, settings: MetaStrategySettings, *, actor: str) -> MetaStrategySettings:
        self._reject_foreign(settings)
        draft = settings.model_copy(update={"status": "DRAFT"})
        self._write_settings(draft, table="meta_strategy_settings_drafts")
        self._record_lifecycle("draft_created", draft.settings_version, actor, ("meta_strategy.settings.draft_created",))
        return draft

    def promote_draft(self, settings_version: str, *, actor: str, validation_evidence: dict[str, Any]) -> MetaStrategyPromotionRecord:
        if not validation_evidence or validation_evidence.get("validated") is not True:
            raise ValueError("meta_strategy.settings.promotion_requires_validation_evidence")
        draft = self._read_settings(settings_version, table="meta_strategy_settings_drafts")
        promoted = draft.model_copy(update={"status": "ACTIVE"})
        self._write_settings(promoted, table="meta_strategy_settings_versions")
        self.activate_settings(promoted.settings_version, actor=actor)
        record = MetaStrategyPromotionRecord(
            promoted_settings_version=promoted.settings_version,
            actor=actor,
            promoted_at=datetime.now(UTC),
            validation_evidence=dict(validation_evidence),
            reason_codes=("meta_strategy.settings.draft_promoted",),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta_strategy_settings_promotion_history(record_json) VALUES (?)",
                (_json(record.model_dump(mode="json")),),
            )
        return record

    def rollback_to(self, settings_version: str, *, actor: str, reason: str) -> MetaStrategyRollbackRecord:
        previous = self.get_active_settings().settings_version
        restored = self.activate_settings(settings_version, actor=actor)
        record = MetaStrategyRollbackRecord(
            previous_settings_version=previous,
            restored_settings_version=restored.settings_version,
            actor=actor,
            rolled_back_at=datetime.now(UTC),
            reason=reason,
            reason_codes=("meta_strategy.settings.rollback_applied",),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta_strategy_settings_rollback_history(record_json) VALUES (?)",
                (_json(record.model_dump(mode="json")),),
            )
        return record

    def get_settings(self, settings_version: str) -> MetaStrategySettings:
        return self._read_settings(settings_version, table="meta_strategy_settings_versions")

    def get_active_settings(self) -> MetaStrategySettings:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT active_settings_version FROM meta_strategy_settings_active_pointer WHERE algorithm_id=?",
                (ALGORITHM_ID,),
            ).fetchone()
        if row is None:
            baseline = self.create_baseline(build_meta_strategy_settings(), actor="system")
            return self.activate_settings(baseline.settings_version, actor="system")
        return self.get_settings(str(row["active_settings_version"]))

    def has_active_settings(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT active_settings_version FROM meta_strategy_settings_active_pointer WHERE algorithm_id=?",
                (ALGORITHM_ID,),
            ).fetchone()
        return row is not None

    def persist_effective_settings(self, settings: MetaStrategySettings) -> None:
        self._reject_foreign(settings)
        self._write_settings(settings, table="meta_strategy_settings_effective_profiles")

    def promotion_history(self) -> tuple[MetaStrategyPromotionRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT record_json FROM meta_strategy_settings_promotion_history ORDER BY id").fetchall()
        return tuple(MetaStrategyPromotionRecord.model_validate_json(row["record_json"]) for row in rows)

    def rollback_history(self) -> tuple[MetaStrategyRollbackRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT record_json FROM meta_strategy_settings_rollback_history ORDER BY id").fetchall()
        return tuple(MetaStrategyRollbackRecord.model_validate_json(row["record_json"]) for row in rows)

    def settings_history(self, *, include_drafts: bool = False, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 500))
        tables = ("meta_strategy_settings_versions", "meta_strategy_settings_drafts") if include_drafts else ("meta_strategy_settings_versions",)
        records: list[dict[str, Any]] = []
        with self._connect() as conn:
            for table in tables:
                rows = conn.execute(
                    f"""
                    SELECT settings_version, algorithm_id, status, settings_hash, settings_json, created_at
                    FROM {table}
                    WHERE algorithm_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (ALGORITHM_ID, bounded),
                ).fetchall()
                records.extend(
                    {
                        "settingsVersion": str(row["settings_version"]),
                        "algorithmId": str(row["algorithm_id"]),
                        "status": str(row["status"]),
                        "settingsHash": str(row["settings_hash"]),
                        "createdAt": str(row["created_at"]),
                        "settings": json.loads(str(row["settings_json"])),
                    }
                    for row in rows
                )
        return tuple(sorted(records, key=lambda item: str(item["createdAt"]), reverse=True)[:bounded])

    def effective_profiles(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT settings_version, algorithm_id, status, settings_hash, settings_json, created_at
                FROM meta_strategy_settings_effective_profiles
                WHERE algorithm_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (ALGORITHM_ID, bounded),
            ).fetchall()
        return tuple(
            {
                "settingsVersion": str(row["settings_version"]),
                "algorithmId": str(row["algorithm_id"]),
                "status": str(row["status"]),
                "effectiveSettingsHash": str(row["settings_hash"]),
                "createdAt": str(row["created_at"]),
                "settings": json.loads(str(row["settings_json"])),
            }
            for row in rows
        )

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_strategy_settings_versions (
                    settings_version TEXT PRIMARY KEY,
                    algorithm_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    settings_hash TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_strategy_settings_active_pointer (
                    algorithm_id TEXT PRIMARY KEY,
                    active_settings_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )
            for table in ("meta_strategy_settings_drafts", "meta_strategy_settings_effective_profiles"):
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        settings_version TEXT PRIMARY KEY,
                        algorithm_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        settings_hash TEXT NOT NULL,
                        settings_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            conn.execute("CREATE TABLE IF NOT EXISTS meta_strategy_settings_promotion_history (id INTEGER PRIMARY KEY AUTOINCREMENT, record_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta_strategy_settings_rollback_history (id INTEGER PRIMARY KEY AUTOINCREMENT, record_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta_strategy_settings_lifecycle_history (id INTEGER PRIMARY KEY AUTOINCREMENT, record_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta_strategy_settings_schema(version TEXT PRIMARY KEY)")
            conn.execute("INSERT OR IGNORE INTO meta_strategy_settings_schema(version) VALUES (?)", (META_STRATEGY_SETTINGS_STORE_SCHEMA_VERSION,))

    def _write_settings(self, settings: MetaStrategySettings, *, table: str) -> None:
        self._reject_foreign(settings)
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {table}(settings_version, algorithm_id, status, settings_hash, settings_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(settings_version) DO UPDATE SET
                    status=excluded.status,
                    settings_hash=excluded.settings_hash,
                    settings_json=excluded.settings_json,
                    created_at=excluded.created_at
                """,
                (
                    settings.settings_version,
                    settings.algorithm_id,
                    settings.status,
                    settings.settings_hash,
                    _json(settings.model_dump(mode="json")),
                    settings.created_at.isoformat(),
                ),
            )

    def _read_settings(self, settings_version: str, *, table: str) -> MetaStrategySettings:
        with self._connect() as conn:
            row = conn.execute(f"SELECT settings_json FROM {table} WHERE settings_version=?", (settings_version,)).fetchone()
        if row is None:
            raise KeyError(settings_version)
        return MetaStrategySettings.model_validate(_upgrade_settings_payload(json.loads(row["settings_json"])))

    def _record_lifecycle(self, event_type: str, settings_version: str, actor: str, reason_codes: tuple[str, ...]) -> None:
        record = MetaStrategySettingsLifecycleRecord(
            event_type=event_type,
            settings_version=settings_version,
            actor=actor,
            recorded_at=datetime.now(UTC),
            reason_codes=reason_codes,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta_strategy_settings_lifecycle_history(record_json) VALUES (?)",
                (_json(record.model_dump(mode="json")),),
            )

    def _reject_foreign(self, settings: MetaStrategySettings) -> None:
        if settings.algorithm_id != ALGORITHM_ID:
            raise ValueError(f"meta_strategy.settings.rejects_foreign_algorithm.{settings.algorithm_id}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _default_directional_settings() -> dict[str, MetaStrategyStrategySettings]:
    defaults: dict[str, tuple[int, float, float]] = {
        "multi_timeframe_trend_alignment": (50, 0.60, 0.60),
        "first_pullback_after_open": (30, 0.55, 0.55),
        "vwap_trend_continuation": (30, 0.50, 0.50),
        "opening_range_breakout": (30, 0.65, 0.65),
        "volatility_breakout": (50, 0.70, 0.70),
        "failed_breakout_reversal": (40, 0.60, 0.60),
        "liquidity_sweep_reversal": (40, 0.62, 0.62),
        "vwap_mean_reversion": (40, 0.58, 0.58),
        "bollinger_atr_reversion": (50, 0.60, 0.60),
        "gap_continuation": (30, 0.57, 0.57),
        "gap_fade": (30, 0.57, 0.57),
        "economic_event_reaction": (30, 0.62, 0.62),
    }
    return {
        entry.strategy_id: MetaStrategyStrategySettings(
            enabled=entry.enabled,
            minimum_warmup=defaults.get(entry.strategy_id, (entry.minimum_warmup, 0.0, 0.0))[0],
            buy_threshold=defaults.get(entry.strategy_id, (entry.minimum_warmup, 0.0, 0.0))[1],
            sell_threshold=defaults.get(entry.strategy_id, (entry.minimum_warmup, 0.0, 0.0))[2],
        )
        for entry in DIRECTIONAL_STRATEGIES
    }


def _default_context_settings() -> dict[str, MetaStrategyContextSettings]:
    return {entry.strategy_id: MetaStrategyContextSettings(enabled=entry.enabled) for entry in CONTEXT_STRATEGIES}


def _default_regime_settings() -> dict[str, MetaStrategyRegimeSettings]:
    return {entry.strategy_id: MetaStrategyRegimeSettings(enabled=entry.enabled) for entry in REGIME_STRATEGIES}


def _default_safety_settings() -> dict[str, MetaStrategySafetyGateSettings]:
    overrides = {
        "cash_avoid_trading_filter": {"min_cash_available": 500.0},
        "excessive_spread_filter": {"max_spread_bps": 12.0},
        "insufficient_liquidity_filter": {"min_liquidity_score": 0.35},
        "stale_market_data_filter": {"max_age_seconds": 90},
        "extreme_volatility_filter": {"max_atr_percent": 0.045, "max_relative_volume": 5.0},
        "economic_event_blackout_filter": {"blackout_window_minutes": 15.0},
        "daily_loss_limit_filter": {},
        "trade_count_limit_filter": {},
        "duplicate_order_protection_filter": {},
        "existing_position_policy_filter": {},
        "local_risk_budget_filter": {},
    }
    return {
        entry.strategy_id: MetaStrategySafetyGateSettings(enabled=entry.enabled, **overrides.get(entry.strategy_id, {}))
        for entry in SAFETY_STRATEGIES
    }


def _upgrade_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    upgraded = dict(payload)
    directional = dict(upgraded.get("directional_strategies") or {})
    combined_gap = directional.pop("gap_continuation_gap_fade", None)
    if combined_gap is not None:
        directional.setdefault("gap_continuation", {**dict(combined_gap), "enabled": False})
        directional.setdefault("gap_fade", {**dict(combined_gap), "enabled": False})
    for key, value in _default_directional_settings().items():
        directional.setdefault(key, value.model_dump(mode="python"))
    upgraded["directional_strategies"] = {
        key: directional[key]
        for key in (entry.strategy_id for entry in DIRECTIONAL_STRATEGIES)
        if key in directional
    }

    regime = dict(upgraded.get("regime_classification") or {})
    legacy_regime = regime.get("adx_trend_strength_regime") or regime.get("atr_volatility_regime")
    if legacy_regime is not None:
        regime.setdefault("adx_atr_regime_classifier", dict(legacy_regime))
    for key, value in _default_regime_settings().items():
        regime.setdefault(key, value.model_dump(mode="python"))
    upgraded["regime_classification"] = {
        key: regime[key]
        for key in (entry.strategy_id for entry in REGIME_STRATEGIES)
        if key in regime
    }

    safety = dict(upgraded.get("safety_gates") or {})
    for key, value in _default_safety_settings().items():
        safety.setdefault(key, value.model_dump(mode="python"))
    upgraded["safety_gates"] = {
        key: safety[key]
        for key in (entry.strategy_id for entry in SAFETY_STRATEGIES)
        if key in safety
    }
    return upgraded


def _merge_strategy_settings(defaults: dict[str, Any], overlays: dict[str, Any] | None, model_type: type[BaseModel]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in (overlays or {}).items():
        if key not in merged:
            raise ValueError(f"meta_strategy.settings.unknown_strategy.{key}")
        base = merged[key].model_dump(mode="python")
        update = value.model_dump(mode="python") if isinstance(value, BaseModel) else dict(value)
        merged[key] = model_type(**{**base, **update})
    return merged


def _coerce(value: dict[str, Any] | BaseModel | None, model_type: type[BaseModel]) -> Any:
    if value is None:
        return model_type()
    if isinstance(value, model_type):
        return value
    if isinstance(value, BaseModel):
        return model_type(**value.model_dump(mode="python"))
    return model_type(**dict(value))


def _require_keys(label: str, values: dict[str, Any], required: tuple[str, ...]) -> None:
    missing = tuple(key for key in required if key not in values)
    extra = tuple(key for key in values if key not in required)
    if missing or extra:
        raise ValueError(f"meta_strategy.settings.{label}_keys_invalid.missing={missing}.extra={extra}")


def _validate_overlay_bounds(baseline: MetaStrategySettings, overlay: MetaStrategyDynamicOverlaySettings) -> None:
    if overlay.risk_multiplier > 1.0:
        raise ValueError("meta_strategy.settings.overlay_must_not_increase_risk")
    if overlay.position_size_multiplier > 1.0:
        raise ValueError("meta_strategy.settings.overlay_must_not_increase_capital_use")
    if overlay.trade_count_limit is not None and overlay.trade_count_limit > baseline.local_risk.trade_count_limit:
        raise ValueError("meta_strategy.settings.overlay_must_not_increase_trade_count")
    if overlay.spread_limit_bps is not None and overlay.spread_limit_bps > baseline.local_risk.spread_limit_bps:
        raise ValueError("meta_strategy.settings.overlay_must_not_increase_spread_tolerance")
    if overlay.allowed_sessions is not None and not set(overlay.allowed_sessions).issubset(set(baseline.sessions.allowed_sessions)):
        raise ValueError("meta_strategy.settings.overlay_sessions_must_be_subset")


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "META_STRATEGY_DEFAULT_SETTINGS_VERSION",
    "META_STRATEGY_SETTINGS_NAMESPACE",
    "META_STRATEGY_SETTINGS_STORE_SCHEMA_VERSION",
    "MetaStrategyCandidateAggregationSettings",
    "MetaStrategyContextSettings",
    "MetaStrategyCorrelationSettings",
    "MetaStrategyDynamicOverlaySettings",
    "MetaStrategyEntryExitSettings",
    "MetaStrategyLocalRiskSettings",
    "MetaStrategyMLInferenceSettings",
    "MetaStrategyOrderConstructionSettings",
    "MetaStrategyPaperExecutionSettings",
    "MetaStrategyPositionSizingSettings",
    "MetaStrategyPromotionRecord",
    "MetaStrategyRegimeSettings",
    "MetaStrategyRollbackRecord",
    "MetaStrategySafetyGateSettings",
    "MetaStrategySessionSettings",
    "MetaStrategySettings",
    "MetaStrategySettingsStore",
    "MetaStrategyStrategySettings",
    "MetaStrategyTrainingPromotionSettings",
    "build_meta_strategy_settings",
    "resolve_meta_strategy_effective_settings",
]
