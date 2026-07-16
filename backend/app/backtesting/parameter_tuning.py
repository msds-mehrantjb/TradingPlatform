from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from enum import Enum
from statistics import mean
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.domain.models import DomainModel, _require_utc


class ParameterGroup(str, Enum):
    STRATEGY_DETECTION = "strategy_detection"
    ENSEMBLE_AGGREGATION = "ensemble_aggregation"
    ML_FILTERING = "ml_filtering"
    DYNAMIC_POLICY = "dynamic_policy"
    GLOBAL_RISK = "global_risk"
    EXECUTION_ASSUMPTIONS = "execution_assumptions"


REQUIRED_PARAMETER_GROUPS = [
    ParameterGroup.STRATEGY_DETECTION,
    ParameterGroup.ENSEMBLE_AGGREGATION,
    ParameterGroup.ML_FILTERING,
    ParameterGroup.DYNAMIC_POLICY,
    ParameterGroup.GLOBAL_RISK,
    ParameterGroup.EXECUTION_ASSUMPTIONS,
]


class ParameterTuningConfig(DomainModel):
    tuningVersion: str = "parameter_tuning_v1"
    objectiveMetricName: str = "net_expectancy_after_costs"
    scoreTolerance: float = Field(default=0.02, ge=0)
    neighborNumericDistance: float = Field(default=0.05, ge=0)
    sharpOptimumPenalty: float = Field(default=0.10, ge=0)
    stableRegionBonus: float = Field(default=0.005, ge=0)
    unstableScoreRange: float = Field(default=0.08, ge=0)
    minimumStableValues: int = Field(default=2, ge=1)


class ParameterConfiguration(DomainModel):
    configurationVersion: str = "parameter_configuration_v1"
    strategyDetection: dict[str, Any] = Field(default_factory=dict)
    ensembleAggregation: dict[str, Any] = Field(default_factory=dict)
    mlFiltering: dict[str, Any] = Field(default_factory=dict)
    dynamicPolicy: dict[str, Any] = Field(default_factory=dict)
    globalRisk: dict[str, Any] = Field(default_factory=dict)
    executionAssumptions: dict[str, Any] = Field(default_factory=dict)


class ParameterCandidateEvaluation(DomainModel):
    candidateId: str = Field(min_length=1)
    configuration: ParameterConfiguration
    trainingScore: float
    evaluationSource: Literal["training_fold"] = "training_fold"
    evaluationWindowStartUtc: datetime
    evaluationWindowEndUtc: datetime
    evaluatedParameterGroups: list[ParameterGroup] = Field(min_length=1)
    sampleCount: int = Field(default=0, ge=0)
    explanation: str = Field(default="Candidate evaluated on the fold training window only.", min_length=1)

    @field_validator("evaluationWindowStartUtc", "evaluationWindowEndUtc")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def end_after_start(self) -> "ParameterCandidateEvaluation":
        if self.evaluationWindowEndUtc < self.evaluationWindowStartUtc:
            raise ValueError("candidate evaluation window end must be on or after start")
        return self


class ParameterTuningFoldInput(DomainModel):
    foldId: str = Field(min_length=1)
    trainingWindowStartUtc: datetime
    trainingWindowEndUtc: datetime
    validationWindowStartUtc: datetime
    validationWindowEndUtc: datetime
    candidates: list[ParameterCandidateEvaluation] = Field(min_length=1)

    @field_validator(
        "trainingWindowStartUtc",
        "trainingWindowEndUtc",
        "validationWindowStartUtc",
        "validationWindowEndUtc",
    )
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def chronological_training_before_validation(self) -> "ParameterTuningFoldInput":
        if self.trainingWindowEndUtc >= self.validationWindowStartUtc:
            raise ValueError("training window must end before outer validation starts")
        if self.validationWindowEndUtc < self.validationWindowStartUtc:
            raise ValueError("validation window end must be on or after validation start")
        for candidate in self.candidates:
            if candidate.evaluationWindowEndUtc > self.trainingWindowEndUtc:
                raise ValueError("outer validation data cannot affect parameter selection")
            if candidate.evaluationWindowStartUtc < self.trainingWindowStartUtc:
                raise ValueError("candidate evaluation starts before the fold training window")
        return self


