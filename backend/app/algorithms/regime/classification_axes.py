"""Canonical Regime classification axes and composite mapping."""

from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES, LEGACY_REGIME_ALIASES, REGIME_OPPORTUNITY_TAGS

REGIME_DIRECTION_AXES = ("strong_up", "weak_up", "neutral", "weak_down", "strong_down")
REGIME_VOLATILITY_AXES = ("compressed", "normal", "expanded", "extreme")
REGIME_STRUCTURE_AXES = ("trend", "range", "breakout", "failed_breakout", "reversal", "mixed")
REGIME_LIQUIDITY_AXES = ("good", "acceptable", "poor", "unknown")
REGIME_SESSION_AXES = ("opening", "midday", "afternoon", "closing", "outside_regular")
REGIME_EVENT_RISK_AXES = ("none", "elevated", "blackout")

__all__ = [
    "CANONICAL_MARKET_REGIMES",
    "LEGACY_REGIME_ALIASES",
    "REGIME_OPPORTUNITY_TAGS",
    "REGIME_DIRECTION_AXES",
    "REGIME_VOLATILITY_AXES",
    "REGIME_STRUCTURE_AXES",
    "REGIME_LIQUIDITY_AXES",
    "REGIME_SESSION_AXES",
    "REGIME_EVENT_RISK_AXES",
]

