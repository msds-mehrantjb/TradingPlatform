"""Backtest data validation for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from math import isfinite

from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle


WEIGHTED_VOTING_BACKTEST_DATA_VALIDATION_VERSION = "weighted_voting_backtest_data_validation_v2"
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EXPECTED_ONE_MINUTE_BARS = 390


@dataclass(frozen=True)
class WeightedBacktestQuote:
    timestamp: datetime
    bid: float
    ask: float


@dataclass(frozen=True)
class WeightedBacktestDataManifest:
    symbol: str
    timeframes: tuple[str, ...]
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    row_counts: dict[str, int]
    missing_bar_counts: dict[str, int]
    source: str
    data_hash: str
    created_at: datetime
    validation_warnings: tuple[str, ...]
    fill_policy: str
    manifest_version: str = "weighted_backtest_data_manifest_v1"

    def deterministic_json(self) -> str:
        payload = {
            "symbol": self.symbol,
            "timeframes": self.timeframes,
            "startTimestamp": self.start_timestamp.isoformat() if self.start_timestamp else None,
            "endTimestamp": self.end_timestamp.isoformat() if self.end_timestamp else None,
            "rowCounts": self.row_counts,
            "missingBarCounts": self.missing_bar_counts,
            "source": self.source,
            "dataHash": self.data_hash,
            "createdAt": self.created_at.isoformat(),
            "validationWarnings": self.validation_warnings,
            "fillPolicy": self.fill_policy,
            "manifestVersion": self.manifest_version,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WeightedBacktestDataValidationResult:
    valid: bool
    blocks_run: bool
    manifest: WeightedBacktestDataManifest
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_candles(candles: tuple[WeightedVotingCandle, ...]) -> tuple[str, ...]:
    if not candles:
        return ("weighted_voting.backtest.no_candles",)
    return ()


def validate_historical_data(
    *,
    symbol: str,
    candles_by_timeframe: dict[str, tuple[WeightedVotingCandle, ...]],
    source: str,
    created_at: datetime,
    quotes: tuple[WeightedBacktestQuote, ...] = (),
    fill_policy: str = "none",
    market_holidays: tuple[date, ...] = (),
    expected_split_adjustments: dict[date, float] | None = None,
    quote_freshness_seconds: int = 60,
) -> WeightedBacktestDataValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if fill_policy == "silent":
        errors.append("weighted_voting.backtest.silent_fill_policy_blocked")

    row_counts = {timeframe: len(candles) for timeframe, candles in sorted(candles_by_timeframe.items())}
    missing_counts: dict[str, int] = {}
    all_candles = tuple(candle for timeframe in sorted(candles_by_timeframe) for candle in candles_by_timeframe[timeframe])
    for timeframe, candles in sorted(candles_by_timeframe.items()):
        _validate_timeframe(timeframe, candles, errors, warnings, market_holidays, expected_split_adjustments or {}, missing_counts)
    if quotes:
        _validate_quotes(quotes, all_candles, errors, warnings, quote_freshness_seconds)
    start_timestamp = min((candle.timestamp for candle in all_candles), default=None)
    end_timestamp = max((candle.timestamp for candle in all_candles), default=None)
    data_hash = _data_hash(symbol, candles_by_timeframe, quotes, source, fill_policy)
    manifest = WeightedBacktestDataManifest(
        symbol=symbol,
        timeframes=tuple(sorted(candles_by_timeframe)),
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        row_counts=row_counts,
        missing_bar_counts=missing_counts,
        source=source,
        data_hash=data_hash,
        created_at=created_at,
        validation_warnings=tuple(warnings),
        fill_policy=fill_policy,
    )
    return WeightedBacktestDataValidationResult(
        valid=not errors,
        blocks_run=bool(errors),
        manifest=manifest,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _validate_timeframe(
    timeframe: str,
    candles: tuple[WeightedVotingCandle, ...],
    errors: list[str],
    warnings: list[str],
    market_holidays: tuple[date, ...],
    expected_split_adjustments: dict[date, float],
    missing_counts: dict[str, int],
) -> None:
    if not candles:
        errors.append(f"weighted_voting.backtest.{timeframe}.no_candles")
        missing_counts[timeframe] = 0
        return
    timestamps = [candle.timestamp for candle in candles]
    if any(timestamp.tzinfo is None for timestamp in timestamps):
        errors.append(f"weighted_voting.backtest.{timeframe}.timezone_missing")
    utc_timestamps = [_to_utc(timestamp) for timestamp in timestamps]
    if utc_timestamps != sorted(utc_timestamps):
        errors.append(f"weighted_voting.backtest.{timeframe}.timestamp_order_invalid")
    if len(set(utc_timestamps)) != len(utc_timestamps):
        errors.append(f"weighted_voting.backtest.{timeframe}.duplicate_timestamps")
    for candle in candles:
        if not _valid_candle(candle):
            errors.append(f"weighted_voting.backtest.{timeframe}.invalid_ohlcv")
            break
    session_counts = _regular_session_counts(candles)
    missing = 0
    for session_date, count in session_counts.items():
        if session_date in market_holidays:
            warnings.append(f"weighted_voting.backtest.{timeframe}.holiday_data_present.{session_date.isoformat()}")
            continue
        expected = EXPECTED_ONE_MINUTE_BARS if timeframe == "1m" else max(1, EXPECTED_ONE_MINUTE_BARS // _timeframe_minutes(timeframe))
        if count < expected:
            missing += expected - count
            if count < expected * 0.80:
                errors.append(f"weighted_voting.backtest.{timeframe}.partial_session.{session_date.isoformat()}")
            else:
                warnings.append(f"weighted_voting.backtest.{timeframe}.missing_regular_session_bars.{session_date.isoformat()}")
    missing_counts[timeframe] = missing
    _validate_corporate_actions(candles, expected_split_adjustments, errors, warnings, timeframe)


def _valid_candle(candle: WeightedVotingCandle) -> bool:
    values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
    if any(not isfinite(float(value)) for value in values):
        return False
    if any(price <= 0 for price in (candle.open, candle.high, candle.low, candle.close)):
        return False
    if candle.volume < 0:
        return False
    if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close) or candle.low > candle.high:
        return False
    return True


def _regular_session_counts(candles: tuple[WeightedVotingCandle, ...]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for candle in candles:
        local = _to_new_york(candle.timestamp)
        if REGULAR_OPEN <= local.time() < REGULAR_CLOSE:
            counts[local.date()] = counts.get(local.date(), 0) + 1
    return counts


def _validate_corporate_actions(
    candles: tuple[WeightedVotingCandle, ...],
    expected_split_adjustments: dict[date, float],
    errors: list[str],
    warnings: list[str],
    timeframe: str,
) -> None:
    for previous, current in zip(candles, candles[1:]):
        if previous.close <= 0:
            continue
        ratio = current.open / previous.close
        if ratio < 0.65 or ratio > 1.5:
            current_date = _to_new_york(current.timestamp).date()
            expected = expected_split_adjustments.get(current_date)
            if expected is None:
                warnings.append(f"weighted_voting.backtest.{timeframe}.possible_unrecorded_corporate_action.{current_date.isoformat()}")
            elif abs(ratio - expected) > 0.05:
                errors.append(f"weighted_voting.backtest.{timeframe}.corporate_action_inconsistent.{current_date.isoformat()}")


def _validate_quotes(
    quotes: tuple[WeightedBacktestQuote, ...],
    candles: tuple[WeightedVotingCandle, ...],
    errors: list[str],
    warnings: list[str],
    quote_freshness_seconds: int,
) -> None:
    quote_times = [_to_utc(quote.timestamp) for quote in quotes]
    if quote_times != sorted(quote_times):
        errors.append("weighted_voting.backtest.quotes.timestamp_order_invalid")
    for quote in quotes:
        if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
            errors.append("weighted_voting.backtest.quotes.invalid_bid_ask")
            break
    for candle in candles:
        candle_time = _to_utc(candle.timestamp)
        nearest = min((abs((quote_time - candle_time).total_seconds()) for quote_time in quote_times), default=None)
        if nearest is None or nearest > quote_freshness_seconds:
            warnings.append(f"weighted_voting.backtest.quotes.stale_or_missing.{candle.timestamp.isoformat()}")
            break


def _data_hash(symbol: str, candles_by_timeframe: dict[str, tuple[WeightedVotingCandle, ...]], quotes: tuple[WeightedBacktestQuote, ...], source: str, fill_policy: str) -> str:
    payload = {
        "symbol": symbol,
        "source": source,
        "fillPolicy": fill_policy,
        "candles": {
            timeframe: [
                {
                    "timestamp": _to_utc(candle.timestamp).isoformat(),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for candle in candles
            ]
            for timeframe, candles in sorted(candles_by_timeframe.items())
        },
        "quotes": [
            {
                "timestamp": _to_utc(quote.timestamp).isoformat(),
                "bid": quote.bid,
                "ask": quote.ask,
            }
            for quote in quotes
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _to_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _to_new_york(timestamp: datetime) -> datetime:
    utc_value = _to_utc(timestamp)
    return utc_value.astimezone(_new_york_tz_for_utc(utc_value))


def _new_york_tz_for_utc(utc_value: datetime) -> timezone:
    year = utc_value.year
    dst_start = datetime(year, 3, _nth_weekday(year, 3, 6, 2), 7, tzinfo=timezone.utc)
    dst_end = datetime(year, 11, _nth_weekday(year, 11, 6, 1), 6, tzinfo=timezone.utc)
    offset_hours = -4 if dst_start <= utc_value < dst_end else -5
    return timezone(timedelta(hours=offset_hours), "America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> int:
    current = date(year, month, 1)
    days_until_weekday = (weekday - current.weekday()) % 7
    return 1 + days_until_weekday + (occurrence - 1) * 7


def _timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        try:
            return max(1, int(timeframe[:-1]))
        except ValueError:
            return 1
    return 1
