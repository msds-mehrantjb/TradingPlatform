"""Build point-in-time Meta-Strategy training rows from features and labels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.feature_builder import MetaStrategyFeatureSet
from backend.app.algorithms.meta_strategy.labeling.dataset_validation import (
    validate_feature_row_for_labeling,
    validate_label_end_timestamp,
)
from backend.app.algorithms.meta_strategy.labeling.lineage import MetaStrategyLabelLineage, build_label_lineage
from backend.app.algorithms.meta_strategy.labeling.triple_barrier import MetaStrategyExecutionLabel


@dataclass(frozen=True)
class MetaStrategyLabeledDatasetRow:
    rowId: str
    featureSet: MetaStrategyFeatureSet
    executionLabel: MetaStrategyExecutionLabel
    lineage: MetaStrategyLabelLineage
    featureValues: dict[str, Any]
    labelValues: dict[str, Any]
    validationReasonCodes: tuple[str, ...]


def build_labeled_dataset_row(
    *,
    feature_set: MetaStrategyFeatureSet,
    execution_label: MetaStrategyExecutionLabel,
) -> MetaStrategyLabeledDatasetRow:
    validation_codes = validate_feature_row_for_labeling(feature_set)
    validate_label_end_timestamp(execution_label.labelEndTimestampUtc)
    if feature_set.rowId not in {execution_label.snapshotId, execution_label.decisionId}:
        validation_codes = (*validation_codes, "meta_strategy.dataset.feature_row_external_id")
    lineage = build_label_lineage(
        snapshot_id=execution_label.snapshotId,
        decision_id=execution_label.decisionId,
        feature_row_id=feature_set.rowId,
        label_id_value=execution_label.labelId,
        label_end_timestamp_utc=execution_label.labelEndTimestampUtc,
    )
    return MetaStrategyLabeledDatasetRow(
        rowId=f"{feature_set.rowId}:{execution_label.labelId}",
        featureSet=feature_set,
        executionLabel=execution_label,
        lineage=lineage,
        featureValues=dict(feature_set.featureValues),
        labelValues={
            "labelId": execution_label.labelId,
            "labelVersion": execution_label.labelVersion,
            "labelSpecificationVersion": execution_label.labelSpecificationVersion,
            "candidateSide": execution_label.candidateSide,
            "firstBarrierHit": execution_label.firstBarrierHit,
            "strictOutcomeLabel": execution_label.strictOutcomeLabel,
            "costAdjustedTrainingLabel": execution_label.costAdjustedTrainingLabel,
            "eligibleForTraining": execution_label.eligibleForTraining,
            "labelEndTimestampUtc": execution_label.labelEndTimestampUtc,
            "netPnlAfterCosts": execution_label.netPnlAfterCosts,
            "reasonCodes": execution_label.reasonCodes,
        },
        validationReasonCodes=validation_codes,
    )


__all__ = [
    "MetaStrategyLabeledDatasetRow",
    "build_labeled_dataset_row",
]
