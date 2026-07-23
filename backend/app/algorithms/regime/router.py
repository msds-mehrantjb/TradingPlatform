"""Backend-owned Regime strategy routing."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, evaluate_strategy


NO_ENTRY_REGIMES = {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}
RANGE_REGIMES = {"range_bound", "sideways_range", "choppy_mixed", "low_volatility_quiet"}
TREND_REGIMES = {"strong_uptrend", "weak_uptrend", "strong_downtrend", "weak_downtrend", "high_volatility_trend"}
BREAKOUT_REGIMES = {"opening_breakout", "intraday_expansion"}


def route_regime_strategies(snapshot: RegimeMarketSnapshot, classification: RegimeClassification, profile: dict | None = None) -> dict[str, object]:
    outputs: list[RegimeStrategyEvaluation] = []
    skipped: list[dict[str, str]] = []
    profile = profile or {}
    for definition in REGIME_STRATEGY_DEFINITIONS:
        compatible = _compatible(definition.family, definition.role, classification.raw_regime, profile)
        if not compatible and definition.role == "directional":
            skipped.append({"strategyId": definition.strategy_id, "reason": _profile_skip_reason(definition.family, classification.raw_regime, profile)})
            continue
        outputs.append(evaluate_strategy(definition.strategy_id, snapshot, classification))
    return {
        "outputs": tuple(outputs),
        "skippedStrategies": tuple(skipped),
        "selectedStrategyIds": tuple(output.strategy_id for output in outputs if output.role == "directional" and output.eligible),
        "representedFamilies": tuple(sorted({output.family for output in outputs if output.role == "directional" and output.eligible})),
        "profileRouting": {
            "noNewEntries": bool(profile.get("noNewEntries", False)),
            "preferredStrategyFamilies": tuple(profile.get("preferredStrategyFamilies", ())),
            "allowedStrategyFamilies": tuple(profile.get("allowedStrategyFamilies", ())),
            "disabledStrategyFamilies": tuple(profile.get("disabledStrategyFamilies", ())),
        },
    }


def _compatible(family: str, role: str, regime: str, profile: dict | None = None) -> bool:
    if role != "directional":
        return True
    profile = profile or {}
    if profile.get("noNewEntries"):
        return False
    disabled = set(profile.get("disabledStrategyFamilies", ()))
    if family in disabled:
        return False
    allowed = set(profile.get("allowedStrategyFamilies", ()))
    if allowed:
        return family in allowed
    if regime in NO_ENTRY_REGIMES:
        return False
    if regime in RANGE_REGIMES:
        return family in {"mean_reversion", "vwap", "reversal", "structure"}
    if regime in BREAKOUT_REGIMES:
        return family in {"breakout", "momentum", "trend", "vwap", "structure", "event"}
    if regime in TREND_REGIMES:
        return family in {"trend", "momentum", "vwap", "breakout", "structure", "event"}
    return True


def _profile_skip_reason(family: str, regime: str, profile: dict) -> str:
    if profile.get("noNewEntries"):
        return "regime.router.profile_no_new_entries"
    if family in set(profile.get("disabledStrategyFamilies", ())):
        return "regime.router.profile_family_disabled"
    if profile.get("allowedStrategyFamilies"):
        return "regime.router.profile_family_not_allowed"
    return "regime.router.incompatible_with_confirmed_regime"
