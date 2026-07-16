# Realistic Execution Simulation V2

The V2 replay engine uses `RealisticExecutionSimulator` for simulated order execution instead of crediting strategies at decision-candle closes.

The simulator models:

- next executable entry after configured latency,
- side-correct bid/ask assumptions,
- slippage and fees on every filled trade,
- market, limit, and stop-limit entries,
- stop-limit trigger without fill,
- partial fills through volume participation,
- unfilled and expired orders,
- protective stop and profit target bracket/OCO exits,
- end-of-day exits,
- same-bar stop/target ambiguity.

Buy entries use ask-side conservative pricing. Sell exits for long positions use bid-side conservative pricing. Short entries use bid-side conservative pricing, and buy-to-cover exits use ask-side conservative pricing.

When both target and stop are touched in the same OHLC candle and no quote/tick ordering is available, the simulator records `execution.same_bar_target_stop_ambiguous` and applies the conservative `STOP_FIRST` rule.

Unfilled and expired orders produce no trade credit. Every filled trade records entry costs, exit costs, total costs, fill status, exit status, and reason codes.
