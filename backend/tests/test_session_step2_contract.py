from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.algorithms.session import (
    DEFAULT_SESSION_CONFIG,
    SESSION_CLASSIFIER_VERSION,
    SESSION_FEATURE_SCHEMA_VERSION,
    DataQualityState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionConfig,
    SessionPhase,
    VolatilityState,
    classify_session,
)
from backend.app.market_context import compute_market_context


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


def test_session_step2_enums_are_authoritative_and_serializable() -> None:
    assert {item.value for item in SessionPhase} == {
        "premarket",
        "opening_auction",
        "opening_discovery",
        "opening_range",
        "morning",
        "midday",
        "afternoon",
        "power_hour",
        "closing_auction",
        "postmarket",
        "closed",
        "unknown",
    }
    assert {item.value for item in SessionBehavior} >= {"building", "trend_up", "balanced_range", "liquidity_stress", "unknown"}
    assert {item.value for item in VolatilityState} == {"compressed", "normal", "expanding", "extreme", "unknown"}
    assert {item.value for item in LiquidityState} == {"healthy", "constrained", "stressed", "stale", "unknown"}
    assert {item.value for item in DataQualityState} == {"ready", "warming_up", "incomplete", "stale", "invalid"}

    classification = classify_session("spy", [_candle(index, bid=100 + index * 0.02, ask=100 + index * 0.02 + 0.01, quoteAgeMs=250) for index in range(30)], _daily_bars())
    payload = classification.model_dump(mode="json")

    assert payload["symbol"] == "SPY"
    assert payload["classifier_version"] == SESSION_CLASSIFIER_VERSION
    assert payload["feature_schema_version"] == SESSION_FEATURE_SCHEMA_VERSION
    assert payload["phase"] == SessionPhase.OPENING_RANGE.value
    assert payload["behavior"] == SessionBehavior.BALANCED_RANGE.value
    assert payload["liquidity_state"] == LiquidityState.HEALTHY.value
    assert classification.deterministic_hash() == classification.deterministic_hash()


def test_session_step2_phase_and_behavior_are_separate_dimensions() -> None:
    opening = classify_session("SPY", [_candle(index) for index in range(30)], _daily_bars())
    full = classify_session("SPY", [_candle(index) for index in range(390)], _daily_bars())

    assert opening.phase == SessionPhase.OPENING_RANGE
    assert opening.behavior == SessionBehavior.BALANCED_RANGE
    assert full.phase == SessionPhase.CLOSING_AUCTION
    assert full.behavior == SessionBehavior.TREND_UP


def test_session_step2_config_owns_thresholds_and_time_windows() -> None:
    config = SessionConfig(minimum_behavior_bars=12, opening_range_minutes=20)

    assert config.config_version == DEFAULT_SESSION_CONFIG.config_version
    assert config.as_dict()["minimum_behavior_bars"] == 12
    assert config.as_dict()["opening_range_minutes"] == 20
    assert config.configuration_hash == config.configuration_hash


def test_session_step2_validation_rejects_bad_confidence_and_timestamps() -> None:
    kwargs = _classification_kwargs()

    with pytest.raises(ValidationError):
        SessionClassification(**{**kwargs, "overall_confidence": 1.1})

    with pytest.raises(ValidationError):
        SessionClassification(**{**kwargs, "decision_time": datetime(2026, 7, 23, 13, 59)})

    with pytest.raises(ValidationError):
        SessionClassification(**{**kwargs, "valid_until": kwargs["decision_time"] - timedelta(seconds=1)})

    with pytest.raises(ValidationError):
        SessionClassification(**{key: value for key, value in kwargs.items() if key != "reason_codes"})


def test_session_step2_market_context_uses_compatibility_adapter_without_api_break() -> None:
    context = compute_market_context("SPY", _daily_bars(), [_candle(index) for index in range(30)])
    session = context["session"]

    assert session["layer"] == "session"
    assert session["label"] == "Balanced Session"
    assert session["directionBias"] == "neutral"
    assert session["candleWindow"]["timeframe"] == "1Min"
    assert [signal["name"] for signal in session["signals"]][:3] == ["Opening range 5m", "Opening range 15m", "Opening range 30m"]
    assert _signal_value(session, "Liquidity stress") == "unknown"


def test_session_step2_missing_quote_evidence_blocks_new_entries_in_typed_contract() -> None:
    classification = classify_session("SPY", [_candle(index) for index in range(30)], _daily_bars())

    assert classification.liquidity_state == LiquidityState.UNKNOWN
    assert classification.block_new_entries is True
    assert "session.liquidity.unknown" in classification.reason_codes


def test_session_step2_other_algorithm_packages_do_not_import_session_internals() -> None:
    root = Path("backend/app/algorithms")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "session" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("backend.app.algorithms.session."):
                    offenders.append(f"{path}:{module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("backend.app.algorithms.session."):
                        offenders.append(f"{path}:{alias.name}")
    assert offenders == []


def _classification_kwargs() -> dict[str, object]:
    now = SESSION_START + timedelta(minutes=30)
    return {
        "symbol": "SPY",
        "session_date": "2026-07-23",
        "exchange_timezone": "America/New_York",
        "market_event_time": now,
        "feature_snapshot_time": now,
        "decision_time": now,
        "valid_until": now + timedelta(seconds=60),
        "phase": SessionPhase.OPENING_RANGE,
        "behavior": SessionBehavior.BALANCED_RANGE,
        "volatility_state": VolatilityState.NORMAL,
        "liquidity_state": LiquidityState.UNKNOWN,
        "data_quality_state": DataQualityState.READY,
        "direction_bias": "neutral",
        "phase_confidence": 0.9,
        "behavior_confidence": 0.7,
        "volatility_confidence": 0.8,
        "liquidity_confidence": 0.3,
        "data_quality_confidence": 0.9,
        "overall_confidence": 0.3,
        "reason_codes": ("session.test",),
        "evidence": {"barCount": 30},
        "allowed_strategy_families": ("trend",),
        "blocked_strategy_families": (),
        "block_new_entries": True,
    }


def _daily_bars(count: int = 80) -> list[dict[str, object]]:
    return [
        {
            **_candle(index, volume=1_000_000),
            "timestamp": (SESSION_START - timedelta(days=count - index)).isoformat().replace("+00:00", "Z"),
        }
        for index in range(count)
    ]


def _candle(index: int, *, volume: int = 100_000, **extra: object) -> dict[str, object]:
    close = 100 + index * 0.02
    candle = {
        "timestamp": (SESSION_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
        "open": close - 0.03,
        "high": close + 0.08,
        "low": close - 0.07,
        "close": close,
        "volume": volume,
    }
    candle.update(extra)
    return candle


def _signal_value(layer: dict, name: str) -> str:
    return next(signal["value"] for signal in layer["signals"] if signal["name"] == name)
