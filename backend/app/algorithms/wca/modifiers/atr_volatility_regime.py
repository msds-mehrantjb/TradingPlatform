from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import atr, completed_candles


class AtrVolatilityRegimeModifier:
    modifier_id = "atr_volatility_regime"
    name = "ATR Volatility Regime"
    family = "volatility"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 15:
            return not_applicable_modifier(self, "wca.modifier.atr_volatility_regime.insufficient_history", "ATR regime needs 15 completed candles.")
        atr_pct = atr(candles, 14) / max(candles[-1].close, 0.01)
        if atr_pct >= 0.012:
            return active_modifier(self, 0.82, "wca.modifier.atr_volatility_regime.extreme", "Extreme volatility reduces entry permission, weight, or size.")
        if atr_pct >= 0.006:
            return active_modifier(self, 0.92, "wca.modifier.atr_volatility_regime.high", "High volatility reduces risk or size.")
        if atr_pct <= 0.001:
            return active_modifier(self, 0.96, "wca.modifier.atr_volatility_regime.very_low", "Very low volatility reduces breakout eligibility.")
        return active_modifier(self, 1.0, "wca.modifier.atr_volatility_regime.normal", "ATR volatility regime is normal.")
