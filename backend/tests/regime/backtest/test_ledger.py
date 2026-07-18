import unittest

from backend.app.algorithms.regime.backtest.ledger import close_trade


class BacktestLedgerTest(unittest.TestCase):
    def test_close_trade_records_pnl_and_r_multiple(self):
        trade = close_trade(
            {"tradeId": "t1", "side": "Long", "quantity": 10, "entryAt": "a", "entryPrice": 100, "stopPrice": 99, "targetPrice": 102},
            {"timestamp": "b"},
            102,
            "target_hit",
        )

        self.assertEqual(trade["exitAt"], "b")
        self.assertEqual(trade["pnl"], 20)
        self.assertEqual(trade["rMultiple"], 2.0)

