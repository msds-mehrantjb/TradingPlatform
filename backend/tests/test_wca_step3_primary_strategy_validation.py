from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest

from backend.app.algorithms.wca.configuration import (
    BollingerAtrReversionSettings,
    FailedBreakoutReversalSettings,
    FirstPullbackAfterOpenSettings,
    GapContinuationFadeSettings,
    IntradayVolatilityBreakoutSettings,
    LiquiditySweepReversalSettings,
    MovingAverageTrendSettings,
    OpeningRangeBreakoutSettings,
    RsiMeanReversionSettings,
    VwapMeanReversionSettings,
    VwapTrendContinuationSettings,
)
from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaQuote, WcaSide
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
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY


UTC = timezone.utc


@dataclass(frozen=True)
class StrategyValidationCase:
    slug: str
    strategy: object
    settings: object
    stricter_settings: object
    buy_snapshot: Callable[[], WcaMarketSnapshot]
    sell_snapshot: Callable[[], WcaMarketSnapshot]
    hold_snapshot: Callable[[], WcaMarketSnapshot]
    not_applicable_snapshot: Callable[[], WcaMarketSnapshot]
    missing_input_snapshot: Callable[[], WcaMarketSnapshot]
    insufficient_warmup_snapshot: Callable[[], WcaMarketSnapshot]
    boundary_snapshot: Callable[[], WcaMarketSnapshot]
    contradictory_snapshot: Callable[[], WcaMarketSnapshot]


