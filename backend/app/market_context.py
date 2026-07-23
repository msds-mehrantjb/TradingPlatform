from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt
from statistics import mean
from typing import Any, Literal


LayerName = Literal["regime", "session", "event"]
Bias = Literal["long", "short", "neutral", "cash"]
Volatility = Literal["low", "normal", "high", "expanding", "contracting"]

REGIME_SIGNAL_NAMES = [
    "SMA20",
    "SMA50",
    "SMA100",
    "SMA200",
    "20D return",
    "60D return",
    "120D return",
    "250D return",
    "60D drawdown",
    "250D drawdown",
    "10D realized vol",
    "20D realized vol",
    "30D realized vol",
    "ATR%",
    "20D vol percentile",
    "ATR% percentile",
    "50DMA slope",
    "200DMA slope",
    "Price vs MAs",
]

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

EVENT_SIGNAL_NAMES = [
    "Previous close vs open",
    "Premarket high/low",
    "First 5m range",
    "First 15m range",
    "First 30m range",
    "Last 30m flow",
    "Last 60m flow",
    "Abnormal volume vs same-time avg",
    "Large candle body/range spike",
    "News/event flag",
    "Liquidity stress",
    "Opening range breakout",
    "Gap direction",
]

STRATEGY_CLASSIFICATION: dict[str, dict[str, str]] = {
    "Multi-Timeframe Trend Alignment": {"role": "directional", "family": "trend"},
    "First Pullback After Open": {"role": "directional", "family": "trend"},
    "Failed Breakout Strategy": {"role": "directional", "family": "reversal"},
    "Liquidity Sweep Reversal": {"role": "directional", "family": "reversal"},
    "Moving Average Trend": {"role": "directional", "family": "trend"},
    "Trend Pullback Strategy": {"role": "directional", "family": "trend"},
    "RSI Mean Reversion": {"role": "directional", "family": "mean_reversion"},
    "Bollinger Band Mean Reversion": {"role": "directional", "family": "mean_reversion"},
    "Opening Range Breakout": {"role": "directional", "family": "breakout"},
    "Intraday Breakout Strategy": {"role": "directional", "family": "breakout"},
    "MACD Momentum": {"role": "directional", "family": "trend"},
    "Market Structure Strategy": {"role": "directional", "family": "trend"},
    "Gap Continuation / Gap Fade": {"role": "directional", "family": "event"},
    "VWAP Trend Continuation": {"role": "directional", "family": "vwap"},
    "VWAP Mean Reversion": {"role": "directional", "family": "mean_reversion"},
    "Failed Breakout Reversal": {"role": "directional", "family": "reversal"},
    "Bollinger/ATR Reversion": {"role": "directional", "family": "mean_reversion"},
    "Volatility Breakout": {"role": "directional", "family": "breakout"},
    "Relative Strength vs QQQ/IWM": {"role": "context", "family": "market_regime"},
    "Market Breadth Momentum": {"role": "context", "family": "market_regime"},
    "Economic Event Context": {"role": "context", "family": "event"},
    "VWAP Position Strategy": {"role": "context", "family": "vwap"},
    "Volume Confirmation": {"role": "context", "family": "volume_confirmation"},
    "ADX/ATR Regime Classifier": {"role": "regime", "family": "market_regime"},
    "Cash / Avoid Trading Filter": {"role": "safety", "family": "safety"},
    "Breakout Strategy": {"role": "directional", "family": "breakout"},
    "MACD Strategy": {"role": "directional", "family": "trend"},
}


@dataclass(frozen=True)
class LayerResult:
    layer: LayerName
    label: str
    direction_bias: Bias
    volatility: Volatility
    confidence: float
    reasons: list[str]
    strategy_tags: list[str]
    candle_window: dict[str, Any]
    signals: list[dict[str, str]] = field(default_factory=list)
    valid_until: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "label": self.label,
            "directionBias": self.direction_bias,
            "volatility": self.volatility,
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons[:4],
            "strategyTags": self.strategy_tags,
            "candleWindow": self.candle_window,
            "signals": self.signals,
            "validUntil": self.valid_until,
        }


def compute_market_context(symbol: str, daily: list[dict], intraday: list[dict]) -> dict[str, Any]:
    daily_bars = sorted(daily, key=lambda candle: candle["timestamp"])
    intraday_bars = _latest_session(sorted(intraday, key=lambda candle: candle["timestamp"]))

    regime = _compute_regime(daily_bars)
    session = _compute_session(intraday_bars, daily_bars)
    event = _compute_event(daily_bars, intraday_bars)
    strategies = _score_strategies(regime, session, event)

    latest_timestamp = None
    if intraday_bars:
        latest_timestamp = intraday_bars[-1]["timestamp"]
    elif daily_bars:
        latest_timestamp = daily_bars[-1]["timestamp"]

    return {
        "symbol": symbol,
        "updatedAt": latest_timestamp,
        "regime": regime.as_dict(),
        "session": session.as_dict(),
        "event": event.as_dict(),
        "strategies": strategies,
    }


