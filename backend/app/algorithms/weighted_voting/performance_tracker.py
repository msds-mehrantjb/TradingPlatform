"""Dedicated performance tracking for the Weighted Voting algorithm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import math
from statistics import pstdev
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_PERFORMANCE_TRACKER_VERSION = "weighted_voting_performance_tracker_v2"
WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE = "weighted_voting.performance_tracker"


@dataclass(frozen=True)
class WeightedVotingPerformanceSnapshot:
    strategy_id: str
    trade_count: int
    recent_expectancy: float | None
    explanation: str


@dataclass(frozen=True)
class WeightedVotingTrackedTrade:
    algorithm_id: Literal["weighted_voting"]
    trade_id: str
    decision_id: str
    symbol: str
    side: str
    quantity: int
    entry_time: datetime
    exit_time: datetime
    gross_pnl: float
    net_pnl: float
    total_costs: float
    stop: float | None
    target: float | None
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float
    exit_reason: str
    owning_strategy_ids: tuple[str, ...]
    weight_version: str
    settings_version: str
    trend_condition: str = "unknown"
    volatility_condition: str = "unknown"
    session_period: str = "unknown"
    confidence_by_strategy: dict[str, float] | None = None
    weight_by_strategy: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("foreign algorithm trade cannot enter Weighted Voting performance tracking")
        if self.quantity <= 0:
            raise ValueError("Weighted Voting tracked trades require positive quantity")
        if self.exit_time < self.entry_time:
            raise ValueError("Weighted Voting tracked trade cannot exit before entry")

    @property
    def hold_minutes(self) -> float:
        return max(0.0, (self.exit_time - self.entry_time).total_seconds() / 60.0)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_time"] = self.entry_time.isoformat()
        payload["exit_time"] = self.exit_time.isoformat()
        return _camel_payload(payload)


@dataclass(frozen=True)
class WeightedVotingSignalObservation:
    algorithm_id: Literal["weighted_voting"]
    strategy_id: str
    decision_id: str
    eligible: bool
    directional: bool
    confidence: float
    active_weight: float
    weight_version: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("foreign algorithm signal cannot enter Weighted Voting performance tracking")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("Weighted Voting signal confidence must be between zero and one")
        if self.active_weight < 0:
            raise ValueError("Weighted Voting active weight must be non-negative")


@dataclass(frozen=True)
class WeightedVotingWeightVersionSnapshot:
    algorithm_id: Literal["weighted_voting"]
    weight_version: str
    effective_at: datetime
    strategy_weights: dict[str, float]
    previous_weight_version: str | None = None
    previous_strategy_weights: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("foreign algorithm weights cannot enter Weighted Voting performance tracking")
        if any(weight < 0 for weight in self.strategy_weights.values()):
            raise ValueError("Weighted Voting strategy weights must be non-negative")


@dataclass(frozen=True)
class WeightedVotingAlgorithmPerformance:
    algorithm_id: Literal["weighted_voting"]
    trade_count: int
    net_return: float
    gross_return: float
    total_costs: float
    win_rate: float
    profit_factor: float
    expectancy: float
    sharpe_like: float
    maximum_drawdown: float
    daily_loss: float
    average_hold_minutes: float
    trade_frequency: float

    def as_dict(self) -> dict[str, Any]:
        return _camel_payload(asdict(self))


@dataclass(frozen=True)
class WeightedVotingStrategyPerformance:
    algorithm_id: Literal["weighted_voting"]
    strategy_id: str
    eligible_signal_count: int
    directional_signal_count: int
    contributing_trade_count: int
    win_rate: float
    expectancy: float
    profit_factor: float
    drawdown_contribution: float
    average_confidence: float
    confidence_calibration: float
    weight_contribution: float
    marginal_contribution: float

    def as_dict(self) -> dict[str, Any]:
        return _camel_payload(asdict(self))


@dataclass(frozen=True)
class WeightedVotingPerformanceBucket:
    bucket_id: str
    trade_count: int
    net_return: float
    gross_return: float
    total_costs: float
    win_rate: float
    expectancy: float
    profit_factor: float
    performance_after_costs: float

    def as_dict(self) -> dict[str, Any]:
        return _camel_payload(asdict(self))


@dataclass(frozen=True)
class WeightedVotingMarketConditionPerformance:
    algorithm_id: Literal["weighted_voting"]
    by_trend_or_range_condition: dict[str, WeightedVotingPerformanceBucket]
    by_volatility_condition: dict[str, WeightedVotingPerformanceBucket]
    by_session_period: dict[str, WeightedVotingPerformanceBucket]
    by_long_short_direction: dict[str, WeightedVotingPerformanceBucket]

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "byTrendOrRangeCondition": {key: value.as_dict() for key, value in self.by_trend_or_range_condition.items()},
            "byVolatilityCondition": {key: value.as_dict() for key, value in self.by_volatility_condition.items()},
            "bySessionPeriod": {key: value.as_dict() for key, value in self.by_session_period.items()},
            "byLongShortDirection": {key: value.as_dict() for key, value in self.by_long_short_direction.items()},
        }


@dataclass(frozen=True)
class WeightedVotingWeightVersionPerformance:
    algorithm_id: Literal["weighted_voting"]
    weight_version: str
    previous_weight_version: str | None
    performance_before_update: float | None
    performance_after_update: float
    attribution_of_improvement_or_degradation: float | None
    stability_of_weight_changes: float
    trade_count: int
    profit_factor: float
    maximum_drawdown: float

    def as_dict(self) -> dict[str, Any]:
        return _camel_payload(asdict(self))


@dataclass(frozen=True)
class WeightedVotingPerformanceReport:
    tracker_version: str
    algorithm_id: Literal["weighted_voting"]
    evaluated_at: datetime
    algorithm_level: WeightedVotingAlgorithmPerformance
    strategy_level: dict[str, WeightedVotingStrategyPerformance]
    market_condition_level: WeightedVotingMarketConditionPerformance
    weight_version_level: dict[str, WeightedVotingWeightVersionPerformance]
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "trackerVersion": self.tracker_version,
            "algorithmId": self.algorithm_id,
            "evaluatedAt": self.evaluated_at.isoformat(),
            "algorithmLevel": self.algorithm_level.as_dict(),
            "strategyLevel": {key: value.as_dict() for key, value in self.strategy_level.items()},
            "marketConditionLevel": self.market_condition_level.as_dict(),
            "weightVersionLevel": {key: value.as_dict() for key, value in self.weight_version_level.items()},
            "reasonCodes": list(self.reason_codes),
            "explanation": self.explanation,
        }


def build_weighted_voting_performance_report(
    *,
    trades: tuple[WeightedVotingTrackedTrade, ...],
    signal_observations: tuple[WeightedVotingSignalObservation, ...] = (),
    weight_snapshots: tuple[WeightedVotingWeightVersionSnapshot, ...] = (),
    evaluated_at: datetime,
    starting_equity: float = 100_000.0,
) -> WeightedVotingPerformanceReport:
    _validate_owned_inputs(trades, signal_observations, weight_snapshots)
    algorithm = _algorithm_performance(trades, starting_equity)
    strategy = _strategy_performance(trades, signal_observations)
    market = _market_condition_performance(trades, starting_equity)
    weight_versions = _weight_version_performance(trades, weight_snapshots)
    return WeightedVotingPerformanceReport(
        tracker_version=WEIGHTED_VOTING_PERFORMANCE_TRACKER_VERSION,
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        evaluated_at=evaluated_at,
        algorithm_level=algorithm,
        strategy_level=strategy,
        market_condition_level=market,
        weight_version_level=weight_versions,
        reason_codes=("weighted_voting.performance.tracked_in_dedicated_namespace",),
        explanation="Weighted Voting performance report computed only from Weighted Voting-owned trades, signals, and weight versions.",
    )


def persist_weighted_voting_performance_report(store: WeightedVotingStateStore, report: WeightedVotingPerformanceReport, *, key: str | None = None) -> None:
    if report.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("foreign performance report cannot be persisted under Weighted Voting")
    store.write_snapshot(key or f"{WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE}.latest", report.as_dict())


def performance_tracker_status() -> dict[str, Any]:
    return {
        "trackerVersion": WEIGHTED_VOTING_PERFORMANCE_TRACKER_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE,
        "trackingLevels": ["algorithm", "strategy", "market_condition", "weight_version"],
        "algorithmMetrics": ["net_return", "gross_return", "total_costs", "win_rate", "profit_factor", "expectancy", "sharpe_like", "maximum_drawdown", "daily_loss", "average_hold_time", "trade_frequency"],
        "strategyMetrics": ["eligible_signals", "directional_signals", "contributing_trades", "win_rate", "expectancy", "profit_factor", "drawdown_contribution", "average_confidence", "confidence_calibration", "weight_contribution", "marginal_contribution"],
        "marketConditionMetrics": ["trend_range_condition", "volatility_condition", "session_period", "long_short_direction", "performance_after_costs"],
        "weightVersionMetrics": ["performance_before_update", "performance_after_update", "attribution_of_improvement_or_degradation", "stability_of_weight_changes"],
        "ownershipRule": "only_weighted_voting_attributed_trades_signals_and_weights",
    }


def _algorithm_performance(trades: tuple[WeightedVotingTrackedTrade, ...], starting_equity: float) -> WeightedVotingAlgorithmPerformance:
    values = [trade.net_pnl for trade in trades]
    gross_values = [trade.gross_pnl for trade in trades]
    total_costs = sum(trade.total_costs for trade in trades)
    net_pnl = sum(values)
    gross_pnl = sum(gross_values)
    days = {trade.exit_time.date() for trade in trades}
    daily_pnl: dict[Any, float] = {}
    for trade in trades:
        day = trade.exit_time.date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + trade.net_pnl
    return WeightedVotingAlgorithmPerformance(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        trade_count=len(trades),
        net_return=round(_return_value(net_pnl, starting_equity), 10),
        gross_return=round(_return_value(gross_pnl, starting_equity), 10),
        total_costs=round(total_costs, 10),
        win_rate=round(_win_rate(values), 10),
        profit_factor=round(_profit_factor(values), 10),
        expectancy=round(_mean(values), 10),
        sharpe_like=round(_sharpe_like(values), 10),
        maximum_drawdown=round(_maximum_drawdown(values), 10),
        daily_loss=round(abs(min(0.0, min(daily_pnl.values(), default=0.0))), 10),
        average_hold_minutes=round(_mean([trade.hold_minutes for trade in trades]), 10),
        trade_frequency=round(len(trades) / max(1, len(days)), 10),
    )


def _strategy_performance(
    trades: tuple[WeightedVotingTrackedTrade, ...],
    signal_observations: tuple[WeightedVotingSignalObservation, ...],
) -> dict[str, WeightedVotingStrategyPerformance]:
    strategy_ids = sorted(
        {
            *[strategy_id for trade in trades for strategy_id in trade.owning_strategy_ids],
            *[observation.strategy_id for observation in signal_observations],
        }
    )
    result: dict[str, WeightedVotingStrategyPerformance] = {}
    for strategy_id in strategy_ids:
        observations = [item for item in signal_observations if item.strategy_id == strategy_id]
        contributing = [trade for trade in trades if strategy_id in trade.owning_strategy_ids]
        allocated_returns = [_allocated_trade_return(trade) for trade in contributing]
        confidences = [item.confidence for item in observations]
        if not confidences:
            confidences = [float((trade.confidence_by_strategy or {}).get(strategy_id, 0.0)) for trade in contributing if trade.confidence_by_strategy and strategy_id in trade.confidence_by_strategy]
        weight_values = [item.active_weight for item in observations]
        if not weight_values:
            weight_values = [float((trade.weight_by_strategy or {}).get(strategy_id, 0.0)) for trade in contributing if trade.weight_by_strategy and strategy_id in trade.weight_by_strategy]
        win_rate = _win_rate(allocated_returns)
        average_confidence = _mean(confidences)
        result[strategy_id] = WeightedVotingStrategyPerformance(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            strategy_id=strategy_id,
            eligible_signal_count=sum(1 for item in observations if item.eligible),
            directional_signal_count=sum(1 for item in observations if item.directional),
            contributing_trade_count=len(contributing),
            win_rate=round(win_rate, 10),
            expectancy=round(_mean(allocated_returns), 10),
            profit_factor=round(_profit_factor(allocated_returns), 10),
            drawdown_contribution=round(_maximum_drawdown(allocated_returns), 10),
            average_confidence=round(average_confidence, 10),
            confidence_calibration=round(max(0.0, 1.0 - abs(average_confidence - win_rate)), 10) if observations or confidences else 0.0,
            weight_contribution=round(sum(weight_values), 10),
            marginal_contribution=round(_mean(allocated_returns), 10),
        )
    return result


def _market_condition_performance(trades: tuple[WeightedVotingTrackedTrade, ...], starting_equity: float) -> WeightedVotingMarketConditionPerformance:
    return WeightedVotingMarketConditionPerformance(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        by_trend_or_range_condition=_bucketed(trades, lambda trade: trade.trend_condition, starting_equity),
        by_volatility_condition=_bucketed(trades, lambda trade: trade.volatility_condition, starting_equity),
        by_session_period=_bucketed(trades, lambda trade: trade.session_period, starting_equity),
        by_long_short_direction=_bucketed(trades, lambda trade: "short" if trade.side.upper() in {"SELL", "SHORT"} else "long", starting_equity),
    )


def _weight_version_performance(
    trades: tuple[WeightedVotingTrackedTrade, ...],
    snapshots: tuple[WeightedVotingWeightVersionSnapshot, ...],
) -> dict[str, WeightedVotingWeightVersionPerformance]:
    by_version: dict[str, list[WeightedVotingTrackedTrade]] = {}
    for trade in trades:
        by_version.setdefault(trade.weight_version, []).append(trade)
    snapshot_by_version = {snapshot.weight_version: snapshot for snapshot in snapshots}
    versions = sorted(by_version)
    result: dict[str, WeightedVotingWeightVersionPerformance] = {}
    previous_expectancy: float | None = None
    previous_version: str | None = None
    for version in versions:
        version_trades = by_version[version]
        values = [trade.net_pnl for trade in version_trades]
        expectancy = _mean(values)
        snapshot = snapshot_by_version.get(version)
        declared_previous = snapshot.previous_weight_version if snapshot else previous_version
        before = previous_expectancy
        attribution = None if before is None else expectancy - before
        result[version] = WeightedVotingWeightVersionPerformance(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            weight_version=version,
            previous_weight_version=declared_previous,
            performance_before_update=round(before, 10) if before is not None else None,
            performance_after_update=round(expectancy, 10),
            attribution_of_improvement_or_degradation=round(attribution, 10) if attribution is not None else None,
            stability_of_weight_changes=round(_weight_stability(snapshot), 10),
            trade_count=len(version_trades),
            profit_factor=round(_profit_factor(values), 10),
            maximum_drawdown=round(_maximum_drawdown(values), 10),
        )
        previous_expectancy = expectancy
        previous_version = version
    return result


def _bucketed(trades: tuple[WeightedVotingTrackedTrade, ...], key_fn, starting_equity: float) -> dict[str, WeightedVotingPerformanceBucket]:
    grouped: dict[str, list[WeightedVotingTrackedTrade]] = {}
    for trade in trades:
        grouped.setdefault(str(key_fn(trade) or "unknown"), []).append(trade)
    return {
        key: _bucket(key, tuple(values), starting_equity)
        for key, values in sorted(grouped.items())
    }


def _bucket(bucket_id: str, trades: tuple[WeightedVotingTrackedTrade, ...], starting_equity: float) -> WeightedVotingPerformanceBucket:
    values = [trade.net_pnl for trade in trades]
    gross_values = [trade.gross_pnl for trade in trades]
    return WeightedVotingPerformanceBucket(
        bucket_id=bucket_id,
        trade_count=len(trades),
        net_return=round(_return_value(sum(values), starting_equity), 10),
        gross_return=round(_return_value(sum(gross_values), starting_equity), 10),
        total_costs=round(sum(trade.total_costs for trade in trades), 10),
        win_rate=round(_win_rate(values), 10),
        expectancy=round(_mean(values), 10),
        profit_factor=round(_profit_factor(values), 10),
        performance_after_costs=round(sum(values), 10),
    )


def _validate_owned_inputs(
    trades: tuple[WeightedVotingTrackedTrade, ...],
    signal_observations: tuple[WeightedVotingSignalObservation, ...],
    weight_snapshots: tuple[WeightedVotingWeightVersionSnapshot, ...],
) -> None:
    for item in (*trades, *signal_observations, *weight_snapshots):
        if getattr(item, "algorithm_id", None) != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting performance tracker received foreign algorithm input")


def _allocated_trade_return(trade: WeightedVotingTrackedTrade) -> float:
    strategy_count = max(1, len(trade.owning_strategy_ids))
    return trade.net_pnl / strategy_count


def _weight_stability(snapshot: WeightedVotingWeightVersionSnapshot | None) -> float:
    if snapshot is None or not snapshot.previous_strategy_weights:
        return 1.0
    keys = set(snapshot.strategy_weights) | set(snapshot.previous_strategy_weights)
    if not keys:
        return 1.0
    average_change = sum(abs(snapshot.strategy_weights.get(key, 0.0) - snapshot.previous_strategy_weights.get(key, 0.0)) for key in keys) / len(keys)
    return max(0.0, min(1.0, 1.0 - average_change))


def _return_value(pnl: float, starting_equity: float) -> float:
    if starting_equity <= 0:
        return pnl
    return pnl / starting_equity


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _win_rate(values: list[float]) -> float:
    return sum(1 for value in values if value > 0) / len(values) if values else 0.0


def _profit_factor(values: list[float]) -> float:
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses > 0:
        return wins / losses
    return 4.0 if wins > 0 else 0.0


def _sharpe_like(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    sigma = pstdev(values)
    if sigma <= 0:
        return 0.0
    return (_mean(values) / sigma) * math.sqrt(len(values))


def _maximum_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _camel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {_camel(key): _jsonable(value) for key, value in payload.items()}


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE",
    "WEIGHTED_VOTING_PERFORMANCE_TRACKER_VERSION",
    "WeightedVotingAlgorithmPerformance",
    "WeightedVotingMarketConditionPerformance",
    "WeightedVotingPerformanceBucket",
    "WeightedVotingPerformanceReport",
    "WeightedVotingPerformanceSnapshot",
    "WeightedVotingSignalObservation",
    "WeightedVotingStrategyPerformance",
    "WeightedVotingTrackedTrade",
    "WeightedVotingWeightVersionPerformance",
    "WeightedVotingWeightVersionSnapshot",
    "build_weighted_voting_performance_report",
    "performance_tracker_status",
    "persist_weighted_voting_performance_report",
]