CASES: tuple[StrategyValidationCase, ...] = (
    StrategyValidationCase("moving_average_trend", MovingAverageTrendStrategy(), MovingAverageTrendSettings(), MovingAverageTrendSettings(minimum_slope_percent=0.05), lambda: trend_snapshot(0.14), lambda: trend_snapshot(-0.14, start_price=112), lambda: flat_snapshot(), lambda: outside_session_snapshot(), lambda: not_ready_snapshot(), lambda: trend_snapshot(0.14, count=20), lambda: trend_snapshot(0.001), lambda: ma_contradiction_snapshot()),
    StrategyValidationCase("first_pullback_after_open", TrendPullbackStrategy(), FirstPullbackAfterOpenSettings(), FirstPullbackAfterOpenSettings(minimum_impulse_percent=0.05), lambda: first_pullback_buy_snapshot(), lambda: first_pullback_sell_snapshot(), lambda: flat_snapshot(30), lambda: outside_session_snapshot(), lambda: zero_volume_first_pullback_snapshot(), lambda: trend_snapshot(0.1, count=20), lambda: first_pullback_buy_snapshot(impulse=0.003), lambda: first_pullback_contradiction_snapshot()),
    StrategyValidationCase("vwap_trend_continuation", VwapTrendContinuationStrategy(), VwapTrendContinuationSettings(), VwapTrendContinuationSettings(minimum_vwap_slope_percent=0.05), lambda: vwap_continuation_buy_snapshot(), lambda: vwap_continuation_sell_snapshot(), lambda: flat_snapshot(25, volume=150000), lambda: outside_session_snapshot(), lambda: zero_volume_snapshot(), lambda: trend_snapshot(0.1, count=10), lambda: flat_snapshot(25, volume=150000), lambda: vwap_continuation_contradiction_snapshot()),
    StrategyValidationCase("vwap_mean_reversion", VwapMeanReversionStrategy(), VwapMeanReversionSettings(), VwapMeanReversionSettings(minimum_overextension_percent=0.05), lambda: vwap_reversion_buy_snapshot(), lambda: vwap_reversion_sell_snapshot(), lambda: flat_snapshot(22, volume=150000), lambda: trend_snapshot(0.35, count=25), lambda: zero_volume_snapshot(), lambda: flat_snapshot(10), lambda: vwap_reversion_buy_snapshot(extension=0.001), lambda: vwap_reversion_contradiction_snapshot()),
    StrategyValidationCase("rsi_mean_reversion", RsiMeanReversionStrategy(), RsiMeanReversionSettings(maximum_trend_separation_percent=0.02), RsiMeanReversionSettings(oversold_threshold=10, overbought_threshold=90, maximum_trend_separation_percent=0.02), lambda: rsi_buy_snapshot(), lambda: rsi_sell_snapshot(), lambda: flat_snapshot(20), lambda: outside_session_snapshot(), lambda: not_ready_snapshot(), lambda: flat_snapshot(8), lambda: rsi_boundary_snapshot(), lambda: rsi_contradiction_snapshot()),
    StrategyValidationCase("bollinger_atr_reversion", BollingerAtrReversionStrategy(), BollingerAtrReversionSettings(), BollingerAtrReversionSettings(minimum_atr_extension=5.0), lambda: bollinger_buy_snapshot(), lambda: bollinger_sell_snapshot(), lambda: flat_snapshot(25), lambda: outside_session_snapshot(), lambda: not_ready_snapshot(), lambda: flat_snapshot(8), lambda: flat_snapshot(25), lambda: flat_snapshot(25)),
    StrategyValidationCase("opening_range_breakout", OpeningRangeBreakoutStrategy(), OpeningRangeBreakoutSettings(), OpeningRangeBreakoutSettings(volume_expansion_ratio=5.0), lambda: orb_buy_snapshot(), lambda: orb_sell_snapshot(), lambda: orb_hold_snapshot(), lambda: outside_session_snapshot(), lambda: zero_volume_orb_snapshot(), lambda: session_snapshot([candle(regular_start() + timedelta(minutes=i), 100) for i in range(10)]), lambda: orb_boundary_snapshot(), lambda: orb_false_breakout_snapshot()),
    StrategyValidationCase("intraday_volatility_breakout", IntradayVolatilityBreakoutStrategy(), IntradayVolatilityBreakoutSettings(), IntradayVolatilityBreakoutSettings(expansion_ratio=10.0), lambda: intraday_breakout_buy_snapshot(), lambda: intraday_breakout_sell_snapshot(), lambda: intraday_breakout_hold_snapshot(), lambda: orb_hold_snapshot(), lambda: zero_volume_intraday_snapshot(), lambda: flat_snapshot(10, at=datetime(2026, 1, 6, 15, 45, tzinfo=UTC)), lambda: intraday_boundary_snapshot(), lambda: intraday_contradiction_snapshot()),
    StrategyValidationCase("failed_breakout_reversal", FailedBreakoutReversalStrategy(), FailedBreakoutReversalSettings(), FailedBreakoutReversalSettings(minimum_break_percent=0.05), lambda: failed_breakout_buy_snapshot(), lambda: failed_breakout_sell_snapshot(), lambda: failed_breakout_hold_snapshot(), lambda: outside_session_snapshot(), lambda: zero_volume_failed_breakout_snapshot(), lambda: flat_snapshot(8), lambda: failed_breakout_boundary_snapshot(), lambda: failed_breakout_contradiction_snapshot()),
    StrategyValidationCase("liquidity_sweep_reversal", LiquiditySweepReversalStrategy(), LiquiditySweepReversalSettings(), LiquiditySweepReversalSettings(rejection_wick_fraction=0.90), lambda: sweep_buy_snapshot(), lambda: sweep_sell_snapshot(), lambda: sweep_hold_snapshot(), lambda: outside_session_snapshot(), lambda: zero_volume_sweep_snapshot(), lambda: flat_snapshot(8), lambda: sweep_boundary_snapshot(), lambda: sweep_contradiction_snapshot()),
    StrategyValidationCase("gap_continuation_fade", GapContinuationFadeStrategy(), GapContinuationFadeSettings(), GapContinuationFadeSettings(minimum_gap_percent=0.05), lambda: gap_continuation_buy_snapshot(), lambda: gap_fade_sell_snapshot(), lambda: gap_hold_snapshot(), lambda: flat_snapshot(20), lambda: gap_missing_context_snapshot(), lambda: session_snapshot([previous_close(), *[candle(regular_start() + timedelta(minutes=i), 101, volume=100000) for i in range(5)]]), lambda: gap_boundary_snapshot(), lambda: gap_contradiction_snapshot()),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.slug)
