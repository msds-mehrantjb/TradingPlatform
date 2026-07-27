"""Shadow-only gap continuation strategy identity."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.directional.gap_continuation_gap_fade import GapContinuationGapFadeStrategy


class GapContinuationStrategy(GapContinuationGapFadeStrategy):
    strategy_id = "gap_continuation"

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        forced = snapshot.model_copy(update={"features": {**snapshot.features, "gapTradeType": "continuation"}})
        evidence = super().evidence(forced)
        return {
            **evidence,
            "gapTradeType": "continuation",
            "splitFrom": "gap_continuation_gap_fade",
        }
