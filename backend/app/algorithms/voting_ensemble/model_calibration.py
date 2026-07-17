from __future__ import annotations

from typing import Any


VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION = "voting_ensemble_model_calibration_v1"


def model_calibration_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
        "voting_ensemble.model_calibration.out_of_fold_required",
        "voting_ensemble.model_calibration.probability_sizing_requires_approval",
        "voting_ensemble.model_calibration.schema_bound",
    )


def voting_ensemble_calibration_policy() -> dict[str, Any]:
    return {
        "calibrationVersion": VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
        "requiredSource": "inner_out_of_fold",
        "probabilitySizingRequiresApproval": True,
        "minimumCalibrationRows": 60,
        "maximumCalibrationBrier": 0.28,
        "maximumCalibrationLogLoss": 1.20,
        "maximumCalibrationEce": 0.12,
        "reasonCodes": list(model_calibration_reason_codes()),
    }


def voting_ensemble_model_calibration_artifact(artifact: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    calibration = artifact.get("calibration") or {}
    return {
        **artifact,
        "calibration": {
            **calibration,
            "calibrationVersion": VOTING_ENSEMBLE_MODEL_CALIBRATION_VERSION,
            "policy": voting_ensemble_calibration_policy(),
        },
        "calibrationPolicy": voting_ensemble_calibration_policy(),
    }

