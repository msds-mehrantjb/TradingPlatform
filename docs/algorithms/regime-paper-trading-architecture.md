# Regime Paper-Trading Architecture

Audit date: 2026-07-30

## Purpose

This document defines the Regime algorithm architecture for low-frequency, one-minute automatic paper trading. It records the Phase 0 audit of the existing Regime package, database schema, tests, shared event infrastructure, global risk services, Alpaca market-data client, order persistence, and FastAPI startup lifecycle.

Regime remains paper-only. Live trading is not enabled, and `runtimeMode = "live"` must continue to fail closed.

## Ownership

The authoritative Regime implementation lives under `backend/app/algorithms/regime/`. API and frontend paths are transport, display, settings control, and job polling only.

Regime owns:

- Settings and settings history through `RegimeTradingSettings`, `regime_settings_versions`, `regime_active_settings`, and `regime_strategy_settings`.
- Runtime state through `RegimeRuntimeState`, `regime_runtime_checkpoints`, `regime_hysteresis_state`, and `regime_daily_counters`.
- Classifications, transitions, strategy outputs, context outputs, confirmation outputs, safety results, family scores, effective profiles, local-risk results, decisions, order intents, execution outbox records, orders, fills, inventory events, inventory snapshots, positions, trades, daily risk state, reconciliation runs/events, backtests, rollout evidence, runtime alerts, and ML artifacts.

Every Regime-owned record is scoped by:

- `algorithm_id = "regime"`
- `algorithm_instance_id`
- `account_id`
- `runtime_mode`
- `symbol`

Other algorithms must not read, reserve, mutate, or reuse Regime-owned inventory, settings, positions, strategy state, or decision state. Shared infrastructure is allowed only as read-only or transport infrastructure and must preserve Regime attribution.

Regime repositories are explicit Regime repositories. They do not expose a generic repository API where callers can choose an arbitrary `algorithm_id`; repository writes internally resolve and validate `algorithm_id = "regime"`, and fresh SQLite tables include `CHECK (algorithm_id = 'regime')`.

Settings are read and written through the dedicated `RegimeSettingsRepository` facade. It hard-codes Regime attribution and delegates only to Regime-owned settings tables. Runtime workers load the active settings version from this repository; API decision, backtest, and completed-bar request payloads may not supply authoritative settings, account, position, inventory, buying-power, risk-capacity, or P&L state.

Each settings version records an immutable version ID, content hash, creation timestamp, activation timestamp when active, activation status, created-by/source metadata, baseline settings, hard safety limits, strategy lifecycle states, Regime-profile matrix version, and activation or rollback reason. `regime_active_settings` is the authoritative active pointer, with one latest active settings version per algorithm instance, account, runtime mode, and symbol.

## Reused Shared Infrastructure

The audit found existing shared infrastructure that Regime should reuse instead of duplicating:

- Market data and clock: `backend/app/alpaca.py` provides Alpaca bars, latest quote normalization, market clock/calendar status, and local market-status fallback.
- Paper broker gateway: `backend/app/execution/paper_order_gateway.py` provides paper-only broker verification, deterministic client order IDs, idempotency snapshots, stale-order cancellation, restart recovery, global-risk reservations, broker acknowledgements, fills, and protective-order records.
- Neutral order contracts and idempotency helpers: `backend/app/execution/order_contracts.py` and `backend/app/execution/idempotency.py`.
- Global account risk: `backend/app/risk/manager.py` and `backend/app/risk/global_gate_engine.py` provide account-wide approval, reduction, rejection, emergency flatten, duplicate checks, buying-power limits, market-data checks, reconciliation gates, and reservation lifecycle.
- Application lifecycle: `backend/app/main.py` includes the Regime router and starts/stops `get_regime_runtime_supervisor()` during FastAPI startup/shutdown.

Regime may use shared broker connectivity and global risk only after it has produced a complete, attributed, idempotent Regime order intent. Shared risk may reduce or reject quantity. It must not rewrite Regime signals, settings, stops, targets, confidence, strategy output, or trade direction.

## Event Flow

One-minute automation is event driven:

