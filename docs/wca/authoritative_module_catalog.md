# WCA Authoritative Module Catalog

This document mirrors `backend.app.algorithms.wca.strategy_registry`. Update the registry first, then update this table in the same change.

## Primary Voters

| ID | Slug | Display name | Family | Role | Lifecycle | Baseline weight |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | `moving_average_trend` | Moving Average Trend | trend | primary_voter | active | 0.10 |
| C2 | `first_pullback_after_open` | First Pullback After Open | trend | primary_voter | active | 0.09 |
| C3 | `vwap_trend_continuation` | VWAP Trend Continuation | trend | primary_voter | active | 0.09 |
| C4 | `vwap_mean_reversion` | VWAP Mean Reversion | mean_reversion | primary_voter | active | 0.08 |
| C5 | `rsi_mean_reversion` | RSI Mean Reversion | mean_reversion | primary_voter | active | 0.08 |
| C6 | `bollinger_atr_reversion` | Bollinger/ATR Reversion | mean_reversion | primary_voter | active | 0.08 |
| C7 | `opening_range_breakout` | Opening Range Breakout | breakout | primary_voter | active | 0.10 |
| C8 | `intraday_volatility_breakout` | Intraday/Volatility Breakout | breakout | primary_voter | active | 0.10 |
| C9 | `failed_breakout_reversal` | Failed Breakout Reversal | reversal | primary_voter | active | 0.09 |
| C10 | `liquidity_sweep_reversal` | Liquidity Sweep Reversal | reversal | primary_voter | active | 0.09 |
| C11 | `gap_continuation_fade` | Gap Continuation/Fade | event | primary_voter | active | 0.10 |

## Contextual Modifiers

| ID | Slug | Display name | Family | Role | Lifecycle | Baseline weight |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | `vwap_position` | VWAP Position | vwap | modifier | active |  |
| M2 | `volume_confirmation` | Volume Confirmation | volume | modifier | active |  |
| M3 | `macd_momentum` | MACD Momentum | momentum | modifier | active |  |
| M4 | `market_structure` | Market Structure | structure | modifier | active |  |
| M5 | `adx_trend_strength` | ADX Trend Strength | trend | modifier | active |  |
| M6 | `atr_volatility_regime` | ATR Volatility Regime | volatility | modifier | active |  |
| M7 | `multi_timeframe_trend_alignment` | Multi-Timeframe Trend Alignment | trend | modifier | active |  |
| M8 | `relative_strength_vs_qqq_iwm` | Relative Strength vs QQQ/IWM | relative_strength | modifier | active |  |
| M9 | `market_breadth` | Market Breadth | breadth | modifier | active |  |
| M10 | `session_phase` | Session Phase | session | modifier | active |  |
| M11 | `spread_liquidity` | Spread/Liquidity | liquidity | modifier | active |  |

## Hard Filters

| ID | Slug | Display name | Family | Role | Lifecycle | Baseline weight |
| --- | --- | --- | --- | --- | --- | --- |
| H1 | `cash_avoid_trading` | Cash/Avoid Trading | risk | hard_filter | active |  |
| H2 | `economic_event_risk` | Economic Event Risk | risk | hard_filter | active |  |
| H3 | `invalid_or_stale_data` | Invalid or Stale Data | risk | hard_filter | active |  |
| H4 | `unsafe_spread` | Unsafe Spread | risk | hard_filter | active |  |
| H5 | `unsafe_liquidity` | Unsafe Liquidity | risk | hard_filter | active |  |
| H6 | `extreme_volatility` | Extreme Volatility | risk | hard_filter | active |  |
| H7 | `session_entry_block` | Session Entry Block | risk | hard_filter | active |  |

## Deprecated Aliases

| Alias slug | Canonical slug | Canonical ID | Lifecycle |
| --- | --- | --- | --- |
| `trend_pullback` | `first_pullback_after_open` | C2 | deprecated_alias |
