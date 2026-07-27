# Regime Architecture

## Authority

The Regime algorithm has one production decision core:

- `backend/app/algorithms/regime/execution_pipeline.py`
- exported as `backend.app.algorithms.regime.execution_pipeline.execute_regime_pipeline`

The decision pipeline delegates completed-bar state transition to:

- `backend/app/algorithms/regime/stateful_core.py`
- exported as `backend.app.algorithms.regime.stateful_core.process_regime_bar`

The Regime algorithm has one production backtest core:

- `backend/app/algorithms/regime/backtest/engine.py`
- exported as `backend.app.algorithms.regime.backtest.engine.run_regime_backtest`

Frontend TypeScript is not authoritative for Regime decisions, sizing, order intents, trade management, or backtests. Frontend Regime code is limited to API clients, DTO mapping, display formatting, polling status, controls, and legacy diagnostics that do not create final decisions or orders.

## Backend-Owned Core

`backend/app/algorithms/regime/` is the only authoritative implementation for:

- market snapshot construction
- classification
- hysteresis and transition handling
- strategy routing and strategy evaluation
- confirmation and context handling
- family aggregation
- local gates
- dynamic profiles
- sizing
- order-intent creation
- trade management
- backtesting

The backend package owns the Regime constants, settings versions, strategy catalog, profile matrix, hysteresis state, counters, decisions, backtest state, execution state, and ML policy. `algorithm_id` must remain `regime`.

## Stateful Decision Core

The deterministic decision interface is state-in/state-out:

```python
result = process_regime_bar(
    snapshot=snapshot,
    settings_snapshot=settings_snapshot,
    previous_state=previous_state,
    inventory_snapshot=inventory_snapshot,
    account_snapshot=account_snapshot,
)
```

`RegimeRuntimeState` is versioned by `regime_runtime_state_v1` and contains confirmed regime, previous confirmed regime, candidate regime, candidate confirmation count, regime start timestamp, dwell bars, unknown-bar count, last processed bar timestamp, last decision ID, cooldown-until state, strategy and family cooldowns, open-position summary, daily counters, circuit-breaker state, and state version.

The completed-bar result contains `decision`, `nextRuntimeState`, `classification`, `transition`, strategy outputs, context outputs, confirmation outputs, safety outputs, family scores, family aggregation, effective profile, local-risk candidate, order proposal, and persistence records. The same state transition function is used by shadow evaluation, paper evaluation, replay, and backtesting.

Decision IDs are deterministic from algorithm instance, runtime mode, symbol, completed-bar timestamp, data-manifest hash, and settings version. The application service restores the active runtime checkpoint before processing a bar. If the latest checkpoint already processed that completed bar, the service returns the existing persisted decision instead of advancing state or creating a second execution-outbox record.

## Background Runtime

API handlers do not run Regime decision evaluation or backtests inline. They enqueue jobs into:

- `backend.app.algorithms.regime.runtime.RegimeBackgroundJobManager`

Current job kinds:

- `decision_evaluation`
- `backtest`
- `settings_activation`

Logical worker responsibilities are declared by the runtime inventory: market processing, strategy evaluation, backtesting, risk processing, execution processing, reconciliation, and position management. The worker executes the existing Python decision pipeline and Python backtest engine; it does not contain a second decision implementation.

## API Boundary

`backend/app/algorithms/regime/api.py` is transport, control, status, and job management only.

Compatibility note: existing frontend client function names remain, but `/api/regime/evaluate` and `/api/regime/backtests/run` now return job receipts. The frontend clients poll `/api/regime/jobs/{job_id}` and resolve to the completed backend result.

Settings compatibility note: runtime evaluation and queued backtests ignore caller-supplied operational settings. `/api/regime/settings/commands` accepts settings-change commands and enqueues `settings_activation`; background processing validates and persists the immutable version before moving the active pointer. `/api/regime/settings/active` returns the restored active snapshot for status and display.

## Versioned Trading Settings

Production settings are owned by:

- `backend.app.algorithms.regime.configuration.RegimeTradingSettings`
- `backend.app.algorithms.regime.repository.RegimeRepository`

The typed settings snapshot contains these sections: `identity`, `runtime`, `data_quality`, `classifier`, `hysteresis`, `strategy_catalog`, `strategy_settings`, `family_aggregation`, `local_risk`, `dynamic_profiles`, `position_sizing`, `entry_policy`, `exit_policy`, `execution`, `daily_limits`, `rollout`, `backtest`, and `ml_shadow`.