1. A completed one-minute SPY bar is submitted to `/api/regime/runtime/events/completed-bar`.
2. `RegimeFinalisedBarEvent` validates `algorithmId = "regime"`, symbol `SPY`, allowed runtime mode, completed one-minute alignment, and absence of forbidden operational payload state.
3. `RegimeRuntimeSupervisor.publish_completed_bar` persists the event and queues it.
4. `DecisionWorker` calls `process_finalised_bar_event`.
5. The worker loads active settings and computes or verifies the data-manifest hash.
6. A processing lease and idempotency checkpoints are recorded.
7. `RegimeApplicationService.evaluate` restores the latest runtime checkpoint and invokes `execute_regime_pipeline`.
8. The deterministic stateful core classifies the bar, applies hysteresis, evaluates strategies, aggregates families, applies local risk, sizes, creates an order proposal when allowed, and persists the resulting decision/state.
9. The supervisor runs Regime-owned trade management for existing open Regime positions on the same completed bar.
10. New-entry order intents and risk-reducing exit intents are written to `regime_order_intents` and `regime_execution_outbox`.
11. `ExecutionOutboxWorker` submits only pending paper-mode outbox records through `PaperOrderGateway`.
12. Broker observations are copied back into Regime-owned orders, fills, positions, trades, and reconciliation events.

API handlers do not execute authoritative decisions, sizing, trade management, broker submission, fill handling, reconciliation, or backtests inline.

## State Transitions

The deterministic decision transition is state-in/state-out:

```python
result = process_completed_bar(
    snapshot=snapshot,
    settings_snapshot=settings_snapshot,
    previous_state=previous_state,
    inventory_snapshot=inventory_snapshot,
    account_snapshot=account_snapshot,
)
```

`process_completed_bar` delegates to the Regime stateful core and is the public transition contract exposed in inventory metadata. The result includes the decision, next runtime state, classification, transition, strategy outputs, family aggregation, effective profile, local-risk result, order proposal, order validation, global-risk approval, broker-submission payload, sizing, and trade-management evidence.

Runtime idempotency stages are durable. They include event receipt, snapshot validation, decision completion, decision persistence, risk request/reservation, outbox creation, position management, order submission, broker acknowledgement, fill observation, inventory reconciliation, and position close.

If a completed bar was already processed, the worker returns the existing persisted decision and does not create a duplicate economic order.

Effective settings are resolved in this order:

1. Regime immutable baseline.
2. Confirmed-regime profile.
3. Volatility overlay.
4. Liquidity overlay.
5. Session overlay.
6. Economic-event overlay.
7. Regime local-risk reduction.
8. Shared global-risk reduction or rejection.

Dynamic overlays may disable strategies, tighten thresholds, reduce risk or size, restrict order types, reduce holding time, or disable new entries. They must not exceed hard baseline risk, position, trade-count, slippage, or daily-loss caps. The complete effective-settings snapshot and reason codes are persisted with every decision in `regime_effective_profiles`.

## Deterministic Classifier

The deterministic Regime classifier is backend-authoritative and point-in-time. It produces separate axes for direction, trend strength, volatility, market structure, liquidity, session, event risk, and data quality. Canonical Regime states are `strong_uptrend`, `weak_uptrend`, `strong_downtrend`, `weak_downtrend`, `range_bound`, `low_volatility_quiet`, `opening_breakout`, `intraday_expansion`, `high_volatility_trend`, `failed_breakout_reversal`, `gap_session`, `choppy_mixed`, `event_risk`, `liquidity_stress`, `extreme_volatility_no_trade`, and `unknown`. `sideways_range` remains a legacy alias, not a canonical state.

Insufficient classifier warm-up returns `unknown`, records `regime.classifier.insufficient_warmup.*` reason codes, and disables new entries through the `unknown` no-entry profile. Market-data validation failures also resolve to canonical `unknown` while preserving the validation reason codes on the decision.

Confirmed Regime changes use hysteresis. A single abnormal candle cannot immediately confirm a non-risk Regime transition; the transition candidate and confirmation count are persisted with separate enter and exit threshold evidence. Protective risk-off regimes may still take over immediately. Session-scoped classifier and runtime state resets at the exchange-session boundary so opening/session-dependent evidence does not inherit stale candidate counts, daily counters, or cooldowns from the prior session.

Phase 6 makes this hysteresis state durable per Regime identity and symbol. Runtime checkpoints now carry the last confirmed regime, candidate regime, candidate start timestamp, consecutive confirmation bars, regime confidence, last transition timestamp, bars in current regime, transition reason, and state version. On restart, the stateful core restores those fields before classification and transition evaluation, including safety `unknown` states.

The application service rejects duplicate and out-of-order completed bars before the decision pipeline can mutate state. Exact duplicate bars return the already persisted decision when available; otherwise they return an ignored-bar Hold result without writing a new checkpoint. Older bars return an ignored-bar Hold result and preserve the current runtime checkpoint. No-trade safety regimes can still activate immediately, while recovery from safety regimes requires confirmation bars.