def test_each_primary_strategy_covers_required_outcomes(case: StrategyValidationCase) -> None:
    assert case.strategy.definition.slug == case.slug
    assert callable(case.strategy.evaluate)

    expected = (
        ("valid_buy", case.buy_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("valid_sell", case.sell_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("valid_hold", case.hold_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", case.not_applicable_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("missing_input", case.missing_input_snapshot(), None, WcaSide.HOLD.value),
        ("insufficient_warmup", case.insufficient_warmup_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("boundary_values", case.boundary_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("contradictory_evidence", case.contradictory_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("stale_data", stale(case.buy_snapshot()), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    )
    for label, snapshot, expected_status, expected_side in expected:
        result = case.strategy.evaluate(snapshot, case.settings)
        if expected_status is not None:
            assert result.status == expected_status, (case.slug, label, result)
        else:
            assert result.status in {WcaEvaluationStatus.NOT_APPLICABLE.value, WcaEvaluationStatus.INVALID.value}, (case.slug, label, result)
        assert result.signal == expected_side, (case.slug, label, result)
        assert result.raw_confidence == result.confidence
        assert result.calibrated_confidence == result.confidence
        assert 0 <= result.evidence_strength <= 1
        assert result.data_quality_status
        assert result.reason_codes

    first = case.strategy.evaluate(case.buy_snapshot(), case.settings)
    second = case.strategy.evaluate(case.buy_snapshot(), case.settings)
    assert first.deterministic_json() == second.deterministic_json()

    stricter = case.strategy.evaluate(case.buy_snapshot(), case.stricter_settings)
    assert stricter.signal != _opposite(first.signal)
    assert stricter.signal in {WcaSide.HOLD.value, first.signal}

    no_fallback = case.strategy.evaluate(case.contradictory_snapshot(), case.settings)
    assert no_fallback.signal == WcaSide.HOLD.value
    assert no_fallback.status == WcaEvaluationStatus.ACTIVE.value


def test_all_primary_strategies_are_active_after_focused_validation() -> None:
    assert len(WCA_STRATEGY_REGISTRY) == 11
    assert {entry.lifecycle for entry in WCA_STRATEGY_REGISTRY} == {"active"}


def _opposite(side: str) -> str:
    if side == WcaSide.BUY.value:
        return WcaSide.SELL.value
    if side == WcaSide.SELL.value:
        return WcaSide.BUY.value
    return "NONE"


def candle(timestamp: datetime, close: float, *, open_: float | None = None, high: float | None = None, low: float | None = None, volume: float = 120000, vwap: float | None = None) -> WcaCandle:
    open_value = close if open_ is None else open_
    high_value = max(high if high is not None else close + 0.12, open_value, close)
    low_value = min(low if low is not None else close - 0.12, open_value, close)
    return WcaCandle(timestamp=timestamp, open=open_value, high=high_value, low=low_value, close=close, volume=volume, vwap=vwap)


def regular_start() -> datetime:
    return datetime(2026, 1, 6, 14, 30, tzinfo=UTC)


def session_snapshot(candles: list[WcaCandle], *, quote: WcaQuote | None = None, reason_codes: tuple[str, ...] = ()) -> WcaMarketSnapshot:
    latest = candles[-1]
    return WcaMarketSnapshot(symbol="SPY", data_timestamp=latest.timestamp, decision_timestamp=latest.timestamp, candles=tuple(candles), quote=quote, reason_codes=reason_codes)


def stale(snapshot: WcaMarketSnapshot) -> WcaMarketSnapshot:
    return snapshot.model_copy(update={"decision_timestamp": snapshot.data_timestamp + timedelta(minutes=3)})


def not_ready_snapshot() -> WcaMarketSnapshot:
    snap = flat_snapshot(5)
    return snap.model_copy(update={"data_ready": False})


def outside_session_snapshot() -> WcaMarketSnapshot:
    return flat_snapshot(60, at=datetime(2026, 1, 6, 22, 0, tzinfo=UTC))


def flat_snapshot(count: int = 60, close: float = 100, *, volume: float = 120000, at: datetime | None = None) -> WcaMarketSnapshot:
    start = at or regular_start()
    return session_snapshot([candle(start + timedelta(minutes=i), close, open_=close, high=close + 0.05, low=close - 0.05, volume=volume) for i in range(count)])


def zero_volume_snapshot() -> WcaMarketSnapshot:
    return flat_snapshot(25, volume=0)


def trend_snapshot(step: float, *, count: int = 60, start_price: float = 100, at: datetime | None = None) -> WcaMarketSnapshot:
    start = at or regular_start()
    return session_snapshot([candle(start + timedelta(minutes=i), start_price + i * step, open_=start_price + i * step - (0.04 if step >= 0 else -0.04), volume=150000) for i in range(count)])


def ma_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(trend_snapshot(0.14).candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 5, open_=latest.close - 4.5, high=latest.close - 4.3, low=latest.close - 5.2, volume=150000)
    return session_snapshot(candles)


def first_pullback_buy_snapshot(*, impulse: float = 0.012) -> WcaMarketSnapshot:
    start = regular_start()
    candles = _first_pullback_session(start_price=100, impulse=impulse, side=WcaSide.BUY)
    return session_snapshot(candles)


def first_pullback_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    candles = _first_pullback_session(start_price=105, impulse=-0.012, side=WcaSide.SELL)
    return session_snapshot(candles)


def _first_pullback_session(*, start_price: float, impulse: float, side: WcaSide) -> list[WcaCandle]:
    start = regular_start()
    impulse_end = start_price * (1 + impulse)
    candles = [candle(start + timedelta(minutes=i), start_price + (impulse_end - start_price) * (i + 1) / 10, volume=220000) for i in range(10)]
    if side == WcaSide.BUY:
        pullback = [impulse_end - 0.18 - (i % 3) * 0.03 for i in range(19)]
        candles.extend(candle(start + timedelta(minutes=10 + i), value, high=value + 0.08, low=value - 0.10, volume=120000) for i, value in enumerate(pullback))
        candles.append(candle(start + timedelta(minutes=29), impulse_end + 0.10, open_=impulse_end - 0.08, high=impulse_end + 0.16, low=impulse_end - 0.12, volume=150000))
    else:
        pullback = [impulse_end + 0.18 + (i % 3) * 0.03 for i in range(19)]
        candles.extend(candle(start + timedelta(minutes=10 + i), value, high=value + 0.10, low=value - 0.08, volume=120000) for i, value in enumerate(pullback))
        candles.append(candle(start + timedelta(minutes=29), impulse_end - 0.10, open_=impulse_end + 0.08, high=impulse_end + 0.12, low=impulse_end - 0.16, volume=150000))
    return candles


def zero_volume_first_pullback_snapshot() -> WcaMarketSnapshot:
    return session_snapshot([bar.model_copy(update={"volume": 0}) for bar in first_pullback_buy_snapshot().candles])


def first_pullback_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(first_pullback_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 0.50, open_=latest.close - 0.40, high=latest.close - 0.35, low=latest.close - 0.60, volume=150000)
    return session_snapshot(candles)


def vwap_continuation_buy_snapshot(*, step: float = 0.06) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100 + i * step, volume=150000) for i in range(22)]
    candles.extend([candle(start + timedelta(minutes=22), 101.25, volume=130000), candle(start + timedelta(minutes=23), 101.35, volume=130000), candle(start + timedelta(minutes=24), 102.2, volume=170000)])
    return session_snapshot(candles)


def vwap_continuation_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 104 - i * 0.06, volume=150000) for i in range(22)]
    candles.extend([candle(start + timedelta(minutes=22), 102.75, volume=130000), candle(start + timedelta(minutes=23), 102.65, volume=130000), candle(start + timedelta(minutes=24), 101.8, volume=170000)])
    return session_snapshot(candles)


def vwap_continuation_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(vwap_continuation_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 1.5, open_=latest.close - 1.3, volume=170000)
    return session_snapshot(candles)


def vwap_reversion_buy_snapshot(*, extension: float = 0.006) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100 + (0.03 if i % 2 else -0.03), volume=150000) for i in range(19)]
    candles.append(candle(start + timedelta(minutes=19), 100 * (1 - extension * 1.25), open_=100 * (1 - extension), low=100 * (1 - extension * 1.8), volume=160000))
    candles.append(candle(start + timedelta(minutes=20), 100 * (1 - extension), open_=100 * (1 - extension * 1.35), high=100 * (1 - extension * 0.7), low=100 * (1 - extension * 1.7), volume=160000))
    return session_snapshot(candles)


def vwap_reversion_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100 + (0.03 if i % 2 else -0.03), volume=150000) for i in range(19)]
    candles.append(candle(start + timedelta(minutes=19), 100.75, open_=100.55, high=100.95, volume=160000))
    candles.append(candle(start + timedelta(minutes=20), 100.55, open_=100.80, high=100.95, low=100.40, volume=160000))
    return session_snapshot(candles)


