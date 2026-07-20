from __future__ import annotations

import importlib
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import SAFETY_STRATEGIES, MetaStrategyMarketSnapshot


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
FORBIDDEN_EVIDENCE_KEYS = {"candidateSide", "entry", "stop", "target", "orderIntent", "positionSize", "buyScore", "sellScore"}


@dataclass(frozen=True)
class SafetyCase:
    trigger: dict[str, Any]
    non_trigger: dict[str, Any]
    boundary: dict[str, Any]
    missing: dict[str, Any]
    trigger_reason: str
    pass_reason: str
    missing_reason: str
    existing_position_action: str
    boundary_blocks: bool = False


class MetaStrategyStep12SafetyInventoryTest(unittest.TestCase):
    maxDiff = None

    def test_full_safety_inventory_is_registered(self) -> None:
        expected = {
            "cash_avoid_trading_filter",
            "missing_critical_data_filter",
            "stale_market_data_filter",
            "excessive_spread_filter",
            "insufficient_liquidity_filter",
            "extreme_volatility_filter",
            "economic_event_blackout_filter",
            "unsupported_session_filter",
            "halt_luld_filter",
            "operational_health_filter",
        }
        self.assertEqual({entry.strategy_id for entry in SAFETY_STRATEGIES}, expected)
        self.assertEqual(len(SAFETY_STRATEGIES), 10)

    def test_each_safety_module_has_required_behavior_coverage(self) -> None:
        for entry in SAFETY_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            case = safety_cases()[entry.strategy_id]

            with self.subTest(strategy=entry.strategy_id, case="trigger"):
                result = strategy.evaluate(snapshot_fixture(**case.trigger))
                assert_safety_result(self, result, blocks=True, reason=case.trigger_reason, existing_position_action=case.existing_position_action)

            with self.subTest(strategy=entry.strategy_id, case="non_trigger"):
                result = strategy.evaluate(snapshot_fixture(**case.non_trigger))
                assert_safety_result(self, result, blocks=False, reason=case.pass_reason, existing_position_action="ALLOW_MANAGE")

            with self.subTest(strategy=entry.strategy_id, case="threshold_boundary"):
                result = strategy.evaluate(snapshot_fixture(**case.boundary))
                assert_safety_result(
                    self,
                    result,
                    blocks=case.boundary_blocks,
                    reason=case.trigger_reason if case.boundary_blocks else case.pass_reason,
                    existing_position_action=case.existing_position_action if case.boundary_blocks else "ALLOW_MANAGE",
                )

            with self.subTest(strategy=entry.strategy_id, case="missing_data"):
                result = strategy.evaluate(snapshot_fixture(**case.missing))
                assert_safety_result(self, result, blocks=True, reason=case.missing_reason, existing_position_action="MONITOR")
                self.assertTrue(result.evidence["missingDataSafe"])
                self.assertIn(False, result.required_input_status.values())

            with self.subTest(strategy=entry.strategy_id, case="entry_blocking"):
                result = strategy.evaluate(snapshot_fixture(**case.trigger))
                self.assertTrue(result.evidence["entryBlocking"])
                self.assertFalse(result.eligible)

            with self.subTest(strategy=entry.strategy_id, case="existing_position_behavior"):
                result = strategy.evaluate(snapshot_fixture(**case.trigger))
                self.assertEqual(result.evidence["existingPositionAction"], case.existing_position_action)

    def test_safety_modules_do_not_generate_trades_or_votes(self) -> None:
        for entry in SAFETY_STRATEGIES:
            result = strategy_for(entry.strategy_id).evaluate(snapshot_fixture(**safety_cases()[entry.strategy_id].trigger))
            with self.subTest(strategy=entry.strategy_id):
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.evidence["canGenerateTrade"])
                self.assertFalse(result.evidence["castsIndependentVote"])
                self.assertFalse(FORBIDDEN_EVIDENCE_KEYS.intersection(result.evidence))
                self.assertEqual(entry.supported_directions, ("HOLD",))

    def test_safety_registry_uses_dedicated_modules(self) -> None:
        for entry in SAFETY_STRATEGIES:
            with self.subTest(strategy=entry.strategy_id):
                self.assertTrue(entry.implementation_module.startswith("backend.app.algorithms.meta_strategy.strategies.safety."))
                self.assertNotEqual(entry.implementation_module, "backend.app.algorithms.meta_strategy.strategies.safety")

    def test_safety_sources_do_not_encode_directional_signals(self) -> None:
        safety_dir = Path(__file__).parents[1] / "app" / "algorithms" / "meta_strategy" / "strategies" / "safety"
        for path in safety_dir.glob("*.py"):
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn('signal="BUY"', source)
                self.assertNotIn('signal="SELL"', source)
                self.assertNotIn("signal='BUY'", source)
                self.assertNotIn("signal='SELL'", source)
                self.assertNotIn("buyScore", source)
                self.assertNotIn("sellScore", source)


