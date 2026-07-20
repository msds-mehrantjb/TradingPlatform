"""Chronological validation gates for Meta-Strategy training."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.algorithms.meta_strategy.training.configuration import MetaTrainingConfig


class ChronologicalValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ChronologicalValidationReport:
    valid: bool
    method: str
    nestedWalkForward: bool
    randomSplittingProhibited: bool
    minimumCoverage: dict[str, Any]
    foldWindows: tuple[dict[str, Any], ...]
    finalHoldoutWindow: dict[str, Any]
    reasonCodes: tuple[str, ...]


def prohibit_random_split_policy(policy: dict[str, Any] | str | None) -> None:
    if policy is None:
        return
    text = str(policy).replace("-", "_").lower()
    if "random" in text or "shuffle" in text:
        raise ChronologicalValidationError("meta_strategy.training.random_splitting_prohibited")


def validate_chronological_examples(examples: list[dict[str, Any]], config: MetaTrainingConfig) -> dict[str, Any]:
    normalized = config.normalized()
    timestamps = [_timestamp(row, "timestamp") for row in examples]
    if timestamps != sorted(timestamps):
        raise ChronologicalValidationError("meta_strategy.training.examples_not_chronological")

    labels = Counter(str(row.get("label") or "") for row in examples)
    binary = Counter(1 if int(row.get("binaryOutcome", 0)) > 0 else 0 for row in examples)
    sessions = {str(row.get("sessionDate") or "") for row in examples if row.get("sessionDate")}
    regimes = {str(row.get("regime") or "") for row in examples if row.get("regime")}
    checks = {
        "totalCandidates": {"observed": len(examples), "minimum": normalized.minimumTotalCandidates},
        "buyCandidates": {"observed": labels["BUY"], "minimum": normalized.minimumBuyCandidates},
        "sellCandidates": {"observed": labels["SELL"], "minimum": normalized.minimumSellCandidates},
        "positiveLabels": {"observed": binary[1], "minimum": normalized.minimumPositiveOutcomes},
        "negativeLabels": {"observed": binary[0], "minimum": normalized.minimumNegativeOutcomes},
        "sessions": {"observed": len(sessions), "minimum": normalized.minimumTradingSessions},
        "regimes": {"observed": len(regimes), "minimum": normalized.minimumRegimesRepresented},
    }
    failures = [name for name, check in checks.items() if int(check["observed"]) < int(check["minimum"])]
    if failures:
        raise ChronologicalValidationError(f"meta_strategy.training.minimum_coverage_failed:{','.join(failures)}")
    return checks


def validate_chronological_training_plan(
    plan: dict[str, Any],
    config: MetaTrainingConfig,
    *,
    split_policy: dict[str, Any] | str | None = "nested_chronological_purged_walk_forward",
) -> ChronologicalValidationReport:
    prohibit_random_split_policy(split_policy)
    normalized = config.normalized()
    if not plan.get("sufficient"):
        raise ChronologicalValidationError("meta_strategy.training.walk_forward_plan_insufficient")
    if (plan.get("report") or {}).get("method") != "nested_chronological_purged_walk_forward":
        raise ChronologicalValidationError("meta_strategy.training.nested_walk_forward_required")

    development_rows = list(plan.get("developmentRows") or [])
    final_rows = list(plan.get("finalTestRows") or [])
    if not development_rows or not final_rows:
        raise ChronologicalValidationError("meta_strategy.training.untouched_final_holdout_required")
    if _max_timestamp(development_rows, "timestamp") >= _min_timestamp(final_rows, "timestamp"):
        raise ChronologicalValidationError("meta_strategy.training.final_holdout_overlaps_development")

    final_ids = _row_ids(final_rows)
    if final_ids & _row_ids(development_rows):
        raise ChronologicalValidationError("meta_strategy.training.final_holdout_reused_in_development")

    fold_windows: list[dict[str, Any]] = []
    for fold in plan.get("outerFolds") or []:
        fold_windows.append(_validate_fold_window(fold, normalized, final_ids))

    if len(fold_windows) < normalized.outerFolds:
        raise ChronologicalValidationError("meta_strategy.training.minimum_outer_fold_count_failed")

    final_window = {
        "start": _min_timestamp(final_rows, "timestamp").isoformat(),
        "end": _max_timestamp(final_rows, "timestamp").isoformat(),
        "rows": len(final_rows),
        "untouched": True,
    }
    return ChronologicalValidationReport(
        valid=True,
        method="nested_chronological_purged_walk_forward",
        nestedWalkForward=True,
        randomSplittingProhibited=True,
        minimumCoverage={},
        foldWindows=tuple(fold_windows),
        finalHoldoutWindow=final_window,
        reasonCodes=(
            "meta_strategy.training.chronological_order_valid",
            "meta_strategy.training.nested_walk_forward_valid",
            "meta_strategy.training.purging_valid",
            "meta_strategy.training.embargo_valid",
            "meta_strategy.training.final_holdout_untouched",
        ),
    )


def build_chronological_validation_report(
    examples: list[dict[str, Any]],
    plan: dict[str, Any],
    config: MetaTrainingConfig,
    *,
    split_policy: dict[str, Any] | str | None = "nested_chronological_purged_walk_forward",
) -> ChronologicalValidationReport:
    coverage = validate_chronological_examples(examples, config)
    report = validate_chronological_training_plan(plan, config, split_policy=split_policy)
    return ChronologicalValidationReport(
        valid=report.valid,
        method=report.method,
        nestedWalkForward=report.nestedWalkForward,
        randomSplittingProhibited=report.randomSplittingProhibited,
        minimumCoverage=coverage,
        foldWindows=report.foldWindows,
        finalHoldoutWindow=report.finalHoldoutWindow,
        reasonCodes=report.reasonCodes + ("meta_strategy.training.minimum_coverage_valid",),
    )


def _validate_fold_window(fold: dict[str, Any], config: MetaTrainingConfig, final_ids: set[str]) -> dict[str, Any]:
    required = ("trainingWindowStart", "trainingWindowEnd", "validationWindowStart", "validationWindowEnd", "labelWindowCutoff", "embargoMinutes")
    missing = [key for key in required if key not in fold]
    if missing:
        raise ChronologicalValidationError(f"meta_strategy.training.fold_windows_missing:{','.join(missing)}")
    train_rows = list(fold.get("trainRows") or [])
    validation_rows = list(fold.get("validationRows") or [])
    if len(validation_rows) < config.minimumCandidatesPerOuterFold:
        raise ChronologicalValidationError("meta_strategy.training.minimum_fold_validation_rows_failed")
    if _row_ids(train_rows) & _row_ids(validation_rows):
        raise ChronologicalValidationError("meta_strategy.training.fold_train_validation_overlap")
    if final_ids & (_row_ids(train_rows) | _row_ids(validation_rows)):
        raise ChronologicalValidationError("meta_strategy.training.final_holdout_reused_in_fold")

    validation_start = _timestamp(fold, "validationWindowStart")
    validation_end = _timestamp(fold, "validationWindowEnd")
    label_cutoff = _timestamp(fold, "labelWindowCutoff")
    embargo_minutes = int(fold["embargoMinutes"])
    if embargo_minutes < config.maximumHoldingHorizonMinutes:
        raise ChronologicalValidationError("meta_strategy.training.embargo_shorter_than_horizon")
    if any(_timestamp(row, "timestamp") >= validation_start for row in train_rows):
        raise ChronologicalValidationError("meta_strategy.training.fold_not_chronological")
    if any(_timestamp(row, "labelEnd") >= label_cutoff for row in train_rows):
        raise ChronologicalValidationError("meta_strategy.training.fold_purge_leakage")
    if any(not (validation_start <= _timestamp(row, "timestamp") <= validation_end) for row in validation_rows):
        raise ChronologicalValidationError("meta_strategy.training.validation_row_outside_window")
    return {
        "fold": int(fold.get("fold") or 0),
        "trainingWindowStart": str(fold["trainingWindowStart"]),
        "trainingWindowEnd": str(fold["trainingWindowEnd"]),
        "validationWindowStart": str(fold["validationWindowStart"]),
        "validationWindowEnd": str(fold["validationWindowEnd"]),
        "labelWindowCutoff": str(fold["labelWindowCutoff"]),
        "embargoMinutes": embargo_minutes,
        "purgedRows": int(fold.get("purgedRows") or 0),
        "trainingRows": len(train_rows),
        "validationRows": len(validation_rows),
    }


def _row_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("rowId") or row.get("snapshotId") or id(row)) for row in rows}


def _timestamp(row: dict[str, Any], key: str) -> datetime:
    value = row.get(key)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _min_timestamp(rows: list[dict[str, Any]], key: str) -> datetime:
    return min(_timestamp(row, key) for row in rows)


def _max_timestamp(rows: list[dict[str, Any]], key: str) -> datetime:
    return max(_timestamp(row, key) for row in rows)


__all__ = [
    "ChronologicalValidationError",
    "ChronologicalValidationReport",
    "build_chronological_validation_report",
    "prohibit_random_split_policy",
    "validate_chronological_examples",
    "validate_chronological_training_plan",
]
