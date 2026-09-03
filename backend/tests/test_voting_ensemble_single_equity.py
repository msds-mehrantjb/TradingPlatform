from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble.backtest_config import backtest_config_from_live_settings
from backend.app.algorithms.voting_ensemble.local_paper_account import VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH, _configured_initial_cash
from backend.app.algorithms.voting_ensemble.trading_settings.baseline import one_minute_baseline_settings
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings


class SingleStartingEquityTest(unittest.TestCase):
    """Settings, the local paper account and the replay all start from one equity figure.

    The settings baseline said 25,000 while the local paper account opened with 100,000,
    so every recorded size depended on which one a code path read. The account now derives
    its opening cash from the settings baseline; the replay configuration already did.
    """

    def test_the_account_default_is_the_settings_baseline(self) -> None:
        baseline = one_minute_baseline_settings()["startingCapital"]

        self.assertEqual(baseline, 100000.0)
        self.assertEqual(VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH, Decimal("100000.0"))
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH", None)
            self.assertEqual(_configured_initial_cash(), Decimal("100000.0"))

    def test_resolved_settings_and_replay_config_agree_with_the_account(self) -> None:
        resolved = resolve_one_minute_trading_settings(None)
        replay = backtest_config_from_live_settings()

        self.assertEqual(resolved.riskPerTrade.startingCapital, 100000.0)
        self.assertEqual(replay.startingCapital, 100000.0)
        self.assertEqual(Decimal(str(resolved.riskPerTrade.startingCapital)), VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH)

    def test_the_environment_override_is_explicit_not_a_second_default(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "250000"}):
            self.assertEqual(_configured_initial_cash(), Decimal("250000"))
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "   "}):
            self.assertEqual(_configured_initial_cash(), VOTING_ENSEMBLE_DEFAULT_LOCAL_CASH)


if __name__ == "__main__":
    unittest.main()
