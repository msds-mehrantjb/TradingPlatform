"""Deterministic WCA confidence calibration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.algorithms.wca.contracts import (
    WcaConfidenceCalibrationBin,
    WcaConfidenceCalibrationOutcome,
    WcaConfidenceCalibrationTable,
    WcaEvaluationStatus,
    WcaSide,
    WcaStrategyEvaluation,
)


DEFAULT_CONFIDENCE_BINS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.000001))
DISABLED_CALIBRATION_VERSION = "wca_confidence_calibration_disabled_v1"


@dataclass(frozen=True)
class ConfidenceCalibrationConfig:
    enabled: bool = True
    minimum_samples: int = 30
    prior_success_rate: float = 0.50
    prior_strength: float = 20.0
    max_unseeded_confidence: float = 0.60
    calibration_version: str = "wca_confidence_calibration_beta_binomial_v1"
    bins: tuple[tuple[float, float], ...] = DEFAULT_CONFIDENCE_BINS


def build_calibration_table(
    *,
    strategy_id: str,
    strategy_version: str,
    outcomes: tuple[WcaConfidenceCalibrationOutcome, ...],
    as_of: datetime,
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
) -> WcaConfidenceCalibrationTable:
    eligible = tuple(
        outcome
        for outcome in outcomes
        if outcome.strategy_id == strategy_id
        and outcome.strategy_version == strategy_version
        and outcome.outcome_available_at < as_of
    )
    bins = tuple(_build_bin(lower, upper, eligible, config) for lower, upper in config.bins)
    return WcaConfidenceCalibrationTable(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        calibration_version=config.calibration_version,
        outcome_cutoff_timestamp=as_of,
        minimum_samples=config.minimum_samples,
        prior_success_rate=config.prior_success_rate,
        prior_strength=config.prior_strength,
        bins=bins,
    )


def calibrate_evaluation(
    evaluation: WcaStrategyEvaluation,
    *,
    table: WcaConfidenceCalibrationTable | None,
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
) -> WcaStrategyEvaluation:
    if not config.enabled:
        return evaluation.model_copy(
            update={
                "confidence": evaluation.raw_confidence,
                "calibrated_confidence": evaluation.raw_confidence,
                "calibration_version": DISABLED_CALIBRATION_VERSION,
            }
        )
    if evaluation.status != WcaEvaluationStatus.ACTIVE.value or evaluation.signal == WcaSide.HOLD.value:
        return evaluation.model_copy(
            update={
                "confidence": evaluation.raw_confidence,
                "calibrated_confidence": evaluation.raw_confidence,
                "calibration_version": table.calibration_version if table else DISABLED_CALIBRATION_VERSION,
            }
        )
    if table is None:
        calibrated = min(evaluation.raw_confidence, config.max_unseeded_confidence)
        return _with_calibrated_confidence(
            evaluation,
            calibrated,
            DISABLED_CALIBRATION_VERSION,
            ("wca.confidence_calibration.no_table",),
        )
    calibration_bin = _find_bin(evaluation.raw_confidence, table)
    if calibration_bin.sample_count < table.minimum_samples:
        calibrated = min(evaluation.raw_confidence, config.max_unseeded_confidence)
        return _with_calibrated_confidence(
            evaluation,
            calibrated,
            table.calibration_version,
            ("wca.confidence_calibration.insufficient_samples",),
        )
    return _with_calibrated_confidence(
        evaluation,
        calibration_bin.posterior_success_rate,
        table.calibration_version,
        ("wca.confidence_calibration.beta_binomial",),
    )


def calibrate_evaluations(
    evaluations: tuple[WcaStrategyEvaluation, ...],
    *,
    tables: tuple[WcaConfidenceCalibrationTable, ...],
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
) -> tuple[WcaStrategyEvaluation, ...]:
    table_by_key = {(table.strategy_id, table.strategy_version): table for table in tables}
    return tuple(
        calibrate_evaluation(evaluation, table=table_by_key.get((evaluation.strategy_id, evaluation.strategy_version)), config=config)
        for evaluation in evaluations
    )


def _build_bin(
    lower: float,
    upper: float,
    outcomes: tuple[WcaConfidenceCalibrationOutcome, ...],
    config: ConfidenceCalibrationConfig,
) -> WcaConfidenceCalibrationBin:
    selected = tuple(outcome for outcome in outcomes if lower <= outcome.raw_confidence < upper)
    sample_count = len(selected)
    success_count = sum(1 for outcome in selected if outcome.realized_success)
    posterior = (
        success_count + config.prior_success_rate * config.prior_strength
    ) / max(1.0, sample_count + config.prior_strength)
    return WcaConfidenceCalibrationBin(
        lower_bound=lower,
        upper_bound=min(1.0, upper),
        sample_count=sample_count,
        success_count=success_count,
        prior_success_rate=config.prior_success_rate,
        prior_strength=config.prior_strength,
        posterior_success_rate=round(max(0, min(1, posterior)), 4),
    )


def _find_bin(raw_confidence: float, table: WcaConfidenceCalibrationTable) -> WcaConfidenceCalibrationBin:
    for calibration_bin in table.bins:
        if calibration_bin.lower_bound <= raw_confidence < calibration_bin.upper_bound or (
            raw_confidence == 1.0 and calibration_bin.upper_bound == 1.0
        ):
            return calibration_bin
    return table.bins[-1]


def _with_calibrated_confidence(
    evaluation: WcaStrategyEvaluation,
    calibrated_confidence: float,
    calibration_version: str,
    reason_codes: tuple[str, ...],
) -> WcaStrategyEvaluation:
    calibrated = round(max(0, min(1, calibrated_confidence)), 4)
    direction = 1 if evaluation.signal == WcaSide.BUY.value else -1 if evaluation.signal == WcaSide.SELL.value else 0
    contribution = round(direction * evaluation.effective_weight * calibrated, 4)
    return evaluation.model_copy(
        update={
            "confidence": calibrated,
            "calibrated_confidence": calibrated,
            "calibration_version": calibration_version,
            "contribution": contribution,
            "reason_codes": (*evaluation.reason_codes, *reason_codes),
        }
    )

__all__ = (
    "ConfidenceCalibrationConfig",
    "DISABLED_CALIBRATION_VERSION",
    "WcaConfidenceCalibrationBin",
    "WcaConfidenceCalibrationOutcome",
    "WcaConfidenceCalibrationTable",
    "WcaStrategyEvaluation",
    "build_calibration_table",
    "calibrate_evaluation",
    "calibrate_evaluations",
)
