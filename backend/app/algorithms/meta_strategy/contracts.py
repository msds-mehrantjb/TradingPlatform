"""Canonical immutable backend contracts for the Meta-Strategy boundary."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.algorithms.meta_strategy.identity import (
    ALGORITHM_ID,
    ALGORITHM_NAME,
    META_STRATEGY_ALLOWED_SHARED_SERVICES,
    META_STRATEGY_FORBIDDEN_PRIVATE_STATE,
    META_STRATEGY_OWNED_CAPABILITIES,
)
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_CONTRACT_VERSION,
    META_STRATEGY_MANDATORY_VERSION_FIELDS,
    META_STRATEGY_PACKAGE_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
    meta_strategy_version_identifiers,
)


class MetaStrategyContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True, allow_inf_nan=False)

    def deterministic_json(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def deterministic_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def numeric_values_must_be_finite(self) -> MetaStrategyContractModel:
        _reject_non_finite_numbers(self.model_dump(mode="python"))
        return self


class MetaStrategyVersionContract(MetaStrategyContractModel):
    algorithmVersion: str
    strategyCatalogVersion: str
    featureSchemaVersion: str
    labelSpecificationVersion: str
    modelVersion: str
    modelArtifactVersion: str
    configurationVersion: str
    dynamicProfileVersion: str
    positionSizingVersion: str
    exitPolicyVersion: str
    backtestEngineVersion: str

    @field_validator(*META_STRATEGY_MANDATORY_VERSION_FIELDS)
    @classmethod
    def mandatory_versions_are_non_empty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("mandatory version identifier cannot be empty")
        return normalized


class MetaStrategyBoundaryManifest(MetaStrategyContractModel):
    contractVersion: str = META_STRATEGY_CONTRACT_VERSION
    packageVersion: str = META_STRATEGY_PACKAGE_VERSION
    algorithmId: Literal["meta_strategy"] = ALGORITHM_ID
    algorithmName: Literal["Meta-Strategy"] = ALGORITHM_NAME
    versions: MetaStrategyVersionContract
    ownedCapabilities: tuple[str, ...] = Field(default=META_STRATEGY_OWNED_CAPABILITIES)
    allowedSharedServices: tuple[str, ...] = Field(default=META_STRATEGY_ALLOWED_SHARED_SERVICES)
    forbiddenPrivateState: tuple[str, ...] = Field(default=META_STRATEGY_FORBIDDEN_PRIVATE_STATE)
    productionBehaviorChanged: bool = False
    explanation: str = "Passive Meta-Strategy package boundary manifest; no runtime behavior is activated by this contract."


class MetaStrategyPersistedResultEnvelope(MetaStrategyContractModel):
    contractVersion: str = META_STRATEGY_CONTRACT_VERSION
    algorithmId: Literal["meta_strategy"] = ALGORITHM_ID
    resultType: str
    versions: MetaStrategyVersionContract
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("resultType")
    @classmethod
    def result_type_is_non_empty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("persisted result type cannot be empty")
        return normalized


class MetaStrategyDecisionContract(MetaStrategyContractModel):
    algorithm_id: Literal["meta_strategy"]
    algorithm_version: Literal["meta_strategy_algorithm_v1"]
    configuration_version: Literal["meta_strategy_config_v1"]
    strategy_catalog_version: Literal["meta_strategy_strategy_catalog_v1"]
    decision_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    timestamp: datetime

    @field_validator("decision_id", "snapshot_id")
    @classmethod
    def identifiers_are_non_empty(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("identifier cannot be empty")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class MetaStrategyMarketSnapshot(MetaStrategyDecisionContract):
    symbol: str = Field(min_length=1)
    last_price: float = Field(gt=0)
    bid_price: float | None = Field(default=None, gt=0)
    ask_price: float | None = Field(default=None, gt=0)
    spread_bps: float | None = Field(default=None, ge=0)
    volume: float = Field(ge=0)
    source_cutoff_timestamp: datetime | None = None
    point_in_time: bool = True
    candles: dict[str, tuple[dict[str, Any], ...]] = Field(default_factory=dict)
    quote: dict[str, Any] | None = None
    vwap: float | None = Field(default=None, gt=0)
    moving_averages: dict[str, Any] = Field(default_factory=dict)
    atr: dict[str, float | None] = Field(default_factory=dict)
    adx: dict[str, float | None] = Field(default_factory=dict)
    rsi: dict[str, float | None] = Field(default_factory=dict)
    macd: dict[str, dict[str, float] | None] = Field(default_factory=dict)
    bollinger_bands: dict[str, dict[str, float] | None] = Field(default_factory=dict)
    relative_volume: dict[str, float | None] = Field(default_factory=dict)
    spread: dict[str, Any] = Field(default_factory=dict)
    liquidity: dict[str, Any] = Field(default_factory=dict)
    session_phase: str = "unknown"
    gap_state: dict[str, Any] = Field(default_factory=dict)
    qqq_iwm_context: dict[str, Any] = Field(default_factory=dict)
    breadth: dict[str, Any] = Field(default_factory=dict)
    economic_event_state: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_cutoff_timestamp")
    @classmethod
    def cutoff_timestamp_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_cutoff_timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def market_snapshot_must_be_point_in_time(self) -> MetaStrategyMarketSnapshot:
        if self.source_cutoff_timestamp is not None and self.source_cutoff_timestamp > self.timestamp:
            raise ValueError("source_cutoff_timestamp cannot be after decision timestamp")
        if self.bid_price is not None and self.ask_price is not None and self.ask_price < self.bid_price:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class StrategyEvaluation(MetaStrategyDecisionContract):
    strategy_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    signal: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    eligible: bool
    features: dict[str, Any] = Field(default_factory=dict)


class ContextEvaluation(MetaStrategyDecisionContract):
    context_id: str = Field(min_length=1)
    effect: Literal["confirm_long", "confirm_short", "neutral", "block"]
    confidence: float = Field(ge=0, le=1)
    data_ready: bool
    features: dict[str, Any] = Field(default_factory=dict)


class RegimeEvaluation(MetaStrategyDecisionContract):
    regime_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: Literal[-1, 0, 1]
    volatility: Literal["LOW", "NORMAL", "HIGH", "EXTREME"]
    confidence: float = Field(ge=0, le=1)
    features: dict[str, Any] = Field(default_factory=dict)


class SafetyEvaluation(MetaStrategyDecisionContract):
    status: Literal["PASS", "CAUTION", "FAIL", "INFO"]
    eligible: bool
    risk_multiplier: float = Field(ge=0, le=1)
    failed_gates: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class FamilyScore(MetaStrategyDecisionContract):
    family: str = Field(min_length=1)
    buy_score: float = Field(ge=0, le=1)
    sell_score: float = Field(ge=0, le=1)
    hold_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)


class DeterministicCandidate(MetaStrategyDecisionContract):
    signal: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=1)
    eligible: bool
    family_scores: tuple[FamilyScore, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CandidateGeometry(MetaStrategyDecisionContract):
    candidate_id: str = Field(min_length=1)
    side: Literal["BUY", "SELL", "HOLD"]
    entry_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    quantity: float = Field(ge=0)
    risk_reward: float | None = Field(default=None, ge=0)


class MetaFeatureSet(MetaStrategyDecisionContract):
    feature_schema_version: str = Field(min_length=1)
    feature_schema_hash: str = Field(min_length=1)
    feature_count: int = Field(ge=0)
    features: dict[str, Any] = Field(default_factory=dict)


class MetaLabel(MetaStrategyDecisionContract):
    label_specification_version: str = Field(min_length=1)
    label: Literal["BUY", "SELL", "HOLD"]
    outcome: Literal["WIN", "LOSS", "TIMEOUT", "NO_TRADE"]
    return_r: float = Field(ge=-1000, le=1000)
    barrier_minutes: int = Field(ge=0)


class ModelArtifactManifest(MetaStrategyDecisionContract):
    model_version: str = Field(min_length=1)
    model_artifact_version: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    feature_schema_hash: str = Field(min_length=1)
    label_specification_version: str = Field(min_length=1)
    trained_rows: int = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class ModelPrediction(MetaStrategyDecisionContract):
    model_version: str = Field(min_length=1)
    model_artifact_version: str = Field(min_length=1)
    probabilities: dict[str, float]
    predicted_label: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=1)
    ood_score: float = Field(ge=0, le=1)

    @field_validator("probabilities")
    @classmethod
    def probabilities_are_valid(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("probabilities cannot be empty")
        for label, probability in value.items():
            if not label.strip():
                raise ValueError("probability label cannot be empty")
            if not math.isfinite(float(probability)) or float(probability) < 0.0 or float(probability) > 1.0:
                raise ValueError("probabilities must be finite values between 0 and 1")
        return value


class MetaDecision(MetaStrategyDecisionContract):
    final_signal: Literal["BUY", "SELL", "HOLD"]
    status: Literal["ACCEPTED", "REJECTED", "HOLD_DIAGNOSTIC", "FALLBACK_ACCEPTED"]
    confidence: float = Field(ge=0, le=1)
    risk_multiplier: float = Field(ge=0, le=1)
    reason_codes: tuple[str, ...] = ()


class EffectiveMetaProfile(MetaStrategyDecisionContract):
    dynamic_profile_version: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    operating_mode: Literal["OFF", "SHADOW", "FILTER", "ACTIVE", "FALLBACK"]
    max_risk_multiplier: float = Field(ge=0, le=1)
    settings: dict[str, Any] = Field(default_factory=dict)


class MetaSizingResult(MetaStrategyDecisionContract):
    position_sizing_version: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    notional: float = Field(ge=0)
    risk_dollars: float = Field(ge=0)
    risk_multiplier: float = Field(ge=0, le=1)


class MetaOrderIntent(MetaStrategyDecisionContract):
    order_intent_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    time_in_force: str = Field(min_length=1)


class TradeManagementResult(MetaStrategyDecisionContract):
    exit_policy_version: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    action: Literal["HOLD", "EXIT", "REDUCE", "MOVE_STOP"]
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    realized_r: float | None = Field(default=None, ge=-1000, le=1000)
    reason_codes: tuple[str, ...] = ()


class PromotionEvidence(MetaStrategyDecisionContract):
    candidate_model_version: str = Field(min_length=1)
    promoted: bool
    sample_size: int = Field(ge=0)
    net_expectancy: float = Field(ge=-1000, le=1000)
    max_drawdown: float = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class PaperStabilityEvidence(MetaStrategyDecisionContract):
    stable: bool
    paper_sessions: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    rejection_rate: float = Field(ge=0, le=1)
    max_drawdown: float = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class MetaBacktestResult(MetaStrategyDecisionContract):
    backtest_engine_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    start_timestamp: datetime
    end_timestamp: datetime
    trade_count: int = Field(ge=0)
    net_pnl: float = Field(ge=-1_000_000_000, le=1_000_000_000)
    max_drawdown: float = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("start_timestamp", "end_timestamp")
    @classmethod
    def backtest_timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def backtest_end_must_not_precede_start(self) -> MetaBacktestResult:
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp must be greater than or equal to start_timestamp")
        return self


def meta_strategy_version_contract() -> MetaStrategyVersionContract:
    return MetaStrategyVersionContract(**meta_strategy_version_identifiers())


def meta_strategy_boundary_manifest() -> MetaStrategyBoundaryManifest:
    return MetaStrategyBoundaryManifest(versions=meta_strategy_version_contract())


def meta_strategy_persisted_result_envelope(
    *,
    result_type: str,
    payload: Mapping[str, Any] | None = None,
    versions: MetaStrategyVersionContract | None = None,
) -> MetaStrategyPersistedResultEnvelope:
    return MetaStrategyPersistedResultEnvelope(
        resultType=result_type,
        versions=versions or meta_strategy_version_contract(),
        payload=dict(payload or {}),
    )


def meta_strategy_version_compatibility(
    candidate_versions: Mapping[str, Any] | MetaStrategyVersionContract | MetaStrategyPersistedResultEnvelope,
    *,
    expected_versions: MetaStrategyVersionContract | None = None,
) -> dict[str, Any]:
    expected = (expected_versions or meta_strategy_version_contract()).model_dump(mode="json")
    candidate = _version_payload(candidate_versions)
    missing = tuple(field for field in META_STRATEGY_MANDATORY_VERSION_FIELDS if not candidate.get(field))
    mismatched = tuple(
        field
        for field in META_STRATEGY_MANDATORY_VERSION_FIELDS
        if field not in missing and str(candidate.get(field)) != str(expected[field])
    )
    return {
        "algorithmId": ALGORITHM_ID,
        "contractVersion": META_STRATEGY_CONTRACT_VERSION,
        "valid": not missing and not mismatched,
        "missing": missing,
        "mismatched": mismatched,
        "expectedVersions": expected,
        "candidateVersions": candidate,
        "reasonCodes": (
            "meta_strategy.versions.compatible" if not missing and not mismatched else "meta_strategy.versions.incompatible",
        ),
    }


def meta_strategy_contract_inventory() -> dict[str, Any]:
    manifest = meta_strategy_boundary_manifest()
    return {
        **manifest.model_dump(mode="json"),
        "contractHash": manifest.deterministic_hash()[:16],
        "reasonCodes": ("meta_strategy.contracts.boundary_manifest_ready",),
    }


def _version_payload(candidate: Mapping[str, Any] | MetaStrategyVersionContract | MetaStrategyPersistedResultEnvelope) -> dict[str, Any]:
    if isinstance(candidate, MetaStrategyPersistedResultEnvelope):
        return candidate.versions.model_dump(mode="json")
    if isinstance(candidate, MetaStrategyVersionContract):
        return candidate.model_dump(mode="json")
    if isinstance(candidate, Mapping) and isinstance(candidate.get("versions"), Mapping):
        return dict(candidate["versions"])
    return dict(candidate)


def _reject_non_finite_numbers(value: Any) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError("numeric values must be finite")
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_non_finite_numbers(nested)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            _reject_non_finite_numbers(nested)