def _compute_regime(candles: list[dict]) -> LayerResult:
    if len(candles) < 60:
        return LayerResult(
            layer="regime",
            label="Insufficient Daily Data",
            direction_bias="neutral",
            volatility="normal",
            confidence=0.2,
            reasons=["Need at least 60 daily candles"],
            strategy_tags=["reduce-size"],
            candle_window=_candle_window("1Day", candles, "Need 60 daily candles"),
            signals=_na_signals(REGIME_SIGNAL_NAMES),
        )

    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    latest = closes[-1]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma100 = _sma(closes, 100)
    sma200 = _sma(closes, 200)
    ret20 = _return(closes, 20)
    ret60 = _return(closes, 60)
    high250 = max(highs[-250:]) if len(highs) >= 250 else max(highs)
    high60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    drawdown = (latest - high250) / high250 if high250 else 0
    drawdown60 = (latest - high60) / high60 if high60 else 0
    ret120 = _return(closes, 120)
    ret250 = _return(closes, 250)
    vol10 = _realized_vol(closes, 10)
    vol20 = _realized_vol(closes, 20)
    vol30 = _realized_vol(closes, 30)
    vol60 = _realized_vol(closes, 60)
    atr_pct = _atr_percent(candles, 14)
    vol20_percentile = _rolling_percentile(closes, 20, _realized_vol(closes, 20))
    atr_percentile = _rolling_atr_percentile(candles, 14, atr_pct)
    slope50 = _slope(closes, 50)
    slope200 = _slope(closes, 200)
    signals = [
        _signal("SMA20", _money_value(sma20)),
        _signal("SMA50", _money_value(sma50)),
        _signal("SMA100", _money_value(sma100)),
        _signal("SMA200", _money_value(sma200)),
        _signal("20D return", _pct_value(ret20)),
        _signal("60D return", _pct_value(ret60)),
        _signal("120D return", _pct_value(ret120, available=len(closes) > 120)),
        _signal("250D return", _pct_value(ret250, available=len(closes) > 250)),
        _signal("60D drawdown", _pct_value(drawdown60)),
        _signal("250D drawdown", _pct_value(drawdown)),
        _signal("10D realized vol", _pct_value(vol10)),
        _signal("20D realized vol", _pct_value(vol20)),
        _signal("30D realized vol", _pct_value(vol30)),
        _signal("ATR%", _pct_value(atr_pct)),
        _signal("20D vol percentile", _pct_value(vol20_percentile)),
        _signal("ATR% percentile", _pct_value(atr_percentile)),
        _signal("50DMA slope", _pct_value(slope50, available=len(closes) > 50)),
        _signal("200DMA slope", _pct_value(slope200, available=len(closes) > 200)),
        _signal("Price vs MAs", _price_location(latest, [("20", sma20), ("50", sma50), ("100", sma100), ("200", sma200)])),
    ]

    reasons: list[str] = []
    tags: list[str] = []
    label = "Neutral Regime"
    bias: Bias = "neutral"

    above200 = sma200 is not None and latest > sma200
    above50 = sma50 is not None and latest > sma50
    stacked_up = sma20 is not None and sma50 is not None and sma20 > sma50
    long_stack = sma50 is not None and sma200 is not None and sma50 > sma200

    if drawdown <= -0.2 and not above200:
        label = "Bear Regime"
        bias = "short"
        tags.extend(["short-bias", "cash-filter"])
        reasons.append("Drawdown exceeds 20%")
    elif drawdown <= -0.08 and (not above50 or ret20 < 0):
        label = "Correction Regime"
        bias = "cash"
        tags.extend(["cash-filter", "mean-reversion"])
        reasons.append("Drawdown exceeds 8%")
    elif above50 and ret20 > 0 and -0.2 < drawdown <= -0.04:
        label = "Recovery Regime"
        bias = "long"
        tags.extend(["long-bias", "recovery"])
        reasons.append("Reclaiming short-term trend after drawdown")
    elif above200 and long_stack and slope200 >= 0:
        label = "Bull Regime"
        bias = "long"
        tags.extend(["long-bias", "trend-follow"])
        reasons.append("Price is above the 200DMA")
    else:
        tags.append("balanced")
        reasons.append("Trend filters are mixed")

    if stacked_up and ret20 > 0.02 and slope50 > 0:
        label = "Strong Uptrend" if label in {"Neutral Regime", "Bull Regime"} else label
        tags.append("strong-uptrend")
        reasons.append("20DMA is above 50DMA with positive momentum")
    elif above50 and ret20 > 0:
        tags.append("weak-uptrend")
        reasons.append("Price is above 50DMA")
    elif sma20 is not None and sma50 is not None and latest < sma20 < sma50 and ret20 < 0:
        label = "Strong Downtrend" if label == "Neutral Regime" else label
        bias = "short" if bias == "neutral" else bias
        tags.append("strong-downtrend")
        reasons.append("Price is below falling short-term averages")

    vol_state: Volatility = "normal"
    if vol60 and vol20 > vol60 * 1.25:
        vol_state = "high"
        tags.append("high-volatility")
        reasons.append("20-day realized volatility is elevated")
    elif vol60 and vol20 < vol60 * 0.75:
        vol_state = "low"
        tags.append("low-volatility")
        reasons.append("20-day realized volatility is muted")

    confidence = _confidence(
        [
            above200,
            above50,
            long_stack,
            abs(drawdown) < 0.08 if bias == "long" else abs(drawdown) >= 0.08,
            abs(ret60) > 0.02,
            bool(vol60),
        ],
        floor=0.48,
    )

    if sma100 is not None and latest > sma100 and "Price is above 50DMA" not in reasons:
        reasons.append("Price is above 100DMA")

    return LayerResult(
        layer="regime",
        label=label,
        direction_bias=bias,
        volatility=vol_state,
        confidence=confidence,
        reasons=reasons,
        strategy_tags=_unique(tags),
        candle_window=_candle_window("1Day", candles[-250:], "Last 250 daily candles"),
        signals=signals,
    )


