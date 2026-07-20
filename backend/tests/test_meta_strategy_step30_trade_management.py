from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_EXIT_POLICY_VERSION,
    MetaStrategyExitCandle,
    MetaStrategyExitInputs,
    apply_meta_strategy_partial_exit,
    evaluate_meta_strategy_exit,
    manage_meta_strategy_trade,
    open_meta_strategy_position,
    reconcile_meta_strategy_fill,
    reconcile_meta_strategy_position,
    tighten_meta_strategy_stop,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def position(side: str = "BUY", *, quantity: int = 10):
    if side == "BUY":
        return open_meta_strategy_position(
            position_id="meta-pos-1",
            symbol="SPY",
            side="BUY",
            quantity=quantity,
            entry_price=100.0,
            opened_at=NOW,
            protective_stop=98.0,
            profit_target=104.0,
            maximum_holding_minutes=30,
        )
    return open_meta_strategy_position(
        position_id="meta-pos-1",
        symbol="SPY",
        side="SELL",
        quantity=quantity,
        entry_price=100.0,
        opened_at=NOW,
        protective_stop=102.0,
        profit_target=96.0,
        maximum_holding_minutes=30,
    )


def candle(minutes: int, *, open: float, high: float, low: float, close: float) -> MetaStrategyExitCandle:
    return MetaStrategyExitCandle(timestamp=NOW + timedelta(minutes=minutes), open=open, high=high, low=low, close=close)


