"""Deterministic Weighted Voting aggregation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_CATALOG_VERSION
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import WeightedStrategyOutcome, WeightedVoteScores, WeightedVotingDecision, WeightedVotingSide, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.weight_engine import apply_weight_controls


WEIGHTED_VOTING_AGGREGATION_VERSION = "weighted_voting_aggregation_v1"
SIDE_ORDER = (WeightedVotingSide.BUY, WeightedVotingSide.SELL, WeightedVotingSide.HOLD)


def aggregate_weighted_signals(
    signals: list[WeightedVotingSignal],
    *,
    config: WeightedVotingConfig | None = None,
    decision_timestamp: datetime | None = None,
    historical_outcomes: tuple[WeightedStrategyOutcome, ...] = (),
) -> WeightedVotingDecision:
    active_config = config or WeightedVotingConfig()
    data_timestamp = decision_timestamp
    if not signals:
        if data_timestamp is None:
            raise ValueError("decision_timestamp is required when no signals are supplied")
        vote_scores = WeightedVoteScores(
            buy_score=0.0,
            sell_score=0.0,
            hold_score=1.0,
            normalized_buy_score=0.0,
            normalized_sell_score=0.0,
            normalized_hold_score=1.0,
            winning_side=WeightedVotingSide.HOLD,
            winner_score=1.0,
            second_best_score=0.0,
            winner_edge=1.0,
            active_strategy_count=0,
            directional_strategy_count=0,
            active_weight=0.0,
            family_contributions={},
            disagreement_score=0.0,
            max_score=1.0,
            margin=1.0,
            raw_winner=WeightedVotingSide.HOLD,
            data_timestamp=data_timestamp,
            explanation="No Weighted Voting strategy signals were supplied.",
        )
        return WeightedVotingDecision(
            decision_id=f"weighted-voting-{data_timestamp.isoformat()}",
            configuration_version=active_config.config_version,
            strategy_catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
            weight_version="weighted_weights_v1",
            data_timestamp=data_timestamp,
            settings_version=active_config.config_version,
            proposed_side=WeightedVotingSide.HOLD,
            proposed_quantity=0,
            reason_codes=("weighted_voting.no_signals",),
            vote_scores=vote_scores,
            signal=WeightedVotingSide.HOLD,
            raw_winner=WeightedVotingSide.HOLD,
            eligible=False,
            data_ready=False,
            configuration_hash=active_config.configuration_hash,
            explanation="Weighted Voting aggregation received no strategy signals.",
        )

    data_timestamp = data_timestamp or max(signal.data_timestamp for signal in signals)
    weight_result = apply_weight_controls(signals, config=active_config, historical_outcomes=historical_outcomes)
    effective_signals = weight_result.signals
    raw_scores = {
        WeightedVotingSide.BUY: sum(signal.final_weight * signal.p_buy for signal in effective_signals),
        WeightedVotingSide.SELL: sum(signal.final_weight * signal.p_sell for signal in effective_signals),
        WeightedVotingSide.HOLD: sum(signal.final_weight * signal.p_hold for signal in effective_signals),
    }
    normalized_scores = _normalized_side_scores(raw_scores)
    winning_side, winner_score, second_best_score, winner_edge = _winning_side(normalized_scores, active_config)
    buy_score = round(raw_scores[WeightedVotingSide.BUY], 4)
    sell_score = round(raw_scores[WeightedVotingSide.SELL], 4)
    hold_score = round(raw_scores[WeightedVotingSide.HOLD], 4)
    active_strategy_count = sum(1 for signal in effective_signals if signal.eligible and signal.data_ready and signal.final_weight > 0)
    directional_strategy_count = sum(
        1
        for signal in effective_signals
        if signal.eligible and signal.data_ready and signal.final_weight > 0 and signal.signal in (WeightedVotingSide.BUY.value, WeightedVotingSide.SELL.value)
    )
    active_weight = round(sum(signal.final_weight for signal in effective_signals if signal.eligible and signal.data_ready), 10)
    data_ready = all(signal.data_ready for signal in signals)
    eligibility_failures = _eligibility_failures(
        data_ready=data_ready,
        active_strategy_count=active_strategy_count,
        directional_strategy_count=directional_strategy_count,
        active_weight=active_weight,
        winning_side=winning_side,
        winner_score=winner_score,
        winner_edge=winner_edge,
        config=active_config,
    )
    eligible = not eligibility_failures
    final_side = winning_side if eligible else WeightedVotingSide.HOLD
    reason_codes = tuple(eligibility_failures)
    vote_scores = WeightedVoteScores(
        buy_score=buy_score,
        sell_score=sell_score,
        hold_score=hold_score,
        normalized_buy_score=round(normalized_scores[WeightedVotingSide.BUY], 10),
        normalized_sell_score=round(normalized_scores[WeightedVotingSide.SELL], 10),
        normalized_hold_score=round(normalized_scores[WeightedVotingSide.HOLD], 10),
        winning_side=winning_side,
        winner_score=round(winner_score, 10),
        second_best_score=round(second_best_score, 10),
        winner_edge=round(winner_edge, 10),
        active_strategy_count=active_strategy_count,
        directional_strategy_count=directional_strategy_count,
        active_weight=active_weight,
        family_contributions=_family_contributions(effective_signals),
        disagreement_score=round(1.0 - winner_score, 10),
        max_score=round(winner_score, 10),
        margin=round(winner_edge, 10),
        raw_winner=winning_side,
        data_timestamp=data_timestamp,
        explanation="Weighted Voting scores and winner are deterministic weighted sums of Buy/Sell/Hold probabilities.",
    )
    return WeightedVotingDecision(
        decision_id=f"weighted-voting-{data_timestamp.isoformat()}",
        configuration_version=active_config.config_version,
        strategy_catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        weight_version="weighted_weights_v1",
        data_timestamp=data_timestamp,
        settings_version=active_config.config_version,
        proposed_side=final_side,
        proposed_quantity=0,
        reason_codes=reason_codes,
        vote_scores=vote_scores,
        weight_adjustments=weight_result.adjustments,
        signal=final_side,
        raw_winner=winning_side,
        eligible=eligible,
        data_ready=data_ready,
        configuration_hash=active_config.configuration_hash,
        explanation="Final direction came only from deterministic weighted aggregation; context may reject but never reverse it.",
    )


def _normalized_side_scores(scores: dict[WeightedVotingSide, float]) -> dict[WeightedVotingSide, float]:
    total = sum(max(0.0, scores[side]) for side in SIDE_ORDER)
    if total <= 0:
        return {
            WeightedVotingSide.BUY: 0.0,
            WeightedVotingSide.SELL: 0.0,
            WeightedVotingSide.HOLD: 1.0,
        }
    return {side: max(0.0, scores[side]) / total for side in SIDE_ORDER}


def _winning_side(scores: dict[WeightedVotingSide, float], config: WeightedVotingConfig) -> tuple[WeightedVotingSide, float, float, float]:
    ordered = sorted(((side, scores[side]) for side in SIDE_ORDER), key=lambda item: item[1], reverse=True)
    winner, winner_score = ordered[0]
    second_best_score = ordered[1][1]
    winner_edge = max(0.0, winner_score - second_best_score)
    if winner_edge <= config.aggregation_tie_tolerance:
        return WeightedVotingSide.HOLD, winner_score, second_best_score, winner_edge
    return winner, winner_score, second_best_score, winner_edge


def _eligibility_failures(
    *,
    data_ready: bool,
    active_strategy_count: int,
    directional_strategy_count: int,
    active_weight: float,
    winning_side: WeightedVotingSide,
    winner_score: float,
    winner_edge: float,
    config: WeightedVotingConfig,
) -> list[str]:
    failures: list[str] = []
    if not data_ready:
        failures.append("weighted_voting.data_not_ready")
    if active_strategy_count < config.minimum_active_strategies:
        failures.append("weighted_voting.insufficient_active_strategies")
    if active_weight + config.aggregation_tie_tolerance < config.minimum_active_weight:
        failures.append("weighted_voting.insufficient_active_weight")
    if winning_side == WeightedVotingSide.HOLD:
        failures.append("weighted_voting.hold_or_tie_winner")
    else:
        if directional_strategy_count < config.minimum_directional_strategies:
            failures.append("weighted_voting.insufficient_directional_strategies")
        if winner_score + config.aggregation_tie_tolerance < config.minimum_score:
            failures.append("weighted_voting.insufficient_winner_score")
        if winner_edge + config.aggregation_tie_tolerance < config.minimum_edge:
            failures.append("weighted_voting.insufficient_winner_edge")
    return failures


def _family_contributions(signals: tuple[WeightedVotingSignal, ...]) -> dict[str, dict[str, float]]:
    contributions: dict[str, dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "hold": 0.0, "weight": 0.0})
    for signal in signals:
        family = str(signal.family)
        contributions[family]["buy"] += signal.final_weight * signal.p_buy
        contributions[family]["sell"] += signal.final_weight * signal.p_sell
        contributions[family]["hold"] += signal.final_weight * signal.p_hold
        contributions[family]["weight"] += signal.final_weight
    return {
        family: {key: round(value, 10) for key, value in values.items()}
        for family, values in sorted(contributions.items())
    }
