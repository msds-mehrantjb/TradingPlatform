"""Same-time normalized Session volatility evidence."""

from __future__ import annotations

from datetime import datetime
from math import sqrt
from statistics import median
from typing import Any

from backend.app.algorithms.session.baselines import MinuteBaseline, SessionBaselineArtifact, baseline_for_decision, percentile_rank
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


def analyze_session_volatility(
    candles: list[dict[str, Any]],
    *,
    symbol: str,
    baseline_artifact: SessionBaselineArtifact | None = None,
    decision_time: datetime | str | None = None,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> dict[str, Any]:
    if not candles:
        return _unknown("session.volatility.no_bars", baseline_artifact)
    latest = candles[-1]
    timestamp = decision_time or latest.get("timestamp")
    baseline, baseline_meta = baseline_for_decision(baseline_artifact, symbol=symbol, decision_time=timestamp, config=config)
    previous_close = float(candles[-2]["close"]) if len(candles) >= 2 else None
    high = float(latest["high"])
    low = float(latest["low"])
    close = float(latest["close"])
    true_range = max(high - low, abs(high - previous_close) if previous_close is not None else high - low, abs(low - previous_close) if previous_close is not None else high - low)
    range_pct = true_range / close if close else None
    closes = [float(candle["close"]) for candle in candles[-config.realized_volatility_window_bars :]]
    returns = [(closes[index] - closes[index - 1]) / closes[index - 1] for index in range(1, len(closes)) if closes[index - 1] != 0]
    realized_volatility = sqrt(sum(value * value for value in returns)) if returns else 0.0
    if baseline is None:
        return {
            "status": "unknown",
            "oneMinuteTrueRangePercent": range_pct,
            "shortWindowRealizedVolatility": realized_volatility,
            "rangePercentile": None,
            "realizedVolatilityPercentile": None,
            "baseline": baseline_meta,
            "reasonCodes": (baseline_meta["reason"],),
        }
    if baseline.sample_count < config.minimum_baseline_samples:
        return _not_ready(range_pct, realized_volatility, baseline, baseline_meta, "session.volatility.baseline_sample_count_insufficient", config)
    return {
        "status": "ready",
        "oneMinuteTrueRangePercent": range_pct,
        "shortWindowRealizedVolatility": realized_volatility,
        "rangePercentile": percentile_rank(range_pct, baseline.range_pct_samples),
        "realizedVolatilityPercentile": percentile_rank(realized_volatility, baseline.realized_volatility_samples),
        "baseline": {
            **baseline_meta,
            "rangePctMedian": median(baseline.range_pct_samples),
            "realizedVolatilityMedian": median(baseline.realized_volatility_samples),
        },
        "reasonCodes": ("session.volatility.same_time_ready",),
    }


def _not_ready(range_pct: float | None, realized_volatility: float, baseline: MinuteBaseline, baseline_meta: dict[str, Any], reason: str, config: SessionConfig) -> dict[str, Any]:
    return {
        "status": "not_ready",
        "oneMinuteTrueRangePercent": range_pct,
        "shortWindowRealizedVolatility": realized_volatility,
        "rangePercentile": None,
        "realizedVolatilityPercentile": None,
        "baseline": {**baseline_meta, "sampleCount": baseline.sample_count, "reliability": baseline.reliability(config=config)},
        "reasonCodes": (reason,),
    }


def _unknown(reason: str, baseline_artifact: SessionBaselineArtifact | None) -> dict[str, Any]:
    return {
        "status": "unknown",
        "oneMinuteTrueRangePercent": None,
        "shortWindowRealizedVolatility": None,
        "rangePercentile": None,
        "realizedVolatilityPercentile": None,
        "baseline": {
            "baselineVersion": baseline_artifact.baseline_version if baseline_artifact else None,
            "baselineCutoffDate": baseline_artifact.cutoff_date if baseline_artifact else None,
        },
        "reasonCodes": (reason,),
    }
