"""WCA modifier module namespace."""

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaModifierEvaluation
from backend.app.algorithms.wca.modifiers.adx_trend_strength import AdxTrendStrengthModifier
from backend.app.algorithms.wca.modifiers.atr_volatility_regime import AtrVolatilityRegimeModifier
from backend.app.algorithms.wca.modifiers.base import WcaModifier
from backend.app.algorithms.wca.modifiers.macd_momentum import MacdMomentumModifier
from backend.app.algorithms.wca.modifiers.market_breadth import MarketBreadthModifier
from backend.app.algorithms.wca.modifiers.market_structure import MarketStructureModifier
from backend.app.algorithms.wca.modifiers.multi_timeframe_trend_alignment import MultiTimeframeTrendAlignmentModifier
from backend.app.algorithms.wca.modifiers.relative_strength_vs_qqq_iwm import RelativeStrengthVsQqqIwmModifier
from backend.app.algorithms.wca.modifiers.session_phase import SessionPhaseModifier
from backend.app.algorithms.wca.modifiers.spread_liquidity import SpreadLiquidityModifier
from backend.app.algorithms.wca.modifiers.volume_confirmation import VolumeConfirmationModifier
from backend.app.algorithms.wca.modifiers.vwap_position import VwapPositionModifier


WCA_MODIFIERS: tuple[WcaModifier, ...] = (
    VwapPositionModifier(),
    VolumeConfirmationModifier(),
    MacdMomentumModifier(),
    MarketStructureModifier(),
    AdxTrendStrengthModifier(),
    AtrVolatilityRegimeModifier(),
    MultiTimeframeTrendAlignmentModifier(),
    RelativeStrengthVsQqqIwmModifier(),
    MarketBreadthModifier(),
    SessionPhaseModifier(),
    SpreadLiquidityModifier(),
)


def evaluate_all_modifiers(snapshot: WcaMarketSnapshot) -> tuple[WcaModifierEvaluation, ...]:
    return tuple(modifier.evaluate(snapshot) for modifier in WCA_MODIFIERS)


__all__ = (
    "WCA_MODIFIERS",
    "WcaModifier",
    "WcaModifierEvaluation",
    "evaluate_all_modifiers",
)
