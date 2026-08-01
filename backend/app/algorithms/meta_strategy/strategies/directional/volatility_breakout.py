from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, candle_high, candle_low, latest_close, structural_invalidation


class VolatilityBreakoutStrategy(DirectionalSnapshotStrategy):
    strategy_id = "volatility_breakout"
    family = "BREAKOUT"
    required_inputs = ("candles", "atr", "bollinger_bands", "bollingerWidthPercentile", "relative_volume", "spread", "liquidity")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        bands = snapshot.bollinger_bands.get("1m") or {}
        upper = float(bands.get("upper") or 0.0)
        lower = float(bands.get("lower") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 0.0)
        width_percentile = float(snapshot.features.get("bollingerWidthPercentile") or 0.0)
        buy_extension = (latest_close(snapshot) - upper) / atr if atr and upper else 0.0
        sell_extension = (lower - latest_close(snapshot)) / atr if atr and lower else 0.0
        prior_compression = width_percentile <= 0.35 or bool(snapshot.features.get("priorCompression"))
        current_width_expansion = width_percentile >= 0.80
        atr_expansion = bool(snapshot.features.get("atrExpansion", atr > 0))
        relvol_ok = relvol >= 1.50
        spread_ok = spread <= 10
        no_excess_extension_buy = buy_extension <= 1.75 and (atr <= 0 or (candle_high(snapshot) - upper) / atr <= 2.25)
        no_excess_extension_sell = sell_extension <= 1.75 and (atr <= 0 or (lower - candle_low(snapshot)) / atr <= 2.25)
        quality = 0.25 + (0.2 if prior_compression else 0.0) + (0.2 if current_width_expansion else 0.0) + (0.15 if relvol_ok else 0.0) + (0.1 if spread_ok else 0.0)
        return {
            "priorCompression": prior_compression,
            "volatilityExpansion": current_width_expansion,
            "atrExpansion": atr_expansion,
            "buyExtensionAtr": buy_extension,
            "sellExtensionAtr": sell_extension,
            "bollingerWidthPercentile": width_percentile,
            "relativeVolume": relvol,
            "spreadBps": spread,
            "closeBeyondBreakoutReference": {"buy": buy_extension >= 0.20, "sell": sell_extension >= 0.20},
            "excessiveExtension": {"buy": not no_excess_extension_buy, "sell": not no_excess_extension_sell},
            "unconfirmedLowVolumeBreak": not relvol_ok,
            "entryReference": latest_close(snapshot),
            "invalidationReference": structural_invalidation(snapshot, "BUY" if buy_extension >= sell_extension else "SELL"),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if buy_extension >= sell_extension else "SELL"),
            "buyScore": quality if prior_compression and current_width_expansion and atr_expansion and relvol_ok and spread_ok and buy_extension >= 0.20 and no_excess_extension_buy else 0.0,
            "sellScore": quality if prior_compression and current_width_expansion and atr_expansion and relvol_ok and spread_ok and sell_extension >= 0.20 and no_excess_extension_sell else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumExtensionAtr": 0.20, "compressionMaxWidthPercentile": 0.35, "minimumWidthPercentile": 0.80},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and float(evidence["bollingerWidthPercentile"]) >= 0.80
