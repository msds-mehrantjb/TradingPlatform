from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.config import ApplicationConfig, FeatureFlags
from backend.app.domain.models import DomainModel, Signal, _require_utc
from backend.app.ensemble.diagnostics import (
    StrategyDiversityDiagnosticsReport,
    strategy_diversity_diagnostics,
)
from backend.app.strategies.registry import directional_strategy_input_ids

from .event_replay import ReplayDecisionSnapshot, ReplayResult


SHADOW_COMPARISON_VERSION = "historical_shadow_comparison_v1"
V1_SHADOW_NAMESPACE = "voting_ensemble_v1_reference"
V2_SHADOW_NAMESPACE = "family_ensemble_v2_shadow"
FORBIDDEN_V2_PROXY_NAMES = frozenset(
    {
        "Ensemble Strategy Voting",
        "strategyVoteCatalog",
        "strategyVote",
        "Failed Breakout Strategy",
        "Bollinger Band Reversion",
        "ATR Overextension Reversion",
        "Economic Event Reaction Strategy",
        "VWAP Position Strategy",
        "ADX Trend Strength Filter",
    }
)


class HistoricalShadowFeatureFlags(DomainModel):
    strategyEngineV2Enabled: bool = True
    familyEnsembleV2Enabled: bool = True
    metaModelV2Enabled: bool = False
    dynamicTradingPolicyEnabled: bool = False
    globalGateEngineEnabled: bool = False
    orderBehavior: Literal["V1_OR_DISABLED"] = "V1_OR_DISABLED"
    paperOrderSubmissionEnabled: bool = False
    configurationVersion: str = "historical_shadow_feature_flags_v1"
    configurationHash: str

    @model_validator(mode="after")
    def enforce_shadow_posture(self) -> "HistoricalShadowFeatureFlags":
        if not self.strategyEngineV2Enabled or not self.familyEnsembleV2Enabled:
            raise ValueError("historical shadow comparison requires strategyEngineV2Enabled and familyEnsembleV2Enabled")
        if self.metaModelV2Enabled or self.dynamicTradingPolicyEnabled:
            raise ValueError("historical shadow comparison cannot enable V2 ML or dynamic trading policy")
        if self.paperOrderSubmissionEnabled:
            raise ValueError("historical shadow comparison cannot enable V2 paper-order submission")
        return self


class SnapshotStorageSeparation(DomainModel):
    v1Namespace: str = V1_SHADOW_NAMESPACE
    v2Namespace: str = V2_SHADOW_NAMESPACE
    v1TrainingCompatibleWithV2: bool = False
    v2TrainingEligible: bool = False
    explanation: str

    @model_validator(mode="after")
    def namespaces_must_be_separate(self) -> "SnapshotStorageSeparation":
        if self.v1Namespace == self.v2Namespace:
            raise ValueError("V1 and V2 shadow snapshots must use separate storage namespaces")
        if self.v1TrainingCompatibleWithV2:
            raise ValueError("V1 shadow data cannot be training-compatible with V2")
        return self