def _compute_session(candles: list[dict], daily: list[dict]) -> LayerResult:
    if len(candles) < 10:
        return LayerResult(
            layer="session",
            label="Session Building",
            direction_bias="neutral",
            volatility="normal",
            confidence=0.25,
            reasons=["Need more intraday candles"],
            strategy_tags=["wait"],
            candle_window=_candle_window("1Min", candles, "Today's intraday candles"),
            signals=_na_signals(SESSION_SIGNAL_NAMES),
        )

    first = candles[0]
    latest = candles[-1]
    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]
    session_high = max(highs)
    session_low = min(lows)
    session_range = max(session_high - session_low, 0.01)
    move = float(latest["close"]) - float(first["open"])
    efficiency = abs(move) / session_range
    vwap = _session_vwap(candles)
    vwap_slope = _vwap_slope(candles)
    vwap_crosses = _level_crosses(closes, vwap) if vwap else 0
    recent_vol = _average_range(candles[-15:])
    base_vol = _average_range(candles[:-15] or candles)
    avg_daily_range = _average_daily_range(daily, 20)
    range_vs_avg_daily = session_range / avg_daily_range if avg_daily_range else None
    realized_intraday_vol = _intraday_realized_vol(closes)
    opening = candles[: min(30, len(candles))]
    opening_5 = candles[: min(5, len(candles))]
    opening_15 = candles[: min(15, len(candles))]
    opening_high = max(float(candle["high"]) for candle in opening)
    opening_low = min(float(candle["low"]) for candle in opening)
    above_vwap = bool(vwap and float(latest["close"]) > vwap)
    below_vwap = bool(vwap and float(latest["close"]) < vwap)
    volume_pace = _volume_pace(volumes)
    failed_breakouts = _failed_breakouts(candles, opening_high, opening_low)
    liquidity_stress = _liquidity_stress(candles)
    signals = [
        _signal("Opening range 5m", _range_value(opening_5)),
        _signal("Opening range 15m", _range_value(opening_15)),
        _signal("Opening range 30m", _range_value(opening)),
        _signal("VWAP", _money_value(vwap)),
        _signal("VWAP slope", _pct_value(vwap_slope)),
        _signal("VWAP crosses", str(vwap_crosses)),
        _signal("Range vs avg daily range", _multiple_value(range_vs_avg_daily)),
        _signal("Realized intraday vol", _pct_value(realized_intraday_vol)),
        _signal("Directional efficiency", _pct_value(efficiency)),
        _signal("Volume pace vs session avg", _multiple_value(volume_pace)),
        _signal("Failed breakouts", failed_breakouts),
        _signal("Liquidity stress", liquidity_stress),
        _signal("Pullback depth", "NA"),
        _signal("Same-time volume avg", "NA"),
    ]

    reasons: list[str] = []
    tags: list[str] = []
    label = "Balanced Session"
    bias: Bias = "neutral"
    volatility: Volatility = "normal"

    if efficiency > 0.62 and above_vwap and float(latest["close"]) > opening_high:
        label = "Trend Day Up"
        bias = "long"
        tags.extend(["trend-day", "long-bias", "above-vwap"])
        reasons.extend(["Price is above VWAP", "Opening range high is cleared"])
    elif efficiency > 0.62 and below_vwap and float(latest["close"]) < opening_low:
        label = "Trend Day Down"
        bias = "short"
        tags.extend(["trend-day", "short-bias", "below-vwap"])
        reasons.extend(["Price is below VWAP", "Opening range low is cleared"])
    elif vwap_crosses >= 5 and efficiency < 0.38:
        label = "Choppy Whipsaw Day"
        tags.extend(["chop", "avoid-breakout"])
        reasons.append("Price has crossed VWAP repeatedly")
    elif efficiency < 0.42 and vwap_crosses >= 2:
        label = "Mean-Reversion Day"
        tags.extend(["mean-reversion", "vwap-reversion"])
        reasons.append("Price keeps rotating around VWAP")
    else:
        reasons.append("Intraday trend and rotation signals are mixed")
        tags.append("balanced")

    if recent_vol > base_vol * 1.35:
        volatility = "expanding"
        tags.append("volatility-expansion")
        reasons.append("Recent candle range is expanding")
    elif recent_vol < base_vol * 0.7:
        volatility = "contracting"
        tags.append("volatility-contraction")
        reasons.append("Recent candle range is contracting")

    if sum(volumes[-10:]) > (sum(volumes[:-10]) / max(len(volumes[:-10]), 1)) * 10 * 1.7:
        tags.append("liquidity-watch")
        reasons.append("Recent volume pace is elevated")

    confidence = _confidence(
        [
            efficiency > 0.5,
            vwap_crosses <= 2 if "trend-day" in tags else vwap_crosses >= 2,
            abs(vwap_slope) > 0.01,
            recent_vol != base_vol,
            len(candles) >= 30,
        ],
        floor=0.42,
    )

    return LayerResult(
        layer="session",
        label=label,
        direction_bias=bias,
        volatility=volatility,
        confidence=confidence,
        reasons=reasons,
        strategy_tags=_unique(tags),
        candle_window=_candle_window("1Min", candles, "Today's 1-minute candles"),
        signals=signals,
    )