Every immutable settings version is persisted in `regime_settings_versions`, the active pointer is persisted in `regime_active_settings`, per-strategy settings are persisted in `regime_strategy_settings`, and activation audit records are persisted in `regime_runtime_events`. The active pointer is scoped by `algorithm_id`, `algorithm_instance_id`, `account_id`, `runtime_mode`, and `symbol`.

The initial paper defaults are conservative, including `baseRiskPercent = 0.10`, `maxPositionPercent = 10.0`, `dailyAllocationPercent = 20.0`, `maxTradesPerDay = 5`, `maxConsecutiveLosses = 3`, `maxDailyLossPercent = 0.50`, `maxParticipationPercent = 0.02`, disabled pyramiding and short entries, `confirmationBars = 3`, `minimumDwellBars = 5`, `transitionConfidenceGap = 0.10`, `cooldownBars = 5`, `entryCutoffTimeEt = 15:30`, and `flattenTimeEt = 15:55`. Finite limits also cover maximum shares, notional, holding bars, order TTL, cancel/replace attempts, slippage, stale bar age, quote age, family risk, per-strategy trades, and minimum net expected edge after costs.

Dynamic profile overlays may reduce risk or tighten explicit baseline bounds; they may not increase risk, enable live trading, enable short entries or pyramiding, loosen slippage, or let ML alter signals, sizing, or orders. Invalid activation fails before the active version is disturbed.

## Persistence

Regime-owned persistence is implemented by:

- `backend.app.algorithms.regime.persistence.RegimeSqliteRepository`
- exposed through `backend.app.algorithms.regime.repository.RegimeRepository`

Regime-owned tables include decision snapshots, classifications, transitions, strategy outputs, context outputs, safety results, family scores, effective profiles, order intents, backtest runs, backtest trades, ML predictions, and ML artifacts.

The complete Regime-owned inventory is:

- `regime_settings_versions`
- `regime_active_settings`
- `regime_strategy_settings`
- `regime_runtime_instances`
- `regime_runtime_commands`
- `regime_runtime_events`
- `regime_runtime_checkpoints`
- `regime_hysteresis_state`
- `regime_daily_counters`
- `regime_strategy_performance`
- `regime_decisions`
- `regime_classifications`
- `regime_transitions`
- `regime_strategy_outputs`
- `regime_context_outputs`
- `regime_confirmation_outputs`
- `regime_safety_results`
- `regime_family_scores`
- `regime_effective_profiles`
- `regime_order_intents`
- `regime_execution_outbox`
- `regime_orders`
- `regime_fills`
- `regime_positions`
- `regime_trades`
- `regime_reconciliation_events`
- `regime_backtest_jobs`
- `regime_backtest_runs`
- `regime_backtest_trades`
- `regime_rollout_evidence`
- `regime_ml_predictions`
- `regime_ml_artifacts`

Every Regime-owned record uses the same ownership key:

- `algorithm_id`
- `algorithm_instance_id`
- `account_id`
- `runtime_mode`
- `symbol`

`algorithm_id` is constrained to `regime`. Mutable runtime-state tables use `sequence_version` for optimistic locking. Processing tables expose `processing_status`. Decisions, state updates, and execution-outbox records are written transactionally. `nextRuntimeState` is written to `regime_runtime_checkpoints` in the same transaction as the decision and order-intent/outbox fan-out.

Shared account and broker tables are infrastructure only and must preserve Regime attribution with `algorithm_id = "regime"`, decision IDs, order-intent IDs, version fields, and related identifiers.

Shared broker-order and raw-fill infrastructure is attribution-only. Broker observations are copied into the authoritative Regime-owned ledgers: `regime_orders`, `regime_fills`, `regime_positions`, `regime_trades`, and `regime_reconciliation_events`.

## Permitted Shared Infrastructure

Regime may use only these shared services:

- read-only market data
- read-only quote and candle caches
- market clock and calendar
- economic-event feed
- read-only account equity and buying-power snapshots
- broker transport for globally approved Regime intents
- global account-risk controls that reduce or reject quantity only
- global risk reservations
- database connection utilities
- logging and telemetry tagged with `algorithm_id=regime`
- shared order-side type definitions
- authentication and API framework transport

Shared infrastructure may never rewrite Regime signals, stops, targets, strategy state, settings, dynamic profiles, hysteresis, performance statistics, backtest state, execution state, positions, trades, or ML artifacts.

## Trading Modes

Regime is restricted to SPY one-minute backtesting, shadow operation, and paper trading. Live trading is not implemented or enabled. Regime ML remains disabled or shadow-only for production decisions and must not alter signals, sizing, or orders.
