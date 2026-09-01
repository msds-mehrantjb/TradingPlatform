from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Callable

from dataclasses import replace

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedCandle, WeightedDataQualityStatus, WeightedSide, WeightedWeightState
from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
    WEIGHTED_VOTING_STRATEGY_CATALOG,
)
from backend.app.algorithms.weighted_voting.strategies.bollinger_atr_reversion import BollingerAtrReversionStrategy
from backend.app.algorithms.weighted_voting.strategies.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.algorithms.weighted_voting.strategies.first_pullback_after_open import FirstPullbackAfterOpenStrategy
from backend.app.algorithms.weighted_voting.strategies.liquidity_sweep_reversal import LiquiditySweepReversalStrategy
from backend.app.algorithms.weighted_voting.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.algorithms.weighted_voting.strategies.volatility_breakout import VolatilityBreakoutStrategy
from backend.app.algorithms.weighted_voting.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from backend.app.algorithms.weighted_voting.strategies.vwap_trend_continuation import VwapTrendContinuationStrategy
from backend.app.algorithms.weighted_voting.signal_engine import WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID, evaluate_signals


UTC = timezone.utc
SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=UTC)


class WeightedVotingStrategyModulesTest(unittest.TestCase):
    def test_strategy_case_matrix(self) -> None:
        for spec in STRATEGY_SPECS:
            strategy = spec.strategy()
            cases = (
                ("buy", spec.buy(), WeightedSide.BUY),
                ("sell", spec.sell(), WeightedSide.SELL),
                ("hold", spec.hold(), WeightedSide.HOLD),
                ("insufficient", insufficient_snapshot(spec.buy()), WeightedSide.HOLD),
                ("stale", stale_snapshot(spec.buy()), WeightedSide.HOLD),
                ("invalid_session", invalid_session_snapshot(spec.buy()), WeightedSide.HOLD),
                ("borderline", spec.borderline(), WeightedSide.HOLD),
            )
            for case_name, snapshot, expected in cases:
                with self.subTest(strategy=strategy.strategy_id, case=case_name):
                    signal = strategy.evaluate(snapshot)
                    self.assertEqual(signal.strategy_id, strategy.strategy_id)
                    self.assertEqual(signal.signal, expected.value)
                    self.assertAlmostEqual(signal.p_buy + signal.p_sell + signal.p_hold, 1.0, delta=0.000001)
                    self.assertGreater(signal.required_data_freshness_seconds, 0)
                    self.assertIn(signal.data_quality_status, {status.value for status in WeightedDataQualityStatus})
                    self.assertEqual(signal.expected_raw_movement, signal.expected_return)
                    self.assertEqual(signal.signal_strength, signal.strength)
                    self.assertGreaterEqual(signal.directional_confidence, 0)
                    self.assertLessEqual(signal.directional_confidence, 1)
                    if expected is WeightedSide.HOLD:
                        self.assertEqual((signal.p_buy, signal.p_sell, signal.p_hold), (0.0, 0.0, 1.0))
                        self.assertEqual(signal.directional_confidence, 0.0)
                        self.assertEqual(signal.signal_strength, 0.0)
                    if case_name in {"insufficient", "stale"}:
                        self.assertFalse(signal.data_ready)
                        self.assertEqual(signal.data_quality_status, WeightedDataQualityStatus.UNAVAILABLE.value)
                        self.assertIsNone(signal.invalidation_level)
                    if case_name == "invalid_session":
                        self.assertEqual(signal.data_quality_status, WeightedDataQualityStatus.UNAVAILABLE.value)
                    if expected is not WeightedSide.HOLD:
                        self.assertTrue(signal.data_ready)
                        self.assertGreater(signal.directional_confidence, 0)
                        self.assertGreater(signal.signal_strength, 0)
                        self.assertGreater(signal.expected_raw_movement, 0)
                        self.assertIsNotNone(signal.invalidation_level)

    def test_future_candles_are_ignored(self) -> None:
        for spec in STRATEGY_SPECS:
            strategy = spec.strategy()
            base = spec.buy()
            future = make_candle(base.data_timestamp + timedelta(minutes=1), 120.0, 121.0, 80.0, 80.5, 900000)
            with_future = snapshot(base.one_minute_candles + (future,), data_timestamp=base.data_timestamp, bid=base.bid, ask=base.ask)

            with self.subTest(strategy=strategy.strategy_id):
                self.assertEqual(strategy.evaluate(with_future).deterministic_json(), strategy.evaluate(base).deterministic_json())

    def test_signal_engine_evaluates_every_catalogued_strategy_in_catalog_order(self) -> None:
        signals = evaluate_signals(s3_buy_snapshot())

        self.assertEqual([signal.strategy_id for signal in signals], ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"])
        self.assertEqual(len(signals), 8)
        self.assertEqual(set(WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID), {entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG})

    def test_signal_engine_standardizes_strategy_results_and_attaches_active_weight(self) -> None:
        market_snapshot = s2_buy_snapshot()
        condition = classify_market_condition(market_snapshot)
        weights = WeightedWeightState(
            strategy_weights={"S2": 0.25, "S5": 0.25, "S6": 0.25, "S7": 0.25},
            last_updated_at=market_snapshot.data_timestamp,
            data_timestamp=market_snapshot.data_timestamp,
            explanation="Synthetic active weights for signal-engine contract coverage.",
        )

        signals = evaluate_signals(market_snapshot, active_weight_state=weights, market_condition=condition)
        s2 = next(signal for signal in signals if signal.strategy_id == "S2")

        self.assertEqual(s2.strategy_version, "weighted_strategy_S2_v1")
        self.assertEqual(s2.direction, s2.signal)
        self.assertEqual(s2.buy_probability, s2.p_buy)
        self.assertEqual(s2.sell_probability, s2.p_sell)
        self.assertEqual(s2.hold_probability, s2.p_hold)
        self.assertEqual(s2.confidence, s2.directional_confidence)
        self.assertEqual(s2.expected_return_before_costs, s2.expected_return)
        self.assertEqual(s2.base_weight, 0.25)
        self.assertEqual(s2.active_weight, 0.25)
        self.assertTrue(s2.eligible)
        self.assertTrue(s2.active)
        self.assertTrue(s2.data_ready)
        self.assertGreater(s2.market_condition_fit, 0)
        self.assertGreater(s2.final_weight, 0)
        self.assertIn("weighted_voting.signal_engine.strategy_active", s2.reason_codes)
        self.assertEqual(s2.feature_snapshot["strategy_id"], "S2")
        self.assertEqual(s2.feature_snapshot["strategy_lifecycle"], "active")
        self.assertEqual(s2.feature_snapshot["signal_engine_version"], "weighted_voting_signal_engine_v1")
        self.assertIn("market_quality", s2.feature_snapshot)

        for signal in signals:
            with self.subTest(strategy=signal.strategy_id):
                self.assertEqual(signal.algorithm_id, "weighted_voting")
                self.assertAlmostEqual(signal.buy_probability + signal.sell_probability + signal.hold_probability, 1.0, delta=0.000001)
                self.assertIn("completed_one_minute_candles", signal.feature_snapshot)
                self.assertNotIn("voting_ensemble", str(signal.model_dump(mode="json")))

    def test_a_strategy_disabled_in_settings_stops_voting(self) -> None:
        """Settings could switch a strategy off and it would keep voting anyway.

        The resolved state reached the decision's settings snapshot and was reported
        there, but the signal engine never read it, so every surface showed the strategy
        as disabled while its weight still counted.
        """
        snapshot = s2_buy_snapshot()
        config = WeightedVotingConfig()
        disabled = replace(config, strategy_states={**config.strategy_states, "S2": "disabled"})

        voting = next(signal for signal in evaluate_signals(snapshot, config) if signal.strategy_id == "S2")
        stopped = next(signal for signal in evaluate_signals(snapshot, disabled) if signal.strategy_id == "S2")

        self.assertTrue(voting.eligible)
        self.assertGreater(voting.final_weight, 0.0)
        # The strategy still evaluates and still reports its call; it just cannot vote.
        self.assertEqual(stopped.signal, voting.signal)
        self.assertFalse(stopped.eligible)
        self.assertEqual(stopped.base_weight, 0.0)
        self.assertEqual(stopped.active_weight, 0.0)
        self.assertEqual(stopped.final_weight, 0.0)
        self.assertIn("weighted_voting.signal_engine.strategy_disabled_by_settings", stopped.reason_codes)
        self.assertTrue(stopped.feature_snapshot["strategy_runtime_disabled"])

    def test_strategy_eligibility_false_also_stops_a_strategy_voting(self) -> None:
        snapshot = s2_buy_snapshot()
        config = WeightedVotingConfig()
        ineligible = replace(config, strategy_enablement={**config.strategy_enablement, "S2": False})

        signal = next(item for item in evaluate_signals(snapshot, ineligible) if item.strategy_id == "S2")

        self.assertFalse(signal.eligible)
        self.assertEqual(signal.final_weight, 0.0)

    def test_settings_cannot_turn_a_shadow_strategy_into_a_voter(self) -> None:
        """Settings may only ever take a vote away.

        If a runtime flag could grant one, flipping it would put a strategy on capital
        without any of the evidence the promotion gate exists to require.
        """
        snapshot = s2_buy_snapshot()
        config = WeightedVotingConfig()
        forced = replace(
            config,
            strategy_states={strategy_id: "active" for strategy_id in config.strategy_states},
            strategy_enablement={strategy_id: True for strategy_id in config.strategy_enablement},
        )

        signals = {signal.strategy_id: signal for signal in evaluate_signals(snapshot, forced)}

        for strategy_id in WEIGHTED_VOTING_SHADOW_STRATEGY_IDS:
            with self.subTest(strategy_id=strategy_id):
                self.assertFalse(signals[strategy_id].eligible)
                self.assertEqual(signals[strategy_id].final_weight, 0.0)

    def test_shadow_strategies_run_but_carry_no_voting_weight(self) -> None:
        signals = evaluate_signals(s3_buy_snapshot())
        by_id = {signal.strategy_id: signal for signal in signals}

        for strategy_id in WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS:
            with self.subTest(strategy_id=strategy_id):
                self.assertEqual(by_id[strategy_id].feature_snapshot["strategy_lifecycle"], "active")
                self.assertTrue(by_id[strategy_id].feature_snapshot["strategy_contributes_to_vote"])

        # A shadow strategy has to evaluate to produce the evidence its promotion gate wants,
        # so it appears in the signal list -- but with zero weight and marked ineligible, it
        # cannot move the vote.
        for strategy_id in WEIGHTED_VOTING_SHADOW_STRATEGY_IDS:
            with self.subTest(strategy_id=strategy_id):
                signal = by_id[strategy_id]
                self.assertEqual(signal.feature_snapshot["strategy_lifecycle"], "shadow")
                self.assertFalse(signal.feature_snapshot["strategy_contributes_to_vote"])
                self.assertEqual(signal.base_weight, 0.0)
                self.assertEqual(signal.active_weight, 0.0)
                self.assertEqual(signal.final_weight, 0.0)
                self.assertFalse(signal.eligible)
                self.assertFalse(signal.active)
                self.assertIn("weighted_voting.signal_engine.strategy_shadow_observed_zero_weight", signal.reason_codes)

    def test_strategy_pairs_have_distinct_triggers(self) -> None:
        s1_snapshot = s1_buy_snapshot()
        s8_snapshot = s8_buy_snapshot()
        self.assertEqual(OpeningRangeBreakoutStrategy().evaluate(s1_snapshot).signal, WeightedSide.BUY.value)
        self.assertEqual(VolatilityBreakoutStrategy().evaluate(s1_snapshot).signal, WeightedSide.HOLD.value)
        self.assertEqual(VolatilityBreakoutStrategy().evaluate(s8_snapshot).signal, WeightedSide.BUY.value)
        self.assertEqual(OpeningRangeBreakoutStrategy().evaluate(s8_snapshot).signal, WeightedSide.HOLD.value)

        s2_snapshot = s2_buy_snapshot()
        s3_snapshot = s3_buy_snapshot()
        self.assertEqual(FirstPullbackAfterOpenStrategy().evaluate(s2_snapshot).signal, WeightedSide.BUY.value)
        self.assertEqual(VwapTrendContinuationStrategy().evaluate(s2_snapshot).signal, WeightedSide.HOLD.value)
        self.assertEqual(VwapTrendContinuationStrategy().evaluate(s3_snapshot).signal, WeightedSide.BUY.value)
        self.assertEqual(FirstPullbackAfterOpenStrategy().evaluate(s3_snapshot).signal, WeightedSide.HOLD.value)

        self.assertIn("failed", FailedBreakoutReversalStrategy().evaluate(s5_sell_snapshot()).reason_codes[0])
        s6_signal = LiquiditySweepReversalStrategy().evaluate(s6_sell_snapshot())
        self.assertIn("sweep", s6_signal.reason_codes[0])
        self.assertEqual(s6_signal.data_quality_status, WeightedDataQualityStatus.PROXY.value)
        self.assertLess(s6_signal.directional_confidence, 0.7)

        self.assertIn("vwap", VwapMeanReversionStrategy().evaluate(s4_buy_snapshot()).reason_codes[0])
        self.assertIn("bollinger", BollingerAtrReversionStrategy().evaluate(s7_buy_snapshot()).reason_codes[0])


class DedicatedStrategySuiteMixin:
    strategy_factory: Callable
    buy_snapshot: Callable[[], WeightedMarketSnapshot]
    sell_snapshot: Callable[[], WeightedMarketSnapshot]
    hold_snapshot: Callable[[], WeightedMarketSnapshot]
    boundary_snapshot: Callable[[], WeightedMarketSnapshot]

    def test_buy_case(self) -> None:
        signal = self.strategy_factory().evaluate(self.buy_snapshot())

        self.assertEqual(signal.signal, WeightedSide.BUY.value)
        self.assertTrue(signal.data_ready)
        self.assertGreater(signal.directional_confidence, 0)

    def test_sell_case(self) -> None:
        signal = self.strategy_factory().evaluate(self.sell_snapshot())

        self.assertEqual(signal.signal, WeightedSide.SELL.value)
        self.assertTrue(signal.data_ready)
        self.assertGreater(signal.directional_confidence, 0)

    def test_hold_case(self) -> None:
        signal = self.strategy_factory().evaluate(self.hold_snapshot())

        self.assertEqual(signal.signal, WeightedSide.HOLD.value)
        self.assertEqual((signal.p_buy, signal.p_sell, signal.p_hold), (0.0, 0.0, 1.0))

    def test_missing_data_case(self) -> None:
        signal = self.strategy_factory().evaluate(insufficient_snapshot(self.buy_snapshot()))

        self.assertEqual(signal.signal, WeightedSide.HOLD.value)
        self.assertFalse(signal.data_ready)
        self.assertEqual(signal.data_quality_status, WeightedDataQualityStatus.UNAVAILABLE.value)

    def test_stale_data_case(self) -> None:
        signal = self.strategy_factory().evaluate(stale_snapshot(self.buy_snapshot()))

        self.assertEqual(signal.signal, WeightedSide.HOLD.value)
        self.assertFalse(signal.data_ready)
        self.assertEqual(signal.data_quality_status, WeightedDataQualityStatus.UNAVAILABLE.value)

    def test_boundary_threshold_case(self) -> None:
        signal = self.strategy_factory().evaluate(self.boundary_snapshot())

        self.assertEqual(signal.signal, WeightedSide.HOLD.value)

    def test_no_lookahead_case(self) -> None:
        base = self.buy_snapshot()
        future = make_candle(base.data_timestamp + timedelta(minutes=1), 120.0, 121.0, 80.0, 80.5, 900000)
        with_future = snapshot(base.one_minute_candles + (future,), data_timestamp=base.data_timestamp, bid=base.bid, ask=base.ask)

        self.assertEqual(self.strategy_factory().evaluate(with_future).deterministic_json(), self.strategy_factory().evaluate(base).deterministic_json())


class OpeningRangeBreakoutStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = OpeningRangeBreakoutStrategy
    buy_snapshot = staticmethod(lambda: s1_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s1_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s1_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s1_borderline_snapshot())


class FirstPullbackAfterOpenStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = FirstPullbackAfterOpenStrategy
    buy_snapshot = staticmethod(lambda: s2_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s2_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s2_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s2_borderline_snapshot())


class VwapTrendContinuationStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = VwapTrendContinuationStrategy
    buy_snapshot = staticmethod(lambda: s3_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s3_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s3_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s3_borderline_snapshot())


class VwapMeanReversionStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = VwapMeanReversionStrategy
    buy_snapshot = staticmethod(lambda: s4_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s4_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s4_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s4_borderline_snapshot())


class FailedBreakoutReversalStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = FailedBreakoutReversalStrategy
    buy_snapshot = staticmethod(lambda: s5_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s5_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s5_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s5_borderline_snapshot())


class LiquiditySweepReversalStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = LiquiditySweepReversalStrategy
    buy_snapshot = staticmethod(lambda: s6_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s6_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s6_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s6_borderline_snapshot())


class BollingerAtrReversionStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = BollingerAtrReversionStrategy
    buy_snapshot = staticmethod(lambda: s7_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s7_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s7_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s7_borderline_snapshot())


class VolatilityBreakoutStrategySuite(DedicatedStrategySuiteMixin, unittest.TestCase):
    strategy_factory = VolatilityBreakoutStrategy
    buy_snapshot = staticmethod(lambda: s8_buy_snapshot())
    sell_snapshot = staticmethod(lambda: s8_sell_snapshot())
    hold_snapshot = staticmethod(lambda: s8_hold_snapshot())
    boundary_snapshot = staticmethod(lambda: s8_borderline_snapshot())


class StrategySpec:
    def __init__(
        self,
        strategy: Callable,
        buy: Callable[[], WeightedMarketSnapshot],
        sell: Callable[[], WeightedMarketSnapshot],
        hold: Callable[[], WeightedMarketSnapshot],
        borderline: Callable[[], WeightedMarketSnapshot],
    ) -> None:
        self.strategy = strategy
        self.buy = buy
        self.sell = sell
        self.hold = hold
        self.borderline = borderline


def s1_buy_snapshot() -> WeightedMarketSnapshot:
    candles = opening_range_base()
    candles.append(make_candle(SESSION_OPEN + timedelta(minutes=15), 100.04, 100.17, 100.03, 100.16, 240000))
    return snapshot(tuple(candles))


def s1_sell_snapshot() -> WeightedMarketSnapshot:
    candles = opening_range_base()
    candles.append(make_candle(SESSION_OPEN + timedelta(minutes=15), 99.96, 99.97, 99.83, 99.84, 240000))
    return snapshot(tuple(candles))


def s1_hold_snapshot() -> WeightedMarketSnapshot:
    candles = opening_range_base()
    candles.append(make_candle(SESSION_OPEN + timedelta(minutes=15), 100.01, 100.04, 99.98, 100.02, 240000))
    return snapshot(tuple(candles))


def s1_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = opening_range_base()
    candles.append(make_candle(SESSION_OPEN + timedelta(minutes=15), 100.04, 100.10, 100.03, 100.09, 240000))
    return snapshot(tuple(candles))


def s2_buy_snapshot() -> WeightedMarketSnapshot:
    candles = s2_base(up=True)
    candles[-3] = make_candle(candles[-3].timestamp, 100.95, 101.02, 100.38, 100.55, 180000)
    candles[-2] = make_candle(candles[-2].timestamp, 100.55, 100.62, 100.50, 100.58, 170000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.58, 100.86, 100.57, 100.82, 220000)
    return snapshot(tuple(candles))


def s2_sell_snapshot() -> WeightedMarketSnapshot:
    candles = s2_base(up=False)
    candles[-3] = make_candle(candles[-3].timestamp, 99.05, 99.62, 98.98, 99.45, 180000)
    candles[-2] = make_candle(candles[-2].timestamp, 99.45, 99.50, 99.38, 99.42, 170000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.42, 99.43, 99.14, 99.18, 220000)
    return snapshot(tuple(candles))


def s2_hold_snapshot() -> WeightedMarketSnapshot:
    return trend_series(25, SESSION_OPEN, 100.0, 0.0, 100000)


def s2_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = s2_base(up=True)
    candles[-1] = make_candle(candles[-1].timestamp, 100.58, 100.61, 100.57, 100.60, 220000)
    return snapshot(tuple(candles))


def s3_buy_snapshot() -> WeightedMarketSnapshot:
    return trend_series(95, SESSION_OPEN, 100.0, 0.035, 160000, last_time=SESSION_OPEN + timedelta(minutes=95))


def s3_sell_snapshot() -> WeightedMarketSnapshot:
    return trend_series(95, SESSION_OPEN, 103.0, -0.035, 160000, last_time=SESSION_OPEN + timedelta(minutes=95))


def s3_hold_snapshot() -> WeightedMarketSnapshot:
    return flat_series(95, SESSION_OPEN, 100.0, 120000, last_time=SESSION_OPEN + timedelta(minutes=95))


def s3_borderline_snapshot() -> WeightedMarketSnapshot:
    return flat_series(95, SESSION_OPEN, 100.0, 160000, last_time=SESSION_OPEN + timedelta(minutes=95))


def s4_buy_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(34, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-2] = make_candle(candles[-2].timestamp, 99.72, 99.78, 99.50, 99.58, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.55, 99.78, 99.50, 99.72, 150000)
    return snapshot(tuple(candles))


def s4_sell_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(34, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-2] = make_candle(candles[-2].timestamp, 100.28, 100.50, 100.22, 100.42, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.45, 100.50, 100.22, 100.28, 150000)
    return snapshot(tuple(candles))


def s4_hold_snapshot() -> WeightedMarketSnapshot:
    return flat_series(40, SESSION_OPEN + timedelta(minutes=30), 100.0, 130000)


def s4_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(34, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.85, 99.95, 99.80, 99.89, 150000)
    return snapshot(tuple(candles))


def s5_buy_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(31, SESSION_OPEN + timedelta(minutes=35), 100.0, 0.18, 120000)
    candles[-2] = make_candle(candles[-2].timestamp, 99.85, 99.90, 99.55, 99.70, 160000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.72, 100.02, 99.70, 99.95, 170000)
    return snapshot(tuple(candles))


def s5_sell_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(31, SESSION_OPEN + timedelta(minutes=35), 100.0, 0.18, 120000)
    candles[-2] = make_candle(candles[-2].timestamp, 100.15, 100.45, 100.10, 100.30, 160000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.28, 100.30, 99.98, 100.05, 170000)
    return snapshot(tuple(candles))


def s5_hold_snapshot() -> WeightedMarketSnapshot:
    return range_snapshot(35, SESSION_OPEN + timedelta(minutes=35), 100.0, 0.15)


def s5_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(31, SESSION_OPEN + timedelta(minutes=35), 100.0, 0.18, 120000)
    candles[-2] = make_candle(candles[-2].timestamp, 100.12, 100.23, 100.05, 100.20, 160000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.20, 100.25, 100.17, 100.19, 170000)
    return snapshot(tuple(candles))


def s6_buy_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(25, SESSION_OPEN + timedelta(minutes=20), 100.0, 0.18, 120000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.85, 100.00, 99.55, 99.92, 180000)
    return snapshot(tuple(candles))


def s6_sell_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(25, SESSION_OPEN + timedelta(minutes=20), 100.0, 0.18, 120000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.15, 100.45, 100.00, 100.08, 180000)
    return snapshot(tuple(candles))


def s6_hold_snapshot() -> WeightedMarketSnapshot:
    return range_snapshot(30, SESSION_OPEN + timedelta(minutes=20), 100.0, 0.15)


def s6_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = range_candles(25, SESSION_OPEN + timedelta(minutes=20), 100.0, 0.18, 120000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.85, 99.95, 99.78, 99.88, 180000)
    return snapshot(tuple(candles))


def s7_buy_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(55, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-2] = make_candle(candles[-2].timestamp, 99.50, 99.55, 99.35, 99.40, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.35, 99.55, 99.30, 99.50, 145000)
    return snapshot(tuple(candles))


def s7_sell_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(55, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-2] = make_candle(candles[-2].timestamp, 100.50, 100.65, 100.45, 100.60, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 100.65, 100.70, 100.45, 100.50, 145000)
    return snapshot(tuple(candles))


def s7_hold_snapshot() -> WeightedMarketSnapshot:
    return flat_series(55, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)


def s7_borderline_snapshot() -> WeightedMarketSnapshot:
    candles = flat_candles(55, SESSION_OPEN + timedelta(minutes=30), 100.0, 140000)
    candles[-1] = make_candle(candles[-1].timestamp, 99.92, 100.02, 99.90, 99.96, 145000)
    return snapshot(tuple(candles))


def s8_buy_snapshot() -> WeightedMarketSnapshot:
    return s8_snapshot(up=True, breakout=True)


def s8_sell_snapshot() -> WeightedMarketSnapshot:
    return s8_snapshot(up=False, breakout=True)


def s8_hold_snapshot() -> WeightedMarketSnapshot:
    return s8_snapshot(up=True, breakout=False)


def s8_borderline_snapshot() -> WeightedMarketSnapshot:
    return s8_snapshot(up=True, breakout=False, borderline=True)


def opening_range_base() -> list[WeightedCandle]:
    return [
        make_candle(SESSION_OPEN + timedelta(minutes=index), 100.0, 100.05, 99.95, 100.0 + (0.002 if index % 2 else -0.002), 100000)
        for index in range(15)
    ]


def s2_base(up: bool) -> list[WeightedCandle]:
    step = 0.04 if up else -0.04
    candles = []
    for index in range(25):
        price = 100.0 + step * index
        candles.append(make_candle(SESSION_OPEN + timedelta(minutes=index), price, price + 0.08, price - 0.08, price + step * 0.6, 140000))
    return candles


def s8_snapshot(up: bool, breakout: bool, borderline: bool = False) -> WeightedMarketSnapshot:
    start = SESSION_OPEN + timedelta(minutes=45)
    candles: list[WeightedCandle] = []
    for index in range(24):
        center = 100.0 + (0.01 if index % 2 else -0.01)
        candles.append(make_candle(start + timedelta(minutes=index), center, center + 0.04, center - 0.04, center, 100000))
    start = start + timedelta(minutes=24)
    for index in range(20):
        center = 100.0 + (0.01 if index % 2 else -0.01)
        candles.append(make_candle(start + timedelta(minutes=index), center, center + 0.04, center - 0.04, center, 100000))
    for index in range(10):
        direction = 1 if up else -1
        center = 100.0 + direction * 0.08 * index
        candles.append(make_candle(start + timedelta(minutes=20 + index), center, center + 0.16, center - 0.16, center + direction * 0.03, 130000))
    final_base = 100.75 if up else 99.25
    if not breakout:
        final_base = 100.04 if up else 99.96
    if borderline:
        final_base = 100.09 if up else 99.91
    candles.append(
        make_candle(
            start + timedelta(minutes=30),
            final_base - (0.08 if up else -0.08),
            final_base + 0.18,
            final_base - 0.18,
            final_base,
            180000,
        )
    )
    return snapshot(tuple(candles))


def trend_series(count: int, start: datetime, base: float, step: float, volume: float, last_time: datetime | None = None) -> WeightedMarketSnapshot:
    candles = []
    for index in range(count):
        price = base + step * index
        close = price + step * 0.5
        candles.append(make_candle(start + timedelta(minutes=index), price, max(price, close) + 0.05, min(price, close) - 0.05, close, volume))
    if last_time is not None:
        shift = last_time - candles[-1].timestamp
        candles = [make_candle(candle.timestamp + shift, candle.open, candle.high, candle.low, candle.close, candle.volume) for candle in candles]
    return snapshot(tuple(candles))


def flat_series(count: int, start: datetime, base: float, volume: float, last_time: datetime | None = None) -> WeightedMarketSnapshot:
    return snapshot(tuple(flat_candles(count, start, base, volume, last_time=last_time)))


def flat_candles(count: int, start: datetime, base: float, volume: float, last_time: datetime | None = None) -> list[WeightedCandle]:
    candles = [make_candle(start + timedelta(minutes=index), base, base + 0.05, base - 0.05, base, volume) for index in range(count)]
    if last_time is not None:
        shift = last_time - candles[-1].timestamp
        candles = [make_candle(candle.timestamp + shift, candle.open, candle.high, candle.low, candle.close, candle.volume) for candle in candles]
    return candles


def range_snapshot(count: int, start: datetime, base: float, width: float) -> WeightedMarketSnapshot:
    return snapshot(tuple(range_candles(count, start, base, width, 120000)))


def range_candles(count: int, start: datetime, base: float, width: float, volume: float) -> list[WeightedCandle]:
    candles = []
    for index in range(count):
        offset = width * (0.5 if index % 2 else -0.5)
        close = base + offset
        candles.append(make_candle(start + timedelta(minutes=index), base, base + width, base - width, close, volume))
    return candles


def insufficient_snapshot(base: WeightedMarketSnapshot) -> WeightedMarketSnapshot:
    return snapshot(base.one_minute_candles[-3:], data_timestamp=base.data_timestamp, bid=base.bid, ask=base.ask)


def stale_snapshot(base: WeightedMarketSnapshot) -> WeightedMarketSnapshot:
    return snapshot(base.one_minute_candles, data_timestamp=base.data_timestamp + timedelta(minutes=10), bid=base.bid, ask=base.ask)


def invalid_session_snapshot(base: WeightedMarketSnapshot) -> WeightedMarketSnapshot:
    shift = datetime(2026, 7, 14, 20, 30, tzinfo=UTC) - base.one_minute_candles[-1].timestamp
    candles = tuple(make_candle(candle.timestamp + shift, candle.open, candle.high, candle.low, candle.close, candle.volume) for candle in base.one_minute_candles)
    return snapshot(candles, bid=base.bid, ask=base.ask)


def snapshot(
    candles: tuple[WeightedCandle, ...],
    *,
    data_timestamp: datetime | None = None,
    bid: float | None = None,
    ask: float | None = None,
) -> WeightedMarketSnapshot:
    timestamp = data_timestamp or candles[-1].timestamp
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=timestamp,
        one_minute_candles=candles,
        bid=bid,
        ask=ask,
        explanation="Synthetic strategy test snapshot.",
    )


