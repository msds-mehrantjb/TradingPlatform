from __future__ import annotations

from datetime import timezone

from backend.app.algorithms.wca.configuration import RelativeStrengthVsQqqIwmSettings
from backend.app.algorithms.wca.contracts import WcaMarketSnapshot
from backend.app.algorithms.wca.modifiers.base import active_modifier, invalid_snapshot_result, not_applicable_modifier
from backend.app.algorithms.wca.strategies.indicators import completed_candles


class RelativeStrengthVsQqqIwmModifier:
    modifier_id = "relative_strength_vs_qqq_iwm"
    name = "Relative Strength vs QQQ/IWM"
    family = "relative_strength"

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: RelativeStrengthVsQqqIwmSettings | None = None):
        settings = settings or RelativeStrengthVsQqqIwmSettings()
        invalid = invalid_snapshot_result(snapshot, self)
        if invalid:
            return invalid
        base = completed_candles(snapshot)
        qqq = tuple(snapshot.external_market_data.get(settings.qqq_symbol, ()))
        iwm = tuple(snapshot.external_market_data.get(settings.iwm_symbol, ()))
        if len(base) < settings.lookback_bars + 1 or len(qqq) < settings.lookback_bars + 1 or len(iwm) < settings.lookback_bars + 1:
            return not_applicable_modifier(self, "wca.modifier.relative_strength_vs_qqq_iwm.missing_external_history", "Timestamp-aligned QQQ/IWM history is unavailable.", settings=settings)
        latest_timestamp = base[-1].timestamp
        qqq_aligned = _aligned_tail(qqq, base[-settings.lookback_bars - 1 :])
        iwm_aligned = _aligned_tail(iwm, base[-settings.lookback_bars - 1 :])
        if qqq_aligned is None or iwm_aligned is None:
            return not_applicable_modifier(self, "wca.modifier.relative_strength_vs_qqq_iwm.unaligned_external_history", "QQQ/IWM inputs are not timestamp-aligned with the WCA snapshot.", settings=settings)
        input_timestamp = snapshot.external_input_timestamps.get("relative_strength_vs_qqq_iwm", latest_timestamp)
        age = (snapshot.decision_timestamp.astimezone(timezone.utc) - input_timestamp.astimezone(timezone.utc)).total_seconds()
        if age > settings.stale_after_seconds:
            return not_applicable_modifier(self, "wca.modifier.relative_strength_vs_qqq_iwm.stale_external_history", "QQQ/IWM relative-strength inputs are stale.", settings=settings)

        base_return = _return(base[-settings.lookback_bars - 1], base[-1])
        qqq_return = _return(qqq_aligned[0], qqq_aligned[-1])
        iwm_return = _return(iwm_aligned[0], iwm_aligned[-1])
        relative_strength = base_return - ((qqq_return + iwm_return) / 2.0)
        contributions = {
            "base_return": round(base_return, 6),
            "qqq_return": round(qqq_return, 6),
            "iwm_return": round(iwm_return, 6),
            "relative_strength": round(relative_strength, 6),
        }
        if relative_strength >= settings.supportive_relative_strength_percent:
            return active_modifier(self, 1.04, "wca.modifier.relative_strength_vs_qqq_iwm.supportive", "Timestamp-aligned relative strength is supportive.", settings=settings, market_status_contributions=contributions)
        if relative_strength <= -settings.weak_relative_strength_percent:
            return active_modifier(self, 0.94, "wca.modifier.relative_strength_vs_qqq_iwm.weak", "Timestamp-aligned relative strength is weak.", settings=settings, risk_multiplier=0.95, position_size_multiplier=0.95, entry_requirement_multiplier=1.05, market_status_contributions=contributions)
        return active_modifier(self, 1.0, "wca.modifier.relative_strength_vs_qqq_iwm.neutral", "Timestamp-aligned relative strength is neutral.", settings=settings, market_status_contributions=contributions)


def _aligned_tail(candidate, reference):
    by_timestamp = {candle.timestamp: candle for candle in candidate}
    aligned = tuple(by_timestamp.get(candle.timestamp) for candle in reference)
    if any(candle is None for candle in aligned):
        return None
    return aligned


def _return(start, end) -> float:
    return (end.close - start.close) / max(start.close, 0.01)