def vwap_reversion_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(vwap_reversion_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 0.20, open_=latest.close + 0.05, low=latest.close - 0.35, volume=160000)
    return session_snapshot(candles)


def rsi_buy_snapshot() -> WcaMarketSnapshot:
    closes = [100, 100.02, 99.84, 99.66, 99.48, 99.30, 99.32, 99.14, 98.96, 98.78, 98.60, 98.62, 98.44, 98.26, 98.08, 97.90, 98.10]
    return _closes_snapshot(closes)


def rsi_sell_snapshot() -> WcaMarketSnapshot:
    closes = [100, 99.98, 100.16, 100.34, 100.52, 100.70, 100.68, 100.86, 101.04, 101.22, 101.40, 101.38, 101.56, 101.74, 101.92, 102.10, 101.90]
    return _closes_snapshot(closes)


def rsi_boundary_snapshot() -> WcaMarketSnapshot:
    return _closes_snapshot([100 + (0.05 if i % 2 else -0.05) for i in range(20)])


def rsi_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(rsi_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 0.50, open_=latest.close - 0.20, volume=120000)
    return session_snapshot(candles)


def _closes_snapshot(closes: list[float]) -> WcaMarketSnapshot:
    start = regular_start()
    return session_snapshot([candle(start + timedelta(minutes=i), close, open_=closes[i - 1] if i else close, volume=120000) for i, close in enumerate(closes)])


