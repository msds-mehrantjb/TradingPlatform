"""WCA modifier module namespace."""

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaModifierEvaluation
from backend.app.algorithms.wca.modifiers.adx_trend_strength import AdxTrendStrengthModifier
from backend.app.algorithms.wca.modifiers.atr_volatility_regime import AtrVolatilityRegimeModifier
from backend.app.algorithms.wca.modifiers.base import WcaModifier
from backend.app.algorithms.wca.modifiers.market_breadth import MarketBreadthModifier
from backend.app.algorithms.wca.modifiers.relative_strength_vs_qqq_iwm import RelativeStrengthVsQqqIwmModifier


WCA_MODIFIERS: tuple[WcaModifier, ...] = (
    AdxTrendStrengthModifier(),
    AtrVolatilityRegimeModifier(),
    RelativeStrengthVsQqqIwmModifier(),
    MarketBreadthModifier(),
)


def evaluate_all_modifiers(snapshot: WcaMarketSnapshot) -> tuple[WcaModifierEvaluation, ...]:
    return tuple(modifier.evaluate(snapshot) for modifier in WCA_MODIFIERS)


__all__ = (
    "WCA_MODIFIERS",
    "WcaModifier",
    "WcaModifierEvaluation",
    "evaluate_all_modifiers",
)
