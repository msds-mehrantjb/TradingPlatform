from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaSide
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.strategy_registry import (
    WCA_DEPRECATED_ALIASES,
    WCA_HARD_FILTER_REGISTRY,
    WCA_HARD_FILTER_SLUGS,
    WCA_MODIFIER_REGISTRY,
    WCA_MODIFIER_SLUGS,
    WCA_MODULE_CATALOG,
    WCA_PRIMARY_VOTER_SLUGS,
    WCA_STRATEGY_IDS,
    WCA_STRATEGY_REGISTRY,
    WcaCatalogRole,
    resolve_wca_module_slug,
    validate_wca_module_catalog,
)
from backend.tests.test_wca_step3_primary_strategy_validation import CASES as FOCUSED_STRATEGY_CASES


UTC = timezone.utc
ROOT = Path(__file__).parents[2]
WCA_CATALOG_DOC = ROOT / "docs" / "wca" / "authoritative_module_catalog.md"


class WcaStep3StrategyCatalogTest(unittest.TestCase):
    def test_catalog_registers_exactly_11_primary_voters(self) -> None:
        expected_inventory = (
            ("C1", "moving_average_trend", "Moving Average Trend", "trend", 0.10),
            ("C2", "first_pullback_after_open", "First Pullback After Open", "trend", 0.09),
            ("C3", "vwap_trend_continuation", "VWAP Trend Continuation", "trend", 0.09),
            ("C4", "vwap_mean_reversion", "VWAP Mean Reversion", "mean_reversion", 0.08),
            ("C5", "rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", 0.08),
            ("C6", "bollinger_atr_reversion", "Bollinger/ATR Reversion", "mean_reversion", 0.08),
            ("C7", "opening_range_breakout", "Opening Range Breakout", "breakout", 0.10),
            ("C8", "intraday_volatility_breakout", "Intraday/Volatility Breakout", "breakout", 0.10),
            ("C9", "failed_breakout_reversal", "Failed Breakout Reversal", "reversal", 0.09),
            ("C10", "liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", 0.09),
            ("C11", "gap_continuation_fade", "Gap Continuation/Fade", "event", 0.10),
        )
        self.assertEqual(len(WCA_STRATEGY_REGISTRY), 11)
        self.assertEqual(
            tuple((row.strategy_id, row.slug, row.name, row.family, row.base_weight) for row in WCA_STRATEGY_REGISTRY),
            expected_inventory,
        )
        self.assertEqual(WCA_STRATEGY_IDS, {f"C{index}" for index in range(1, 12)})
        self.assertEqual(WCA_PRIMARY_VOTER_SLUGS, {row[1] for row in expected_inventory})
        self.assertEqual({row.role for row in WCA_STRATEGY_REGISTRY}, {WcaCatalogRole.PRIMARY_VOTER})
        self.assertTrue(all(row.family for row in WCA_STRATEGY_REGISTRY))
        self.assertAlmostEqual(sum(row.base_weight for row in WCA_STRATEGY_REGISTRY), 1.0, places=6)

    def test_catalog_contains_required_operational_metadata(self) -> None:
        self.assertEqual(len(WCA_MODULE_CATALOG), 29)
        for entry in WCA_MODULE_CATALOG:
            with self.subTest(module=entry.slug):
                self.assertTrue(entry.slug)
                self.assertTrue(entry.name)
                self.assertTrue(entry.family)
                self.assertIn(entry.lifecycle, {"active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"})
                self.assertTrue(entry.implementation_import_path)
                self.assertTrue(entry.settings_model)
                self.assertTrue(entry.settings_version)
                self.assertTrue(entry.strategy_version)
                self.assertTrue(entry.minimum_history)
                self.assertTrue(entry.required_market_inputs)

    def test_startup_validation_rejects_catalog_drift(self) -> None:
        self.assertTrue(validate_wca_module_catalog()["valid"])

        duplicate_id = replace(WCA_STRATEGY_REGISTRY[1], strategy_id="C1")
        self.assertIn("duplicate_id:C1", validate_wca_module_catalog((WCA_STRATEGY_REGISTRY[0], duplicate_id, *WCA_STRATEGY_REGISTRY[2:]))["errors"])

        bad_weight = replace(WCA_STRATEGY_REGISTRY[0], base_weight=0.11)
        self.assertTrue(any(error.startswith("primary_baseline_weights_total:") for error in validate_wca_module_catalog((bad_weight, *WCA_STRATEGY_REGISTRY[1:]))["errors"]))

        bad_role = replace(WCA_MODIFIER_REGISTRY[0], role=WcaCatalogRole.PRIMARY_VOTER)
        self.assertIn("modifier_or_hard_filter_registered_as_primary", validate_wca_module_catalog(WCA_STRATEGY_REGISTRY, (bad_role, *WCA_MODIFIER_REGISTRY[1:]), WCA_HARD_FILTER_REGISTRY)["errors"])

        missing_settings = replace(WCA_STRATEGY_REGISTRY[1], settings_model="")
        self.assertIn("active_strategy_missing_settings_model:first_pullback_after_open", validate_wca_module_catalog((WCA_STRATEGY_REGISTRY[0], missing_settings, *WCA_STRATEGY_REGISTRY[2:]))["errors"])

    def test_deprecated_trend_pullback_alias_resolves_without_second_voter(self) -> None:
        self.assertEqual(WCA_DEPRECATED_ALIASES[0].alias_slug, "trend_pullback")
        self.assertEqual(resolve_wca_module_slug("trend_pullback"), "first_pullback_after_open")
        self.assertNotIn("trend_pullback", WCA_PRIMARY_VOTER_SLUGS)
        self.assertEqual([row.strategy_id for row in WCA_STRATEGY_REGISTRY if row.slug == "first_pullback_after_open"], ["C2"])

    def test_documentation_matches_authoritative_catalog(self) -> None:
        self.assertEqual(_doc_rows("Primary Voters"), _catalog_rows(WCA_STRATEGY_REGISTRY))
        self.assertEqual(_doc_rows("Contextual Modifiers"), _catalog_rows(WCA_MODIFIER_REGISTRY))
        self.assertEqual(_doc_rows("Hard Filters"), _catalog_rows(WCA_HARD_FILTER_REGISTRY))

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
        expected_filter_inventory = (
            ("cash_avoid_trading", "Cash/Avoid Trading"),
            ("economic_event_risk", "Economic Event Risk"),
            ("invalid_or_stale_data", "Invalid or Stale Data"),
            ("unsafe_spread", "Unsafe Spread"),
            ("unsafe_liquidity", "Unsafe Liquidity"),
            ("extreme_volatility", "Extreme Volatility"),
            ("session_entry_block", "Session Entry Block"),
        )
        self.assertEqual(WCA_MODIFIER_SLUGS, required_modifiers)
        self.assertEqual(WCA_HARD_FILTER_SLUGS, required_filters)
        self.assertEqual(tuple((row.slug, row.name) for row in WCA_HARD_FILTER_REGISTRY), expected_filter_inventory)
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
    "first_pullback_after_open": (
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

STRATEGY_CASES = {
    case.slug: (
        ("buy", case.buy_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.BUY.value),
        ("sell", case.sell_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.SELL.value),
        ("hold", case.hold_snapshot(), WcaEvaluationStatus.ACTIVE.value, WcaSide.HOLD.value),
        ("not_applicable", case.not_applicable_snapshot(), WcaEvaluationStatus.NOT_APPLICABLE.value, WcaSide.HOLD.value),
        ("invalid", invalid_snapshot(), WcaEvaluationStatus.INVALID.value, WcaSide.HOLD.value),
    )
    for case in FOCUSED_STRATEGY_CASES
}


def _catalog_rows(entries) -> tuple[tuple[str, str, str, str, str, str], ...]:
    rows = []
    for entry in entries:
        stable_id = entry.strategy_id if hasattr(entry, "strategy_id") else entry.module_id
        weight = f"{entry.base_weight:.2f}" if hasattr(entry, "base_weight") else ""
        rows.append((stable_id, entry.slug, entry.name, entry.family, entry.role.value, entry.lifecycle, weight))
    return tuple(rows)


def _doc_rows(section: str) -> tuple[tuple[str, str, str, str, str, str], ...]:
    lines = WCA_CATALOG_DOC.read_text(encoding="utf-8").splitlines()
    header = f"## {section}"
    start = lines.index(header)
    rows: list[tuple[str, str, str, str, str, str]] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if not line.startswith("|") or line.startswith("| ID ") or line.startswith("| ---"):
            continue
        cells = tuple(cell.strip().strip("`") for cell in line.strip("|").split("|"))
        rows.append(cells)
    return tuple(rows)


if __name__ == "__main__":
    unittest.main()
