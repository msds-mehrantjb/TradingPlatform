# WCA Primary Strategy Validation

Step 3 completes the 11 WCA primary voters as deterministic WCA-owned strategy classes.

## Active Primary Voters

All 11 authoritative primary voters are active after focused validation:

- C1 `moving_average_trend`
- C2 `first_pullback_after_open`
- C3 `vwap_trend_continuation`
- C4 `vwap_mean_reversion`
- C5 `rsi_mean_reversion`
- C6 `bollinger_atr_reversion`
- C7 `opening_range_breakout`
- C8 `intraday_volatility_breakout`
- C9 `failed_breakout_reversal`
- C10 `liquidity_sweep_reversal`
- C11 `gap_continuation_fade`

## Validation Contract

Each primary voter:

- uses only the supplied immutable `WcaMarketSnapshot` and its dedicated WCA settings model;
- emits BUY, SELL, valid HOLD, or NOT_APPLICABLE/INVALID when context is unavailable or bad;
- reports raw confidence, calibrated confidence, evidence strength, data-quality status, and reason codes;
- declares minimum warm-up and required market inputs in the authoritative catalog;
- avoids fallback direction when evidence is flat, contradictory, stale, or missing;
- produces identical output for identical snapshot and settings inputs.

Focused tests in `backend/tests/test_wca_step3_primary_strategy_validation.py` cover valid buy, valid sell, valid hold, not applicable, missing input, insufficient warm-up, boundary values, contradictory evidence, stale data, deterministic replay, configuration changes, and no fallback direction for every primary voter.

