from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.catalog import (
    WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS,
    WEIGHTED_VOTING_SHADOW_STRATEGY_IDS,
)
from backend.app.algorithms.weighted_voting.models import WeightedCandle
from backend.app.algorithms.weighted_voting.shadow_evidence import (
    WEIGHTED_VOTING_SHADOW_LIVE_ONLY_METRICS,
    WeightedVotingShadowObservation,
    build_shadow_evidence,
    load_shadow_observations,
    record_shadow_observations,
    review_shadow_promotion,
    shadow_evidence_report,
    shadow_observation_key,
    simulate_shadow_trades,
)
from backend.app.algorithms.weighted_voting.strategy_lifecycle import (
    evaluate_strategy_lifecycle_change,
)


UTC = timezone.utc
START = datetime(2026, 7, 14, 13, 35, tzinfo=UTC)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def candles(count: int = 2000, *, drift: float = 0.02) -> list[WeightedCandle]:
    rows: list[WeightedCandle] = []
    price = 100.0
    for index in range(count):
        price += drift if index % 5 else -drift / 2
        rows.append(
            WeightedCandle(
                timestamp=START + timedelta(minutes=index),
                open=price,
                high=price + 0.35,
                low=price - 0.12,
                close=price,
                volume=100_000,
                finalized=True,
            )
        )
    return rows


def observations(
    bars: list[WeightedCandle],
    *,
    strategy_id: str = "S1",
    every: int = 25,
    side: str = "Buy",
) -> list[WeightedVotingShadowObservation]:
    rows: list[WeightedVotingShadowObservation] = []
    for index, candle in enumerate(bars):
        directional = index % every == 0 and index > 0
        offset = -0.20 if side == "Buy" else 0.20
        rows.append(
            WeightedVotingShadowObservation(
                algorithm_id="weighted_voting",
                strategy_id=strategy_id,
                data_timestamp=candle.timestamp,
                signal=side if directional else "Hold",
                directional_confidence=0.7 if directional else 0.0,
                expected_return=0.01 if directional else 0.0,
                data_ready=True,
                data_quality_status="full",
                invalidation_level=candle.close + offset if directional else None,
                reference_close=candle.close,
                session_label="regular",
                regime_label="uptrend" if index % 2 else "range",
            )
        )
    return rows


class ShadowSimulationTest(unittest.TestCase):
    def test_counterfactual_produces_trades_a_shadow_strategy_never_gets_on_its_own(self) -> None:
        bars = candles()
        trades = simulate_shadow_trades(observations(bars), bars, strategy_id="S1")

        self.assertTrue(trades)
        for trade in trades:
            with self.subTest(entry=trade.entry_timestamp):
                self.assertEqual(trade.strategy_id, "S1")
                self.assertGreater(trade.risk_per_share, 0.0)
                self.assertLess(trade.net_pnl, trade.gross_pnl)  # costs are always taken
                self.assertIn(trade.exit_reason, {"stop", "target", "time_stop"})

    def test_positions_do_not_overlap(self) -> None:
        bars = candles()
        trades = simulate_shadow_trades(observations(bars, every=3), bars, strategy_id="S1")

        for earlier, later in zip(trades, trades[1:]):
            with self.subTest(entry=later.entry_timestamp):
                self.assertGreater(later.entry_timestamp, earlier.exit_timestamp)

    def test_a_signal_whose_stop_is_the_wrong_side_of_entry_is_skipped(self) -> None:
        bars = candles()
        broken = observations(bars)
        for index, item in enumerate(broken):
            if item.signal == "Buy":
                # Stop above entry on a long: there is no coherent risk to normalise by.
                broken[index] = WeightedVotingShadowObservation(
                    **{**item.__dict__, "invalidation_level": (item.reference_close or 0.0) + 1.0}
                )

        self.assertEqual(simulate_shadow_trades(broken, bars, strategy_id="S1"), ())

    def test_a_trade_still_open_when_the_candles_run_out_is_dropped(self) -> None:
        bars = candles(count=40)
        late = [
            item
            for item in observations(bars, every=1)
            if item.data_timestamp >= bars[-2].timestamp
        ]

        # Nothing can be resolved, so nothing is reported as a result.
        self.assertEqual(simulate_shadow_trades(late, bars, strategy_id="S1"), ())


