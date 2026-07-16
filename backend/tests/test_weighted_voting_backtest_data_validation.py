from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.backtest.data_validation import (
    WeightedBacktestQuote,
    validate_historical_data,
)
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle


NEW_YORK = timezone(timedelta(hours=-5), "America/New_York")
SESSION_DATE = date(2026, 1, 5)
CREATED_AT = datetime(2026, 1, 6, 12, 0, tzinfo=timezone.utc)


class WeightedVotingBacktestDataValidationTest(unittest.TestCase):
    def test_valid_data_creates_reproducible_manifest(self) -> None:
        candles = make_session_candles()
        quotes = make_quotes(candles)

        first = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": candles},
            source="unit-test",
            created_at=CREATED_AT,
            quotes=quotes,
        )
        second = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": candles},
            source="unit-test",
            created_at=CREATED_AT,
            quotes=quotes,
        )

        self.assertTrue(first.valid)
        self.assertFalse(first.blocks_run)
        self.assertEqual(first.manifest.symbol, "SPY")
        self.assertEqual(first.manifest.timeframes, ("1m",))
        self.assertEqual(first.manifest.row_counts, {"1m": 390})
        self.assertEqual(first.manifest.missing_bar_counts, {"1m": 0})
        self.assertEqual(first.manifest.fill_policy, "none")
        self.assertEqual(first.manifest.data_hash, second.manifest.data_hash)
        self.assertEqual(first.manifest.manifest_hash, second.manifest.manifest_hash)
        self.assertEqual(first.manifest.deterministic_json(), second.manifest.deterministic_json())

    def test_corrupted_data_blocks_run(self) -> None:
        candles = (
            valid_candle(minutes_after_open=2),
            invalid_candle(minutes_after_open=1),
            valid_candle(minutes_after_open=1),
        )

        result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": candles},
            source="unit-test",
            created_at=CREATED_AT,
        )

        self.assertFalse(result.valid)
        self.assertTrue(result.blocks_run)
        self.assertIn("weighted_voting.backtest.1m.timestamp_order_invalid", result.errors)
        self.assertIn("weighted_voting.backtest.1m.duplicate_timestamps", result.errors)
        self.assertIn("weighted_voting.backtest.1m.invalid_ohlcv", result.errors)

    def test_missing_bars_warn_but_partial_sessions_block(self) -> None:
        warning_result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": make_session_candles(count=380)},
            source="unit-test",
            created_at=CREATED_AT,
        )
        blocking_result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": make_session_candles(count=100)},
            source="unit-test",
            created_at=CREATED_AT,
        )

        self.assertTrue(warning_result.valid)
        self.assertFalse(warning_result.blocks_run)
        self.assertEqual(warning_result.manifest.missing_bar_counts, {"1m": 10})
        self.assertTrue(
            any(warning.startswith("weighted_voting.backtest.1m.missing_regular_session_bars") for warning in warning_result.warnings)
        )
        self.assertFalse(blocking_result.valid)
        self.assertTrue(blocking_result.blocks_run)
        self.assertTrue(any(error.startswith("weighted_voting.backtest.1m.partial_session") for error in blocking_result.errors))

    def test_market_holiday_data_warns_without_hiding_the_run_manifest(self) -> None:
        result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": make_session_candles()},
            source="unit-test",
            created_at=CREATED_AT,
            market_holidays=(SESSION_DATE,),
        )

        self.assertTrue(result.valid)
        self.assertFalse(result.blocks_run)
        self.assertIn("weighted_voting.backtest.1m.holiday_data_present.2026-01-05", result.warnings)
        self.assertIn("weighted_voting.backtest.1m.holiday_data_present.2026-01-05", result.manifest.validation_warnings)

    def test_corporate_action_consistency_is_reported(self) -> None:
        split_like_candles = (
            valid_candle(price=100.0),
            valid_candle(minutes_after_open=1, price=50.0),
            *(valid_candle(minutes_after_open=index, price=50.0 + index * 0.01) for index in range(2, 390)),
        )
        unrecorded = validate_historical_data(
            symbol="SPLT",
            candles_by_timeframe={"1m": split_like_candles},
            source="unit-test",
            created_at=CREATED_AT,
        )
        inconsistent = validate_historical_data(
            symbol="SPLT",
            candles_by_timeframe={"1m": split_like_candles},
            source="unit-test",
            created_at=CREATED_AT,
            expected_split_adjustments={SESSION_DATE: 0.25},
        )

        self.assertTrue(unrecorded.valid)
        self.assertTrue(any(warning.startswith("weighted_voting.backtest.1m.possible_unrecorded_corporate_action") for warning in unrecorded.warnings))
        self.assertFalse(inconsistent.valid)
        self.assertTrue(any(error.startswith("weighted_voting.backtest.1m.corporate_action_inconsistent") for error in inconsistent.errors))

    def test_quote_freshness_warning_uses_actual_quote_history(self) -> None:
        candles = make_session_candles()
        stale_quote = WeightedBacktestQuote(timestamp=candles[0].timestamp - timedelta(minutes=5), bid=99.99, ask=100.01)

        result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": candles},
            source="unit-test",
            created_at=CREATED_AT,
            quotes=(stale_quote,),
            quote_freshness_seconds=30,
        )

        self.assertTrue(result.valid)
        self.assertTrue(any(warning.startswith("weighted_voting.backtest.quotes.stale_or_missing") for warning in result.warnings))

    def test_silent_fill_policy_blocks_run_and_is_recorded(self) -> None:
        result = validate_historical_data(
            symbol="SPY",
            candles_by_timeframe={"1m": make_session_candles()},
            source="unit-test",
            created_at=CREATED_AT,
            fill_policy="silent",
        )

        self.assertFalse(result.valid)
        self.assertTrue(result.blocks_run)
        self.assertIn("weighted_voting.backtest.silent_fill_policy_blocked", result.errors)
        self.assertEqual(result.manifest.fill_policy, "silent")


def make_session_candles(count: int = 390) -> tuple[WeightedVotingCandle, ...]:
    return tuple(valid_candle(minutes_after_open=index, price=100.0 + index * 0.01) for index in range(count))


def make_quotes(candles: tuple[WeightedVotingCandle, ...]) -> tuple[WeightedBacktestQuote, ...]:
    return tuple(WeightedBacktestQuote(timestamp=candle.timestamp, bid=candle.close - 0.01, ask=candle.close + 0.01) for candle in candles)


def valid_candle(minutes_after_open: int = 0, price: float = 100.0) -> WeightedVotingCandle:
    timestamp = datetime.combine(SESSION_DATE, datetime.min.time(), tzinfo=NEW_YORK).replace(hour=9, minute=30)
    close = price + 0.01
    return WeightedVotingCandle(
        timestamp=(timestamp + timedelta(minutes=minutes_after_open)).astimezone(timezone.utc),
        open=price,
        high=close + 0.05,
        low=price - 0.05,
        close=close,
        volume=1_000,
    )


def invalid_candle(minutes_after_open: int = 0) -> WeightedVotingCandle:
    timestamp = datetime.combine(SESSION_DATE, datetime.min.time(), tzinfo=NEW_YORK).replace(hour=9, minute=30)
    return WeightedVotingCandle.model_construct(
        timestamp=(timestamp + timedelta(minutes=minutes_after_open)).astimezone(timezone.utc),
        open=100.0,
        high=99.0,
        low=101.0,
        close=100.0,
        volume=-1.0,
    )
