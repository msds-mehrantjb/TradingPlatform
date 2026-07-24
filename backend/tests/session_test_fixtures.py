from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.algorithms.session import (
    DataQualityState,
    EventRiskState,
    LiquidityState,
    SessionBehavior,
    SessionClassification,
    SessionPhase,
    VolatilityState,
    resolve_session_clock,
)
from backend.app.algorithms.session.state import FINALIZED_ONE_MINUTE_BAR, QUOTE_NBBO_UPDATE


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
NOW = datetime(2026, 7, 23, 14, 5, tzinfo=UTC)


@dataclass(frozen=True)
class GoldenSessionCase:
    name: str
    timestamp: datetime
    expected_phase: SessionPhase
    expected_behavior: SessionBehavior
    expected_direction: str
    expected_volatility: VolatilityState = VolatilityState.NORMAL
    expected_liquidity: LiquidityState = LiquidityState.HEALTHY
    expected_data_quality: DataQualityState = DataQualityState.READY
    expected_event_risk: EventRiskState = EventRiskState.CLEAR
    block_new_entries: bool = False
    reason_fragment: str = "SESSION_"
    inputs: dict[str, Any] | None = None


def golden_axis_cases() -> tuple[GoldenSessionCase, ...]:
    return (
        _case("opening_drive_up", datetime(2026, 7, 23, 13, 33, tzinfo=UTC), SessionPhase.OPENING_DISCOVERY, SessionBehavior.OPENING_DRIVE, "long", reason_fragment="SESSION_OPENING_DRIVE", inputs=_axis_inputs(opening_drive="up", or5_status="building")),
        _case("opening_drive_down", datetime(2026, 7, 23, 13, 33, tzinfo=UTC), SessionPhase.OPENING_DISCOVERY, SessionBehavior.OPENING_DRIVE, "short", reason_fragment="SESSION_OPENING_DRIVE", inputs=_axis_inputs(opening_drive="down", or5_status="building")),
        _case("or_breakout_acceptance", datetime(2026, 7, 23, 13, 45, tzinfo=UTC), SessionPhase.OPENING_RANGE, SessionBehavior.BREAKOUT_UP, "long", VolatilityState.EXPANDING, reason_fragment="SESSION_OR5_BREAK_ACCEPTED", inputs=_axis_inputs(structure_behavior="valid_breakout_up", range_percentile=0.82, rv_percentile=0.60)),
        _case("failed_or_breakout", datetime(2026, 7, 23, 13, 48, tzinfo=UTC), SessionPhase.OPENING_RANGE, SessionBehavior.FAILED_BREAKOUT_UP, "short", reason_fragment="SESSION_FAILED_BREAKOUT", inputs=_axis_inputs(structure_behavior="failed_breakout_up")),
        _case("strong_morning_trend", datetime(2026, 7, 23, 14, 20, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.TREND_UP, "long", reason_fragment="SESSION_DIRECTIONAL_EFFICIENCY_HIGH", inputs=_axis_inputs(structure_behavior="trend_up")),
        _case("midday_compression", datetime(2026, 7, 23, 16, 0, tzinfo=UTC), SessionPhase.MIDDAY, SessionBehavior.COMPRESSION, "neutral", VolatilityState.COMPRESSED, reason_fragment="SESSION_MIDDAY_COMPRESSION", inputs=_axis_inputs(range_percentile=0.20, rv_percentile=0.20)),
        _case("midday_mean_reversion", datetime(2026, 7, 23, 16, 10, tzinfo=UTC), SessionPhase.MIDDAY, SessionBehavior.MEAN_REVERTING, "neutral", reason_fragment="session.behavior.mean_reverting", inputs=_axis_inputs(structure_behavior="mean_reverting")),
        _case("choppy_vwap_rotation", datetime(2026, 7, 23, 15, 0, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.CHOPPY, "neutral", reason_fragment="SESSION_VWAP_ROTATION_HIGH", inputs=_axis_inputs(structure_behavior="choppy", crossing_frequency=8)),
        _case("afternoon_expansion", datetime(2026, 7, 23, 18, 30, tzinfo=UTC), SessionPhase.AFTERNOON, SessionBehavior.EXPANSION, "neutral", VolatilityState.EXPANDING, reason_fragment="SESSION_RANGE_PERCENTILE_EXPANDING", inputs=_axis_inputs(range_percentile=0.80, rv_percentile=0.70)),
        _case("power_hour_trend", datetime(2026, 7, 23, 19, 10, tzinfo=UTC), SessionPhase.POWER_HOUR, SessionBehavior.TREND_UP, "long", reason_fragment="SESSION_DIRECTIONAL_EFFICIENCY_HIGH", inputs=_axis_inputs(structure_behavior="trend_up")),
        _case("event_blackout", datetime(2026, 7, 23, 14, 20, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.EVENT_DRIVEN, "cash", expected_event_risk=EventRiskState.BLACKOUT, block_new_entries=True, reason_fragment="SESSION_EVENT_BLACKOUT", inputs=_axis_inputs(event_blackout=True)),
        _case("liquidity_stress", datetime(2026, 7, 23, 14, 20, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.LIQUIDITY_STRESS, "cash", expected_liquidity=LiquidityState.STRESSED, block_new_entries=True, reason_fragment="SESSION_LIQUIDITY_BLOCK", inputs=_axis_inputs(liquidity_state="stressed", liquidity_block=True)),
        _case("stale_quote", datetime(2026, 7, 23, 14, 20, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.LIQUIDITY_STRESS, "cash", expected_liquidity=LiquidityState.STALE, block_new_entries=True, reason_fragment="SESSION_QUOTE_STALE", inputs=_axis_inputs(liquidity_state="stale", liquidity_block=True)),
        _case("missing_bar", datetime(2026, 7, 23, 14, 20, tzinfo=UTC), SessionPhase.MORNING, SessionBehavior.UNKNOWN, "cash", expected_data_quality=DataQualityState.INCOMPLETE, block_new_entries=True, reason_fragment="SESSION_DATA_NOT_READY", inputs=_axis_inputs(data_quality_state="incomplete", data_block=True)),
        _case("early_close", datetime(2026, 11, 27, 17, 55, tzinfo=UTC), SessionPhase.CLOSING_AUCTION, SessionBehavior.BALANCED_RANGE, "neutral", reason_fragment="SESSION_PHASE_CLOSING_AUCTION", inputs=_axis_inputs()),
        _case("dst_summer", datetime(2026, 7, 23, 13, 30, tzinfo=UTC), SessionPhase.OPENING_AUCTION, SessionBehavior.BALANCED_RANGE, "neutral", reason_fragment="SESSION_PHASE_OPENING_AUCTION", inputs=_axis_inputs()),
        _case("dst_winter", datetime(2026, 1, 20, 14, 30, tzinfo=UTC), SessionPhase.OPENING_AUCTION, SessionBehavior.BALANCED_RANGE, "neutral", reason_fragment="SESSION_PHASE_OPENING_AUCTION", inputs=_axis_inputs()),
    )


def axis_inputs(case: GoldenSessionCase) -> dict[str, Any]:
    inputs = dict(case.inputs or {})
    inputs.setdefault("clock", resolve_session_clock(case.timestamp))
    return inputs


def classification_fixture(
    *,
    phase: SessionPhase = SessionPhase.MORNING,
    behavior: SessionBehavior = SessionBehavior.BALANCED_RANGE,
    volatility: VolatilityState = VolatilityState.NORMAL,
    liquidity: LiquidityState = LiquidityState.HEALTHY,
    data_quality: DataQualityState = DataQualityState.READY,
    event_risk: EventRiskState = EventRiskState.CLEAR,
    block: bool = False,
    decision_time: datetime = NOW,
    confidence: float = 0.75,
) -> SessionClassification:
    return SessionClassification(
        symbol="SPY",
        session_date="2026-07-23",
        exchange_timezone="America/New_York",
        market_event_time=decision_time - timedelta(milliseconds=120),
        feature_snapshot_time=decision_time - timedelta(milliseconds=40),
        decision_time=decision_time,
        valid_until=decision_time + timedelta(seconds=60),
        phase=phase,
        behavior=behavior,
        volatility_state=volatility,
        liquidity_state=liquidity,
        data_quality_state=data_quality,
        event_risk_state=event_risk,
        direction_bias="cash" if block else "neutral",
        phase_confidence=0.9,
        behavior_confidence=confidence,
        volatility_confidence=0.8,
        liquidity_confidence=0.8,
        data_quality_confidence=0.9,
        overall_confidence=confidence,
        safety_block_confidence=0.9 if block else 0.0,
        reason_codes=(f"fixture.{phase.value}.{behavior.value}",),
        evidence={"classificationId": "fixture-classification", "featureSnapshotId": "fixture-snapshot", "baselineVersion": "fixture-baseline"},
        allowed_strategy_families=("trend", "vwap"),
        blocked_strategy_families=(),
        block_new_entries=block,
    )


def golden_candles(name: str, *, quoted: bool = False) -> list[dict[str, Any]]:
    prices_by_name = {
        "opening_drive_up": [100, 100.3, 100.7, 101.0, 101.4, 101.7, 102.0],
        "opening_drive_down": [100, 99.7, 99.3, 99.0, 98.7, 98.4, 98.2],
        "or_breakout_acceptance": [100, 100.1, 99.9, 100.0, 100.05, 100.7, 101.0, 101.2, 101.4, 101.6],
        "failed_or_breakout": [100, 100.1, 99.9, 100.0, 100.05, 100.8, 100.0, 99.8, 99.7, 99.6],
        "strong_morning_trend": [100, 101, 100.6, 102, 101.3, 103, 102.2, 104, 103.1, 105, 104.2, 106, 105.2, 107, 108],
        "midday_compression": [100, 100.03, 99.99, 100.02, 100.0, 100.01, 99.98, 100.02, 100.01, 100.0],
        "midday_mean_reversion": [100, 100.5, 99.7, 100.4, 99.8, 100.3, 99.9, 100.2, 99.95, 100.1],
        "choppy_vwap_rotation": [100, 101, 99, 101.1, 98.9, 101, 99, 101.2, 98.8, 101, 99],
        "afternoon_expansion": [100, 100.05, 100.03, 100.1, 100.4, 101.1, 102.0, 103.0, 104.2, 105.4],
        "power_hour_trend": [100, 100.4, 100.9, 101.5, 102.2, 103.0, 103.7, 104.5, 105.2, 106.0],
        "balanced_range": [100, 100.3, 99.9, 100.2, 99.8, 100.3, 99.9, 100.1, 99.7, 100.2],
    }
    prices = prices_by_name[name]
    width = 2.4 if name == "choppy_vwap_rotation" else 0.2 if "compression" in name else 0.40 if name == "strong_morning_trend" else 0.45
    bars = [_bar(index, price, width=width, volume=100_000 + index * 1000) for index, price in enumerate(prices)]
    if name == "failed_or_breakout":
        bars[5]["high"] = 101.2
    if name == "strong_morning_trend":
        bars = [{**bar, "volume": 1000} for bar in bars]
    if quoted:
        return [with_quote(bar) for bar in bars]
    return bars


def event_stream(length: int = 12, *, duplicate_index: int | None = None, missing_index: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index in range(length):
        if index == missing_index:
            continue
        close = 100 + index * 0.08
        timestamp = SESSION_START + timedelta(minutes=index)
        quote = {
            "type": QUOTE_NBBO_UPDATE,
            "event_id": f"quote-{index}",
            "symbol": "SPY",
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "bid": round(close - 0.005, 4),
            "ask": round(close + 0.005, 4),
        }
        bar = {
            "type": FINALIZED_ONE_MINUTE_BAR,
            "event_id": f"bar-{index}",
            "symbol": "SPY",
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "open": round(close - 0.03, 4),
            "high": round(close + 0.08, 4),
            "low": round(close - 0.07, 4),
            "close": round(close, 4),
            "volume": 100_000 + index * 1_000,
            "finalized": True,
        }
        events.extend([quote, bar])
        if duplicate_index == index:
            events.append(dict(bar))
    return events


def with_quote(candle: dict[str, Any]) -> dict[str, Any]:
    close = float(candle["close"])
    return {
        **candle,
        "bestBid": close - 0.01,
        "bestAsk": close + 0.01,
        "bidSize": 1_000,
        "askSize": 1_000,
        "quoteTimestamp": candle["timestamp"],
        "latestTradeTimestamp": candle["timestamp"],
        "barDollarVolume": close * float(candle.get("volume") or 0),
        "tradeCount": 100,
        "intendedOrderQuantity": 100,
    }


def _case(
    name: str,
    timestamp: datetime,
    phase: SessionPhase,
    behavior: SessionBehavior,
    direction: str,
    volatility: VolatilityState = VolatilityState.NORMAL,
    *,
    expected_liquidity: LiquidityState = LiquidityState.HEALTHY,
    expected_data_quality: DataQualityState = DataQualityState.READY,
    expected_event_risk: EventRiskState = EventRiskState.CLEAR,
    block_new_entries: bool = False,
    reason_fragment: str,
    inputs: dict[str, Any],
) -> GoldenSessionCase:
    return GoldenSessionCase(name, timestamp, phase, behavior, direction, volatility, expected_liquidity, expected_data_quality, expected_event_risk, block_new_entries, reason_fragment, inputs)


def _axis_inputs(
    *,
    opening_drive: str = "inside",
    or5_status: str = "complete",
    structure_behavior: str = "balanced_range",
    range_percentile: float = 0.50,
    rv_percentile: float = 0.50,
    crossing_frequency: float = 0.0,
    liquidity_state: str = "healthy",
    liquidity_block: bool = False,
    data_quality_state: str = "ready",
    data_block: bool = False,
    event_blackout: bool = False,
) -> dict[str, Any]:
    return {
        "data_quality_result": {"state": data_quality_state, "confidence": 0.9 if data_quality_state == "ready" else 0.35, "reasonCodes": (f"session.data.{data_quality_state}",), "blockNewEntries": data_block},
        "opening_range_features": {"references": {"OR5": {"status": or5_status}}, "openingDrive": {"status": "building", "direction": opening_drive}, "reasonCodes": (f"session.opening_range.or5.{or5_status}",)},
        "vwap_features": {"status": "ready", "current": {"position": "above" if structure_behavior.endswith("_up") else "neutral", "crossingFrequencyPerHour": crossing_frequency, "acceptanceAbove": structure_behavior.endswith("_up"), "acceptanceBelow": structure_behavior.endswith("_down")}, "reasonCodes": ("session.vwap.ready",)},
        "volatility_features": {"status": "ready", "rangePercentile": range_percentile, "realizedVolatilityPercentile": rv_percentile, "reasonCodes": ("session.volatility.same_time_ready",)},
        "volume_features": {"status": "ready", "volumePaceRatio": 1.0, "reasonCodes": ("session.volume.same_time_ready",)},
        "liquidity_features": {"status": liquidity_state, "liquidityState": liquidity_state, "blockNewEntries": liquidity_block, "reasonCodes": (f"session.liquidity.{liquidity_state}",)},
        "structure_features": {"behavior": structure_behavior, "reasonCodes": (f"session.structure.behavior.{structure_behavior}",)},
        "event_risk_context": {"riskState": "blackout", "blockNewEntries": True, "reasonCodes": ("event.blackout",)} if event_blackout else None,
    }


def _bar(index: int, close: float, *, width: float, volume: float) -> dict[str, Any]:
    timestamp = SESSION_START + timedelta(minutes=index)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": close,
        "high": close + width / 2,
        "low": close - width / 2,
        "close": close,
        "volume": volume,
    }
