"""Deterministic performance-derived WCA weight engine."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.algorithms.wca.contracts import WcaStrategyPerformanceRecord, WcaStrategyWeightDetail, WcaWeightSnapshot
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY


@dataclass(frozen=True)
class WcaWeightEngineConfig:
    strategy_floor: float = 0.02
    strategy_cap: float = 0.18
    family_cap: float = 0.35
    minimum_trade_count_full_weight: int = 40
    bayesian_prior_trade_count: int = 40
    recent_decay: float = 0.94
    max_profit_factor: float = 3.0
    max_expectancy_bonus: float = 0.40
    max_drawdown_penalty_r: float = 8.0
    max_consecutive_loss_penalty: int = 8
    high_correlation_threshold: float = 0.75
    max_correlation_penalty: float = 0.35
    weight_version: str = "wca_statistical_weights_v1"


def equal_weight_snapshot() -> WcaWeightSnapshot:
    weight = 1.0 / len(WCA_STRATEGY_REGISTRY)
    return WcaWeightSnapshot(weights={definition.strategy_id: weight for definition in WCA_STRATEGY_REGISTRY})


def baseline_weight_snapshot(*, cutoff: datetime | None = None, weight_version: str = "wca_static_baseline_weights_v1") -> WcaWeightSnapshot:
    created = cutoff or datetime.now(timezone.utc)
    weights = _normalize({definition.strategy_id: definition.base_weight for definition in WCA_STRATEGY_REGISTRY})
    details = tuple(
        WcaStrategyWeightDetail(
            strategy_id=definition.strategy_id,
            family=definition.family,
            base_weight=definition.base_weight,
            performance_factor=1,
            reliability_factor=0,
            regime_factor=1,
            health_factor=1,
            correlation_factor=1,
            final_weight=weights[definition.strategy_id],
            trade_count=0,
            metrics_cutoff_timestamp=created,
            weight_version=weight_version,
            reason_codes=("wca.weights.static_baseline",),
        )
        for definition in WCA_STRATEGY_REGISTRY
    )
    return WcaWeightSnapshot(
        weight_version=weight_version,
        created_at=created,
        weights=weights,
        details=details,
        metrics_cutoff_timestamp=created,
        reason_codes=("wca.weights.static_baseline",),
    )


def performance_weight_snapshot(
    *,
    records: tuple[WcaStrategyPerformanceRecord, ...],
    cutoff: datetime,
    config: WcaWeightEngineConfig = WcaWeightEngineConfig(),
    regime: str = "default",
) -> WcaWeightSnapshot:
    completed = tuple(record for record in records if record.outcome_available_at < cutoff)
    by_strategy = {definition.strategy_id: tuple(record for record in completed if record.strategy_id == definition.strategy_id) for definition in WCA_STRATEGY_REGISTRY}
    correlation_factors = _correlation_factors(by_strategy, config)

    unscaled: dict[str, float] = {}
    draft_details: list[WcaStrategyWeightDetail] = []
    for definition in WCA_STRATEGY_REGISTRY:
        strategy_records = by_strategy[definition.strategy_id]
        metrics = _metrics(strategy_records, config)
        reliability = min(1.0, len(strategy_records) / config.minimum_trade_count_full_weight)
        performance_factor = _performance_factor(metrics, definition.base_weight, config)
        regime_factor = _regime_factor(strategy_records, regime)
        health_factor = _health_factor(metrics, config)
        correlation_factor = correlation_factors[definition.strategy_id]
        raw = definition.base_weight * reliability * performance_factor * regime_factor * health_factor * correlation_factor
        shrunk = _shrink_toward_baseline(raw, definition.base_weight, len(strategy_records), config)
        clamped = min(config.strategy_cap, max(config.strategy_floor, shrunk))
        unscaled[definition.strategy_id] = clamped
        draft_details.append(
            WcaStrategyWeightDetail(
                strategy_id=definition.strategy_id,
                family=definition.family,
                base_weight=definition.base_weight,
                performance_factor=round(performance_factor, 6),
                reliability_factor=round(reliability, 6),
                regime_factor=round(regime_factor, 6),
                health_factor=round(health_factor, 6),
                correlation_factor=round(correlation_factor, 6),
                final_weight=0,
                trade_count=len(strategy_records),
                rolling_expectancy=round(metrics["expectancy"], 6),
                profit_factor=round(metrics["profit_factor"], 6),
                win_rate=round(metrics["win_rate"], 6),
                average_r=round(metrics["average_r"], 6),
                downside_deviation=round(metrics["downside_deviation"], 6),
                maximum_drawdown=round(metrics["maximum_drawdown"], 6),
                consecutive_losses=metrics["consecutive_losses"],
                metrics_cutoff_timestamp=cutoff,
                weight_version=config.weight_version,
                reason_codes=_reason_codes(len(strategy_records), reliability, correlation_factor),
            )
        )

    capped_weights = _apply_family_caps(_normalize(unscaled), config)
    details = tuple(detail.model_copy(update={"final_weight": capped_weights[detail.strategy_id]}) for detail in draft_details)
    return WcaWeightSnapshot(
        weight_version=config.weight_version,
        created_at=cutoff,
        weights=capped_weights,
        details=details,
        metrics_cutoff_timestamp=cutoff,
        reason_codes=("wca.weights.performance_derived",),
    )


def _metrics(records: tuple[WcaStrategyPerformanceRecord, ...], config: WcaWeightEngineConfig) -> dict[str, float | int]:
    if not records:
        return {
            "expectancy": 0,
            "profit_factor": 1,
            "win_rate": 0,
            "average_r": 0,
            "downside_deviation": 0,
            "maximum_drawdown": 0,
            "consecutive_losses": 0,
            "stability": 1,
        }
    ordered = tuple(sorted(records, key=lambda record: record.outcome_available_at))
    values = tuple(record.r_multiple for record in ordered)
    weighted_values = _decayed_values(values, config.recent_decay)
    wins = tuple(value for value in values if value > 0)
    losses = tuple(value for value in values if value < 0)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    recent = values[-max(1, min(len(values), max(5, len(values) // 3))):]
    long_term = sum(values) / len(values)
    recent_expectancy = sum(recent) / len(recent)
    stability = 1 / (1 + abs(recent_expectancy - long_term))
    downside = math.sqrt(sum(value * value for value in losses) / len(losses)) if losses else 0
    return {
        "expectancy": sum(weighted_values) / len(weighted_values),
        "profit_factor": min(config.max_profit_factor, gross_win / gross_loss) if gross_loss > 0 else config.max_profit_factor,
        "win_rate": len(wins) / len(values),
        "average_r": sum(values) / len(values),
        "downside_deviation": downside,
        "maximum_drawdown": _max_drawdown(values),
        "consecutive_losses": _max_consecutive_losses(values),
        "stability": stability,
    }


def _performance_factor(metrics: dict[str, float | int], base_weight: float, config: WcaWeightEngineConfig) -> float:
    expectancy_component = 1 + max(-0.35, min(config.max_expectancy_bonus, float(metrics["expectancy"]) * 0.30))
    profit_component = 0.75 + min(config.max_profit_factor, float(metrics["profit_factor"])) / (config.max_profit_factor * 2)
    win_component = 0.75 + float(metrics["win_rate"]) * 0.50
    average_component = 1 + max(-0.20, min(0.20, float(metrics["average_r"]) * 0.15))
    return max(0.25, expectancy_component * profit_component * win_component * average_component / max(0.01, 1 + base_weight))


def _regime_factor(records: tuple[WcaStrategyPerformanceRecord, ...], regime: str) -> float:
    if regime == "default" or not records:
        return 1.0
    matching = tuple(record.r_multiple for record in records if record.regime == regime)
    if len(matching) < 10:
        return 0.95
    expectancy = sum(matching) / len(matching)
    return max(0.70, min(1.25, 1 + expectancy * 0.10))


def _health_factor(metrics: dict[str, float | int], config: WcaWeightEngineConfig) -> float:
    drawdown_penalty = min(0.35, float(metrics["maximum_drawdown"]) / config.max_drawdown_penalty_r * 0.35)
    downside_penalty = min(0.25, float(metrics["downside_deviation"]) * 0.12)
    streak_penalty = min(0.25, int(metrics["consecutive_losses"]) / config.max_consecutive_loss_penalty * 0.25)
    stability_bonus = min(0.15, max(0, float(metrics["stability"]) - 0.8) * 0.20)
    return max(0.35, 1 - drawdown_penalty - downside_penalty - streak_penalty + stability_bonus)


def _shrink_toward_baseline(raw_weight: float, base_weight: float, trade_count: int, config: WcaWeightEngineConfig) -> float:
    observed_strength = trade_count / (trade_count + config.bayesian_prior_trade_count)
    return base_weight * (1 - observed_strength) + raw_weight * observed_strength


def _correlation_factors(by_strategy: dict[str, tuple[WcaStrategyPerformanceRecord, ...]], config: WcaWeightEngineConfig) -> dict[str, float]:
    factors = {definition.strategy_id: 1.0 for definition in WCA_STRATEGY_REGISTRY}
    series = {strategy_id: tuple(record.r_multiple for record in sorted(records, key=lambda row: row.outcome_available_at)) for strategy_id, records in by_strategy.items()}
    for left in WCA_STRATEGY_REGISTRY:
        penalties: list[float] = []
        for right in WCA_STRATEGY_REGISTRY:
            if left.strategy_id == right.strategy_id:
                continue
            corr = _correlation(series[left.strategy_id], series[right.strategy_id])
            if corr > config.high_correlation_threshold:
                excess = (corr - config.high_correlation_threshold) / max(0.01, 1 - config.high_correlation_threshold)
                penalties.append(min(config.max_correlation_penalty, excess * config.max_correlation_penalty))
        if penalties:
            factors[left.strategy_id] = max(1 - max(penalties), 1 - config.max_correlation_penalty)
    return factors


def _apply_family_caps(weights: dict[str, float], config: WcaWeightEngineConfig) -> dict[str, float]:
    family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
    capped = dict(weights)
    for _ in range(10):
        family_totals: dict[str, float] = defaultdict(float)
        for strategy_id, weight in capped.items():
            family_totals[family_by_strategy[strategy_id]] += weight
        over_cap = {family: total for family, total in family_totals.items() if total > config.family_cap + 1e-12}
        if not over_cap:
            return _normalize(capped)
        freed = 0.0
        for family, total in over_cap.items():
            scale = config.family_cap / total
            for strategy_id, weight in tuple(capped.items()):
                if family_by_strategy[strategy_id] == family:
                    new_weight = max(config.strategy_floor, weight * scale)
                    freed += weight - new_weight
                    capped[strategy_id] = new_weight
        eligible = tuple(strategy_id for strategy_id, weight in capped.items() if family_totals[family_by_strategy[strategy_id]] <= config.family_cap and weight < config.strategy_cap)
        if not eligible or freed <= 0:
            return _normalize(capped)
        add_each = freed / len(eligible)
        for strategy_id in eligible:
            capped[strategy_id] = min(config.strategy_cap, capped[strategy_id] + add_each)
    return _normalize(capped)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {definition.strategy_id: round(definition.base_weight, 10) for definition in WCA_STRATEGY_REGISTRY}
    normalized = {strategy_id: weight / total for strategy_id, weight in weights.items()}
    drift = 1.0 - sum(normalized.values())
    first = next(iter(normalized))
    normalized[first] += drift
    return {strategy_id: round(weight, 10) for strategy_id, weight in normalized.items()}


def _decayed_values(values: tuple[float, ...], decay: float) -> tuple[float, ...]:
    if not values:
        return ()
    weighted_sum = 0.0
    total_weight = 0.0
    for age, value in enumerate(reversed(values)):
        weight = decay**age
        weighted_sum += value * weight
        total_weight += weight
    return (weighted_sum / total_weight,) * len(values)


def _max_drawdown(values: tuple[float, ...]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _max_consecutive_losses(values: tuple[float, ...]) -> int:
    current = 0
    longest = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    count = min(len(left), len(right))
    if count < 10:
        return 0.0
    x = left[-count:]
    y = right[-count:]
    mean_x = sum(x) / count
    mean_y = sum(y) / count
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denom_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    denom_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    if denom_x <= 0 or denom_y <= 0:
        return 0.0
    return numerator / (denom_x * denom_y)


def _reason_codes(trade_count: int, reliability: float, correlation_factor: float) -> tuple[str, ...]:
    reasons = ["wca.weights.performance_derived"]
    if reliability < 1:
        reasons.append("wca.weights.shrunk_to_baseline")
    if trade_count == 0:
        reasons.append("wca.weights.no_history")
    if correlation_factor < 1:
        reasons.append("wca.weights.correlation_penalty")
    return tuple(reasons)
