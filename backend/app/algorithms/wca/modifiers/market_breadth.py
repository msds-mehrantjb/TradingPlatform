from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import invalid_snapshot_result, not_applicable_modifier


class MarketBreadthModifier:
    modifier_id = "market_breadth"
    name = "Market Breadth"
    family = "breadth"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        return not_applicable_modifier(
            self,
            "wca.modifier.market_breadth.external_context_unavailable",
            "Market-breadth inputs are not present in the immutable WCA market snapshot.",
        )
