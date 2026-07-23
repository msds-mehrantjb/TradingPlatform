import json
import unittest
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.app.algorithms.regime.context_feeds import build_regime_context_feeds
from backend.app.algorithms.regime.volatility_calibration import (
    INACTIVE_UNTIL_LIVE_PAPER_TRADING,
    build_intraday_volatility_calibration_artifact,
    build_intraday_volatility_context_feed,
)


ET = ZoneInfo("America/New_York")


class IntradayVolatilityCalibrationTest(unittest.TestCase):
    def test_artifact_groups_by_exchange_minute_across_dst(self) -> None:
        candles = _session_candles(date(2026, 1, 6), day_index=0)
        candles.extend(_session_candles(date(2026, 7, 23), day_index=1))

        artifact = build_intraday_volatility_calibration_artifact(
            candles,
            min_sample_size=2,
            atr_period=3,
            realized_volatility_period=3,
            created_at="2026-07-23T00:00:00+00:00",
        )

        self.assertIn("20", artifact["minutes"])
        self.assertEqual(artifact["minutes"]["20"]["minuteOfSession"], 20)
        self.assertEqual(artifact["minutes"]["20"]["sampleSize"], 2)
        self.assertEqual(artifact["activationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertEqual(artifact["unitConvention"]["atrPercent"], "decimal_ratio")

    def test_context_feed_is_inactive_until_live_paper_trading(self) -> None:
        artifact = build_intraday_volatility_calibration_artifact(
            _calibration_history(),
            min_sample_size=4,
            atr_period=3,
            realized_volatility_period=3,
            created_at="2026-07-23T00:00:00+00:00",
        )
        latest = _bar(date(2026, 7, 23), 20, 106.0, 0.44, 145_000)

        inactive = build_intraday_volatility_context_feed(
            artifact,
            latest,
            atr_percent=0.004,
            realized_volatility_value=0.0015,
        )

        self.assertEqual(inactive["calibrationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertIsNone(inactive["atrPercentile"])
        self.assertIsNone(inactive["realizedVolatilityPercentile"])
        self.assertEqual(inactive["source"], "historical_minute_calibration_inactive")

    def test_context_feed_can_be_explicitly_enabled_for_paper_validation(self) -> None:
        artifact = build_intraday_volatility_calibration_artifact(
            _calibration_history(),
            min_sample_size=4,
            atr_period=3,
            realized_volatility_period=3,
            created_at="2026-07-23T00:00:00+00:00",
        )
        latest = _bar(date(2026, 7, 23), 20, 106.0, 0.44, 145_000)

        baseline = build_intraday_volatility_context_feed(
            artifact,
            latest,
            atr_percent=0.004,
            realized_volatility_value=0.0015,
            allow_inactive=True,
        )

        self.assertEqual(baseline["calibrationStatus"], "ready")
        self.assertGreaterEqual(baseline["sampleSize"], 4)
        self.assertIsNotNone(baseline["atrPercentile"])
        self.assertIsNotNone(baseline["realizedVolatilityPercentile"])
        self.assertGreater(baseline["currentRangeVsExpected"], 0)
        self.assertGreater(baseline["currentVolumeVsExpected"], 0)

    def test_artifact_is_json_safe_and_feed_adapter_preserves_inactive_status(self) -> None:
        artifact = build_intraday_volatility_calibration_artifact(
            _calibration_history(),
            min_sample_size=4,
            atr_period=3,
            realized_volatility_period=3,
            created_at="2026-07-23T00:00:00+00:00",
        )
        json.dumps(artifact)

        feed = build_intraday_volatility_context_feed(
            artifact,
            _bar(date(2026, 7, 23), 20, 106.0, 0.44, 145_000),
            atr_percent=0.004,
            realized_volatility_value=0.0015,
        )
        adapted = build_regime_context_feeds({"intradayVolatilityBaseline": feed})

        self.assertEqual(
            adapted["intradayVolatilityBaseline"]["calibrationStatus"],
            INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        )
        self.assertEqual(adapted["intradayVolatilityBaseline"]["artifactId"], artifact["artifactId"])


def _calibration_history() -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    day = date(2026, 7, 13)
    day_index = 0
    while len(rows) < 25 * 30:
        if day.weekday() < 5:
            rows.extend(_session_candles(day, day_index=day_index))
            day_index += 1
        day += timedelta(days=1)
    return rows


def _session_candles(session_day: date, *, day_index: int) -> list[dict[str, float | str]]:
    return [
        _bar(
            session_day,
            minute,
            price=100.0 + (day_index * 0.08) + (minute * 0.03),
            candle_range=0.20 + ((minute % 7) * 0.02) + (day_index * 0.003),
            volume=100_000 + (minute * 1_000) + (day_index * 500),
        )
        for minute in range(30)
    ]


def _bar(
    session_day: date,
    minute_from_open: int,
    price: float,
    candle_range: float,
    volume: float,
) -> dict[str, float | str]:
    local_timestamp = datetime.combine(session_day, time(9, 30), ET) + timedelta(minutes=minute_from_open)
    timestamp = local_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "timestamp": timestamp,
        "open": price - 0.02,
        "high": price + (candle_range / 2),
        "low": price - (candle_range / 2),
        "close": price,
        "volume": volume,
    }

