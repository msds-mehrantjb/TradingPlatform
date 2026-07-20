"""Dedicated Meta-Strategy model implementations."""

from backend.app.algorithms.meta_strategy.models.artifact import (
    MetaStrategyRuntimeModelArtifact,
    artifact_hash,
    model_artifact_payload,
    model_library_versions,
    runtime_model_artifact_payload,
    stable_json_hash,
)
from backend.app.algorithms.meta_strategy.models.artifact_loader import (
    REQUIRED_RUNTIME_ARTIFACT_FIELDS,
    load_meta_strategy_model_artifact,
    load_meta_strategy_model_artifact_data,
    load_runtime_model_artifact,
    load_runtime_model_artifact_data,
    validate_runtime_artifact_hash,
    validate_runtime_artifact_manifest,
)
from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase
from backend.app.algorithms.meta_strategy.models.calibration import (
    apply_meta_strategy_calibration,
    tune_meta_strategy_calibration_from_oof_rows,
)
from backend.app.algorithms.meta_strategy.models.compatibility import assert_common_model_interface
from backend.app.algorithms.meta_strategy.models.logistic import LogisticRegressionChampion
from backend.app.algorithms.meta_strategy.models.optional_challengers import (
    LightGBMChallenger,
    OptionalBoosterChallenger,
    XGBoostChallenger,
    train_optional_challenger_models,
)
from backend.app.algorithms.meta_strategy.models.probability_contract import (
    CandidateConditionalModelOutput,
    CandidateConditionalProbability,
    CandidateSide,
    candidate_conditional_output,
    candidate_success_probability,
    normalize_probabilities,
)
from backend.app.algorithms.meta_strategy.models.random_forest import RandomForestChallenger

__all__ = [
    "CandidateConditionalProbability",
    "CandidateConditionalModelOutput",
    "CandidateSide",
    "LightGBMChallenger",
    "LogisticRegressionChampion",
    "MetaStrategyModelBase",
    "MetaStrategyRuntimeModelArtifact",
    "OptionalBoosterChallenger",
    "REQUIRED_RUNTIME_ARTIFACT_FIELDS",
    "RandomForestChallenger",
    "XGBoostChallenger",
    "apply_meta_strategy_calibration",
    "assert_common_model_interface",
    "artifact_hash",
    "candidate_conditional_output",
    "candidate_success_probability",
    "load_meta_strategy_model_artifact",
    "load_meta_strategy_model_artifact_data",
    "load_runtime_model_artifact",
    "load_runtime_model_artifact_data",
    "model_artifact_payload",
    "model_library_versions",
    "normalize_probabilities",
    "runtime_model_artifact_payload",
    "stable_json_hash",
    "train_optional_challenger_models",
    "tune_meta_strategy_calibration_from_oof_rows",
    "validate_runtime_artifact_hash",
    "validate_runtime_artifact_manifest",
]
