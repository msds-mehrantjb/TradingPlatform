"""Point-in-time VWAP features for the Session subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from backend.app.algorithms.session.calendar import parse_session_timestamp_utc, resolve_session_clock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


VWAP_PRICE_CONVENTION = "typical_price_x_volume"
VWAP_PRICE_CONVENTION_DESCRIPTION = "VWAP uses finalized one-minute typical price ((high + low + close) / 3) weighted by finalized bar volume."
VwapPosition = Literal["above", "below", "neutral", "unknown"]


@dataclass(frozen=True)
class VwapBarSnapshot:
    timestamp: datetime
    vwap: float | None
    close: float
    volume: float | None
    cumulative_volume: float
    distance_dollars: float | None
    distance_bps: float | None
    distance_atr: float | None
    position: VwapPosition
    crossing_count: int
    crossing_frequency_per_hour: float
    time_above_bars: int
    time_below_bars: int
    acceptance_above: bool
    acceptance_below: bool
    average_excursion: float | None
    reclaim_above: bool
    reclaim_below: bool
    rejection_above: bool
    rejection_below: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "vwap": self.vwap,
            "close": self.close,
            "volume": self.volume,
            "cumulativeVolume": self.cumulative_volume,
            "distanceDollars": self.distance_dollars,
            "distanceBps": self.distance_bps,
            "distanceAtr": self.distance_atr,
            "position": self.position,
            "crossingCount": self.crossing_count,
            "crossingFrequencyPerHour": self.crossing_frequency_per_hour,
            "timeAboveBars": self.time_above_bars,
            "timeBelowBars": self.time_below_bars,
            "acceptanceAbove": self.acceptance_above,
            "acceptanceBelow": self.acceptance_below,
            "averageExcursion": self.average_excursion,
            "reclaimAbove": self.reclaim_above,
            "reclaimBelow": self.reclaim_below,
            "rejectionAbove": self.rejection_above,
            "rejectionBelow": self.rejection_below,
        }


def analyze_vwap(candles: list[dict[str, Any]], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> dict[str, Any]:
    bars = _regular_session_bars(candles, config=config)
    if not bars:
        return _empty_result("not_ready", ("session.vwap.no_regular_bars",))
    if any(bar["volume"] is None for bar in bars):
        return _empty_result("invalid", ("session.vwap.volume_missing",), bar_count=len(bars))

    snapshots = _snapshots(bars, config=config)
    current = snapshots[-1] if snapshots else None
    if current is None or current.vwap is None:
        return _empty_result("not_ready", ("session.vwap.no_cumulative_volume",), bar_count=len(bars), cumulative_volume=sum(float(bar["volume"] or 0) for bar in bars))

    slopes = _slopes(snapshots, config=config)
    reason_codes = ["session.vwap.ready", f"session.vwap.position.{current.position}"]
    if current.acceptance_above:
        reason_codes.append("session.vwap.acceptance_above")
    if current.acceptance_below:
        reason_codes.append("session.vwap.acceptance_below")
    if current.reclaim_above:
        reason_codes.append("session.vwap.reclaim_above")
    if current.reclaim_below:
        reason_codes.append("session.vwap.reclaim_below")
    if current.rejection_above:
        reason_codes.append("session.vwap.rejection_above")
    if current.rejection_below:
        reason_codes.append("session.vwap.rejection_below")
    return {
        "status": "ready",
        "metadata": _metadata(),
        "current": current.as_dict(),
        "slopes": slopes,
        "history": [snapshot.as_dict() for snapshot in snapshots],
        "barCount": len(bars),
        "cumulativeVolume": current.cumulative_volume,
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
    }


def vwap_at(candles: list[dict[str, Any]], timestamp: datetime | str, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> dict[str, Any]:
    cutoff = parse_session_timestamp_utc(timestamp)
    eligible = []
    for candle in candles:
        try:
            parsed = parse_session_timestamp_utc(str(candle["timestamp"]))
        except (KeyError, ValueError):
            continue
        if parsed <= cutoff:
            eligible.append(candle)
    return analyze_vwap(eligible, config=config)


def legacy_vwap_value(features: dict[str, Any]) -> float | None:
    return (features.get("current") or {}).get("vwap")


def legacy_vwap_slope(features: dict[str, Any], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> float | None:
    slopes = features.get("slopes") or {}
    preferred = str(max(config.vwap_slope_windows or (0,)))
    return slopes.get(preferred) or slopes.get(int(preferred))


def legacy_vwap_crosses(features: dict[str, Any]) -> int | None:
    current = features.get("current") or {}
    return current.get("crossingCount")


def _snapshots(bars: list[dict[str, Any]], *, config: SessionConfig) -> list[VwapBarSnapshot]:
    snapshots: list[VwapBarSnapshot] = []
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    signs: list[VwapPosition] = []
    crossing_timestamps: list[datetime] = []
    for index, bar in enumerate(bars):
        volume = float(bar["volume"] or 0.0)
        typical_price = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
        cumulative_pv += typical_price * volume
        cumulative_volume += volume
        vwap = cumulative_pv / cumulative_volume if cumulative_volume > 0 else None
        close = float(bar["close"])
        atr = _atr(bars[: index + 1])
        distance = close - vwap if vwap is not None else None
        distance_bps = None if distance is None or close == 0 else (distance / close) * 10_000
        distance_atr = None if distance is None or not atr else distance / atr
        position = _position(distance_bps, config=config)
        previous_position = signs[-1] if signs else "unknown"
        if _is_cross(previous_position, position):
            crossing_timestamps.append(bar["timestamp"])
        signs.append(position)
        lookback_start = max(0, index - config.vwap_crossing_lookback_bars + 1)
        lookback_timestamps = {item["timestamp"] for item in bars[lookback_start : index + 1]}
        crossing_count = sum(1 for timestamp in crossing_timestamps if timestamp in lookback_timestamps)
        elapsed_minutes = max(1.0, ((bars[index]["timestamp"] - bars[lookback_start]["timestamp"]).total_seconds() / 60.0) + 1.0)
        recent_signs = signs[lookback_start : index + 1]
        recent_distances = [
            abs(snapshot.distance_dollars)
            for snapshot in snapshots[lookback_start:index]
            if snapshot.distance_dollars is not None
        ]
        if distance is not None:
            recent_distances.append(abs(distance))
        acceptance_window = signs[max(0, index - config.vwap_acceptance_bars + 1) : index + 1]
        snapshots.append(
            VwapBarSnapshot(
                timestamp=bar["timestamp"],
                vwap=vwap,
                close=close,
                volume=bar["volume"],
                cumulative_volume=cumulative_volume,
                distance_dollars=distance,
                distance_bps=distance_bps,
                distance_atr=distance_atr,
                position=position,
                crossing_count=crossing_count,
                crossing_frequency_per_hour=(crossing_count / elapsed_minutes) * 60.0,
                time_above_bars=sum(1 for sign in recent_signs if sign == "above"),
                time_below_bars=sum(1 for sign in recent_signs if sign == "below"),
                acceptance_above=len(acceptance_window) >= config.vwap_acceptance_bars and all(sign == "above" for sign in acceptance_window),
                acceptance_below=len(acceptance_window) >= config.vwap_acceptance_bars and all(sign == "below" for sign in acceptance_window),
                average_excursion=sum(recent_distances[-config.vwap_average_excursion_bars :]) / len(recent_distances[-config.vwap_average_excursion_bars :]) if recent_distances else None,
                reclaim_above=previous_position == "below" and position == "above",
                reclaim_below=previous_position == "above" and position == "below",
                rejection_above=bool(vwap is not None and float(bar["low"]) <= vwap and position == "above"),
                rejection_below=bool(vwap is not None and float(bar["high"]) >= vwap and position == "below"),
            )
        )
    return snapshots


def _slopes(snapshots: list[VwapBarSnapshot], *, config: SessionConfig) -> dict[str, float | None]:
    current = snapshots[-1].vwap if snapshots else None
    slopes: dict[str, float | None] = {}
    for window in config.vwap_slope_windows:
        key = str(window)
        if current is None or len(snapshots) <= window:
            slopes[key] = None
            continue
        prior = snapshots[-1 - window].vwap
        slopes[key] = None if prior in {None, 0} else (current - prior) / prior
    return slopes


def _regular_session_bars(candles: list[dict[str, Any]], *, config: SessionConfig) -> list[dict[str, Any]]:
    regular: dict[datetime, dict[str, Any]] = {}
    for candle in candles:
        try:
            timestamp = parse_session_timestamp_utc(str(candle["timestamp"]))
            clock = resolve_session_clock(timestamp, config=config)
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not clock.regular_session:
            continue
        regular[timestamp] = {
            "timestamp": timestamp,
            "high": high,
            "low": low,
            "close": close,
            "volume": None if candle.get("volume") is None else float(candle["volume"]),
        }
    return [regular[key] for key in sorted(regular)]


def _position(distance_bps: float | None, *, config: SessionConfig) -> VwapPosition:
    if distance_bps is None:
        return "unknown"
    if distance_bps > config.vwap_deadband_bps:
        return "above"
    if distance_bps < -config.vwap_deadband_bps:
        return "below"
    return "neutral"


def _is_cross(previous: VwapPosition, current: VwapPosition) -> bool:
    return previous in {"above", "below"} and current in {"above", "below"} and previous != current


def _atr(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    sample = bars[-period:]
    if not sample:
        return None
    ranges = [float(bar["high"]) - float(bar["low"]) for bar in sample]
    return sum(ranges) / len(ranges) if ranges else None


def _empty_result(status: str, reason_codes: tuple[str, ...], *, bar_count: int = 0, cumulative_volume: float = 0.0) -> dict[str, Any]:
    return {
        "status": status,
        "metadata": _metadata(),
        "current": None,
        "slopes": {},
        "history": [],
        "barCount": bar_count,
        "cumulativeVolume": cumulative_volume,
        "reasonCodes": reason_codes,
    }


def _metadata() -> dict[str, str]:
    return {
        "priceConvention": VWAP_PRICE_CONVENTION,
        "priceConventionDescription": VWAP_PRICE_CONVENTION_DESCRIPTION,
    }
