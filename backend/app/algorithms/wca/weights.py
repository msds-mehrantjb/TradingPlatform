"""Deterministic WCA strategy multiplier engine.

WCA runtime consumes immutable strategy multipliers. A neutral strategy has a
multiplier of 1.00; normalized shares are derived only for reporting and
family-concentration controls.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WcaStrategyPerformanceRecord,
    WcaStrategyWeightDetail,
    WcaWeightMaturityStage,
    WcaWeightSnapshot,
    WcaWeightVersionStatus,
)
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY


WCA_WEIGHT_MULTIPLIER_VERSION = "wca_statistical_weight_multipliers_v2"
WCA_WEIGHT_SNAPSHOT_SCHEMA_VERSION = "wca_weight_snapshot_v2"
WCA_WEIGHT_CALCULATION_CONFIG_VERSION = "wca_weight_calculation_config_v2"
WCA_WEIGHT_EPSILON = 1e-9


@dataclass(frozen=True)
class WcaWeightEngineConfig:
    neutral_multiplier: float = 1.00
    minimum_multiplier: float = 0.25
    maximum_multiplier: float = 2.00
    low_sample_upper_exclusive: int = 100
    limited_adjustment_lower: int = 100
    full_adjustment_minimum_signals: int = 300
    preferred_full_sample_signals: int = 500
    minimum_walk_forward_windows: int = 4
    minimum_distinct_calendar_months: int = 3
    minimum_distinct_market_regimes: int = 2
    bayesian_prior_signal_count: int = 200
    low_sample_minimum_multiplier: float = 0.90
    low_sample_maximum_multiplier: float = 1.10
    limited_minimum_multiplier: float = 0.65
    limited_maximum_multiplier: float = 1.35
    family_cap: float = 0.35
    minimum_correlation_overlap: int = 30
    high_correlation_threshold: float = 0.75
    maximum_correlation_penalty: float = 0.35
    maximum_profit_factor: float = 3.0
    maximum_abs_expected_edge: float = 1.0
    maximum_drawdown_r: float = 8.0
    maximum_cost_penalty: float = 0.35
    weight_version: str = WCA_WEIGHT_MULTIPLIER_VERSION
    cost_model_version: str = "wca_neutral_cost_model_adapter_v1"
    strategy_catalog_version: str = "wca_strategy_catalog_v2"
    build_version: str = WCA_WEIGHT_MULTIPLIER_VERSION
    # Backward-compatible constructor aliases. They are not used as shares.
    strategy_floor: float | None = None
    strategy_cap: float | None = None
    minimum_trade_count_full_weight: int | None = None
    bayesian_prior_trade_count: int | None = None
    recent_decay: float = 1.0
    max_profit_factor: float | None = None
    max_correlation_penalty: float | None = None

    def __post_init__(self) -> None:
        if self.strategy_floor is not None:
            object.__setattr__(self, "minimum_multiplier", self.strategy_floor)
        if self.strategy_cap is not None:
            object.__setattr__(self, "maximum_multiplier", self.strategy_cap)
        if self.minimum_trade_count_full_weight is not None:
            object.__setattr__(self, "full_adjustment_minimum_signals", self.minimum_trade_count_full_weight)
        if self.bayesian_prior_trade_count is not None:
            object.__setattr__(self, "bayesian_prior_signal_count", self.bayesian_prior_trade_count)
        if self.max_profit_factor is not None:
            object.__setattr__(self, "maximum_profit_factor", self.max_profit_factor)
        if self.max_correlation_penalty is not None:
            object.__setattr__(self, "maximum_correlation_penalty", self.max_correlation_penalty)


@dataclass(frozen=True)
class WcaWeightSystemComponent:
    component_id: str
    responsibility: str


WCA_WEIGHT_SYSTEM_INVENTORY: tuple[WcaWeightSystemComponent, ...] = (
    WcaWeightSystemComponent("baseline_weights", "Use WCA neutral base multipliers for every registered strategy."),
    WcaWeightSystemComponent("performance_derived_weights", "Derive WCA target multipliers from completed out-of-sample post-cost evidence."),
    WcaWeightSystemComponent("sample_size_reliability", "Scale WCA multiplier influence by valid out-of-sample signal count."),
    WcaWeightSystemComponent("shrinkage_toward_baseline", "Shrink immature WCA samples toward neutral 1.00."),
    WcaWeightSystemComponent("time_decay", "Give deterministic record ordering without using future outcomes."),
    WcaWeightSystemComponent("strategy_health", "Reduce WCA multiplier quality for drawdown, downside deviation, and instability."),
    WcaWeightSystemComponent("regime_adjustment", "Adjust WCA multiplier maturity by requiring multiple market regimes before full adjustment."),
    WcaWeightSystemComponent("correlation_penalties", "Penalize highly correlated WCA strategies using aligned observations."),
    WcaWeightSystemComponent("maximum_strategy_weight", "Cap every WCA strategy multiplier."),
    WcaWeightSystemComponent("maximum_family_concentration", "Cap WCA strategy-family normalized share concentration."),
    WcaWeightSystemComponent("versioned_weight_snapshots", "Emit immutable WCA multiplier snapshots with cutoff timestamps and versions."),
)

WCA_WEIGHT_SYSTEM_COMPONENT_IDS = frozenset(component.component_id for component in WCA_WEIGHT_SYSTEM_INVENTORY)


class WcaWeightConstraintError(ValueError):
    """Raised when WCA multiplier constraints cannot be satisfied."""


@dataclass(frozen=True)
class WcaMultiplierCandidate:
    strategy_id: str
    family: str
    multiplier: float
    minimum: float
    maximum: float


def equal_weight_snapshot() -> WcaWeightSnapshot:
    return baseline_weight_snapshot(reason_codes=("wca.weights.neutral_equal_multipliers",))


def baseline_weight_snapshot(
    *,
    cutoff: datetime | None = None,
    weight_version: str = WCA_WEIGHT_MULTIPLIER_VERSION,
    reason_codes: tuple[str, ...] = ("wca.weights.neutral_baseline_multipliers",),
) -> WcaWeightSnapshot:
    created = cutoff or datetime.now(timezone.utc)
    multipliers = {definition.strategy_id: 1.0 for definition in WCA_STRATEGY_REGISTRY}
    shares = _normalized_shares(multipliers)
    details = tuple(
        WcaStrategyWeightDetail(
            strategy_id=definition.strategy_id,
            family=definition.family,
            base_weight=definition.base_weight,
            maturity_stage=WcaWeightMaturityStage.UNTESTED,
            baseline_multiplier=1.0,
            target_multiplier=1.0,
            reliability_factor=0.0,
            sample_adjusted_multiplier=1.0,
            cost_adjusted_multiplier=1.0,
            correlation_adjusted_multiplier=1.0,
            correlation_factor=1.0,
            family_cap_factor=1.0,
            final_multiplier=1.0,
            final_weight=1.0,
            normalized_share=shares[definition.strategy_id],
            trade_count=0,
            allowed_multiplier_min=1.0,
            allowed_multiplier_max=1.0,
            metrics_cutoff_timestamp=created,
            weight_version=weight_version,
            reason_codes=reason_codes,
        )
        for definition in WCA_STRATEGY_REGISTRY
    )
    return _snapshot(
        weight_version=weight_version,
        created_at=created,
        cutoff=created,
        multipliers=multipliers,
        details=details,
        status=WcaWeightVersionStatus.ACTIVE,
        reason_codes=reason_codes,
    )


def performance_weight_snapshot(
    *,
    records: tuple[WcaStrategyPerformanceRecord, ...],
    cutoff: datetime,
    config: WcaWeightEngineConfig = WcaWeightEngineConfig(),
    regime: str = "default",
) -> WcaWeightSnapshot:
    valid_records, invalid_records = _records_before_cutoff(records, cutoff)
    by_strategy = {definition.strategy_id: tuple(record for record in valid_records if record.strategy_id == definition.strategy_id) for definition in WCA_STRATEGY_REGISTRY}
    correlation = _correlation_analysis(by_strategy, config)

    candidates: list[WcaMultiplierCandidate] = []
    draft_details: dict[str, WcaStrategyWeightDetail] = {}
    for definition in WCA_STRATEGY_REGISTRY:
        strategy_records = by_strategy[definition.strategy_id]
        invalid_for_strategy = tuple(record for record in invalid_records if record.strategy_id == definition.strategy_id)
        metrics = _strategy_metrics(strategy_records, config)
        maturity = _maturity_stage(strategy_records, invalid_for_strategy, config)
        allowed_min, allowed_max = _allowed_range(maturity, config)
        quality = _quality_score(metrics)
        target = _clamp(1.0 + 2.0 * (quality - 0.50), config.minimum_multiplier, config.maximum_multiplier)
        reliability = _reliability(len(strategy_records), config)
        sample_adjusted = 1.0 if not strategy_records else 1.0 + reliability * (target - 1.0)
        sample_adjusted = _clamp(sample_adjusted, allowed_min, allowed_max)
        cost_factor = _transaction_cost_factor(strategy_records, config)
        cost_adjusted = _clamp(1.0 + (sample_adjusted - 1.0) * cost_factor, allowed_min, allowed_max)
        corr_result = correlation[definition.strategy_id]
        correlation_adjusted = _clamp(1.0 + (cost_adjusted - 1.0) * corr_result["factor"], allowed_min, allowed_max)
        if maturity == WcaWeightMaturityStage.UNTESTED:
            correlation_adjusted = 1.0
        candidates.append(
            WcaMultiplierCandidate(
                strategy_id=definition.strategy_id,
                family=definition.family,
                multiplier=correlation_adjusted,
                minimum=allowed_min,
                maximum=allowed_max,
            )
        )
        draft_details[definition.strategy_id] = WcaStrategyWeightDetail(
            strategy_id=definition.strategy_id,
            family=definition.family,
            base_weight=definition.base_weight,
            maturity_stage=maturity,
            out_of_sample_signal_count=len(strategy_records),
            walk_forward_window_count=metrics["walk_forward_window_count"],
            distinct_month_count=metrics["distinct_month_count"],
            distinct_regime_count=metrics["distinct_regime_count"],
            baseline_multiplier=1.0,
            quality_score=quality,
            target_multiplier=target,
            reliability_factor=reliability,
            sample_adjusted_multiplier=sample_adjusted,
            cost_adjusted_multiplier=cost_adjusted,
            correlation_adjusted_multiplier=correlation_adjusted,
            correlation_factor=corr_result["factor"],
            family_cap_factor=1.0,
            final_multiplier=0.0,
            final_weight=0.0,
            normalized_share=0.0,
            trade_count=len(strategy_records),
            allowed_multiplier_min=allowed_min,
            allowed_multiplier_max=allowed_max,
            rolling_expectancy=metrics["net_expected_edge"],
            net_expected_edge=metrics["net_expected_edge"],
            profit_factor=metrics["profit_factor_after_costs"],
            profit_factor_after_costs=metrics["profit_factor_after_costs"],
            win_rate=metrics["win_rate"],
            directional_quality=metrics["directional_quality"],
            confidence_calibration=metrics["confidence_calibration"],
            walk_forward_stability=metrics["walk_forward_stability"],
            risk_quality=metrics["risk_quality"],
            average_r=metrics["average_r"],
            downside_deviation=metrics["downside_deviation"],
            maximum_drawdown=metrics["maximum_drawdown"],
            consecutive_losses=int(metrics["consecutive_losses"]),
            aligned_overlap_count=corr_result["aligned_overlap_count"],
            maximum_observed_correlation=corr_result["maximum_observed_correlation"],
            correlated_strategy_ids=tuple(corr_result["correlated_strategy_ids"]),
            correlation_reason_codes=tuple(corr_result["reason_codes"]),
            validation_results={
                "holdout_evaluation_passed": metrics["holdout_evaluation_passed"],
                "timestamp_integrity_passed": not invalid_for_strategy,
                "out_of_sample_only": True,
                "regime": regime,
            },
            supporting_metrics=metrics,
            metrics_cutoff_timestamp=cutoff,
            weight_version=config.weight_version,
            reason_codes=_detail_reason_codes(maturity, strategy_records, invalid_for_strategy, corr_result),
        )

    final_multipliers = bounded_mean_one_normalize(candidates, family_cap=config.family_cap)
    shares = _normalized_shares(final_multipliers)
    details = []
    for definition in WCA_STRATEGY_REGISTRY:
        original = draft_details[definition.strategy_id]
        before_family = max(WCA_WEIGHT_EPSILON, original.correlation_adjusted_multiplier)
        final_multiplier = final_multipliers[definition.strategy_id]
        details.append(
            original.model_copy(
                update={
                    "family_cap_factor": round(final_multiplier / before_family, 10),
                    "final_multiplier": final_multiplier,
                    "final_weight": final_multiplier,
                    "normalized_share": shares[definition.strategy_id],
                }
            )
        )

    payload_for_checksum = {
        "cutoff": cutoff.isoformat(),
        "config": _config_payload(config),
        "records": [_record_checksum_payload(record) for record in valid_records],
    }
    input_checksum = _checksum(payload_for_checksum)
    output_checksum = _checksum({strategy_id: final_multipliers[strategy_id] for strategy_id in sorted(final_multipliers)})
    return _snapshot(
        weight_version=config.weight_version,
        created_at=cutoff,
        cutoff=cutoff,
        multipliers=final_multipliers,
        details=tuple(details),
        status=WcaWeightVersionStatus.CANDIDATE,
        reason_codes=("wca.weights.performance_derived_multipliers", "wca.weights.promotion_required"),
        config=config,
        input_checksum=input_checksum,
        output_checksum=output_checksum,
        dataset_ids=tuple(sorted({record.dataset_id for record in valid_records if record.dataset_id})),
        replay_run_ids=tuple(sorted({record.replay_run_id for record in valid_records if record.replay_run_id})),
        walk_forward_window_ids=tuple(sorted({record.walk_forward_window_id for record in valid_records if record.walk_forward_window_id})),
        holdout_partition_ids=tuple(sorted({record.holdout_partition_id for record in valid_records if record.holdout_partition_id})),
    )


def adapt_v1_weight_snapshot_to_multipliers(snapshot: WcaWeightSnapshot) -> WcaWeightSnapshot:
    if snapshot.weight_schema_version == WCA_WEIGHT_SNAPSHOT_SCHEMA_VERSION or "multiplier" in snapshot.weight_version:
        return snapshot
    count = len(snapshot.weights)
    multipliers = {strategy_id: _clamp(weight * count, 0.25, 2.0) for strategy_id, weight in snapshot.weights.items()}
    candidates = tuple(
        WcaMultiplierCandidate(strategy_id=definition.strategy_id, family=definition.family, multiplier=multipliers.get(definition.strategy_id, 1.0), minimum=0.25, maximum=2.0)
        for definition in WCA_STRATEGY_REGISTRY
    )
    final = bounded_mean_one_normalize(candidates)
    shares = _normalized_shares(final)
    cutoff = snapshot.metrics_cutoff_timestamp or snapshot.created_at
    details = tuple(
        WcaStrategyWeightDetail(
            strategy_id=definition.strategy_id,
            family=definition.family,
            base_weight=definition.base_weight,
            maturity_stage=WcaWeightMaturityStage.LIMITED_ADJUSTMENT,
            baseline_multiplier=1.0,
            target_multiplier=final[definition.strategy_id],
            reliability_factor=0.0,
            sample_adjusted_multiplier=final[definition.strategy_id],
            cost_adjusted_multiplier=final[definition.strategy_id],
            correlation_adjusted_multiplier=final[definition.strategy_id],
            correlation_factor=1.0,
            final_multiplier=final[definition.strategy_id],
            final_weight=final[definition.strategy_id],
            normalized_share=shares[definition.strategy_id],
            trade_count=0,
            allowed_multiplier_min=0.25,
            allowed_multiplier_max=2.0,
            metrics_cutoff_timestamp=cutoff,
            weight_version=f"{snapshot.weight_version}.adapted_to_multipliers",
            reason_codes=("wca.weights.v1_share_snapshot_adapted",),
        )
        for definition in WCA_STRATEGY_REGISTRY
    )
    return _snapshot(
        weight_version=f"{snapshot.weight_version}.adapted_to_multipliers",
        created_at=snapshot.created_at,
        cutoff=cutoff,
        multipliers=final,
        details=details,
        status=WcaWeightVersionStatus.ACTIVE,
        reason_codes=(*snapshot.reason_codes, "wca.weights.v1_share_snapshot_adapted"),
    )


def bounded_mean_one_normalize(candidates: Iterable[WcaMultiplierCandidate], *, family_cap: float = 0.35) -> dict[str, float]:
    ordered = tuple(sorted(candidates, key=lambda item: item.strategy_id))
    if not ordered:
        raise WcaWeightConstraintError("wca.weights.no_candidates")
    target_total = float(len(ordered))
    mins = {item.strategy_id: item.minimum for item in ordered}
    maxes = {item.strategy_id: item.maximum for item in ordered}
    if sum(mins.values()) > target_total + WCA_WEIGHT_EPSILON or sum(maxes.values()) < target_total - WCA_WEIGHT_EPSILON:
        raise WcaWeightConstraintError("wca.weights.infeasible_bounds")
    values = {item.strategy_id: _clamp(item.multiplier, item.minimum, item.maximum) for item in ordered}
    values = _project_to_total(values, mins, maxes, target_total)
    values = _apply_family_caps_to_multipliers(values, ordered, mins, maxes, target_total, family_cap)
    values = _project_to_total(values, mins, maxes, target_total)
    _assert_multiplier_invariants(values, mins, maxes)
    rounded = {strategy_id: round(values[strategy_id], 10) for strategy_id in sorted(values)}
    drift = round(target_total - sum(rounded.values()), 10)
    if abs(drift) > 0:
        for strategy_id in sorted(rounded):
            candidate = rounded[strategy_id] + drift
            if mins[strategy_id] - WCA_WEIGHT_EPSILON <= candidate <= maxes[strategy_id] + WCA_WEIGHT_EPSILON:
                rounded[strategy_id] = round(candidate, 10)
                break
    _assert_multiplier_invariants(rounded, mins, maxes)
    return rounded


def _snapshot(
    *,
    weight_version: str,
    created_at: datetime,
    cutoff: datetime,
    multipliers: dict[str, float],
    details: tuple[WcaStrategyWeightDetail, ...],
    status: WcaWeightVersionStatus,
    reason_codes: tuple[str, ...],
    config: WcaWeightEngineConfig | None = None,
    input_checksum: str = "",
    output_checksum: str = "",
    dataset_ids: tuple[str, ...] = (),
    replay_run_ids: tuple[str, ...] = (),
    walk_forward_window_ids: tuple[str, ...] = (),
    holdout_partition_ids: tuple[str, ...] = (),
) -> WcaWeightSnapshot:
    active_config = config or WcaWeightEngineConfig(weight_version=weight_version)
    return WcaWeightSnapshot(
        weight_version=weight_version,
        weight_schema_version=WCA_WEIGHT_SNAPSHOT_SCHEMA_VERSION,
        created_at=created_at,
        weights=multipliers,
        details=details,
        metrics_cutoff_timestamp=cutoff,
        status=status,
        calculation_config_version=WCA_WEIGHT_CALCULATION_CONFIG_VERSION,
        strategy_catalog_version=active_config.strategy_catalog_version,
        cost_model_version=active_config.cost_model_version,
        dataset_ids=dataset_ids,
        replay_run_ids=replay_run_ids,
        walk_forward_window_ids=walk_forward_window_ids,
        holdout_partition_ids=holdout_partition_ids,
        build_version=active_config.build_version,
        input_checksum=input_checksum,
        output_checksum=output_checksum,
        reason_codes=reason_codes,
    )


def _records_before_cutoff(records: tuple[WcaStrategyPerformanceRecord, ...], cutoff: datetime) -> tuple[tuple[WcaStrategyPerformanceRecord, ...], tuple[WcaStrategyPerformanceRecord, ...]]:
    cutoff_utc = cutoff.astimezone(timezone.utc)
    valid: list[WcaStrategyPerformanceRecord] = []
    invalid: list[WcaStrategyPerformanceRecord] = []
    for record in records:
        available = record.outcome_available_at.astimezone(timezone.utc)
        if available >= cutoff_utc:
            continue
        if record.in_sample or not record.out_of_sample:
            continue
        if record.data_leakage_detected or not record.timestamp_integrity_passed:
            invalid.append(record)
            continue
        valid.append(record)
    return tuple(sorted(valid, key=lambda item: (_observation_key(item), item.outcome_available_at, item.strategy_id))), tuple(invalid)


def _maturity_stage(
    records: tuple[WcaStrategyPerformanceRecord, ...],
    invalid_records: tuple[WcaStrategyPerformanceRecord, ...],
    config: WcaWeightEngineConfig,
) -> WcaWeightMaturityStage:
    if invalid_records:
        return WcaWeightMaturityStage.INELIGIBLE
    count = len(records)
    if count == 0:
        return WcaWeightMaturityStage.UNTESTED
    if count < config.low_sample_upper_exclusive:
        return WcaWeightMaturityStage.LOW_SAMPLE
    metrics = _coverage_metrics(records)
    full_ready = (
        count >= config.full_adjustment_minimum_signals
        and metrics["walk_forward_window_count"] >= config.minimum_walk_forward_windows
        and metrics["distinct_month_count"] >= config.minimum_distinct_calendar_months
        and metrics["distinct_regime_count"] >= config.minimum_distinct_market_regimes
        and metrics["holdout_evaluation_passed"]
    )
    return WcaWeightMaturityStage.FULL_ADJUSTMENT if full_ready else WcaWeightMaturityStage.LIMITED_ADJUSTMENT


def _allowed_range(stage: WcaWeightMaturityStage, config: WcaWeightEngineConfig) -> tuple[float, float]:
    if stage == WcaWeightMaturityStage.INELIGIBLE:
        return config.neutral_multiplier, config.neutral_multiplier
    if stage == WcaWeightMaturityStage.UNTESTED:
        return config.neutral_multiplier, config.neutral_multiplier
    if stage == WcaWeightMaturityStage.LOW_SAMPLE:
        return config.low_sample_minimum_multiplier, config.low_sample_maximum_multiplier
    if stage == WcaWeightMaturityStage.LIMITED_ADJUSTMENT:
        return config.limited_minimum_multiplier, config.limited_maximum_multiplier
    return config.minimum_multiplier, config.maximum_multiplier


def _strategy_metrics(records: tuple[WcaStrategyPerformanceRecord, ...], config: WcaWeightEngineConfig) -> dict[str, float | int | bool]:
    coverage = _coverage_metrics(records)
    if not records:
        return {
            **coverage,
            "net_expected_edge": 0.0,
            "profit_factor_after_costs": 1.0,
            "win_rate": 0.0,
            "average_r": 0.0,
            "downside_deviation": 0.0,
            "maximum_drawdown": 0.0,
            "consecutive_losses": 0,
            "directional_quality": 0.5,
            "confidence_calibration": 0.5,
            "walk_forward_stability": 0.5,
            "risk_quality": 0.5,
            "net_edge_score": 0.5,
            "profit_factor_score": 0.5,
        }
    net_values = tuple(_net_record_value(record) for record in records)
    wins = tuple(value for value in net_values if value > 0)
    losses = tuple(value for value in net_values if value < 0)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = config.maximum_profit_factor if gross_loss <= 0 and gross_win > 0 else gross_win / gross_loss if gross_loss > 0 else 1.0
    expectancy = sum(net_values) / len(net_values)
    win_rate = len(wins) / len(net_values)
    downside = math.sqrt(sum(value * value for value in losses) / len(losses)) if losses else 0.0
    max_drawdown = _max_drawdown(net_values)
    directional_quality = sum(1 for record in records if _direction_correct(record)) / len(records)
    confidence_calibration = 1.0 - min(1.0, sum(abs(float(record.confidence) - (1.0 if record.success else 0.0)) for record in records) / len(records))
    stability = _walk_forward_stability(records)
    risk_quality = 1.0 - min(1.0, max_drawdown / max(WCA_WEIGHT_EPSILON, config.maximum_drawdown_r))
    net_edge_score = _clamp(0.5 + expectancy / max(WCA_WEIGHT_EPSILON, 2 * config.maximum_abs_expected_edge), 0.0, 1.0)
    profit_factor_score = _clamp(profit_factor / config.maximum_profit_factor, 0.0, 1.0)
    return {
        **coverage,
        "net_expected_edge": round(expectancy, 10),
        "profit_factor_after_costs": round(min(config.maximum_profit_factor, profit_factor), 10),
        "win_rate": round(win_rate, 10),
        "average_r": round(expectancy, 10),
        "downside_deviation": round(downside, 10),
        "maximum_drawdown": round(max_drawdown, 10),
        "consecutive_losses": _max_consecutive_losses(net_values),
        "directional_quality": round(directional_quality, 10),
        "confidence_calibration": round(confidence_calibration, 10),
        "walk_forward_stability": round(stability, 10),
        "risk_quality": round(risk_quality, 10),
        "net_edge_score": round(net_edge_score, 10),
        "profit_factor_score": round(profit_factor_score, 10),
    }


def _coverage_metrics(records: tuple[WcaStrategyPerformanceRecord, ...]) -> dict[str, int | bool]:
    months = {record.decision_timestamp.strftime("%Y-%m") for record in records}
    regimes = {record.market_regime or record.regime for record in records}
    windows = {record.walk_forward_window_id for record in records if record.walk_forward_window_id}
    holdouts = {record.holdout_partition_id for record in records if record.holdout_partition_id}
    return {
        "walk_forward_window_count": len(windows),
        "distinct_month_count": len(months),
        "distinct_regime_count": len(regimes),
        "holdout_evaluation_passed": bool(holdouts) and all(record.holdout_evaluation_passed for record in records if record.holdout_partition_id),
    }


def _quality_score(metrics: dict[str, float | int | bool]) -> float:
    quality = (
        0.30 * float(metrics["net_edge_score"])
        + 0.15 * float(metrics["profit_factor_score"])
        + 0.15 * float(metrics["directional_quality"])
        + 0.15 * float(metrics["confidence_calibration"])
        + 0.15 * float(metrics["walk_forward_stability"])
        + 0.10 * float(metrics["risk_quality"])
    )
    return round(_clamp(quality, 0.0, 1.0), 10)


def _reliability(count: int, config: WcaWeightEngineConfig) -> float:
    if count <= 0:
        return 0.0
    return round(count / (count + config.bayesian_prior_signal_count), 10)


def _transaction_cost_factor(records: tuple[WcaStrategyPerformanceRecord, ...], config: WcaWeightEngineConfig) -> float:
    if not records:
        return 1.0
    gross = sum(abs(record.gross_r_multiple or record.r_multiple) for record in records)
    costs = sum(record.total_transaction_cost or (record.spread_cost + record.fee_cost + record.slippage_cost + record.market_impact_cost) for record in records)
    if gross <= WCA_WEIGHT_EPSILON:
        return 1.0
    penalty = min(config.maximum_cost_penalty, costs / gross)
    return round(1.0 - penalty, 10)


def _correlation_analysis(
    by_strategy: dict[str, tuple[WcaStrategyPerformanceRecord, ...]],
    config: WcaWeightEngineConfig,
) -> dict[str, dict[str, object]]:
    results = {
        definition.strategy_id: {
            "factor": 1.0,
            "aligned_overlap_count": 0,
            "maximum_observed_correlation": 0.0,
            "correlated_strategy_ids": (),
            "reason_codes": ("wca.weights.correlation.insufficient_aligned_overlap",),
        }
        for definition in WCA_STRATEGY_REGISTRY
    }
    keyed = {
        strategy_id: {_observation_key(record): _net_record_value(record) for record in records}
        for strategy_id, records in by_strategy.items()
    }
    for left in sorted(keyed):
        max_corr = 0.0
        max_overlap = 0
        correlated: list[str] = []
        penalty = 0.0
        for right in sorted(keyed):
            if left == right:
                continue
            common_keys = tuple(sorted(set(keyed[left]) & set(keyed[right])))
            overlap = len(common_keys)
            max_overlap = max(max_overlap, overlap)
            if overlap < config.minimum_correlation_overlap:
                continue
            corr = _correlation(tuple(keyed[left][key] for key in common_keys), tuple(keyed[right][key] for key in common_keys))
            max_corr = max(max_corr, corr)
            if corr > config.high_correlation_threshold:
                excess = (corr - config.high_correlation_threshold) / max(WCA_WEIGHT_EPSILON, 1.0 - config.high_correlation_threshold)
                penalty = max(penalty, min(config.maximum_correlation_penalty, excess * config.maximum_correlation_penalty))
                correlated.append(right)
        if penalty > 0:
            results[left] = {
                "factor": round(1.0 - penalty, 10),
                "aligned_overlap_count": max_overlap,
                "maximum_observed_correlation": round(max_corr, 10),
                "correlated_strategy_ids": tuple(correlated),
                "reason_codes": ("wca.weights.correlation.aligned_penalty",),
            }
        elif max_overlap >= config.minimum_correlation_overlap:
            results[left] = {
                "factor": 1.0,
                "aligned_overlap_count": max_overlap,
                "maximum_observed_correlation": round(max_corr, 10),
                "correlated_strategy_ids": (),
                "reason_codes": ("wca.weights.correlation.no_high_correlation",),
            }
    return results


def _apply_family_caps_to_multipliers(
    values: dict[str, float],
    ordered: tuple[WcaMultiplierCandidate, ...],
    mins: dict[str, float],
    maxes: dict[str, float],
    target_total: float,
    family_cap: float,
) -> dict[str, float]:
    if family_cap <= 0 or family_cap >= 1:
        return values
    family_by_strategy = {item.strategy_id: item.family for item in ordered}
    family_limit = family_cap * target_total
    capped = dict(values)
    for _ in range(12):
        family_totals: dict[str, float] = defaultdict(float)
        for strategy_id, value in capped.items():
            family_totals[family_by_strategy[strategy_id]] += value
        over = {family: total for family, total in family_totals.items() if total > family_limit + WCA_WEIGHT_EPSILON}
        if not over:
            return capped
        for family, total in over.items():
            members = tuple(strategy_id for strategy_id in sorted(capped) if family_by_strategy[strategy_id] == family)
            min_total = sum(mins[strategy_id] for strategy_id in members)
            if min_total > family_limit + WCA_WEIGHT_EPSILON:
                raise WcaWeightConstraintError(f"wca.weights.infeasible_family_cap:{family}")
            scale_target = family_limit - min_total
            movable = sum(capped[strategy_id] - mins[strategy_id] for strategy_id in members)
            if movable <= WCA_WEIGHT_EPSILON:
                raise WcaWeightConstraintError(f"wca.weights.infeasible_family_cap:{family}")
            for strategy_id in members:
                capped[strategy_id] = mins[strategy_id] + (capped[strategy_id] - mins[strategy_id]) * scale_target / movable
        capped = _project_to_total(capped, mins, maxes, target_total)
    return capped


def _project_to_total(values: dict[str, float], mins: dict[str, float], maxes: dict[str, float], target_total: float) -> dict[str, float]:
    projected = {strategy_id: _clamp(values[strategy_id], mins[strategy_id], maxes[strategy_id]) for strategy_id in values}
    for _ in range(100):
        delta = target_total - sum(projected.values())
        if abs(delta) <= WCA_WEIGHT_EPSILON:
            return projected
        if delta > 0:
            eligible = tuple(strategy_id for strategy_id in sorted(projected) if projected[strategy_id] < maxes[strategy_id] - WCA_WEIGHT_EPSILON)
            capacity = sum(maxes[strategy_id] - projected[strategy_id] for strategy_id in eligible)
            if not eligible or capacity + WCA_WEIGHT_EPSILON < delta:
                raise WcaWeightConstraintError("wca.weights.infeasible_upper_bounds")
            for strategy_id in eligible:
                projected[strategy_id] += delta * ((maxes[strategy_id] - projected[strategy_id]) / capacity)
        else:
            need = -delta
            eligible = tuple(strategy_id for strategy_id in sorted(projected) if projected[strategy_id] > mins[strategy_id] + WCA_WEIGHT_EPSILON)
            capacity = sum(projected[strategy_id] - mins[strategy_id] for strategy_id in eligible)
            if not eligible or capacity + WCA_WEIGHT_EPSILON < need:
                raise WcaWeightConstraintError("wca.weights.infeasible_lower_bounds")
            for strategy_id in eligible:
                projected[strategy_id] -= need * ((projected[strategy_id] - mins[strategy_id]) / capacity)
    raise WcaWeightConstraintError("wca.weights.normalization_did_not_converge")


def _normalized_shares(multipliers: dict[str, float]) -> dict[str, float]:
    total = sum(multipliers.values())
    if total <= 0:
        raise WcaWeightConstraintError("wca.weights.invalid_share_total")
    shares = {strategy_id: round(multiplier / total, 10) for strategy_id, multiplier in sorted(multipliers.items())}
    drift = round(1.0 - sum(shares.values()), 10)
    if abs(drift) > 0:
        first = next(iter(shares))
        shares[first] = round(shares[first] + drift, 10)
    return shares


def _assert_multiplier_invariants(values: dict[str, float], mins: dict[str, float], maxes: dict[str, float]) -> None:
    if any(not math.isfinite(value) for value in values.values()):
        raise WcaWeightConstraintError("wca.weights.non_finite_multiplier")
    for strategy_id, value in values.items():
        if value < mins[strategy_id] - 1e-7 or value > maxes[strategy_id] + 1e-7:
            raise WcaWeightConstraintError(f"wca.weights.bound_violation:{strategy_id}")
    mean = sum(values.values()) / len(values)
    if abs(mean - 1.0) > 1e-7:
        raise WcaWeightConstraintError("wca.weights.mean_not_one")


def _detail_reason_codes(
    maturity: WcaWeightMaturityStage,
    records: tuple[WcaStrategyPerformanceRecord, ...],
    invalid_records: tuple[WcaStrategyPerformanceRecord, ...],
    corr_result: dict[str, object],
) -> tuple[str, ...]:
    reasons = ["wca.weights.multiplier_calculated", f"wca.weights.maturity.{maturity.value.lower()}"]
    if not records:
        reasons.append("wca.weights.neutral_no_oos_history")
    if maturity in {WcaWeightMaturityStage.LOW_SAMPLE, WcaWeightMaturityStage.LIMITED_ADJUSTMENT}:
        reasons.append("wca.weights.sample_shrinkage_applied")
        reasons.append("wca.weights.shrunk_to_baseline")
    if invalid_records:
        reasons.append("wca.weights.invalid_evidence_ineligible")
    reasons.extend(str(code) for code in corr_result["reason_codes"])
    return tuple(reasons)


def _walk_forward_stability(records: tuple[WcaStrategyPerformanceRecord, ...]) -> float:
    by_window: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.walk_forward_window_id:
            by_window[record.walk_forward_window_id].append(float(record.net_r_multiple if record.net_r_multiple is not None else record.r_multiple))
    if len(by_window) < 2:
        return 0.5
    means = [sum(values) / len(values) for values in by_window.values()]
    dispersion = max(means) - min(means)
    return round(1.0 / (1.0 + max(0.0, dispersion)), 10)


def _direction_correct(record: WcaStrategyPerformanceRecord) -> bool:
    if record.predicted_direction and record.realized_direction:
        return record.predicted_direction.upper() == record.realized_direction.upper()
    return bool(record.success)


def _net_record_value(record: WcaStrategyPerformanceRecord) -> float:
    raw_net = float(record.net_r_multiple if record.net_r_multiple is not None else record.r_multiple)
    explicit_cost = float(record.total_transaction_cost or 0)
    return raw_net - explicit_cost


def _observation_key(record: WcaStrategyPerformanceRecord) -> str:
    if record.evaluation_id:
        return record.evaluation_id
    if record.signal_id:
        return record.signal_id
    if record.decision_bar_timestamp:
        return record.decision_bar_timestamp.isoformat()
    return record.decision_timestamp.isoformat()


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    count = min(len(left), len(right))
    if count < 2:
        return 0.0
    mean_x = sum(left) / count
    mean_y = sum(right) / count
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(left, right))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in left))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in right))
    if denom_x <= WCA_WEIGHT_EPSILON or denom_y <= WCA_WEIGHT_EPSILON:
        return 0.0
    return max(-1.0, min(1.0, numerator / (denom_x * denom_y)))


def _max_drawdown(values: tuple[float, ...]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _max_consecutive_losses(values: tuple[float, ...]) -> int:
    current = 0
    longest = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _record_checksum_payload(record: WcaStrategyPerformanceRecord) -> dict[str, object]:
    return {
        "strategy_id": record.strategy_id,
        "evaluation_id": record.evaluation_id,
        "decision_bar_timestamp": record.decision_bar_timestamp.isoformat() if record.decision_bar_timestamp else "",
        "outcome_available_at": record.outcome_available_at.isoformat(),
        "net_r_multiple": record.net_r_multiple,
        "total_transaction_cost": record.total_transaction_cost,
    }


def _config_payload(config: WcaWeightEngineConfig) -> dict[str, object]:
    return {key: value for key, value in config.__dict__.items() if value is not None}


def _checksum(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


__all__ = (
    "WCA_WEIGHT_CALCULATION_CONFIG_VERSION",
    "WCA_WEIGHT_MULTIPLIER_VERSION",
    "WCA_WEIGHT_SNAPSHOT_SCHEMA_VERSION",
    "WCA_WEIGHT_SYSTEM_COMPONENT_IDS",
    "WCA_WEIGHT_SYSTEM_INVENTORY",
    "WcaMultiplierCandidate",
    "WcaWeightConstraintError",
    "WcaWeightEngineConfig",
    "WcaWeightSystemComponent",
    "adapt_v1_weight_snapshot_to_multipliers",
    "baseline_weight_snapshot",
    "bounded_mean_one_normalize",
    "equal_weight_snapshot",
    "performance_weight_snapshot",
)