def _compute_event(daily: list[dict], candles: list[dict]) -> LayerResult:
    if len(candles) < 5:
        return LayerResult(
            layer="event",
            label="No Event Confirmed",
            direction_bias="neutral",
            volatility="normal",
            confidence=0.25,
            reasons=["Waiting for opening/event window"],
            strategy_tags=["normal-rules"],
            candle_window=_candle_window("1Min", candles, "Need first 5 candles"),
            signals=_na_signals(EVENT_SIGNAL_NAMES),
        )

    previous_close = float(daily[-2]["close"]) if len(daily) >= 2 else float(candles[0]["open"])
    first = candles[0]
    latest = candles[-1]
    first_open = float(first["open"])
    latest_close = float(latest["close"])
    gap_pct = (first_open - previous_close) / previous_close if previous_close else 0
    opening_5 = candles[: min(5, len(candles))]
    opening_15 = candles[: min(15, len(candles))]
    opening_30 = candles[: min(30, len(candles))]
    event_candles = _unique_candles(opening_15 + candles[-5:])
    opening_high = max(float(candle["high"]) for candle in opening_15)
    opening_low = min(float(candle["low"]) for candle in opening_15)
    recent_range = _average_range(candles[-5:])
    base_range = _average_range(candles[:-5] or candles)
    recent_volume = sum(float(candle["volume"]) for candle in candles[-5:])
    base_volume = sum(float(candle["volume"]) for candle in candles[:-5]) / max(len(candles[:-5]), 1) * 5
    range_volume_spike = recent_range > base_range * 1.8 and recent_volume > base_volume * 1.8
    breakout = latest_close > opening_high or latest_close < opening_low
    liquidity_stress = _liquidity_stress(candles)
    body_spike = _large_body_range_spike(candles)

    label = "Normal Event Window"
    bias: Bias = "neutral"
    volatility: Volatility = "normal"
    reasons: list[str] = []
    tags: list[str] = ["normal-rules"]
    gap_direction = "Flat"

    if gap_pct >= 0.006:
        label = "Gap-Up Open"
        bias = "long"
        gap_direction = "Gap up"
        tags.extend(["gap-up", "event-rules"])
        reasons.append("Opening price is above prior close")
    elif gap_pct <= -0.006:
        label = "Gap-Down Open"
        bias = "short"
        gap_direction = "Gap down"
        tags.extend(["gap-down", "event-rules"])
        reasons.append("Opening price is below prior close")

    if len(candles) >= 15 and latest_close > opening_high:
        label = "Opening Range Breakout Up"
        bias = "long"
        tags.extend(["orb", "long-bias", "event-rules"])
        reasons.append("15-minute opening range high is broken")
    elif len(candles) >= 15 and latest_close < opening_low:
        label = "Opening Range Breakdown"
        bias = "short"
        tags.extend(["orb", "short-bias", "event-rules"])
        reasons.append("15-minute opening range low is broken")

    if range_volume_spike:
        label = "News-Driven Market" if label == "Normal Event Window" else label
        volatility = "expanding"
        tags.extend(["news-risk", "liquidity-stress"])
        reasons.append("Range and volume are spiking together")
    elif recent_range < base_range * 0.65:
        volatility = "contracting"
        tags.append("compression")
        reasons.append("Short-term range is compressing")

    if not reasons:
        reasons.append("No gap, range break, or liquidity alert is active")

    confidence = _confidence(
        [
            abs(gap_pct) >= 0.006,
            len(candles) >= 15,
            latest_close > opening_high or latest_close < opening_low,
            recent_range != base_range,
            len(opening_5) >= 5,
        ],
        floor=0.38,
    )
    signals = [
        _signal("Previous close vs open", _pct_value(gap_pct, available=len(daily) >= 2)),
        _signal("Premarket high/low", "NA"),
        _signal("First 5m range", _range_value(opening_5, available=len(candles) >= 5)),
        _signal("First 15m range", _range_value(opening_15, available=len(candles) >= 15)),
        _signal("First 30m range", _range_value(opening_30, available=len(candles) >= 30)),
        _signal("Last 30m flow", _flow_value(candles[-30:], available=len(candles) >= 30)),
        _signal("Last 60m flow", _flow_value(candles[-60:], available=len(candles) >= 60)),
        _signal("Abnormal volume vs same-time avg", "NA"),
        _signal("Large candle body/range spike", body_spike),
        _signal("News/event flag", "NA"),
        _signal("Liquidity stress", liquidity_stress),
        _signal("Opening range breakout", "Active" if len(candles) >= 15 and breakout else "Inactive"),
        _signal("Gap direction", gap_direction if len(daily) >= 2 else "NA"),
    ]

    return LayerResult(
        layer="event",
        label=label,
        direction_bias=bias,
        volatility=volatility,
        confidence=confidence,
        reasons=reasons,
        strategy_tags=_unique(tags),
        candle_window=_candle_window(
            "1Min",
            event_candles,
            "First 15 and latest 5 candles",
            segments=[
                {
                    "start": opening_15[0]["timestamp"] if opening_15 else None,
                    "end": opening_15[-1]["timestamp"] if opening_15 else None,
                },
                {
                    "start": candles[-5]["timestamp"] if len(candles) >= 5 else candles[0]["timestamp"],
                    "end": candles[-1]["timestamp"],
                },
            ],
        ),
        signals=signals,
    )


