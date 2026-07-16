from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaSide
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.strategy_registry import (
    WCA_HARD_FILTER_REGISTRY,
    WCA_HARD_FILTER_SLUGS,
    WCA_MODIFIER_REGISTRY,
    WCA_MODIFIER_SLUGS,
    WCA_PRIMARY_VOTER_SLUGS,
    WCA_STRATEGY_REGISTRY,
    WcaCatalogRole,
)


UTC = timezone.utc


class WcaStep3StrategyCatalogTest(unittest.TestCase):
    def test_catalog_registers_exactly_11_primary_voters(self) -> None:
        expected = {
            "moving_average_trend",
            "trend_pullback",
            "vwap_trend_continuation",
            "vwap_mean_reversion",
            "rsi_mean_reversion",
            "bollinger_atr_reversion",
            "opening_range_breakout",
            "intraday_volatility_breakout",
            "failed_breakout_reversal",
            "liquidity_sweep_reversal",
            "gap_continuation_fade",
        }
        self.assertEqual(len(WCA_STRATEGY_REGISTRY), 11)
        self.assertEqual(WCA_PRIMARY_VOTER_SLUGS, expected)
        self.assertEqual({row.role for row in WCA_STRATEGY_REGISTRY}, {WcaCatalogRole.PRIMARY_VOTER})
        self.assertTrue(all(row.family for row in WCA_STRATEGY_REGISTRY))
        self.assertAlmostEqual(sum(row.base_weight for row in WCA_STRATEGY_REGISTRY), 1.0, places=6)

    def test_modifiers_and_hard_filters_are_not_primary_votes(self) -> None:
        required_modifiers = {
            "vwap_position",
            "volume_confirmation",
            "macd_momentum",
            "market_structure",
            "adx_trend_strength",
            "atr_volatility_regime",
            "multi_timeframe_trend_alignment",
            "relative_strength_vs_qqq_iwm",
            "market_breadth",
            "session_phase",
            "spread_liquidity",
        }
        required_filters = {
            "cash_avoid_trading",
            "economic_event_risk",
            "invalid_or_stale_data",
            "unsafe_spread",
            "unsafe_liquidity",
            "extreme_volatility",
            "session_entry_block",
        }
        self.assertEqual(WCA_MODIFIER_SLUGS, required_modifiers)
        self.assertEqual(WCA_HARD_FILTER_SLUGS, required_filters)
        self.assertFalse(WCA_PRIMARY_VOTER_SLUGS & WCA_MODIFIER_SLUGS)
        self.assertFalse(WCA_PRIMARY_VOTER_SLUGS & WCA_HARD_FILTER_SLUGS)
        self.assertEqual({row.role for row in WCA_MODIFIER_REGISTRY}, {WcaCatalogRole.MODIFIER})
        self.assertEqual({row.role for row in WCA_HARD_FILTER_REGISTRY}, {WcaCatalogRole.HARD_FILTER})

    def test_every_primary_voter_has_required_outcomes(self) -> None:
        voters = {voter.definition.slug: voter for voter in WCA_PRIMARY_VOTERS}
        for slug, cases in STRATEGY_CASES.items():
            voter = voters[slug]
            for label, snapshot, expected_status, expected_side in cases:
                with self.subTest(strategy=slug, case=label):
                    result = voter.evaluate(snapshot)
                    self.assertEqual(result.status, expected_status)
                    self.assertEqual(result.signal, expected_side)
                    if expected_status in {WcaEvaluationStatus.NOT_APPLICABLE.value, WcaEvaluationStatus.INVALID.value}:
                        self.assertEqual(result.contribution, 0)

    def test_gap_strategy_produces_only_one_vote(self) -> None:
        voter = {voter.definition.slug: voter for voter in WCA_PRIMARY_VOTERS}["gap_continuation_fade"]
        result = voter.evaluate(gap_buy_snapshot())
        self.assertEqual(result.signal, WcaSide.BUY.value)
        self.assertIn("continuation", result.explanation.lower())
        self.assertNotIn("fade", result.explanation.lower())


def snapshot(candles: list[WcaCandle], *, data_ready: bool = True) -> WcaMarketSnapshot:
    latest = candles[-1]
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=tuple(candles),
        data_ready=data_ready,
    )


