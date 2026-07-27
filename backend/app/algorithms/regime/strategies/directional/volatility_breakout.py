from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.regime.strategies.directional.evidence import clamp01, compression, range_expansion, relative_vol, settings_payload


@dataclass(frozen=True)
class VolatilityBreakoutSettings:
    maximum_prior_compression: float = 0.75
    minimum_current_expansion: float = 1.45
    minimum_relative_volume: float = 1.05


DEFAULT_SETTINGS = VolatilityBreakoutSettings()


def evaluate(snapshot, classification):
    settings = DEFAULT_SETTINGS
    comp = compression(snapshot)
    expansion = range_expansion(snapshot)
    rv = relative_vol(snapshot)
    body = snapshot.latest.close - snapshot.latest.open
    evidence = {
        "compressionRatio": comp,
        "rangeExpansion": expansion,
        "relativeVolume": rv,
        "bodyDirection": "up" if body > 0 else "down" if body < 0 else "flat",
        "settings": settings_payload(settings),
    }
    missing = [name for name, value in {"compressionRatio": comp, "rangeExpansion": expansion}.items() if value is None]
    if missing:
        return "Hold", 0.0, "regime.strategy.volatility_breakout.missing_inputs", {**evidence, "missingInputReasons": tuple(missing)}
    if comp > settings.maximum_prior_compression:
        return "Hold", 0.40, "regime.strategy.volatility_breakout.contraction_required", evidence
    if expansion < settings.minimum_current_expansion or rv < settings.minimum_relative_volume:
        return "Hold", 0.43, "regime.strategy.volatility_breakout.expansion_required", evidence
    if body > 0:
        return "Buy", clamp01(0.56 + min(expansion / 10, 0.18) + min(rv / 12, 0.10)), "regime.strategy.volatility_breakout.upside_expansion", evidence
    if body < 0:
        return "Sell", clamp01(0.56 + min(expansion / 10, 0.18) + min(rv / 12, 0.10)), "regime.strategy.volatility_breakout.downside_expansion", evidence
    return "Hold", 0.35, "regime.strategy.volatility_breakout.direction_unconfirmed", evidence
