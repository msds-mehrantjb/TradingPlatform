from __future__ import annotations

import inspect

from backend.app.algorithms.regime.router import route_regime_strategies
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, REGIME_STRATEGY_ALIASES, evaluate_strategy
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.fixtures.market_snapshots import classified_snapshot, frozen_repr, snapshot


def assert_directional_strategy_contract(testcase, strategy_id: str) -> None:
    up_market, up_classification = classified_snapshot("up")
    down_market, down_classification = classified_snapshot("down")
    flat_market, flat_classification = classified_snapshot("flat")
    before = frozen_repr(up_market)
    buy_or_sell = evaluate_strategy(strategy_id, up_market, up_classification)
    opposite = evaluate_strategy(strategy_id, down_market, down_classification)
    hold = evaluate_strategy(strategy_id, flat_market, flat_classification)
    repeat = evaluate_strategy(strategy_id, up_market, up_classification)
    warmup = evaluate_strategy(strategy_id, snapshot("up", count=1), classification())
    routed = route_regime_strategies(up_market, classification(raw_regime="event_risk", event_risk="blackout"))

    testcase.assertEqual(buy_or_sell.role, "directional")
    testcase.assertIn(buy_or_sell.signal, {"Buy", "Sell", "Hold"})
    testcase.assertIn(opposite.signal, {"Buy", "Sell", "Hold"})
    testcase.assertIn(hold.signal, {"Buy", "Sell", "Hold"})
    testcase.assertGreaterEqual(buy_or_sell.confidence, 0)
    testcase.assertLessEqual(buy_or_sell.confidence, 1)
    testcase.assertIsInstance(buy_or_sell.reason, str)
    testcase.assertIsInstance(buy_or_sell.evidence, dict)
    testcase.assertEqual(repeat, buy_or_sell)
    testcase.assertEqual(before, frozen_repr(up_market))
    testcase.assertFalse(warmup.eligible)
    testcase.assertIn(strategy_id, {item["strategyId"] for item in routed["skippedStrategies"]})
    _assert_no_forbidden_access(testcase, strategy_id)


def assert_alias_contract(testcase, alias: str, canonical: str) -> None:
    market, raw = classified_snapshot("up")
    testcase.assertEqual(REGIME_STRATEGY_ALIASES[alias], canonical)
    testcase.assertEqual(evaluate_strategy(alias, market, raw), evaluate_strategy(canonical, market, raw))


def assert_non_directional_contract(testcase, strategy_id: str, role: str) -> None:
    market, raw = classified_snapshot("up")
    output = evaluate_strategy(strategy_id, market, raw)
    repeat = evaluate_strategy(strategy_id, market, raw)
    testcase.assertEqual(output.role, role)
    testcase.assertEqual(output.signal, "Hold")
    testcase.assertGreaterEqual(output.confidence, 0)
    testcase.assertLessEqual(output.confidence, 1)
    testcase.assertEqual(output, repeat)
    _assert_no_forbidden_access(testcase, strategy_id)


def assert_safety_gate_contract(testcase, strategy_id: str, trigger_classification=None, context=None) -> None:
    clear_market, clear_classification = classified_snapshot("up")
    trigger_market = snapshot("up", context=context) if context else clear_market
    triggered = evaluate_strategy(strategy_id, trigger_market, trigger_classification or clear_classification)
    clear = evaluate_strategy(strategy_id, clear_market, clear_classification)
    testcase.assertEqual(triggered.role, "safety_gate")
    testcase.assertEqual(triggered.signal, "Hold")
    testcase.assertIn(triggered.reason, {"regime.safety.clear", triggered.reason})
    testcase.assertEqual(clear.signal, "Hold")
    testcase.assertGreaterEqual(triggered.confidence, 0)
    testcase.assertLessEqual(triggered.confidence, 1)
    _assert_no_forbidden_access(testcase, strategy_id)


def _assert_no_forbidden_access(testcase, strategy_id: str) -> None:
    definition = next(item for item in REGIME_STRATEGY_DEFINITIONS if item.strategy_id == strategy_id)
    source = inspect.getsource(definition.evaluator)
    forbidden = ("broker", "persistence", "sqlite", "order_intent", "ml.", "wca")
    testcase.assertFalse(any(token in source.lower() for token in forbidden), strategy_id)

