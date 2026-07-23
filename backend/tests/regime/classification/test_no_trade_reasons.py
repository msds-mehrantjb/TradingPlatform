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
        reasons = _no_trade_reasons(
            market,
            {"atrPercentile": 0.98, "realizedVolatilityPercentile": 0.97, "currentRangeVsExpected": 1.2},
            0.4,
        )
        self.assertIn("regime.safety.stale_quote", reasons)
        self.assertIn("regime.safety.liquidity_fail_closed", reasons)
        self.assertIn("regime.safety.event_blackout", reasons)
        self.assertIn("regime.safety.halt_luld_circuit", reasons)
        self.assertIn("regime.safety.extreme_volatility", reasons)

    def test_missing_critical_context_feeds_block_new_entries(self):
        market = snapshot("up", context={
            "quoteFreshness": {"status": "unknown"},
            "scheduledEconomicEvent": {"state": "unknown"},
        })
        reasons = _no_trade_reasons(
            market,
            {"atrPercentile": 0.40, "realizedVolatilityPercentile": 0.45, "currentRangeVsExpected": 1.0},
            1.0,
        )

        self.assertIn("regime.safety.missing_quote_freshness", reasons)
        self.assertIn("regime.safety.missing_liquidity_quote_fields", reasons)
        self.assertIn("regime.safety.liquidity_fail_closed", reasons)
        self.assertIn("regime.safety.missing_event_state", reasons)
