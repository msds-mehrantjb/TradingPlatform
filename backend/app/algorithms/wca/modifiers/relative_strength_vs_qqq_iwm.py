from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import invalid_snapshot_result, not_applicable_modifier


class RelativeStrengthVsQqqIwmModifier:
    modifier_id = "relative_strength_vs_qqq_iwm"
    name = "Relative Strength vs QQQ/IWM"
    family = "relative_strength"

    def evaluate(self, snapshot: WcaMarketSnapshot):
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        return not_applicable_modifier(
            self,
            "wca.modifier.relative_strength_vs_qqq_iwm.external_context_unavailable",
            "Relative-strength inputs are not present in the immutable WCA market snapshot.",
        )
