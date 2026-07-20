"""Meta-Strategy-owned feature labeling package."""

from backend.app.algorithms.meta_strategy.labeling.dataset_builder import MetaStrategyLabeledDatasetRow, build_labeled_dataset_row
from backend.app.algorithms.meta_strategy.labeling.dataset_validation import (
    FORBIDDEN_FEATURE_ROW_TOKENS,
    MetaStrategyDatasetValidationError,
    validate_feature_row_for_labeling,
    validate_label_end_timestamp,
)
from backend.app.algorithms.meta_strategy.labeling.execution_labels import (
    CandidateSide,
    LabelExecutionCosts,
    MetaStrategyLabelingError,
    execution_costs,
    execution_price,
    geometry_valid,
)
from backend.app.algorithms.meta_strategy.labeling.lineage import (
    MetaStrategyLabelLineage,
    build_label_lineage,
    deterministic_payload_hash,
    label_id,
)
from backend.app.algorithms.meta_strategy.labeling.triple_barrier import (
    META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION,
    META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION,
    BarrierResult,
    MetaStrategyExecutionLabel,
    MetaStrategyLabelCandle,
    MetaStrategyLabelExecutionConfig,
    SameBarAmbiguityPolicy,
    candle_from_mapping,
    create_triple_barrier_label,
    first_barrier,
)

__all__ = [
    "BarrierResult",
    "CandidateSide",
    "FORBIDDEN_FEATURE_ROW_TOKENS",
    "LabelExecutionCosts",
    "META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION",
    "META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION",
    "MetaStrategyDatasetValidationError",
    "MetaStrategyExecutionLabel",
    "MetaStrategyLabelCandle",
    "MetaStrategyLabelExecutionConfig",
    "MetaStrategyLabelLineage",
    "MetaStrategyLabeledDatasetRow",
    "MetaStrategyLabelingError",
    "SameBarAmbiguityPolicy",
    "build_label_lineage",
    "build_labeled_dataset_row",
    "candle_from_mapping",
    "create_triple_barrier_label",
    "deterministic_payload_hash",
    "execution_costs",
    "execution_price",
    "first_barrier",
    "geometry_valid",
    "label_id",
    "validate_feature_row_for_labeling",
    "validate_label_end_timestamp",
]