def _score_strategies(regime: LayerResult, session: LayerResult, event: LayerResult) -> list[dict[str, Any]]:
    active_tags = set(regime.strategy_tags + session.strategy_tags + event.strategy_tags)
    labels = {regime.label, session.label, event.label}
    layers = [regime, session, event]

    catalog = [
        {
            "name": "Multi-Timeframe Trend Alignment",
            "tags": ["long-bias", "short-bias", "trend-day", "trend-follow", "strong-uptrend", "strong-downtrend"],
            "blocks": ["chop", "cash-filter", "liquidity-stress"],
        },
        {
            "name": "First Pullback After Open",
            "tags": ["trend-day", "above-vwap", "below-vwap", "orb", "event-rules"],
            "blocks": ["chop", "volatility-contraction", "liquidity-stress"],
        },
        {
            "name": "Failed Breakout Strategy",
            "tags": ["avoid-breakout", "chop", "mean-reversion", "vwap-reversion"],
            "blocks": ["trend-day", "volatility-expansion", "orb"],
        },
        {
            "name": "Liquidity Sweep Reversal",
            "tags": ["liquidity-watch", "liquidity-stress", "mean-reversion", "vwap-reversion", "news-risk"],
            "blocks": ["trend-day", "strong-uptrend", "strong-downtrend"],
        },
        {
            "name": "Relative Strength vs QQQ/IWM",
            "tags": ["long-bias", "short-bias", "trend-follow", "strong-uptrend", "strong-downtrend"],
            "blocks": ["balanced", "chop", "cash-filter"],
        },
        {
            "name": "Market Breadth Momentum",
            "tags": ["trend-follow", "trend-day", "strong-uptrend", "strong-downtrend", "liquidity-watch"],
            "blocks": ["chop", "cash-filter", "volatility-contraction"],
        },
        {
            "name": "Economic Event Context",
            "tags": ["event-rules", "news-risk", "gap-up", "gap-down", "orb", "volatility-expansion"],
            "blocks": ["chop", "compression", "liquidity-stress"],
        },
        {
            "name": "Moving Average Trend",
            "tags": ["long-bias", "short-bias", "trend-follow", "strong-uptrend", "strong-downtrend", "weak-uptrend"],
            "blocks": ["chop", "cash-filter", "liquidity-stress"],
        },
        {
            "name": "VWAP Position Strategy",
            "tags": ["above-vwap", "below-vwap", "trend-day", "vwap-reversion"],
            "blocks": ["liquidity-stress"],
        },
        {
            "name": "Trend Pullback Strategy",
            "tags": ["trend-day", "trend-follow", "above-vwap", "below-vwap", "orb"],
            "blocks": ["chop", "volatility-contraction", "liquidity-stress"],
        },
        {
            "name": "RSI Mean Reversion",
            "tags": ["mean-reversion", "vwap-reversion", "balanced", "low-volatility"],
            "blocks": ["trend-day", "strong-uptrend", "strong-downtrend", "orb"],
        },
        {
            "name": "Bollinger Band Mean Reversion",
            "tags": ["mean-reversion", "vwap-reversion", "balanced", "low-volatility"],
            "blocks": ["trend-day", "orb", "news-risk", "volatility-expansion"],
        },
        {
            "name": "Opening Range Breakout",
            "tags": ["orb", "event-rules", "trend-day", "long-bias", "short-bias", "volatility-expansion"],
            "blocks": ["chop", "avoid-breakout", "liquidity-stress"],
        },
        {
            "name": "Intraday Breakout Strategy",
            "tags": ["orb", "trend-day", "volatility-expansion", "liquidity-watch"],
            "blocks": ["chop", "avoid-breakout", "volatility-contraction"],
        },
        {
            "name": "MACD Momentum",
            "tags": ["trend-follow", "trend-day", "strong-uptrend", "strong-downtrend", "long-bias", "short-bias"],
            "blocks": ["chop", "cash-filter", "volatility-contraction"],
        },
        {
            "name": "ADX/ATR Regime Classifier",
            "tags": [
                "trend-follow",
                "trend-day",
                "strong-uptrend",
                "strong-downtrend",
                "volatility-expansion",
                "high-volatility",
                "low-volatility",
                "volatility-contraction",
                "liquidity-watch",
                "event-rules",
                "news-risk",
            ],
            "blocks": ["cash-filter"],
        },
        {
            "name": "Market Structure Strategy",
            "tags": ["trend-follow", "trend-day", "strong-uptrend", "strong-downtrend", "weak-uptrend"],
            "blocks": ["chop", "balanced", "liquidity-stress"],
        },
        {
            "name": "Gap Continuation / Gap Fade",
            "tags": ["gap-up", "gap-down", "event-rules", "orb", "vwap-reversion"],
            "blocks": ["liquidity-stress", "compression"],
        },
        {
            "name": "Cash / Avoid Trading Filter",
            "tags": ["cash-filter", "liquidity-stress", "chop", "volatility-contraction", "news-risk"],
            "blocks": [],
        },
        {
            "name": "VWAP Trend Continuation",
            "tags": ["above-vwap", "below-vwap", "trend-day", "trend-follow", "orb"],
            "blocks": ["chop", "vwap-reversion", "liquidity-stress"],
        },
        {
            "name": "VWAP Mean Reversion",
            "tags": ["vwap-reversion", "mean-reversion", "balanced", "chop"],
            "blocks": ["trend-day", "orb", "volatility-expansion", "news-risk"],
        },
        {
            "name": "Failed Breakout Reversal",
            "tags": ["avoid-breakout", "chop", "mean-reversion", "vwap-reversion"],
            "blocks": ["trend-day", "volatility-expansion", "orb"],
        },
        {
            "name": "Bollinger/ATR Reversion",
            "tags": ["mean-reversion", "vwap-reversion", "high-volatility", "volatility-expansion"],
            "blocks": ["trend-day", "orb", "liquidity-stress"],
        },
        {
            "name": "Volatility Breakout",
            "tags": ["high-volatility", "volatility-expansion", "orb", "liquidity-watch"],
            "blocks": ["chop", "avoid-breakout", "volatility-contraction", "liquidity-stress"],
        },
        {
            "name": "Breakout Strategy",
            "tags": ["orb", "trend-day", "volatility-expansion", "liquidity-watch"],
            "blocks": ["chop", "avoid-breakout", "volatility-contraction"],
        },
        {
            "name": "MACD Strategy",
            "tags": ["trend-follow", "trend-day", "strong-uptrend", "strong-downtrend", "long-bias", "short-bias"],
            "blocks": ["chop", "cash-filter", "volatility-contraction"],
        },
    ]

    scored: list[dict[str, Any]] = []
    for strategy in catalog:
        matched = [tag for tag in strategy["tags"] if tag in active_tags]
        blocked = [tag for tag in strategy["blocks"] if tag in active_tags]
        raw = 44 + len(matched) * 11 - len(blocked) * 18
        confidence_bonus = mean(layer.confidence for layer in layers) * 18
        score = max(0, min(99, round(raw + confidence_bonus)))
        if blocked and score > 64:
            score = 64
        status = "Strong Fit" if score >= 78 and not blocked else "Allowed" if score >= 62 and not blocked else "Watch" if score >= 45 else "Avoid"
        classification = STRATEGY_CLASSIFICATION.get(strategy["name"], {"role": "directional", "family": "uncategorized"})
        scored.append(
            {
                "name": strategy["name"],
                "role": classification["role"],
                "family": classification["family"],
                "strategy_family": classification["family"],
                "status": status,
                "score": score,
                "matches": _strategy_matches(matched, labels),
                "risks": _strategy_risks(blocked),
            }
        )

    return sorted(scored, key=lambda item: item["score"], reverse=True)


