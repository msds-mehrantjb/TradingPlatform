"""Weighted Voting local decision gates before the global gate interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.models import (
    WeightedDataQualityStatus,
    WeightedDecision,
    WeightedGateResult,
    WeightedGateStatus,
    WeightedMarketSnapshot,
    WeightedPositionState,
    WeightedSide,
    WeightedVotingSignal,
)


WEIGHTED_VOTING_DECISION_GATES_VERSION = "weighted_voting_decision_gates_v2"


class WeightedFiveMinuteAlignment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    UNAVAILABLE = "unavailable"


class WeightedGateEvaluationMode(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


@dataclass(frozen=True)
class WeightedVotingLocalGateInputs:
    decision: WeightedDecision
    signals: tuple[WeightedVotingSignal, ...]
    market_snapshot: WeightedMarketSnapshot | None
    five_minute_alignment: WeightedFiveMinuteAlignment | str
    expected_value_after_costs: float
    spread_cost: float
    slippage_cost: float
    fee_cost: float
    atr_percent: float | None
    entry_quality: float
    session_allowed: bool
    weighted_daily_loss_percent: float
    weighted_daily_trade_count: int
    capital_available: float
    current_position: WeightedPositionState | None = None
    minimum_candle_history: int = 1
    market_condition_eligible: bool = True
    cooldown_active: bool = False
    remaining_weighted_capital_partition: float | None = None
    mode: WeightedGateEvaluationMode | str = WeightedGateEvaluationMode.MANUAL
    data_timestamp: datetime | None = None


@dataclass(frozen=True)
class WeightedVotingGatePipelineResult:
    permission_granted: bool
    mode: WeightedGateEvaluationMode | str
    gate_results: tuple[WeightedGateResult, ...]
    reason_codes: tuple[str, ...]
    explanation: str


def evaluate_local_decision_gates(
    inputs: WeightedVotingLocalGateInputs,
    *,
    config: WeightedVotingConfig | None = None,
) -> WeightedVotingGatePipelineResult:
    active_config = config or WeightedVotingConfig()
    data_timestamp = inputs.data_timestamp or inputs.decision.data_timestamp
    mandatory_gates = (
        _data_freshness(inputs, active_config, data_timestamp),
        _minimum_candle_history(inputs, data_timestamp),
        _valid_weighted_winner(inputs, data_timestamp),
        _minimum_active_strategy_count(inputs, active_config, data_timestamp),
        _minimum_directional_strategy_count(inputs, active_config, data_timestamp),
        _minimum_active_weight_coverage(inputs, active_config, data_timestamp),
        _minimum_winner_score(inputs, active_config, data_timestamp),
        _minimum_winner_edge(inputs, active_config, data_timestamp),
        _maximum_family_concentration(inputs, active_config, data_timestamp),
        _maximum_conflicting_weight(inputs, active_config, data_timestamp),
        _acceptable_disagreement(inputs, active_config, data_timestamp),
        _acceptable_strategy_data_quality(inputs, data_timestamp),
        _five_minute_confirmation(inputs, data_timestamp),
        _market_condition_eligibility(inputs, data_timestamp),
        _positive_expected_value(inputs, active_config, data_timestamp),
        _local_spread_threshold(inputs, active_config, data_timestamp),
        _local_slippage_threshold(inputs, active_config, data_timestamp),
        _local_liquidity_threshold(inputs, active_config, data_timestamp),
        _valid_atr_range(inputs, active_config, data_timestamp),
        _entry_quality(inputs, active_config, data_timestamp),
        _allowed_session_window(inputs, data_timestamp),
        _weighted_daily_loss(inputs, active_config, data_timestamp),
        _weighted_trade_count_limit(inputs, active_config, data_timestamp),
        _weighted_cooldown(inputs, data_timestamp),
        _existing_position(inputs, active_config, data_timestamp),
        _weighted_capital_availability(inputs, active_config, data_timestamp),
        _weighted_capital_partition_availability(inputs, active_config, data_timestamp),
        _weighted_pyramiding_rule(inputs, active_config, data_timestamp),
    )
    gates = mandatory_gates + (_final_local_acceptance(mandatory_gates, data_timestamp),)
    failures = tuple(
        reason_code
        for gate in gates
        if gate.status == WeightedGateStatus.FAIL.value and gate.blocks_order
        for reason_code in gate.reason_codes
    )
    return WeightedVotingGatePipelineResult(
        permission_granted=not failures,
        mode=inputs.mode,
        gate_results=gates,
        reason_codes=failures,
        explanation="Manual and automatic Weighted Voting modes use this same local gate permission result; automatic mode may only automate submission.",
    )


def all_gates_pass(gates: tuple[WeightedGateResult, ...]) -> bool:
    return all(gate.status != WeightedGateStatus.FAIL.value or not gate.blocks_order for gate in gates)


def _gate(
    gate_id: str,
    gate_name: str,
    passed: bool,
    data_timestamp: datetime,
    reason_code: str,
    explanation: str,
    *,
    informational: bool = False,
) -> WeightedGateResult:
    status = WeightedGateStatus.INFO if informational else WeightedGateStatus.PASS if passed else WeightedGateStatus.FAIL
    return WeightedGateResult(
        gate_id=gate_id,
        gate_name=gate_name,
        status=status,
        blocks_order=not passed and not informational,
        data_timestamp=data_timestamp,
        reason_codes=() if passed or informational else (reason_code,),
        explanation=explanation,
    )


def _valid_weighted_winner(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    valid = inputs.decision.raw_winner in (WeightedSide.BUY.value, WeightedSide.SELL.value) and inputs.decision.signal == inputs.decision.raw_winner
    return _gate(
        "weighted_winner",
        "Valid Weighted Winner",
        valid,
        data_timestamp,
        "weighted_voting.gate.invalid_weighted_winner",
        "Weighted winner must be a final Buy or Sell from weighted aggregation only.",
    )


def _data_freshness(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    freshness = _data_freshness_seconds(inputs.market_snapshot, data_timestamp)
    return _gate(
        "data_freshness",
        "Data Freshness",
        freshness is not None and freshness <= config.stale_after_seconds,
        data_timestamp,
        "weighted_voting.gate.stale_market_data",
        f"Market data freshness {freshness if freshness is not None else 'unavailable'} seconds must be within {config.stale_after_seconds} seconds.",
    )


def _minimum_candle_history(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    actual = _completed_candle_count(inputs.market_snapshot, data_timestamp)
    required = max(inputs.minimum_candle_history, _required_candle_history_from_signals(inputs.signals))
    return _gate(
        "minimum_candle_history",
        "Minimum Candle History",
        actual >= required,
        data_timestamp,
        "weighted_voting.gate.insufficient_candle_history",
        f"Completed one-minute candle count {actual} must meet required history {required}.",
    )


def _minimum_active_strategy_count(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.active_strategy_count
    return _gate(
        "minimum_active_strategy_count",
        "Minimum Active Strategy Count",
        actual >= config.minimum_active_strategies,
        data_timestamp,
        "weighted_voting.gate.insufficient_active_strategy_count",
        f"Active strategy count {actual} must meet {config.minimum_active_strategies}.",
    )


def _minimum_directional_strategy_count(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.directional_strategy_count
    return _gate(
        "minimum_directional_strategy_count",
        "Minimum Directional Strategy Count",
        actual >= config.minimum_directional_strategies,
        data_timestamp,
        "weighted_voting.gate.insufficient_directional_strategy_count",
        f"Directional strategy count {actual} must meet {config.minimum_directional_strategies}.",
    )


def _minimum_active_weight_coverage(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.effective_weight_coverage
    fallback = inputs.decision.vote_scores.active_weight
    coverage = actual if actual > 0 else fallback
    return _gate(
        "minimum_active_weight_coverage",
        "Minimum Active Weight Coverage",
        coverage + config.aggregation_tie_tolerance >= config.minimum_active_weight,
        data_timestamp,
        "weighted_voting.gate.insufficient_active_weight_coverage",
        f"Effective weight coverage {coverage:.4f} must meet {config.minimum_active_weight:.4f}.",
    )


def _minimum_winner_score(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.winner_score
    return _gate(
        "minimum_winner_score",
        "Minimum Winner Score",
        actual >= config.minimum_score,
        data_timestamp,
        "weighted_voting.gate.insufficient_winner_score",
        f"Winner score {actual:.4f} must meet {config.minimum_score:.4f}.",
    )


def _minimum_winner_edge(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.winner_edge
    return _gate(
        "minimum_winner_edge",
        "Minimum Winner Edge",
        actual >= config.minimum_edge,
        data_timestamp,
        "weighted_voting.gate.insufficient_winner_edge",
        f"Winner edge {actual:.4f} must meet {config.minimum_edge:.4f}.",
    )


def _maximum_family_concentration(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    maximum = inputs.decision.vote_scores.family_concentration or max((family.get("weight", 0.0) for family in inputs.decision.vote_scores.family_contributions.values()), default=0.0)
    return _gate(
        "maximum_family_concentration",
        "Maximum Family Concentration",
        maximum <= config.maximum_family_weight + config.aggregation_tie_tolerance,
        data_timestamp,
        "weighted_voting.gate.family_concentration_exceeded",
        f"Maximum family contribution {maximum:.4f} must not exceed {config.maximum_family_weight:.4f}.",
    )


def _maximum_conflicting_weight(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.disagreement_score
    return _gate(
        "maximum_conflicting_weight_percentage",
        "Maximum Conflicting-Weight Percentage",
        actual <= config.maximum_disagreement_score,
        data_timestamp,
        "weighted_voting.gate.conflicting_weight_too_high",
        f"Conflicting weight percentage {actual:.4f} must not exceed {config.maximum_disagreement_score:.4f}.",
    )


def _acceptable_disagreement(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    actual = inputs.decision.vote_scores.disagreement_score
    return _gate(
        "acceptable_disagreement",
        "Acceptable Disagreement",
        actual <= config.maximum_disagreement_score,
        data_timestamp,
        "weighted_voting.gate.disagreement_too_high",
        f"Disagreement score {actual:.4f} must not exceed {config.maximum_disagreement_score:.4f}.",
    )


def _acceptable_strategy_data_quality(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    bad = [
        signal.strategy_id
        for signal in inputs.signals
        if not signal.data_ready or signal.data_quality_status == WeightedDataQualityStatus.UNAVAILABLE.value
    ]
    return _gate(
        "acceptable_strategy_data_quality",
        "Acceptable Strategy Data Quality",
        not bad,
        data_timestamp,
        "weighted_voting.gate.unacceptable_strategy_data_quality",
        f"Unavailable or not-ready strategy data: {', '.join(bad) if bad else 'none'}.",
    )


def _five_minute_confirmation(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    alignment = _alignment_value(inputs.five_minute_alignment)
    if alignment == WeightedFiveMinuteAlignment.POSITIVE.value:
        return _gate("five_minute_confirmation", "Five-Minute Confirmation", True, data_timestamp, "", "Five-minute alignment is positive.")
    if alignment == WeightedFiveMinuteAlignment.NEUTRAL.value:
        return _gate(
            "five_minute_confirmation",
            "Five-Minute Confirmation",
            False,
            data_timestamp,
            "weighted_voting.gate.neutral_five_minute_alignment",
            "Five-minute alignment is neutral; it is informational and does not count as confirmed.",
            informational=True,
        )
    return _gate(
        "five_minute_confirmation",
        "Five-Minute Confirmation",
        False,
        data_timestamp,
        "weighted_voting.gate.negative_five_minute_alignment",
        "Five-minute alignment is negative or unavailable.",
    )


def _market_condition_eligibility(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "market_condition_eligibility",
        "Market-Condition Eligibility",
        inputs.market_condition_eligible,
        data_timestamp,
        "weighted_voting.gate.market_condition_not_eligible",
        "Weighted Voting market-condition classifier must permit local evaluation.",
    )


def _positive_expected_value(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    required = config.minimum_expected_value_after_costs + config.expected_value_safety_margin
    total_cost = inputs.spread_cost + inputs.slippage_cost + inputs.fee_cost + config.expected_value_safety_margin
    return _gate(
        "positive_expected_value_after_costs",
        "Positive Expected Value After Costs",
        inputs.expected_value_after_costs > required,
        data_timestamp,
        "weighted_voting.gate.nonpositive_expected_value_after_costs",
        f"Expected value after spread/slippage/fees/safety margin must be positive; supplied EV {inputs.expected_value_after_costs:.6f}, explicit cost stack {total_cost:.6f}.",
    )


def _local_spread_threshold(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    spread_percent = _spread_percent(inputs.market_snapshot)
    return _gate(
        "local_spread_threshold",
        "Local Spread Threshold",
        spread_percent is not None and spread_percent <= config.local_max_spread_percent,
        data_timestamp,
        "weighted_voting.gate.spread_too_wide",
        f"Spread percent {spread_percent if spread_percent is not None else 'unavailable'} must be at or below {config.local_max_spread_percent:.6f}.",
    )


def _local_slippage_threshold(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    maximum_slippage = max(config.expected_value_safety_margin, config.local_max_spread_percent)
    return _gate(
        "local_slippage_threshold",
        "Local Slippage Threshold",
        inputs.slippage_cost <= maximum_slippage,
        data_timestamp,
        "weighted_voting.gate.slippage_too_high",
        f"Slippage cost {inputs.slippage_cost:.6f} must not exceed {maximum_slippage:.6f}.",
    )


def _local_liquidity_threshold(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    volume = _latest_volume(inputs.market_snapshot)
    return _gate(
        "local_liquidity_threshold",
        "Local Liquidity Threshold",
        volume is not None and volume >= config.local_minimum_liquidity_volume,
        data_timestamp,
        "weighted_voting.gate.insufficient_liquidity",
        f"Latest volume {volume if volume is not None else 'unavailable'} must meet {config.local_minimum_liquidity_volume:.2f}.",
    )


def _valid_atr_range(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    atr = inputs.atr_percent
    return _gate(
        "valid_atr_range",
        "Valid ATR Range",
        atr is not None and config.minimum_atr_percent <= atr <= config.maximum_atr_percent,
        data_timestamp,
        "weighted_voting.gate.invalid_atr_range",
        f"ATR percent {atr if atr is not None else 'unavailable'} must be within configured range.",
    )


def _entry_quality(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "entry_quality",
        "Entry Quality",
        inputs.entry_quality >= config.minimum_entry_quality,
        data_timestamp,
        "weighted_voting.gate.entry_quality_too_low",
        f"Entry quality {inputs.entry_quality:.4f} must meet {config.minimum_entry_quality:.4f}.",
    )


def _allowed_session_window(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "allowed_weighted_voting_session_window",
        "Allowed Weighted Voting Session Window",
        inputs.session_allowed,
        data_timestamp,
        "weighted_voting.gate.session_window_closed",
        "Decision timestamp must be inside the allowed Weighted Voting session window.",
    )


def _weighted_daily_loss(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "weighted_voting_daily_loss",
        "Weighted Voting Daily Loss",
        inputs.weighted_daily_loss_percent <= config.maximum_weighted_daily_loss_percent,
        data_timestamp,
        "weighted_voting.gate.daily_loss_limit_exceeded",
        f"Weighted Voting daily loss {inputs.weighted_daily_loss_percent:.4f}% must not exceed {config.maximum_weighted_daily_loss_percent:.4f}%.",
    )


def _weighted_trade_count_limit(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "weighted_voting_trade_count_limit",
        "Weighted Voting Trade-Count Limit",
        inputs.weighted_daily_trade_count < config.maximum_weighted_daily_trades,
        data_timestamp,
        "weighted_voting.gate.trade_count_limit_reached",
        f"Weighted Voting daily trade count {inputs.weighted_daily_trade_count} must be below {config.maximum_weighted_daily_trades}.",
    )


def _weighted_cooldown(inputs: WeightedVotingLocalGateInputs, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "weighted_voting_cooldown",
        "Weighted Voting Cooldown",
        not inputs.cooldown_active,
        data_timestamp,
        "weighted_voting.gate.cooldown_active",
        "Weighted Voting algorithm-local cooldown must be inactive before accepting a new entry.",
    )


def _existing_position(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    open_position = inputs.current_position is not None and inputs.current_position.quantity != 0
    return _gate(
        "existing_position",
        "Existing Position",
        config.allow_weighted_pyramiding or not open_position,
        data_timestamp,
        "weighted_voting.gate.existing_position_blocks_entry",
        "Existing Weighted Voting position blocks a new entry unless Weighted Voting pyramiding is enabled.",
    )


def _weighted_capital_availability(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    return _gate(
        "weighted_voting_capital_availability",
        "Weighted Voting Capital Availability",
        inputs.capital_available >= config.minimum_capital_available,
        data_timestamp,
        "weighted_voting.gate.insufficient_capital",
        f"Available capital {inputs.capital_available:.2f} must meet {config.minimum_capital_available:.2f}.",
    )


def _weighted_capital_partition_availability(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    available = inputs.remaining_weighted_capital_partition if inputs.remaining_weighted_capital_partition is not None else inputs.capital_available
    return _gate(
        "weighted_voting_capital_partition_availability",
        "Weighted Voting Capital-Partition Availability",
        available >= config.minimum_capital_available,
        data_timestamp,
        "weighted_voting.gate.insufficient_capital_partition",
        f"Weighted Voting capital partition availability {available:.2f} must meet {config.minimum_capital_available:.2f}.",
    )


def _weighted_pyramiding_rule(inputs: WeightedVotingLocalGateInputs, config: WeightedVotingConfig, data_timestamp: datetime) -> WeightedGateResult:
    open_position = inputs.current_position is not None and inputs.current_position.quantity != 0
    return _gate(
        "weighted_voting_pyramiding_rule",
        "Weighted Voting Pyramiding Rule",
        config.allow_weighted_pyramiding or not open_position,
        data_timestamp,
        "weighted_voting.gate.pyramiding_not_allowed",
        "Weighted Voting cannot add to an existing position unless pyramiding is explicitly enabled.",
    )


def _final_local_acceptance(gates: tuple[WeightedGateResult, ...], data_timestamp: datetime) -> WeightedGateResult:
    passed = all_gates_pass(gates)
    return _gate(
        "final_local_acceptance",
        "Final Local Acceptance",
        passed,
        data_timestamp,
        "weighted_voting.gate.final_local_acceptance_failed",
        "All mandatory Weighted Voting local gates must pass before routing to account-wide global gates.",
    )


def _alignment_value(alignment: WeightedFiveMinuteAlignment | str) -> str:
    return alignment.value if isinstance(alignment, WeightedFiveMinuteAlignment) else str(alignment)


def _spread_percent(snapshot: WeightedMarketSnapshot | None) -> float | None:
    if snapshot is None or snapshot.bid is None or snapshot.ask is None:
        return None
    midpoint = (snapshot.bid + snapshot.ask) / 2.0
    if midpoint <= 0:
        return None
    return (snapshot.ask - snapshot.bid) / midpoint


def _latest_volume(snapshot: WeightedMarketSnapshot | None) -> float | None:
    if snapshot is None or not snapshot.one_minute_candles:
        return None
    return snapshot.one_minute_candles[-1].volume


def _data_freshness_seconds(snapshot: WeightedMarketSnapshot | None, data_timestamp: datetime) -> float | None:
    if snapshot is None:
        return None
    if snapshot.data_freshness_seconds is not None:
        return snapshot.data_freshness_seconds
    if not snapshot.one_minute_candles:
        return None
    return max(0.0, (data_timestamp - snapshot.one_minute_candles[-1].timestamp).total_seconds())


def _completed_candle_count(snapshot: WeightedMarketSnapshot | None, data_timestamp: datetime) -> int:
    if snapshot is None:
        return 0
    return sum(1 for candle in snapshot.one_minute_candles if candle.timestamp <= data_timestamp)


def _required_candle_history_from_signals(signals: tuple[WeightedVotingSignal, ...]) -> int:
    required = 1
    for signal in signals:
        value = signal.feature_snapshot.get("required_one_minute_candles")
        if value is not None:
            required = max(required, int(float(value)))
    return required
