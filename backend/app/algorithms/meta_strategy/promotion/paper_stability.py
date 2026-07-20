"""Paper-stability validation for Meta-Strategy promotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, Mapping

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.models import artifact_hash


@dataclass(frozen=True)
class MetaStrategyPaperStabilityConfig:
    minimum_paper_days: int = 3
    minimum_shadow_decisions: int = 100
    minimum_eligible_candidates: int = 20
    minimum_completed_trades: int = 10
    minimum_buy_coverage: int = 1
    minimum_sell_coverage: int = 1
    minimum_distinct_regimes: int = 2
    maximum_calibration_drift: float = 0.05
    maximum_feature_drift: float = 0.10
    maximum_ood_rate: float = 0.10
    maximum_risk_violations: int = 0
    maximum_reconciliation_failures: int = 0
    maximum_operational_errors: int = 0


@dataclass(frozen=True)
class MetaStrategyPaperStabilityEvidence:
    algorithm_id: str
    artifact_id: str
    artifact_hash: str
    stable: bool
    paper_days: int
    shadow_decisions: int
    eligible_candidates: int
    completed_trades: int
    buy_count: int
    sell_count: int
    distinct_regimes: int
    calibration_drift: float
    feature_drift: float
    ood_rate: float
    risk_violations: int
    reconciliation_failures: int
    operational_errors: int
    first_observed_at: datetime
    last_observed_at: datetime
    generated_at: datetime
    metrics: Mapping[str, Any]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.algorithm_id != ALGORITHM_ID:
            raise ValueError("paper-stability evidence must be attributed to meta_strategy")
        for timestamp in (self.first_observed_at, self.last_observed_at, self.generated_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("paper-stability timestamps must be timezone-aware")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


def validate_meta_strategy_paper_stability(
    *,
    candidate_artifact: Mapping[str, Any],
    observations: tuple[Mapping[str, Any], ...],
    config: MetaStrategyPaperStabilityConfig | None = None,
    generated_at: datetime | None = None,
) -> MetaStrategyPaperStabilityEvidence:
    config = config or MetaStrategyPaperStabilityConfig()
    generated = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
    artifact_id = str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or "")
    if not artifact_id:
        raise ValueError("candidate artifact must include artifactId")
    expected_hash = artifact_hash(dict(candidate_artifact))
    supplied_hash = str(candidate_artifact.get("artifactHash") or expected_hash)
    if supplied_hash != expected_hash:
        raise ValueError("candidate artifact hash mismatch")
    matching_observations = tuple(observation for observation in observations if _matches_artifact(observation, artifact_id, expected_hash))
    first_observed_at, last_observed_at = _observation_window(matching_observations, generated)
    paper_days = len({_session_date(observation) for observation in matching_observations})
    shadow_decisions = sum(_int(observation, "shadowDecisions", "shadow_decisions") for observation in matching_observations)
    eligible_candidates = sum(_int(observation, "eligibleCandidates", "eligible_candidates") for observation in matching_observations)
    completed_trades = sum(_int(observation, "completedTrades", "completed_trades") for observation in matching_observations)
    buy_count = sum(_int(observation, "buyCount", "buy_count", "buyTrades") for observation in matching_observations)
    sell_count = sum(_int(observation, "sellCount", "sell_count", "sellTrades") for observation in matching_observations)
    regimes = {
        regime
        for observation in matching_observations
        for regime in _regime_values(observation)
    }
    calibration_drift = max((_float(observation, "calibrationDrift", "calibration_drift") for observation in matching_observations), default=0.0)
    feature_drift = max((_float(observation, "featureDrift", "feature_drift") for observation in matching_observations), default=0.0)
    ood_rate = max((_float(observation, "oodRate", "ood_rate") for observation in matching_observations), default=0.0)
    risk_violations = sum(_int(observation, "riskViolations", "risk_violations") for observation in matching_observations)
    reconciliation_failures = sum(_int(observation, "reconciliationFailures", "reconciliation_failures") for observation in matching_observations)
    operational_errors = sum(_int(observation, "operationalErrors", "operational_errors") for observation in matching_observations)
    reason_codes = _reason_codes(
        paper_days=paper_days,
        shadow_decisions=shadow_decisions,
        eligible_candidates=eligible_candidates,
        completed_trades=completed_trades,
        buy_count=buy_count,
        sell_count=sell_count,
        distinct_regimes=len(regimes),
        calibration_drift=calibration_drift,
        feature_drift=feature_drift,
        ood_rate=ood_rate,
        risk_violations=risk_violations,
        reconciliation_failures=reconciliation_failures,
        operational_errors=operational_errors,
        matched_observation_count=len(matching_observations),
        config=config,
    )
    stable = not reason_codes
    final_reasons = ("meta_strategy.paper_stability.stable",) if stable else tuple(dict.fromkeys(("meta_strategy.paper_stability.fail_closed", *reason_codes)))
    metrics = {
        "paperDays": paper_days,
        "shadowDecisions": shadow_decisions,
        "eligibleCandidates": eligible_candidates,
        "completedTrades": completed_trades,
        "buyCount": buy_count,
        "sellCount": sell_count,
        "distinctRegimes": len(regimes),
        "calibrationDrift": calibration_drift,
        "featureDrift": feature_drift,
        "oodRate": ood_rate,
        "riskViolations": risk_violations,
        "reconciliationFailures": reconciliation_failures,
        "operationalErrors": operational_errors,
        "matchedObservationCount": len(matching_observations),
    }
    return MetaStrategyPaperStabilityEvidence(
        algorithm_id=ALGORITHM_ID,
        artifact_id=artifact_id,
        artifact_hash=expected_hash,
        stable=stable,
        paper_days=paper_days,
        shadow_decisions=shadow_decisions,
        eligible_candidates=eligible_candidates,
        completed_trades=completed_trades,
        buy_count=buy_count,
        sell_count=sell_count,
        distinct_regimes=len(regimes),
        calibration_drift=calibration_drift,
        feature_drift=feature_drift,
        ood_rate=ood_rate,
        risk_violations=risk_violations,
        reconciliation_failures=reconciliation_failures,
        operational_errors=operational_errors,
        first_observed_at=first_observed_at,
        last_observed_at=last_observed_at,
        generated_at=generated,
        metrics=metrics,
        reason_codes=final_reasons,
    )


def paper_stability_matches_candidate_artifact(evidence: MetaStrategyPaperStabilityEvidence, candidate_artifact: Mapping[str, Any]) -> bool:
    artifact_id = str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or "")
    return artifact_id == evidence.artifact_id and artifact_hash(dict(candidate_artifact)) == evidence.artifact_hash


def _reason_codes(
    *,
    paper_days: int,
    shadow_decisions: int,
    eligible_candidates: int,
    completed_trades: int,
    buy_count: int,
    sell_count: int,
    distinct_regimes: int,
    calibration_drift: float,
    feature_drift: float,
    ood_rate: float,
    risk_violations: int,
    reconciliation_failures: int,
    operational_errors: int,
    matched_observation_count: int,
    config: MetaStrategyPaperStabilityConfig,
) -> tuple[str, ...]:
    reasons = []
    if matched_observation_count == 0:
        reasons.append("meta_strategy.paper_stability.no_matching_artifact_observations")
    if paper_days < config.minimum_paper_days:
        reasons.append("meta_strategy.paper_stability.insufficient_paper_days")
    if shadow_decisions < config.minimum_shadow_decisions:
        reasons.append("meta_strategy.paper_stability.insufficient_shadow_decisions")
    if eligible_candidates < config.minimum_eligible_candidates:
        reasons.append("meta_strategy.paper_stability.insufficient_eligible_candidates")
    if completed_trades < config.minimum_completed_trades:
        reasons.append("meta_strategy.paper_stability.insufficient_completed_trades")
    if buy_count < config.minimum_buy_coverage:
        reasons.append("meta_strategy.paper_stability.buy_coverage_missing")
    if sell_count < config.minimum_sell_coverage:
        reasons.append("meta_strategy.paper_stability.sell_coverage_missing")
    if distinct_regimes < config.minimum_distinct_regimes:
        reasons.append("meta_strategy.paper_stability.regime_coverage_missing")
    if calibration_drift > config.maximum_calibration_drift:
        reasons.append("meta_strategy.paper_stability.calibration_unstable")
    if feature_drift > config.maximum_feature_drift:
        reasons.append("meta_strategy.paper_stability.feature_drift_too_high")
    if ood_rate > config.maximum_ood_rate:
        reasons.append("meta_strategy.paper_stability.ood_rate_too_high")
    if risk_violations > config.maximum_risk_violations:
        reasons.append("meta_strategy.paper_stability.risk_violations")
    if reconciliation_failures > config.maximum_reconciliation_failures:
        reasons.append("meta_strategy.paper_stability.reconciliation_failures")
    if operational_errors > config.maximum_operational_errors:
        reasons.append("meta_strategy.paper_stability.operational_errors")
    return tuple(reasons)


def _matches_artifact(observation: Mapping[str, Any], artifact_id: str, candidate_hash: str) -> bool:
    observed_id = str(observation.get("artifactId") or observation.get("artifact_id") or "")
    observed_hash = str(observation.get("artifactHash") or observation.get("artifact_hash") or "")
    return observed_id == artifact_id and observed_hash == candidate_hash


def _observation_window(observations: tuple[Mapping[str, Any], ...], fallback: datetime) -> tuple[datetime, datetime]:
    timestamps = sorted(_timestamp(observation) for observation in observations)
    if not timestamps:
        return fallback, fallback
    return timestamps[0], timestamps[-1]


def _session_date(observation: Mapping[str, Any]) -> date:
    value = observation.get("sessionDate") or observation.get("session_date")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return _timestamp(observation).date()


def _timestamp(observation: Mapping[str, Any]) -> datetime:
    value = observation.get("timestamp") or observation.get("observedAt") or observation.get("observed_at")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(tz=UTC)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _regime_values(observation: Mapping[str, Any]) -> tuple[str, ...]:
    value = observation.get("regimes") or observation.get("distinctRegimes") or observation.get("regime")
    if isinstance(value, Mapping):
        return tuple(str(key) for key, present in value.items() if present)
    if isinstance(value, list | tuple | set):
        return tuple(str(item) for item in value)
    if value:
        return (str(value),)
    return ()


def _int(payload: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        if key in payload and payload[key] is not None:
            return int(payload[key])
    return 0


def _float(payload: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return 0.0


__all__ = [
    "MetaStrategyPaperStabilityConfig",
    "MetaStrategyPaperStabilityEvidence",
    "paper_stability_matches_candidate_artifact",
    "validate_meta_strategy_paper_stability",
]
