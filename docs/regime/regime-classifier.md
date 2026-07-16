# Regime Classifier

## Inputs

The deterministic classifier uses point-in-time data only:

- SPY one-minute candles and available five-minute candles.
- Moving-average position and slope.
- Higher-high/higher-low and lower-high/lower-low structure.
- ADX, ATR, ATR percentile, realized volatility.
- VWAP position and slope.
- Opening range and recent range.
- Relative volume and volume trend.
- Spread/liquidity state when available.
- QQQ/IWM relative strength, market breadth, VIX, ES futures, scheduled events, and quote freshness are represented as unknown unless supplied point-in-time.
- Time of day.

Unavailable inputs are recorded in `missingInputs`; they are not fabricated.

## Axes

| Axis | Values | Current rule summary |
| --- | --- | --- |
| Direction | strong_up, weak_up, neutral, weak_down, strong_down | Bull/bear score edge plus ADX. Strong requires edge >= 4 and ADX >= 20; weak requires edge >= 2. |
| Volatility | compressed, normal, expanded, extreme | ATR regime, ATR percentile, ATR percent, and realized volatility. Extreme triggers at ATR regime extreme, ATR percentile >= 0.95, or realized volatility >= 0.006. |
| Structure | trend, range, breakout, failed_breakout, reversal, mixed | Opening/prior-day breaks, failed retests, rejection candles, HH/HL or LH/LL, VWAP chop, and ADX/range tests. |
| Liquidity | good, acceptable, poor, unknown | Spread validity, volume, relative volume, and configured max spread. |
| Session | opening, midday, afternoon, closing, outside_regular | Derived from market time: opening until 10:30, midday until 13:30, afternoon until 15:30, closing until 16:00. |
| Event risk | none, elevated, blackout | Currently detects blackout from supplied no-trade reasons; otherwise none. |

## Composite Regime Rules

Composite regime selection is ordered. Earlier rows win.

| Condition | Composite regime |
| --- | --- |
| Volatility axis is extreme | `extreme_volatility_no_trade` |
| Event risk is elevated or blackout | `event_risk` |
| Liquidity axis is poor | `liquidity_stress` |
| Opening gap magnitude >= 0.35% | `gap_session` |
| Structure is failed_breakout or reversal | `failed_breakout_reversal` |
| Structure is breakout during opening session | `opening_breakout` |
| Structure is breakout, or volatility expanded with relative volume >= 1.2 | `intraday_expansion` |
| Volatility expanded and structure trend | `high_volatility_trend` |
| Volatility compressed and structure range | `low_volatility_quiet` |
| Structure range | `range_bound` |
| Structure mixed | `choppy_mixed` |
| Direction strong_up | `strong_uptrend` |
| Direction weak_up | `weak_uptrend` |
| Direction strong_down | `strong_downtrend` |
| Direction weak_down | `weak_downtrend` |
| Fallback | `choppy_mixed` |

## Hysteresis

Defaults:

| Setting | Default |
| --- | ---: |
| `confirmationBars` | 3 |
| `immediateConfidenceThreshold` | 0.65 |
| `minimumDwellBars` | 0 |
| `transitionConfidenceGap` | 0 |
| `maximumUnknownBars` | 3 |

Normal risk-on transitions require the candidate regime to persist for the configured confirmation bars, or to exceed the immediate confidence threshold and current-regime confidence by the configured gap. Risk-off transitions may occur immediately for halt/LULD, circuit breaker, extreme spread, stale critical data, event blackout, extreme volatility, and broker/account risk blocks.

The confirmed state records raw regime, confirmed regime, raw and confirmed confidence, candidate regime, candidate count, dwell bars, whether the previous regime was held, transition reason, and timestamp.
