from __future__ import annotations

from backend.app.algorithms.wca.configuration import AtrVolatilityRegimeSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import atr, completed_candles


class AtrVolatilityRegimeModifier:
    modifier_id = "atr_volatility_regime"
    name = "ATR Volatility Regime"
    family = "volatility"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: AtrVolatilityRegimeSettings | None = None):
        settings = settings or AtrVolatilityRegimeSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < settings.atr_period + 1:
            return not_applicable_modifier(self, "wca.modifier.atr_volatility_regime.insufficient_history", "ATR regime needs completed ATR history.", settings=settings)
        atr_pct = atr(candles, settings.atr_period) / max(candles[-1].close, 0.01)
        if atr_pct >= settings.extreme_atr_percent:
            return active_modifier(self, 0.82, "wca.modifier.atr_volatility_regime.extreme", "Extreme volatility reduces entry permission, weight, or size.", settings=settings, risk_multiplier=0.70, position_size_multiplier=0.70, entry_requirement_multiplier=1.20, market_status_contributions={"volatility": "extreme", "atr_percent": round(atr_pct, 6)})
        if atr_pct >= settings.high_atr_percent:
            return active_modifier(self, 0.92, "wca.modifier.atr_volatility_regime.high", "High volatility reduces risk or size.", settings=settings, risk_multiplier=0.85, position_size_multiplier=0.85, entry_requirement_multiplier=1.10, market_status_contributions={"volatility": "high", "atr_percent": round(atr_pct, 6)})
        if atr_pct <= settings.very_low_atr_percent:
            return active_modifier(self, 0.96, "wca.modifier.atr_volatility_regime.very_low", "Very low volatility tightens breakout eligibility.", settings=settings, entry_requirement_multiplier=1.10, market_status_contributions={"volatility": "very_low", "atr_percent": round(atr_pct, 6)})
        return active_modifier(self, 1.0, "wca.modifier.atr_volatility_regime.normal", "ATR volatility regime is normal.", settings=settings, market_status_contributions={"volatility": "normal", "atr_percent": round(atr_pct, 6)})
