"""Shadow-only gap fade strategy identity."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.gap_continuation_gap_fade import GapContinuationGapFadeStrategy


class GapFadeStrategy(GapContinuationGapFadeStrategy):
    strategy_id = "gap_fade"

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        forced = snapshot.model_copy(update={"features": {**snapshot.features, "gapTradeType": "fade"}})
        evidence = super().evidence(forced)
        return {
            **evidence,
            "gapTradeType": "fade",
            "splitFrom": "gap_continuation_gap_fade",
        }