## Inventory Ledger

`regime_inventory_events` is the append-only authoritative inventory event ledger. `regime_inventory_snapshots` is the materialized current inventory view. Position quantity, average entry price, realized P&L, and flat/open state change only from confirmed broker fills or broker corrections. Decisions, order proposals, submitted order quantities, and outbox status changes do not change authoritative inventory quantity.

Inventory snapshots include symbol, runtime mode, quantity, average entry price, realized P&L, unrealized P&L, reserved cash, reserved risk, open-order quantity, position ID, trade ID, last broker reconciliation time, state version, and decision/intent/order/fill attribution.

The ledger handles partial fills, multiple fills, duplicate fill IDs, entry fills, exit fills, stop fills, end-of-day liquidation fills, terminal order statuses, replacements, and broker corrections. Terminal order statuses such as cancelled, rejected, and expired can update open-order quantity, but not position quantity or P&L.

Inventory event application uses a SQLite `BEGIN IMMEDIATE` transaction around event insertion and snapshot materialization, plus deterministic inventory event IDs, so two workers cannot concurrently advance the same Regime inventory snapshot.

## Order and Execution Flow

Regime creates order intents in backend workers only.

New entries:

- Created by the deterministic decision pipeline only after local risk, sizing, order validation, and global-risk proposal construction.
- Written to `regime_order_intents` and fanned out to `regime_execution_outbox`.
- Submitted only when runtime mode is `paper`, recovery has succeeded, inventory is reconciled, and the paper gateway is available.

Risk-reducing exits:

- Created by Regime trade management when a completed bar triggers stop, target, time stop, maximum holding bars, end-of-day flatten, risk-off transition, regime/strategy invalidation, emergency flatten, stale protective order, or reconciliation-discrepancy exit.
- Marked `paperOnly = true` and `liveTradingEnabled = false`.
- Receive a Regime-owned local-risk approval record before outbox insertion.
- Use deterministic exit intent IDs and idempotency keys so restarts do not create duplicate economic orders.
- Use only Regime-owned positions with fill-level attribution. A shared account position is never treated as the Regime position unless the Regime fill ledger, order intent, position ID, and trade ID can attribute it.
- Cap exit quantity to the Regime-owned filled quantity and mark `opensReversePosition = false`; closing logic must not open an unapproved reverse position.
- Preserve the filled quantity immediately after a partial fill by recording protective child-order evidence against the Regime position.
- Apply trailing stops only when the effective profile or exit policy explicitly enables `trailingExitsEnabled`, and only in the direction that tightens risk.

The shared paper gateway may submit only attributed Regime proposals. It verifies paper account status, applies shared global risk, records idempotent client-order snapshots, handles broker acknowledgements/fills, cancels stale orders, and records execution-cost observations.

Phase 13 execution uses a Regime-owned transactional outbox. The decision transaction persists the decision, order intent, global-risk approval/reservation metadata, and a `regime_execution_outbox` row before any broker call is possible. A separate execution worker reads durable outbox records and is the only Regime path that may submit to the shared paper gateway.

The durable outbox state machine is:

`created -> risk_approved -> queued -> submitting -> acknowledged -> partially_filled -> filled`

Terminal and exception states are `cancel_pending`, `cancelled`, `rejected`, `expired`, `reconciliation_required`, and `dead_letter`. `pending`, `risk_reserved`, `submitted`, and `cancel_requested` remain readable as legacy compatibility aliases for records created before the Phase 13 vocabulary. Safe retries use `retry_scheduled` with bounded backoff metadata. Unsafe broker interruptions move to `reconciliation_required` instead of resubmitting blindly.

Every outbox record carries the immutable order proposal, local-risk result, shared global-risk application, deterministic broker client order ID, retry policy, stale-entry expiration evidence, replacement policy, and paper broker safety evidence. Replacement is cancel-and-new-intent only; in-place mutation of an existing economic order is not permitted.

Regime paper submission refuses `live` or unknown runtime modes, rejects market entry order types, and blocks broker configurations that identify a live Alpaca base URL, live account type, disabled paper-only mode, enabled live trading, or unverified credentials. The worker never falls back from paper to live.

## Failure Handling

Regime fails closed for:

