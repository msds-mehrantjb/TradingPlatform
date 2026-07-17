from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyReliabilityEstimate:
    score: float
    version: str
    sourceWindow: dict[str, Any]
    reasonCodes: tuple[str, ...]


class StrategyPerformanceTracker:
    """Isolated lookup boundary for walk-forward strategy reliability estimates."""

    fallback_version = "strategy_performance_tracker_v1"

    def reliability_for(
        self,
        *,
        raw_inputs: dict[str, Any],
        strategy_id: str,
        regime_key: str,
    ) -> StrategyReliabilityEstimate:
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
                    "source": "performance_tracker_unavailable",
                    "strategyId": strategy_id,
                    "regimeKey": regime_key,
                },
                reasonCodes=("historical_reliability.unavailable_neutral_fallback",),
            )
        return StrategyReliabilityEstimate(
            score=max(0.0, min(1.0, score)),
            version=str(record.get("version") or performance.get("version") or self.fallback_version),
            sourceWindow={
                "source": "walk_forward_performance_tracker",
                "strategyId": strategy_id,
                "regimeKey": regime_key,
                "window": record.get("window") or record.get("sourceWindow") or strategy_record.get("window"),
                "sampleSize": record.get("sampleSize"),
            },
            reasonCodes=("historical_reliability.walk_forward_lookup",),
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
