import unittest
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.decision.test_phase11_sizing_local_risk import _account, _decision, _inventory, _settings, _snapshot

class LiquidityCapTest(unittest.TestCase):
    def test_low_volume_caps_quantity(self):
        settings = {**_settings(), "maxParticipationPercent": 0.001}
        sizing = calculate_regime_position_size(
            _decision(settings),
            _snapshot(volume=100, expected_fill_quantity=500),
            _account(),
            _inventory(),
            {"entryCount": 0, "dailyLossPercent": 0.0},
        )
        self.assertTrue(any(cap["label"] == "regime_liquidity_participation_cap" for cap in sizing.quantity_caps))
