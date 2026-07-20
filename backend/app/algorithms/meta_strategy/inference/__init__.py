"""Dedicated Meta-Strategy inference engine."""

from backend.app.algorithms.meta_strategy.inference.artifact_health import (
    artifact_schema_compatible,
    model_health_status,
    select_champion_model,
)
from backend.app.algorithms.meta_strategy.inference.decision_policy import (
    DecisionAction,
    InferenceMode,
    MetaStrategyInferenceConfig,
    MetaStrategyInferenceResult,
    apply_decision_policy,
    bounded_risk_reduction_cap,
)
from backend.app.algorithms.meta_strategy.inference.fallback import FallbackBehavior, fallback_risk, fallback_signal
from backend.app.algorithms.meta_strategy.inference.feature_health import (
    feature_missingness_ratio,
    feature_schema_hash,
    out_of_distribution_score,
)
from backend.app.algorithms.meta_strategy.inference.predictor import (
    apply_meta_strategy_inference,
    expected_value_after_costs,
    model_probabilities,
)
from backend.app.algorithms.meta_strategy.inference.result_validation import (
    MetaStrategyInferenceValidationError,
    validate_inference_result,
)
from backend.app.algorithms.meta_strategy.inference.runtime_health import (
    RuntimeHealthCheck,
    RuntimeHealthGateResult,
    evaluate_runtime_health_gates,
)
from backend.app.algorithms.meta_strategy.inference.uncertainty import probability_uncertainty

__all__ = [
    "FallbackBehavior",
    "DecisionAction",
    "InferenceMode",
    "MetaStrategyInferenceConfig",
    "MetaStrategyInferenceResult",
    "MetaStrategyInferenceValidationError",
    "RuntimeHealthCheck",
    "RuntimeHealthGateResult",
    "apply_decision_policy",
    "apply_meta_strategy_inference",
    "artifact_schema_compatible",
    "bounded_risk_reduction_cap",
    "evaluate_runtime_health_gates",
    "expected_value_after_costs",
    "fallback_risk",
    "fallback_signal",
    "feature_missingness_ratio",
    "feature_schema_hash",
    "model_health_status",
    "model_probabilities",
    "out_of_distribution_score",
    "probability_uncertainty",
    "select_champion_model",
    "validate_inference_result",
]
