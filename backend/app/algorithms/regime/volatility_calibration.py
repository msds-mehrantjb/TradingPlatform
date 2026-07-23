"""Historical minute-of-session volatility calibration for Regime.

The artifact built here is passive by default. Live and paper runtimes must
explicitly opt in before calibrated percentiles are applied to classification.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping

from backend.app.algorithms.regime.contracts import RegimeCandle
from backend.app.algorithms.regime.exchange_calendar import exchange_session, parse_exchange_timestamp


INTRADAY_VOLATILITY_CALIBRATION_VERSION = "regime_intraday_volatility_calibration_v1"
INTRADAY_VOLATILITY_ARTIFACT_TYPE = "intraday_volatility_minute_percentiles"
INACTIVE_UNTIL_LIVE_PAPER_TRADING = "inactive_until_live_paper_trading"


@dataclass(frozen=True)
class _CalibrationObservation:
    minute_of_session: int
    atr_percent: float
    realized_volatility: float
    candle_range: float
    volume: float


def build_intraday_volatility_calibration_artifact(
    candles: Iterable[RegimeCandle | Mapping[str, Any]],
    *,
    symbol: str = "SPY",
    min_sample_size: int = 20,
    atr_period: int = 14,
    realized_volatility_period: int = 20,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a passive calibration artifact from historical one-minute candles.

    Candles are grouped by exchange-local minute-of-session, so 9:30 a.m. ET
    maps to minute 0 in both summer and winter sessions.
    """

    parsed_candles = sorted((_coerce_candle(candle) for candle in candles), key=_parsed_timestamp_sort_key)
    observations_by_session: dict[str, list[RegimeCandle]] = defaultdict(list)
    skipped = {"invalidTimestamp": 0, "outsideRegularSession": 0}

    for candle in parsed_candles:
        session = exchange_session(candle.timestamp)
        if session.reason == "invalid_timestamp":
            skipped["invalidTimestamp"] += 1
            continue
        if session.minutes_from_open is None or session.session_date is None:
            skipped["outsideRegularSession"] += 1
            continue
        observations_by_session[session.session_date].append(candle)

    observations_by_minute: dict[int, list[_CalibrationObservation]] = defaultdict(list)
    for session_candles in observations_by_session.values():
        for observation in _session_observations(
            tuple(sorted(session_candles, key=_parsed_timestamp_sort_key)),
            atr_period=atr_period,
            realized_volatility_period=realized_volatility_period,
        ):
            observations_by_minute[observation.minute_of_session].append(observation)

    minutes: dict[str, dict[str, Any]] = {}
    insufficient_minutes = 0
    for minute, observations in sorted(observations_by_minute.items()):
        if len(observations) < min_sample_size:
            insufficient_minutes += 1
            continue
        atr_samples = sorted(obs.atr_percent for obs in observations)
        rv_samples = sorted(obs.realized_volatility for obs in observations)
        range_samples = sorted(obs.candle_range for obs in observations)
        volume_samples = sorted(obs.volume for obs in observations)
        minutes[str(minute)] = {
            "minuteOfSession": minute,
            "sampleSize": len(observations),
            "expectedRange": mean(range_samples),
            "expectedVolume": mean(volume_samples),
            "atrPercentSamples": atr_samples,
            "realizedVolatilitySamples": rv_samples,
            "rangeSamples": range_samples,
            "volumeSamples": volume_samples,
            "quantiles": {
                "atrPercent": _quantiles(atr_samples),
                "realizedVolatility": _quantiles(rv_samples),
                "range": _quantiles(range_samples),
                "volume": _quantiles(volume_samples),
            },
        }

    return {
        "artifactId": f"{symbol.upper()}_{INTRADAY_VOLATILITY_ARTIFACT_TYPE}_{INTRADAY_VOLATILITY_CALIBRATION_VERSION}",
        "algorithmId": "regime",
        "artifactType": INTRADAY_VOLATILITY_ARTIFACT_TYPE,
        "calibrationVersion": INTRADAY_VOLATILITY_CALIBRATION_VERSION,
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "symbol": symbol.upper(),
        "createdAt": created_at or datetime.now(timezone.utc).isoformat(),
        "unitConvention": {
            "atrPercent": "decimal_ratio",
            "realizedVolatility": "decimal_ratio",
            "minuteOfSession": "minutes_after_regular_open",
            "range": "price_units",
            "volume": "shares",
        },
        "parameters": {
            "minSampleSize": min_sample_size,
            "atrPeriod": atr_period,
            "realizedVolatilityPeriod": realized_volatility_period,
        },
        "coverage": {
            "regularSessionCandles": sum(len(items) for items in observations_by_session.values()),
            "sessions": len(observations_by_session),
            "calibratedMinutes": len(minutes),
            "insufficientMinutes": insufficient_minutes,
            "skipped": skipped,
        },
        "minutes": minutes,
    }