class ShadowEvidenceTest(unittest.TestCase):
    def test_metrics_are_computed_from_the_strategys_own_behaviour(self) -> None:
        bars = candles()
        result = build_shadow_evidence("S1", observations=observations(bars), candles=bars)
        evidence = result.evidence

        self.assertEqual(evidence.strategy_id, "S1")
        self.assertGreater(evidence.eligible_opportunities, 0)
        self.assertEqual(evidence.completed_trades, len(result.trades))
        self.assertGreater(evidence.net_expectancy_after_costs, 0.0)
        self.assertLessEqual(evidence.conservative_expectancy_lower_bound, evidence.net_expectancy_after_costs)
        self.assertEqual(evidence.data_readiness_rate, 1.0)
        self.assertEqual(evidence.data_quality_stability, 1.0)
        self.assertEqual(evidence.strategy_error_rate, 0.0)

    def test_live_only_metrics_are_reported_at_their_failing_value_and_named(self) -> None:
        """The safety property: evidence that was never established must never read as a pass."""
        bars = candles()
        result = build_shadow_evidence("S1", observations=observations(bars), candles=bars)

        for metric in WEIGHTED_VOTING_SHADOW_LIVE_ONLY_METRICS:
            with self.subTest(metric=metric):
                self.assertIn(metric, result.unavailable_metrics)
        self.assertEqual(result.evidence.paper_shadow_stability, 0.0)
        self.assertEqual(result.evidence.paper_backtest_divergence, 1.0)
        self.assertFalse(result.complete)

    def test_a_flattering_sample_is_still_not_promotable(self) -> None:
        bars = candles()
        result = build_shadow_evidence("S1", observations=observations(bars), candles=bars)
        decision = evaluate_strategy_lifecycle_change(result.evidence)

        self.assertFalse(decision.approved)
        failed = {gate.gate_id for gate in decision.gates if not gate.passed}
        self.assertIn("paper_shadow_stability", failed)

    def test_an_empty_record_cannot_slip_through_on_vacuous_metrics(self) -> None:
        result = build_shadow_evidence("S1", observations=[], candles=candles())
        decision = evaluate_strategy_lifecycle_change(result.evidence)

        self.assertEqual(result.evidence.completed_trades, 0)
        self.assertEqual(result.evidence.maximum_drawdown, 1.0)
        self.assertFalse(decision.approved)

    def test_correlation_with_an_active_strategy_is_measured_when_both_series_exist(self) -> None:
        bars = candles()
        active_id = WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS[0]
        mirror = [
            WeightedVotingShadowObservation(**{**item.__dict__, "strategy_id": active_id})
            for item in observations(bars)
        ]

        result = build_shadow_evidence(
            "S1",
            observations=observations(bars),
            candles=bars,
            peer_observations={active_id: mirror},
        )

        self.assertNotIn("correlation_with_active_strategies", result.unavailable_metrics)
        # An exact mirror of an active voter adds no diversification.
        self.assertAlmostEqual(result.evidence.correlation_with_active_strategies, 1.0, places=6)

    def test_correlation_fails_closed_when_no_active_series_was_recorded(self) -> None:
        bars = candles()
        result = build_shadow_evidence("S1", observations=observations(bars), candles=bars)

        self.assertIn("correlation_with_active_strategies", result.unavailable_metrics)
        self.assertEqual(result.evidence.correlation_with_active_strategies, 1.0)

    def test_evidence_cannot_be_built_for_a_strategy_outside_the_catalog(self) -> None:
        with self.assertRaises(KeyError):
            build_shadow_evidence("S99", observations=[], candles=candles())