- Invalid or non-finalized market events.
- Unsupported runtime modes, especially `live`.
- API payloads containing settings, inventory, positions, fills, trades, orders, runtime state, or other operational state.
- Missing active settings.
- Database persistence failures.
- Stale or out-of-order completed bars.
- Queue overflow or excessive processing lag.
- Paper gateway unavailability.
- Broker submission interruption.
- Missing or stale local-risk approval.
- Reconciliation discrepancies.
- Recovery failure or unreconciled inventory.

New entries are paused during recovery, unresolved reconciliation, operator pause, unhealthy settings/database/runtime state, broker unavailability, and queue lag. Risk-reducing exits remain allowed where possible so existing positions can be protected.

## Restart Behavior

On startup, FastAPI starts the Regime runtime supervisor. Recovery:

1. Loads active settings.
2. Restores the latest runtime checkpoint.
3. Recovers unfinished outbox records.
4. Detects abandoned processing leases.
5. Rebuilds or verifies the materialized inventory snapshot from `regime_inventory_events`.
6. Reconciles the rebuilt snapshot and Regime position records against the paper broker when a paper gateway is available.
7. Keeps new entries paused until inventory reconciliation succeeds.

Durable stage checkpoints allow the worker to distinguish a fully persisted completed decision from a partially processed event. Completed duplicate events return the existing decision. Unfinished outbox records are recovered instead of blindly resubmitted.

Phase 15 adds `backend.app.algorithms.regime.reconciliation.run_regime_broker_reconciliation` as the Regime-owned broker reconciliation worker path. It runs at startup, periodically during the session, after broker/network or ambiguous submission results, after order-update gaps, after finalized-bar processing, and with a before-end-of-day-shutdown trigger near the close. The worker compares the latest Regime outbox state, Regime order and fill records, paper-gateway broker observations, broker open orders, broker fills, Regime inventory ledger/snapshot state, Regime positions, and broker account positions.

Broker observations can update Regime inventory only when ownership is proven from existing Regime attribution: order-intent ID, deterministic client order ID, broker order ID, position ID, trade ID, or explicit `algorithmId = "regime"` tied to a known Regime record. An unattributed broker position is never assigned to Regime. On mismatch, reconciliation records a durable `regime_reconciliation_runs` row and a `regime_runtime_alert`, blocks new entries, keeps risk-reducing/protective handling available where safe, and requires manual review when ownership cannot be proven. Deterministic recovery is limited to replaying known broker order/fill observations into Regime-owned order, fill, position, and inventory stores.

## API Control Plane

Phase 16 makes `/api/regime/evaluate` a non-authoritative control-plane endpoint only. It accepts three request shapes: a diagnostic shadow read using repository-loaded state, a trusted finalized-bar event reference that is re-enqueued through the runtime supervisor, or a read-only explanation request for an already persisted decision. It rejects caller-supplied market data, settings, account, inventory, position, sizing, order, fill, or decision state.

`/api/regime/backtests/run` enqueues a background backtest job and returns immediately with a job ID plus status/result endpoints. Backtest execution remains in backend workers. Read-only API endpoints expose runtime health, last processed bar, active settings version, strategy inventory, current confirmed regime, Regime-owned inventory, open Regime orders, reconciliation status, paper rollout stage, recent decisions/blockers, and backtest jobs.

Settings creation and validation endpoints may transport candidate settings into the backend validation path. Activation and rollback require explicit version, actor/source metadata, and reason text before a backend-audited settings command is queued.

## Backtesting and Replay

Regime backtests run in backend workers through `RegimeBackgroundJobManager` and durable `regime_backtest_jobs`; API routes enqueue jobs and return receipts. Backtests use `backend.app.algorithms.regime.backtest.engine.run_regime_backtest` and must not mutate paper inventory, paper settings, or paper positions.

Replay and backtest paths share the deterministic stateful core so paper/shadow decisions and replay decisions remain comparable for identical point-in-time input.

Phase 17 makes that parity explicit in every backtest result under `parity`, `replay`, `restartDeterminism`, `dailySessionReplays`, and `holdoutPolicy`. The backtest engine uses the same production market snapshot builder, stateful completed-bar core, strategy registry and implementations, dynamic profile, family aggregation, local gates, shared execution-cost adapter, sizing, entry-intent logic, and backend exit policy as paper processing. Only the data, clock, broker-fill simulation, and persistence adapters are allowed to differ.

