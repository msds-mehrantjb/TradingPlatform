# Regime Strategy Catalog

## Roles

| Role | Meaning |
| --- | --- |
| `directional` | May produce Buy, Sell, or Hold evidence and contribute to directional family scores. |
| `confirmation` | Evaluates context that may reduce or preserve directional confidence. It cannot add Buy/Sell vote weight directly. |
| `regime_context` | Classifier/context input that may affect eligibility, diagnostics, or multipliers. It cannot vote directionally. |
| `safety_gate` | Hard gate that can block new entries. It cannot be normalized into directional weight. |

## Strategy Catalog

| ID | Name | Role | Family | Base weight | Minimum bars | Required inputs | Aliases |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| `moving_average_trend` | Moving Average Trend | directional | trend_momentum | 0.11 | 50 | candles, latest, sma20, sma50 | |
| `vwap_position` | VWAP Position Strategy | regime_context | regime_context | 0 | 5 | candles, latest, vwap | |
| `trend_pullback` | Trend Pullback Strategy | directional | trend_momentum | 0.10 | 50 | candles, latest, sma20, sma50, vwap | `first_pullback_after_open` |
| `rsi_mean_reversion` | RSI Mean Reversion | directional | mean_reversion | 0.07 | 15 | candles, latest, rsi | |
| `bollinger_band_mean_reversion` | Bollinger Band Mean Reversion | directional | mean_reversion | 0.07 | 20 | candles, latest, bollinger_bands | `bollinger_atr_reversion` |
| `opening_range_breakout` | Opening Range Breakout | directional | breakout | 0.10 | 15 | candles, latest, opening_range | |
| `intraday_breakout` | Intraday Breakout Strategy | directional | breakout | 0.10 | 21 | candles, latest, recent_range | |
| `macd_momentum` | MACD Momentum | directional | trend_momentum | 0.08 | 26 | candles, latest, macd | |
| `market_structure` | Market Structure Strategy | directional | trend_momentum | 0.12 | 10 | candles, latest, market_structure | |
| `gap_continuation_fade` | Gap Continuation / Gap Fade | directional | gap_session_event | 0.06 | 15 | candles, latest, prior_close, opening_range | |
| `volume_confirmation` | Volume Confirmation | confirmation | confirmation | 0 | 5 | candles, latest, volume | |
| `vwap_trend_continuation` | VWAP Trend Continuation | directional | trend_momentum | 0.10 | 50 | candles, latest, vwap, sma20, sma50 | |
| `vwap_mean_reversion` | VWAP Mean Reversion | directional | mean_reversion | 0.09 | 15 | candles, latest, vwap, adx | |
| `failed_breakout_reversal` | Failed Breakout Reversal | directional | reversal | 0.08 | 21 | candles, latest, recent_range | `failed_breakout_strategy` |
| `liquidity_sweep_reversal` | Liquidity Sweep Reversal | directional | reversal | 0.08 | 21 | candles, latest, recent_range, volume | |
| `adx_trend_strength` | ADX Trend Strength Filter | confirmation | confirmation | 0 | 15 | candles, latest, adx | |
| `atr_volatility_regime` | ATR Volatility Regime | regime_context | regime_context | 0 | 15 | candles, latest, atr | |
| `volatility_breakout` | Volatility Breakout | directional | breakout | 0.08 | 21 | candles, latest, atr, recent_range, volume | |
| `cash_avoid_filter` | Cash / Avoid Trading Filter | safety_gate | safety | 0 | 5 | candles, latest, spread_liquidity, time_of_day | |
| `missing_critical_data` | Missing Critical Data | safety_gate | safety | 0 | 5 | candles, latest | |
| `stale_data` | Stale Data | safety_gate | safety | 0 | 5 | candles, latest | |
| `extreme_volatility` | Extreme Volatility | safety_gate | safety | 0 | 15 | candles, latest, atr | |
| `excessive_spread` | Excessive Spread | safety_gate | safety | 0 | 5 | candles, latest, spread_liquidity | |
| `insufficient_liquidity` | Insufficient Liquidity | safety_gate | safety | 0 | 5 | candles, latest, volume | |
| `event_blackout` | Event Blackout | safety_gate | safety | 0 | 5 | candles, latest | |
| `halt_luld` | Halt or LULD | safety_gate | safety | 0 | 5 | candles, latest | |
| `circuit_breaker` | Circuit Breaker | safety_gate | safety | 0 | 5 | candles, latest | |
| `unsupported_session` | Unsupported Session | safety_gate | safety | 0 | 5 | candles, latest, time_of_day | |

All canonical directional strategies support long and short directions. Alias IDs map to canonical strategies and cannot vote separately or create separate performance histories.

## Directional Output Contract

