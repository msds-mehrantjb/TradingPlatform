"""Backend-authoritative Regime classifier."""

from __future__ import annotations

from statistics import mean

from backend.app.algorithms.regime.contracts import RegimeAxes, RegimeCandle, RegimeClassification, RegimeMarketSnapshot
from backend.app.algorithms.regime.indicators import atr, ema, macd_histogram, realized_volatility, relative_volume, rsi, vwap


def classify_market_regime(snapshot: RegimeMarketSnapshot) -> RegimeClassification:
    candles = snapshot.candles
    closes = [c.close for c in candles]
    latest = snapshot.latest
    computed_vwap = vwap(candles)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    latest_atr = atr(candles)
    atr_percent = (latest_atr / max(latest.close, 0.01)) if latest_atr is not None else None
    macd = macd_histogram(closes)
    latest_rsi = rsi(closes)
    rel_volume = relative_volume(candles)
    bull_score = sum(
        [
            latest.close > computed_vwap,
            ema20 is not None and latest.close > ema20,
            ema20 is not None and ema50 is not None and ema20 > ema50,
            macd is not None and macd > 0,
            _higher_highs(candles),
        ]
    )
    bear_score = sum(
        [
            latest.close < computed_vwap,
            ema20 is not None and latest.close < ema20,
            ema20 is not None and ema50 is not None and ema20 < ema50,
            macd is not None and macd < 0,
            _lower_lows(candles),
        ]
    )
    missing = tuple(name for name, value in {"ema20": ema20, "ema50": ema50, "atr": latest_atr, "rsi": latest_rsi}.items() if value is None)
    no_trade = _no_trade_reasons(snapshot, atr_percent, rel_volume)
    axes = RegimeAxes(
        direction=_direction_axis(bull_score, bear_score),
        volatility=_volatility_axis(atr_percent, realized_volatility(closes)),
        structure=_structure_axis(snapshot, bull_score, bear_score),
        liquidity=_liquidity_axis(snapshot, rel_volume),
        session=_session_axis(latest.timestamp),
        event_risk=_event_axis(snapshot),
    )
    raw_regime = _composite_regime(axes)
    confidence = _confidence(bull_score, bear_score, missing, no_trade)
    features = {
        "vwap": computed_vwap,
        "ema20": ema20,
        "ema50": ema50,
        "atr": latest_atr,
        "atrPercent": atr_percent,
        "rsi": latest_rsi,
        "macdHistogram": macd,
        "relativeVolume": rel_volume,
        "bullScore": bull_score,
        "bearScore": bear_score,
        "realizedVolatility": realized_volatility(closes),
    }
    evidence = {
        **features,
        "close": latest.close,
        "quoteFreshness": snapshot.context_feeds["quoteFreshness"].get("status"),
        "qqqRelativeStrength": snapshot.context_feeds["qqqRelativeStrength"].get("relativeToPrimaryPercent"),
        "iwmRelativeStrength": snapshot.context_feeds["iwmRelativeStrength"].get("relativeToPrimaryPercent"),
        "marketBreadth": snapshot.context_feeds["marketBreadth"].get("advanceDeclineRatio") or snapshot.context_feeds["marketBreadth"].get("state"),
        "vixState": snapshot.context_feeds["vix"].get("value") or snapshot.context_feeds["vix"].get("state"),
        "esFuturesState": snapshot.context_feeds["esFutures"].get("changePercent") or snapshot.context_feeds["esFutures"].get("trend"),
        "scheduledEventState": snapshot.context_feeds["scheduledEconomicEvent"].get("state"),
        "noTradeReasons": no_trade,
    }
    return RegimeClassification(
        raw_regime=raw_regime,
        axes=axes,
        confidence=confidence,
        features=features,
        evidence=evidence,
        missing_inputs=missing,
        no_trade_reasons=no_trade,
        timestamp=latest.timestamp,
    )


def _direction_axis(bull_score: int, bear_score: int) -> str:
    edge = bull_score - bear_score
    if edge >= 4:
        return "strong_up"
    if edge >= 2:
        return "weak_up"
    if edge <= -4:
        return "strong_down"
    if edge <= -2:
        return "weak_down"
    return "neutral"


def _volatility_axis(atr_percent: float | None, rv: float | None) -> str:
    proxy = atr_percent if atr_percent is not None else rv
    if proxy is None:
        return "normal"
    if proxy >= 0.035:
        return "extreme"
    if proxy >= 0.018:
        return "expanded"
    if proxy <= 0.004:
        return "compressed"
    return "normal"


