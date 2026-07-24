"""Historical same-time Session baseline artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import sqrt
from statistics import median
from typing import Any, Iterable

from backend.app.algorithms.session.calendar import parse_session_timestamp_utc, resolve_session_clock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


@dataclass(frozen=True)
class MinuteBaseline:
    symbol: str
    minute_from_open: int
    session_type: str
    baseline_version: str
    cutoff_date: str
    range_pct_samples: tuple[float, ...]
    realized_volatility_samples: tuple[float, ...]
    one_minute_volume_samples: tuple[float, ...]
    cumulative_volume_samples: tuple[float, ...]

    @property
    def sample_count(self) -> int:
        return min(
            len(self.range_pct_samples),
            len(self.realized_volatility_samples),
            len(self.one_minute_volume_samples),
            len(self.cumulative_volume_samples),
        )

    def reliability(self, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> str:
        if self.sample_count < config.minimum_baseline_samples:
            return "insufficient"
        if self.sample_count < config.minimum_baseline_samples * 3:
            return "limited"
        return "reliable"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "minuteFromOpen": self.minute_from_open,
            "sessionType": self.session_type,
            "baselineVersion": self.baseline_version,
            "cutoffDate": self.cutoff_date,
            "sampleCount": self.sample_count,
            "rangePctMedian": _median_or_none(self.range_pct_samples),
            "realizedVolatilityMedian": _median_or_none(self.realized_volatility_samples),
            "oneMinuteVolumeMedian": _median_or_none(self.one_minute_volume_samples),
            "cumulativeVolumeMedian": _median_or_none(self.cumulative_volume_samples),
        }


@dataclass(frozen=True)
class SessionBaselineArtifact:
    symbol: str
    baseline_version: str
    cutoff_date: str
    valid_from: datetime | None
    valid_until: datetime | None
    rows: tuple[MinuteBaseline, ...]
    source_session_dates: tuple[str, ...]

    def lookup(self, *, symbol: str, minute_from_open: int, session_type: str) -> MinuteBaseline | None:
        normalized = symbol.upper()
        for row in self.rows:
            if row.symbol == normalized and row.minute_from_open == minute_from_open and row.session_type == session_type:
                return row
        return None

    def valid_at(self, decision_time: datetime | str) -> bool:
        parsed = parse_session_timestamp_utc(decision_time)
        if self.valid_from and parsed < self.valid_from:
            return False
        if self.valid_until and parsed >= self.valid_until:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "baselineVersion": self.baseline_version,
            "cutoffDate": self.cutoff_date,
            "validFrom": self.valid_from.isoformat() if self.valid_from else None,
            "validUntil": self.valid_until.isoformat() if self.valid_until else None,
            "sourceSessionDates": self.source_session_dates,
            "rowCount": len(self.rows),
        }


def build_session_baseline_artifact(
    symbol: str,
    sessions: Iterable[list[dict[str, Any]]],
    *,
    cutoff_date: date | str,
    baseline_version: str | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> SessionBaselineArtifact:
    cutoff = _date(cutoff_date)
    version = baseline_version or config.baseline_version
    buckets: dict[tuple[int, str], dict[str, list[float]]] = {}
    source_dates: set[str] = set()
    for session in sessions:
        bars = _regular_bars(session, symbol=symbol, config=config)
        if not bars:
            continue
        session_date = bars[0]["sessionDate"]
        if _date(session_date) >= cutoff:
            continue
        source_dates.add(session_date)
        cumulative_volume = 0.0
        closes: list[float] = []
        previous_close: float | None = None
        for bar in bars:
            minute = int(bar["minuteFromOpen"])
            session_type = str(bar["sessionType"])
            key = (minute, session_type)
            bucket = buckets.setdefault(key, {"rangePct": [], "realizedVolatility": [], "oneMinuteVolume": [], "cumulativeVolume": []})
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            volume = float(bar["volume"])
            true_range = max(high - low, abs(high - previous_close) if previous_close is not None else high - low, abs(low - previous_close) if previous_close is not None else high - low)
            range_pct = true_range / close if close else 0.0
            closes.append(close)
            returns = _returns(closes[-config.realized_volatility_window_bars :])
            realized_volatility = sqrt(sum(value * value for value in returns)) if returns else 0.0
            cumulative_volume += volume
            bucket["rangePct"].append(range_pct)
            bucket["realizedVolatility"].append(realized_volatility)
            bucket["oneMinuteVolume"].append(volume)
            bucket["cumulativeVolume"].append(cumulative_volume)
            previous_close = close
    rows = tuple(
        MinuteBaseline(
            symbol=symbol.upper(),
            minute_from_open=minute,
            session_type=session_type,
            baseline_version=version,
            cutoff_date=cutoff.isoformat(),
            range_pct_samples=tuple(values["rangePct"]),
            realized_volatility_samples=tuple(values["realizedVolatility"]),
            one_minute_volume_samples=tuple(values["oneMinuteVolume"]),
            cumulative_volume_samples=tuple(values["cumulativeVolume"]),
        )
        for (minute, session_type), values in sorted(buckets.items())
    )
    return SessionBaselineArtifact(
        symbol=symbol.upper(),
        baseline_version=version,
        cutoff_date=cutoff.isoformat(),
        valid_from=valid_from,
        valid_until=valid_until,
        rows=rows,
        source_session_dates=tuple(sorted(source_dates)),
    )


def select_session_baseline_artifact(
    artifacts: Iterable[SessionBaselineArtifact],
    *,
    symbol: str,
    decision_time: datetime | str,
    baseline_version: str | None = None,
) -> SessionBaselineArtifact | None:
    parsed = parse_session_timestamp_utc(decision_time)
    candidates = [
        artifact
        for artifact in artifacts
        if artifact.symbol == symbol.upper()
        and (baseline_version is None or artifact.baseline_version == baseline_version)
        and artifact.valid_at(parsed)
        and _date(artifact.cutoff_date) <= parsed.date()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda artifact: (artifact.cutoff_date, artifact.baseline_version))


def baseline_for_decision(
    artifact: SessionBaselineArtifact | None,
    *,
    symbol: str,
    decision_time: datetime | str,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> tuple[MinuteBaseline | None, dict[str, Any]]:
    if artifact is None:
        return None, {"status": "missing", "baselineVersion": None, "baselineCutoffDate": None, "reason": "session.baseline.missing"}
    parsed = parse_session_timestamp_utc(decision_time)
    if not artifact.valid_at(parsed) or _date(artifact.cutoff_date) > parsed.date():
        return None, {
            "status": "invalid",
            "baselineVersion": artifact.baseline_version,
            "baselineCutoffDate": artifact.cutoff_date,
            "reason": "session.baseline.not_valid_at_decision",
        }
    clock = resolve_session_clock(parsed, config=config)
    if clock.minute_from_open is None:
        return None, {
            "status": "not_ready",
            "baselineVersion": artifact.baseline_version,
            "baselineCutoffDate": artifact.cutoff_date,
            "reason": "session.baseline.no_regular_minute",
        }
    session_type = session_type_for_clock(clock)
    row = artifact.lookup(symbol=symbol, minute_from_open=clock.minute_from_open, session_type=session_type)
    if row is None:
        return None, {
            "status": "missing",
            "baselineVersion": artifact.baseline_version,
            "baselineCutoffDate": artifact.cutoff_date,
            "minuteFromOpen": clock.minute_from_open,
            "sessionType": session_type,
            "reason": "session.baseline.minute_missing",
        }
    return row, {
        "status": "ready",
        "baselineVersion": row.baseline_version,
        "baselineCutoffDate": row.cutoff_date,
        "minuteFromOpen": row.minute_from_open,
        "sessionType": row.session_type,
        "sampleCount": row.sample_count,
        "reliability": row.reliability(config=config),
    }


def percentile_rank(value: float | None, samples: tuple[float, ...]) -> float | None:
    if value is None or not samples:
        return None
    less = sum(1 for sample in samples if sample < value)
    equal = sum(1 for sample in samples if sample == value)
    return (less + (0.5 * equal)) / len(samples)


def session_type_for_clock(clock: Any) -> str:
    return "early_close" if bool(getattr(clock, "early_close", False)) else "regular"


def _regular_bars(candles: list[dict[str, Any]], *, symbol: str, config: SessionConfig) -> list[dict[str, Any]]:
    bars: dict[datetime, dict[str, Any]] = {}
    for candle in candles:
        try:
            timestamp = parse_session_timestamp_utc(str(candle["timestamp"]))
            clock = resolve_session_clock(timestamp, config=config)
            if not clock.regular_session or clock.minute_from_open is None:
                continue
            bars[timestamp] = {
                **candle,
                "timestamp": timestamp,
                "symbol": symbol.upper(),
                "sessionDate": clock.session_date,
                "sessionType": session_type_for_clock(clock),
                "minuteFromOpen": clock.minute_from_open,
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": 0.0 if candle.get("volume") is None else float(candle["volume"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return [bars[key] for key in sorted(bars)]


def _returns(closes: list[float]) -> list[float]:
    return [(closes[index] - closes[index - 1]) / closes[index - 1] for index in range(1, len(closes)) if closes[index - 1] != 0]


def _date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _median_or_none(values: tuple[float, ...]) -> float | None:
    return median(values) if values else None
