# Weighted Voting Authoritative Architecture

Weighted Voting is the algorithm identified by `weighted_voting`. Backend Python in
`backend/app/algorithms/weighted_voting` is the only authoritative runtime for
signals, settings, sizing, inventory, execution state, backtests, replay and paper
trading. Frontend TypeScript, QML and API request handlers may inspect or configure
Weighted Voting through services, but they do not schedule or compute authoritative
trading decisions.

The code-level contract lives in
`backend/app/algorithms/weighted_voting/architecture.py`. The production decision
kernel is `WeightedVotingService.evaluate_context`, and its only authoritative
decision input is the typed `WeightedVotingRuntimeContext` declared in
`backend/app/algorithms/weighted_voting/runtime_context.py`. The deterministic,
side-effect-free implementation is `WeightedVotingDecisionKernel.evaluate` in
`backend/app/algorithms/weighted_voting/decision_kernel.py`. The architecture
contract declares the authoritative package, isolated persistence namespace and
pipeline boundaries. Tests in
`backend/tests/test_weighted_voting_package_architecture.py` enforce that contract
and fail on cross-algorithm mutable imports.

## Non-Negotiable Rules

- `weighted_voting` remains the authoritative algorithm ID.
- Weighted Voting is rule-based and statistical. Machine learning is not allowed.
- Supported modes are backtesting, replay, shadow evaluation and automatic paper
  trading only.
- Live-money trading is not allowed.
- Automatic paper submission remains disabled until rollout gates pass.
- API endpoints call application services but are not the trading scheduler.
- Background workers trigger one-minute evaluation, order submission, exit handling
  and reconciliation through the same application services.
- Backtest, replay, shadow and paper paths use the same Weighted Voting decision
  kernel and production strategy, settings, aggregation, gate, sizing, entry and
  exit logic.
- Completed candles only: no evaluation may use an incomplete one-minute candle.
- Every event handler and broker operation must be idempotent.
- Missing, stale, malformed or conflicting safety information fails closed with
  HOLD or REJECT.
- Global-risk decisions are external inputs returned by the central risk service;
  a client request may not create them.
- Public HTTP evaluation requests may provide market candles and quotes only.
  Deprecated account, capital, inventory, session and global-risk fields are
  ignored and never become authoritative state.

## Runtime Context

Every production decision is evaluated from a `WeightedVotingRuntimeContext`.
The context is built through dedicated backend ports and repositories:
market-data ports provide finalised one-minute snapshots and completed
five-minute confirmation; Weighted Voting repositories provide settings, active
weights and the isolated inventory; read-only shared ports provide broker account
observations, quote/cost facts, event-risk facts and central global-risk
availability/response. A general dictionary or HTTP payload is never accepted as
inventory, risk, session or permission state.

The runtime context contains source attribution and timestamps for each required
field: market snapshot, five-minute alignment, exchange/session state,
data-quality state, market condition, settings, active weights, inventory,
current Weighted Voting position, pending Weighted Voting orders, daily P&L,
daily trade count, remaining daily risk, remaining capital partition, read-only
account equity, broker buying power, spread, quote timestamp, slippage, fees,
event risk, global-risk service availability and external global-risk response.
The manifest hash covers the context version, source manifest, settings version,
weight version and inventory version.

Missing, stale, malformed or conflicting runtime context produces a HOLD with
explicit `weighted_voting.runtime_context.*` reason codes. Explicit overrides are
available only through `evaluate_replay_fixture` or test-fixture context ports,
which are not used by automatic paper runtime.

## Dynamic Trading Settings

Weighted Voting resolves trading settings for every completed one-minute
decision from three algorithm-owned layers: baseline settings, a deterministic
market-condition profile, and immutable hard limits. The current classified
market condition is passed to the backend resolver on each decision. The
resolver may reduce risk, tighten thresholds, disable strategy eligibility, or
block new entries for unsafe volatility, liquidity, session, or event-risk
states, but it may not exceed Weighted Voting hard limits. The exact resolved
settings version and configuration hash are persisted with decisions, order
proposals, and trades. Expired settings are recomputed through the resolver for
runtime paths, and fixture-only paths without a resolver fail closed when stale.

Settings are namespaced and owned by `weighted_voting`: strategy state,
eligibility, strategy risk multipliers, baseline/min/max strategy weights,
family exposure caps, correlation penalties, score and support thresholds, risk
budgets, allocation limits, liquidity/spread limits, cost assumptions, stop and
target rules, strategy-specific holding limits, event-risk actions, condition
multipliers, and finalised-bar/quote freshness thresholds. Identically named
strategies in other algorithms cannot supply or mutate these settings.

## Central Global-Risk Boundary

