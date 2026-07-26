from __future__ import annotations

from backend.app.algorithms.wca.configuration import SpreadLiquiditySettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result
from backend.app.algorithms.wca.strategies.indicators import average_volume, completed_candles


class SpreadLiquidityModifier:
    modifier_id = "spread_liquidity"
    name = "Spread/Liquidity"
    family = "liquidity"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: SpreadLiquiditySettings | None = None):
        settings = settings or SpreadLiquiditySettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        candles = completed_candles(snapshot)
        avg_volume = average_volume(candles, min(20, len(candles)))
        spread_pct = 0.0
        if snapshot.quote is not None:
            midpoint = max((snapshot.quote.bid + snapshot.quote.ask) / 2, 0.01)
            spread_pct = (snapshot.quote.ask - snapshot.quote.bid) / midpoint
        contributions = {"average_volume": round(avg_volume, 4), "spread_percent": round(spread_pct, 6)}
        if avg_volume < settings.unsafe_average_volume or spread_pct >= settings.unsafe_spread_percent:
            return active_modifier(self, 0.8, "wca.modifier.spread_liquidity.unsafe", "Unsafe spread or liquidity reduces entry permission and size.", settings=settings, risk_multiplier=0.50, position_size_multiplier=0.50, entry_requirement_multiplier=1.25, market_status_contributions=contributions | {"liquidity": "unsafe"})
        if avg_volume < settings.thin_average_volume or spread_pct >= settings.thin_spread_percent:
            return active_modifier(self, 0.9, "wca.modifier.spread_liquidity.thin", "Thin spread or liquidity reduces effective weight or size.", settings=settings, risk_multiplier=0.80, position_size_multiplier=0.80, entry_requirement_multiplier=1.10, market_status_contributions=contributions | {"liquidity": "thin"})
        if avg_volume >= settings.deep_average_volume and spread_pct <= settings.deep_spread_percent:
            return active_modifier(self, 1.03, "wca.modifier.spread_liquidity.deep", "Deep liquidity supports normal participation.", settings=settings, market_status_contributions=contributions | {"liquidity": "deep"})
        return active_modifier(self, 1.0, "wca.modifier.spread_liquidity.normal", "Spread and liquidity are normal.", settings=settings, market_status_contributions=contributions | {"liquidity": "normal"})
