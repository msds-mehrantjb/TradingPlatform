from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles, sma


class MacdMomentumModifier:
    modifier_id = "macd_momentum"
    name = "MACD Momentum"
    family = "momentum"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        if len(candles) < 26:
            return not_applicable_modifier(self, "wca.modifier.macd_momentum.insufficient_history", "MACD momentum needs 26 completed candles.")
        close = candles[-1].close
        spread = (sma(candles, 12) - sma(candles, 26)) / max(close, 0.01)
        if spread > 0.001:
            return active_modifier(self, 1.04, "wca.modifier.macd_momentum.positive", "Short momentum is above long momentum.")
        if spread < -0.001:
            return active_modifier(self, 0.96, "wca.modifier.macd_momentum.negative", "Short momentum is below long momentum.")
        return active_modifier(self, 1.0, "wca.modifier.macd_momentum.neutral", "Momentum spread is neutral.")
