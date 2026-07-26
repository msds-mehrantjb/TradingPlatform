from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from typing import Any

from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategyDefinition
from backend.app.algorithms.wca.strategies.indicators import active, average_volume, coerce_strategy_settings, completed_candles, definition_for, invalid_result, not_applicable, outside_regular_session, same_session_candles, sma, vwap


class TrendPullbackStrategy:
    strategy_id = "C2"
    slug = "first_pullback_after_open"
    name = "First Pullback After Open"
    family = "trend"
    version = "wca_first_pullback_after_open_v1"
    base_weight = 0.09
    configuration = StrategyConfig()
    minimum_data_requirements = ("30 completed regular-session candles",)
    performance_history_identifier = "wca.first_pullback_after_open.performance.v1"
    backtest_diagnostic_identifier = "wca.first_pullback_after_open.backtest.v1"

    @property
    def definition(self) -> WcaStrategyDefinition:
        return definition_for(self)

    def evaluate(self, market: WcaMarketSnapshot, config: Any = None) -> WcaStrategyEvaluation:
        from backend.app.algorithms.wca.configuration import FirstPullbackAfterOpenSettings

        config = coerce_strategy_settings(FirstPullbackAfterOpenSettings, config)
        if not config.enabled:
            return not_applicable(self, "wca.config.disabled", "First pullback after open is disabled.")
        invalid = invalid_result(market, self)
        if invalid:
            return invalid
        if outside_regular_session(market):
            return not_applicable(self, "wca.session.outside_regular", "First pullback after open is only evaluated during regular session.")
        candles = completed_candles(market)
        session = same_session_candles(candles, market.data_timestamp)
        minimum = max(30, config.opening_impulse_minutes + 4)
        if len(session) < minimum:
            return not_applicable(self, "wca.data.insufficient_warmup", f"Waiting for {minimum} completed session candles.")
        if not all(c.volume > 0 for c in session):
            return not_applicable(self, "wca.data.missing_volume", "First pullback requires volume contraction evidence.")
        latest_index = len(session) - 1
        eligible = self._eligible_at(session, latest_index, config)
        prior_eligible = any(self._eligible_at(session[: index + 1], index, config)[0] != WcaSide.HOLD for index in range(config.opening_impulse_minutes + 3, latest_index))
        if prior_eligible:
            return active(self, WcaSide.HOLD, 0.10, "The first valid pullback already occurred earlier in the session.", evidence_strength=0.15, reason_codes=("wca.c2.first_pullback.already_used",))
        side, confidence, explanation, reasons = eligible
        if side != WcaSide.HOLD:
            return active(self, side, confidence, explanation, reason_codes=reasons)
        return active(self, WcaSide.HOLD, 0.12, explanation, evidence_strength=0.2, reason_codes=reasons)

    def _eligible_at(self, session: tuple, index: int, config: Any) -> tuple[WcaSide, float, str, tuple[str, ...]]:
        if index < config.opening_impulse_minutes + 3:
            return (WcaSide.HOLD, 0.0, "Opening impulse and pullback sequence is incomplete.", ("wca.c2.sequence.incomplete",))
        opening = session[: config.opening_impulse_minutes]
        latest = session[index]
        impulse_start = opening[0].open
        impulse_end = opening[-1].close
        impulse_percent = (impulse_end - impulse_start) / max(impulse_start, 0.01)
        if abs(impulse_percent) < config.minimum_impulse_percent:
            return (WcaSide.HOLD, 0.0, "Opening impulse is not large enough to qualify.", ("wca.c2.impulse.too_small",))
        direction = WcaSide.BUY if impulse_percent > 0 else WcaSide.SELL
        sequence = session[config.opening_impulse_minutes : index]
        if len(sequence) < 2:
            return (WcaSide.HOLD, 0.0, "Pullback leg is incomplete.", ("wca.c2.pullback.incomplete",))
        impulse_range = abs(impulse_end - impulse_start)
        impulse_volume = average_volume(opening, len(opening))
        pullback_volume = average_volume(sequence, len(sequence))
        contraction = pullback_volume <= impulse_volume * config.pullback_volume_contraction_ratio
        current_vwap = vwap(session[: index + 1])
        fast = sma(session[: index + 1], min(10, len(session[: index + 1])))
        slow = sma(session[: index + 1], min(30, len(session[: index + 1])))
        if direction == WcaSide.BUY:
            pullback_low = min(c.low for c in sequence)
            retrace = (impulse_end - pullback_low) / max(impulse_range, 0.01)
            origin_protected = pullback_low > impulse_start
            vwap_preserved = pullback_low >= current_vwap * (1 - config.vwap_tolerance_percent) or latest.close > current_vwap * (1 + config.confirmation_close_buffer_percent)
            confirmation = latest.close > max(c.high for c in sequence) * (1 + config.confirmation_close_buffer_percent) and latest.close > latest.open
            trend = fast > slow and latest.close > current_vwap
            if all((trend, contraction, retrace <= config.pullback_max_retrace_percent, origin_protected, vwap_preserved, confirmation)):
                confidence = min(0.84, 0.58 + abs(impulse_percent) * 20 + max(0, 1 - retrace) * 0.12)
                return (WcaSide.BUY, confidence, "First post-open pullback held origin/VWAP and confirmed upward.", ("wca.c2.first_pullback.buy",))
        else:
            pullback_high = max(c.high for c in sequence)
            retrace = (pullback_high - impulse_end) / max(impulse_range, 0.01)
            origin_protected = pullback_high < impulse_start
            vwap_preserved = pullback_high <= current_vwap * (1 + config.vwap_tolerance_percent) or latest.close < current_vwap * (1 - config.confirmation_close_buffer_percent)
            confirmation = latest.close < min(c.low for c in sequence) * (1 - config.confirmation_close_buffer_percent) and latest.close < latest.open
            trend = fast < slow and latest.close < current_vwap
            if all((trend, contraction, retrace <= config.pullback_max_retrace_percent, origin_protected, vwap_preserved, confirmation)):
                confidence = min(0.84, 0.58 + abs(impulse_percent) * 20 + max(0, 1 - retrace) * 0.12)
                return (WcaSide.SELL, confidence, "First post-open pullback protected origin/VWAP and confirmed downward.", ("wca.c2.first_pullback.sell",))
        return (WcaSide.HOLD, 0.0, "No qualifying first pullback after opening impulse.", ("wca.c2.first_pullback.no_setup",))