def build_intraday_volatility_context_feed(
    artifact: Mapping[str, Any],
    latest_candle: RegimeCandle | Mapping[str, Any],
    *,
    atr_percent: float | None,
    realized_volatility_value: float | None,
    allow_inactive: bool = False,
) -> dict[str, Any]:
    """Create the classifier-compatible intradayVolatilityBaseline feed.

    With the default ``allow_inactive=False``, the artifact stays diagnostic-only
    and does not return percentiles that classification can consume.
    """

    activation_status = str(artifact.get("activationStatus") or "").lower()
    if activation_status == INACTIVE_UNTIL_LIVE_PAPER_TRADING and not allow_inactive:
        return {
            "calibrationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
            "artifactId": artifact.get("artifactId"),
            "activationStatus": activation_status,
            "atrPercentile": None,
            "realizedVolatilityPercentile": None,
            "currentRangeVsExpected": None,
            "currentVolumeVsExpected": None,
            "expectedRange": None,
            "expectedVolume": None,
            "sampleSize": 0,
            "source": "historical_minute_calibration_inactive",
        }

    candle = _coerce_candle(latest_candle)
    session = exchange_session(candle.timestamp)
    if session.minutes_from_open is None:
        return {
            "calibrationStatus": "outside_regular_session",
            "artifactId": artifact.get("artifactId"),
            "activationStatus": activation_status,
            "sampleSize": 0,
            "source": "historical_minute_calibration",
        }

    minute = (artifact.get("minutes") or {}).get(str(session.minutes_from_open))
    if not isinstance(minute, Mapping):
        return {
            "calibrationStatus": "missing_minute",
            "artifactId": artifact.get("artifactId"),
            "activationStatus": activation_status,
            "minuteOfSession": session.minutes_from_open,
            "sampleSize": 0,
            "source": "historical_minute_calibration",
        }

    expected_range = _number(minute.get("expectedRange"))
    expected_volume = _number(minute.get("expectedVolume"))
    current_range = max(0.0, candle.high - candle.low)
    return {
        "calibrationStatus": "ready",
        "artifactId": artifact.get("artifactId"),
        "activationStatus": activation_status,
        "minuteOfSession": session.minutes_from_open,
        "atrPercentile": _empirical_percentile(atr_percent, minute.get("atrPercentSamples")),
        "realizedVolatilityPercentile": _empirical_percentile(
            realized_volatility_value,
            minute.get("realizedVolatilitySamples"),
        ),
        "currentRangeVsExpected": (current_range / expected_range) if expected_range and expected_range > 0 else None,
        "currentVolumeVsExpected": (candle.volume / expected_volume) if expected_volume and expected_volume > 0 else None,
        "expectedRange": expected_range,
        "expectedVolume": expected_volume,
        "sampleSize": int(_number(minute.get("sampleSize")) or 0),
        "source": "historical_minute_calibration",
    }


def _session_observations(
    candles: tuple[RegimeCandle, ...],
    *,
    atr_period: int,
    realized_volatility_period: int,
) -> list[_CalibrationObservation]:
    observations: list[_CalibrationObservation] = []
    true_ranges: list[float] = []
    closes: list[float] = []

    for index, candle in enumerate(candles):
        previous_close = candles[index - 1].close if index > 0 else candle.close
        true_ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        closes.append(candle.close)
        if len(true_ranges) < atr_period or len(closes) < realized_volatility_period + 1:
            continue
        session = exchange_session(candle.timestamp)
        if session.minutes_from_open is None:
            continue
        atr_value = mean(true_ranges[-atr_period:])
        atr_percent = atr_value / max(candle.close, 0.01)
        returns = [
            (closes[item] - closes[item - 1]) / max(closes[item - 1], 0.01)
            for item in range(len(closes) - realized_volatility_period, len(closes))
        ]
        average_return = mean(returns)
        realized_variance = mean((item - average_return) ** 2 for item in returns)
        observations.append(
            _CalibrationObservation(
                minute_of_session=session.minutes_from_open,
                atr_percent=atr_percent,
                realized_volatility=realized_variance**0.5,
                candle_range=max(0.0, candle.high - candle.low),
                volume=max(0.0, candle.volume),
            )
        )
    return observations


def _coerce_candle(raw: RegimeCandle | Mapping[str, Any]) -> RegimeCandle:
    if isinstance(raw, RegimeCandle):
        return raw
    return RegimeCandle(
        timestamp=str(raw.get("timestamp") or raw.get("t") or ""),
        open=float(raw.get("open") or raw.get("o") or 0.0),
        high=float(raw.get("high") or raw.get("h") or raw.get("close") or raw.get("c") or 0.0),
        low=float(raw.get("low") or raw.get("l") or raw.get("close") or raw.get("c") or 0.0),
        close=float(raw.get("close") or raw.get("c") or 0.0),
        volume=float(raw.get("volume") or raw.get("v") or 0.0),
        vwap=float(raw["vwap"]) if raw.get("vwap") is not None else None,
    )


def _parsed_timestamp_sort_key(candle: RegimeCandle) -> datetime:
    parsed = parse_exchange_timestamp(candle.timestamp)
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _empirical_percentile(value: float | None, samples: Any) -> float | None:
    value = _number(value)
    if value is None or not isinstance(samples, list) or not samples:
        return None
    numeric_samples = sorted(sample for sample in (_number(item) for item in samples) if sample is not None)
    if not numeric_samples:
        return None
    below_or_equal = sum(1 for sample in numeric_samples if sample <= value)
    return max(0.0, min(1.0, below_or_equal / len(numeric_samples)))


def _quantiles(samples: list[float]) -> dict[str, float | None]:
    return {
        "p25": _nearest_rank_quantile(samples, 0.25),
        "p50": _nearest_rank_quantile(samples, 0.50),
        "p75": _nearest_rank_quantile(samples, 0.75),
        "p97": _nearest_rank_quantile(samples, 0.97),
    }


def _nearest_rank_quantile(samples: list[float], probability: float) -> float | None:
    if not samples:
        return None
    index = min(len(samples) - 1, max(0, round((len(samples) - 1) * probability)))
    return samples[index]


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