class ParameterSensitivityReport(DomainModel):
    parameterPath: str = Field(min_length=1)
    selectedValue: Any
    selectedScore: float
    testedValues: list[Any]
    bestScore: float
    worstScore: float
    scoreRange: float = Field(ge=0)
    stableValues: list[Any]
    unstable: bool
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class FoldParameterTuningResult(DomainModel):
    foldId: str = Field(min_length=1)
    selectedCandidateId: str = Field(min_length=1)
    selectedConfiguration: ParameterConfiguration
    selectedConfigurationHash: str = Field(min_length=1)
    selectedTrainingScore: float
    robustSelectionScore: float
    trainingWindowStartUtc: datetime
    trainingWindowEndUtc: datetime
    validationWindowStartUtc: datetime
    validationWindowEndUtc: datetime
    selectionSource: Literal["training_fold_only"] = "training_fold_only"
    sensitivityReports: list[ParameterSensitivityReport]
    unstableParameters: list[str]
    reasonCodes: list[str]
    explanation: str = Field(min_length=1)

    @field_validator(
        "trainingWindowStartUtc",
        "trainingWindowEndUtc",
        "validationWindowStartUtc",
        "validationWindowEndUtc",
    )
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ParameterTuningReport(DomainModel):
    reportVersion: str
    generatedAt: datetime
    configurationHash: str = Field(min_length=1)
    tuningConfig: ParameterTuningConfig
    configurationGroups: list[ParameterGroup]
    foldResults: list[FoldParameterTuningResult] = Field(min_length=1)
    frozenConfiguration: ParameterConfiguration
    frozenConfigurationHash: str = Field(min_length=1)
    choicesFrozenAtUtc: datetime
    finalTestLoadedAtUtc: datetime | None = None
    finalTestPolicy: str
    unstableParameters: list[str]
    reasonCodes: list[str]
    explanation: str = Field(min_length=1)

    @field_validator("generatedAt", "choicesFrozenAtUtc", "finalTestLoadedAtUtc")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def final_test_after_freeze(self) -> "ParameterTuningReport":
        if self.finalTestLoadedAtUtc is not None and self.finalTestLoadedAtUtc <= self.choicesFrozenAtUtc:
            raise ValueError("final-test data may be loaded only after all parameter choices are frozen")
        return self


def parameter_configuration_hash(configuration: ParameterConfiguration | dict[str, Any]) -> str:
    payload = _to_jsonable(configuration)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def build_parameter_tuning_report(
    fold_inputs: list[ParameterTuningFoldInput] | list[dict[str, Any]],
    *,
    tuning_config: ParameterTuningConfig | None = None,
    generated_at: datetime | None = None,
    choices_frozen_at: datetime | None = None,
    final_test_loaded_at: datetime | None = None,
) -> ParameterTuningReport:
    config = tuning_config or ParameterTuningConfig()
    normalized = [item if isinstance(item, ParameterTuningFoldInput) else ParameterTuningFoldInput(**item) for item in fold_inputs]
    fold_results = [select_parameters_for_fold(item, tuning_config=config) for item in normalized]
    frozen = _consensus_configuration(fold_results)
    frozen_hash = parameter_configuration_hash(frozen)
    generated = generated_at or datetime.now(UTC)
    choices_frozen = choices_frozen_at or generated
    unstable_parameters = sorted({parameter for fold in fold_results for parameter in fold.unstableParameters})
    reason_codes = ["outer_validation_excluded_from_tuning", "final_test_loaded_after_freeze", "configuration_hash_identifies_experiment"]
    if unstable_parameters:
        reason_codes.append("sensitivity.unstable_parameters_detected")
    report_payload = {
        "reportVersion": "parameter_tuning_report_v1",
        "tuningConfig": config,
        "foldConfigurationHashes": [fold.selectedConfigurationHash for fold in fold_results],
        "frozenConfigurationHash": frozen_hash,
    }
    return ParameterTuningReport(
        reportVersion="parameter_tuning_report_v1",
        generatedAt=generated,
        configurationHash=parameter_configuration_hash(report_payload),
        tuningConfig=config,
        configurationGroups=REQUIRED_PARAMETER_GROUPS,
        foldResults=fold_results,
        frozenConfiguration=frozen,
        frozenConfigurationHash=frozen_hash,
        choicesFrozenAtUtc=choices_frozen,
        finalTestLoadedAtUtc=final_test_loaded_at,
        finalTestPolicy="Final-test data is loaded only after fold-level selections and the frozen configuration hash are fixed.",
        unstableParameters=unstable_parameters,
        reasonCodes=reason_codes,
        explanation=(
            "Parameter tuning is performed inside each training fold only. Outer validation and final-test windows are "
            "excluded from parameter selection, and sensitivity reports prefer broad stable regions over sharp optima."
        ),
    )


