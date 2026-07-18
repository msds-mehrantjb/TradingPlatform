"""Backend-owned Regime strategy routing."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, evaluate_strategy


NO_ENTRY_REGIMES = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}
RANGE_REGIMES = {"range_bound", "sideways_range", "choppy_mixed", "low_volatility_quiet"}
TREND_REGIMES = {"strong_uptrend", "weak_uptrend", "strong_downtrend", "weak_downtrend", "high_volatility_trend"}
BREAKOUT_REGIMES = {"opening_breakout", "intraday_expansion"}


def route_regime_strategies(snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> dict[str, object]:
    outputs: list[RegimeStrategyEvaluation] = []
    skipped: list[dict[str, str]] = []
    for definition in REGIME_STRATEGY_DEFINITIONS:
        compatible = _compatible(definition.family, definition.role, classification.raw_regime)
        if not compatible and definition.role == "directional":
            skipped.append({"strategyId": definition.strategy_id, "reason": "regime.router.incompatible_with_confirmed_regime"})
            continue
        outputs.append(evaluate_strategy(definition.strategy_id, snapshot, classification))
    return {
        "outputs": tuple(outputs),
        "skippedStrategies": tuple(skipped),
        "selectedStrategyIds": tuple(output.strategy_id for output in outputs if output.role == "directional" and output.eligible),
        "representedFamilies": tuple(sorted({output.family for output in outputs if output.role == "directional" and output.eligible})),
    }


def _compatible(family: str, role: str, regime: str) -> bool:
    if role != "directional":
        return True
    if regime in NO_ENTRY_REGIMES:
        return False
    if regime in RANGE_REGIMES:
        return family in {"mean_reversion", "vwap", "reversal", "structure"}
    if regime in BREAKOUT_REGIMES:
        return family in {"breakout", "momentum", "trend", "vwap", "structure", "event"}
    if regime in TREND_REGIMES:
        return family in {"trend", "momentum", "vwap", "breakout", "structure", "event"}
    return True