def make_candle(timestamp: datetime, open_: float, high: float, low: float, close: float, volume: float) -> WeightedCandle:
    return WeightedCandle(timestamp=timestamp, open=open_, high=max(high, open_, close), low=min(low, open_, close), close=close, volume=volume)


STRATEGY_SPECS = (
    StrategySpec(OpeningRangeBreakoutStrategy, s1_buy_snapshot, s1_sell_snapshot, s1_hold_snapshot, s1_borderline_snapshot),
    StrategySpec(FirstPullbackAfterOpenStrategy, s2_buy_snapshot, s2_sell_snapshot, s2_hold_snapshot, s2_borderline_snapshot),
    StrategySpec(VwapTrendContinuationStrategy, s3_buy_snapshot, s3_sell_snapshot, s3_hold_snapshot, s3_borderline_snapshot),
    StrategySpec(VwapMeanReversionStrategy, s4_buy_snapshot, s4_sell_snapshot, s4_hold_snapshot, s4_borderline_snapshot),
    StrategySpec(FailedBreakoutReversalStrategy, s5_buy_snapshot, s5_sell_snapshot, s5_hold_snapshot, s5_borderline_snapshot),
    StrategySpec(LiquiditySweepReversalStrategy, s6_buy_snapshot, s6_sell_snapshot, s6_hold_snapshot, s6_borderline_snapshot),
    StrategySpec(BollingerAtrReversionStrategy, s7_buy_snapshot, s7_sell_snapshot, s7_hold_snapshot, s7_borderline_snapshot),
    StrategySpec(VolatilityBreakoutStrategy, s8_buy_snapshot, s8_sell_snapshot, s8_hold_snapshot, s8_borderline_snapshot),
)


if __name__ == "__main__":
    unittest.main()