def assert_safety_result(test: unittest.TestCase, result, *, blocks: bool, reason: str, existing_position_action: str) -> None:
    test.assertEqual(result.signal, "HOLD")
    test.assertEqual(result.eligible, not blocks)
    test.assertEqual(result.evidence["blocksNewEntries"], blocks)
    test.assertEqual(result.evidence["entryBlocking"], blocks)
    test.assertEqual(result.evidence["existingPositionAction"], existing_position_action)
    test.assertEqual(result.reason_codes, (reason,))
    test.assertEqual(result.evidence["reasonCode"], reason)
    test.assertIn("observed", result.evidence)
    test.assertIn("threshold", result.evidence)


def strategy_for(strategy_id: str):
    entry = next(item for item in SAFETY_STRATEGIES if item.strategy_id == strategy_id)
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    features = {
        "cashAvailable": 10_000.0,
        "avoidTrading": False,
        "haltLuldState": "clear",
        "operationalHealth": {"status": "ok", "brokerConnected": True, "dataConnected": True},
    }
    features.update(overrides.get("features", {}))
    for key in overrides.get("remove_features", ()):
        features.pop(key, None)

    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-safety-1",
        snapshot_id="snapshot-safety-1",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=price - 0.01,
        ask_price=price + 0.01,
        spread_bps=overrides.get("spread_bps", 5.0),
        volume=100_000,
        source_cutoff_timestamp=overrides.get("source_cutoff_timestamp", NOW),
        point_in_time=overrides.get("point_in_time", True),
        candles=overrides.get("candles", {"1m": candles(60, price)}),
        vwap=overrides.get("vwap", 100.0),
        moving_averages={"1m": {"ema20": 101.0, "ema50": 100.0}},
        atr=overrides.get("atr", {"1m": 1.0}),
        adx={"1m": 24.0},
        rsi={"1m": 50.0},
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands={"1m": {"upper": 102.0, "middle": 100.0, "lower": 98.0}},
        relative_volume=overrides.get("relative_volume", {"1m": 1.2}),
        spread=overrides.get("spread", {"basisPoints": 5.0, "dollars": 0.02}),
        liquidity=overrides.get("liquidity", {"level": "good", "score": 0.8}),
        session_phase=overrides.get("session_phase", "morning"),
        gap_state={"state": "flat_open", "gapPercent": 0.0},
        qqq_iwm_context={"spyVsQqq": 1.0, "spyVsIwm": 1.0},
        breadth={"averageReturn": 0.001, "positiveShare": 0.56, "componentCount": 500},
        economic_event_state=overrides.get("economic_event_state", {"state": "none", "severity": "none", "minutesToEvent": 60, "active": False}),
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


