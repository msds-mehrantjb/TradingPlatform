from __future__ import annotations

import inspect
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import (
    CandidateGeometryConfig,
    CandidateGeometryDraft,
    CandidateGeometryValidationError,
    DeterministicCandidate,
    MetaStrategyMarketSnapshot,
    calculate_candidate_geometry,
    validate_candidate_geometry,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyStep15CandidateGeometryTest(unittest.TestCase):
    maxDiff = None

    def test_long_geometry_calculates_entry_stop_target_cost_and_net_reward_risk(self) -> None:
        result = calculate_candidate_geometry(snapshot_fixture(), candidate("BUY"))

        self.assertEqual(result.geometry.side, "BUY")
        self.assertEqual(result.entry_reference, 101.01)
        self.assertLess(result.geometry.stop_price, result.geometry.entry_price)
        self.assertGreater(result.geometry.target_price, result.geometry.entry_price)
        self.assertEqual(result.maximum_holding_minutes, 30)
        self.assertGreater(result.stop_distance, 0)
        self.assertGreater(result.target_distance, result.stop_distance)
        self.assertGreater(result.estimated_cost, 0)
        self.assertGreater(result.expected_net_reward_risk, 1.0)
        self.assertIn("meta_strategy.geometry.valid", result.reason_codes)
        self.assertIn("meta_strategy.geometry.calculated", result.reason_codes)

    def test_short_geometry_calculates_inverse_stop_and_target(self) -> None:
        result = calculate_candidate_geometry(snapshot_fixture(price=99.0), candidate("SELL"))

        self.assertEqual(result.geometry.side, "SELL")
        self.assertEqual(result.entry_reference, 98.99)
        self.assertGreater(result.geometry.stop_price, result.geometry.entry_price)
        self.assertLess(result.geometry.target_price, result.geometry.entry_price)
        self.assertGreater(result.geometry.risk_reward, 0)

    def test_zero_or_negative_stop_distance_is_rejected(self) -> None:
        draft = CandidateGeometryDraft(
            side="BUY",
            entry_price=100.0,
            stop_price=100.0,
            target_price=102.0,
            stop_distance=0.0,
            target_distance=2.0,
            estimated_cost=0.01,
            reward_risk=None,
            expected_net_reward_risk=None,
        )

        with self.assertRaisesRegex(CandidateGeometryValidationError, "invalid_stop_distance"):
            validate_candidate_geometry(draft)

    def test_gap_boundary_widens_stop_and_shortens_holding_period(self) -> None:
        base = calculate_candidate_geometry(snapshot_fixture(), candidate("BUY"))
        gap = calculate_candidate_geometry(snapshot_fixture(gap_state={"state": "gap_up", "gapPercent": 2.0}), candidate("BUY"))

        self.assertGreater(gap.stop_distance, base.stop_distance)
        self.assertEqual(gap.maximum_holding_minutes, 20)
        self.assertIn("meta_strategy.geometry.gap_boundary", gap.reason_codes)

    def test_volatility_boundary_widens_stop_and_shortens_holding_period(self) -> None:
        base = calculate_candidate_geometry(snapshot_fixture(), candidate("BUY"))
        volatile = calculate_candidate_geometry(snapshot_fixture(atr={"1m": 3.0}), candidate("BUY"))

        self.assertGreater(volatile.stop_distance, base.stop_distance)
        self.assertEqual(volatile.maximum_holding_minutes, 15)
        self.assertIn("meta_strategy.geometry.volatility_boundary", volatile.reason_codes)

    def test_hold_candidate_has_no_trade_geometry(self) -> None:
        result = calculate_candidate_geometry(snapshot_fixture(), candidate("HOLD", eligible=False))

        self.assertEqual(result.geometry.side, "HOLD")
        self.assertEqual(result.geometry.quantity, 0)
        self.assertIsNone(result.geometry.entry_price)
        self.assertEqual(result.maximum_holding_minutes, 0)
        self.assertIn("meta_strategy.geometry.hold_no_trade", result.reason_codes)

    def test_candidate_validation_cannot_be_bypassed_by_ml(self) -> None:
        invalid_config = CandidateGeometryConfig(
            atr_stop_multiplier=0.0,
            minimum_stop_percent=0.0,
            spread_stop_multiplier=0.0,
            commission_per_share=0.0,
            slippage_bps=0.0,
        )
        snapshot = snapshot_fixture(atr={"1m": 0.0}, spread={"basisPoints": 0.0, "dollars": 0.0}, features={"mlApproved": True})

        with self.assertRaisesRegex(CandidateGeometryValidationError, "invalid_stop_distance"):
            calculate_candidate_geometry(snapshot, candidate("BUY"), config=invalid_config)

    def test_geometry_modules_do_not_import_ml_and_validation_is_not_optional(self) -> None:
        package_dir = Path(__file__).parents[1] / "app" / "algorithms" / "meta_strategy"
        for relative in ("candidate_geometry.py", "candidate_validation.py"):
            with self.subTest(file=relative):
                source = (package_dir / relative).read_text(encoding="utf-8")
                self.assertNotIn("backend.app.ml", source)
                self.assertNotIn("ModelPrediction", source)
        signature = inspect.signature(calculate_candidate_geometry)
        self.assertNotIn("model_prediction", signature.parameters)


def candidate(side: str, *, eligible: bool = True) -> DeterministicCandidate:
    return DeterministicCandidate(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-geometry-1",
        snapshot_id="snapshot-geometry-1",
        timestamp=NOW,
        signal=side,
        confidence=0.8 if side != "HOLD" else 0.0,
        eligible=eligible,
        family_scores=(),
        reason_codes=("test.candidate",),
    )


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    features = {"cashAvailable": 10_000.0}
    features.update(overrides.get("features", {}))
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-geometry-1",
        snapshot_id="snapshot-geometry-1",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=round(price - 0.01, 6),
        ask_price=round(price + 0.01, 6),
        spread_bps=5.0,
        volume=100_000,
        source_cutoff_timestamp=NOW,
        point_in_time=True,
        candles={"1m": candles(60, price)},
        vwap=100.0,
        moving_averages={"1m": {"ema20": 101.0, "ema50": 100.0}},
        atr=overrides.get("atr", {"1m": 1.0}),
        adx={"1m": 24.0},
        rsi={"1m": 50.0},
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands={"1m": {"upper": 102.0, "middle": 100.0, "lower": 98.0}},
        relative_volume={"1m": 1.2},
        spread=overrides.get("spread", {"basisPoints": 5.0, "dollars": 0.02}),
        liquidity={"level": "good", "score": 0.8},
        session_phase="morning",
        gap_state=overrides.get("gap_state", {"state": "flat_open", "gapPercent": 0.0}),
        qqq_iwm_context={"spyVsQqq": 1.0, "spyVsIwm": 1.0},
        breadth={"averageReturn": 0.001, "positiveShare": 0.56, "componentCount": 500},
        economic_event_state={"state": "none", "severity": "none", "minutesToEvent": 60, "active": False},
        features=features,
    )


def candles(count: int, close: float) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "timestamp": NOW.isoformat(),
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100_000,
        }
        for _ in range(count)
    )


if __name__ == "__main__":
    unittest.main()
