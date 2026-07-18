from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.dynamic_settings import default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.risk_budget import (
    WEIGHTED_VOTING_RISK_BUDGET_NAMESPACE,
    WeightedVotingOpenPositionRisk,
    WeightedVotingRiskBudget,
    build_weighted_voting_risk_budget,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingRiskBudgetTest(unittest.TestCase):
    def test_budget_tracks_weighted_voting_risk_inventory(self) -> None:
        positions = (
            WeightedVotingOpenPositionRisk(symbol="SPY", quantity=100, side="Buy", entry_price=100.0, stop_price=98.0, current_price=99.0),
            WeightedVotingOpenPositionRisk(symbol="QQQ", quantity=50, side="Sell", entry_price=200.0, stop_price=204.0, current_price=202.0),
        )
        budget = WeightedVotingRiskBudget(
            account_equity=100000.0,
            risk_percent=3.0,
            data_timestamp=TS,
            open_positions=positions,
            realized_pnl=-150.0,
            pending_trade_risk=500.0,
            max_simultaneous_positions=4,
            capital_partition_percent=30.0,
        )

        self.assertEqual(budget.daily_risk_allowance, 3000.0)
        self.assertEqual(budget.risk_used_by_open_positions, 400.0)
        self.assertEqual(budget.realized_weighted_voting_loss, 150.0)
        self.assertEqual(budget.unrealized_weighted_voting_loss, 200.0)
        self.assertEqual(budget.remaining_daily_risk, 2250.0)
        self.assertEqual(budget.trade_level_risk, 500.0)
        self.assertEqual(budget.symbol_level_risk, {"SPY": 200.0, "QQQ": 200.0})
        self.assertEqual(budget.capital_partition, 30000.0)
        self.assertEqual(budget.remaining_capital_partition, 10000.0)
        self.assertFalse(budget.daily_shutdown_state)
        self.assertEqual(budget.risk_dollars, 500.0)

    def test_global_risk_cap_can_reduce_but_never_increase_local_budget(self) -> None:
        local = WeightedVotingRiskBudget(account_equity=100000.0, risk_percent=1.0, data_timestamp=TS)

        raised_by_global = local.with_global_cap(5000.0)
        reduced_by_global = local.with_global_cap(250.0)

        self.assertEqual(local.daily_risk_allowance, 1000.0)
        self.assertEqual(raised_by_global.daily_risk_allowance, 1000.0)
        self.assertEqual(reduced_by_global.daily_risk_allowance, 250.0)
        self.assertEqual(reduced_by_global.risk_dollars, 250.0)

    def test_daily_shutdown_blocks_new_risk(self) -> None:
        budget = WeightedVotingRiskBudget(
            account_equity=100000.0,
            risk_percent=1.0,
            data_timestamp=TS,
            realized_pnl=-1000.0,
        )

        self.assertTrue(budget.daily_shutdown_state)
        self.assertEqual(budget.remaining_daily_risk, 0.0)
        self.assertEqual(budget.risk_dollars, 0.0)

    def test_builder_uses_weighted_voting_effective_settings_and_namespace(self) -> None:
        defaults = default_weighted_settings(timestamp=TS)
        effective = resolve_effective_settings(default_settings=defaults, timestamp=TS)

        budget = build_weighted_voting_risk_budget(
            account_equity=100000.0,
            effective_settings=effective,
            global_daily_risk_cap=2000.0,
            timestamp=TS,
        )
        payload = budget.as_dict()

        self.assertEqual(payload["algorithmId"], "weighted_voting")
        self.assertEqual(payload["namespace"], WEIGHTED_VOTING_RISK_BUDGET_NAMESPACE)
        self.assertEqual(payload["dailyRiskAllowance"], 2000.0)
        self.assertEqual(payload["remainingDailyRisk"], 2000.0)
        self.assertEqual(payload["maximumSimultaneousPositions"], 10)
        self.assertEqual(payload["capitalPartition"], 30000.0)
        self.assertIn("weighted_voting.risk_budget.built", payload["reasonCodes"])

    def test_foreign_position_risk_is_rejected(self) -> None:
        position = WeightedVotingOpenPositionRisk(
            symbol="SPY",
            quantity=10,
            side="Buy",
            entry_price=100.0,
            stop_price=99.0,
            current_price=100.0,
            algorithm_id="wca",  # type: ignore[arg-type]
        )

        with self.assertRaises(ValueError):
            WeightedVotingRiskBudget(account_equity=100000.0, risk_percent=1.0, open_positions=(position,))


if __name__ == "__main__":
    unittest.main()
