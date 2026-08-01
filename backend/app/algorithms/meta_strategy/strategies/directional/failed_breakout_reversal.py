from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, candle_high, candle_low, latest_close, recent_high, recent_low


class FailedBreakoutReversalStrategy(DirectionalSnapshotStrategy):
    strategy_id = "failed_breakout_reversal"
    family = "REVERSAL"
    required_inputs = ("candles", "atr", "spread", "liquidity", "failedBreakoutSide", "opening_range", "session_levels", "recent_swing_levels")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        side = str(snapshot.features.get("failedBreakoutSide") or "none")
        reclaim_atr = float(snapshot.features.get("reclaimDistanceAtr") or 0.0)
        spread = float(snapshot.spread.get("basisPoints") or 0.0)
        atr = float(snapshot.atr.get("1m") or 0.0)
        levels = _reference_levels(snapshot)
        upside_level = _selected_reference_level(snapshot, levels, "upside")
        downside_level = _selected_reference_level(snapshot, levels, "downside")
        excursion_up = (candle_high(snapshot) - upside_level) / atr if atr and upside_level else 0.0
        excursion_down = (downside_level - candle_low(snapshot)) / atr if atr and downside_level else 0.0
        close_back_inside_up = latest_close(snapshot) < upside_level if upside_level else False
        close_back_inside_down = latest_close(snapshot) > downside_level if downside_level else False
        rejection_size = float(snapshot.features.get("rejectionWickRatio") or 0.0)
        liquidity_score = float(snapshot.liquidity.get("score") or 0.0)
        confirmation = liquidity_score >= 0.45 or float(snapshot.relative_volume.get("1m") or 0.0) >= 0.9
        sell_valid = side == "upside" and excursion_up >= 0.10 and close_back_inside_up and reclaim_atr >= 0.15 and rejection_size >= 0.5 and spread <= 10.0 and confirmation
        buy_valid = side == "downside" and excursion_down >= 0.10 and close_back_inside_down and reclaim_atr >= 0.15 and rejection_size >= 0.5 and spread <= 10.0 and confirmation
        quality = 0.35 + (0.15 if reclaim_atr >= 0.15 else 0.0) + (0.15 if rejection_size >= 0.5 else 0.0) + (0.15 if spread <= 10.0 else 0.0) + (0.1 if confirmation else 0.0)
        return {
            "failedBreakoutSide": side,
            "referenceLevels": levels,
            "selectedReferenceLevel": upside_level if side == "upside" else downside_level if side == "downside" else None,
            "excursionAtr": {"upside": excursion_up, "downside": excursion_down},
            "closeBackInside": {"upside": close_back_inside_up, "downside": close_back_inside_down},
            "reclaimDistanceAtr": reclaim_atr,
            "rejectionSize": rejection_size,
            "volumeOrLiquidityConfirmation": confirmation,
            "spreadBps": spread,
            "entryReference": latest_close(snapshot),
            "invalidationReference": downside_level - max(0.01, atr * 0.1) if buy_valid else upside_level + max(0.01, atr * 0.1),
            "suggestedStopReference": downside_level - max(0.01, atr * 0.1) if buy_valid else upside_level + max(0.01, atr * 0.1),
            "buyScore": quality if buy_valid else 0.0,
            "sellScore": quality if sell_valid else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumReclaimAtr": 0.15, "minimumExcursionAtr": 0.10},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and evidence["failedBreakoutSide"] in {"upside", "downside"}


def _reference_levels(snapshot: MetaStrategyMarketSnapshot) -> dict[str, float]:
    values = {
        "openingRangeHigh": snapshot.features.get("openingRangeHigh"),
        "openingRangeLow": snapshot.features.get("openingRangeLow"),
        "previousDayHigh": snapshot.features.get("previousDayHigh"),
        "previousDayLow": snapshot.features.get("previousDayLow"),
        "premarketHigh": snapshot.features.get("premarketHigh"),
        "premarketLow": snapshot.features.get("premarketLow"),
        "sessionHigh": snapshot.features.get("sessionHigh") or recent_high(snapshot, 60),
        "sessionLow": snapshot.features.get("sessionLow") or recent_low(snapshot, 60),
        "recentSwingHigh": snapshot.features.get("recentSwingHigh") or recent_high(snapshot, 20),
        "recentSwingLow": snapshot.features.get("recentSwingLow") or recent_low(snapshot, 20),
    }
    return {key: float(value) for key, value in values.items() if value not in {None, "", 0.0}}


def _selected_reference_level(snapshot: MetaStrategyMarketSnapshot, levels: dict[str, float], side: str) -> float:
    if side == "upside":
        explicit = snapshot.features.get("failedBreakoutReferenceHigh") or snapshot.features.get("openingRangeHigh")
        return float(explicit) if explicit not in {None, "", 0.0} else max(levels.values(), default=0.0)
    explicit = snapshot.features.get("failedBreakoutReferenceLow") or snapshot.features.get("openingRangeLow")
    return float(explicit) if explicit not in {None, "", 0.0} else min(levels.values(), default=0.0)
