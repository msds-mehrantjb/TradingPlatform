"""Compatibility adapters from Session contracts to legacy market-context shapes."""

from __future__ import annotations

from typing import Any, Callable

from backend.app.algorithms.session.models import LiquidityState, SessionBehavior, SessionClassification, VolatilityState


SESSION_SIGNAL_NAMES = [
    "Opening range 5m",
    "Opening range 15m",
    "Opening range 30m",
    "VWAP",
    "VWAP slope",
    "VWAP crosses",
    "Range vs avg daily range",
    "Realized intraday vol",
    "Directional efficiency",
    "Volume pace vs session avg",
    "Failed breakouts",
    "Liquidity stress",
    "Pullback depth",
    "Same-time volume avg",
]


def session_classification_to_layer_result(
    classification: SessionClassification,
    layer_result_factory: Callable[..., Any],
) -> Any:
    evidence = classification.evidence
    tags = tuple(evidence.get("legacyTags") or ())
    reasons = _legacy_reasons(classification)
    return layer_result_factory(
        layer="session",
        label=_legacy_label(classification.behavior),
        direction_bias=classification.direction_bias,
        volatility=_legacy_volatility(classification.volatility_state),
        confidence=classification.overall_confidence,
        reasons=reasons,
        strategy_tags=list(tags),
        candle_window=evidence.get("candleWindow") or _empty_candle_window(),
        signals=_legacy_signals(classification),
        valid_until=classification.valid_until.isoformat(),
    )


def _legacy_label(behavior: SessionBehavior) -> str:
    return {
        SessionBehavior.BUILDING: "Session Building",
        SessionBehavior.OPENING_DRIVE: "Opening Drive",
        SessionBehavior.TREND_UP: "Trend Day Up",
        SessionBehavior.TREND_DOWN: "Trend Day Down",
        SessionBehavior.CHOPPY: "Choppy Whipsaw Day",
        SessionBehavior.MEAN_REVERTING: "Mean-Reversion Day",
        SessionBehavior.LIQUIDITY_STRESS: "Liquidity Stress Session",
    }.get(behavior, "Balanced Session")


def _legacy_volatility(state: VolatilityState) -> str:
    return {
        VolatilityState.COMPRESSED: "contracting",
        VolatilityState.NORMAL: "normal",
        VolatilityState.EXPANDING: "expanding",
        VolatilityState.EXTREME: "high",
        VolatilityState.UNKNOWN: "normal",
    }[state]


def _legacy_reasons(classification: SessionClassification) -> list[str]:
    behavior = classification.behavior
    volatility = classification.volatility_state
    reasons: list[str] = []
    if behavior == SessionBehavior.BUILDING:
        reasons.append("Need more intraday candles")
    elif behavior == SessionBehavior.OPENING_DRIVE:
        reasons.append("Opening drive is forming before the first opening range completes")
    elif behavior == SessionBehavior.TREND_UP:
        reasons.extend(["Price is above VWAP", "Opening range high is cleared"])
    elif behavior == SessionBehavior.TREND_DOWN:
        reasons.extend(["Price is below VWAP", "Opening range low is cleared"])
    elif behavior == SessionBehavior.CHOPPY:
        reasons.append("Price has crossed VWAP repeatedly")
    elif behavior == SessionBehavior.MEAN_REVERTING:
        reasons.append("Price keeps rotating around VWAP")
    elif behavior == SessionBehavior.LIQUIDITY_STRESS:
        reasons.append("Liquidity stress is blocking new entries")
    else:
        reasons.append("Intraday trend and rotation signals are mixed")
    if volatility == VolatilityState.EXPANDING:
        reasons.append("Recent candle range is expanding")
    elif volatility == VolatilityState.COMPRESSED:
        reasons.append("Recent candle range is contracting")
    if classification.evidence.get("volumePace") is not None and float(classification.evidence["volumePace"]) >= 1.7:
        reasons.append("Recent volume pace is elevated")
    return reasons


def _legacy_signals(classification: SessionClassification) -> list[dict[str, str]]:
    evidence = classification.evidence
    liquidity_signal = str(evidence.get("liquidityStress") or _liquidity_signal(classification.liquidity_state))
    rows = [
        ("Opening range 5m", evidence.get("openingRange5m")),
        ("Opening range 15m", evidence.get("openingRange15m")),
        ("Opening range 30m", evidence.get("openingRange30m")),
        ("VWAP", _money_value(evidence.get("vwap"))),
        ("VWAP slope", _pct_value(evidence.get("vwapSlope"))),
        ("VWAP crosses", _string_value(evidence.get("vwapCrosses"))),
        ("Range vs avg daily range", _multiple_value(evidence.get("rangeVsAverageDailyRange"))),
        ("Realized intraday vol", _pct_value(evidence.get("realizedIntradayVolatility"))),
        ("Directional efficiency", _pct_value(evidence.get("efficiency"))),
        ("Volume pace vs session avg", _multiple_value(evidence.get("volumePace"))),
        ("Failed breakouts", evidence.get("failedBreakouts")),
        ("Liquidity stress", liquidity_signal),
        ("Pullback depth", evidence.get("pullbackDepth")),
        ("Same-time volume avg", evidence.get("sameTimeVolumeAvg")),
    ]
    return [_signal(name, str(value) if value is not None else "NA") for name, value in rows]


def _liquidity_signal(state: LiquidityState) -> str:
    if state == LiquidityState.UNKNOWN:
        return "unknown"
    if state in {LiquidityState.STRESSED, LiquidityState.STALE}:
        return "Active"
    return "Inactive"


def _signal(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value, "status": "na" if value in {"NA", "unknown", "not-ready", "not_ready"} else "ok"}


def _money_value(value: Any) -> str:
    return "NA" if value is None else f"{float(value):.2f}"


def _pct_value(value: Any) -> str:
    return "NA" if value is None else f"{float(value) * 100:.2f}%"


def _multiple_value(value: Any) -> str:
    return "NA" if value is None else f"{float(value):.2f}x"


def _string_value(value: Any) -> str:
    return "NA" if value is None else str(value)


def _empty_candle_window() -> dict[str, Any]:
    return {"timeframe": "1Min", "count": 0, "label": "Today's intraday candles", "start": None, "end": None, "segments": [{"start": None, "end": None}]}
