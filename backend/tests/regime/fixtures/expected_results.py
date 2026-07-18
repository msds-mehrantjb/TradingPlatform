CANONICAL_REGIMES = {
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "sideways_range",
    "choppy_mixed",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "gap_session",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
}

DIRECTIONAL_STRATEGIES = (
    "moving_average_trend",
    "trend_pullback",
    "rsi_mean_reversion",
    "bollinger_band_mean_reversion",
    "opening_range_breakout",
    "intraday_breakout",
    "macd_momentum",
    "market_structure",
    "gap_continuation_fade",
    "vwap_trend_continuation",
    "vwap_mean_reversion",
    "failed_breakout_reversal",
    "liquidity_sweep_reversal",
    "volatility_breakout",
)

CONFIRMATION_MODULES = ("volume_confirmation", "adx_trend_strength")
CONTEXT_MODULES = ("vwap_position", "atr_volatility_regime")
SAFETY_GATES = (
    "cash_avoid_filter",
    "missing_critical_data",
    "stale_data",
    "extreme_volatility",
    "excessive_spread",
    "insufficient_liquidity",
    "event_blackout",
    "halt_luld",
    "circuit_breaker",
    "unsupported_session",
)

