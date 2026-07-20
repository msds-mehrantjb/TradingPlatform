"""Deterministic lineage helpers for Meta-Strategy labeling datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_LABEL_SPECIFICATION_VERSION,
)


@dataclass(frozen=True)
class MetaStrategyLabelLineage:
    algorithmVersion: str
    featureSchemaVersion: str
    labelSpecificationVersion: str
    sourceSnapshotId: str
    sourceDecisionId: str
    featureRowId: str
    labelId: str
    labelEndTimestampUtc: datetime
    lineageHash: str


def label_id(*, snapshot_id: str, label_version: str, configuration_hash: str) -> str:
    payload = f"{snapshot_id}|{label_version}|{configuration_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def deterministic_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def build_label_lineage(
    *,
    snapshot_id: str,
    decision_id: str,
    feature_row_id: str,
    label_id_value: str,
    label_end_timestamp_utc: datetime,
) -> MetaStrategyLabelLineage:
    lineage_hash = deterministic_payload_hash(
        {
            "algorithmVersion": META_STRATEGY_ALGORITHM_VERSION,
            "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "labelSpecificationVersion": META_STRATEGY_LABEL_SPECIFICATION_VERSION,
            "snapshotId": snapshot_id,
            "decisionId": decision_id,
            "featureRowId": feature_row_id,
            "labelId": label_id_value,
            "labelEndTimestampUtc": label_end_timestamp_utc,
        }
    )
    return MetaStrategyLabelLineage(
        algorithmVersion=META_STRATEGY_ALGORITHM_VERSION,
        featureSchemaVersion=META_STRATEGY_FEATURE_SCHEMA_VERSION,
        labelSpecificationVersion=META_STRATEGY_LABEL_SPECIFICATION_VERSION,
        sourceSnapshotId=snapshot_id,
        sourceDecisionId=decision_id,
        featureRowId=feature_row_id,
        labelId=label_id_value,
        labelEndTimestampUtc=label_end_timestamp_utc,
        lineageHash=lineage_hash,
    )


__all__ = [
    "MetaStrategyLabelLineage",
    "build_label_lineage",
    "deterministic_payload_hash",
    "label_id",
]
