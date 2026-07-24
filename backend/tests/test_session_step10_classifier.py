from __future__ import annotations

from datetime import UTC, datetime

from backend.app.algorithms.session import DataQualityState, EventRiskState, LiquidityState, SessionBehavior, SessionPhase, VolatilityState, classify_session, classify_session_axes, resolve_session_clock
from backend.app.algorithms.session.classifier import (
    SESSION_CLASSIFICATION_CONFLICT,
    SESSION_EVENT_BLACKOUT,
    SESSION_OR5_BREAK_ACCEPTED,
    SESSION_OR5_NOT_COMPLETE,
    SESSION_QUOTE_STALE,
    SESSION_RANGE_PERCENTILE_EXPANDING,
    SESSION_VWAP_ROTATION_HIGH,
)


def test_session_step10_opening_drive_allowed_before_or5_completion() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 13, 33, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="building", opening_drive_direction="up"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "valid_breakout_up", "reasonCodes": ()},
    )

    assert result.phase == SessionPhase.OPENING_DISCOVERY
    assert result.behavior == SessionBehavior.OPENING_DRIVE
    assert result.direction_bias == "long"
    assert SESSION_OR5_NOT_COMPLETE in result.reason_codes
    assert SESSION_OR5_BREAK_ACCEPTED not in result.reason_codes


def test_session_step10_or5_accepted_breakout_is_phase_aware() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 13, 45, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(range_percentile=0.80, rv_percentile=0.65),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "valid_breakout_up", "reasonCodes": ("session.structure.breakout.valid",)},
    )

    assert result.phase == SessionPhase.OPENING_RANGE
    assert result.behavior == SessionBehavior.BREAKOUT_UP
    assert result.direction_bias == "long"
    assert result.volatility_state == VolatilityState.EXPANDING
    assert SESSION_OR5_BREAK_ACCEPTED in result.reason_codes
    assert SESSION_RANGE_PERCENTILE_EXPANDING in result.reason_codes


def test_session_step10_midday_compression_does_not_use_opening_breakout_thresholds() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 16, 0, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(range_percentile=0.20, rv_percentile=0.20),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "valid_breakout_up", "reasonCodes": ("session.structure.breakout.valid",)},
    )

    assert result.phase == SessionPhase.MIDDAY
    assert result.behavior == SessionBehavior.COMPRESSION
    assert result.volatility_state == VolatilityState.COMPRESSED
    assert result.direction_bias == "neutral"


def test_session_step10_closing_auction_blocks_late_opening_breakout_behavior() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 19, 50, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "valid_breakout_up", "reasonCodes": ("session.structure.breakout.valid",)},
    )

    assert result.phase == SessionPhase.CLOSING_AUCTION
    assert result.behavior == SessionBehavior.BALANCED_RANGE
    assert result.behavior != SessionBehavior.BREAKOUT_UP


def test_session_step10_stale_quote_is_separate_from_confidence_boost() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 14, 20, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(state="stale", block=True),
        structure_features={"behavior": "trend_up", "reasonCodes": ("session.structure.behavior.trend_up",)},
    )

    assert result.liquidity_state == LiquidityState.STALE
    assert result.behavior == SessionBehavior.LIQUIDITY_STRESS
    assert result.block_new_entries is True
    assert result.safety_block_confidence > result.overall_confidence
    assert result.behavior_confidence <= 0.5
    assert SESSION_QUOTE_STALE in result.reason_codes


def test_session_step10_event_blackout_is_its_own_axis() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 14, 20, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "trend_up", "reasonCodes": ("session.structure.behavior.trend_up",)},
        event_risk_context={"riskState": "blackout", "blockNewEntries": True},
    )

    assert result.event_risk_state == EventRiskState.BLACKOUT
    assert result.behavior == SessionBehavior.EVENT_DRIVEN
    assert result.direction_bias == "cash"
    assert result.block_new_entries is True
    assert SESSION_EVENT_BLACKOUT in result.reason_codes
    assert result.safety_block_confidence > result.overall_confidence


def test_session_step10_contradictory_structure_and_vwap_reduce_confidence() -> None:
    clean = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 14, 20, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="above"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "trend_up", "reasonCodes": ("session.structure.behavior.trend_up",)},
    )
    conflicted = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 14, 20, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="below"),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "trend_up", "reasonCodes": ("session.structure.behavior.trend_up",)},
    )

    assert conflicted.behavior == SessionBehavior.TREND_UP
    assert SESSION_CLASSIFICATION_CONFLICT in conflicted.reason_codes
    assert conflicted.overall_confidence < clean.overall_confidence