def select_parameters_for_fold(
    fold: ParameterTuningFoldInput | dict[str, Any],
    *,
    tuning_config: ParameterTuningConfig | None = None,
) -> FoldParameterTuningResult:
    config = tuning_config or ParameterTuningConfig()
    normalized = fold if isinstance(fold, ParameterTuningFoldInput) else ParameterTuningFoldInput(**fold)
    _require_all_groups_represented(normalized.candidates)
    scored = [
        (
            _robust_selection_score(candidate, normalized.candidates, config),
            candidate,
        )
        for candidate in normalized.candidates
    ]
    robust_score, selected = max(scored, key=lambda item: (item[0], item[1].trainingScore, item[1].candidateId))
    sensitivity = _sensitivity_reports(selected, normalized.candidates, config)
    unstable = sorted(report.parameterPath for report in sensitivity if report.unstable)
    return FoldParameterTuningResult(
        foldId=normalized.foldId,
        selectedCandidateId=selected.candidateId,
        selectedConfiguration=selected.configuration,
        selectedConfigurationHash=parameter_configuration_hash(selected.configuration),
        selectedTrainingScore=selected.trainingScore,
        robustSelectionScore=round(robust_score, 6),
        trainingWindowStartUtc=normalized.trainingWindowStartUtc,
        trainingWindowEndUtc=normalized.trainingWindowEndUtc,
        validationWindowStartUtc=normalized.validationWindowStartUtc,
        validationWindowEndUtc=normalized.validationWindowEndUtc,
        sensitivityReports=sensitivity,
        unstableParameters=unstable,
        reasonCodes=["selection.training_fold_only", "sensitivity_report_generated"],
        explanation=(
            f"Fold {normalized.foldId} selected {selected.candidateId} using training-fold scores only. "
            "Outer validation timestamps were not part of candidate scoring."
        ),
    )


def _robust_selection_score(
    candidate: ParameterCandidateEvaluation,
    candidates: list[ParameterCandidateEvaluation],
    config: ParameterTuningConfig,
) -> float:
    near_count = sum(
        1
        for other in candidates
        if other.candidateId != candidate.candidateId
        and abs(other.trainingScore - candidate.trainingScore) <= config.scoreTolerance
        and _nearby_configuration(candidate.configuration, other.configuration, config.neighborNumericDistance)
    )
    has_nearby_support = near_count >= max(0, config.minimumStableValues - 1)
    penalty = 0.0 if has_nearby_support else config.sharpOptimumPenalty
    bonus = near_count * config.stableRegionBonus
    return candidate.trainingScore - penalty + bonus