class V1ShadowDecision(DomainModel):
    snapshotId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    sessionDate: date
    signal: Signal
    tradeOpened: bool = False
    expectedValue: float | None = None
    drawdown: float = Field(default=0.0, ge=0.0)
    strategyProxyMappings: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @field_validator("decisionTimestampUtc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ShadowDecisionComparison(DomainModel):
    decisionTimestampUtc: datetime
    v1SnapshotId: str
    v2SnapshotId: str
    v1Signal: Signal
    v2Signal: Signal
    signalChanged: bool
    v1TradeOpened: bool
    v2CandidateDetected: bool
    tradeCountDelta: int
    expectedValueDelta: float | None
    drawdownDelta: float | None
    dataReady: bool
    dataReadinessFailures: list[str]
    explanation: str

    @field_validator("decisionTimestampUtc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ShadowComparisonAggregates(DomainModel):
    decisionCount: int = Field(ge=0)
    signalDifferenceCount: int = Field(ge=0)
    v1TradeCount: int = Field(ge=0)
    v2CandidateCount: int = Field(ge=0)
    tradeCountDifference: int
    averageExpectedValueDifference: float | None
    maxDrawdownDifference: float | None
    dataReadinessFailureCount: int = Field(ge=0)
    explanation: str


class HistoricalShadowComparisonReport(DomainModel):
    version: str = SHADOW_COMPARISON_VERSION
    generatedAt: datetime
    symbol: str
    sessionDate: date
    featureFlags: HistoricalShadowFeatureFlags
    storage: SnapshotStorageSeparation
    recordedV2SnapshotIds: list[str]
    v2ShadowSnapshots: list[dict[str, Any]]
    decisionComparisons: list[ShadowDecisionComparison]
    aggregates: ShadowComparisonAggregates
    familyCoverage: dict[str, int]
    strategyCorrelation: StrategyDiversityDiagnosticsReport | None
    expectedValueDifferences: list[float]
    drawdownDifferences: list[float]
    proxyStrategyViolations: list[str]
    v2MlTrainingAllowed: bool
    cleanV2SnapshotCount: int = Field(ge=0)
    minimumCleanV2SnapshotsForMl: int = Field(ge=0)
    mlTrainingBlockReason: str
    reasonCodes: list[str]
    explanation: str

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def shadow_report_must_not_train_too_early(self) -> "HistoricalShadowComparisonReport":
        if self.cleanV2SnapshotCount < self.minimumCleanV2SnapshotsForMl and self.v2MlTrainingAllowed:
            raise ValueError("V2 ML training cannot be allowed before enough clean V2 snapshots exist")
        if self.proxyStrategyViolations:
            raise ValueError("V2 shadow comparison contains old proxy or aggregator strategy outputs")
        return self


def historical_shadow_application_config() -> ApplicationConfig:
    return ApplicationConfig(
        version="application-config-v1-historical-shadow",
        featureFlags=FeatureFlags(
            strategyEngineV2Enabled=True,
            familyEnsembleV2Enabled=True,
            metaModelV2Enabled=False,
            dynamicTradingPolicyEnabled=False,
            globalGateEngineEnabled=False,
        ),
    )


def historical_shadow_feature_flags() -> HistoricalShadowFeatureFlags:
    payload = historical_shadow_application_config().as_dict()
    return HistoricalShadowFeatureFlags(
        strategyEngineV2Enabled=True,
        familyEnsembleV2Enabled=True,
        metaModelV2Enabled=False,
        dynamicTradingPolicyEnabled=False,
        globalGateEngineEnabled=False,
        orderBehavior="V1_OR_DISABLED",
        paperOrderSubmissionEnabled=False,
        configurationHash=str(payload["configurationHash"]),
    )


def build_historical_shadow_comparison(
    *,
    v1Decisions: list[V1ShadowDecision] | list[dict[str, Any]],
    v2Replay: ReplayResult | dict[str, Any],
    generatedAt: datetime | None = None,
    minimumCleanV2SnapshotsForMl: int = 500,
) -> HistoricalShadowComparisonReport:
    v1_rows = [row if isinstance(row, V1ShadowDecision) else V1ShadowDecision(**row) for row in v1Decisions]
    replay = v2Replay if isinstance(v2Replay, ReplayResult) else ReplayResult(**v2Replay)
    generated = generatedAt or datetime.now(UTC)
    v2_snapshots = [_shadow_snapshot(snapshot) for snapshot in replay.snapshots]
    paired = _paired_decisions(v1_rows, replay.snapshots)
    comparisons = [_comparison(v1, v2) for v1, v2 in paired]
    family_coverage = _family_coverage(replay.snapshots)
    expected_differences = [row.expectedValueDelta for row in comparisons if row.expectedValueDelta is not None]
    drawdown_differences = [row.drawdownDelta for row in comparisons if row.drawdownDelta is not None]
    clean_count = sum(1 for snapshot in replay.snapshots if _snapshot_data_ready(snapshot) and not _snapshot_proxy_violations(snapshot))
    training_allowed = clean_count >= minimumCleanV2SnapshotsForMl
    proxy_violations = sorted({violation for snapshot in replay.snapshots for violation in _snapshot_proxy_violations(snapshot)})
    correlation = _strategy_correlation_report(replay.snapshots, generated)
    aggregates = _aggregates(comparisons, expected_differences, drawdown_differences)

    return HistoricalShadowComparisonReport(
        generatedAt=generated,
        symbol=replay.symbol,
        sessionDate=replay.sessionDate,
        featureFlags=historical_shadow_feature_flags(),
        storage=SnapshotStorageSeparation(
            explanation=(
                "V1 reference decisions and V2 shadow snapshots are stored in separate namespaces. "
                "V1 rows remain incompatible with V2 training."
            ),
        ),
        recordedV2SnapshotIds=[snapshot.snapshotId for snapshot in replay.snapshots],
        v2ShadowSnapshots=v2_snapshots,
        decisionComparisons=comparisons,
        aggregates=aggregates,
        familyCoverage=family_coverage,
        strategyCorrelation=correlation,
        expectedValueDifferences=[round(value, 6) for value in expected_differences],
        drawdownDifferences=[round(value, 6) for value in drawdown_differences],
        proxyStrategyViolations=proxy_violations,
        v2MlTrainingAllowed=training_allowed,
        cleanV2SnapshotCount=clean_count,
        minimumCleanV2SnapshotsForMl=minimumCleanV2SnapshotsForMl,
        mlTrainingBlockReason=(
            "enough_clean_v2_shadow_snapshots"
            if training_allowed
            else "v2_ml_training_blocked_until_enough_clean_shadow_snapshots_exist"
        ),
        reasonCodes=[
            "v2_shadow_decisions_recorded_only",
            "v1_v2_storage_separated",
            "v2_order_submission_disabled",
            "v2_ml_training_deferred",
        ],
        explanation=(
            "Historical shadow comparison pairs V1 reference decisions with V2 family-aware decisions by timestamp. "
            "V2 decisions are recorded for analysis only and do not affect paper orders."
        ),
    )


def _paired_decisions(
    v1_rows: list[V1ShadowDecision],
    v2_snapshots: list[ReplayDecisionSnapshot],
) -> list[tuple[V1ShadowDecision, ReplayDecisionSnapshot]]:
    v1_by_timestamp = {row.decisionTimestampUtc: row for row in v1_rows}
    paired: list[tuple[V1ShadowDecision, ReplayDecisionSnapshot]] = []
    for snapshot in v2_snapshots:
        v1 = v1_by_timestamp.get(snapshot.decisionTimestampUtc)
        if v1 is not None:
            paired.append((v1, snapshot))
    return paired


def _comparison(v1: V1ShadowDecision, v2: ReplayDecisionSnapshot) -> ShadowDecisionComparison:
    v2_signal = Signal(v2.ensembleDecision.get("signal", Signal.HOLD.value))
    v2_candidate = bool(v2.deterministicCandidate and v2_signal != Signal.HOLD.value)
    v2_expected_value = _v2_expected_value(v2)
    expected_delta = None if v1.expectedValue is None or v2_expected_value is None else v2_expected_value - v1.expectedValue
    v2_drawdown = _v2_drawdown(v2)
    drawdown_delta = None if v2_drawdown is None else v2_drawdown - v1.drawdown
    failures = _data_readiness_failures(v2)
    evidence = _evidence_summary(v2)
    return ShadowDecisionComparison(
        decisionTimestampUtc=v2.decisionTimestampUtc,
        v1SnapshotId=v1.snapshotId,
        v2SnapshotId=v2.snapshotId,
        v1Signal=v1.signal,
        v2Signal=v2_signal,
        signalChanged=Signal(v1.signal) != v2_signal,
        v1TradeOpened=v1.tradeOpened,
        v2CandidateDetected=v2_candidate,
        tradeCountDelta=int(v2_candidate) - int(v1.tradeOpened),
        expectedValueDelta=expected_delta,
        drawdownDelta=drawdown_delta,
        dataReady=not failures,
        dataReadinessFailures=failures,
        explanation=(
            f"V1 {Signal(v1.signal).value} versus V2 {v2_signal.value}. "
            f"V2 evidence: {evidence}."
        ),
    )


def _aggregates(
    comparisons: list[ShadowDecisionComparison],
    expected_differences: list[float],
    drawdown_differences: list[float],
) -> ShadowComparisonAggregates:
    v1_trade_count = sum(1 for row in comparisons if row.v1TradeOpened)
    v2_candidate_count = sum(1 for row in comparisons if row.v2CandidateDetected)
    data_failures = sum(len(row.dataReadinessFailures) for row in comparisons)
    return ShadowComparisonAggregates(
        decisionCount=len(comparisons),
        signalDifferenceCount=sum(1 for row in comparisons if row.signalChanged),
        v1TradeCount=v1_trade_count,
        v2CandidateCount=v2_candidate_count,
        tradeCountDifference=v2_candidate_count - v1_trade_count,
        averageExpectedValueDifference=(
            round(sum(expected_differences) / len(expected_differences), 6) if expected_differences else None
        ),
        maxDrawdownDifference=(round(max(drawdown_differences), 6) if drawdown_differences else None),
        dataReadinessFailureCount=data_failures,
        explanation="Aggregate V1/V2 shadow differences over paired historical decision timestamps.",
    )


def _shadow_snapshot(snapshot: ReplayDecisionSnapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["shadowNamespace"] = V2_SHADOW_NAMESPACE
    payload["orderBehavior"] = "DISABLED"
    payload["paperOrderSubmissionEnabled"] = False
    payload["fill"] = None
    payload["exit"] = None
    payload["reasonCodes"] = sorted(set([*snapshot.reasonCodes, "shadow.v2_record_only", "shadow.order_submission_disabled"]))
    return payload


def _family_coverage(snapshots: list[ReplayDecisionSnapshot]) -> dict[str, int]:
    coverage: Counter[str] = Counter()
    for snapshot in snapshots:
        for signal in snapshot.strategyOutputs:
            family = str(signal.get("family") or "")
            if family and signal.get("eligible") and signal.get("dataReady"):
                coverage[family] += 1
    return dict(sorted(coverage.items()))


def _strategy_correlation_report(
    snapshots: list[ReplayDecisionSnapshot],
    generated_at: datetime,
) -> StrategyDiversityDiagnosticsReport | None:
    observations: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for signal in snapshot.strategyOutputs:
            if signal.get("strategyId") not in directional_strategy_input_ids():
                continue
            observations.append(
                {
                    "decisionKey": snapshot.snapshotId,
                    "decisionTimestamp": snapshot.decisionTimestampUtc,
                    "walkForwardFold": "historical_shadow",
                    "isOutOfSample": True,
                    "strategyId": signal.get("strategyId"),
                    "strategyName": signal.get("strategyName") or signal.get("strategyId"),
                    "family": signal.get("family"),
                    "signal": signal.get("signal", Signal.HOLD.value),
                    "direction": signal.get("direction", 0),
                    "eligible": bool(signal.get("eligible")),
                    "setupId": _setup_id(signal),
                    "outcomeR": None,
                }
            )
    if not observations:
        return None
    return strategy_diversity_diagnostics(observations, generated_at=generated_at)


def _setup_id(signal: dict[str, Any]) -> str | None:
    features = signal.get("features")
    if isinstance(features, dict):
        setup_id = features.get("setupId") or features.get("setup_id")
        if setup_id:
            return str(setup_id)
    return None


def _snapshot_proxy_violations(snapshot: ReplayDecisionSnapshot) -> list[str]:
    violations: list[str] = []
    directional_ids = set(directional_strategy_input_ids())
    for signal in snapshot.strategyOutputs:
        strategy_id = str(signal.get("strategyId") or "")
        strategy_name = str(signal.get("strategyName") or "")
        if strategy_id not in directional_ids:
            violations.append(strategy_id or strategy_name)
        if strategy_name in FORBIDDEN_V2_PROXY_NAMES or strategy_id in FORBIDDEN_V2_PROXY_NAMES:
            violations.append(strategy_name or strategy_id)
    return [item for item in violations if item]


def _snapshot_data_ready(snapshot: ReplayDecisionSnapshot) -> bool:
    feature_ready = bool(snapshot.featureSnapshot.get("dataReady", False))
    strategies_ready = all(bool(signal.get("dataReady")) for signal in snapshot.strategyOutputs)
    return feature_ready and strategies_ready


def _data_readiness_failures(snapshot: ReplayDecisionSnapshot) -> list[str]:
    failures: list[str] = []
    if not snapshot.featureSnapshot.get("dataReady", False):
        failures.extend(str(code) for code in snapshot.featureSnapshot.get("reasonCodes", []))
        if not failures:
            failures.append("feature_snapshot_not_ready")
    for signal in snapshot.strategyOutputs:
        if not signal.get("dataReady", False):
            failures.append(f"{signal.get('strategyId', 'unknown_strategy')}:data_not_ready")
    return sorted(set(failures))


def _v2_expected_value(snapshot: ReplayDecisionSnapshot) -> float | None:
    candidate = snapshot.deterministicCandidate or {}
    value = candidate.get("expectedValue")
    if value is None:
        return None
    return float(value)


def _v2_drawdown(snapshot: ReplayDecisionSnapshot) -> float | None:
    exit_payload = snapshot.exit or {}
    value = exit_payload.get("maxDrawdown") or exit_payload.get("drawdown")
    if value is None:
        return None
    return float(value)


def _evidence_summary(snapshot: ReplayDecisionSnapshot) -> str:
    ensemble = snapshot.ensembleDecision
    supporting = ", ".join(str(item) for item in ensemble.get("supportingFamilies", [])) or "no supporting families"
    opposing = ", ".join(str(item) for item in ensemble.get("opposingFamilies", [])) or "no opposing families"
    return f"score={ensemble.get('finalScore')}, supporting={supporting}, opposing={opposing}"