Every directional strategy returns `DirectionalStrategyResult` with strategy ID, family, role, eligibility, Buy/Sell/Hold signal, confidence, quality, effective weight, signed contribution, timestamp, evidence, and reason. Confidence and quality are clamped to 0-1. Effective weight is nonnegative. Hold is an abstention, not the opposite of Buy or Sell.

Validation rejects unknown strategy IDs, NaN, infinity, out-of-range confidence/quality, missing timestamps, future output timestamps, future evidence timestamps, and direction/sign mismatches.

## Safety Gates

Safety components produce pass/fail gate results. A failed hard gate blocks new entries and does not become a small Hold vote. Protective exits are handled separately by order intent and global risk logic.

## Routing Rules

| Confirmed regime | Selected directional strategies |
| --- | --- |
| `strong_uptrend` | `moving_average_trend`, `trend_pullback`, `macd_momentum`, `market_structure`, `vwap_trend_continuation` |
| `weak_uptrend` | `trend_pullback`, `market_structure`, `vwap_trend_continuation` |
| `strong_downtrend` | `moving_average_trend`, `trend_pullback`, `macd_momentum`, `market_structure`, `vwap_trend_continuation` |
| `weak_downtrend` | `trend_pullback`, `market_structure`, `vwap_trend_continuation` |
| `range_bound`, `sideways_range` | `rsi_mean_reversion`, `bollinger_band_mean_reversion`, `vwap_mean_reversion` |
| `opening_breakout` | `opening_range_breakout`, `volatility_breakout`, `trend_pullback` |
| `intraday_expansion` | `intraday_breakout`, `volatility_breakout`, `market_structure` |
| `high_volatility_trend` | `market_structure`, `moving_average_trend`, `volatility_breakout` |
| `low_volatility_quiet` | `rsi_mean_reversion`, `bollinger_band_mean_reversion`, `vwap_mean_reversion` |
| `failed_breakout_reversal` | `failed_breakout_reversal`, `liquidity_sweep_reversal` |
| `choppy_mixed` | `rsi_mean_reversion`, `bollinger_band_mean_reversion`, `vwap_mean_reversion` |
| `gap_session` | `gap_continuation_fade`, `moving_average_trend`, `market_structure`, `failed_breakout_reversal`, `liquidity_sweep_reversal` |
| `event_risk`, `liquidity_stress`, `extreme_volatility_no_trade`, `no_trade` | none |
| `low_volatility` | `rsi_mean_reversion`, `bollinger_band_mean_reversion`, `vwap_mean_reversion` |
| `normal_volatility` | `moving_average_trend`, `trend_pullback`, `market_structure`, `vwap_mean_reversion` |
| `high_volatility` | `market_structure`, `volatility_breakout`, `failed_breakout_reversal`, `liquidity_sweep_reversal` |
| `trend_continuation` | `moving_average_trend`, `trend_pullback`, `macd_momentum`, `market_structure`, `vwap_trend_continuation` |
| `bullish_breakout`, `bearish_breakout` | `opening_range_breakout`, `intraday_breakout`, `volatility_breakout`, `market_structure` |
| `bullish_reversal_risk`, `bearish_reversal_risk` | `failed_breakout_reversal`, `liquidity_sweep_reversal`, `rsi_mean_reversion`, `vwap_mean_reversion` |
| `mean_reversion` | `rsi_mean_reversion`, `bollinger_band_mean_reversion`, `vwap_mean_reversion` |

## Context Multipliers And Aggregation

Effective directional confidence is derived from:

```text
raw confidence
  * regime compatibility
  * context confirmation
  * reliability multiplier
  * correlation penalty
```

Current multiplier rules:

| Component | Rule |
| --- | --- |
| Regime compatibility | 1 when `routing/compatibility-matrix.ts` selects the strategy for the confirmed regime; 0 otherwise. |
| Context ineligible | Multiplier 0.8. |
| Context Hold | `max(0.65, 1 - confidence * 0.35)`. |
| Context agrees with direction | Multiplier 1. |
| Context conflicts with direction | `max(0.5, 1 - confidence * 0.5)`. |
| Reliability multiplier | 1.0 placeholder. |
| Correlation penalty | 1.0 placeholder; family caps currently enforce correlation control. |
| ATR weight | 0.9 for too-low or high ATR; 0.35 for extreme ATR; 1 otherwise. |
| Volume weight | 0.8 for weak volume or small candles; 1 otherwise. |
| Time-of-day weight | Uses the market time-of-day multiplier, capped at 1. |

Family aggregation caps individual strategy contribution at 0.15 and family contribution at 0.35. Buy and Sell scores are normalized from directional family totals. Hold is tracked as abstention rate, not as a competing directional score.