def candle(timestamp: datetime, close: float, *, open_: float | None = None, high: float | None = None, low: float | None = None, volume: float = 100000, vwap: float | None = None) -> WcaCandle:
    open_value = close if open_ is None else open_
    high_value = max(high if high is not None else close + 0.08, open_value, close)
    low_value = min(low if low is not None else close - 0.08, open_value, close)
    return WcaCandle(timestamp=timestamp, open=open_value, high=high_value, low=low_value, close=close, volume=volume, vwap=vwap)


def regular_start(hour: int = 14, minute: int = 30) -> datetime:
    return datetime(2026, 1, 6, hour, minute, tzinfo=UTC)


def trend_snapshot(step: float, count: int = 60, start: float = 100, at: datetime | None = None) -> WcaMarketSnapshot:
    start_time = at or regular_start()
    candles = [candle(start_time + timedelta(minutes=index), start + index * step, volume=100000) for index in range(count)]
    return snapshot(candles)


def flat_snapshot(count: int = 60, close: float = 100, at: datetime | None = None) -> WcaMarketSnapshot:
    start_time = at or regular_start()
    candles = [candle(start_time + timedelta(minutes=index), close, volume=100000) for index in range(count)]
    return snapshot(candles)


def outside_session_snapshot() -> WcaMarketSnapshot:
    return flat_snapshot(at=datetime(2026, 1, 6, 22, 0, tzinfo=UTC))


def invalid_snapshot() -> WcaMarketSnapshot:
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=regular_start(),
        decision_timestamp=regular_start(),
        candles=(candle(regular_start(), 100),),
        data_ready=False,
    )


def trend_pullback_buy_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=index), 100 + index * 0.12) for index in range(29)]
    candles.append(candle(start + timedelta(minutes=29), 102.9, open_=102.65))
    return snapshot(candles)


def trend_pullback_sell_snapshot() -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=index), 105 - index * 0.12) for index in range(29)]
    candles.append(candle(start + timedelta(minutes=29), 102.1, open_=102.35))
    return snapshot(candles)


def vwap_continuation_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    if side == WcaSide.BUY:
        closes = [100 + index * 0.03 for index in range(19)] + [101.1]
    elif side == WcaSide.SELL:
        closes = [101 - index * 0.03 for index in range(19)] + [99.9]
    else:
        closes = [100 for _ in range(20)]
    return snapshot([candle(start + timedelta(minutes=index), close, volume=100000) for index, close in enumerate(closes)])


def vwap_reversion_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    if side == WcaSide.BUY:
        closes = [100 for _ in range(18)] + [99.4, 99.5]
    elif side == WcaSide.SELL:
        closes = [100 for _ in range(18)] + [100.6, 100.5]
    else:
        closes = [100 for _ in range(20)]
    return snapshot([candle(start + timedelta(minutes=index), close, volume=100000) for index, close in enumerate(closes)])


def rsi_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    if side == WcaSide.BUY:
        closes = [105 - index * 0.5 for index in range(16)]
    elif side == WcaSide.SELL:
        closes = [100 + index * 0.5 for index in range(16)]
    else:
        closes = [100 + (index % 2) * 0.2 for index in range(16)]
    return snapshot([candle(start + timedelta(minutes=index), close) for index, close in enumerate(closes)])


def bollinger_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    if side == WcaSide.BUY:
        closes = [100 for _ in range(19)] + [98.6, 98.8]
    elif side == WcaSide.SELL:
        closes = [100 for _ in range(19)] + [101.4, 101.2]
    else:
        closes = [100 + (index % 2) * 0.05 for index in range(21)]
    return snapshot([candle(start + timedelta(minutes=index), close, open_=close - 0.03 if index % 2 else close + 0.03) for index, close in enumerate(closes)])


def orb_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=index), 100, high=100.2, low=99.8, volume=100000) for index in range(15)]
    if side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=15), 100.8, high=101.0, low=100.3, volume=150000))
    elif side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=15), 99.2, high=99.7, low=99.0, volume=150000))
    else:
        candles.append(candle(start + timedelta(minutes=15), 100.1, high=100.2, low=99.9, volume=90000))
    return snapshot(candles)


