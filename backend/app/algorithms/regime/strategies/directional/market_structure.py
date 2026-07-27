from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, settings_payload, swing_structure


@dataclass(frozen=True)
class MarketStructureSettings:
    require_break_of_structure: bool = True


DEFAULT_SETTINGS = MarketStructureSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    structure = swing_structure(snapshot)
    evidence = {**structure, "classifierStructure": classification.features.get("structureLabel") or classification.axes.structure, "settings": settings_payload(settings)}
    if structure["missingInputs"]:
        return "Hold", 0.0, "regime.strategy.market_structure.missing_inputs", {**evidence, "missingInputReasons": structure["missingInputs"]}
    if structure["state"] == "up" and (structure["breakOfStructure"] == "up" or not settings.require_break_of_structure):
        return "Buy", clamp01(0.58 + (0.08 if structure["breakOfStructure"] == "up" else 0.0)), "regime.strategy.market_structure.hh_hl_break", evidence
    if structure["state"] == "down" and (structure["breakOfStructure"] == "down" or not settings.require_break_of_structure):
        return "Sell", clamp01(0.58 + (0.08 if structure["breakOfStructure"] == "down" else 0.0)), "regime.strategy.market_structure.lh_ll_break", evidence
    if structure["state"] in {"up", "down"}:
        return "Hold", 0.44, "regime.strategy.market_structure.structure_preserved_no_break", evidence
    return "Hold", 0.40, "regime.strategy.market_structure.mixed_or_range", evidence
