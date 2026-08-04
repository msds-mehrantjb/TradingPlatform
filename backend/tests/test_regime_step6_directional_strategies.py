from __future__ import annotations

import inspect
import importlib

import pytest

from backend.app.algorithms.regime.configuration import (
    REGIME_STRATEGY_IDS,
    REGIME_STRATEGY_PARAMETER_DEFAULTS,
    validate_regime_trading_settings_snapshot,
)
from backend.app.algorithms.regime.contracts import RegimeAxes, RegimeClassification
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS, evaluate_strategy


DIRECTIONAL_IDS = tuple(strategy.strategy_id for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "directional")


def test_no_named_directional_strategy_is_generic_score_alias() -> None:
    for strategy_id in DIRECTIONAL_IDS:
        module = importlib.import_module(f"backend.app.algorithms.regime.strategies.directional.{strategy_id}")
        source = inspect.getsource(module)
        assert "directional_by_scores" not in source
        assert "evaluate = lambda" not in source


def test_strategy_settings_are_typed_and_reject_unknown_parameters() -> None:
    settings = validate_regime_trading_settings_snapshot().as_dict()
    for strategy_id in REGIME_STRATEGY_IDS:
        record = settings["strategy_settings"][strategy_id]
        assert record["lifecycle"] in {"active", "shadow", "disabled"}
        assert record["settingsType"] == f"{strategy_id}_settings_v1"
        assert record["parameters"] == REGIME_STRATEGY_PARAMETER_DEFAULTS[strategy_id]

    with pytest.raises(ValueError, match="Unknown Regime strategy parameter"):
        validate_regime_trading_settings_snapshot(
            {"strategy_settings": {"moving_average_trend": {"parameters": {"bullScoreWeight": 1.0}}}}
        )


def test_strategy_lifecycle_shadow_and_disabled_do_not_emit_production_signal() -> None:
    snapshot = _snapshot(_trend_rows("up", count=80))
    classification = _classification("strong_uptrend", direction="up", structure="trend")
    active = evaluate_strategy("moving_average_trend", snapshot, classification, {"lifecycle": "active"})
    shadow = evaluate_strategy("moving_average_trend", snapshot, classification, {"lifecycle": "shadow"})
    disabled = evaluate_strategy("moving_average_trend", snapshot, classification, {"lifecycle": "disabled"})

    assert active.signal == "Buy"
    assert shadow.signal == "Hold"
    assert shadow.eligible is False
    assert shadow.evidence["shadowSignal"] == "Buy"
    assert disabled.signal == "Hold"
    assert disabled.eligible is False


def test_semantic_fixtures_demonstrate_independent_strategy_behaviour() -> None:
    fixtures = {
        "moving_average_trend": (_snapshot(_trend_rows("up", count=80)), _classification("strong_uptrend", direction="up", structure="trend"), "Buy"),
        "opening_range_breakout": (_snapshot(_opening_breakout_rows("up")), _classification("opening_breakout", direction="up", structure="breakout"), "Hold"),
        "rsi_mean_reversion": (_snapshot(_rsi_reversal_rows("down_to_recovery")), _classification("range_bound", direction="flat", structure="range"), "Buy"),
        "liquidity_sweep_reversal": (_snapshot(_sweep_rows("low")), _classification("failed_breakout_reversal", direction="flat", structure="liquidity_sweep"), "Hold"),
        "gap_continuation_fade": (
            _snapshot(_gap_rows("fade_up"), context={"previousRegularClose": 100.0, "premarketHigh": 103.0, "premarketLow": 100.5}),
            _classification("gap_session", direction="down", structure="reversal"),
            "Hold",
        ),
    }

    reasons = set()
    for strategy_id, (snapshot, classification, expected_signal) in fixtures.items():
        output = evaluate_strategy(strategy_id, snapshot, classification, {"lifecycle": "active"})
        assert output.signal == expected_signal, (strategy_id, output)
        reasons.add(output.reason)

    assert len(reasons) == len(fixtures)


def test_strategies_abstain_outside_valid_setup_and_with_missing_inputs() -> None:
    early_orb = evaluate_strategy("opening_range_breakout", _snapshot(_opening_breakout_rows("up", count=20)), _classification("opening_breakout", direction="up", structure="breakout"), {"lifecycle": "active"})
    warm_macd = evaluate_strategy("macd_momentum", _snapshot(_trend_rows("up", count=20)), _classification("strong_uptrend", direction="up", structure="trend"), {"lifecycle": "active"})
    generic_direction = _classification("strong_uptrend", direction="up", structure="trend", bull_score=5, bear_score=0)
    no_setup_reversal = evaluate_strategy("failed_breakout_reversal", _snapshot(_trend_rows("up", count=80)), generic_direction, {"lifecycle": "active"})

    assert early_orb.signal == "Hold"
    assert "missingInputReasons" in early_orb.evidence
    assert warm_macd.signal == "Hold"
    assert "missingInputReasons" in warm_macd.evidence
    assert no_setup_reversal.signal == "Sell"
    assert no_setup_reversal.reason != "regime.strategy.bullish_alignment"


