"""Shared Regime strategy primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation, StrategyRole


StrategyEvaluator = Callable[[RegimeMarketSnapshot, RegimeClassification], tuple[str, float, str, dict]]


@dataclass(frozen=True)
class RegimeStrategyDefinition:
    strategy_id: str
    name: str
    family: str
    role: StrategyRole
    base_weight: float
    minimum_bars: int
    evaluator: StrategyEvaluator


def evaluate_definition(definition: RegimeStrategyDefinition, snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> RegimeStrategyEvaluation:
    if len(snapshot.candles) < definition.minimum_bars:
        return RegimeStrategyEvaluation(
            strategy_id=definition.strategy_id,
            name=definition.name,
            family=definition.family,
            role=definition.role,
            signal="Hold",
            confidence=0.0,
            weight=definition.base_weight,
            eligible=False,
            reason="regime.strategy.minimum_bars_not_met",
            evidence={"minimumBars": definition.minimum_bars, "actualBars": len(snapshot.candles)},
        )
    signal, confidence, reason, evidence = definition.evaluator(snapshot, classification)
    if definition.role != "directional":
        signal = "Hold"
    return RegimeStrategyEvaluation(
        strategy_id=definition.strategy_id,
        name=definition.name,
        family=definition.family,
        role=definition.role,
        signal=signal if signal in {"Buy", "Sell", "Hold"} else "Hold",
        confidence=max(0.0, min(1.0, float(confidence))),
        weight=definition.base_weight,
        eligible=True,
        reason=reason,
        evidence=evidence,
    )


def directional_by_scores(snapshot: RegimeMarketSnapshot, classification: RegimeClassification, *, trend: bool = False, reversal: bool = False) -> tuple[str, float, str, dict]:
    bull = int(classification.features.get("bullScore") or 0)
    bear = int(classification.features.get("bearScore") or 0)
    rsi = classification.features.get("rsi")
    edge = bull - bear
    if reversal and rsi is not None:
        if rsi <= 32:
            return "Buy", 0.66, "regime.strategy.oversold_reversal", {"rsi": rsi}
        if rsi >= 68:
            return "Sell", 0.66, "regime.strategy.overbought_reversal", {"rsi": rsi}
    if edge >= (2 if trend else 3):
        return "Buy", min(0.90, 0.55 + abs(edge) * 0.08), "regime.strategy.bullish_alignment", {"bullScore": bull, "bearScore": bear}
    if edge <= (-2 if trend else -3):
        return "Sell", min(0.90, 0.55 + abs(edge) * 0.08), "regime.strategy.bearish_alignment", {"bullScore": bull, "bearScore": bear}
    return "Hold", 0.45, "regime.strategy.no_edge", {"bullScore": bull, "bearScore": bear}

