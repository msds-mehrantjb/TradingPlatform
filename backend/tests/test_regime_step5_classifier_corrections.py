from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.classifier import _composite_regime, classify_market_regime
from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES, RegimeAxes
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot


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


GOLDEN_CANONICAL_AXIS_FIXTURES = {
    "strong_uptrend": RegimeAxes("strong_up", "normal", "trend", "good", "midday", "none"),
    "weak_uptrend": RegimeAxes("weak_up", "normal", "trend", "good", "midday", "none"),
    "strong_downtrend": RegimeAxes("strong_down", "normal", "trend", "good", "midday", "none"),
    "weak_downtrend": RegimeAxes("weak_down", "normal", "trend", "good", "midday", "none"),
    "range_bound": RegimeAxes("neutral", "normal", "range", "good", "midday", "none"),
    "sideways_range": RegimeAxes("neutral", "normal", "range", "good", "midday", "none"),
    "choppy_mixed": RegimeAxes("neutral", "normal", "mixed", "good", "midday", "none"),
    "opening_breakout": RegimeAxes("weak_up", "normal", "opening_range_breakout", "good", "opening", "none"),
    "intraday_expansion": RegimeAxes("weak_up", "expanded", "valid_breakout", "good", "midday", "none"),
    "high_volatility_trend": RegimeAxes("strong_up", "expanded", "trend", "good", "midday", "none"),
    "low_volatility_quiet": RegimeAxes("neutral", "compressed", "range", "good", "midday", "none"),
    "failed_breakout_reversal": RegimeAxes("neutral", "normal", "failed_breakout", "good", "midday", "none"),
    "gap_session": RegimeAxes("weak_up", "normal", "trend", "good", "opening", "none"),
    "event_risk": RegimeAxes("strong_up", "normal", "trend", "good", "midday", "blackout"),
    "liquidity_stress": RegimeAxes("strong_up", "normal", "trend", "unknown", "midday", "none"),
    "extreme_volatility_no_trade": RegimeAxes("strong_up", "extreme", "trend", "good", "midday", "none"),
}


class RegimeStep5ClassifierCorrectionsTest(unittest.TestCase):
    def test_golden_axis_fixtures_cover_all_canonical_regimes(self) -> None:
        self.assertEqual(set(GOLDEN_CANONICAL_AXIS_FIXTURES), set(CANONICAL_MARKET_REGIMES))
        for expected, axes in GOLDEN_CANONICAL_AXIS_FIXTURES.items():
            with self.subTest(expected=expected):
                actual = _composite_regime(axes)
                if expected == "sideways_range":
                    self.assertEqual(actual, "range_bound")
                elif expected == "gap_session":
                    self.assertIn(actual, {"weak_uptrend", "opening_breakout"})
                else:
                    self.assertEqual(actual, expected)

    def test_volatility_percent_inputs_are_normalized_and_do_not_create_extreme_unit_error(self) -> None:
        classification = classify_market_regime(
            snapshot(
                trend="up",
                context={
                    **FRESH_CONTEXT,
                    "intradayVolatilityBaseline": {
                        "calibrationStatus": "ready",
                        "atrPercentile": 80,
                        "realizedVolatilityPercentile": 80,
                        "currentRangeVsExpected": 1.6,
                        "currentVolumeVsExpected": 1.1,
                        "sampleSize": 120,
                    },
                },
            )
        )

        evidence = classification.evidence["volatilityEvidence"]
        self.assertEqual(evidence["atrPercentile"], 0.8)
        self.assertEqual(evidence["realizedVolatilityPercentile"], 0.8)
        self.assertEqual(classification.axes.volatility, "expanded")
        self.assertNotEqual(classification.raw_regime, "extreme_volatility_no_trade")

    def test_missing_calibration_uses_conservative_fallback_with_reason_codes(self) -> None:
        classification = classify_market_regime(snapshot(trend="flat", context=FRESH_CONTEXT))
        volatility = classification.evidence["volatilityEvidence"]

        self.assertIn("regime.volatility.calibration_unavailable:missing", volatility["reasonCodes"])
        self.assertTrue(any(code.startswith("regime.volatility.fallback.") for code in volatility["reasonCodes"]))
        self.assertIn(classification.axes.volatility, {"normal", "compressed"})

    def test_outside_session_and_missing_quote_are_fail_closed_critical_inputs(self) -> None:
        classification = classify_market_regime(
            snapshot(
                trend="up",
                start=datetime(2026, 7, 18, 15, 0, tzinfo=UTC),
                context={"quoteFreshness": {"status": "unknown"}, "scheduledEconomicEvent": {"state": "none"}},
            )
        )

        self.assertEqual(classification.axes.session, "outside_regular")
        self.assertIn("sessionStatus", classification.missing_inputs)
        self.assertIn("freshQuote", classification.missing_inputs)
        self.assertIn("bid", classification.missing_inputs)
        self.assertIn("ask", classification.missing_inputs)
        self.assertIn("spreadBps", classification.missing_inputs)
        self.assertIn("regime.safety.market_holiday_or_weekend", classification.no_trade_reasons)
        self.assertIn("regime.safety.missing_quote_freshness", classification.no_trade_reasons)
        self.assertEqual(classification.evidence["confidenceEvidence"]["dataQualityConfidence"], 0.10)


def snapshot(*, trend: str, context: dict, start: datetime | None = None) -> object:
    return build_regime_market_snapshot(
        {
            "symbol": "SPY",
            "primaryCandles": candles(trend=trend, start=start or datetime(2026, 7, 23, 15, 0, tzinfo=UTC)),
            "contextFeeds": context,
        }
    )


def candles(*, trend: str, start: datetime) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    price = 100.0
    for index in range(70):
        if trend == "up":
            price += 0.08
        elif trend == "down":
            price -= 0.08
        else:
            price += 0.01 if index % 2 == 0 else -0.01
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "timestamp": timestamp,
                "open": price - 0.03,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "volume": 120_000 + index,
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
