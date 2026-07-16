"""Primary WCA voter implementations.

These functions are deterministic rule evaluators. They use only the supplied
market snapshot and never call execution, persistence, ML, or other algorithm
services.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY, StrategyConfig, WcaStrategyDefinition


@dataclass(frozen=True)
class PrimaryVoter:
    definition: WcaStrategyDefinition
    evaluator: Callable[[WcaMarketSnapshot, WcaStrategyDefinition], WcaStrategyEvaluation]

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id

    @property
    def name(self) -> str:
        return self.definition.name

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        return self.evaluator(snapshot, self.definition)


def evaluate_all_primary_voters(snapshot: WcaMarketSnapshot, config: StrategyConfig = StrategyConfig()) -> tuple[WcaStrategyEvaluation, ...]:
    return tuple(voter.evaluate(snapshot, config) for voter in WCA_PRIMARY_VOTERS)


def moving_average_trend(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "Moving-average trend is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 50:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for 50 completed candles.")
    close = candles[-1].close
    sma20 = sma(candles, 20)
    sma50 = sma(candles, 50)
    if sma20 > sma50 and close > sma20:
        return active(definition, WcaSide.BUY, min(0.95, 0.45 + abs(sma20 - sma50) / close * 80), "20 SMA is above 50 SMA and price is above 20 SMA.")
    if sma20 < sma50 and close < sma20:
        return active(definition, WcaSide.SELL, min(0.95, 0.45 + abs(sma20 - sma50) / close * 80), "20 SMA is below 50 SMA and price is below 20 SMA.")
    return active(definition, WcaSide.HOLD, 0.2, "Moving averages are mixed.")


def trend_pullback(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "Trend pullback is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 30:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for 30 completed candles.")
    latest = candles[-1]
    sma10 = sma(candles, 10)
    sma30 = sma(candles, 30)
    vwap_value = vwap(candles)
    near_sma10 = abs(latest.close - sma10) / latest.close < 0.004
    if sma10 > sma30 and latest.close > vwap_value and near_sma10 and latest.close > latest.open:
        return active(definition, WcaSide.BUY, 0.68, "Uptrend pullback held near short moving average and resumed upward.")
    if sma10 < sma30 and latest.close < vwap_value and near_sma10 and latest.close < latest.open:
        return active(definition, WcaSide.SELL, 0.68, "Downtrend pullback rejected near short moving average and resumed downward.")
    return active(definition, WcaSide.HOLD, 0.2, "No qualified trend pullback.")


def vwap_trend_continuation(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "VWAP continuation is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 20:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for VWAP trend history.")
    latest = candles[-1]
    prior_vwap = vwap(candles[:-1])
    current_vwap = vwap(candles)
    slope = (current_vwap - prior_vwap) / max(current_vwap, 0.01)
    recent_high = max(c.high for c in candles[-8:-1])
    recent_low = min(c.low for c in candles[-8:-1])
    if slope > 0.00005 and latest.close > current_vwap and latest.close > recent_high:
        return active(definition, WcaSide.BUY, 0.68, "VWAP slope and structure confirm upward continuation.")
    if slope < -0.00005 and latest.close < current_vwap and latest.close < recent_low:
        return active(definition, WcaSide.SELL, 0.68, "VWAP slope and structure confirm downward continuation.")
    return active(definition, WcaSide.HOLD, 0.18, "VWAP trend continuation is not confirmed.")


def vwap_mean_reversion(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "VWAP mean reversion is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 20:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for VWAP mean-reversion history.")
    latest = candles[-1]
    current_vwap = vwap(candles)
    if strong_trend(candles):
        return not_applicable(definition, "wca.regime.strong_trend", "VWAP mean reversion is disabled in a strong trend.")
    distance = (latest.close - current_vwap) / max(current_vwap, 0.01)
    if distance < -0.003 and latest.close >= candles[-2].close:
        return active(definition, WcaSide.BUY, min(0.78, 0.52 + abs(distance) * 35), "Price is stretched below VWAP and no longer accelerating lower.")
    if distance > 0.003 and latest.close <= candles[-2].close:
        return active(definition, WcaSide.SELL, min(0.78, 0.52 + abs(distance) * 35), "Price is stretched above VWAP and no longer accelerating higher.")
    return active(definition, WcaSide.HOLD, 0.16, "VWAP mean-reversion setup is not active.")


def rsi_mean_reversion(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "RSI mean reversion is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 15:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for RSI history.")
    rsi_value = rsi([c.close for c in candles], 14)
    if rsi_value <= 30:
        return active(definition, WcaSide.BUY, min(0.9, 0.5 + (30 - rsi_value) / 35), f"RSI {rsi_value:.1f} is oversold.")
    if rsi_value >= 70:
        return active(definition, WcaSide.SELL, min(0.9, 0.5 + (rsi_value - 70) / 35), f"RSI {rsi_value:.1f} is overbought.")
    return active(definition, WcaSide.HOLD, 0.15, f"RSI {rsi_value:.1f} is neutral.")


def bollinger_atr_reversion(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "Bollinger/ATR reversion is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 21:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for Bollinger and ATR history.")
    latest = candles[-1]
    atr_value = atr(candles, 14)
    if atr_value <= 0:
        return invalid_strategy(definition, "wca.data.invalid_atr", "ATR is unavailable.")
    if directional_expansion(candles, atr_value):
        return not_applicable(definition, "wca.regime.directional_expansion", "Strong directional expansion disables Bollinger/ATR reversion.")
    middle = sma(candles, 20)
    std = standard_deviation([c.close for c in candles[-20:]])
    upper = middle + 2 * std
    lower = middle - 2 * std
    if latest.close < lower and (lower - latest.close) >= atr_value * 0.35 and latest.close >= candles[-2].close:
        return active(definition, WcaSide.BUY, 0.68, "Price is below lower Bollinger band by an ATR-confirmed distance and reversing.")
    if latest.close > upper and (latest.close - upper) >= atr_value * 0.35 and latest.close <= candles[-2].close:
        return active(definition, WcaSide.SELL, 0.68, "Price is above upper Bollinger band by an ATR-confirmed distance and reversing.")
    return active(definition, WcaSide.HOLD, 0.12, "Price is not statistically extended enough for Bollinger/ATR reversion.")


def opening_range_breakout(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    minutes = eastern_minutes(snapshot.data_timestamp)
    if minutes < 9 * 60 + 45 or minutes > 10 * 60 + 30:
        return not_applicable(definition, "wca.session.outside_opening_range_window", "Opening-range breakout only evaluates the post-opening window.")
    candles = completed_candles(snapshot)
    session = same_session_candles(candles, snapshot.data_timestamp)
    if len(session) < 16:
        return not_applicable(definition, "wca.data.insufficient_opening_range", "Waiting for the opening range to complete.")
    latest = session[-1]
    opening = session[:15]
    opening_high = max(c.high for c in opening)
    opening_low = min(c.low for c in opening)
    avg_volume = average_volume(session[:-1], 20)
    volume_expansion = avg_volume > 0 and latest.volume > avg_volume * 1.15
    if latest.close > opening_high and volume_expansion:
        return active(definition, WcaSide.BUY, 0.72, "Close broke the opening-range high with volume.")
    if latest.close < opening_low and volume_expansion:
        return active(definition, WcaSide.SELL, 0.72, "Close broke the opening-range low with volume.")
    return active(definition, WcaSide.HOLD, 0.18, "Opening range has not broken with volume.")


def intraday_volatility_breakout(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    minutes = eastern_minutes(snapshot.data_timestamp)
    if minutes <= 10 * 60 + 30 or minutes >= 15 * 60 + 30:
        return not_applicable(definition, "wca.session.outside_intraday_breakout_window", "Intraday volatility breakout excludes the ORB and closing windows.")
    candles = completed_candles(snapshot)
    if len(candles) < 31:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for intraday volatility structure.")
    latest = candles[-1]
    structure = candles[-21:-1]
    prior_high = max(c.high for c in structure)
    prior_low = min(c.low for c in structure)
    recent_ranges = [c.high - c.low for c in structure[-10:]]
    earlier_ranges = [c.high - c.low for c in structure[:10]]
    compression = sum(recent_ranges) / len(recent_ranges) < (sum(earlier_ranges) / len(earlier_ranges)) * 0.85
    expansion = (latest.high - latest.low) > max(0.01, sum(recent_ranges) / len(recent_ranges)) * 1.35
    volume_expansion = latest.volume > average_volume(candles[:-1], 20) * 1.1
    if compression and expansion and volume_expansion and latest.close > prior_high:
        return active(definition, WcaSide.BUY, 0.66, "Later-session compression expanded through structural resistance.")
    if compression and expansion and volume_expansion and latest.close < prior_low:
        return active(definition, WcaSide.SELL, 0.66, "Later-session compression expanded through structural support.")
    return active(definition, WcaSide.HOLD, 0.12, "No later-session volatility breakout confirmation.")


def failed_breakout_reversal(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "Failed breakout reversal is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 22:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for tested level history.")
    latest = candles[-1]
    prior = candles[-21:-1]
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    if latest.high > prior_high and latest.close < prior_high:
        return active(definition, WcaSide.SELL, 0.70, "Break above prior high failed back inside the range.")
    if latest.low < prior_low and latest.close > prior_low:
        return active(definition, WcaSide.BUY, 0.70, "Break below prior low failed back inside the range.")
    return active(definition, WcaSide.HOLD, 0.14, "No confirmed failed breakout reversal.")


def liquidity_sweep_reversal(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    if outside_regular_session(snapshot):
        return not_applicable(definition, "wca.session.outside_regular", "Liquidity sweep reversal is only evaluated during regular session.")
    candles = completed_candles(snapshot)
    if len(candles) < 22:
        return not_applicable(definition, "wca.data.insufficient_warmup", "Waiting for sweep level history.")
    latest = candles[-1]
    prior = candles[-21:-1]
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    avg_vol = average_volume(candles[:-1], 20)
    volume_expansion = avg_vol > 0 and latest.volume > avg_vol * 1.2
    candle_range = max(latest.high - latest.low, 0.01)
    upper_wick = latest.high - max(latest.open, latest.close)
    lower_wick = min(latest.open, latest.close) - latest.low
    if volume_expansion and latest.high > prior_high and latest.close < prior_high and upper_wick / candle_range >= 0.35:
        return active(definition, WcaSide.SELL, 0.72, "High-side liquidity sweep rejected with expanded volume and upper wick.")
    if volume_expansion and latest.low < prior_low and latest.close > prior_low and lower_wick / candle_range >= 0.35:
        return active(definition, WcaSide.BUY, 0.72, "Low-side liquidity sweep rejected with expanded volume and lower wick.")
    return active(definition, WcaSide.HOLD, 0.14, "No wick-and-volume liquidity sweep reversal.")


def gap_continuation_fade(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation:
    invalid = invalid_result(snapshot, definition)
    if invalid:
        return invalid
    minutes = eastern_minutes(snapshot.data_timestamp)
    if minutes < 9 * 60 + 30 or minutes > 11 * 60:
        return not_applicable(definition, "wca.session.outside_gap_window", "Gap continuation/fade only evaluates the first 90 minutes.")
    candles = completed_candles(snapshot)
    prior_close = previous_regular_close(candles, snapshot.data_timestamp)
    session = same_session_candles(candles, snapshot.data_timestamp)
    if prior_close is None or len(session) < 16:
        return not_applicable(definition, "wca.data.missing_gap_context", "Prior close or opening range is unavailable.")
    latest = session[-1]
    opening_range = session[:15]
    opening_high = max(c.high for c in opening_range)
    opening_low = min(c.low for c in opening_range)
    day_open = session[0].open
    gap = (day_open - prior_close) / prior_close
    if abs(gap) < 0.002:
        return active(definition, WcaSide.HOLD, 0.12, "No meaningful opening gap.")
    current_vwap = vwap(session)
    volume_ok = latest.volume >= average_volume(session[:-1], 20) * 1.1
    if gap > 0 and latest.close > current_vwap and latest.close > opening_high and volume_ok:
        return active(definition, WcaSide.BUY, 0.72, "Gap-up continuation confirmed above VWAP and opening range.")
    if gap > 0 and latest.high >= opening_high and latest.close < current_vwap and latest.close < opening_high and volume_ok:
        return active(definition, WcaSide.SELL, 0.70, "Gap-up fade confirmed after failed opening-range high.")
    if gap < 0 and latest.close < current_vwap and latest.close < opening_low and volume_ok:
        return active(definition, WcaSide.SELL, 0.72, "Gap-down continuation confirmed below VWAP and opening range.")
    if gap < 0 and latest.low <= opening_low and latest.close > current_vwap and latest.close > opening_low and volume_ok:
        return active(definition, WcaSide.BUY, 0.70, "Gap-down fade confirmed after failed opening-range low.")
    return active(definition, WcaSide.HOLD, 0.18, "Gap has not confirmed continuation or fade.")


def invalid_result(snapshot: WcaMarketSnapshot, definition: WcaStrategyDefinition) -> WcaStrategyEvaluation | None:
    if not snapshot.data_ready:
        return invalid_strategy(definition, "wca.data.not_ready", "Market snapshot is not data-ready.")
    candles = completed_candles(snapshot)
    if not candles:
        return invalid_strategy(definition, "wca.data.missing_candles", "No completed candles are available.")
    if any(c.close <= 0 or c.high < c.low or c.volume < 0 for c in candles):
        return invalid_strategy(definition, "wca.data.invalid_candle", "Snapshot contains invalid candle data.")
    return None


def active(definition: WcaStrategyDefinition, signal: WcaSide, confidence: float, explanation: str) -> WcaStrategyEvaluation:
    direction = 1 if signal == WcaSide.BUY else -1 if signal == WcaSide.SELL else 0
    contribution = round(direction * definition.base_weight * confidence, 4)
    return WcaStrategyEvaluation(
        strategy_id=definition.strategy_id,
        name=definition.name,
        status=WcaEvaluationStatus.ACTIVE,
        signal=signal,
        confidence=round(max(0, min(1, confidence)), 4),
        base_weight=definition.base_weight,
        effective_weight=definition.base_weight,
        contribution=contribution,
        reason_codes=(f"wca.strategy.{definition.slug}",),
        explanation=explanation,
    )


def not_applicable(definition: WcaStrategyDefinition, reason_code: str, explanation: str) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=definition.strategy_id,
        name=definition.name,
        status=WcaEvaluationStatus.NOT_APPLICABLE,
        signal=WcaSide.HOLD,
        confidence=0,
        base_weight=definition.base_weight,
        effective_weight=0,
        contribution=0,
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def invalid_strategy(definition: WcaStrategyDefinition, reason_code: str, explanation: str) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=definition.strategy_id,
        name=definition.name,
        status=WcaEvaluationStatus.INVALID,
        signal=WcaSide.HOLD,
        confidence=0,
        base_weight=definition.base_weight,
        effective_weight=0,
        contribution=0,
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def completed_candles(snapshot: WcaMarketSnapshot) -> tuple[WcaCandle, ...]:
    return tuple(sorted(snapshot.candles, key=lambda candle: candle.timestamp))


def eastern_minutes(timestamp: datetime) -> int:
    local = timestamp.astimezone(eastern_timezone(timestamp))
    return local.hour * 60 + local.minute


def outside_regular_session(snapshot: WcaMarketSnapshot) -> bool:
    minutes = eastern_minutes(snapshot.data_timestamp)
    return minutes < 9 * 60 + 30 or minutes >= 16 * 60


def same_session_candles(candles: tuple[WcaCandle, ...], timestamp: datetime) -> tuple[WcaCandle, ...]:
    day = timestamp.astimezone(eastern_timezone(timestamp)).date()
    return tuple(c for c in candles if c.timestamp.astimezone(eastern_timezone(c.timestamp)).date() == day and 9 * 60 + 30 <= eastern_minutes(c.timestamp) < 16 * 60)


def previous_regular_close(candles: tuple[WcaCandle, ...], timestamp: datetime) -> float | None:
    day = timestamp.astimezone(eastern_timezone(timestamp)).date()
    prior = [c for c in candles if c.timestamp.astimezone(eastern_timezone(c.timestamp)).date() < day and 9 * 60 + 30 <= eastern_minutes(c.timestamp) < 16 * 60]
    return prior[-1].close if prior else None


def eastern_timezone(timestamp: datetime) -> timezone:
    utc_timestamp = timestamp.astimezone(timezone.utc)
    year = utc_timestamp.year
    dst_start = nth_weekday_utc(year, 3, 6, 2, 7)
    dst_end = nth_weekday_utc(year, 11, 6, 1, 6)
    offset_hours = -4 if dst_start <= utc_timestamp < dst_end else -5
    return timezone(timedelta(hours=offset_hours))


def nth_weekday_utc(year: int, month: int, weekday: int, occurrence: int, local_hour_after_standard_midnight: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    days_until_weekday = (weekday - first.weekday()) % 7
    day = 1 + days_until_weekday + (occurrence - 1) * 7
    return datetime(year, month, day, local_hour_after_standard_midnight, tzinfo=timezone.utc)


def sma(candles: tuple[WcaCandle, ...], period: int) -> float:
    closes = [c.close for c in candles[-period:]]
    return sum(closes) / len(closes)


def average_volume(candles: tuple[WcaCandle, ...], period: int) -> float:
    if not candles:
        return 0
    values = [c.volume for c in candles[-period:]]
    return sum(values) / len(values)


def vwap(candles: tuple[WcaCandle, ...]) -> float:
    if candles[-1].vwap is not None:
        return candles[-1].vwap
    total_volume = sum(c.volume for c in candles)
    if total_volume <= 0:
        return sum(c.close for c in candles) / len(candles)
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles) / total_volume


def atr(candles: tuple[WcaCandle, ...], period: int) -> float:
    if len(candles) < 2:
        return 0
    selected = candles[-(period + 1):]
    ranges = []
    for index in range(1, len(selected)):
        current = selected[index]
        previous = selected[index - 1]
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return sum(ranges) / len(ranges) if ranges else 0


def rsi(closes: list[float], period: int) -> float:
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    recent = deltas[-period:]
    gains = [max(delta, 0) for delta in recent]
    losses = [abs(min(delta, 0)) for delta in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def standard_deviation(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def strong_trend(candles: tuple[WcaCandle, ...]) -> bool:
    if len(candles) < 20:
        return False
    close = candles[-1].close
    return abs(sma(candles, 10) - sma(candles, 20)) / close > 0.004


def directional_expansion(candles: tuple[WcaCandle, ...], atr_value: float) -> bool:
    latest = candles[-1]
    direction = 1 if latest.close > latest.open else -1 if latest.close < latest.open else 0
    if direction == 0:
        return False
    prior_direction = 1 if candles[-2].close > candles[-2].open else -1 if candles[-2].close < candles[-2].open else 0
    return prior_direction == direction and (latest.high - latest.low) > atr_value * 2


from backend.app.algorithms.wca.strategies.bollinger_atr_reversion import BollingerAtrReversionStrategy
from backend.app.algorithms.wca.strategies.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.algorithms.wca.strategies.gap_continuation_fade import GapContinuationFadeStrategy
from backend.app.algorithms.wca.strategies.intraday_volatility_breakout import IntradayVolatilityBreakoutStrategy
from backend.app.algorithms.wca.strategies.liquidity_sweep_reversal import LiquiditySweepReversalStrategy
from backend.app.algorithms.wca.strategies.moving_average_trend import MovingAverageTrendStrategy
from backend.app.algorithms.wca.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.algorithms.wca.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from backend.app.algorithms.wca.strategies.trend_pullback import TrendPullbackStrategy
from backend.app.algorithms.wca.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from backend.app.algorithms.wca.strategies.vwap_trend_continuation import VwapTrendContinuationStrategy


WCA_PRIMARY_VOTERS = (
    MovingAverageTrendStrategy(),
    TrendPullbackStrategy(),
    VwapTrendContinuationStrategy(),
    VwapMeanReversionStrategy(),
    RsiMeanReversionStrategy(),
    BollingerAtrReversionStrategy(),
    OpeningRangeBreakoutStrategy(),
    IntradayVolatilityBreakoutStrategy(),
    FailedBreakoutReversalStrategy(),
    LiquiditySweepReversalStrategy(),
    GapContinuationFadeStrategy(),
)
