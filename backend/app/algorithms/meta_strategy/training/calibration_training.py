"""Out-of-fold calibration training for Meta-Strategy models."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def tune_probability_calibration(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("tune_probability_calibration", *args, **kwargs)


def tune_probability_calibration_from_probability_rows(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("tune_probability_calibration_from_probability_rows", *args, **kwargs)


def evaluate_calibration_report(
    probability_rows: list[dict[str, Any]],
    *,
    minimum_rows: int = 60,
    minimum_isotonic_rows: int = 80,
    maximum_brier: float = 0.28,
    maximum_log_loss: float = 1.20,
    maximum_ece: float = 0.12,
) -> dict[str, Any]:
    calibration = tune_probability_calibration_from_probability_rows(
        probability_rows,
        minimum_rows=minimum_rows,
        minimum_isotonic_rows=minimum_isotonic_rows,
        maximum_brier=maximum_brier,
        maximum_log_loss=maximum_log_loss,
        maximum_ece=maximum_ece,
    )
    metrics = calibration.get("metrics") or {}
    return {
        "source": calibration.get("source"),
        "method": calibration.get("method"),
        "trainingRows": calibration.get("trainingRows", 0),
        "brierScore": metrics.get("brierScore"),
        "logLoss": metrics.get("logLoss"),
        "expectedCalibrationError": metrics.get("expectedCalibrationError"),
        "reliabilityCurve": metrics.get("reliabilityCurve", []),
        "calibrationByCandidateSide": metrics.get("byCandidateSide", {}),
        "calibrationByRegime": metrics.get("byMarketRegime", {}),
        "probabilitySizingApproved": calibration.get("probabilitySizingApproved", False),
        "approvalReasonCodes": tuple(calibration.get("approvalReasonCodes") or ()),
        "methodsEvaluated": calibration.get("methodsEvaluated", []),
        "reasonCodes": (
            "meta_strategy.calibration.out_of_fold_required",
            "meta_strategy.calibration.metrics_evaluated",
        ),
        "calibration": calibration,
    }


def apply_probability_calibration_model(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("apply_probability_calibration_model", *args, **kwargs)


def predict_calibrated_probabilities(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("predict_calibrated_probabilities", *args, **kwargs)


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "apply_probability_calibration_model",
    "evaluate_calibration_report",
    "predict_calibrated_probabilities",
    "tune_probability_calibration",
    "tune_probability_calibration_from_probability_rows",
]
