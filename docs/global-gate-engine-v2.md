# Global Gate Engine V2

The V2 global gate engine is the shared backend authority for automatic new-entry eligibility. It is used before paper-order submission and records all pass, caution, and failure reasons in the decision snapshot. It does not enable live trading.

## Gate Groups

- Operational: trading enabled, paper-trading mode, market open, entry window open, valid session.
- Data health: fresh candle, fresh quote, valid bid/ask, monotonic timestamps, timeframe synchronization, auxiliary data readiness, feature schema validity.
- Broker and account health: broker connection, paper account status, account restrictions, tradability, buying power freshness, position and open-order reconciliation.
- Market safety: symbol halt, LULD pause, circuit breaker, locked/crossed quote, extreme spread, extreme volatility.
- Global account risk: daily loss, intraday drawdown, open risk, SPY notional, same-direction exposure, max trades, consecutive losses.
- Execution safety: liquidity, spread, expected slippage, entry distance, duplicate/conflicting orders, cooldown.
- Candidate quality: deterministic score, independent-family support, expected value, optional ML probability and model health checks.
- Order integrity: positive quantity, valid entry, correct-side stop and target, budget/cap/protective-order/client-ID checks.
- Strategy-aware conditional gates: Weekly/Daily permission, 1-hour direction, market-regime compatibility, economic-event context, relative strength, breadth, 1-minute execution trigger, 5-minute execution confirmation, and late-session conditions.

## Intent Handling

Automatic new entries fail closed when a critical gate input is missing, stale, malformed, or unavailable. Protective exits, risk-reducing orders, end-of-day liquidation, and reconciliation intents are allowed to bypass new-entry-only blockers while preserving the failed gate reasons as cautions for display and audit.

Daily-loss and exposure gates use the broker-authoritative global account risk snapshot, including positions, pending orders, partial fills, unrealized P&L, and conservative exit costs. Local UI history is not accepted as the final authority.

## Strategy-Aware Conditional Gates

Conditional gates use the candidate strategy family and setup subtype. They are not universal directional hard blocks.

- A 1-hour conflict hard-blocks long trend and breakout setups by default, but only cautions long reversal and mean-reversion setups.
- High ADX supports trend and breakout families while cautioning reversal and mean-reversion families.
- Low ADX weakens breakout setups and can support mean reversion.
- Economic-event direction remains context only; it can caution or block according to family policy but cannot replace the deterministic ensemble side.
- Missing conditional inputs are recorded as `INFO` / not executed, never as a fake pass.

The behavior is controlled by `StrategyConditionalGateConfig` inside the versioned global gate configuration.

## Replay Integration

The event-driven replay engine now invokes the shared global gate engine before ML/policy/order planning and again after an order plan exists so order-integrity checks are captured in the snapshot. Existing replay fixtures still provide candle-only quote assumptions; strict critical-feed fail-closed behavior is covered by the standalone gate-engine tests.
