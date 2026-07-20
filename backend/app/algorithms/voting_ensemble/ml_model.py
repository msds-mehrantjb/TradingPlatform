from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.ml_feature_schema import VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH
from backend.app.algorithms.voting_ensemble.model_calibration import (
    VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
    model_calibration_reason_codes,
    voting_ensemble_model_calibration_artifact,
)
from backend.app.domain.models import OperatingMode
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceConfig


VOTING_ENSEMBLE_ML_MODEL_VERSION = "voting_ensemble_ml_model_v1"
VOTING_ENSEMBLE_ML_THRESHOLDS_VERSION = "voting_ensemble_ml_thresholds_v1"


def ml_model_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_ML_MODEL_VERSION,
        VOTING_ENSEMBLE_ML_THRESHOLDS_VERSION,
        VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
        "voting_ensemble.ml_model.schema_bound",
        "voting_ensemble.ml_model.safe_filter_only",
        "voting_ensemble.ml_thresholds.probability_gate",
        "voting_ensemble.ml_thresholds.health_and_ood_gate",
        *model_calibration_reason_codes(),
    )


def voting_ensemble_ml_config() -> SafeMLInferenceConfig:
    return SafeMLInferenceConfig(
        mode=OperatingMode.OFF,
        fallbackBehavior="NO_TRADE",
        fallbackOnModelUnavailable=True,
        fallbackOnSchemaMismatch=True,
        minSuccessProbability=0.52,
        minCalibratedProbability=0.52,
        maxFeatureMissingness=0.25,
        maxOutOfDistributionScore=0.70,
        minModelHealthScore=0.70,
        activeMinRiskCap=0.25,
        activeMaxRiskCap=1.0,
        configurationHash=voting_ensemble_ml_configuration_hash(),
    )


def voting_ensemble_ml_configuration_hash() -> str:
    return ":".join(
        (
            VOTING_ENSEMBLE_ML_MODEL_VERSION,
            VOTING_ENSEMBLE_ML_THRESHOLDS_VERSION,
            VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
            VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
        )
    )


def voting_ensemble_ml_model_artifact(artifact: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    calibrated = voting_ensemble_model_calibration_artifact(artifact) or artifact
    return {
        **calibrated,
        "algorithmId": "voting_ensemble",
        "mlModelVersion": VOTING_ENSEMBLE_ML_MODEL_VERSION,
        "expectedFeatureSchemaHash": VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH,
    }
