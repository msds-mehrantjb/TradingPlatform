from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.session import MetaStrategySession, canonical_session
from backend.app.algorithms.meta_strategy.strategies.directional.common import (
    DirectionalSnapshotStrategy,
    candle_high,
    candle_low,
    candles,
    latest_close,
    pct_distance,
    recent_high,
    recent_low,
    structural_invalidation,
)


class FirstPullbackAfterOpenStrategy(DirectionalSnapshotStrategy):
    strategy_id = "first_pullback_after_open"
    family = "TREND"
    required_inputs = ("candles", "session_phase", "vwap", "relative_volume", "pullbackDepthAtr")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        rows = candles(snapshot, "1m")
        opening_sample = rows[: min(15, len(rows))]
        recent = rows[-6:]
        opening_open = float(opening_sample[0]["open"]) if opening_sample else snapshot.last_price
        opening_high = max((float(row["high"]) for row in opening_sample), default=snapshot.last_price)
        opening_low = min((float(row["low"]) for row in opening_sample), default=snapshot.last_price)
        impulse_up = opening_high - opening_open
        impulse_down = opening_open - opening_low
        atr = float(snapshot.atr.get("1m") or 0.0)
        impulse_direction = "BUY" if impulse_up > impulse_down and impulse_up >= atr * 0.4 else "SELL" if impulse_down > impulse_up and impulse_down >= atr * 0.4 else "NONE"
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        pullback_volume_reduced = relvol <= 1.35
        pullback_depth = float(snapshot.features.get("pullbackDepthAtr") or 0.0)
        continuation_bias = pct_distance(latest_close(snapshot), snapshot.vwap)
        confirmation_candle_buy = len(rows) >= 2 and float(rows[-1]["close"]) > float(rows[-1]["open"]) and float(rows[-1]["close"]) > float(rows[-2]["high"])
        confirmation_candle_sell = len(rows) >= 2 and float(rows[-1]["close"]) < float(rows[-1]["open"]) and float(rows[-1]["close"]) < float(rows[-2]["low"])
        vwap_preserved_buy = snapshot.vwap is not None and candle_low(snapshot) >= float(snapshot.vwap) - max(0.01, atr * 0.15)
        vwap_preserved_sell = snapshot.vwap is not None and candle_high(snapshot) <= float(snapshot.vwap) + max(0.01, atr * 0.15)
        deep_pullback = pullback_depth > 1.25
        expired_window = len(opening_sample) >= 15 and snapshot.session_phase not in {"OPENING", "MORNING", "opening", "morning"}
        try:
            session = canonical_session(snapshot.session_phase)
        except ValueError:
            session = MetaStrategySession.CLOSED
        in_session = session in {MetaStrategySession.OPENING, MetaStrategySession.MORNING}
        depth_ok = 0.25 <= pullback_depth <= 1.25
        score = 0.35 + (0.2 if depth_ok else 0.0) + (0.15 if pullback_volume_reduced else 0.0) + (0.15 if in_session else 0.0)
        buy_valid = impulse_direction == "BUY" and continuation_bias >= 0 and depth_ok and pullback_volume_reduced and vwap_preserved_buy and confirmation_candle_buy and not deep_pullback and not expired_window
        sell_valid = impulse_direction == "SELL" and continuation_bias <= 0 and depth_ok and pullback_volume_reduced and vwap_preserved_sell and confirmation_candle_sell and not deep_pullback and not expired_window
        return {
            "stateScope": {"symbol": snapshot.symbol, "sessionDate": snapshot.timestamp.date().isoformat(), "reset": "start_of_trading_day"},
            "openingImpulse": {"direction": impulse_direction, "upAtr": impulse_up / atr if atr else 0.0, "downAtr": impulse_down / atr if atr else 0.0},
            "relativeVolume": relvol,
            "pullbackVolumeReduced": pullback_volume_reduced,
            "pullbackDepthAtr": pullback_depth,
            "vwapBias": continuation_bias,
            "vwapPreserved": {"buy": vwap_preserved_buy, "sell": vwap_preserved_sell},
            "confirmationCandle": {"buy": confirmation_candle_buy, "sell": confirmation_candle_sell},
            "impulseOriginProtection": {"buy": opening_open, "sell": opening_open},
            "invalidationState": {"deepPullback": deep_pullback, "failedContinuation": False, "secondPullback": False, "expiredTimeWindow": expired_window},
            "sessionPhase": snapshot.session_phase,
            "entryReference": latest_close(snapshot),
            "invalidationReference": min(recent_low(snapshot, 10), opening_open) if buy_valid else max(recent_high(snapshot, 10), opening_open),
            "suggestedStopReference": structural_invalidation(snapshot, "BUY" if impulse_direction != "SELL" else "SELL"),
            "buyScore": score if buy_valid else 0.0,
            "sellScore": score if sell_valid else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "pullbackMinAtr": 0.25, "pullbackMaxAtr": 1.25},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        try:
            session = canonical_session(snapshot.session_phase)
        except ValueError:
            return False
        return super().regime_allows(snapshot, evidence) and session in {MetaStrategySession.OPENING, MetaStrategySession.MORNING}
