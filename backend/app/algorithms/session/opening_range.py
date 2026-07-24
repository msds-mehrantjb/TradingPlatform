"""Point-in-time opening-range references for Session classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from backend.app.algorithms.session.calendar import exchange_session_bounds, parse_session_timestamp_utc, resolve_session_clock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


OpeningRangeStatus = Literal["building", "complete", "invalid"]
BreakDirection = Literal["up", "down", "inside", "unknown"]


@dataclass(frozen=True)
class OpeningRangeReference:
    name: str
    status: OpeningRangeStatus
    high: float | None
    low: float | None
    midpoint: float | None
    range_amount: float | None
    range_percent: float | None
    volume: float | None
    completion_timestamp: datetime
    bars_expected: int
    bars_observed: int
    missing_bar_count: int
    missing_bars: tuple[str, ...]
    start_timestamp: datetime
    end_timestamp: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "high": self.high,
            "low": self.low,
            "midpoint": self.midpoint,
            "rangeAmount": self.range_amount,
            "rangePercent": self.range_percent,
            "volume": self.volume,
            "completionTimestamp": self.completion_timestamp.isoformat(),
            "barsExpected": self.bars_expected,
            "barsObserved": self.bars_observed,
            "missingBarCount": self.missing_bar_count,
            "missingBars": self.missing_bars,
            "startTimestamp": self.start_timestamp.isoformat(),
            "endTimestamp": self.end_timestamp.isoformat(),
        }


@dataclass(frozen=True)
class OpeningRangeBreakout:
    reference_name: str
    status: str
    direction: BreakDirection
    wick_beyond_range: bool
    close_beyond_range: bool
    accepted: bool
    acceptance_bars_required: int
    acceptance_bars_observed: int
    retest: bool
    rejection_back_inside: bool
    failed_breakout: bool
    distance_from_range_amount: float | None
    distance_from_range_atr: float | None
    distance_from_range_bps: float | None
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "referenceName": self.reference_name,
            "status": self.status,
            "direction": self.direction,
            "wickBeyondRange": self.wick_beyond_range,
            "closeBeyondRange": self.close_beyond_range,
            "accepted": self.accepted,
            "acceptanceBarsRequired": self.acceptance_bars_required,
            "acceptanceBarsObserved": self.acceptance_bars_observed,
            "retest": self.retest,
            "rejectionBackInside": self.rejection_back_inside,
            "failedBreakout": self.failed_breakout,
            "distanceFromRangeAmount": self.distance_from_range_amount,
            "distanceFromRangeAtr": self.distance_from_range_atr,
            "distanceFromRangeBps": self.distance_from_range_bps,
            "reasonCodes": self.reason_codes,
        }


def analyze_opening_ranges(candles: list[dict[str, Any]], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> dict[str, Any]:
    bars = _regular_session_bars(candles, config=config)
    latest = bars[-1] if bars else None
    if latest is None:
        return {
            "references": {},
            "breakouts": {},
            "openingDrive": {"status": "not_ready", "direction": "unknown", "reasonCodes": ("session.opening_range.no_regular_bars",)},
            "reasonCodes": ("session.opening_range.no_regular_bars",),
        }

    clock = resolve_session_clock(latest["timestamp"], config=config)
    bounds = exchange_session_bounds(datetime.fromisoformat(clock.session_date).date(), config=config) if clock.session_date else None
    if bounds is None:
        return {
            "references": {},
            "breakouts": {},
            "openingDrive": {"status": "invalid", "direction": "unknown", "reasonCodes": ("session.opening_range.calendar_unavailable",)},
            "reasonCodes": ("session.opening_range.calendar_unavailable",),
        }

    exchange_open, _, _ = bounds
    references = {
        "OR5": _reference("OR5", bars, latest_timestamp=latest["timestamp"], exchange_open=exchange_open.astimezone(UTC), minutes=5, config=config),
        "OR15": _reference("OR15", bars, latest_timestamp=latest["timestamp"], exchange_open=exchange_open.astimezone(UTC), minutes=15, config=config),
        "OR30": _reference("OR30", bars, latest_timestamp=latest["timestamp"], exchange_open=exchange_open.astimezone(UTC), minutes=30, config=config),
    }
    atr = _atr_before_current(bars)
    breakouts = {name: _breakout(name, reference, bars, latest, atr=atr, config=config) for name, reference in references.items()}
    opening_drive = _opening_drive(bars, exchange_open=exchange_open.astimezone(UTC), latest=latest, config=config)
    reason_codes = tuple(
        dict.fromkeys(
            reason
            for item in (
                *(_reference_reasons(reference) for reference in references.values()),
                *(breakout.reason_codes for breakout in breakouts.values()),
                (opening_drive.get("reasonCodes") or ()),
            )
            for reason in item
        )
    )
    return {
        "references": {name: reference.as_dict() for name, reference in references.items()},
        "breakouts": {name: breakout.as_dict() for name, breakout in breakouts.items()},
        "openingDrive": opening_drive,
        "reasonCodes": reason_codes,
    }


def legacy_opening_range_value(reference: dict[str, Any] | None) -> str:
    if not reference or reference.get("status") == "building":
        return "NA"
    if reference.get("status") == "invalid":
        return "invalid"
    amount = reference.get("rangeAmount")
    pct = reference.get("rangePercent")
    if amount is None or pct is None:
        return "NA"
    return f"{float(amount):.2f} ({float(pct) * 100:.2f}%)"


def _regular_session_bars(candles: list[dict[str, Any]], *, config: SessionConfig) -> list[dict[str, Any]]:
    regular: dict[datetime, dict[str, Any]] = {}
    for candle in candles:
        try:
            timestamp = parse_session_timestamp_utc(str(candle["timestamp"]))
            clock = resolve_session_clock(timestamp, config=config)
        except (KeyError, TypeError, ValueError):
            continue
        if not clock.regular_session:
            continue
        regular[timestamp] = {
            **candle,
            "timestamp": timestamp,
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": 0.0 if candle.get("volume") is None else float(candle["volume"]),
        }
    return [regular[key] for key in sorted(regular)]


def _reference(
    name: str,
    bars: list[dict[str, Any]],
    *,
    latest_timestamp: datetime,
    exchange_open: datetime,
    minutes: int,
    config: SessionConfig,
) -> OpeningRangeReference:
    start = exchange_open
    end = exchange_open + timedelta(minutes=minutes)
    observed = [bar for bar in bars if start <= bar["timestamp"] < end]
    observed_by_timestamp = {bar["timestamp"]: bar for bar in observed}
    expected_timestamps = tuple(start + timedelta(minutes=index) for index in range(minutes))
    if latest_timestamp < end:
        eligible_expected = tuple(timestamp for timestamp in expected_timestamps if timestamp <= latest_timestamp)
        status: OpeningRangeStatus = "building"
    else:
        eligible_expected = expected_timestamps
        missing_full = tuple(timestamp for timestamp in expected_timestamps if timestamp not in observed_by_timestamp)
        status = "invalid" if len(missing_full) > config.opening_range_missing_bars_allowed else "complete"
    missing = tuple(timestamp.isoformat() for timestamp in eligible_expected if timestamp not in observed_by_timestamp)
    high = max((float(bar["high"]) for bar in observed), default=None)
    low = min((float(bar["low"]) for bar in observed), default=None)
    first_open = float(observed[0]["open"]) if observed else None
    range_amount = high - low if high is not None and low is not None else None
    midpoint = (high + low) / 2 if high is not None and low is not None else None
    range_percent = range_amount / first_open if range_amount is not None and first_open else None
    volume = sum(float(bar["volume"]) for bar in observed) if observed else None
    return OpeningRangeReference(
        name=name,
        status=status,
        high=high,
        low=low,
        midpoint=midpoint,
        range_amount=range_amount,
        range_percent=range_percent,
        volume=volume,
        completion_timestamp=end,
        bars_expected=minutes,
        bars_observed=len(observed_by_timestamp),
        missing_bar_count=len(missing),
        missing_bars=missing,
        start_timestamp=start,
        end_timestamp=end,
    )


def _breakout(
    name: str,
    reference: OpeningRangeReference,
    bars: list[dict[str, Any]],
    latest: dict[str, Any],
    *,
    atr: float | None,
    config: SessionConfig,
) -> OpeningRangeBreakout:
    if reference.status != "complete" or reference.high is None or reference.low is None:
        return OpeningRangeBreakout(name, reference.status, "unknown", False, False, False, config.opening_range_acceptance_bars, 0, False, False, False, None, None, None, (f"session.opening_range.{name.lower()}.{reference.status}",))
    if latest["timestamp"] < reference.completion_timestamp:
        return OpeningRangeBreakout(name, "not_ready", "unknown", False, False, False, config.opening_range_acceptance_bars, 0, False, False, False, None, None, None, (f"session.opening_range.{name.lower()}.not_complete_at_decision",))

    post_reference = [bar for bar in bars if bar["timestamp"] >= reference.completion_timestamp and bar["timestamp"] <= latest["timestamp"]]
    high = float(reference.high)
    low = float(reference.low)
    close = float(latest["close"])
    wick_up = float(latest["high"]) > high
    wick_down = float(latest["low"]) < low
    close_up = close > high
    close_down = close < low
    if close_up or wick_up:
        direction: BreakDirection = "up"
        distance = max(close - high, 0.0)
    elif close_down or wick_down:
        direction = "down"
        distance = min(close - low, 0.0)
    else:
        direction = "inside"
        distance = 0.0

    accepted_count = _accepted_count(post_reference, high=high, low=low, direction=direction)
    accepted = accepted_count >= config.opening_range_acceptance_bars
    retest = _has_retest(post_reference, high=high, low=low, direction=direction)
    rejection = (wick_up or wick_down or _had_prior_close_beyond(post_reference[:-1], high=high, low=low)) and low <= close <= high
    failed = rejection and _had_prior_close_beyond(post_reference, high=high, low=low)
    close_beyond = close_up or close_down
    wick_beyond = (wick_up or wick_down) and not close_beyond
    reason_codes = _breakout_reasons(name, direction, wick_beyond, close_beyond, accepted, retest, rejection, failed)
    return OpeningRangeBreakout(
        reference_name=name,
        status="ready",
        direction=direction,
        wick_beyond_range=wick_beyond,
        close_beyond_range=close_beyond,
        accepted=accepted,
        acceptance_bars_required=config.opening_range_acceptance_bars,
        acceptance_bars_observed=accepted_count,
        retest=retest,
        rejection_back_inside=rejection,
        failed_breakout=failed,
        distance_from_range_amount=distance,
        distance_from_range_atr=None if atr in {None, 0} else distance / atr,
        distance_from_range_bps=None if close == 0 else (distance / close) * 10_000,
        reason_codes=reason_codes,
    )


def _opening_drive(bars: list[dict[str, Any]], *, exchange_open: datetime, latest: dict[str, Any], config: SessionConfig) -> dict[str, Any]:
    or5_completion = exchange_open + timedelta(minutes=5)
    opening_bars = [bar for bar in bars if exchange_open <= bar["timestamp"] <= latest["timestamp"]]
    if latest["timestamp"] >= or5_completion or not opening_bars:
        return {"status": "complete", "direction": "unknown", "reasonCodes": ("session.opening_drive.complete",)}
    first_open = float(opening_bars[0]["open"])
    close = float(latest["close"])
    move_bps = 0.0 if first_open == 0 else ((close - first_open) / first_open) * 10_000
    if move_bps >= config.opening_drive_minimum_move_bps:
        direction = "up"
    elif move_bps <= -config.opening_drive_minimum_move_bps:
        direction = "down"
    else:
        direction = "inside"
    return {
        "status": "building",
        "direction": direction,
        "moveBps": move_bps,
        "high": max(float(bar["high"]) for bar in opening_bars),
        "low": min(float(bar["low"]) for bar in opening_bars),
        "volume": sum(float(bar["volume"]) for bar in opening_bars),
        "reasonCodes": (f"session.opening_drive.{direction}",),
    }


def _accepted_count(post_reference: list[dict[str, Any]], *, high: float, low: float, direction: BreakDirection) -> int:
    count = 0
    for bar in reversed(post_reference):
        close = float(bar["close"])
        if direction == "up" and close > high:
            count += 1
        elif direction == "down" and close < low:
            count += 1
        else:
            break
    return count


def _has_retest(post_reference: list[dict[str, Any]], *, high: float, low: float, direction: BreakDirection) -> bool:
    if direction == "up":
        return any(float(bar["low"]) <= high <= float(bar["high"]) and float(bar["close"]) >= high for bar in post_reference[1:])
    if direction == "down":
        return any(float(bar["low"]) <= low <= float(bar["high"]) and float(bar["close"]) <= low for bar in post_reference[1:])
    return False


def _had_prior_close_beyond(post_reference: list[dict[str, Any]], *, high: float, low: float) -> bool:
    return any(float(bar["close"]) > high or float(bar["close"]) < low for bar in post_reference)


def _breakout_reasons(
    name: str,
    direction: BreakDirection,
    wick_beyond: bool,
    close_beyond: bool,
    accepted: bool,
    retest: bool,
    rejection: bool,
    failed: bool,
) -> tuple[str, ...]:
    prefix = f"session.opening_range.{name.lower()}"
    reasons = [f"{prefix}.{direction}"]
    if wick_beyond:
        reasons.append(f"{prefix}.wick_only")
    if close_beyond:
        reasons.append(f"{prefix}.close_beyond")
    if accepted:
        reasons.append(f"{prefix}.accepted")
    if retest:
        reasons.append(f"{prefix}.retest")
    if rejection:
        reasons.append(f"{prefix}.rejection_back_inside")
    if failed:
        reasons.append(f"{prefix}.failed_breakout")
    return tuple(reasons)


def _reference_reasons(reference: OpeningRangeReference) -> tuple[str, ...]:
    return (f"session.opening_range.{reference.name.lower()}.{reference.status}",)


def _atr_before_current(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    sample = bars[:-1][-period:]
    if not sample:
        return None
    return sum(float(bar["high"]) - float(bar["low"]) for bar in sample) / len(sample)
