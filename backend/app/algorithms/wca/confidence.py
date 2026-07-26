"""Deterministic WCA confidence calibration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    direction_minimum_samples: int = 30
    regime_minimum_samples: int = 50
    prior_success_rate: float = 0.50
    prior_strength: float = 20.0
    max_unseeded_confidence: float = 0.60
    max_table_age_days: int = 30
    calibration_version: str = "wca_confidence_calibration_beta_binomial_v1"
    bins: tuple[tuple[float, float], ...] = DEFAULT_CONFIDENCE_BINS


def build_calibration_table(
    *,
    strategy_id: str,
    strategy_version: str,
    outcomes: tuple[WcaConfidenceCalibrationOutcome, ...],
    as_of: datetime,
    direction: WcaSide | str | None = None,
    regime: str | None = None,
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
) -> WcaConfidenceCalibrationTable:
    cutoff = as_of.astimezone(timezone.utc)
    eligible_base = tuple(
        outcome
        for outcome in outcomes
        if outcome.strategy_id == strategy_id
        and outcome.strategy_version == strategy_version
        and outcome.outcome_available_at.astimezone(timezone.utc) < cutoff
        and outcome.decision_timestamp.astimezone(timezone.utc) < cutoff
    )
    reason_codes = ["wca.confidence_calibration.versioned_prior_outcomes"]
    selected_direction = _direction_value(direction)
    direction_records = tuple(outcome for outcome in eligible_base if _direction_value(outcome.direction) == selected_direction) if selected_direction else ()
    if selected_direction and len(direction_records) >= config.direction_minimum_samples:
        eligible = direction_records
        scope = "strategy_version_direction"
        reason_codes.append("wca.confidence_calibration.direction_specific")
    else:
        eligible = eligible_base
        scope = "strategy_version"
        if selected_direction:
            reason_codes.append("wca.confidence_calibration.direction_insufficient_samples")

    selected_regime = regime if regime and regime != "default" else None
    regime_records = tuple(outcome for outcome in eligible if outcome.regime == selected_regime) if selected_regime else ()
    if selected_regime and len(regime_records) >= config.regime_minimum_samples:
        eligible = regime_records
        scope = f"{scope}_regime"
        reason_codes.append("wca.confidence_calibration.regime_specific")
    elif selected_regime:
        reason_codes.append("wca.confidence_calibration.regime_insufficient_samples")

    if len(eligible) < config.minimum_samples:
        reason_codes.append("wca.confidence_calibration.insufficient_samples")
    bins = tuple(_build_bin(lower, upper, eligible, config) for lower, upper in config.bins)
    return WcaConfidenceCalibrationTable(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        direction=selected_direction if "direction_specific" in " ".join(reason_codes) else None,
        regime=selected_regime if "regime_specific" in " ".join(reason_codes) else None,
        calibration_version=_calibration_version(config, strategy_id, strategy_version, cutoff, selected_direction if "direction_specific" in " ".join(reason_codes) else None, selected_regime if "regime_specific" in " ".join(reason_codes) else None),
        created_at=cutoff,
        outcome_cutoff_timestamp=cutoff,
        minimum_samples=config.minimum_samples,
        prior_success_rate=config.prior_success_rate,
        prior_strength=config.prior_strength,
        sample_scope=scope,
        sample_count=len(eligible),
        bins=bins,
        reason_codes=tuple(reason_codes),
    )


def conservative_fallback_calibration_table(
    *,
    strategy_id: str,
    strategy_version: str,
    as_of: datetime,
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
) -> WcaConfidenceCalibrationTable:
    cutoff = as_of.astimezone(timezone.utc)
    bins = tuple(
        WcaConfidenceCalibrationBin(
            lower_bound=lower,
            upper_bound=min(1.0, upper),
            sample_count=0,
            success_count=0,
            prior_success_rate=config.prior_success_rate,
            prior_strength=config.prior_strength,
            posterior_success_rate=round(min(config.max_unseeded_confidence, config.prior_success_rate), 4),
        )
        for lower, upper in config.bins
    )
    return WcaConfidenceCalibrationTable(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        calibration_version=f"wca_confidence_calibration_conservative_fallback_v1:{strategy_id}:{strategy_version}",
        created_at=cutoff,
        outcome_cutoff_timestamp=cutoff,
        minimum_samples=config.minimum_samples,
        prior_success_rate=config.prior_success_rate,
        prior_strength=config.prior_strength,
        sample_scope="conservative_fallback",
        sample_count=0,
        bins=bins,
        reason_codes=("wca.confidence_calibration.conservative_fallback_table", "wca.confidence_calibration.insufficient_samples"),
    )


def calibrate_evaluation(
    evaluation: WcaStrategyEvaluation,
    *,
    table: WcaConfidenceCalibrationTable | None,
    config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
    decision_timestamp: datetime | None = None,
) -> WcaStrategyEvaluation:
    calibration_input = evaluation.confidence
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
        calibrated = min(calibration_input, config.max_unseeded_confidence)
        return _with_calibrated_confidence(
            evaluation,
            calibrated,
            DISABLED_CALIBRATION_VERSION,
            ("wca.confidence_calibration.no_table",),
        )
    if not _table_usable_for_decision(table, decision_timestamp, config):
        calibrated = min(calibration_input, config.max_unseeded_confidence)
        return _with_calibrated_confidence(
            evaluation,
            calibrated,
            table.calibration_version,
            ("wca.confidence_calibration.table_unusable_or_stale",),
        )
    calibration_bin = _find_bin(calibration_input, table)
    if calibration_bin.sample_count < table.minimum_samples:
        calibrated = min(calibration_input, config.max_unseeded_confidence)
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
    decision_timestamp: datetime | None = None,
    regime: str = "default",
) -> tuple[WcaStrategyEvaluation, ...]:
    table_by_key = _select_tables(tables, decision_timestamp=decision_timestamp, regime=regime)
    return tuple(
        calibrate_evaluation(
            evaluation,
            table=table_by_key.get((evaluation.strategy_id, evaluation.strategy_version, _direction_value(evaluation.signal)))
            or table_by_key.get((evaluation.strategy_id, evaluation.strategy_version, None)),
            config=config,
            decision_timestamp=decision_timestamp,
        )
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


def _select_tables(
    tables: tuple[WcaConfidenceCalibrationTable, ...],
    *,
    decision_timestamp: datetime | None,
    regime: str,
) -> dict[tuple[str, str, str | None], WcaConfidenceCalibrationTable]:
    selected: dict[tuple[str, str, str | None], WcaConfidenceCalibrationTable] = {}
    for table in sorted(tables, key=lambda row: (row.outcome_cutoff_timestamp, row.created_at, row.calibration_version)):
        if decision_timestamp is not None and (
            table.outcome_cutoff_timestamp.astimezone(timezone.utc) > decision_timestamp.astimezone(timezone.utc)
            or table.created_at.astimezone(timezone.utc) > decision_timestamp.astimezone(timezone.utc)
        ):
            continue
        if table.regime is not None and table.regime != regime:
            continue
        key = (table.strategy_id, table.strategy_version, _direction_value(table.direction))
        selected[key] = table
    return selected


def _table_usable_for_decision(
    table: WcaConfidenceCalibrationTable,
    decision_timestamp: datetime | None,
    config: ConfidenceCalibrationConfig,
) -> bool:
    if decision_timestamp is None:
        return True
    decision = decision_timestamp.astimezone(timezone.utc)
    if table.outcome_cutoff_timestamp.astimezone(timezone.utc) > decision or table.created_at.astimezone(timezone.utc) > decision:
        return False
    return decision - table.created_at.astimezone(timezone.utc) <= timedelta(days=config.max_table_age_days)


def _calibration_version(
    config: ConfidenceCalibrationConfig,
    strategy_id: str,
    strategy_version: str,
    as_of: datetime,
    direction: str | None,
    regime: str | None,
) -> str:
    digest = hashlib.sha256(f"{strategy_id}:{strategy_version}:{direction or 'all'}:{regime or 'all'}:{as_of.isoformat()}:{config.minimum_samples}:{config.prior_success_rate}:{config.prior_strength}".encode("utf-8")).hexdigest()[:12]
    return f"{config.calibration_version}:{strategy_id}:{strategy_version}:{direction or 'all'}:{regime or 'all'}:{digest}"


def _direction_value(direction: WcaSide | str | None) -> str | None:
    if direction is None:
        return None
    value = direction.value if isinstance(direction, WcaSide) else str(direction)
    return None if value == WcaSide.HOLD.value else value

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
    "conservative_fallback_calibration_table",
)
