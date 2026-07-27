# Regime Classifier

## Inputs

The deterministic classifier uses point-in-time data only:

- SPY completed one-minute primary candles and available five-minute candles.
- Moving-average position and slope.
- VWAP position and slope.
- ADX, +DI, -DI, DMI spread, and efficiency ratio.
- ATR as price units, ATR percent as a decimal ratio, and realized volatility as a decimal ratio.
- Minute-of-session ATR percentiles and realized-volatility percentiles as decimals from `0.0` to `1.0`.
- Current candle range versus expected range as a ratio.
- Current volume versus expected volume as a ratio.
- Opening range, prior-day levels, premarket levels, recent swing levels, acceptance, retests, rejection candles, and VWAP crossing frequency.
- Quote freshness, bid, ask, spread, quote age, expected fill quantity, participation rate, trade count/rate, and top-of-book depth when supplied point-in-time.
- QQQ, IWM, market breadth, VIX, VIX1D, ES futures, scheduled economic events, and halt/LULD feeds as read-only point-in-time context.

Unavailable external feeds are recorded as unknown or missing. They are never replaced with fabricated neutral evidence.

## Sessions

Session state comes from `backend.app.algorithms.regime.exchange_calendar.exchange_session`, which uses the America/New_York exchange calendar and handles daylight saving time, weekends, holidays, early closes, opening window, closing window, and outside-session data.

Session values are `opening`, `midday`, `afternoon`, `closing`, and `outside_regular`.

## Units

| Field | Unit |
| --- | --- |
| `spreadPercent`, `atrPercent`, realized volatility, risk percentages, participation rate | decimal ratio |
| `spreadBps`, slippage | basis points |
| ATR | price units |
| `atrPercentile`, `realizedVolatilityPercentile` | percentile decimal from `0.0` to `1.0` |
| `currentRangeVsExpected`, `currentVolumeVsExpected` | ratio |
| `minuteOfSession` | minutes after regular open |

## Axes

| Axis | Values | Rule summary |
| --- | --- | --- |
| Direction | strong_up, weak_up, neutral, weak_down, strong_down | EMA/VWAP slope, VWAP location, and ordered market structure determine direction. |
| Trend strength | score in evidence | ADX, +DI/-DI spread, and efficiency ratio determine strength only; they do not determine direction. |
| Volatility | compressed, normal, expanded, extreme | Requires calibrated minute-of-session ATR and realized-volatility percentiles when available, plus current range versus expected range and current volume versus expected volume. Conservative same-minute-history fallback is used only when a valid calibration artifact is unavailable, and reason codes are recorded. |
| Structure | trend, range, breakout, valid_breakout, opening_range_breakout, prior_day_level_breakout, premarket_level_breakout, failed_breakout, liquidity_sweep, reversal, mixed | Uses point-in-time swing order, reference levels, acceptance, retests, VWAP crossings, directional efficiency, and rejection candles. |
| Liquidity | good, acceptable, poor, unknown | Missing or stale quote, missing bid/ask/spread, quote age, spread, relative volume, participation, top-of-book depth, and fill quantity. |
| Session | opening, midday, afternoon, closing, outside_regular | DST-aware exchange-calendar status. |
| Event risk | none, elevated, blackout | Scheduled macro-event state and halt/LULD state. |

## Confidence

The classifier reports direction, volatility, structure, liquidity, session, event-risk, and data-quality confidence. Composite confidence is the minimum required axis confidence for the selected composite regime plus data-quality confidence. Safety-block confidence is separate and does not inflate classification confidence.

## Fail-Closed Inputs

These inputs are critical and block new entries when missing or invalid:

- completed primary candle;
- usable recent history;
- valid timestamp;
- fresh quote when execution is possible;
- bid and ask;
- spread;
- session status.

Every fallback or missing critical input is recorded with an explicit reason code in classifier evidence and/or no-trade reasons.

## Composite Regime Rules

Composite regime selection is ordered. Earlier rows win.

| Condition | Composite regime |
| --- | --- |
| Volatility axis is extreme | `extreme_volatility_no_trade` |
| Event risk is elevated or blackout | `event_risk` |
| Liquidity axis is poor or unknown | `liquidity_stress` |
| Structure is failed_breakout, liquidity_sweep, or reversal | `failed_breakout_reversal` |
| Opening-range breakout, or breakout during opening session | `opening_breakout` |
| Valid breakout with expanded volatility | `intraday_expansion` |
| Expanded volatility with strong direction | `high_volatility_trend` |
| Compressed volatility | `low_volatility_quiet` |
| Strong/weak direction | directional trend regimes |
| Range structure | `range_bound` |
| Fallback | `choppy_mixed` |
