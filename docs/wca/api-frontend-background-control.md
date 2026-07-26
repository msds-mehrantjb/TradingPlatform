# WCA API and Frontend Background Control

Step 15 converts FastAPI and the browser UI into transport and presentation surfaces for WCA. The API process does not calculate WCA strategy signals, aggregate votes, run backtests inline, submit directly to the paper broker, or mutate WCA positions. Those actions are owned by the WCA runtime process or WCA research worker through durable queues.

## Allowed API Responsibilities

- Read WCA status, inventory, virtual positions, decisions, trades, runtime health, command progress, and research job progress.
- Save candidate configuration revisions.
- Enqueue activation, rollback, manual paper, pause, resume, reconciliation, shadow comparison, emergency risk-reduction, backtest, and research commands.
- Return `202 Accepted` with a durable command or job identifier for background actions.

## Disallowed API Responsibilities

- Calculating strategy signals or authoritative votes.
- Running WCA backtests, replay, shadow comparisons, or stability reports synchronously.
- Submitting orders directly to the broker.
- Modifying WCA lots, virtual positions, trades, settings, weights, or calibration state in process memory.
- Trusting frontend-calculated WCA data as authoritative.

## Frontend Contract

The frontend may display the active configuration, weights, calibration versions, runtime process health, API health, WCA virtual inventory, event lag, decision latency, broker status, and reconciliation status. It may submit candidate configuration payloads and enqueue durable actions. It must not store authoritative WCA settings in local storage, run browser-side WCA replay/backtest logic, or submit orders from timers.

Paper-only status must remain visible in the WCA control surface. Promotion and rollout actions require explicit user action and are represented as background commands rather than local UI state changes.