def _latest_session(candles: list[dict]) -> list[dict]:
    if not candles:
        return []
    latest_date = _date_key(candles[-1]["timestamp"])
    return [candle for candle in candles if _date_key(candle["timestamp"]) == latest_date]


def _candle_window(
    timeframe: str,
    candles: list[dict],
    label: str,
    segments: list[dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    return {
        "timeframe": timeframe,
        "count": len(candles),
        "label": label,
        "start": candles[0]["timestamp"] if candles else None,
        "end": candles[-1]["timestamp"] if candles else None,
        "segments": segments
        or [
            {
                "start": candles[0]["timestamp"] if candles else None,
                "end": candles[-1]["timestamp"] if candles else None,
            }
        ],
    }


def _unique_candles(candles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for candle in candles:
        timestamp = candle["timestamp"]
        if timestamp in seen:
            continue
        seen.add(timestamp)
        result.append(candle)
    return sorted(result, key=lambda candle: candle["timestamp"])


def _date_key(value: str) -> str:
    return _parse_time(value).date().isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def _return(values: list[float], period: int) -> float:
    if len(values) <= period or values[-period - 1] == 0:
        return 0
    return (values[-1] - values[-period - 1]) / values[-period - 1]


def _realized_vol(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 0
    returns = [
        (values[index] - values[index - 1]) / values[index - 1]
        for index in range(len(values) - period, len(values))
        if values[index - 1] != 0
    ]
    if not returns:
        return 0
    avg = mean(returns)
    variance = mean([(value - avg) ** 2 for value in returns])
    return sqrt(variance) * sqrt(252)


def _slope(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 0
    previous = values[-period]
    if previous == 0:
        return 0
    return (values[-1] - previous) / previous


def _session_vwap(candles: list[dict]) -> float:
    total_volume = sum(float(candle["volume"]) for candle in candles)
    if total_volume <= 0:
        return 0
    return (
        sum(
            ((float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3)
            * float(candle["volume"])
            for candle in candles
        )
        / total_volume
    )


def _vwap_slope(candles: list[dict]) -> float:
    if len(candles) < 20:
        return 0
    early = _session_vwap(candles[: len(candles) // 2])
    late = _session_vwap(candles[len(candles) // 2 :])
    if early == 0:
        return 0
    return (late - early) / early


def _level_crosses(values: list[float], level: float) -> int:
    if not values or not level:
        return 0
    signs = [value >= level for value in values]
    return sum(1 for index in range(1, len(signs)) if signs[index] != signs[index - 1])


def _average_range(candles: list[dict]) -> float:
    if not candles:
        return 0.01
    return max(mean(float(candle["high"]) - float(candle["low"]) for candle in candles), 0.01)


def _confidence(flags: list[bool], *, floor: float) -> float:
    if not flags:
        return floor
    score = floor + (sum(1 for flag in flags if flag) / len(flags)) * (0.94 - floor)
    return max(floor, min(0.94, score))


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _na_signals(names: list[str]) -> list[dict[str, str]]:
    return [_signal(name, "NA") for name in names]


def _signal(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value, "status": "na" if value == "NA" else "ok"}


def _money_value(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"


def _pct_value(value: float | None, *, available: bool = True) -> str:
    if not available or value is None:
        return "NA"
    return f"{value * 100:.2f}%"


def _multiple_value(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}x"


def _range_value(candles: list[dict], *, available: bool = True) -> str:
    if not available or not candles:
        return "NA"
    high = max(float(candle["high"]) for candle in candles)
    low = min(float(candle["low"]) for candle in candles)
    start = float(candles[0]["open"])
    pct = (high - low) / start if start else 0
    return f"{high - low:.2f} ({pct * 100:.2f}%)"


def _flow_value(candles: list[dict], *, available: bool = True) -> str:
    if not available or not candles:
        return "NA"
    start = float(candles[0]["open"])
    end = float(candles[-1]["close"])
    pct = (end - start) / start if start else 0
    direction = "Up" if pct > 0 else "Down" if pct < 0 else "Flat"
    return f"{direction} {_pct_value(pct)}"


def _price_location(latest: float, averages: list[tuple[str, float | None]]) -> str:
    above = [label for label, value in averages if value is not None and latest > value]
    below = [label for label, value in averages if value is not None and latest <= value]
    missing = [label for label, value in averages if value is None]
    parts: list[str] = []
    if above:
        parts.append(f"Above {'/'.join(above)}DMA")
    if below:
        parts.append(f"Below {'/'.join(below)}DMA")
    if missing:
        parts.append(f"{'/'.join(missing)}DMA NA")
    return "; ".join(parts) if parts else "NA"


def _atr_percent(candles: list[dict], period: int) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges: list[float] = []
    for index in range(len(candles) - period, len(candles)):
        current = candles[index]
        previous_close = float(candles[index - 1]["close"])
        high = float(current["high"])
        low = float(current["low"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    latest_close = float(candles[-1]["close"])
    return mean(true_ranges) / latest_close if latest_close else None


def _rolling_percentile(values: list[float], period: int, current: float | None) -> float | None:
    if current is None or len(values) <= period * 2:
        return None
    window_values = [_realized_vol(values[:end], period) for end in range(period + 1, len(values) + 1)]
    window_values = [value for value in window_values if value > 0]
    if len(window_values) < period:
        return None
    return sum(1 for value in window_values if value <= current) / len(window_values)


def _rolling_atr_percentile(candles: list[dict], period: int, current: float | None) -> float | None:
    if current is None or len(candles) <= period * 2:
        return None
    values = [_atr_percent(candles[:end], period) for end in range(period + 1, len(candles) + 1)]
    values = [value for value in values if value is not None]
    if len(values) < period:
        return None
    return sum(1 for value in values if value <= current) / len(values)


def _average_daily_range(candles: list[dict], period: int) -> float | None:
    if len(candles) < 2:
        return None
    sample = candles[-period:]
    if not sample:
        return None
    return mean(float(candle["high"]) - float(candle["low"]) for candle in sample)


def _intraday_realized_vol(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    returns = [
        (values[index] - values[index - 1]) / values[index - 1]
        for index in range(1, len(values))
        if values[index - 1] != 0
    ]
    if not returns:
        return None
    return sqrt(sum(value * value for value in returns))


def _volume_pace(volumes: list[float]) -> float | None:
    if len(volumes) < 20:
        return None
    recent = mean(volumes[-10:])
    baseline = mean(volumes[:-10])
    if baseline <= 0:
        return None
    return recent / baseline


def _failed_breakouts(candles: list[dict], opening_high: float, opening_low: float) -> str:
    if len(candles) < 15:
        return "NA"
    failures = 0
    for candle in candles[15:]:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if high > opening_high and close < opening_high:
            failures += 1
        if low < opening_low and close > opening_low:
            failures += 1
    return str(failures)


def _liquidity_stress(candles: list[dict]) -> str:
    if len(candles) < 20:
        return "NA"
    recent = candles[-5:]
    baseline = candles[:-5]
    range_ratio = _average_range(recent) / _average_range(baseline)
    recent_volume = mean(float(candle["volume"]) for candle in recent)
    baseline_volume = mean(float(candle["volume"]) for candle in baseline)
    volume_ratio = recent_volume / baseline_volume if baseline_volume else 0
    return "Active" if range_ratio >= 1.8 and volume_ratio >= 1.8 else "Inactive"


def _large_body_range_spike(candles: list[dict]) -> str:
    if len(candles) < 20:
        return "NA"
    latest = candles[-1]
    body = abs(float(latest["close"]) - float(latest["open"]))
    range_size = max(float(latest["high"]) - float(latest["low"]), 0.01)
    baseline = _average_range(candles[:-1])
    is_spike = range_size >= baseline * 1.8 and body / range_size >= 0.6
    return "Active" if is_spike else "Inactive"


def _strategy_matches(tags: list[str], labels: set[str]) -> list[str]:
    readable = {
        "long-bias": "Long bias",
        "short-bias": "Short bias",
        "trend-day": "Trend day",
        "above-vwap": "Above VWAP",
        "below-vwap": "Below VWAP",
        "strong-uptrend": "Strong uptrend",
        "strong-downtrend": "Strong downtrend",
        "trend-follow": "Trend-following regime",
        "weak-uptrend": "Weak uptrend",
        "orb": "Opening range active",
        "volatility-expansion": "Volatility expanding",
        "volatility-contraction": "Volatility contracting",
        "high-volatility": "High volatility",
        "low-volatility": "Low volatility",
        "compression": "Range compression",
        "gap-up": "Gap-up open",
        "gap-down": "Gap-down open",
        "mean-reversion": "Mean-reversion session",
        "vwap-reversion": "VWAP rotation",
        "balanced": "Balanced conditions",
        "event-rules": "Event rules active",
        "avoid-breakout": "Failed-breakout risk",
        "liquidity-watch": "Liquidity watch",
        "liquidity-stress": "Liquidity stress",
        "news-risk": "News/event risk",
        "low-volume": "Low volume",
        "wait": "Waiting for data",
    }
    matches = [readable[tag] for tag in tags if tag in readable]
    if not matches and labels:
        matches.append("No strong confirming condition")
    return matches[:3]


def _strategy_risks(tags: list[str]) -> list[str]:
    readable = {
        "short-bias": "Conflicts with short bias",
        "long-bias": "Conflicts with long bias",
        "chop": "Choppy tape",
        "liquidity-stress": "Liquidity stress",
        "liquidity-watch": "Liquidity watch",
        "cash-filter": "Cash filter active",
        "volatility-contraction": "Volatility contracting",
        "volatility-expansion": "Volatility expanding",
        "strong-uptrend": "Strong uptrend conflicts",
        "strong-downtrend": "Strong downtrend conflicts",
        "balanced": "No directional edge",
        "trend-day": "Trend day blocks fading",
        "orb": "Opening range move blocks fading",
        "news-risk": "News-risk conditions",
        "compression": "Range compression",
        "low-volume": "Low volume",
    }
    return [readable[tag] for tag in tags if tag in readable][:3]