def test_session_step10_warming_up_is_explicit_not_unknown_healthy() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 13, 34, tzinfo=UTC)),
        data_quality_result=_data_quality(state="warming_up", confidence=0.45, block=False),
        opening_range_features=_opening(or5_status="building", opening_drive_direction="inside"),
        vwap_features={"status": "not_ready", "current": None},
        volatility_features={"status": "unknown"},
        volume_features={"status": "unknown"},
        liquidity_features=_liquidity(state="unknown", block=True),
        structure_features={"behavior": "unknown", "reasonCodes": ()},
    )

    assert result.data_quality_state == DataQualityState.WARMING_UP
    assert result.behavior == SessionBehavior.BUILDING
    assert result.liquidity_state == LiquidityState.UNKNOWN
    assert result.block_new_entries is True


def test_session_step10_runtime_classification_exposes_event_axis() -> None:
    candles = [_candle(index) for index in range(30)]
    classification = classify_session(
        "SPY",
        candles,
        event_risk_context={"riskState": "blackout", "blockNewEntries": True, "reasonCodes": ("event.test",)},
    )

    assert classification.event_risk_state == EventRiskState.BLACKOUT
    assert classification.behavior == SessionBehavior.EVENT_DRIVEN
    assert classification.block_new_entries is True
    assert classification.safety_block_confidence > classification.overall_confidence


def test_session_step10_vwap_rotation_reason_is_not_display_string_logic() -> None:
    result = classify_session_axes(
        clock=resolve_session_clock(datetime(2026, 7, 23, 15, 0, tzinfo=UTC)),
        data_quality_result=_data_quality(),
        opening_range_features=_opening(or5_status="complete"),
        vwap_features=_vwap(position="neutral", crossing_frequency=8),
        volatility_features=_volatility(),
        volume_features=_volume(),
        liquidity_features=_liquidity(),
        structure_features={"behavior": "balanced_range", "reasonCodes": ()},
    )

    assert result.behavior == SessionBehavior.CHOPPY
    assert SESSION_VWAP_ROTATION_HIGH in result.reason_codes


def _data_quality(*, state: str = "ready", confidence: float = 0.9, block: bool = False) -> dict[str, object]:
    return {"state": state, "confidence": confidence, "reasonCodes": ("session.data.ready",), "blockNewEntries": block}


def _opening(*, or5_status: str, opening_drive_direction: str = "inside") -> dict[str, object]:
    return {
        "references": {"OR5": {"status": or5_status}},
        "openingDrive": {"status": "building" if or5_status != "complete" else "complete", "direction": opening_drive_direction},
        "reasonCodes": (f"session.opening_range.or5.{or5_status}",),
    }


def _vwap(*, position: str, crossing_frequency: float = 0.0) -> dict[str, object]:
    return {
        "status": "ready",
        "current": {
            "position": position,
            "crossingFrequencyPerHour": crossing_frequency,
            "acceptanceAbove": position == "above",
            "acceptanceBelow": position == "below",
        },
        "reasonCodes": ("session.vwap.ready",),
    }


def _volatility(*, range_percentile: float = 0.50, rv_percentile: float = 0.50) -> dict[str, object]:
    return {
        "status": "ready",
        "rangePercentile": range_percentile,
        "realizedVolatilityPercentile": rv_percentile,
        "reasonCodes": ("session.volatility.same_time_ready",),
    }


def _volume() -> dict[str, object]:
    return {"status": "ready", "volumePaceRatio": 1.0, "reasonCodes": ("session.volume.same_time_ready",)}


def _liquidity(*, state: str = "healthy", block: bool = False) -> dict[str, object]:
    return {
        "status": state,
        "liquidityState": state,
        "blockNewEntries": block,
        "reasonCodes": (f"session.liquidity.{state}",),
    }


def _candle(index: int) -> dict[str, object]:
    close = 100 + index * 0.02
    timestamp = datetime(2026, 7, 23, 13, 30 + index, tzinfo=UTC)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": close - 0.03,
        "high": close + 0.08,
        "low": close - 0.07,
        "close": close,
        "volume": 100_000,
        "bestBid": close - 0.01,
        "bestAsk": close + 0.01,
        "bidSize": 1000,
        "askSize": 1000,
        "quoteTimestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "tradeCount": 100,
    }