Backtest replay is modeled as finalized one-minute bar events. For each replayed bar, the engine builds point-in-time one-minute history, derives 5-minute and 15-minute candles from finalized one-minute bars, carries forward durable runtime state, and records a deterministic decision fingerprint. Supplied higher-timeframe bars are not used as confirmed evidence. Order simulation remains a broker-fill adapter and may model next-bar execution, spread, slippage, fees, partial fills, and expiration without changing the authoritative decision components.

The replay result records deterministic hashes for decisions, trades, and the replay manifest. Running the same replay with the same versions, settings, and input data must produce the same hashes. Walk-forward and holdout evidence is reported by the backtest result, and the holdout policy explicitly records that optimization on the holdout set is not allowed.

## ML Policy

Regime ML remains shadow or confirm-only. ML artifacts, predictions, paper-stability evidence, and promotion decisions are Regime-owned. ML must not independently create an order, reverse a deterministic signal, increase size, loosen risk, promote itself, or enable live trading.

Phase 18 keeps ML optional for automatic Regime paper trading. The deterministic Regime pipeline must continue to classify, route, size, risk-check, generate intents, manage exits, and backtest/replay when ML is unavailable or explicitly `off`.

ML predictions are stored separately from deterministic decisions under Regime-owned ML stores and default to `shadow`. A shadow prediction records its baseline deterministic decision ID and declares no direction, sizing, gate, or order authority. `confirm_only` is the maximum automatic promotion mode and still cannot create direction, increase quantity, loosen a gate, or submit an order.

Promotion evidence must be backend-recorded and tied to durable replay, walk-forward, untouched holdout, and paper-stability evidence references. Frontend, browser, API-client, or otherwise untrusted promotion evidence is rejected. Promotion decisions require versioned artifacts, feature schema, labels, deterministic baseline version, audit metadata, activation reason, retained rollback artifact, current evidence, paper-stability evidence, and clean operational/reconciliation history. A promotion can only target `confirm_only`; otherwise ML remains `shadow`.

## Paper-Trading Rollout

Rollout is staged and evidence-based:

1. Offline/backtest validation only.
2. Background shadow operation with completed-bar events and no broker submission.
3. Paper intent validation with durable order intents and outbox records, but no broker submission.
4. Limited SPY automated paper trading after explicit paper-submission flag and readiness evidence.
5. Expanded paper validation only after stable operational, fill, cost, slippage, reconciliation, and P&L evidence.

Live trading is not a rollout stage. Any `liveTradingEnabled` setting or runtime mode is invalid for Regime.

## Monitoring And Operational Safety

Phase 19 adds Regime-specific operational telemetry with `algorithmId = "regime"` on every snapshot, alert, heartbeat, command audit, and health result. Runtime observability reports supervisor heartbeat, last received/finalized/processed bar, processing latency, queue depth, queue lag, duplicate bars, missing bars, stale-data state, current confirmed regime, current strategy routing, entry blockers, active settings version, Regime-owned inventory, open Regime orders, risk reservations, outbox status, reconciliation status, broker connectivity, daily Regime P&L, daily trade count, and kill-switch state.

The Regime kill switch is a persisted runtime state, not a frontend-owned decision path. Activating it blocks new entries immediately, records an audit event, persists `kill_switch` runtime state for restart recovery, and can mark pending entry outbox records as `cancel_requested`. Risk-reducing exits, protective position management, reconciliation, and inventory protection remain allowed while the kill switch is active.

Regime health fails closed when the supervisor is stopped, data is stale, reconciliation is unresolved, settings or inventory cannot be loaded, paper broker mode is not verified, the active strategy registry is invalid, the execution outbox is stuck, or risk reservations are inconsistent. These failures are exposed through component health and persisted runtime alerts where possible.

## Contract Changes Recorded In This Branch

