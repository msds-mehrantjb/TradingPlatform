"""Runtime-health gates for Meta-Strategy inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.inference.artifact_health import artifact_schema_compatible


@dataclass(frozen=True)
class RuntimeHealthCheck:
    name: str
    passed: bool
    reasonCode: str
    observed: Any = None
    threshold: Any = None


@dataclass(frozen=True)
class RuntimeHealthGateResult:
    passed: bool
    checks: tuple[RuntimeHealthCheck, ...]

    @property
    def reasonCodes(self) -> tuple[str, ...]:
        return tuple(check.reasonCode for check in self.checks if not check.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": tuple(
                {
                    "name": check.name,
                    "passed": check.passed,
                    "reasonCode": check.reasonCode,
                    "observed": check.observed,
                    "threshold": check.threshold,
                }
                for check in self.checks
            ),
            "reasonCodes": self.reasonCodes,
        }


def evaluate_runtime_health_gates(
    *,
    config: Any,
    model_artifact: dict[str, Any] | None,
    model: dict[str, Any] | None,
    expected_schema_hash: str,
    feature_missingness: float,
    out_of_distribution_score: float | None,
    model_health: dict[str, Any],
    deterministic_signal: str,
    candidate_conditional_output: Any | None = None,
    prediction_started_at: datetime | None = None,
    prediction_finished_at: datetime | None = None,
) -> RuntimeHealthGateResult:
    checks = [
        _artifact_available(model_artifact, model),
        _feature_schema_compatible(model_artifact, expected_schema_hash),
        _feature_missingness(feature_missingness, getattr(config, "maxFeatureMissingness", 0.0)),
        _out_of_distribution(out_of_distribution_score, getattr(config, "maxOutOfDistributionScore", 0.0)),
        _calibration_status(model_artifact, model, require_calibration=getattr(config, "requireCalibratedModel", False)),
        _model_health(model_health, getattr(config, "minModelHealthScore", 0.0)),
        _artifact_age(model_artifact, getattr(config, "maxArtifactAgeDays", None), prediction_finished_at),
        _prediction_latency(prediction_started_at, prediction_finished_at, getattr(config, "maxPredictionLatencyMs", None)),
        _candidate_side_compatible(deterministic_signal, model, candidate_conditional_output),
    ]
    return RuntimeHealthGateResult(passed=all(check.passed for check in checks), checks=tuple(checks))


def _artifact_available(model_artifact: dict[str, Any] | None, model: dict[str, Any] | None) -> RuntimeHealthCheck:
    passed = bool(model_artifact and model)
    return RuntimeHealthCheck(
        name="artifact_compatibility",
        passed=passed,
        reasonCode="meta_strategy.inference.runtime_health.artifact_unavailable",
        observed="available" if passed else "missing",
        threshold="available",
    )


def _feature_schema_compatible(model_artifact: dict[str, Any] | None, expected_schema_hash: str) -> RuntimeHealthCheck:
    passed = artifact_schema_compatible(model_artifact, expected_schema_hash)
    return RuntimeHealthCheck(
        name="feature_schema_compatibility",
        passed=passed,
        reasonCode="meta_strategy.inference.runtime_health.feature_schema_mismatch",
        observed=str((model_artifact or {}).get("featureSchemaHash") or ""),
        threshold=expected_schema_hash,
    )


def _feature_missingness(feature_missingness: float, maximum: float) -> RuntimeHealthCheck:
    observed = _bounded(feature_missingness)
    threshold = _bounded(maximum)
    return RuntimeHealthCheck(
        name="feature_missingness",
        passed=observed <= threshold,
        reasonCode="meta_strategy.inference.runtime_health.feature_missingness_too_high",
        observed=observed,
        threshold=threshold,
    )


def _out_of_distribution(score: float | None, maximum: float) -> RuntimeHealthCheck:
    observed = 1.0 if score is None else _bounded(score)
    threshold = _bounded(maximum)
    return RuntimeHealthCheck(
        name="out_of_distribution_score",
        passed=observed <= threshold,
        reasonCode="meta_strategy.inference.runtime_health.out_of_distribution",
        observed=observed,
        threshold=threshold,
    )


def _calibration_status(
    model_artifact: dict[str, Any] | None,
    model: dict[str, Any] | None,
    *,
    require_calibration: bool,
) -> RuntimeHealthCheck:
    calibration = (model or {}).get("calibration")
    method = str((calibration or {}).get("method") or (model_artifact or {}).get("calibrationMethod") or "none")
    status = str((calibration or {}).get("status") or (model_artifact or {}).get("calibrationStatus") or "OK").upper()
    approved = (calibration or {}).get("approved", (model_artifact or {}).get("calibrationApproved", True))
    passed = status not in {"FAILED", "STALE", "IN_SAMPLE"} and approved is not False and (method != "none" or not require_calibration)
    return RuntimeHealthCheck(
        name="calibration_status",
        passed=passed,
        reasonCode="meta_strategy.inference.runtime_health.calibration_invalid",
        observed={"method": method, "status": status, "approved": approved},
        threshold={"requireCalibration": require_calibration, "forbiddenStatuses": ("FAILED", "STALE", "IN_SAMPLE")},
    )


def _model_health(model_health: dict[str, Any], minimum: float) -> RuntimeHealthCheck:
    observed = _bounded(float((model_health or {}).get("score", 0.0)))
    threshold = _bounded(minimum)
    return RuntimeHealthCheck(
        name="model_health_status",
        passed=observed >= threshold and str((model_health or {}).get("status") or "OK") != "UNAVAILABLE",
        reasonCode="meta_strategy.inference.runtime_health.model_health_too_low",
        observed={"score": observed, "status": (model_health or {}).get("status")},
        threshold=threshold,
    )


def _artifact_age(
    model_artifact: dict[str, Any] | None,
    max_age_days: int | None,
    now: datetime | None,
) -> RuntimeHealthCheck:
    if max_age_days is None:
        return RuntimeHealthCheck(
            name="artifact_age",
            passed=True,
            reasonCode="meta_strategy.inference.runtime_health.artifact_too_old",
            observed="not_configured",
            threshold=None,
        )
    timestamp = _artifact_timestamp(model_artifact)
    if timestamp is None:
        return RuntimeHealthCheck(
            name="artifact_age",
            passed=False,
            reasonCode="meta_strategy.inference.runtime_health.artifact_age_missing",
            observed=None,
            threshold=max_age_days,
        )
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    age_days = max(0.0, (reference - timestamp).total_seconds() / 86400.0)
    return RuntimeHealthCheck(
        name="artifact_age",
        passed=age_days <= float(max_age_days),
        reasonCode="meta_strategy.inference.runtime_health.artifact_too_old",
        observed=round(age_days, 6),
        threshold=max_age_days,
    )


def _prediction_latency(
    started_at: datetime | None,
    finished_at: datetime | None,
    maximum_ms: int | None,
) -> RuntimeHealthCheck:
    if maximum_ms is None:
        return RuntimeHealthCheck(
            name="prediction_latency",
            passed=True,
            reasonCode="meta_strategy.inference.runtime_health.prediction_latency_too_high",
            observed="not_configured",
            threshold=None,
        )
    if started_at is None or finished_at is None:
        return RuntimeHealthCheck(
            name="prediction_latency",
            passed=False,
            reasonCode="meta_strategy.inference.runtime_health.prediction_latency_missing",
            observed=None,
            threshold=maximum_ms,
        )
    latency_ms = max(0.0, (finished_at - started_at).total_seconds() * 1000.0)
    return RuntimeHealthCheck(
        name="prediction_latency",
        passed=latency_ms <= float(maximum_ms),
        reasonCode="meta_strategy.inference.runtime_health.prediction_latency_too_high",
        observed=round(latency_ms, 6),
        threshold=maximum_ms,
    )


def _candidate_side_compatible(
    deterministic_signal: str,
    model: dict[str, Any] | None,
    candidate_conditional_output: Any | None,
) -> RuntimeHealthCheck:
    signal = str(deterministic_signal).upper()
    output_side = str(getattr(candidate_conditional_output, "candidate_side", signal)).upper()
    model_side = str((model or {}).get("candidateSide") or (model or {}).get("candidateSidePrediction") or signal).upper()
    allowed = {signal, "ANY", "BOTH", "CANDIDATE_CONDITIONAL"}
    passed = signal == "HOLD" or (output_side == signal and model_side in allowed)
    return RuntimeHealthCheck(
        name="candidate_side_compatibility",
        passed=passed,
        reasonCode="meta_strategy.inference.runtime_health.candidate_side_mismatch",
        observed={"deterministicSignal": signal, "modelSide": model_side, "outputSide": output_side},
        threshold={"allowedModelSides": tuple(sorted(allowed)), "requiredOutputSide": signal},
    )


def _artifact_timestamp(model_artifact: dict[str, Any] | None) -> datetime | None:
    if not model_artifact:
        return None
    for key in ("createdAt", "approvedAt", "artifactCreatedAt", "trainedAt"):
        value = model_artifact.get(key)
        if value:
            return _parse_datetime(value)
    window = model_artifact.get("trainingWindow") or {}
    if isinstance(window, dict) and window.get("end"):
        return _parse_datetime(window["end"])
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "RuntimeHealthCheck",
    "RuntimeHealthGateResult",
    "evaluate_runtime_health_gates",
]
