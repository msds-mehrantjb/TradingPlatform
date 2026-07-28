import unittest
from datetime import UTC, datetime

from backend.app.alpaca import normalize_quote


class AlpacaQuoteTest(unittest.TestCase):
    def test_normalize_quote_returns_voting_ensemble_nbbo_contract(self) -> None:
        quote = normalize_quote(
            provider="alpaca",
            feed="iex",
            symbol="SPY",
            quote={
                "t": "2026-01-05T14:31:00Z",
                "bp": 470.01,
                "ap": 470.03,
                "bs": 400,
                "as": 500,
            },
            received_at=datetime(2026, 1, 5, 14, 31, 1, tzinfo=UTC),
        )

        self.assertEqual(quote["source"], "alpaca_latest_quote")
        self.assertEqual(quote["bid"], 470.01)
        self.assertEqual(quote["ask"], 470.03)
        self.assertEqual(quote["bidSize"], 400)
        self.assertEqual(quote["askSize"], 500)
        self.assertEqual(quote["quoteTimestamp"], "2026-01-05T14:31:00.000000Z")
        self.assertEqual(quote["lastTradeTimestamp"], "2026-01-05T14:31:00.000000Z")
        self.assertEqual(quote["marketDataReceiptTimestamp"], "2026-01-05T14:31:01.000000Z")


if __name__ == "__main__":
    unittest.main()
