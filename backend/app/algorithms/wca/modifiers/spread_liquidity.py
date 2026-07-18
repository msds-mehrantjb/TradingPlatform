from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import average_volume, completed_candles


class SpreadLiquidityModifier:
    modifier_id = "spread_liquidity"
    name = "Spread/Liquidity"
    family = "liquidity"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        avg_volume = average_volume(candles, min(20, len(candles)))
        spread_pct = 0.0
        if snapshot.quote is not None:
            midpoint = max((snapshot.quote.bid + snapshot.quote.ask) / 2, 0.01)
            spread_pct = (snapshot.quote.ask - snapshot.quote.bid) / midpoint
        if avg_volume < 10000 or spread_pct >= 0.002:
            return active_modifier(self, 0.8, "wca.modifier.spread_liquidity.unsafe", "Unsafe spread or liquidity reduces entry permission and size.")
        if avg_volume < 50000 or spread_pct >= 0.0008:
            return active_modifier(self, 0.9, "wca.modifier.spread_liquidity.thin", "Thin spread or liquidity reduces effective weight or size.")
        if avg_volume >= 250000 and spread_pct <= 0.0002:
            return active_modifier(self, 1.03, "wca.modifier.spread_liquidity.deep", "Deep liquidity supports normal participation.")
        return active_modifier(self, 1.0, "wca.modifier.spread_liquidity.normal", "Spread and liquidity are normal.")
