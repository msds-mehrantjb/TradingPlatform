"""Shadow-only economic event reaction strategy."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.common import DirectionalSnapshotStrategy


class EconomicEventReactionStrategy(DirectionalSnapshotStrategy):
    strategy_id = "economic_event_reaction"
    family = "EVENT_DRIVEN"
    required_inputs = ("candles", "economic_event_state", "session_phase", "spread", "relative_volume")

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        state = str(snapshot.economic_event_state.get("state") or "none").lower()
        direction = str(snapshot.economic_event_state.get("directionalBias") or "none").lower()
        relative_volume = float(snapshot.relative_volume.get("1m") or 0.0)
        event_active = bool(snapshot.economic_event_state.get("active") or state in {"active", "released"})
        score = min(0.75, 0.35 + relative_volume * 0.12) if event_active else 0.0
        buy_score = score if direction in {"bullish", "up", "risk_on"} else 0.0
        sell_score = score if direction in {"bearish", "down", "risk_off"} else 0.0
        return {
            "eventState": state,
            "eventActive": event_active,
            "directionalBias": direction,
            "relativeVolume": relative_volume,
            "buyScore": buy_score,
            "sellScore": sell_score,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold},
        }