class ShadowRecordingTest(unittest.TestCase):
    def test_observations_survive_a_round_trip_through_the_store(self) -> None:
        store = MemoryStore()
        bars = candles(count=10)
        signals = [
            {
                "strategyId": "S1",
                "dataTimestamp": bars[0].timestamp.isoformat(),
                "signal": "Buy",
                "directionalConfidence": 0.7,
                "dataReady": True,
                "dataQualityStatus": "full",
                "invalidationLevel": 99.0,
                "featureSnapshot": {"latest_close": 100.0},
                "reasonCodes": ("weighted_voting.s1.opening_range_breakout_buy",),
            }
        ]

        written = record_shadow_observations(store, signals)

        self.assertEqual(written, {"S1": 1})
        self.assertIn(shadow_observation_key("S1"), store.snapshots)
        restored = load_shadow_observations(store, "S1")
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].signal, "Buy")
        self.assertEqual(restored[0].reference_close, 100.0)
        self.assertTrue(restored[0].tradable)

    def test_a_strategy_outside_the_catalog_is_never_written(self) -> None:
        store = MemoryStore()
        signals = [
            {
                "strategyId": "S99",
                "dataTimestamp": START.isoformat(),
                "signal": "Buy",
                "dataReady": True,
            }
        ]

        self.assertEqual(record_shadow_observations(store, signals), {})
        self.assertEqual(store.snapshots, {})

    def test_recording_appends_rather_than_replacing(self) -> None:
        store = MemoryStore()
        for index in range(3):
            record_shadow_observations(
                store,
                [
                    {
                        "strategyId": "S1",
                        "dataTimestamp": (START + timedelta(minutes=index)).isoformat(),
                        "signal": "Hold",
                        "dataReady": True,
                    }
                ],
            )

        self.assertEqual(len(load_shadow_observations(store, "S1")), 3)

    def test_a_failing_store_never_breaks_the_caller(self) -> None:
        class Broken(MemoryStore):
            def write_snapshot(self, key: str, snapshot: dict) -> None:
                raise RuntimeError("disk is gone")

        written = record_shadow_observations(
            Broken(),
            [{"strategyId": "S1", "dataTimestamp": START.isoformat(), "signal": "Hold", "dataReady": True}],
        )

        self.assertEqual(written, {})

    def test_active_strategies_are_recorded_too_so_correlation_is_measurable(self) -> None:
        store = MemoryStore()
        active_id = WEIGHTED_VOTING_ACTIVE_STRATEGY_IDS[0]

        record_shadow_observations(
            store,
            [{"strategyId": active_id, "dataTimestamp": START.isoformat(), "signal": "Buy", "dataReady": True}],
        )

        self.assertEqual(len(load_shadow_observations(store, active_id)), 1)


class ShadowPromotionReviewTest(unittest.TestCase):
    def test_review_reports_the_gate_without_changing_anything(self) -> None:
        store = MemoryStore()
        bars = candles()
        store.write_snapshot(
            shadow_observation_key("S1"),
            {
                "algorithmId": "weighted_voting",
                "strategyId": "S1",
                "observations": [item.as_dict() for item in observations(bars)],
            },
        )
        before = dict(store.snapshots[shadow_observation_key("S1")])

        review = review_shadow_promotion(store, "S1", candles=bars)

        self.assertFalse(review.approved)
        self.assertIn("paper_shadow_stability", review.failed_gates)
        self.assertTrue(review.passed_gates)
        # Reviewing reports the gate; only an operator-approved apply may move a strategy.
        self.assertEqual(store.snapshots[shadow_observation_key("S1")], before)
        latest = store.snapshots["weighted_voting.strategy_lifecycle.snapshot.latest"]
        self.assertEqual(latest["strategy_states"]["S1"], "shadow")

    def test_report_covers_every_shadow_strategy(self) -> None:
        store = MemoryStore()
        report = shadow_evidence_report(store, candles=candles(count=50))

        self.assertEqual(
            [row["strategyId"] for row in report["strategies"]],
            list(WEIGHTED_VOTING_SHADOW_STRATEGY_IDS),
        )
        for row in report["strategies"]:
            with self.subTest(strategy_id=row["strategyId"]):
                self.assertFalse(row["approved"])
                self.assertFalse(row["evidenceComplete"])


if __name__ == "__main__":
    unittest.main()
