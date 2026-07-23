from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline


FRESH_CONTEXT = {
    "quoteFreshness": {
        "status": "fresh",
        "ageMs": 1000,
        "bid": 99.99,
        "ask": 100.01,
        "tradeCount": 100,
        "expectedFillQuantity": 100,
    },
    "scheduledEconomicEvent": {"state": "none"},
}


def candles(closes: list[float], *, start: datetime, width: float = 0.08) -> list[dict]:
    rows = []
    for index, close in enumerate(closes):
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "timestamp": timestamp,
                "open": close - 0.02,
                "high": close + width,
                "low": close - width,
                "close": close,
                "volume": 120000 + index,
                "vwap": 100.0,
            }
        )
    return rows


def run_pattern(primary_candles: list[dict], context: dict | None = None) -> dict:
    timestamps = [row["timestamp"] for row in primary_candles]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise AssertionError("Golden Regime fixtures must use strictly increasing unique timestamps.")
    return execute_regime_pipeline(
        {
            "marketData": {
                "symbol": "SPY",
                "primaryCandles": primary_candles,
                "contextFeeds": context or FRESH_CONTEXT,
            }
        }
    )["decision"]


class GoldenRegimePatternTest(unittest.TestCase):
    def test_known_strong_trend_classifies_as_strong_trend(self):
        decision = run_pattern(candles([100 + index * 0.12 for index in range(70)], start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC)))
        classification = decision["raw_classification"]

        self.assertEqual(classification["raw_regime"], "strong_uptrend")
        self.assertEqual(classification["axes"]["direction"], "strong_up")

    def test_low_volatility_range_classifies_as_range_or_quiet(self):
        context = {
            **FRESH_CONTEXT,
            "intradayVolatilityBaseline": {
                "calibrationStatus": "ready",
                "atrPercentile": 0.20,
                "realizedVolatilityPercentile": 0.20,
                "currentRangeVsExpected": 0.70,
                "sampleSize": 80,
            },
        }
        decision = run_pattern(
            candles([100 + (0.03 if index % 2 == 0 else -0.03) for index in range(70)], start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC)),
            context,
        )
        classification = decision["raw_classification"]

        self.assertEqual(classification["axes"]["volatility"], "compressed")
        self.assertIn(classification["axes"]["structure"], {"range", "mixed"})
        self.assertIn(classification["raw_regime"], {"low_volatility_quiet", "range_bound", "choppy_mixed"})

    def test_compression_then_expansion_classifies_as_intraday_expansion(self):
        context = {
            **FRESH_CONTEXT,
            "intradayVolatilityBaseline": {
                "calibrationStatus": "ready",
                "atrPercentile": 0.80,
                "realizedVolatilityPercentile": 0.80,
                "currentRangeVsExpected": 2.00,
                "sampleSize": 80,
            },
        }
        closes = [100 + (0.01 if index % 2 == 0 else -0.01) for index in range(65)] + [100.2, 100.4, 100.7, 101.0, 101.4]
        decision = run_pattern(candles(closes, start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC)), context)

        self.assertEqual(decision["raw_classification"]["raw_regime"], "intraday_expansion")
        self.assertEqual(decision["raw_classification"]["axes"]["volatility"], "expanded")

    def test_opening_break_classifies_as_opening_breakout(self):
        rows = candles([100 + index * 0.01 for index in range(20)], start=datetime(2026, 7, 23, 13, 30, tzinfo=UTC))
        rows[-1] = {**rows[-1], "open": 100.15, "high": 101.30, "low": 100.10, "close": 101.20}
        context = {**FRESH_CONTEXT, "marketStructureLevels": {"openingRangeHigh": 101.0, "openingRangeLow": 99.5}}

        decision = run_pattern(rows, context)

        self.assertEqual(decision["raw_classification"]["raw_regime"], "opening_breakout")
        self.assertEqual(decision["raw_classification"]["axes"]["session"], "opening")
        self.assertEqual(decision["raw_classification"]["axes"]["structure"], "opening_range_breakout")

    def test_failed_break_classifies_as_failed_breakout_reversal(self):
        rows = candles([100 + index * 0.01 for index in range(40)], start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC))
        rows[-1] = {**rows[-1], "open": 101.08, "high": 101.42, "low": 100.72, "close": 100.86}
        context = {**FRESH_CONTEXT, "marketStructureLevels": {"priorDayHigh": 101.0}}

        decision = run_pattern(rows, context)

        self.assertEqual(decision["raw_classification"]["raw_regime"], "failed_breakout_reversal")
        self.assertIn(decision["raw_classification"]["axes"]["structure"], {"failed_breakout", "liquidity_sweep", "reversal"})

    def test_stale_quote_classifies_as_liquidity_no_entry(self):
        context = {
            "quoteFreshness": {"status": "stale", "ageMs": 30000},
            "scheduledEconomicEvent": {"state": "none"},
        }

        decision = run_pattern(candles([100 + index * 0.12 for index in range(70)], start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC)), context)

        self.assertEqual(decision["raw_classification"]["raw_regime"], "liquidity_stress")
        self.assertEqual(decision["raw_classification"]["axes"]["liquidity"], "unknown")
        self.assertIn("regime.local_gate.no_entry_regime:liquidity_stress", decision["trade_blockers"])

    def test_event_blackout_classifies_as_event_risk_no_entry(self):
        context = {**FRESH_CONTEXT, "scheduledEconomicEvent": {"state": "blackout"}}

        decision = run_pattern(candles([100 + index * 0.12 for index in range(70)], start=datetime(2026, 7, 23, 16, 0, tzinfo=UTC)), context)

        self.assertEqual(decision["raw_classification"]["raw_regime"], "event_risk")
        self.assertEqual(decision["raw_classification"]["axes"]["event_risk"], "blackout")
        self.assertIn("regime.local_gate.no_entry_regime:event_risk", decision["trade_blockers"])

    def test_dst_summer_and_winter_sessions_are_correct(self):
        summer = run_pattern(candles([100 + index * 0.01 for index in range(20)], start=datetime(2026, 7, 23, 13, 30, tzinfo=UTC)))
        winter = run_pattern(candles([100 + index * 0.01 for index in range(20)], start=datetime(2026, 1, 6, 14, 30, tzinfo=UTC)))

        self.assertEqual(summer["raw_classification"]["axes"]["session"], "opening")
        self.assertEqual(summer["raw_classification"]["evidence"]["sessionEvidence"]["sessionDate"], "2026-07-23")
        self.assertEqual(winter["raw_classification"]["axes"]["session"], "opening")
        self.assertEqual(winter["raw_classification"]["evidence"]["sessionEvidence"]["sessionDate"], "2026-01-06")


if __name__ == "__main__":
    unittest.main()
