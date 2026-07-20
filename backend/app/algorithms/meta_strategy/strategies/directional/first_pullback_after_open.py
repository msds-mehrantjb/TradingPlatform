from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy, pct_distance


class FirstPullbackAfterOpenStrategy(DirectionalSnapshotStrategy):
    strategy_id = "first_pullback_after_open"
    family = "TREND"
    minimum_warmup = 30
    required_inputs = ("candles", "session_phase", "vwap", "relative_volume")
    buy_threshold = 0.55
    sell_threshold = 0.55

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        relvol = float(snapshot.relative_volume.get("1m") or 0.0)
        pullback_depth = float(snapshot.features.get("pullbackDepthAtr") or 0.0)
        continuation_bias = pct_distance(snapshot.last_price, snapshot.vwap)
        in_session = snapshot.session_phase in {"opening", "morning"}
        buy_score = (0.35 if continuation_bias >= 0.001 else 0.0) + (0.25 if 0.25 <= pullback_depth <= 1.25 else 0.0) + min(0.4, relvol / 3.0)
        sell_score = (0.35 if continuation_bias <= -0.001 else 0.0) + (0.25 if 0.25 <= pullback_depth <= 1.25 else 0.0) + min(0.4, relvol / 3.0)
        return {
            "relativeVolume": relvol,
            "pullbackDepthAtr": pullback_depth,
            "vwapBias": continuation_bias,
            "sessionPhase": snapshot.session_phase,
            "buyScore": buy_score if in_session else 0.0,
            "sellScore": sell_score if in_session else 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "pullbackMinAtr": 0.25, "pullbackMaxAtr": 1.25},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return super().regime_allows(snapshot, evidence) and snapshot.session_phase in {"opening", "morning"}