def _structure_axis(snapshot: RegimeMarketSnapshot, bull_score: int, bear_score: int) -> str:
    candles = snapshot.candles
    if len(candles) >= 6:
        recent_high = max(c.high for c in candles[-6:-1])
        recent_low = min(c.low for c in candles[-6:-1])
        if snapshot.latest.close > recent_high or snapshot.latest.close < recent_low:
            return "breakout"
    if abs(bull_score - bear_score) >= 2:
        return "trend"
    return "range"


def _liquidity_axis(snapshot: RegimeMarketSnapshot, rel_volume: float) -> str:
    spread_percent = float(snapshot.context_feeds.get("quoteFreshness", {}).get("spreadPercent") or 0)
    if snapshot.context_feeds["quoteFreshness"].get("status") == "stale" or spread_percent > 0.03 or rel_volume < 0.45:
        return "poor"
    if rel_volume < 0.75:
        return "acceptable"
    return "good"


def _session_axis(timestamp: str) -> str:
    try:
        hour = int(timestamp[11:13])
        minute = int(timestamp[14:16])
    except (ValueError, IndexError):
        return "midday"
    minutes = hour * 60 + minute
    if minutes < 14 * 60 + 30 or minutes > 21 * 60:
        return "outside_regular"
    if minutes < 15 * 60:
        return "opening"
    if minutes >= 20 * 60 + 30:
        return "closing"
    return "midday" if minutes < 18 * 60 else "afternoon"


def _event_axis(snapshot: RegimeMarketSnapshot) -> str:
    event_state = snapshot.context_feeds["scheduledEconomicEvent"].get("state")
    if event_state == "blackout":
        return "blackout"
    if event_state in {"elevated", "soon"}:
        return "elevated"
    return "none"


def _composite_regime(axes: RegimeAxes) -> str:
    if axes.volatility == "extreme":
        return "extreme_volatility_no_trade"
    if axes.event_risk in {"blackout", "elevated"}:
        return "event_risk"
    if axes.liquidity == "poor":
        return "liquidity_stress"
    if axes.session == "opening" and axes.structure == "breakout":
        return "opening_breakout"
    if axes.structure == "breakout" and axes.volatility == "expanded":
        return "intraday_expansion"
    if axes.volatility == "expanded" and axes.direction in {"strong_up", "strong_down"}:
        return "high_volatility_trend"
    if axes.volatility == "compressed":
        return "low_volatility_quiet"
    if axes.direction == "strong_up":
        return "strong_uptrend"
    if axes.direction == "weak_up":
        return "weak_uptrend"
    if axes.direction == "strong_down":
        return "strong_downtrend"
    if axes.direction == "weak_down":
        return "weak_downtrend"
    if axes.structure == "range":
        return "range_bound"
    return "choppy_mixed"


def _no_trade_reasons(snapshot: RegimeMarketSnapshot, atr_percent: float | None, rel_volume: float) -> tuple[str, ...]:
    reasons: list[str] = []
    if snapshot.context_feeds["quoteFreshness"].get("status") == "stale":
        reasons.append("regime.safety.stale_quote")
    if snapshot.context_feeds["scheduledEconomicEvent"].get("state") == "blackout":
        reasons.append("regime.safety.event_blackout")
    if snapshot.context_feeds["haltLuldCircuitBreaker"].get("newEntriesBlocked"):
        reasons.append("regime.safety.halt_luld_circuit")
    if atr_percent is not None and atr_percent >= 0.035:
        reasons.append("regime.safety.extreme_volatility")
    if rel_volume < 0.45:
        reasons.append("regime.safety.insufficient_liquidity")
    return tuple(reasons)


def _higher_highs(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].high > candles[-2].high > candles[-3].high


def _lower_lows(candles: tuple[RegimeCandle, ...]) -> bool:
    return len(candles) >= 4 and candles[-1].low < candles[-2].low < candles[-3].low


def _confidence(bull_score: int, bear_score: int, missing: tuple[str, ...], no_trade: tuple[str, ...]) -> float:
    score = 0.55 + min(0.25, abs(bull_score - bear_score) * 0.06)
    score -= min(0.20, len(missing) * 0.03)
    if no_trade:
        score = max(score, 0.70)
    return max(0.0, min(1.0, round(score, 4)))