Weighted Voting sends every local order proposal through a typed central
global-risk request before paper execution. The request includes the proposal ID,
algorithm ID, capital partition, symbol, side, quantity, notional, planned risk,
current Weighted Voting exposure, read-only account-risk observations, daily
Weighted Voting P&L, proposal timestamp, settings version, inventory version and
request ID. The response is accepted only when its request ID, proposal ID,
algorithm ID, expiry, quantity cap, risk cap and optional response hash match the
request. Missing, timed-out, stale, forged, mismatched or failed service
responses become `REJECT`; there is no default `ALLOW` path.

Global controls remain shared account-level controls, but they may only allow,
reduce, reject or request emergency action against the local proposal. They do
not own or mutate Weighted Voting inventory, settings, weights, positions, P&L or
strategy state. Weighted Voting persists both global-risk requests and responses
under `weighted_voting.global_risk_*` audit keys before applying the result.

## Background One-Minute Runtime

Weighted Voting has a backend-owned runtime supervisor that starts with the
FastAPI application and shuts down with it. The dashboard and HTTP API are not
required to trigger one-minute evaluation, risk checks, paper execution,
reconciliation, recovery or heartbeat processing. API routes expose only runtime
status and administrative pause/resume controls.

The supervisor subscribes to typed finalised one-minute bar events for configured
symbols through a bounded backend event bus. Within Weighted Voting, events are
processed sequentially per symbol. Each completed candle receives an idempotency
key derived from `algorithm_id`, symbol, finalised candle timestamp, data
manifest hash, settings version and weight version. Processed event records and
symbol checkpoints are persisted under `weighted_voting.runtime.*`, allowing
restart recovery from the last safe checkpoint. Duplicate events are no-ops,
out-of-order events are rejected unless explicitly marked as replay/recovery,
and stale queued events are rejected before order creation.

Accepted automatic paper proposals are persisted to a Weighted Voting-owned
execution queue under `weighted_voting.execution_gateway.queue.*`. The execution
worker consumes that queue, reuses the persisted local-gate and global-risk
application evidence, reserves Weighted Voting inventory capital/risk before
external submission, persists the broker command, and only then calls the shared
paper broker gateway. Rejections, cancellations and expiries release the
reservation through append-only inventory events; fills are attributed back to
Weighted Voting inventory and positions. Duplicate execution events return the
persisted result and do not create another broker order. Automatic submission is
still blocked until rollout validation enables it; manual paper testing uses the
same validation and gateway path rather than a direct arbitrary-order endpoint.

Broker reconciliation is handled by backend workers through
`broker_reconciliation`. Reconciliation matches shared broker observations to
Weighted Voting only by deterministic client-order ID plus
`algorithm_id == "weighted_voting"`. It processes acknowledgements, partial
fills, multiple fills, full fills, rejections, cancellations, expirations and
cancel/replace observations into durable Weighted Voting reconciliation records,
inventory events, checkpoints and discrepancy records. Fill application is
idempotent by broker fill ID. Multiple partial fills merge into one Weighted
Voting position using weighted-average entry price, shrink reserved buying power
as actual quantity fills, and update open quantity and exposure from inventory
events. The documented daily trade-count definition is: count increments when a
Weighted Voting position is closed, not when entry partial fills arrive.

Foreign or unattributed broker activity is never imported into Weighted Voting
inventory. It is flagged for central operations review. Weighted Voting broker
records missing locally, local records missing at the broker, and quantity
mismatches are persisted as discrepancies and pause new Weighted Voting entries.
Risk-reducing exits remain allowed while entry creation is paused. Startup
reconciliation runs before automatic entry submission can proceed.

Weighted Voting position management is autonomous in backend workers through
`position_manager`. On entry fill it creates the authoritative managed position
record, opens the deterministic exit lifecycle using the exact settings version
and configuration hash from the entry, and persists linkage between entry,
protective stop, target and trade IDs. Broker-held protective orders are
preferred when the paper broker supports them; otherwise the manager still
persists local protective instructions and restores orphaned protection after
restart. Position monitoring can be driven by broker, quote or completed-bar
events and uses the Weighted Voting trade-management policy for structural or
ATR/fallback stops, hard maximum loss, profit target, break-even movement,
trailing stop, signal deterioration, opposite high-confidence exit,
spread/liquidity emergency, strategy-specific time stop, end-of-day liquidation
and global emergency liquidation. Exit management continues while new entries
are paused.

Only `weighted_voting` positions may be protected, closed or modified. Every
closed position produces one authoritative trade record with exit reason, gross
P&L, estimated and realised costs, net P&L, MAE, MFE, holding time, settings
version/hash and supporting strategies.