def bollinger_buy_snapshot(*, extension: float = 1.6) -> WcaMarketSnapshot:
    start = regular_start()
    closes = [100 + (0.04 if i % 2 else -0.04) for i in range(19)]
    candles = [candle(start + timedelta(minutes=i), close, volume=120000) for i, close in enumerate(closes)]
    candles.append(candle(start + timedelta(minutes=19), 100 - extension, open_=99.0, low=98.1, volume=130000))
    candles.append(candle(start + timedelta(minutes=20), 99.0, open_=98.4, high=99.2, low=98.0, volume=130000))
    return session_snapshot(candles)


def bollinger_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    closes = [100 + (0.04 if i % 2 else -0.04) for i in range(19)]
    candles = [candle(start + timedelta(minutes=i), close, volume=120000) for i, close in enumerate(closes)]
    candles.append(candle(start + timedelta(minutes=19), 101.6, open_=101.0, high=101.9, volume=130000))
    candles.append(candle(start + timedelta(minutes=20), 101.0, open_=101.6, high=102.0, low=100.8, volume=130000))
    return session_snapshot(candles)


def bollinger_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(bollinger_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, latest.close - 0.5, open_=latest.close, low=latest.close - 0.8, volume=130000)
    return session_snapshot(candles)


def orb_buy_snapshot() -> WcaMarketSnapshot:
    return _orb_snapshot(WcaSide.BUY)


