from __future__ import annotations

import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