- `productionStateTransitionCore` inventory metadata now reports `backend.app.algorithms.regime.stateful_core.process_completed_bar`, the public compatibility wrapper around the same deterministic stateful core.
- Runtime idempotency stages now include `position_management`.
- `trade_management.py` now exports `manage_regime_positions_for_completed_bar` while preserving `evaluate_regime_exit`.
- `estimate_entry_transaction_cost_bps` remains an entry-cost compatibility helper; the authoritative local-risk path continues to use round-trip cost through `estimate_round_trip_transaction_cost_bps`.
- Phase 1 adds explicit Regime-owned durable stores for runtime state, bar processing, inventory events, inventory snapshots, daily risk state, reconciliation runs, and runtime alerts.
- Confirmed broker fills now advance the Regime-owned inventory ledger and current inventory snapshot before position/trade restore state is recorded.
- Startup recovery now verifies or rebuilds `regime_inventory_snapshots` from `regime_inventory_events` and records reconciliation runs.
- Phase 2 adds `RegimeSettingsRepository`, explicit settings activation metadata, stricter rejection of authoritative runtime request fields, internal-only account/inventory snapshots, and reason-coded effective-settings overlays.
- Phase 3 uses the finalized-bar idempotency key `regime:{mode}:{symbol}:{bar_close_timestamp}:{algorithm_version}:{settings_version}` once active settings are loaded. The data-manifest hash remains persisted as market-data-quality evidence, but it cannot create a second decision for the same finalized bar.
- Phase 3 adds fresh-schema unique indexes preventing duplicate finalized-bar decisions and duplicate entry intents for the same Regime identity, bar timestamp, algorithm version, and settings version. Exit intents remain independently idempotent by Regime exit order-intent ID.
- Phase 3 recovery reads durable `finalised_bar` runtime events that did not reach `decision_persisted` and requeues them through the same decision worker path. Recovered stale events block new entries but continue exits, reconciliation, and inventory protection.
- Phase 3 finalized-bar event validation rejects unsupported symbols, incomplete bars, non-one-minute timeframes, publication before bar close, and exchange-calendar-invalid timestamps before a worker can process the event.
- Phase 4 adds `market_data_validation.py` as the fail-closed market-data validation layer before classification in the stateful core. Malformed, incomplete, future-dated, off-session, duplicate, out-of-order, wrong-interval, missing-bar, stale-bar, negative-volume, unsupported-symbol, and unhandled corporate-action bar evidence produces a persisted Hold decision with explicit reason codes.
- Phase 4 records quote-quality validation evidence without bypassing existing Regime stale-quote classification and safety-gate behavior. Bid/ask, quote age, and spread evidence are persisted under the market-data validation report.
- Phase 4 derives confirmed 5-minute and 15-minute bars point-in-time from finalized one-minute candles in `market_snapshot.py`; supplied higher-timeframe bars are ignored as confirmed evidence unless a future partial-evidence strategy explicitly owns that contract.
- Phase 4 persists `dataTimestamp`, `featureTimestamp`, and the complete market-data validation report on every Regime decision result.
- Phase 5 makes `unknown` canonical, moves `sideways_range` to legacy aliases, adds explicit `trend_strength` and `data_quality` axes, maps warmed opening-window reference breaks to `opening_breakout`, maps opening directional trend without a confirmed break to `gap_session`, and gives `unknown` a no-entry dynamic profile.
- Phase 5 removes non-risk immediate hysteresis promotion based solely on a high confidence score. Confirmed non-risk Regime transitions require candidate confirmation bars, while risk-off states still fail closed immediately.
- Phase 5 resets session-scoped runtime counters, strategy/family cooldowns, and hysteresis candidate counts across exchange-session boundaries.
- Phase 6 persists expanded hysteresis state in `regime_runtime_checkpoints`, `regime_runtime_state`, and `regime_hysteresis_state`, including candidate start, regime confidence, last transition, bars-in-regime, transition reason, and state version.
- Phase 6 rejects duplicate and out-of-order completed bars in the Regime service before invoking the authoritative decision pipeline, so those bars cannot increase confirmation counts or mutate runtime state.
- Phase 6 applies minimum dwell periods to non-safety transitions and asymmetric recovery from no-trade safety regimes.
- Phase 17 adds computed backtest replay/parity metadata, deterministic replay fingerprints, per-session replay summaries, and holdout-policy evidence to Regime backtest results.
- `evaluate_regime_exit` now accepts an optional settings snapshot for Regime maximum-holding-time, end-of-day flatten, and trailing-exit parity while preserving hold behavior for callers that do not pass settings.
- Regime local-risk evaluation in the completed-bar core is anchored to the finalized bar timestamp when available, improving deterministic replay and restart parity.
- Phase 18 requires backend ML promotion evidence sources and durable replay, walk-forward, holdout, and paper-stability evidence IDs before `confirm_only` can be allowed.
- Phase 18 rejects frontend/API/client ML promotion evidence at the persistence boundary and no longer infers old ML artifact rows as trusted by default.
- Phase 18 expands ML shadow prediction metadata to explicitly deny direction creation, signal reversal, size increases, gate loosening, and order authority.
- Phase 19 adds Regime operational telemetry fields and component-health derivation for supervisor, stale data, reconciliation, settings, inventory, broker paper-mode verification, strategy registry, outbox, and risk-reservation failures.
- Phase 19 adds persisted Regime kill-switch runtime state and audited activate/deactivate control-plane endpoints.
- Phase 19 kill-switch activation can request cancellation of pending Regime entry outbox records while preserving risk-reducing exit/protection authority.
- Phase 21 adds persisted operational rollout stages:
  `disabled -> decision_shadow -> simulated_execution -> limited_paper -> normal_paper`.