def _sensitivity_reports(
    selected: ParameterCandidateEvaluation,
    candidates: list[ParameterCandidateEvaluation],
    config: ParameterTuningConfig,
) -> list[ParameterSensitivityReport]:
    selected_flat = _flatten_config(selected.configuration)
    reports: list[ParameterSensitivityReport] = []
    for path, selected_value in sorted(selected_flat.items()):
        value_scores: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            candidate_flat = _flatten_config(candidate.configuration)
            if path not in candidate_flat:
                continue
            value = candidate_flat[path]
            value_key = json.dumps(_to_jsonable(value), sort_keys=True)
            existing = value_scores.setdefault(value_key, {"value": value, "scores": []})
            existing["scores"].append(candidate.trainingScore)
        if len(value_scores) < 2:
            continue
        aggregated = [
            {"value": item["value"], "score": mean(float(score) for score in item["scores"])}
            for item in value_scores.values()
        ]
        best = max(float(item["score"]) for item in aggregated)
        worst = min(float(item["score"]) for item in aggregated)
        stable_values = [item["value"] for item in aggregated if float(item["score"]) >= selected.trainingScore - config.scoreTolerance]
        unstable = bool((best - worst) >= config.unstableScoreRange and len(stable_values) < config.minimumStableValues)
        reason_codes = ["sensitivity.stable_region"] if not unstable else ["sensitivity.unstable_parameter"]
        reports.append(
            ParameterSensitivityReport(
                parameterPath=path,
                selectedValue=selected_value,
                selectedScore=selected.trainingScore,
                testedValues=[item["value"] for item in sorted(aggregated, key=lambda row: str(row["value"]))],
                bestScore=round(best, 6),
                worstScore=round(worst, 6),
                scoreRange=round(best - worst, 6),
                stableValues=stable_values,
                unstable=unstable,
                reasonCodes=reason_codes,
                explanation=(
                    f"{path} has {len(stable_values)} value(s) within {config.scoreTolerance:.4f} of the selected score."
                    if not unstable
                    else f"{path} is unstable because only {len(stable_values)} value(s) remain near the selected score across a wide score range."
                ),
            )
        )
    return reports


def _require_all_groups_represented(candidates: list[ParameterCandidateEvaluation]) -> None:
    represented = {ParameterGroup(group) for candidate in candidates for group in candidate.evaluatedParameterGroups}
    missing = [group.value for group in REQUIRED_PARAMETER_GROUPS if group not in represented]
    if missing:
        raise ValueError(f"parameter tuning candidates are missing configuration groups: {', '.join(missing)}")


def _nearby_configuration(left: ParameterConfiguration, right: ParameterConfiguration, numeric_distance: float) -> bool:
    left_flat = _flatten_config(left)
    right_flat = _flatten_config(right)
    shared = sorted(set(left_flat) & set(right_flat))
    differences = [(left_flat[path], right_flat[path]) for path in shared if left_flat[path] != right_flat[path]]
    if not differences:
        return True
    numeric_differences = [
        abs(float(left_value) - float(right_value))
        for left_value, right_value in differences
        if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float))
    ]
    if len(numeric_differences) != len(differences):
        return False
    return max(numeric_differences) <= numeric_distance


def _consensus_configuration(fold_results: list[FoldParameterTuningResult]) -> ParameterConfiguration:
    counts: dict[str, tuple[int, ParameterConfiguration]] = {}
    for result in fold_results:
        hash_value = result.selectedConfigurationHash
        count, _ = counts.get(hash_value, (0, result.selectedConfiguration))
        counts[hash_value] = (count + 1, result.selectedConfiguration)
    return max(counts.values(), key=lambda item: item[0])[1]


def _flatten_config(configuration: ParameterConfiguration) -> dict[str, Any]:
    payload = configuration.model_dump(mode="json")
    flattened: dict[str, Any] = {}
    for group_name, group_value in payload.items():
        if group_name == "configurationVersion":
            continue
        _flatten_value(group_name, group_value, flattened)
    return flattened


def _flatten_value(prefix: str, value: Any, flattened: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_value(f"{prefix}.{key}", item, flattened)
    else:
        flattened[prefix] = value


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value
