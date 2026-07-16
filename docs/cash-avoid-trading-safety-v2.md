# Cash / Avoid Trading Safety V2

Step 20 adds `CashAvoidTradingSafety`, a hard safety module that returns a `GlobalGateDecision`.

## Blocking Scope

The module may block only `new_entry` intents. It must not block:

- `protective_exit`
- `risk_reducing`
- `end_of_day_liquidation`
- `reconciliation`

Unsafe reasons remain visible for non-entry intents, but the decision remains eligible and the safety gate does not set `blocksTrading=true`.

## Configured Thresholds

`CashAvoidTradingConfig` is versioned and hashed. It contains:

- manual cash mode
- maximum spread in basis points
- extreme ATR percentile
- extreme realized-volatility percentile
- maximum daily loss percent
- account-state freshness limit
- operational-state freshness limit
- event blackout importance labels

## Blocking Reasons

Automatic new entries fail closed for:

- insufficient market data
- market closed
- event blackout
- extreme spread
- extreme volatility
- halt or LULD
- circuit breaker
- daily loss limit
- broker/account restriction
- manual cash mode
- stale account or operational state
- unknown critical operational state

Critical operational fields are `marketOpen`, `haltOrLuld`, `circuitBreaker`, and `brokerAccountRestricted`. If any are unknown, automatic new entries are blocked with explicit `safety.unknown_critical_state:*` reason codes.

## Decision Contract

Every decision includes:

- `status`
- `eligible`
- `dataReady`
- one `GateResult`
- one or more reason codes
- explanation
- configuration hash
- decision timestamp
- New York session date

The module does not calculate strategy direction and cannot be inserted into the directional-voter list.