def test_strategy_outputs_are_deterministic_and_isolated_from_other_algorithms() -> None:
    snapshot = _snapshot(_trend_rows("down", count=80))
    classification = _classification("strong_downtrend", direction="down", structure="trend")
    first = evaluate_strategy("moving_average_trend", snapshot, classification, {"lifecycle": "active"})
    second = evaluate_strategy("moving_average_trend", snapshot, classification, {"lifecycle": "active"})
    assert first == second
    assert first.signal == "Sell"

    forbidden = ("weighted", "voting", "wca", "meta_strategy", "persistence", "sqlite", "broker")
    for strategy_id in DIRECTIONAL_IDS:
        module = importlib.import_module(f"backend.app.algorithms.regime.strategies.directional.{strategy_id}")
        source = inspect.getsource(module).lower()
        assert not any(token in source for token in forbidden), strategy_id


def _classification(raw_regime: str, *, direction: str, structure: str, bull_score: int = 0, bear_score: int = 0) -> RegimeClassification:
    return RegimeClassification(
        raw_regime=raw_regime,
        axes=RegimeAxes(
            direction=direction,
            volatility="normal",
            structure=structure,
            liquidity="normal",
            session="opening",
            event_risk="clear",
        ),
        confidence=0.8,
        features={
            "bullScore": bull_score,
            "bearScore": bear_score,
            "structureLabel": structure,
            "liquidityBlockNewEntries": False,
            "spreadBps": 1.0,
        },
        evidence={"liquidityEvidence": {"spreadBps": 1.0}},
        missing_inputs=(),
        no_trade_reasons=(),
        timestamp="2026-07-23T14:40:00Z",
    )


def _snapshot(rows: list[dict], context: dict | None = None):
    return build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": rows, "oneMinuteCandles": rows, "contextFeeds": context or {}})


def _timestamp(index: int) -> str:
    minute = 30 + index
    hour = 13 + minute // 60
    return f"2026-07-23T{hour:02d}:{minute % 60:02d}:00Z"


def _trend_rows(direction: str, *, count: int) -> list[dict]:
    rows = []
    price = 100.0
    step = 0.03 if direction == "up" else -0.03
    for index in range(count):
        price += step
        rows.append(_row(index, price - step * 0.5, price + 0.18, price - 0.18, price, 120_000 + index * 100))
    return rows


def _opening_breakout_rows(direction: str, count: int = 50) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        if index < 30:
            price = 100.0 + (0.05 if index % 2 == 0 else -0.05)
        elif direction == "up":
            price = 100.25 + (index - 30) * 0.005
        else:
            price = 99.75 - (index - 30) * 0.005
        volume = 180_000 if index >= count - 2 else 100_000
        rows.append(_row(index, price - 0.04, price + 0.08, price - 0.08, price, volume))
    return rows


def _rsi_reversal_rows(pattern: str) -> list[dict]:
    rows = []
    price = 100.2
    for index in range(70):
        if index < 68:
            price -= 0.025
        else:
            price += 0.04
        if index >= 68:
            rows.append(_row(index, price - 0.02, price + 0.08, price - 0.12, price, 110_000))
        else:
            rows.append(_row(index, price - 0.02, 100.45, 98.25, price, 110_000))
    return rows


def _sweep_rows(side: str) -> list[dict]:
    rows = _opening_breakout_rows("up", count=55)
    if side == "low":
        rows[-1] = _row(54, 100.05, 100.20, 99.65, 100.12, 220_000)
    else:
        rows[-1] = _row(54, 99.95, 100.45, 99.80, 99.90, 220_000)
    return rows


def _gap_rows(pattern: str) -> list[dict]:
    rows = []
    price = 102.0
    for index in range(40):
        price -= 0.025 if pattern == "fade_up" else -0.025
        rows.append(_row(index, price + 0.03, price + 0.12, price - 0.12, price, 130_000))
    return rows


def _row(index: int, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    return {
        "timestamp": _timestamp(index),
        "open": round(open_, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": volume,
    }
