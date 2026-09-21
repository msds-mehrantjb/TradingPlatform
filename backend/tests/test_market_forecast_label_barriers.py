"""The forecast's up and down barriers must be symmetric, and served as trained.

A 0.25% stop floor against a 0% target floor put the down barrier about six times farther
from the entry than the up barrier, so "up first" was the label 65% of the time and "down
first" 4%. Models trained on that answered "up" on 96-99% of bars and scored the base
rate, which read as 65% accuracy and was worth nothing.

The serving side reads these from the artifact that was trained, so a configured zero has
to survive the read: `or` treated it as missing and silently restored the asymmetry.
"""

from __future__ import annotations

from backend.app import market_forecast
from backend.app.train_market_forecast import volatility_adjusted_label_barriers


def candles(count: int = 30, *, price: float = 760.0, spread: float = 0.30) -> list[dict]:
    return [
        {
            "timestamp": f"2026-09-21T14:{minute:02d}:00Z",
            "open": price,
            "high": price + spread / 2,
            "low": price - spread / 2,
            "close": price,
            "volume": 1000,
        }
        for minute in range(count)
    ]


def features(atr: float = 0.30) -> dict:
    return {"volatility": {"atr_1m": atr, "realized_volatility": 0.0}}


LABEL_DEFAULTS = dict(
    profit_target=0.25,
    stop_loss=0.25,
    min_target_pct=market_forecast.DEFAULT_MIN_TARGET_PCT,
    min_stop_pct=market_forecast.DEFAULT_MIN_STOP_PCT,
    target_atr_multiplier=market_forecast.DEFAULT_TARGET_ATR_MULTIPLIER,
    stop_atr_multiplier=market_forecast.DEFAULT_STOP_ATR_MULTIPLIER,
    atr_lookback_minutes=5,
)


def test_label_barriers_are_symmetric_by_default() -> None:
    rows = candles()
    barriers = volatility_adjusted_label_barriers(rows, len(rows) - 1, **LABEL_DEFAULTS)

    assert barriers["targetDistance"] == barriers["stopDistance"]


def test_the_stop_floor_no_longer_dwarfs_the_target_floor() -> None:
    assert market_forecast.DEFAULT_MIN_STOP_PCT == market_forecast.DEFAULT_MIN_TARGET_PCT


def test_served_barriers_are_symmetric_for_a_symmetric_artifact() -> None:
    artifact = {"label": {"profitTargetDollars": 0.25, "stopLossDollars": 0.25, "minTargetPct": 0.0, "minStopPct": 0.0}}
    served = market_forecast.volatility_adjusted_barriers(features(), 760.0, artifact=artifact, horizon_minutes=5)

    assert served["minStopPct"] == 0.0, "a configured zero must not fall back to the default"
    assert served["targetDistance"] == served["stopDistance"]


def test_an_artifact_trained_on_asymmetric_barriers_is_still_served_them() -> None:
    # Models trained before the fix must keep the barriers they learned.
    artifact = {"label": {"profitTargetDollars": 0.25, "stopLossDollars": 0.25, "minTargetPct": 0.0, "minStopPct": 0.0025}}
    served = market_forecast.volatility_adjusted_barriers(features(), 760.0, artifact=artifact, horizon_minutes=5)

    assert served["minStopPct"] == 0.0025
    assert served["stopDistance"] > served["targetDistance"]


def test_a_missing_label_config_uses_the_defaults() -> None:
    served = market_forecast.volatility_adjusted_barriers(features(), 760.0, artifact={}, horizon_minutes=5)

    assert served["minStopPct"] == market_forecast.DEFAULT_MIN_STOP_PCT
    assert served["stopAtrMultiplier"] == market_forecast.DEFAULT_STOP_ATR_MULTIPLIER