- The default operational stage is `decision_shadow`, so the runtime supervisor continues processing finalized one-minute bars and recording decisions while suppressing entry intents, execution outbox rows and broker submission.
- `regime_hypothetical_fills` stores decision-shadow hypothetical fills separately from authoritative `regime_fills`; Regime inventory still changes only from confirmed broker fills.
- `simulated_execution` creates real Regime order intents and outbox records but routes them through a deterministic fake paper broker inside backend workers.
- `limited_paper` and `normal_paper` require backend-recorded promotion evidence before real paper submission can run. API callers cannot supply promotion evidence.
- Limited paper remains SPY-only, long-only, one-position, no-pyramiding, limit/stop-limit, mandatory-stop and end-of-day-flatten constrained by Regime settings, local risk, cost gating, global risk and broker-paper verification.
- Unresolved reconciliation, stale data, kill switch, invalid registry, paper-broker safety failure or missing promotion evidence fail closed and block new entries while preserving protective exits.

## Phase 0 Test Record

Current branch:

- `python -m pytest backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 271 passed.
- `npm test` in `frontend`: 4 passed.
- `npm run build` in `frontend`: passed.
- `python -m pytest backend/tests`: completed with failures in legacy/non-Regime broad-suite areas; the Regime-focused suite passed.

Clean `origin/main` comparison:

- A temporary worktree at `.tmp/origin-main-audit` was created from commit `81b8d2f`.
- `python -m pytest backend/tests` against `origin/main` timed out after 20 minutes and showed the same broad pre-existing failure pattern before this branch's new Step 11 test exists.

Introduced failures:

- A timestamp-staleness failure in the new Step 11 test was observed during the first full backend run and fixed by generating event timestamps inside the test scenario.
- After the fix, the new Step 11 test and Regime-focused suite pass.

Phase 1 verification:

- `python -m pytest backend/tests/regime/test_phase1_inventory_ledger.py`: 3 passed.
- `python -m pytest backend/tests/regime/test_persistence_isolation_boundary.py backend/tests/test_regime_phase14_persistence.py backend/tests/test_regime_step2_inventory_isolation.py backend/tests/test_regime_step11_execution_outbox.py backend/tests/regime/test_step7_paper_execution_positions.py`: 36 passed.
- `python -m pytest backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 274 passed.

Phase 2 verification:

- `python -m pytest backend/tests/regime/test_phase2_settings_repository.py`: 3 passed.
- `python -m pytest backend/tests/regime/test_phase2_settings_repository.py backend/tests/regime/test_versioned_settings_boundary.py backend/tests/regime/test_step5_background_runtime.py backend/tests/regime/test_step9_fail_closed_health.py backend/tests/test_regime_backend_boundary.py`: 24 passed.
- `python -m pytest -q backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 277 passed.
- `npm test` in `frontend`: 4 passed.
- `npm run build` in `frontend`: passed.
- `python -m pytest -q backend/tests`: timed out after 15 minutes at 66% with broad pre-existing/non-Regime failures still present; no Regime-focused Phase 2 failure was observed in the passing focused suite.

Phase 3 verification:

- `python -m compileall backend/app/algorithms/regime`: passed.
- `python -m pytest -q backend/tests/regime/test_phase3_event_runtime.py`: 4 passed.
- `python -m pytest -q backend/tests/regime/test_phase3_event_runtime.py backend/tests/regime/test_step5_background_runtime.py backend/tests/regime/test_step7_paper_execution_positions.py backend/tests/regime/test_step11_event_driven_trade_management.py backend/tests/regime/test_persistence_isolation_boundary.py backend/tests/test_regime_phase14_persistence.py`: 29 passed.
- `python -m pytest -q backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 281 passed.

Phase 4 verification:

