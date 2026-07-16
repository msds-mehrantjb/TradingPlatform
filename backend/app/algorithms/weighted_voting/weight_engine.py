"""Weight calculations for Weighted Voting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import sqrt

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import (
    WeightedDataQualityStatus,
    WeightedPerformanceWeightMetric,
    WeightedStrategyOutcome,
    WeightedVotingSignal,
    WeightedWeightAdjustment,
    WeightedWeightState,
    WeightedWeightStateStatus,
)


WEIGHTED_VOTING_WEIGHT_ENGINE_VERSION = "weighted_voting_weight_engine_v3"
WEIGHTED_VOTING_STRATEGY_IDS = tuple(entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)
WEIGHTED_VOTING_STRATEGY_FAMILIES = {entry.strategy_id: entry.family for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


@dataclass(frozen=True)
class WeightedWeightResult:
    signals: tuple[WeightedVotingSignal, ...]
    adjustments: tuple[WeightedWeightAdjustment, ...]


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    non_negative = {key: max(0.0, float(value)) for key, value in weights.items()}
    total = sum(non_negative.values())
    if total <= 0:
        return {key: 0.0 for key in weights}
    return {key: value / total for key, value in non_negative.items()}


def create_unseeded_equal_weight_state(
    *,
    timestamp: datetime,
    data_timestamp: datetime | None = None,
    strategy_ids: tuple[str, ...] = WEIGHTED_VOTING_STRATEGY_IDS,
) -> WeightedWeightState:
    return WeightedWeightState(
        weight_version="weighted_weights_unseeded_equal_v1",
        state_status=WeightedWeightStateStatus.UNSEEDED_EQUAL_WEIGHTS,
        strategy_weights=_equal_weights(strategy_ids),
        last_updated_at=timestamp,
        data_timestamp=data_timestamp or timestamp,
        reason_codes=("weighted_voting.weights.unseeded_equal",),
        explanation="Initial deterministic equal weights before qualified Weighted Voting outcomes are available.",
    )


def update_performance_weight_state(
    previous_state: WeightedWeightState,
    outcomes: tuple[WeightedStrategyOutcome, ...],
    *,
    update_timestamp: datetime,
    data_timestamp: datetime | None = None,
    session_date: date | str | None = None,
    config: WeightedVotingConfig | None = None,
    regime_label: str | None = None,
) -> WeightedWeightState:
    active_config = config or WeightedVotingConfig()
    effective_data_timestamp = data_timestamp or update_timestamp
    session_key = _session_key(session_date, update_timestamp)
    if previous_state.active_session_date == session_key:
        return previous_state

    try:
        _validate_weight_update_config(active_config)
        strategy_ids = _ordered_strategy_ids(previous_state.strategy_weights)
        previous_weights = _complete_weights(previous_state.strategy_weights, strategy_ids)
        qualified_outcomes = _qualified_outcomes(outcomes, strategy_ids)
        if not qualified_outcomes:
            return _preserve_weight_state(
                previous_state,
                status=WeightedWeightStateStatus.FROZEN_INSUFFICIENT_DATA,
                update_timestamp=update_timestamp,
                data_timestamp=effective_data_timestamp,
                active_session_date=session_key,
                reason_codes=("weighted_voting.weights.insufficient_qualified_outcomes",),
                explanation="No completed qualified Weighted Voting outcomes were available; last valid active weights were preserved.",
            )

        returns_by_strategy = _returns_by_strategy(qualified_outcomes, strategy_ids)
        base_metrics = _performance_metrics(strategy_ids, returns_by_strategy, active_config, regime_label, qualified_outcomes)
        performance_scores = {metric.strategy_id: metric.raw_performance_score * metric.correlation_penalty for metric in base_metrics}
        performance_weights = normalize_weights(performance_scores)
        if sum(performance_weights.values()) <= 0:
            performance_weights = _equal_weights(strategy_ids)

        equal_weights = _equal_weights(strategy_ids)
        candidate_weights = normalize_weights(
            {
                metric.strategy_id: (
                    equal_weights[metric.strategy_id] * (1.0 - metric.sample_shrinkage)
                    + performance_weights.get(metric.strategy_id, 0.0) * metric.sample_shrinkage
                )
                for metric in base_metrics
            }
        )
        candidate_weights = _normalize_state_weights_with_caps(candidate_weights, active_config, strategy_ids)
        smoothed_weights = normalize_weights(
            {
                strategy_id: previous_weights[strategy_id] * active_config.weight_smoothing_previous
                + candidate_weights[strategy_id] * active_config.weight_smoothing_candidate
                for strategy_id in strategy_ids
            }
        )
        limited_weights = _apply_daily_weight_change_limit(previous_weights, smoothed_weights, active_config.maximum_daily_weight_change)
        final_weights = _normalize_state_weights_with_caps(limited_weights, active_config, strategy_ids)
        final_weights = _apply_daily_weight_change_limit(previous_weights, final_weights, active_config.maximum_daily_weight_change)
        final_weights = _round_weight_dict(final_weights)

        status = (
            WeightedWeightStateStatus.BACKTEST_SEEDED
            if previous_state.state_status == WeightedWeightStateStatus.UNSEEDED_EQUAL_WEIGHTS.value
            else WeightedWeightStateStatus.LIVE_ADAPTING
        )
        metrics = tuple(
            metric.model_copy(
                update={
                    "candidate_weight": round(candidate_weights.get(metric.strategy_id, 0.0), 10),
                    "smoothed_weight": round(smoothed_weights.get(metric.strategy_id, 0.0), 10),
                    "final_weight": final_weights.get(metric.strategy_id, 0.0),
                }
            )
            for metric in base_metrics
        )
        return WeightedWeightState(
            weight_version=f"weighted_weights_performance_{session_key}",
            state_status=status,
            strategy_weights=final_weights,
            active_session_date=session_key,
            performance_metrics=metrics,
            last_updated_at=update_timestamp,
            data_timestamp=effective_data_timestamp,
            reason_codes=("weighted_voting.weights.performance_update",),
            explanation="Deterministic performance-derived weights updated once for the trading session.",
        )
    except Exception as exc:
        return _preserve_weight_state(
            previous_state,
            status=WeightedWeightStateStatus.VALIDATION_FAILED,
            update_timestamp=update_timestamp,
            data_timestamp=effective_data_timestamp,
            active_session_date=previous_state.active_session_date,
            reason_codes=("weighted_voting.weights.validation_failed",),
            explanation=f"Weight update failed validation; last valid active weights were preserved. {exc}",
        )


def apply_weight_controls(
    signals: list[WeightedVotingSignal],
    *,
    config: WeightedVotingConfig | None = None,
    historical_outcomes: tuple[WeightedStrategyOutcome, ...] = (),
) -> WeightedWeightResult:
    active_config = config or WeightedVotingConfig()
    enabled = [signal for signal in signals if signal.eligible and signal.data_ready and signal.data_quality_status != WeightedDataQualityStatus.UNAVAILABLE.value]
    enabled_ids = {signal.strategy_id for signal in enabled}
    original_weights = _original_frozen_weights(signals, enabled_ids)
    correlation_penalties = _correlation_penalties(signals, historical_outcomes)
    raw_weights: dict[str, float] = {}
    pre_family_weights: dict[str, float] = {}

    for signal in signals:
        if signal.strategy_id not in enabled_ids:
            raw_weights[signal.strategy_id] = 0.0
            pre_family_weights[signal.strategy_id] = 0.0
            continue
        data_quality_adjustment = _data_quality_adjustment(signal)
        market_condition_adjustment = _market_condition_adjustment(signal)
        raw = original_weights[signal.strategy_id] * correlation_penalties[signal.strategy_id] * data_quality_adjustment * market_condition_adjustment
        raw_weights[signal.strategy_id] = max(0.0, raw)
        pre_family_weights[signal.strategy_id] = max(0.0, raw)

    final_weights = _normalize_with_strategy_and_family_caps(raw_weights, signals, active_config, enabled_ids)
    adjusted_signals = tuple(signal.model_copy(update={"final_weight": final_weights.get(signal.strategy_id, 0.0)}) for signal in signals)
    adjustments = tuple(
        _adjustment_for(signal, original_weights, correlation_penalties, pre_family_weights, final_weights)
        for signal in signals
    )
    return WeightedWeightResult(signals=adjusted_signals, adjustments=adjustments)


def _equal_weights(strategy_ids: tuple[str, ...]) -> dict[str, float]:
    if not strategy_ids:
        return {}
    equal = 1.0 / len(strategy_ids)
    return _round_weight_dict({strategy_id: equal for strategy_id in strategy_ids})


def _session_key(session_date: date | str | None, update_timestamp: datetime) -> str:
    if isinstance(session_date, date):
        return session_date.isoformat()
    if isinstance(session_date, str) and session_date:
        return session_date
    return update_timestamp.date().isoformat()


def _validate_weight_update_config(config: WeightedVotingConfig) -> None:
    if config.minimum_qualified_outcomes_for_adaptation <= 0:
        raise ValueError("minimum qualified outcomes must be positive")
    smoothing_total = config.weight_smoothing_previous + config.weight_smoothing_candidate
    if abs(smoothing_total - 1.0) > 1e-9:
        raise ValueError("weight smoothing coefficients must sum to one")
    if config.maximum_daily_weight_change < 0 or config.maximum_daily_weight_change > 1:
        raise ValueError("maximum daily weight change must be between zero and one")


def _ordered_strategy_ids(previous_weights: dict[str, float]) -> tuple[str, ...]:
    known = [strategy_id for strategy_id in WEIGHTED_VOTING_STRATEGY_IDS if strategy_id in previous_weights]
    extras = sorted(strategy_id for strategy_id in previous_weights if strategy_id not in known)
    return tuple(known + extras)


def _complete_weights(weights: dict[str, float], strategy_ids: tuple[str, ...]) -> dict[str, float]:
    completed = {strategy_id: max(0.0, weights.get(strategy_id, 0.0)) for strategy_id in strategy_ids}
    normalized = normalize_weights(completed)
    if sum(normalized.values()) <= 0:
        return _equal_weights(strategy_ids)
    return normalized


def _qualified_outcomes(outcomes: tuple[WeightedStrategyOutcome, ...], strategy_ids: tuple[str, ...]) -> tuple[WeightedStrategyOutcome, ...]:
    strategy_id_set = set(strategy_ids)
    return tuple(
        outcome
        for outcome in outcomes
        if outcome.strategy_id in strategy_id_set and outcome.outcome_return is not None
    )


def _returns_by_strategy(outcomes: tuple[WeightedStrategyOutcome, ...], strategy_ids: tuple[str, ...]) -> dict[str, list[float]]:
    returns = {strategy_id: [] for strategy_id in strategy_ids}
    for outcome in outcomes:
        returns[outcome.strategy_id].append(float(outcome.outcome_return or 0.0))
    return returns


def _performance_metrics(
    strategy_ids: tuple[str, ...],
    returns_by_strategy: dict[str, list[float]],
    config: WeightedVotingConfig,
    regime_label: str | None,
    outcomes: tuple[WeightedStrategyOutcome, ...],
) -> tuple[WeightedPerformanceWeightMetric, ...]:
    correlation_penalties = _correlation_penalties_from_returns(strategy_ids, returns_by_strategy)
    regime_returns = _regime_returns_by_strategy(outcomes, strategy_ids, regime_label)
    metrics: list[WeightedPerformanceWeightMetric] = []
    for strategy_id in strategy_ids:
        returns = returns_by_strategy[strategy_id]
        sample_size = len(returns)
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value < 0]
        net_expectancy = sum(returns) / sample_size if sample_size else 0.0
        average_win = sum(wins) / len(wins) if wins else 0.0
        average_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        profit_factor = sum(wins) / abs(sum(losses)) if losses else (4.0 if wins else 0.0)
        win_loss_ratio = average_win / average_loss if average_loss > 0 else (4.0 if average_win > 0 else 0.0)
        maximum_drawdown = _maximum_drawdown(returns)
        outcome_stability = _outcome_stability(returns)
        recent_values = returns[-min(10, sample_size) :] if sample_size else []
        recent_performance = sum(recent_values) / len(recent_values) if recent_values else 0.0
        regime_values = regime_returns[strategy_id]
        regime_specific_performance = sum(regime_values) / len(regime_values) if regime_values else net_expectancy
        raw_score = _performance_score(
            net_expectancy=net_expectancy,
            profit_factor=profit_factor,
            win_loss_ratio=win_loss_ratio,
            maximum_drawdown=maximum_drawdown,
            outcome_stability=outcome_stability,
            recent_performance=recent_performance,
            regime_specific_performance=regime_specific_performance,
            config=config,
        )
        shrinkage = min(1.0, sample_size / config.minimum_qualified_outcomes_for_adaptation)
        metrics.append(
            WeightedPerformanceWeightMetric(
                strategy_id=strategy_id,
                sample_size=sample_size,
                net_expectancy_after_costs=round(net_expectancy, 10),
                profit_factor=round(profit_factor, 10),
                average_win=round(average_win, 10),
                average_loss=round(average_loss, 10),
                win_loss_ratio=round(win_loss_ratio, 10),
                maximum_drawdown=round(maximum_drawdown, 10),
                outcome_stability=round(outcome_stability, 10),
                recent_performance=round(recent_performance, 10),
                regime_specific_performance=round(regime_specific_performance, 10),
                correlation_penalty=correlation_penalties[strategy_id],
                sample_shrinkage=round(shrinkage, 10),
                raw_performance_score=round(raw_score, 10),
                explanation=f"{strategy_id} deterministic performance metrics for Weighted Voting weight adaptation.",
            )
        )
    return tuple(metrics)


def _regime_returns_by_strategy(
    outcomes: tuple[WeightedStrategyOutcome, ...],
    strategy_ids: tuple[str, ...],
    regime_label: str | None,
) -> dict[str, list[float]]:
    returns = {strategy_id: [] for strategy_id in strategy_ids}
    if not regime_label:
        return returns
    regime_code = f"weighted_voting.regime.{regime_label}"
    for outcome in outcomes:
        if regime_code in outcome.reason_codes:
            returns[outcome.strategy_id].append(float(outcome.outcome_return or 0.0))
    return returns


def _performance_score(
    *,
    net_expectancy: float,
    profit_factor: float,
    win_loss_ratio: float,
    maximum_drawdown: float,
    outcome_stability: float,
    recent_performance: float,
    regime_specific_performance: float,
    config: WeightedVotingConfig,
) -> float:
    return max(
        0.0,
        config.expectancy_score_weight * _return_score(net_expectancy)
        + config.profit_factor_score_weight * _bounded_score(profit_factor, 2.0)
        + config.win_loss_score_weight * _bounded_score(win_loss_ratio, 2.0)
        + config.drawdown_score_weight * max(0.0, 1.0 - maximum_drawdown / 0.05)
        + config.stability_score_weight * outcome_stability
        + config.recent_performance_score_weight * _return_score(recent_performance)
        + config.regime_performance_score_weight * _return_score(regime_specific_performance),
    )


def _return_score(value: float) -> float:
    return max(0.0, min(1.0, 0.5 + value / 0.02))


def _bounded_score(value: float, full_score_at: float) -> float:
    if full_score_at <= 0:
        return 0.0
    return max(0.0, min(1.0, value / full_score_at))


def _maximum_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return max(0.0, drawdown)


def _outcome_stability(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.5 if returns else 0.0
    average = sum(returns) / len(returns)
    variance = sum((value - average) ** 2 for value in returns) / len(returns)
    deviation = sqrt(variance)
    return max(0.0, min(1.0, 1.0 - deviation / (abs(average) + 0.02)))


def _correlation_penalties_from_returns(strategy_ids: tuple[str, ...], returns_by_strategy: dict[str, list[float]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for strategy_id in strategy_ids:
        correlations: list[float] = []
        own_returns = returns_by_strategy.get(strategy_id, [])
        for other_strategy_id in strategy_ids:
            if other_strategy_id == strategy_id:
                continue
            if WEIGHTED_VOTING_STRATEGY_FAMILIES.get(other_strategy_id) != WEIGHTED_VOTING_STRATEGY_FAMILIES.get(strategy_id):
                continue
            corr = _pearson(own_returns, returns_by_strategy.get(other_strategy_id, []))
            if corr is not None and corr > 0:
                correlations.append(corr)
        average_positive_corr = sum(correlations) / len(correlations) if correlations else 0.0
        penalty = 1.0 - min(0.35, max(0.0, average_positive_corr - 0.70) * 0.75)
        result[strategy_id] = round(max(0.65, penalty), 6)
    return result


def _normalize_state_weights_with_caps(
    weights: dict[str, float],
    config: WeightedVotingConfig,
    strategy_ids: tuple[str, ...],
) -> dict[str, float]:
    signals = [
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} weight state normalization",
            strategy_version=WEIGHTED_VOTING_WEIGHT_ENGINE_VERSION,
            family=WEIGHTED_VOTING_STRATEGY_FAMILIES[strategy_id],
            signal="Hold",
            p_buy=0.0,
            p_sell=0.0,
            p_hold=1.0,
            directional_confidence=0.0,
            signal_strength=0.0,
            strength=0.0,
            final_weight=weights.get(strategy_id, 0.0),
            eligible=True,
            data_ready=True,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=datetime(1970, 1, 1),
            explanation="Synthetic signal for deterministic weight-state cap normalization.",
        )
        for strategy_id in strategy_ids
    ]
    return _normalize_with_strategy_and_family_caps(weights, signals, config, set(strategy_ids))


def _apply_daily_weight_change_limit(
    previous_weights: dict[str, float],
    target_weights: dict[str, float],
    maximum_change: float,
) -> dict[str, float]:
    strategy_ids = tuple(previous_weights.keys())
    limited = {
        strategy_id: previous_weights[strategy_id]
        + max(-maximum_change, min(maximum_change, target_weights.get(strategy_id, 0.0) - previous_weights[strategy_id]))
        for strategy_id in strategy_ids
    }
    residual = 1.0 - sum(limited.values())
    for _ in range(8):
        if abs(residual) <= 1e-12:
            break
        if residual > 0:
            candidates = [
                strategy_id
                for strategy_id in strategy_ids
                if limited[strategy_id] < previous_weights[strategy_id] + maximum_change - 1e-12
            ]
        else:
            candidates = [
                strategy_id
                for strategy_id in strategy_ids
                if limited[strategy_id] > previous_weights[strategy_id] - maximum_change + 1e-12
            ]
        if not candidates:
            break
        share = residual / len(candidates)
        applied = 0.0
        for strategy_id in candidates:
            if residual > 0:
                capacity = previous_weights[strategy_id] + maximum_change - limited[strategy_id]
                delta = min(capacity, share)
            else:
                capacity = limited[strategy_id] - max(0.0, previous_weights[strategy_id] - maximum_change)
                delta = max(-capacity, share)
            limited[strategy_id] += delta
            applied += delta
        if abs(applied) <= 1e-12:
            break
        residual -= applied
    return normalize_weights(limited)


def _round_weight_dict(weights: dict[str, float]) -> dict[str, float]:
    rounded = {strategy_id: round(max(0.0, weight), 10) for strategy_id, weight in weights.items()}
    total = sum(rounded.values())
    if total <= 0:
        return rounded
    difference = round(1.0 - total, 10)
    if abs(difference) > 0:
        receiver = max(rounded, key=rounded.get)
        rounded[receiver] = round(rounded[receiver] + difference, 10)
    return rounded


def _preserve_weight_state(
    previous_state: WeightedWeightState,
    *,
    status: WeightedWeightStateStatus,
    update_timestamp: datetime,
    data_timestamp: datetime,
    active_session_date: str | None,
    reason_codes: tuple[str, ...],
    explanation: str,
) -> WeightedWeightState:
    return WeightedWeightState(
        weight_version=previous_state.weight_version,
        state_status=status,
        strategy_weights=dict(previous_state.strategy_weights),
        active_session_date=active_session_date,
        performance_metrics=previous_state.performance_metrics,
        last_updated_at=update_timestamp,
        data_timestamp=data_timestamp,
        reason_codes=reason_codes,
        explanation=explanation,
    )


def _original_frozen_weights(signals: list[WeightedVotingSignal], enabled_ids: set[str]) -> dict[str, float]:
    supplied = {signal.strategy_id: max(0.0, signal.final_weight) for signal in signals if signal.strategy_id in enabled_ids}
    if not supplied:
        return {signal.strategy_id: 0.0 for signal in signals}
    if sum(supplied.values()) <= 0:
        equal = 1.0 / len(enabled_ids)
        return {signal.strategy_id: equal if signal.strategy_id in enabled_ids else 0.0 for signal in signals}
    normalized = normalize_weights(supplied)
    return {signal.strategy_id: normalized.get(signal.strategy_id, 0.0) for signal in signals}


def _correlation_penalties(signals: list[WeightedVotingSignal], historical_outcomes: tuple[WeightedStrategyOutcome, ...]) -> dict[str, float]:
    returns_by_strategy: dict[str, list[float]] = defaultdict(list)
    for outcome in historical_outcomes:
        if outcome.outcome_return is not None:
            returns_by_strategy[outcome.strategy_id].append(float(outcome.outcome_return))

    result: dict[str, float] = {}
    for signal in signals:
        correlations: list[float] = []
        own_returns = returns_by_strategy.get(signal.strategy_id, [])
        for other in signals:
            if other.strategy_id == signal.strategy_id or other.family != signal.family:
                continue
            corr = _pearson(own_returns, returns_by_strategy.get(other.strategy_id, []))
            if corr is not None and corr > 0:
                correlations.append(corr)
        average_positive_corr = sum(correlations) / len(correlations) if correlations else 0.0
        penalty = 1.0 - min(0.35, max(0.0, average_positive_corr - 0.70) * 0.75)
        result[signal.strategy_id] = round(max(0.65, penalty), 6)
    return result


def _pearson(left: list[float], right: list[float]) -> float | None:
    size = min(len(left), len(right))
    if size < 3:
        return None
    left_values = left[-size:]
    right_values = right[-size:]
    left_mean = sum(left_values) / size
    right_mean = sum(right_values) / size
    numerator = sum((left_values[index] - left_mean) * (right_values[index] - right_mean) for index in range(size))
    left_denominator = sqrt(sum((value - left_mean) ** 2 for value in left_values))
    right_denominator = sqrt(sum((value - right_mean) ** 2 for value in right_values))
    denominator = left_denominator * right_denominator
    if denominator <= 0:
        return None
    return numerator / denominator


def _data_quality_adjustment(signal: WeightedVotingSignal) -> float:
    if signal.data_quality_status == WeightedDataQualityStatus.FULL.value:
        return 1.0
    if signal.data_quality_status == WeightedDataQualityStatus.DEGRADED.value:
        return 0.85
    if signal.data_quality_status == WeightedDataQualityStatus.PROXY.value:
        return 0.70
    return 0.0


def _market_condition_adjustment(signal: WeightedVotingSignal) -> float:
    if not signal.eligible:
        return 0.0
    return 1.0


def _normalize_with_strategy_and_family_caps(
    raw_weights: dict[str, float],
    signals: list[WeightedVotingSignal],
    config: WeightedVotingConfig,
    enabled_ids: set[str],
) -> dict[str, float]:
    if not enabled_ids:
        return {signal.strategy_id: 0.0 for signal in signals}

    feasible_strategy_floor = 1.0 / len(enabled_ids)
    max_strategy = max(max(0.0, min(1.0, config.maximum_strategy_weight)), feasible_strategy_floor)
    families = {signal.strategy_id: signal.family for signal in signals}
    enabled_family_count = len({families[strategy_id] for strategy_id in enabled_ids})
    feasible_family_floor = 1.0 / enabled_family_count if enabled_family_count else 1.0
    max_family = max(max(0.0, min(1.0, config.maximum_family_weight)), feasible_family_floor)
    minimum = max(0.0, min(config.minimum_enabled_strategy_weight, max_strategy))
    if minimum * len(enabled_ids) > 1.0:
        minimum = 1.0 / len(enabled_ids)

    weights = {signal.strategy_id: minimum if signal.strategy_id in enabled_ids else 0.0 for signal in signals}
    remaining = max(0.0, 1.0 - sum(weights.values()))
    scores = {strategy_id: max(0.0, raw_weights.get(strategy_id, 0.0)) for strategy_id in enabled_ids}
    if sum(scores.values()) <= 0:
        scores = {strategy_id: 1.0 for strategy_id in enabled_ids}
    for _ in range(64):
        if remaining <= 1e-12:
            break
        available = [
            strategy_id
            for strategy_id in enabled_ids
            if weights[strategy_id] < max_strategy - 1e-12 and _family_total(weights, families, families[strategy_id]) < max_family - 1e-12
        ]
        if not available:
            break
        score_total = sum(scores[strategy_id] for strategy_id in available) or float(len(available))
        proposed = {strategy_id: remaining * (scores[strategy_id] / score_total) for strategy_id in available}
        for family in {families[strategy_id] for strategy_id in available}:
            family_ids = [strategy_id for strategy_id in available if families[strategy_id] == family]
            family_remaining = max(0.0, max_family - _family_total(weights, families, family))
            family_proposed = sum(proposed[strategy_id] for strategy_id in family_ids)
            if family_proposed > family_remaining > 0:
                scale = family_remaining / family_proposed
                for strategy_id in family_ids:
                    proposed[strategy_id] *= scale
            elif family_remaining <= 0:
                for strategy_id in family_ids:
                    proposed[strategy_id] = 0.0
        added = 0.0
        for strategy_id in available:
            strategy_capacity = max(0.0, max_strategy - weights[strategy_id])
            family_capacity = max(0.0, max_family - _family_total(weights, families, families[strategy_id]))
            increment = min(proposed[strategy_id], strategy_capacity, family_capacity, remaining - added)
            if increment > 0:
                weights[strategy_id] += increment
                added += increment
        if added <= 1e-12:
            break
        remaining -= added

    rounded = {signal.strategy_id: round(weights.get(signal.strategy_id, 0.0), 10) for signal in signals}
    total = sum(rounded.values())
    if total > 0:
        difference = round(1.0 - total, 10)
        if abs(difference) > 0:
            receivers = sorted(
                [strategy_id for strategy_id in enabled_ids if 0 <= rounded[strategy_id] + difference <= max_strategy],
                key=lambda strategy_id: rounded[strategy_id],
                reverse=difference > 0,
            )
            if receivers:
                rounded[receivers[0]] = round(rounded[receivers[0]] + difference, 10)
    return rounded


def _family_total(weights: dict[str, float], families: dict[str, str], family: str) -> float:
    return sum(weight for strategy_id, weight in weights.items() if families.get(strategy_id) == family)


def _adjustment_for(
    signal: WeightedVotingSignal,
    original_weights: dict[str, float],
    correlation_penalties: dict[str, float],
    pre_family_weights: dict[str, float],
    final_weights: dict[str, float],
) -> WeightedWeightAdjustment:
    original = original_weights.get(signal.strategy_id, 0.0)
    pre_family = pre_family_weights.get(signal.strategy_id, 0.0)
    final = final_weights.get(signal.strategy_id, 0.0)
    family_cap_adjustment = 1.0 if pre_family <= 0 else min(1.0, final / pre_family)
    data_quality_adjustment = _data_quality_adjustment(signal)
    market_condition_adjustment = _market_condition_adjustment(signal)
    reason_codes = []
    if final == 0:
        reason_codes.append("weighted_voting.weight.zero")
    if correlation_penalties.get(signal.strategy_id, 1.0) < 1:
        reason_codes.append("weighted_voting.weight.correlation_penalty")
    if family_cap_adjustment < 1:
        reason_codes.append("weighted_voting.weight.family_cap")
    if data_quality_adjustment < 1:
        reason_codes.append("weighted_voting.weight.data_quality")
    return WeightedWeightAdjustment(
        strategy_id=signal.strategy_id,
        family=signal.family,
        original_frozen_weight=round(original, 10),
        correlation_penalty=round(correlation_penalties.get(signal.strategy_id, 1.0), 6),
        family_cap_adjustment=round(family_cap_adjustment, 6),
        data_quality_adjustment=round(data_quality_adjustment, 6),
        market_condition_adjustment=round(market_condition_adjustment, 6),
        final_effective_weight=round(final, 10),
        reason_codes=tuple(reason_codes),
        explanation=f"{signal.strategy_id} effective weight after deterministic concentration controls.",
    )
