export const REGIME_SAFETY_GATE_INVENTORY = [
  { key: "R8", id: "cash_avoid_filter", name: "Cash/Avoid Trading" },
  { key: null, id: "missing_critical_data", name: "Missing Critical Data" },
  { key: null, id: "stale_data", name: "Stale Data" },
  { key: null, id: "extreme_volatility", name: "Extreme Volatility" },
  { key: null, id: "excessive_spread", name: "Excessive Spread" },
  { key: null, id: "insufficient_liquidity", name: "Insufficient Liquidity" },
  { key: null, id: "event_blackout", name: "Event Blackout" },
  { key: null, id: "halt_luld", name: "Halt/LULD" },
  { key: null, id: "circuit_breaker", name: "Circuit Breaker" },
  { key: null, id: "unsupported_session", name: "Unsupported Session" },
] as const;

