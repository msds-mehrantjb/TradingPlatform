from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from backend.app.backtesting.parameter_tuning import (
    ParameterTuningConfig,
    ParameterTuningFoldInput,
    ParameterTuningReport,
    build_parameter_tuning_report,
    select_parameters_for_fold,
)
from backend.app.domain.models import DomainModel


VOTING_ENSEMBLE_PARAMETER_TUNING_VERSION = "voting_ensemble_parameter_tuning_v1"


def parameter_tuning_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_PARAMETER_TUNING_VERSION,
        "voting_ensemble.parameter_tuning.training_fold_only",
        "voting_ensemble.parameter_tuning.outer_validation_excluded",
        "voting_ensemble.parameter_tuning.stable_region_preferred",
        "voting_ensemble.parameter_tuning.frozen_before_final_test",
    )


class VotingEnsembleParameterTuningConfig(DomainModel):
    tuningVersion: str = VOTING_ENSEMBLE_PARAMETER_TUNING_VERSION
    objectiveMetricName: str = "voting_ensemble_net_expectancy_after_costs"
    scoreTolerance: float = Field(default=0.02, ge=0)
    neighborNumericDistance: float = Field(default=0.05, ge=0)
    sharpOptimumPenalty: float = Field(default=0.10, ge=0)
    stableRegionBonus: float = Field(default=0.005, ge=0)
    unstableScoreRange: float = Field(default=0.08, ge=0)
    minimumStableValues: int = Field(default=2, ge=1)

    def to_shared_config(self) -> ParameterTuningConfig:
        return ParameterTuningConfig(
            tuningVersion=self.tuningVersion,
            objectiveMetricName=self.objectiveMetricName,
            scoreTolerance=self.scoreTolerance,
            neighborNumericDistance=self.neighborNumericDistance,
            sharpOptimumPenalty=self.sharpOptimumPenalty,
            stableRegionBonus=self.stableRegionBonus,
            unstableScoreRange=self.unstableScoreRange,
            minimumStableValues=self.minimumStableValues,
        )


class VotingEnsembleParameterTuningReport(DomainModel):
    tuningVersion: str
    generatedAt: datetime
    sharedReport: ParameterTuningReport
    frozenConfigurationHash: str
    reasonCodes: list[str]
    explanation: str


def build_voting_ensemble_parameter_tuning_report(
    fold_inputs: list[ParameterTuningFoldInput] | list[dict[str, Any]],
    *,
    tuning_config: VotingEnsembleParameterTuningConfig | None = None,
    generated_at: datetime | None = None,
    choices_frozen_at: datetime | None = None,
    final_test_loaded_at: datetime | None = None,
) -> VotingEnsembleParameterTuningReport:
    config = tuning_config or VotingEnsembleParameterTuningConfig()
    generated = generated_at or datetime.now(UTC)
    shared = build_parameter_tuning_report(
        fold_inputs,
        tuning_config=config.to_shared_config(),
        generated_at=generated,
        choices_frozen_at=choices_frozen_at or generated,
        final_test_loaded_at=final_test_loaded_at,
    )
    return VotingEnsembleParameterTuningReport(
        tuningVersion=VOTING_ENSEMBLE_PARAMETER_TUNING_VERSION,
        generatedAt=generated,
        sharedReport=shared,
        frozenConfigurationHash=shared.frozenConfigurationHash,
        reasonCodes=list(dict.fromkeys([*parameter_tuning_reason_codes(), *shared.reasonCodes])),
        explanation=(
            "Voting Ensemble parameter tuning selected configurations inside training folds only, "
            "froze the selected configuration before final-test access, and preserved the shared robust-selection report."
        ),
    )


def select_voting_ensemble_parameters_for_fold(
    fold: ParameterTuningFoldInput | dict[str, Any],
    *,
    tuning_config: VotingEnsembleParameterTuningConfig | None = None,
):
    config = tuning_config or VotingEnsembleParameterTuningConfig()
    return select_parameters_for_fold(fold, tuning_config=config.to_shared_config())

