from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.domain.feature_engine import PointInTimeFeatureSnapshot
from backend.app.domain.models import EnsembleDecision
from backend.app.algorithms.voting_ensemble.ml_feature_schema import (
    VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
    VOTING_ENSEMBLE_ML_FEATURE_SCHEMA_VERSION,
    ml_feature_schema_reason_codes,
    voting_ensemble_ml_feature_names,
)
from backend.app.ml.features import MLFeatureSet


VOTING_ENSEMBLE_CANDIDATE_DATASET_VERSION = "voting_ensemble_candidate_dataset_v1"


def candidate_dataset_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_CANDIDATE_DATASET_VERSION,
        *ml_feature_schema_reason_codes(),
        "voting_ensemble.candidate_dataset.decision_time_only",
        "voting_ensemble.candidate_dataset.ensemble_features",
        "voting_ensemble.candidate_dataset.market_snapshot_features",
        "voting_ensemble.candidate_dataset.no_future_outcomes",
    )


class VotingEnsembleCandidateDatasetBuilder:
    def __call__(
        self,
        *,
        snapshotId: str,
        symbol: str,
        decisionTimestamp: datetime,
        schemaHash: str,
        featureSnapshot: PointInTimeFeatureSnapshot,
        ensembleDecision: EnsembleDecision,
    ) -> MLFeatureSet:
        values = self.feature_values(featureSnapshot, ensembleDecision)
        missing = self.missing_indicators(featureSnapshot, values)
        return MLFeatureSet(
            schemaHash=schemaHash or VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
            snapshotId=snapshotId,
            symbol=symbol.upper(),
            decisionTimestampUtc=decisionTimestamp.isoformat(),
            featureValues=values,
            missingIndicators=missing,
            forbiddenFieldsChecked=[
                "finalOutcome",
                "fillResult",
                "fills",
                "brokerSubmissionResult",
                "metaModelPrediction",
                "post_decision_timestamps",
            ],
            explanation=f"Voting Ensemble candidate dataset row built from {VOTING_ENSEMBLE_ML_FEATURE_SCHEMA_VERSION} decision-time ensemble and market features only.",
        )

    def feature_values(self, feature_snapshot: PointInTimeFeatureSnapshot, ensemble_decision: EnsembleDecision) -> dict[str, Any]:
        values = {
            "dataset_version": VOTING_ENSEMBLE_CANDIDATE_DATASET_VERSION,
            "candidate_side": _enum_value(ensemble_decision.signal),
            "candidate_direction": int(ensemble_decision.direction),
            "candidate_eligible": int(bool(ensemble_decision.eligible)),
            "data_ready": int(bool(feature_snapshot.dataReady and ensemble_decision.dataReady)),
            "deterministic_score": float(ensemble_decision.finalScore),
            "raw_score": float(ensemble_decision.rawScore),
            "confidence": float(ensemble_decision.confidence),
            "buy_confidence": float(ensemble_decision.buyConfidence),
            "sell_confidence": float(ensemble_decision.sellConfidence),
            "hold_confidence": float(ensemble_decision.holdConfidence),
            "eligible_strategy_count": int(ensemble_decision.eligibleStrategyCount),
            "supporting_family_count": len(ensemble_decision.supportingFamilies),
            "opposing_family_count": len(ensemble_decision.opposingFamilies),
            "context_adjustment_count": len(ensemble_decision.contextAdjustments),
            "latest_close": _feature_number(feature_snapshot, "spy1mClose"),
            "latest_volume": _feature_number(feature_snapshot, "spy1mVolume"),
            "spread_bps": _feature_number(feature_snapshot, "spreadBasisPoints"),
            "realized_volatility_percentile": _feature_number(feature_snapshot, "spy1mRealizedVolatilityPercentile"),
            "feature_reason_count": len(feature_snapshot.reasonCodes),
        }
        return {name: values.get(name) for name in voting_ensemble_ml_feature_names()}

    def missing_indicators(self, feature_snapshot: PointInTimeFeatureSnapshot, values: dict[str, Any]) -> dict[str, bool]:
        missing = {name: value.quality != "READY" for name, value in feature_snapshot.features.items()}
        for key, value in values.items():
            missing[key] = value is None
        return missing


def _feature_number(snapshot: PointInTimeFeatureSnapshot, name: str) -> float | None:
    feature = snapshot.features.get(name)
    if feature is None or feature.value is None:
        return None
    try:
        return float(feature.value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