def orb_sell_snapshot() -> WcaMarketSnapshot:
    return _orb_snapshot(WcaSide.SELL)


def orb_hold_snapshot() -> WcaMarketSnapshot:
    return _orb_snapshot(WcaSide.HOLD)


def _orb_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100, high=100.2, low=99.8, volume=120000) for i in range(15)]
    if side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=15), 100.85, open_=100.35, high=100.95, low=100.30, volume=180000))
    elif side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=15), 99.15, open_=99.65, high=99.70, low=99.05, volume=180000))
    else:
        candles.append(candle(start + timedelta(minutes=15), 100.05, high=100.15, low=99.90, volume=110000))
    return session_snapshot(candles)


def zero_volume_orb_snapshot() -> WcaMarketSnapshot:
    return session_snapshot([bar.model_copy(update={"volume": 0}) for bar in orb_buy_snapshot().candles])


def orb_boundary_snapshot() -> WcaMarketSnapshot:
    candles = list(orb_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 100.21, high=100.30, low=100.00, volume=180000)
    return session_snapshot(candles)


def orb_false_breakout_snapshot() -> WcaMarketSnapshot:
    candles = list(orb_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 100.30, open_=100.25, high=101.50, low=100.20, volume=180000)
    return session_snapshot(candles)


def intraday_breakout_buy_snapshot() -> WcaMarketSnapshot:
    return _intraday_breakout_snapshot(WcaSide.BUY)


def intraday_breakout_sell_snapshot() -> WcaMarketSnapshot:
    return _intraday_breakout_snapshot(WcaSide.SELL)


def intraday_breakout_hold_snapshot() -> WcaMarketSnapshot:
    return _intraday_breakout_snapshot(WcaSide.HOLD)


def _intraday_breakout_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = datetime(2026, 1, 6, 15, 40, tzinfo=UTC)
    candles = []
    for i in range(10):
        candles.append(candle(start + timedelta(minutes=i), 100, high=100.5, low=99.5, volume=120000))
    for i in range(10, 20):
        candles.append(candle(start + timedelta(minutes=i), 100, high=100.45, low=99.55, volume=120000))
    for i in range(20, 30):
        candles.append(candle(start + timedelta(minutes=i), 100, high=100.1, low=99.9, volume=120000))
    if side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=30), 100.75, open_=100.05, high=100.85, low=99.95, volume=170000))
    elif side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=30), 99.25, open_=99.95, high=100.05, low=99.15, volume=170000))
    else:
        candles.append(candle(start + timedelta(minutes=30), 100.03, high=100.10, low=99.95, volume=120000))
    return session_snapshot(candles, quote=WcaQuote(timestamp=candles[-1].timestamp, bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01))


def zero_volume_intraday_snapshot() -> WcaMarketSnapshot:
    return session_snapshot([bar.model_copy(update={"volume": 0}) for bar in intraday_breakout_buy_snapshot().candles])


def intraday_boundary_snapshot() -> WcaMarketSnapshot:
    candles = list(intraday_breakout_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 100.44, high=100.57, low=99.95, volume=170000)
    return session_snapshot(candles)


def intraday_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(intraday_breakout_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 100.40, open_=100.75, high=100.80, low=99.95, volume=170000)
    return session_snapshot(candles)


def failed_breakout_buy_snapshot() -> WcaMarketSnapshot:
    return _failed_breakout_snapshot(WcaSide.BUY)


def failed_breakout_sell_snapshot() -> WcaMarketSnapshot:
    return _failed_breakout_snapshot(WcaSide.SELL)


def failed_breakout_hold_snapshot() -> WcaMarketSnapshot:
    return _failed_breakout_snapshot(WcaSide.HOLD)


