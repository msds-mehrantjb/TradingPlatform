from __future__ import annotations

import json
import unittest

from unittest.mock import patch

from backend.app.algorithms.voting_ensemble import backtest_config as backtest_config_module
from backend.app.algorithms.voting_ensemble.backtest_config import backtest_config_from_live_settings
from backend.app.algorithms.voting_ensemble.intelligence_capture import CAPTURE_TABLES, VOTING_ENSEMBLE_OPERATIONAL_EVENT_TYPES
from backend.app.algorithms.voting_ensemble.trading_settings import resolve_one_minute_trading_settings
from backend.app.algorithms.voting_ensemble.trading_settings.models import HoldingTimePolicySettings


class InertControlsTest(unittest.TestCase):
    def test_signal_fade_exit_is_gone_from_the_holding_time_policy(self) -> None:
        self.assertNotIn("signalFadeExit", HoldingTimePolicySettings.model_fields)
        resolved = resolve_one_minute_trading_settings({})
        self.assertNotIn("signalFadeExit", json.dumps(resolved.model_dump(mode="json")))

    def test_a_payload_carrying_the_removed_field_is_ignored(self) -> None:
        plain = resolve_one_minute_trading_settings({})
        carrying = resolve_one_minute_trading_settings({"signalFadeExit": "active"})
        self.assertEqual(plain.configurationHash, carrying.configurationHash)

    def test_no_capture_type_is_declared_without_a_producer(self) -> None:
        # A shadow_decision capture table was declared and never written to; refused
        # shorts go to the algorithm's own snapshot store instead.
        self.assertNotIn("shadow_decision", VOTING_ENSEMBLE_OPERATIONAL_EVENT_TYPES)
        self.assertNotIn("shadow_decision", CAPTURE_TABLES)


class ReplayFollowsTheLiveShortSettingTest(unittest.TestCase):
    """The replay configuration derives from the live resolution so the two cannot drift."""

    def test_short_entries_track_the_runtime_flag(self) -> None:
        with patch.object(backtest_config_module, "_live_short_entries_enabled", return_value=False):
            self.assertFalse(backtest_config_from_live_settings().allowShortEntries)
        with patch.object(backtest_config_module, "_live_short_entries_enabled", return_value=True):
            self.assertTrue(backtest_config_from_live_settings().allowShortEntries)

    def test_shorts_are_off_by_default_and_an_override_still_wins(self) -> None:
        self.assertFalse(backtest_config_from_live_settings().allowShortEntries)
        self.assertTrue(backtest_config_from_live_settings(allowShortEntries=True).allowShortEntries)


if __name__ == "__main__":
    unittest.main()