class MetaStrategyStep30TradeManagementTest(unittest.TestCase):
    def test_ml_cannot_widen_or_remove_protective_stops(self) -> None:
        long_stop, long_reasons = tighten_meta_strategy_stop(side="BUY", current_stop=98.0, proposed_stop=97.0)
        short_stop, short_reasons = tighten_meta_strategy_stop(side="SELL", current_stop=102.0, proposed_stop=103.0)
        removed_stop, removed_reasons = tighten_meta_strategy_stop(side="BUY", current_stop=98.0, proposed_stop=None)
        tightened_stop, tightened_reasons = tighten_meta_strategy_stop(side="BUY", current_stop=98.0, proposed_stop=99.0)

        self.assertEqual(long_stop, 98.0)
        self.assertEqual(short_stop, 102.0)
        self.assertEqual(removed_stop, 98.0)
        self.assertEqual(tightened_stop, 99.0)
        self.assertIn("meta_strategy.exit.stop_widening_rejected", long_reasons)
        self.assertIn("meta_strategy.exit.stop_widening_rejected", short_reasons)
        self.assertIn("meta_strategy.exit.stop_removal_rejected", removed_reasons)
        self.assertIn("meta_strategy.exit.stop_tightened_or_unchanged", tightened_reasons)

    def test_long_and_short_protective_stop_and_profit_target_exits(self) -> None:
        cases = (
            (position("BUY"), candle(1, open=100.0, high=101.0, low=97.5, close=98.0), "PROTECTIVE_STOP", 98.0),
            (position("BUY"), candle(1, open=100.0, high=104.5, low=99.5, close=104.0), "PROFIT_TARGET", 104.0),
            (position("SELL"), candle(1, open=100.0, high=102.5, low=99.0, close=102.0), "PROTECTIVE_STOP", 102.0),
            (position("SELL"), candle(1, open=100.0, high=100.5, low=95.5, close=96.0), "PROFIT_TARGET", 96.0),
        )

        for pos, bar, expected_reason, expected_price in cases:
            with self.subTest(side=pos.side, reason=expected_reason):
                decision = evaluate_meta_strategy_exit(MetaStrategyExitInputs(position=pos, candle=bar))

                self.assertEqual(decision.action, "EXIT")
                self.assertEqual(decision.exit_reason, expected_reason)
                self.assertEqual(decision.exit_quantity, pos.remaining_quantity)
                self.assertEqual(decision.exit_price, expected_price)
                self.assertEqual(decision.updated_position.remaining_quantity, 0)

    def test_gap_through_stop_uses_open_price_for_long_and_short(self) -> None:
        long_decision = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(position=position("BUY"), candle=candle(1, open=95.0, high=99.0, low=94.0, close=96.0))
        )
        short_decision = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(position=position("SELL"), candle=candle(1, open=105.0, high=106.0, low=101.0, close=104.0))
        )

        self.assertTrue(long_decision.gap_through_stop)
        self.assertTrue(short_decision.gap_through_stop)
        self.assertEqual(long_decision.exit_price, 95.0)
        self.assertEqual(short_decision.exit_price, 105.0)
        self.assertIn("meta_strategy.exit.gap_through_stop", long_decision.reason_codes)
        self.assertIn("meta_strategy.exit.gap_through_stop", short_decision.reason_codes)

    def test_ml_cannot_delay_required_exits(self) -> None:
        stale = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(
                position=position("BUY"),
                candle=candle(31, open=100.0, high=100.5, low=99.5, close=100.0),
                ml_delay_requested=True,
            )
        )
        session = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(
                position=position("BUY"),
                candle=candle(5, open=100.0, high=100.5, low=99.5, close=100.0),
                session_end_exit=True,
                ml_delay_requested=True,
            )
        )

        self.assertEqual(stale.exit_reason, "MAXIMUM_HOLD")
        self.assertEqual(session.exit_reason, "SESSION_END")
        self.assertFalse(stale.ml_delay_applied)
        self.assertFalse(session.ml_delay_applied)
        self.assertIn("meta_strategy.exit.ml_delay_rejected", stale.reason_codes)
        self.assertIn("meta_strategy.exit.ml_delay_rejected", session.reason_codes)

    def test_event_liquidity_and_global_emergency_exits_are_deterministic(self) -> None:
        event = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(position=position("BUY"), candle=candle(1, open=100.0, high=101.0, low=99.0, close=100.5), event_risk_exit=True)
        )
        liquidity = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(position=position("BUY"), candle=candle(1, open=100.0, high=101.0, low=99.0, close=100.5), liquidity_emergency_exit=True)
        )
        global_exit = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(position=position("BUY"), candle=candle(1, open=100.0, high=101.0, low=99.0, close=100.5), global_emergency_exit=True)
        )

        self.assertEqual(event.exit_reason, "EVENT_RISK")
        self.assertEqual(liquidity.exit_reason, "LIQUIDITY_EMERGENCY")
        self.assertEqual(global_exit.exit_reason, "GLOBAL_EMERGENCY")
        self.assertIn("meta_strategy.exit.event_risk_exit", event.reason_codes)
        self.assertIn("meta_strategy.exit.liquidity_emergency_exit", liquidity.reason_codes)
        self.assertIn("meta_strategy.exit.global_emergency_exit", global_exit.reason_codes)

    def test_partial_fill_and_partial_exit_update_protective_quantities(self) -> None:
        fill = reconcile_meta_strategy_fill(
            planned_quantity=100,
            filled_quantity=25,
            position_id="meta-pos-partial",
            symbol="SPY",
            side="BUY",
            average_fill_price=100.0,
            filled_at=NOW,
            protective_stop=98.0,
            profit_target=106.0,
            maximum_holding_minutes=30,
        )
        self.assertEqual(fill.status, "PARTIAL")
        self.assertEqual(fill.protective_order_quantity, 25)
        self.assertIn("meta_strategy.trade.partial_fill_tracked", fill.reason_codes)

        decision = evaluate_meta_strategy_exit(
            MetaStrategyExitInputs(
                position=fill.position,
                candle=candle(1, open=100.0, high=102.5, low=99.5, close=102.0),
                partial_exit_fraction=0.40,
            )
        )
        self.assertEqual(decision.action, "PARTIAL_EXIT")
        self.assertEqual(decision.exit_quantity, 10)
        self.assertEqual(decision.updated_position.remaining_quantity, 15)
        self.assertTrue(decision.updated_position.partial_exit_taken)

        after_manual_partial = apply_meta_strategy_partial_exit(fill.position, exit_quantity=5)
        self.assertEqual(after_manual_partial.remaining_quantity, 20)
        self.assertEqual(after_manual_partial.protective_order_quantity, 20)

    def test_position_reconciliation_and_management_wrapper(self) -> None:
        pos = position("BUY", quantity=10)
        reconciled = reconcile_meta_strategy_position(pos, broker_quantity=6)
        managed = manage_meta_strategy_trade(
            MetaStrategyExitInputs(
                position=reconciled.reconciled_position,
                candle=candle(1, open=100.0, high=100.5, low=99.5, close=100.0),
            )
        )

        self.assertEqual(reconciled.reconciled_position.remaining_quantity, 6)
        self.assertEqual(reconciled.reconciled_position.protective_order_quantity, 6)
        self.assertEqual(reconciled.discrepancy, -4)
        self.assertIn("meta_strategy.trade.position_quantity_reconciled", reconciled.reason_codes)
        self.assertEqual(managed.exit_policy_version, META_STRATEGY_EXIT_POLICY_VERSION)
        self.assertEqual(managed.exit_decision.action, "HOLD")
        self.assertIn("meta_strategy.trade_management.evaluated", managed.reason_codes)


if __name__ == "__main__":
    unittest.main()
