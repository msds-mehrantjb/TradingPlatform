from __future__ import annotations

from backend.app.ml.features import MLFeatureSpec


VOTING_ENSEMBLE_ML_FEATURE_SCHEMA_VERSION = "voting_ensemble_ml_feature_schema_v1"
VOTING_ENSEMBLE_CANDIDATE_FEATURE_SCHEMA_HASH = "voting_ensemble_candidate_features_v1"


def ml_feature_schema_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_ML_FEATURE_SCHEMA_VERSION,
        "voting_ensemble.ml_feature_schema.dedicated_candidate_features",
        "voting_ensemble.ml_feature_schema.decision_time_only",
        "voting_ensemble.ml_feature_schema.no_outcome_or_fill_fields",
    )


def voting_ensemble_ml_feature_schema() -> tuple[MLFeatureSpec, ...]:
    return (
        MLFeatureSpec(name="dataset_version", group="candidate", valueType="categorical"),
        MLFeatureSpec(name="candidate_side", group="candidate", valueType="categorical"),
        MLFeatureSpec(name="candidate_direction", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="candidate_eligible", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="data_ready", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="deterministic_score", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="raw_score", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="confidence", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="buy_confidence", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="sell_confidence", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="hold_confidence", group="candidate", valueType="numeric"),
        MLFeatureSpec(name="eligible_strategy_count", group="family", valueType="numeric"),
        MLFeatureSpec(name="supporting_family_count", group="family", valueType="numeric"),
        MLFeatureSpec(name="opposing_family_count", group="family", valueType="numeric"),
        MLFeatureSpec(name="context_adjustment_count", group="context", valueType="numeric"),
        MLFeatureSpec(name="latest_close", group="execution", valueType="numeric"),
        MLFeatureSpec(name="latest_volume", group="execution", valueType="numeric"),
        MLFeatureSpec(name="spread_bps", group="execution", valueType="numeric"),
        MLFeatureSpec(name="realized_volatility_percentile", group="execution", valueType="numeric"),
        MLFeatureSpec(name="feature_reason_count", group="candidate", valueType="numeric"),
    )


def voting_ensemble_ml_feature_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in voting_ensemble_ml_feature_schema())

