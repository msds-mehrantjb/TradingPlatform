"""WCA aggregation and eligibility handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.app.algorithms.wca.contracts import (
    WcaAggregationExclusion,
    WcaAggregationResult,
    WcaEffectiveSettings,
    WcaEvaluationStatus,
    WcaFamilyContribution,
    WcaGateStatus,
    WcaLocalGateResult,
    WcaSide,
    WcaStrategyContribution,
    WcaStrategyEvaluation,
)
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY


@dataclass(frozen=True)
class WcaAggregationConfig:
    minimum_active_strategies: int = 3
    minimum_normalized_score: float = 0.35
    minimum_directional_agreement: float = 0.50
    minimum_average_confidence: float = 0.45
    minimum_winner_edge: float = 0.05
    maximum_family_concentration: float = 0.45
    minimum_expectancy_after_costs: float = 0.0


def aggregate_wca(
    evaluations: tuple[WcaStrategyEvaluation, ...],
    *,
    effective_settings: WcaEffectiveSettings | None = None,
    local_gates: tuple[WcaLocalGateResult, ...] = (),
    estimated_expectancy_after_costs: float = 0.01,
    config: WcaAggregationConfig | None = None,
) -> WcaAggregationResult:
    active_config = config or _config_from_effective_settings(effective_settings)
    family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
    eligible, exclusions = _eligible_evaluations(evaluations, family_by_strategy)
    adjusted_weights = _family_capped_weights(eligible, family_by_strategy, active_config.maximum_family_concentration)
    contributions = tuple(_contribution(row, family_by_strategy, adjusted_weights[row.strategy_id]) for row in eligible)
    buy_score = round4(sum(row.score_contribution for row in contributions if row.signal == WcaSide.BUY.value))
    sell_score = round4(abs(sum(row.score_contribution for row in contributions if row.signal == WcaSide.SELL.value)))
    active_weight = round4(sum(row.adjusted_weight for row in contributions))
    buy_directional_weight = round4(sum(row.adjusted_weight for row in contributions if row.signal == WcaSide.BUY.value))
    sell_directional_weight = round4(sum(row.adjusted_weight for row in contributions if row.signal == WcaSide.SELL.value))
    net_score = round4(buy_score - sell_score)
    normalized_net_score = round4(net_score / active_weight) if active_weight else 0
    buy_agreement = round4(buy_directional_weight / active_weight) if active_weight else 0
    sell_agreement = round4(sell_directional_weight / active_weight) if active_weight else 0
    buy_average_confidence = round4(buy_score / buy_directional_weight) if buy_directional_weight else 0
    sell_average_confidence = round4(sell_score / sell_directional_weight) if sell_directional_weight else 0
    winner_score = max(buy_score, sell_score)
    runner_up_score = min(buy_score, sell_score)
    winner_edge = round4(winner_score - runner_up_score)
    family_contributions = _family_contributions(contributions)
    family_concentration = round4(max((row.directional_weight for row in family_contributions), default=0) / active_weight) if active_weight else 0
    pre_gate = _pre_gate_decision(
        active_strategy_count=len(contributions),
        normalized_net_score=normalized_net_score,
        buy_score=buy_score,
        sell_score=sell_score,
        buy_agreement=buy_agreement,
        sell_agreement=sell_agreement,
        buy_average_confidence=buy_average_confidence,
        sell_average_confidence=sell_average_confidence,
        winner_edge=winner_edge,
        family_concentration=family_concentration,
        expectancy=estimated_expectancy_after_costs,
        config=active_config,
    )
    failed_gate_codes = tuple(code for gate in local_gates if gate.status == WcaGateStatus.FAIL.value and gate.blocks_entry for code in gate.reason_codes)
    post_gate = WcaSide.HOLD if failed_gate_codes else pre_gate
    reason_codes = _reason_codes(pre_gate, post_gate, exclusions, failed_gate_codes)
    return WcaAggregationResult(
        signal=post_gate,
        decision_label=_decision_label(post_gate),
        pre_gate_decision=pre_gate,
        post_local_gate_decision=post_gate,
        buy_score=buy_score,
        sell_score=sell_score,
        net_score=net_score,
        active_weight=active_weight,
        normalized_net_score=normalized_net_score,
        active_strategy_count=len(contributions),
        runner_up_score=runner_up_score,
        winner_edge=winner_edge,
        buy_agreement=buy_agreement,
        sell_agreement=sell_agreement,
        buy_average_confidence=buy_average_confidence,
        sell_average_confidence=sell_average_confidence,
        family_concentration=family_concentration,
        estimated_expectancy_after_costs=estimated_expectancy_after_costs,
        strategy_contributions=contributions,
        family_contributions=family_contributions,
        exclusions=exclusions,
        strategy_evaluations=evaluations,
        reason_codes=reason_codes,
    )


def _config_from_effective_settings(settings: WcaEffectiveSettings | None) -> WcaAggregationConfig:
    if settings is None:
        return WcaAggregationConfig()
    return WcaAggregationConfig(
        minimum_active_strategies=settings.baseline.minimum_active_strategies,
        minimum_normalized_score=settings.final_minimum_score,
        minimum_directional_agreement=settings.final_minimum_agreement,
        minimum_average_confidence=settings.final_minimum_confidence,
    )


def _eligible_evaluations(
    evaluations: tuple[WcaStrategyEvaluation, ...],
    family_by_strategy: dict[str, str],
) -> tuple[tuple[WcaStrategyEvaluation, ...], tuple[WcaAggregationExclusion, ...]]:
    eligible: list[WcaStrategyEvaluation] = []
    exclusions: list[WcaAggregationExclusion] = []
    for row in evaluations:
        reason = _exclusion_reason(row)
        if reason:
            exclusions.append(
                WcaAggregationExclusion(
                    strategy_id=row.strategy_id,
                    family=family_by_strategy.get(row.strategy_id, "unknown"),
                    reason_codes=(reason, *row.reason_codes),
                )
            )
            continue
        eligible.append(row)
    return tuple(eligible), tuple(exclusions)


def _exclusion_reason(row: WcaStrategyEvaluation) -> str | None:
    if row.status != WcaEvaluationStatus.ACTIVE.value:
        return "wca.aggregation.excluded.not_active"
    if row.data_quality_status != WcaEvaluationStatus.ACTIVE.value:
        return "wca.aggregation.excluded.unhealthy_data"
    if row.effective_weight <= 0:
        return "wca.aggregation.excluded.disabled_or_zero_weight"
    if row.signal == WcaSide.HOLD.value:
        return "wca.aggregation.excluded.deliberate_hold"
    return None


def _family_capped_weights(
    eligible: tuple[WcaStrategyEvaluation, ...],
    family_by_strategy: dict[str, str],
    family_cap: float,
) -> dict[str, float]:
    weights = {row.strategy_id: row.effective_weight for row in eligible}
    for _ in range(8):
        total = sum(weights.values())
        if total <= 0:
            return {row.strategy_id: 0 for row in eligible}
        family_totals: dict[str, float] = {}
        for strategy_id, weight in weights.items():
            family = family_by_strategy.get(strategy_id, "unknown")
            family_totals[family] = family_totals.get(family, 0) + weight
        over = {family: value for family, value in family_totals.items() if value / total > family_cap}
        if not over:
            return weights
        freed = 0.0
        for family, family_weight in over.items():
            target = total * family_cap
            scale = target / family_weight
            for strategy_id, weight in tuple(weights.items()):
                if family_by_strategy.get(strategy_id, "unknown") == family:
                    new_weight = weight * scale
                    freed += weight - new_weight
                    weights[strategy_id] = new_weight
        recipients = tuple(strategy_id for strategy_id in weights if family_by_strategy.get(strategy_id, "unknown") not in over)
        if not recipients or freed <= 0:
            return weights
        add_each = freed / len(recipients)
        for strategy_id in recipients:
            weights[strategy_id] += add_each
    return weights


def _contribution(row: WcaStrategyEvaluation, family_by_strategy: dict[str, str], adjusted_weight: float) -> WcaStrategyContribution:
    direction = 1 if row.signal == WcaSide.BUY.value else -1
    return WcaStrategyContribution(
        strategy_id=row.strategy_id,
        family=family_by_strategy.get(row.strategy_id, "unknown"),
        signal=row.signal,
        effective_weight=round(row.effective_weight, 6),
        adjusted_weight=round(adjusted_weight, 6),
        calibrated_confidence=row.calibrated_confidence,
        score_contribution=round4(direction * adjusted_weight * row.calibrated_confidence),
        reason_codes=("wca.aggregation.included",),
    )


def _family_contributions(contributions: Iterable[WcaStrategyContribution]) -> tuple[WcaFamilyContribution, ...]:
    by_family: dict[str, dict[str, float]] = {}
    for row in contributions:
        family = by_family.setdefault(row.family, {"buy": 0.0, "sell": 0.0, "weight": 0.0})
        family["weight"] += row.adjusted_weight
        if row.signal == WcaSide.BUY.value:
            family["buy"] += row.score_contribution
        elif row.signal == WcaSide.SELL.value:
            family["sell"] += abs(row.score_contribution)
    return tuple(
        WcaFamilyContribution(
            family=family,
            buy_score=round4(values["buy"]),
            sell_score=round4(values["sell"]),
            directional_weight=round4(values["weight"]),
            total_weight=round4(values["weight"]),
        )
        for family, values in sorted(by_family.items())
    )


def _pre_gate_decision(
    *,
    active_strategy_count: int,
    normalized_net_score: float,
    buy_score: float,
    sell_score: float,
    buy_agreement: float,
    sell_agreement: float,
    buy_average_confidence: float,
    sell_average_confidence: float,
    winner_edge: float,
    family_concentration: float,
    expectancy: float,
    config: WcaAggregationConfig,
) -> WcaSide:
    if active_strategy_count < config.minimum_active_strategies:
        return WcaSide.HOLD
    if winner_edge < config.minimum_winner_edge or abs(buy_score - sell_score) <= 1e-12:
        return WcaSide.HOLD
    if family_concentration > config.maximum_family_concentration + 1e-8:
        return WcaSide.HOLD
    if expectancy <= config.minimum_expectancy_after_costs:
        return WcaSide.HOLD
    if normalized_net_score >= config.minimum_normalized_score:
        if buy_agreement >= config.minimum_directional_agreement and buy_average_confidence >= config.minimum_average_confidence:
            return WcaSide.BUY
    if normalized_net_score <= -config.minimum_normalized_score:
        if sell_agreement >= config.minimum_directional_agreement and sell_average_confidence >= config.minimum_average_confidence:
            return WcaSide.SELL
    return WcaSide.HOLD


def _reason_codes(
    pre_gate: WcaSide,
    post_gate: WcaSide,
    exclusions: tuple[WcaAggregationExclusion, ...],
    failed_gate_codes: tuple[str, ...],
) -> tuple[str, ...]:
    codes = ["wca.aggregation.calculated"]
    if pre_gate == WcaSide.HOLD:
        codes.append("wca.aggregation.pre_gate_hold")
    if post_gate == WcaSide.HOLD and pre_gate != WcaSide.HOLD:
        codes.append("wca.aggregation.local_gate_hold")
    if exclusions:
        codes.append("wca.aggregation.exclusions_present")
    codes.extend(failed_gate_codes)
    return tuple(codes)


def _decision_label(signal: WcaSide) -> str:
    if signal == WcaSide.BUY:
        return "Buy"
    if signal == WcaSide.SELL:
        return "Sell"
    return "Hold"


def round4(value: float) -> float:
    return round(value, 4)


__all__ = ("WcaAggregationConfig", "WcaAggregationResult", "aggregate_wca")
