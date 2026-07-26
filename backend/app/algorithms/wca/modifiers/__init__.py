"""WCA modifier construction from the authoritative catalog."""

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaModifierEvaluation
from backend.app.algorithms.wca.configuration import WcaModifierSettings
from backend.app.algorithms.wca.modifiers.base import WcaModifier
from backend.app.algorithms.wca.strategy_registry import build_wca_modifiers


WCA_MODIFIERS: tuple[WcaModifier, ...] = build_wca_modifiers()


def evaluate_all_modifiers(snapshot: WcaMarketSnapshot, settings: WcaModifierSettings | None = None) -> tuple[WcaModifierEvaluation, ...]:
    return tuple(modifier.evaluate(snapshot, getattr(settings, modifier.modifier_id, None)) for modifier in WCA_MODIFIERS)


__all__ = (
    "WCA_MODIFIERS",
    "WcaModifier",
    "WcaModifierEvaluation",
    "evaluate_all_modifiers",
)