def intraday_breakout_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = datetime(2026, 1, 6, 15, 40, tzinfo=UTC)
    candles = []
    for index in range(10):
        candles.append(candle(start + timedelta(minutes=index), 100, high=100.2, low=99.8, volume=100000))
    for index in range(10, 20):
        candles.append(candle(start + timedelta(minutes=index), 100, high=100.5, low=99.5, volume=100000))
    for index in range(20, 30):
        candles.append(candle(start + timedelta(minutes=index), 100, high=100.1, low=99.9, volume=100000))
    if side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=30), 100.7, high=100.8, low=99.8, volume=140000))
    elif side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=30), 99.3, high=100.2, low=99.2, volume=140000))
    else:
        candles.append(candle(start + timedelta(minutes=30), 100.05, high=100.15, low=99.95, volume=100000))
    return snapshot(candles)


def failed_breakout_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=index), 100, high=100.5, low=99.5, volume=100000) for index in range(21)]
    if side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=21), 100.3, high=101.0, low=100.1, volume=100000))
    elif side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=21), 99.7, high=99.9, low=99.0, volume=100000))
    else:
        candles.append(candle(start + timedelta(minutes=21), 100.1, high=100.3, low=99.9, volume=100000))
    return snapshot(candles)


def sweep_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    start = regular_start()
    candles = [candle(start + timedelta(minutes=index), 100, high=100.5, low=99.5, volume=100000) for index in range(21)]
    if side == WcaSide.SELL:
        candles.append(candle(start + timedelta(minutes=21), 100.3, open_=100.4, high=101.2, low=100.2, volume=140000))
    elif side == WcaSide.BUY:
        candles.append(candle(start + timedelta(minutes=21), 99.7, open_=99.6, high=99.8, low=98.8, volume=140000))
    else:
        candles.append(candle(start + timedelta(minutes=21), 100.1, high=100.3, low=99.9, volume=100000))
    return snapshot(candles)


def gap_buy_snapshot() -> WcaMarketSnapshot:
    return gap_snapshot(WcaSide.BUY)


def gap_snapshot(side: WcaSide) -> WcaMarketSnapshot:
    prior = candle(datetime(2026, 1, 5, 20, 59, tzinfo=UTC), 100)
    start = regular_start()
    if side == WcaSide.HOLD:
        session = [candle(start + timedelta(minutes=index), 100.1, high=100.2, low=100.0, volume=100000) for index in range(16)]
    else:
        session = [candle(start + timedelta(minutes=index), 101.0, high=101.2, low=100.8, volume=100000) for index in range(15)]
        if side == WcaSide.BUY:
            session.append(candle(start + timedelta(minutes=15), 101.5, high=101.6, low=101.1, volume=140000))
        else:
            session.append(candle(start + timedelta(minutes=15), 100.8, high=101.4, low=100.7, volume=140000))
    return snapshot([prior, *session])


STRATEGY_CASES = {
    "moving_average_trend": (
        ("buy", trend_snapshot(0.08), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", trend_snapshot(-0.08, start=105), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", flat_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "trend_pullback": (
        ("buy", trend_pullback_buy_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", trend_pullback_sell_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", flat_snapshot(30), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "vwap_trend_continuation": (
        ("buy", vwap_continuation_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", vwap_continuation_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", vwap_continuation_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "vwap_mean_reversion": (
        ("buy", vwap_reversion_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", vwap_reversion_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", vwap_reversion_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", trend_snapshot(0.2, 25), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "rsi_mean_reversion": (
        ("buy", rsi_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", rsi_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", rsi_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "bollinger_atr_reversion": (
        ("buy", bollinger_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", bollinger_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", bollinger_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "opening_range_breakout": (
        ("buy", orb_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", orb_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", orb_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "intraday_volatility_breakout": (
        ("buy", intraday_breakout_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", intraday_breakout_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", intraday_breakout_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", orb_snapshot(WcaSide.HOLD), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "failed_breakout_reversal": (
        ("buy", failed_breakout_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", failed_breakout_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", failed_breakout_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "liquidity_sweep_reversal": (
        ("buy", sweep_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", sweep_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", sweep_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", outside_session_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
    "gap_continuation_fade": (
        ("buy", gap_snapshot(WcaSide.BUY), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", gap_snapshot(WcaSide.SELL), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", gap_snapshot(WcaSide.HOLD), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", flat_snapshot(20), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    ),
}


if __name__ == "__main__":
    unittest.main()
