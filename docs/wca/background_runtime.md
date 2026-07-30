# WCA Background Runtime

Step 7 introduces a standalone WCA runtime entry point:

```bash
python -m backend.app.algorithms.wca.runtime_main
```

Normal deployment must run this as a separate operating-system process. FastAPI request handlers may enqueue or inspect durable state, but an in-process API task is not the authoritative WCA runtime.

## Durable Queues

The runtime owns database-backed queues:

- `wca_runtime_event_queue` for immutable completed one-minute SPY bar events.
- `wca_runtime_command_queue` for worker commands.
- `wca_runtime_symbol_leases` for per-symbol single-writer leases.
- `wca_runtime_checkpoints` for finalized-bar cursor state.
- `wca_runtime_health` for heartbeat and fail-closed status.

The finalized-bar publisher must provide event ID, WCA subscription ID, symbol, finalized candle timestamp, data manifest or snapshot hash, publication timestamp, source, and replay/recovery indicator. Incomplete, duplicate, stale, and out-of-order events are rejected before runtime processing.

## Workers

The supervisor declares these logical workers:

- finalised-bar consumer
- decision worker
- position and protective-exit worker
- global-risk request worker
- execution outbox worker
- broker reconciliation worker
- recovery worker
- heartbeat and health worker
- end-of-session worker

The decision worker persists the WCA decision before any execution outbox request is created. It writes the finalized-bar checkpoint only after the decision persistence transaction has committed.

## Fail-Closed Operation

After restart, new WCA entries remain paused until active configuration, active weights, inventory state, and broker reconciliation are usable. Lag above the configured threshold also pauses new entries. Protective position management continues during entry pauses.

The runtime never imports Weighted Voting, Voting Ensemble, Regime, Session, or Meta-Strategy mutable queues, checkpoints, inventories, settings, or repositories.

## Operator Commands

Start WCA workers with the background runtime entry point after the dedicated WCA paper account and environment variables are configured:

```bash
python -m backend.app.algorithms.wca.runtime_main
```

Stop WCA workers with the normal process supervisor stop command for the runtime process. Do not use API refreshes to start evaluations; API handlers may inspect persisted state or enqueue commands only.

Run replay through the historical replay/backtest entry point used by the WCA backend tests. Replay must use the same engine contracts, settings, weights, calibration, inventory snapshot, and final validation path as paper modes.

Run shadow mode by selecting the `SHADOW` rollout stage after replay parity evidence is accepted. Shadow mode evaluates finalized events and records decisions without order submission.

Run manual paper by selecting `MANUAL_PAPER` only after recommendation and shadow evidence is accepted. Manual paper may prepare order intents, but execution remains operator controlled.

Enable limited automatic paper only through `LIMITED_AUTOMATIC_PAPER` after all required promotion evidence for that stage is persisted and the paper-execution feature flag is enabled. Code deployment alone must not advance the stage.
