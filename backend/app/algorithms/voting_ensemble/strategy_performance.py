from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.voting_ensemble.family_performance_history import (
    VotingEnsembleFamilyPerformanceHistory,
    VotingEnsembleFamilyPerformanceWindow,
)
from backend.app.algorithms.voting_ensemble.reliability_history import (
    VOTING_ENSEMBLE_RELIABILITY_HISTORY_VERSION,
    VotingEnsembleReliabilityHistory,
    reliability_history_payload,
)


VOTING_ENSEMBLE_PERFORMANCE_TRACKER_VERSION = "voting_ensemble_performance_tracker_v1"


@dataclass(frozen=True)
class StrategyReliabilityEstimate:
    score: float
    version: str
    sourceWindow: dict[str, Any]
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class VotingEnsemblePerformanceSnapshot:
    version: str
    tradeCount: int
    winRate: float
    totalPnl: float
    expectancy: float
    profitFactor: float | None
    maxDrawdown: float
    sourceWindow: dict[str, Any]
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class VotingEnsembleStrategyPerformanceSnapshot:
    strategyId: str
    family: str | None
    signalCount: int
    averageConfidence: float
    averageReliability: float
    reasonCodes: tuple[str, ...]


class VotingEnsembleStrategyPerformanceTracker:
    """Voting Ensemble-owned lookup boundary for walk-forward reliability estimates."""

    fallback_version = VOTING_ENSEMBLE_PERFORMANCE_TRACKER_VERSION

    def reliability_for(
        self,
        *,
        raw_inputs: dict[str, Any],
        strategy_id: str,
        regime_key: str,
    ) -> StrategyReliabilityEstimate:
        history_estimate = self._reliability_from_history(raw_inputs=raw_inputs, strategy_id=strategy_id, regime_key=regime_key)
        if history_estimate is not None:
            return history_estimate
        performance = _record(raw_inputs.get("strategyPerformance"))
        strategies = _record(performance.get("strategies"))
        strategy_record = _record(strategies.get(strategy_id) or performance.get(strategy_id))
        regime_records = _record(strategy_record.get("regimes"))
        record = _record(regime_records.get(regime_key) or strategy_record)
        score = _number(record.get("walkForwardReliability") or record.get("reliability") or record.get("score"))
        if score is None:
            return StrategyReliabilityEstimate(
                score=0.5,
                version=self.fallback_version,
                sourceWindow={
                    "source": "voting_ensemble_performance_tracker_unavailable",
                    "strategyId": strategy_id,
                    "regimeKey": regime_key,
                },
                reasonCodes=("voting_ensemble.historical_reliability.unavailable_neutral_fallback",),
            )
        return StrategyReliabilityEstimate(
            score=max(0.0, min(1.0, score)),
            version=str(record.get("version") or performance.get("version") or self.fallback_version),
            sourceWindow={
                "source": "voting_ensemble_walk_forward_performance_tracker",
                "strategyId": strategy_id,
                "regimeKey": regime_key,
                "window": record.get("window") or record.get("sourceWindow") or strategy_record.get("window"),
                "sampleSize": record.get("sampleSize"),
            },
            reasonCodes=("voting_ensemble.historical_reliability.walk_forward_lookup",),
        )

    def _reliability_from_history(
        self,
        *,
        raw_inputs: dict[str, Any],
        strategy_id: str,
        regime_key: str,
    ) -> StrategyReliabilityEstimate | None:
        observations = raw_inputs.get("strategyReliabilityHistory")
        if not isinstance(observations, list):
            return None
        window = VotingEnsembleReliabilityHistory().window_for(
            observations=observations,
            strategy_id=strategy_id,
            regime_key=regime_key,
        )
        return StrategyReliabilityEstimate(
            score=window.reliability,
            version=window.version,
            sourceWindow={
                **reliability_history_payload(window),
                "source": "voting_ensemble_strategy_reliability_history",
            },
            reasonCodes=window.reasonCodes,
        )

    def snapshot_from_backtest_result(self, result: dict[str, Any]) -> VotingEnsemblePerformanceSnapshot:
        trades = result.get("trades") if isinstance(result.get("trades"), list) else []
        trade_count = int(_number(result.get("totalTrades")) or len(trades))
        total_pnl = _number(result.get("totalPnl") or result.get("totalPnL"))
        if total_pnl is None:
            total_pnl = sum(_number(_record(trade).get("pnl")) or 0.0 for trade in trades)
        winners = int(_number(result.get("winners")) or sum(1 for trade in trades if (_number(_record(trade).get("pnl")) or 0.0) > 0))
        gross_profit = _number(result.get("grossProfit"))
        gross_loss = _number(result.get("grossLoss"))
        if gross_profit is None:
            gross_profit = sum(max(0.0, _number(_record(trade).get("pnl")) or 0.0) for trade in trades)
        if gross_loss is None:
            gross_loss = abs(sum(min(0.0, _number(_record(trade).get("pnl")) or 0.0) for trade in trades))
        profit_factor = (round(gross_profit / gross_loss, 4) if gross_loss else None)
        return VotingEnsemblePerformanceSnapshot(
            version=VOTING_ENSEMBLE_PERFORMANCE_TRACKER_VERSION,
            tradeCount=trade_count,
            winRate=round(winners / trade_count, 4) if trade_count else 0.0,
            totalPnl=round(total_pnl, 4),
            expectancy=round(total_pnl / trade_count, 4) if trade_count else 0.0,
            profitFactor=profit_factor,
            maxDrawdown=round(_number(result.get("maxDrawdown")) or 0.0, 4),
            sourceWindow={
                "source": "voting_ensemble_backtest_result",
                "dateRange": result.get("dateRange"),
                "timeframe": result.get("timeframe"),
                "decisionCount": result.get("decisionCount"),
            },
            reasonCodes=performance_tracking_reason_codes(),
        )

    def strategy_snapshots_from_stage_results(self, stage_results: list[dict[str, Any]]) -> tuple[VotingEnsembleStrategyPerformanceSnapshot, ...]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        family_by_strategy: dict[str, str | None] = {}
        for stage in stage_results:
            for output in _strategy_outputs(stage):
                strategy_id = str(output.get("strategy") or output.get("strategyId") or "unknown")
                buckets.setdefault(strategy_id, []).append(output)
                family_by_strategy.setdefault(strategy_id, output.get("family"))
        snapshots: list[VotingEnsembleStrategyPerformanceSnapshot] = []
        for strategy_id, outputs in sorted(buckets.items()):
            snapshots.append(
                VotingEnsembleStrategyPerformanceSnapshot(
                    strategyId=strategy_id,
                    family=family_by_strategy.get(strategy_id),
                    signalCount=len(outputs),
                    averageConfidence=round(_average(_number(output.get("confidence")) for output in outputs), 4),
                    averageReliability=round(_average(_number(output.get("reliability")) for output in outputs), 4),
                    reasonCodes=("voting_ensemble.performance_tracking.strategy_rollup",),
                )
            )
        return tuple(snapshots)

    def family_performance_window(
        self,
        *,
        raw_inputs: dict[str, Any],
        family: str,
        regime_key: str,
    ) -> VotingEnsembleFamilyPerformanceWindow:
        observations = raw_inputs.get("strategyFamilyPerformanceHistory")
        if not isinstance(observations, list):
            observations = []
        return VotingEnsembleFamilyPerformanceHistory().window_for(
            observations=observations,
            family=family,
            regime_key=regime_key,
        )


def performance_tracking_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_PERFORMANCE_TRACKER_VERSION,
        "voting_ensemble.performance_tracking.trade_summary",
        "voting_ensemble.performance_tracking.strategy_rollups",
        "voting_ensemble.performance_tracking.walk_forward_ready",
    )


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: Any) -> float:
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _strategy_outputs(stage: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = stage.get("strategyOutputs") or stage.get("directionalStrategies") or []
    return [output for output in outputs if isinstance(output, dict)]
