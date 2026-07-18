import unittest
from backend.app.algorithms.regime.classifier import _no_trade_reasons
from backend.tests.regime.fixtures.market_snapshots import snapshot

class NoTradeReasonsTest(unittest.TestCase):
    def test_no_trade_reasons_for_stale_event_and_halt(self):
        market = snapshot("up", context={
            "quoteFreshness": {"status": "stale"},
            "scheduledEconomicEvent": {"state": "blackout"},
            "haltLuldCircuitBreaker": {"newEntriesBlocked": True},
        })
        reasons = _no_trade_reasons(market, 0.04, 0.4)
        self.assertIn("regime.safety.stale_quote", reasons)
        self.assertIn("regime.safety.event_blackout", reasons)
        self.assertIn("regime.safety.halt_luld_circuit", reasons)
        self.assertIn("regime.safety.extreme_volatility", reasons)
        self.assertIn("regime.safety.insufficient_liquidity", reasons)