def _failed_breakout_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100, high=100.5, low=99.5, volume=120000) for i in range(21)]
    if side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=21), 100.25, open_=100.8, high=101.0, low=100.10, volume=130000))
    elif side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=21), 99.75, open_=99.2, high=99.90, low=99.0, volume=130000))
    else:
        candles.append(candle(start + timedelta(minutes=21), 100.10, high=100.30, low=99.90, volume=120000))
    return session_snapshot(candles)


def zero_volume_failed_breakout_snapshot() -> WcaMarketSnapshot:
    return session_snapshot([bar.model_copy(update={"volume": 0}) for bar in failed_breakout_buy_snapshot().candles])


def failed_breakout_boundary_snapshot() -> WcaMarketSnapshot:
    candles = list(failed_breakout_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 99.50, open_=99.2, high=99.80, low=99.45, volume=130000)
    return session_snapshot(candles)


def failed_breakout_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(failed_breakout_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 99.75, open_=99.9, high=100.0, low=99.0, volume=130000)
    return session_snapshot(candles)


def sweep_buy_snapshot() -> WcaMarketSnapshot:
    return _sweep_snapshot(WcaSide.BUY)


def sweep_sell_snapshot() -> WcaMarketSnapshot:
    return _sweep_snapshot(WcaSide.SELL)


def sweep_hold_snapshot() -> WcaMarketSnapshot:
    return _sweep_snapshot(WcaSide.HOLD)


def _sweep_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=i), 100, high=100.5, low=99.5, volume=120000) for i in range(21)]
    if side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=21), 100.25, open_=100.65, high=101.20, low=100.20, volume=180000))
    elif side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=21), 99.75, open_=99.35, high=99.80, low=98.80, volume=180000))
    else:
        candles.append(candle(start + timedelta(minutes=21), 100.05, high=100.25, low=99.85, volume=120000))
    return session_snapshot(candles)


def zero_volume_sweep_snapshot() -> WcaMarketSnapshot:
    return session_snapshot([bar.model_copy(update={"volume": 0}) for bar in sweep_buy_snapshot().candles])


def sweep_boundary_snapshot() -> WcaMarketSnapshot:
    candles = list(sweep_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 99.60, open_=99.40, high=99.80, low=99.46, volume=180000)
    return session_snapshot(candles)


def sweep_contradiction_snapshot() -> WcaMarketSnapshot:
    candles = list(sweep_buy_snapshot().candles)
    latest = candles[-1]
    candles[-1] = candle(latest.timestamp, 99.20, open_=99.60, high=99.80, low=98.80, volume=180000)
    return session_snapshot(candles)


def previous_close() -> WcaCandle:
    return candle(datetime(2026, 1, 5, 20, 59, tzinfo=UTC), 100, volume=120000)


def gap_continuation_buy_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 101.0, high=101.2, low=100.8, volume=120000) for i in range(15)]
    session.append(candle(start + timedelta(minutes=15), 101.65, open_=101.10, high=101.75, low=101.05, volume=180000))
    return session_snapshot([previous_close(), *session])


def gap_fade_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 101.0, high=101.2, low=100.8, volume=120000) for i in range(15)]
    session.append(candle(start + timedelta(minutes=15), 100.65, open_=101.15, high=101.45, low=100.55, volume=180000))
    return session_snapshot([previous_close(), *session])


def gap_hold_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 100.1, high=100.2, low=100.0, volume=120000) for i in range(16)]
    return session_snapshot([previous_close(), *session])


def gap_missing_context_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 101.0, volume=120000) for i in range(16)]
    return session_snapshot(session)


def gap_boundary_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 100.19, high=100.25, low=100.10, volume=120000) for i in range(16)]
    return session_snapshot([previous_close(), *session])


def gap_contradiction_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    session = [candle(start + timedelta(minutes=i), 101.0, high=101.2, low=100.8, volume=120000) for i in range(15)]
    session.append(candle(start + timedelta(minutes=15), 101.05, open_=101.00, high=101.12, low=100.95, volume=180000))
    return session_snapshot([previous_close(), *session])
