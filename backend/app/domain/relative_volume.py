from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any


MARKET_OPEN = time(9, 30)


@dataclass(frozen=True)
class RelativeVolumeWindow:
    dataReady: bool
    actualVolume: float
    expectedVolume: float | None
    cumulativeRelativeVolume: float | None
    averageRelativeVolume: float | None
    perBarRelativeVolumes: tuple[float, ...]
    missingBaselineMinutes: tuple[int, ...]
    reasonCodes: tuple[str, ...]


class PointInTimeRelativeVolumeService:
    def time_of_day_baseline(self, candles: list[dict[str, Any]], session_date: date) -> dict[int, float]:
        by_minute: dict[int, list[float]] = {}
        for candle in candles:
            timestamp = _timestamp(candle)
            local = _new_york_datetime(timestamp)
            if local.date() == session_date:
                continue
            minute = _minutes_after_open(timestamp)
            if minute < 0 or minute >= 390:
                continue
            by_minute.setdefault(minute, []).append(float(candle["volume"]))
        return {minute: mean(values) for minute, values in by_minute.items() if values}

    def measure_window(
        self,
        candles: list[dict[str, Any]],
        *,
        start_index: int,
        end_index: int,
        baseline: dict[int, float],
    ) -> RelativeVolumeWindow:
        selected = candles[start_index : end_index + 1]
        if not selected:
            return RelativeVolumeWindow(False, 0.0, None, None, None, (), (), ("relative_volume.empty_window",))

        actual_volume = sum(float(candle["volume"]) for candle in selected)
        expected_volume = 0.0
        per_bar: list[float] = []
        missing_minutes: list[int] = []
        for candle in selected:
            minute = _minutes_after_open(_timestamp(candle))
            expected = baseline.get(minute)
            if expected is None or expected <= 0:
                missing_minutes.append(minute)
                continue
            expected_volume += expected
            per_bar.append(float(candle["volume"]) / expected)

        if missing_minutes:
            return RelativeVolumeWindow(
                False,
                actual_volume,
                expected_volume if expected_volume > 0 else None,
                None,
                None,
                tuple(round(value, 4) for value in per_bar),
                tuple(missing_minutes),
                ("relative_volume.missing_time_of_day_baseline",),
            )
        if expected_volume <= 0:
            return RelativeVolumeWindow(
                False,
                actual_volume,
                None,
                None,
                None,
                (),
                (),
                ("relative_volume.zero_expected_volume",),
            )

        return RelativeVolumeWindow(
            True,
            actual_volume,
            expected_volume,
            actual_volume / expected_volume,
            mean(per_bar) if per_bar else None,
            tuple(round(value, 4) for value in per_bar),
            (),
            (),
        )


def _timestamp(candle: dict[str, Any]) -> datetime:
    value = candle["timestamp"]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _minutes_after_open(timestamp: datetime) -> int:
    local = _new_york_datetime(timestamp)
    open_at = local.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0)
    return int((local - open_at).total_seconds() // 60)


def _new_york_datetime(value: datetime) -> datetime:
    utc_value = value.astimezone(UTC)
    year = utc_value.year
    dst_start_utc = datetime(year, 3, _nth_sunday(year, 3, 2), 7, 0, tzinfo=UTC)
    dst_end_utc = datetime(year, 11, _nth_sunday(year, 11, 1), 6, 0, tzinfo=UTC)
    offset_hours = -4 if dst_start_utc <= utc_value < dst_end_utc else -5
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours), "America/New_York"))


def _nth_sunday(year: int, month: int, nth: int) -> int:
    first = datetime(year, month, 1)
    first_sunday = 1 + ((6 - first.weekday()) % 7)
    return first_sunday + ((nth - 1) * 7)
