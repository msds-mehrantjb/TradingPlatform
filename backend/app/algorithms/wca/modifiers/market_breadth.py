from __future__ import annotations

from datetime import timezone

from backend.app.algorithms.wca.configuration import MarketBreadthSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier


class MarketBreadthModifier:
    modifier_id = "market_breadth"
    name = "Market Breadth"
    family = "breadth"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: MarketBreadthSettings | None = None):
        settings = settings or MarketBreadthSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        inputs = snapshot.market_breadth_inputs
        required = (settings.advancers_key, settings.decliners_key, settings.up_volume_key, settings.down_volume_key)
        if any(key not in inputs for key in required):
            return not_applicable_modifier(self, "wca.modifier.market_breadth.missing_inputs", "Configured market-breadth inputs are unavailable.", settings=settings)
        input_timestamp = snapshot.external_input_timestamps.get("market_breadth")
        if input_timestamp is None:
            return not_applicable_modifier(self, "wca.modifier.market_breadth.missing_timestamp", "Market-breadth input timestamp is unavailable.", settings=settings)
        age = (snapshot.decision_timestamp.astimezone(timezone.utc) - input_timestamp.astimezone(timezone.utc)).total_seconds()
        if age > settings.stale_after_seconds:
            return not_applicable_modifier(self, "wca.modifier.market_breadth.stale_inputs", "Market-breadth inputs are stale.", settings=settings)
        advancers = inputs[settings.advancers_key]
        decliners = inputs[settings.decliners_key]
        up_volume = inputs[settings.up_volume_key]
        down_volume = inputs[settings.down_volume_key]
        issue_total = advancers + decliners
        volume_total = up_volume + down_volume
        if issue_total <= 0 or volume_total <= 0:
            return not_applicable_modifier(self, "wca.modifier.market_breadth.invalid_inputs", "Market-breadth totals must be positive.", settings=settings)
        advance_ratio = advancers / issue_total
        up_volume_ratio = up_volume / volume_total
        breadth_score = (advance_ratio + up_volume_ratio) / 2.0
        contributions = {"advance_ratio": round(advance_ratio, 6), "up_volume_ratio": round(up_volume_ratio, 6), "breadth_score": round(breadth_score, 6)}
        if breadth_score >= settings.supportive_breadth_threshold:
            return active_modifier(self, 1.04, "wca.modifier.market_breadth.supportive", "Configured market breadth is supportive.", settings=settings, market_status_contributions=contributions)
        if breadth_score <= settings.weak_breadth_threshold:
            return active_modifier(self, 0.94, "wca.modifier.market_breadth.weak", "Configured market breadth is weak.", settings=settings, risk_multiplier=0.95, position_size_multiplier=0.95, entry_requirement_multiplier=1.05, market_status_contributions=contributions)
        return active_modifier(self, 1.0, "wca.modifier.market_breadth.neutral", "Configured market breadth is neutral.", settings=settings, market_status_contributions=contributions)