- `python -m compileall backend/app/algorithms/regime`: passed.
- `python -m pytest -q backend/tests/regime/test_phase4_market_data_validation.py`: 3 passed.
- `python -m pytest -q backend/tests/regime/test_phase4_market_data_validation.py backend/tests/regime/test_phase3_event_runtime.py backend/tests/regime/test_step5_background_runtime.py backend/tests/regime/test_step11_event_driven_trade_management.py backend/tests/regime/test_step6_local_risk.py backend/tests/regime/market/test_context_feeds.py backend/tests/regime/market/test_real_feed_validation.py backend/tests/test_regime_phase14_persistence.py`: 65 passed.
- `python -m pytest -q backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 284 passed.

Phase 5 verification:

- `backend\.venv\Scripts\python -m compileall backend\app\algorithms\regime`: passed.
- `backend\.venv\Scripts\python -m pytest -q backend/tests/regime/classification backend/tests/test_regime_step5_classifier_corrections.py backend/tests/regime/test_phase5_classifier_production_safety.py`: 54 passed, 16 subtests passed.
- `backend\.venv\Scripts\python -m pytest -q backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 289 passed, 125 subtests passed.

Phase 6 verification:

- `backend\.venv\Scripts\python -m compileall backend\app\algorithms\regime`: passed.
- `backend\.venv\Scripts\python -m pytest -q backend/tests/regime/transitions backend/tests/regime/test_phase5_classifier_production_safety.py`: 16 passed.
- `backend\.venv\Scripts\python -m pytest -q backend/tests/regime backend/tests/test_regime_backend_boundary.py backend/tests/test_regime_step8_profiles_local_risk.py`: 294 passed, 125 subtests passed.

Phase 17 verification:

- `backend\.venv\Scripts\python -m pytest backend\tests\regime\backtest\test_phase17_replay_paper_parity.py backend\tests\regime\backtest\test_engine.py backend\tests\regime\backtest\test_engine_correctness.py backend\tests\regime\trade_management -q`: 11 passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_step8_background_backtest_parity.py backend\tests\test_regime_step13_backtest_parity.py backend\tests\regime\execution\test_runtime_parity.py -q -k "not test_step8_backtest_run_api_enqueues_job_without_inline_execution"`: 10 passed, 1 deselected.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime backend\tests\test_regime_backend_boundary.py backend\tests\test_regime_step11_execution_outbox.py -q -k "not test_step8_backtest_run_api_enqueues_job_without_inline_execution"`: 352 passed, 1 deselected, 138 subtests passed.
- `backend\.venv\Scripts\python -m compileall backend\app\algorithms\regime`: passed.
- `git diff --check`: no whitespace errors; Git reported existing LF-to-CRLF normalization warnings.

Phase 18 verification:

- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase18_ml_shadow_only.py backend\tests\test_regime_ml_promotion_policy.py -q`: 15 passed, 29 subtests passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime backend\tests\test_regime_ml_promotion_policy.py backend\tests\test_regime_backend_boundary.py backend\tests\test_regime_step11_execution_outbox.py -q -k "not test_step8_backtest_run_api_enqueues_job_without_inline_execution"`: 367 passed, 1 deselected, 167 subtests passed.

Phase 19 verification:

- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase19_monitoring_operational_safety.py backend\tests\regime\test_step9_fail_closed_health.py backend\tests\test_regime_step11_execution_outbox.py -q`: 23 passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime backend\tests\test_regime_ml_promotion_policy.py backend\tests\test_regime_backend_boundary.py backend\tests\test_regime_step11_execution_outbox.py -q -k "not test_step8_backtest_run_api_enqueues_job_without_inline_execution"`: 371 passed, 1 deselected, 167 subtests passed.

Phase 20 verification:

- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase20_focused_tests.py -q`: 8 passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_coverage_manifest.py backend\tests\regime\test_behavioral_ci_contract.py backend\tests\regime\test_phase20_focused_tests.py -q`: 18 passed, 114 subtests passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime backend\tests\test_regime_ml_promotion_policy.py backend\tests\test_regime_backend_boundary.py backend\tests\test_regime_step11_execution_outbox.py -q -k "not test_step8_backtest_run_api_enqueues_job_without_inline_execution"`: 379 passed, 1 deselected, 167 subtests passed.

Phase 21 verification:

- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase21_staged_paper_rollout.py -q`: 5 passed.
- `backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase21_staged_paper_rollout.py backend\tests\regime\test_rollout_acceptance_contract.py backend\tests\test_regime_phase17_rollout.py backend\tests\test_regime_phase14_persistence.py backend\tests\test_regime_step2_inventory_isolation.py backend\tests\regime\test_persistence_isolation_boundary.py backend\tests\regime\test_phase16_api_control_plane.py backend\tests\regime\test_step7_paper_execution_positions.py -q`: 58 passed.
