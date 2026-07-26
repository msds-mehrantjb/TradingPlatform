from __future__ import annotations

from backend.app.algorithms.wca.configuration import MacdMomentumSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class MacdMomentumModifier:
    modifier_id = "macd_momentum"
    name = "MACD Momentum"
    family = "momentum"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: MacdMomentumSettings | None = None):
        settings = settings or MacdMomentumSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < settings.slow_period:
            return not_applicable_modifier(self, "wca.modifier.macd_momentum.insufficient_history", "MACD momentum needs completed slow-period history.", settings=settings)
        close = candles[-1].close
        spread = (sma(candles, settings.fast_period) - sma(candles, settings.slow_period)) / max(close, 0.01)
        if abs(spread) > settings.neutral_band_percent:
            return active_modifier(self, 1.04, "wca.modifier.macd_momentum.expanded", "Short and long momentum windows are separated.", settings=settings, market_status_contributions={"macd_spread_percent": round(spread, 6)})
        return active_modifier(self, 1.0, "wca.modifier.macd_momentum.neutral", "Momentum spread is neutral.", settings=settings, market_status_contributions={"macd_spread_percent": round(spread, 6)})
