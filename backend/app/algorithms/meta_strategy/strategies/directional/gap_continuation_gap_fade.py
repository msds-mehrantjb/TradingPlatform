from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.session import MetaStrategySession, canonical_session
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class GapContinuationGapFadeStrategy(DirectionalSnapshotStrategy):
    strategy_id = "gap_continuation_gap_fade"
    family = "GAP_SESSION"
    required_inputs = ("candles", "gap_state", "session_phase", "qqq_iwm_context", "economic_event_state")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        state = str(snapshot.gap_state.get("state") or "unknown")
        gap_percent = float(snapshot.gap_state.get("gapPercent") or 0.0)
        continuation_bias = str(snapshot.features.get("gapTradeType") or "continuation")
        context_bias = float(snapshot.qqq_iwm_context.get("spyVsQqq") or 1.0) - 1.0
        if continuation_bias == "fade":
            buy_score = 0.42 if state == "gap_down" and gap_percent <= -0.75 else 0.0
            sell_score = 0.42 if state == "gap_up" and gap_percent >= 0.75 else 0.0
        else:
            buy_score = 0.42 if state == "gap_up" and gap_percent >= 0.75 else 0.0
            sell_score = 0.42 if state == "gap_down" and gap_percent <= -0.75 else 0.0
        context_score = min(0.2, abs(context_bias) * 10)
        try:
            session = canonical_session(snapshot.session_phase)
        except ValueError:
            session = MetaStrategySession.CLOSED
        session_score = 0.15 if session in {MetaStrategySession.OPENING, MetaStrategySession.MORNING} else 0.0
        return {
            "gapState": state,
            "gapPercent": gap_percent,
            "gapTradeType": continuation_bias,
            "contextBias": context_bias,
            "buyScore": buy_score + context_score + session_score,
            "sellScore": sell_score + context_score + session_score,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold, "minimumGapPercent": 0.75},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        return (
            super().regime_allows(snapshot, evidence)
            and evidence["gapState"] in {"gap_up", "gap_down"}
            and self._canonical_session(snapshot) in {MetaStrategySession.OPENING, MetaStrategySession.MORNING}
        )

    def _canonical_session(self, snapshot: MetaStrategyMarketSnapshot) -> MetaStrategySession:
        try:
            return canonical_session(snapshot.session_phase)
        except ValueError:
            return MetaStrategySession.CLOSED