Separately monitored workers cover finalised bar intake, decision evaluation,
global-risk handoff, execution, reconciliation, position management, daily
updates, recovery and heartbeat. Repeated worker failures pause automatic order
creation while reconciliation, recovery and scheduled maintenance can continue.
The runtime uses market-session facts and a backend market calendar instead of
assuming every weekday is tradable.

## Decision Kernel

`WeightedVotingDecisionKernel.evaluate(context)` is the single production,
replay, shadow and paper decision kernel. It validates completed bars, reads the
immutable inventory snapshot from the context, classifies market condition,
resolves effective dynamic settings, loads active versioned weights from the
context, evaluates strategies, applies controls during aggregation, calculates
five-minute alignment and transaction costs, runs local gates, sizes from
Weighted Voting risk/capital only, creates a local order proposal or HOLD, and
returns an immutable observability record.

The kernel does not write persistence, submit orders, call HTTP APIs, read global
mutable variables, create default inventory, create default global-risk
responses, use wall-clock time, or evaluate incomplete candles. Orchestration
services persist kernel results and communicate with central risk/execution
services after kernel evaluation.

## Ownership

Weighted Voting owns its strategy catalogue, strategy implementations, signal
state, weight state, configuration, dynamic profiles, inventory, capital partition,
orders, fills, positions, trades, P&L, backtests, performance history and execution
attribution.

Mutable persistence keys use `weighted_voting.*`. Filesystem artifacts use
`data/algorithms/weighted_voting`. Every write validates or stamps
`algorithm_id == "weighted_voting"`; foreign algorithm writes are rejected.

Weighted Voting owns one isolated virtual inventory and the
`weighted_voting.paper.default` capital partition. The broker account is a shared
external resource, not the algorithm inventory. Shared services may provide
read-only market data, read-only broker/account observations, clocks, logging and
central risk responses. They may not mutate Weighted Voting strategy state,
weights, settings, positions, P&L or performance attribution.

## Pipeline Boundaries

| Stage | Authoritative module | Boundary rule |
| --- | --- | --- |
| market-data input | `market_snapshot` | Read-only market data port; foreign algorithm fields are ignored and unsafe input holds. |
| finalised one-minute bar events | `runtime.worker` | Background worker only; incomplete candles are never evaluated. |
| five-minute confirmation data | `decision_gates` | Uses completed one-minute bars only; unavailable confirmation blocks or reduces entry. |
| strategy evaluation | `signal_engine` and `strategies/*` | Dedicated Weighted Voting strategies only; missing data returns HOLD. |
| market-condition classification | `market_condition` | Weighted Voting condition state only; conflicting quality becomes avoid or hold. |
| dynamic-settings resolution | `dynamic_settings` | Starts from Weighted Voting defaults and profiles; invalid values are clamped or rejected. |
| weight loading | `weight_engine` | Uses Weighted Voting outcomes and active weights only; foreign weights are rejected. |
| aggregation | `aggregation` | Aggregates Weighted Voting signals only; insufficient active weight or ties return HOLD. |
| local gates | `decision_gates` | Missing safety inputs reject new entry or hold. |
| algorithm inventory | `position_trade_state` | Weighted Voting positions, orders, fills and trades only. |
| position sizing | `position_sizing` | Uses Weighted Voting inventory and capital partition; unknown capacity rejects entry. |
| global-risk request | `global_interface` | Sends proposals to central risk; response may allow, reduce or reject only. |
| paper-order execution | `execution_gateway` | Paper only, accepted proposals enter a bounded Weighted Voting execution queue, reserve inventory first, persist broker commands before external submission, use deterministic client order IDs, and require rollout gates. |
| order/fill reconciliation | `broker_reconciliation` and `execution_gateway` | Broker observations must match Weighted Voting client-order IDs and attribution; discrepancies pause entries and are checkpointed. |
| position lifecycle | `position_manager` | Position mutation and protective-order management require Weighted Voting ownership. |
| trade closing | `position_manager` | Weighted Voting can close only its own positions and writes one authoritative trade record. |
| performance attribution | `performance_tracker` | Trades, signals and weights must be Weighted Voting-owned. |
| after-market weight updates | `scheduler` | Runs after market with complete data; incomplete datasets preserve prior weights. |
| backtesting and replay | `backtest.engine` | Uses completed historical candles and production logic; invalid history blocks run. |

## Dependency Boundary

Weighted Voting may import its own package and typed shared ports. It must not
import mutable settings, strategy registries, state repositories, execution
pipelines, backtest ledgers or trade state from `voting_ensemble`, `wca`,
`regime`, `meta_strategy`, `session` or legacy shared strategy/trading-policy
packages.

The dependency tests statically parse every Python file under
`backend/app/algorithms/weighted_voting` and fail on sibling algorithm imports.
This prevents circular ownership and protects other algorithms from Weighted
Voting state changes.