def safety_cases() -> dict[str, SafetyCase]:
    return {
        "cash_avoid_trading_filter": SafetyCase(
            trigger={"features": {"cashAvailable": 499.0}},
            non_trigger={},
            boundary={"features": {"cashAvailable": 500.0}},
            missing={"remove_features": ("cashAvailable",)},
            trigger_reason="meta_strategy.safety.cash_avoid_trading.blocked",
            pass_reason="meta_strategy.safety.cash_avoid_trading.pass",
            missing_reason="meta_strategy.safety.cash_avoid_trading_filter.missing_data",
            existing_position_action="ALLOW_MANAGE",
        ),
        "missing_critical_data_filter": SafetyCase(
            trigger={"vwap": None},
            non_trigger={},
            boundary={},
            missing={"candles": {}},
            trigger_reason="meta_strategy.safety.missing_critical_data.blocked",
            pass_reason="meta_strategy.safety.missing_critical_data.pass",
            missing_reason="meta_strategy.safety.missing_critical_data.blocked",
            existing_position_action="MONITOR",
        ),
        "stale_market_data_filter": SafetyCase(
            trigger={"source_cutoff_timestamp": NOW - timedelta(seconds=91)},
            non_trigger={},
            boundary={"source_cutoff_timestamp": NOW - timedelta(seconds=90)},
            missing={"source_cutoff_timestamp": None},
            trigger_reason="meta_strategy.safety.stale_market_data.blocked",
            pass_reason="meta_strategy.safety.stale_market_data.pass",
            missing_reason="meta_strategy.safety.stale_market_data_filter.missing_data",
            existing_position_action="MONITOR",
        ),
        "excessive_spread_filter": SafetyCase(
            trigger={"spread": {"basisPoints": 12.01, "dollars": 0.13}},
            non_trigger={},
            boundary={"spread": {"basisPoints": 12.0, "dollars": 0.12}},
            missing={"spread": {}, "spread_bps": None},
            trigger_reason="meta_strategy.safety.excessive_spread.blocked",
            pass_reason="meta_strategy.safety.excessive_spread.pass",
            missing_reason="meta_strategy.safety.excessive_spread_filter.missing_data",
            existing_position_action="REDUCE_ONLY",
        ),
        "insufficient_liquidity_filter": SafetyCase(
            trigger={"liquidity": {"level": "thin", "score": 0.34}},
            non_trigger={},
            boundary={"liquidity": {"level": "fair", "score": 0.35}},
            missing={"liquidity": {}},
            trigger_reason="meta_strategy.safety.insufficient_liquidity.blocked",
            pass_reason="meta_strategy.safety.insufficient_liquidity.pass",
            missing_reason="meta_strategy.safety.insufficient_liquidity_filter.missing_data",
            existing_position_action="REDUCE_ONLY",
        ),
        "extreme_volatility_filter": SafetyCase(
            trigger={"relative_volume": {"1m": 5.01}},
            non_trigger={},
            boundary={"relative_volume": {"1m": 5.0}},
            missing={"atr": {}, "relative_volume": {}},
            trigger_reason="meta_strategy.safety.extreme_volatility.blocked",
            pass_reason="meta_strategy.safety.extreme_volatility.pass",
            missing_reason="meta_strategy.safety.extreme_volatility_filter.missing_data",
            existing_position_action="REDUCE_ONLY",
        ),
        "economic_event_blackout_filter": SafetyCase(
            trigger={"economic_event_state": {"state": "scheduled", "severity": "high", "minutesToEvent": 15, "active": True}},
            non_trigger={},
            boundary={"economic_event_state": {"state": "scheduled", "severity": "high", "minutesToEvent": 15, "active": True}},
            missing={"economic_event_state": {}},
            trigger_reason="meta_strategy.safety.economic_event_blackout.blocked",
            pass_reason="meta_strategy.safety.economic_event_blackout.pass",
            missing_reason="meta_strategy.safety.economic_event_blackout_filter.missing_data",
            existing_position_action="REDUCE_ONLY",
            boundary_blocks=True,
        ),
        "unsupported_session_filter": SafetyCase(
            trigger={"session_phase": "premarket"},
            non_trigger={},
            boundary={"session_phase": "opening"},
            missing={"session_phase": ""},
            trigger_reason="meta_strategy.safety.unsupported_session.blocked",
            pass_reason="meta_strategy.safety.unsupported_session.pass",
            missing_reason="meta_strategy.safety.unsupported_session_filter.missing_data",
            existing_position_action="ALLOW_MANAGE",
        ),
        "halt_luld_filter": SafetyCase(
            trigger={"features": {"haltLuldState": "halted"}},
            non_trigger={},
            boundary={"features": {"haltLuldState": "clear"}},
            missing={"remove_features": ("haltLuldState",)},
            trigger_reason="meta_strategy.safety.halt_luld.blocked",
            pass_reason="meta_strategy.safety.halt_luld.pass",
            missing_reason="meta_strategy.safety.halt_luld_filter.missing_data",
            existing_position_action="EXIT_REQUIRED",
        ),
        "operational_health_filter": SafetyCase(
            trigger={"features": {"operationalHealth": {"status": "degraded", "brokerConnected": True, "dataConnected": True}}},
            non_trigger={},
            boundary={"features": {"operationalHealth": {"status": "ok", "brokerConnected": True, "dataConnected": True}}},
            missing={"remove_features": ("operationalHealth",)},
            trigger_reason="meta_strategy.safety.operational_health.blocked",
            pass_reason="meta_strategy.safety.operational_health.pass",
            missing_reason="meta_strategy.safety.operational_health_filter.missing_data",
            existing_position_action="MONITOR",
        ),
    }


if __name__ == "__main__":
    unittest.main()
