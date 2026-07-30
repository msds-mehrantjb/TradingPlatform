# WCA Automatic Paper Readiness Plan

Phase 0 baseline audit. This document maps the automatic paper trading mission requirements to the current implementation and the remaining file-level work. It does not promote any acceptance item from `PENDING` to `PASS` based on code presence alone.

## Audit Anchor

- Branch: `main`
- Commit: `d9cae87fa44093f3185736dd18830cfb62b25456`
- Commit label: `d9cae87 (HEAD -> main, origin/main) weighted Voting Algorithm Updated`
- Baseline worktree: dirty. Existing local changes were present during this audit, including WCA paper-account guard work, script changes, and a pytest temp folder.
- New Phase 0 artifact: `docs/wca/automatic_paper_readiness_plan.md`

## Baseline Test Evidence

| Command | Result | Existing failures |
| --- | --- | --- |
| `$tests = Get-ChildItem -LiteralPath 'backend\tests' -Filter 'test_wca*.py' \| Sort-Object Name \| ForEach-Object { $_.FullName }; backend\.venv\Scripts\python.exe -m pytest @tests -q` | `286 passed, 4 failed, 977 warnings, 565 subtests passed` | `test_wca_step16_diagnostics.py::test_aggregate_diagnostics_separate_gross_net_costs_and_drawdown`; `test_wca_step16_diagnostics.py::test_global_rejected_orders_have_counterfactuals_without_executed_trades`; `test_wca_step2_legacy_backend_engine.py::test_api_evaluate_and_configuration_routes`; `test_wca_step2_legacy_backend_engine.py::test_api_rejects_missing_strategy_snapshot` |
| `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_global_account_risk_state.py backend\tests\test_global_gate_engine.py backend\tests\test_global_decision_interface.py backend\tests\test_execution_cost_model.py backend\tests\test_execution_simulation.py backend\tests\test_broker_reconciliation.py backend\tests\test_algorithm_ownership_ledger.py backend\tests\test_algorithm_module_inventories.py backend\tests\test_ci_quality_gates.py -q` | `64 passed, 927 warnings, 5 subtests passed` | None in this selected shared backend suite |

### Failure Notes

- Diagnostics backtest failures show fake BUY voters do not create executed trades or global rejection diagnostics as expected. This blocks using diagnostics tests as acceptance evidence.
- Legacy backend API tests expect synchronous `/api/wca/evaluate` and updated configuration response shapes, but current API behavior enqueues background jobs and returns `202` for evaluation. This is likely an intentional boundary change, but the test suite is not reconciled with it.
- Because existing WCA tests fail, Phase 0 must keep "Critical tests pass" operationally unaccepted for automatic paper readiness, even if some ledger constants contain PASS defaults.

## Requirement Matrix

| Requirement | Existing implementation | Missing implementation | Files requiring changes | Required migrations | Required tests | Current status | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WCA identity is `algorithm_id = "wca"` and owns its own state | `WCA_ALGORITHM_ID`; WCA package under `backend/app/algorithms/wca`; WCA-prefixed tables in `repository.py`; isolation tests | No known duplicate package needed; verify dirty working tree before acceptance | Mostly none; possible final audit in `contracts.py`, `repository.py`, tests | None expected | Existing isolation tests plus full green WCA suite | Implemented, not newly accepted in Phase 0 | Existing tests cover isolation, but baseline WCA suite is not green |
| WCA must not read or mutate other algorithms' mutable state | Runtime structure scan prevents imports from sibling algorithm runtime stores; WCA repository is isolated | Need complete acceptance scan after all automatic-paper changes | `runtime_supervisor.py`, `repository.py`, `test_wca_step1_backend_structure.py`, `test_wca_step19_comprehensive.py` | None expected | Architecture boundary scan and comprehensive WCA tests | Mostly implemented | No cross-algorithm mutation evidence still required for rollout |
| Shared services are restricted to market data, account info, calendar, logging, global risk, transport, persistence infrastructure | Shared global risk adapter and broker transport contracts exist; API is transport/presentation; repository uses shared DB path only | Need proof that automatic runtime account, risk, and broker reads are read-only except explicit global-risk reservations | `global_risk.py`, `runtime_supervisor.py`, `execution_pipeline.py`, `paper_broker.py`, `broker_reconciliation.py` | Possibly none; may need evidence rows if shared-service decisions are persisted beyond existing tables | Tests for no shared mutable state access during runtime worker processing | Partial | Shared backend tests pass, rollout evidence absent |
| Strict dedicated Alpaca paper account for automatic WCA paper trading | Current dirty tree has `paper_account.py` and env examples for `WCA_ALPACA_PAPER_*`; runtime outbox worker validates before broker submission | No real WCA Alpaca paper transport exists; no live account verification call; no accepted broker account evidence persisted; guard is uncommitted baseline work | `paper_account.py`, `paper_broker.py`, `runtime_supervisor.py`, `.env.example`, docs | Possibly add persisted paper-account validation result to `wca_runtime_health`, `wca_execution_outbox`, or new table if evidence must be queryable | Tests for missing env, live URL, shared generic credentials, account mismatch, valid dedicated account, no fallback to `APCA_*` | Partial, unaccepted | Focused tests pass in dirty tree, but no live paper account verification evidence |
| `WCA_ALPACA_PAPER_BASE_URL` must equal `https://paper-api.alpaca.markets` | Dirty tree guard enforces exact value | Need ensure any future HTTP transport also uses this validated URL and does not append live endpoints | `paper_account.py`, future Alpaca paper transport | None unless persisting validation | Unit and integration tests around transport construction | Partial | Guard unit tests exist in dirty tree |
| Do not reuse another algorithm's paper credentials | Dirty tree rejects WCA credentials equal to generic `APCA_*` values | Cannot verify credentials belong to distinct broker account until account API is queried | `paper_account.py`, future broker transport | Evidence persistence may be needed | Tests with generic env reuse; integration account-id check | Partial | Unit-level only |
| API/frontend must only request or read; background workers own decisions, orders, reconciliation, recovery, backtests | `api.py` routes enqueue commands/jobs for paper, backtests, shadow, reconciliation, pause/resume, risk reduction; docs say API is transport only | `WcaService.execute_paper()` still runs pipeline inline when called directly; legacy tests disagree with enqueue-only route behavior | `service.py`, `api.py`, `test_wca_step2_legacy_backend_engine.py`, `test_wca_step15_api_frontend_control_surface.py` | None expected | Tests should distinguish API boundary from service compatibility helpers | Partial | API enqueue tests pass; legacy API tests fail |
| One-minute finalized candles are the decision clock | `runtime_events.py` requires `is_finalized`; `runtime_repository.py` rejects duplicate/stale/out-of-order events; `runtime_supervisor.py` consumes finalized-bar commands | Need real market-data publisher integration and persisted event-lag evidence; stale entry-age limit must be accepted under production feed | `runtime_events.py`, `runtime_repository.py`, `runtime_supervisor.py`, market data publisher integration | Existing tables likely sufficient | Runtime event tests plus integration replay from finalized bar feed | Implemented structurally, unaccepted operationally | Existing runtime tests pass except broader WCA suite failures elsewhere |
| No entries from unfinished candles, frontend refresh, REST request, timer, duplicate event, or stale event | Runtime queue rejects incomplete/duplicate/stale/out-of-order events; API routes enqueue commands instead of evaluating inline | Need prove frontend cannot trigger automatic entries directly; reconcile legacy frontend behavior/docs | `frontend/src/features/wca`, `frontend/src/main.ts`, `api.py`, runtime tests | None expected | Frontend boundary and runtime event tests | Partial | Step 18/frontend presentation tests need full-suite green status |
| Authoritative state must not come from callers | Repository owns WCA decisions, orders, fills, lots, virtual positions, trade ledger, reconciliation | `WcaPaperExecutionRequest` still includes caller-provided `accountEquity`, `availableBuyingPower`, `tradesToday`, `realizedDailyLoss`, `currentPosition*`, risk budgets; `service.execute_paper()` feeds them into pipeline | `contracts.py`, `service.py`, `runtime_supervisor.py`, `repository.py`, `global_risk.py`, `broker_reconciliation.py` | Possible migration if account snapshots/reserved risk need WCA-owned persistence beyond current tables | Tests rejecting authoritative request fields and loading state from WCA repository/global-risk/broker adapters | Missing for automatic acceptance | Existing code still accepts caller-authoritative values at compatibility boundary |
| Missing/stale authoritative state blocks new entries | Configuration/weights missing blocks runtime entries; reconciliation blocks entries; lag pauses entries | Need account snapshot freshness, buying-power freshness, reserved-risk freshness, pending-order freshness, and stale broker-state gates | `runtime_supervisor.py`, `execution_pipeline.py`, `order_validation.py`, `repository.py`, `broker_reconciliation.py` | Maybe add account snapshot or risk reservation freshness columns/table | Tests for missing/stale account, buying power, pending orders, reserved risk | Partial | Runtime has config/weight/reconciliation/lag fail-closed only |
| Preserve current WCA strategy architecture; do not add strategies | `strategy_registry.py`, dedicated primary voters, modifiers, hard filters; current docs and tests cover catalog | No new strategy work required | None unless tests need updates | None | Catalog and primary strategy tests | Implemented | Existing tests cover this area |
| HOLD and NOT_APPLICABLE remain distinct | Contracts and aggregation tests distinguish statuses | None identified | `contracts.py`, `aggregation.py` only if regression appears | None | Aggregation/contract tests | Implemented | Existing WCA tests cover this area |
| WCA remains non-ML; ML read-only only | `feature_snapshot.py`; ML/forecast decoupling tests; docs state ML cannot block entries or exits | Continue scans after automatic paper changes | `feature_snapshot.py`, tests | None | ML forecast decoupling tests | Implemented | Existing tests included in WCA suite, but full suite has unrelated failures |
| Fail closed for entries, not protective exits | Runtime pauses entries on lag/reconciliation/config issues; position/protective worker continues; rollout critical failure action exists | Need end-to-end proof with broker/API/database failures and real outbox/broker errors; ensure account guard cancellation does not disable protective exits | `runtime_supervisor.py`, `position_management.py`, `exits.py`, `paper_broker.py`, `broker_reconciliation.py`, `rollout.py` | Existing exit state and reconciliation tables may suffice | Failure-injection tests for data/broker/DB/global-risk failures plus protective exits | Partial | Some runtime and position tests exist; no full failure campaign evidence |
| Protective exits must continue even when entries are blocked | `PositionProtectiveExitWorker`; `manage_wca_position`; exit state/circuit breaker; tests for lag continuing protective management | Need actual broker protective-order submission/reconciliation, not just proposed pending exit state | `position_management.py`, `paper_broker.py`, `broker_reconciliation.py`, future paper transport | Possibly persist protective broker order linkage | Tests for stop/target/emergency exit submission and reconciliation | Partial | Position management tests exist, real broker evidence absent |
| Duplicate broker orders prevented atomically | `reserve_decision_order_and_outbox()` transaction, unique outbox/broker idempotency indexes, stable client order id, outbox worker claims one record | Need accepted proof under concurrent workers/processes and real broker timeout/retry reconciliation | `repository.py`, `paper_broker.py`, `runtime_supervisor.py` | Current unique indexes likely sufficient; maybe migration for broker-side client-order lookup state | Concurrency tests, restart retry tests, broker duplicate ack tests | Partial/PENDING | Tests exist but final acceptance still says persisted duplicate-submission evidence required |
| Broker/API failures and timeout recovery | `WcaPaperBrokerTimeout` maps to `SUBMISSION_UNKNOWN`; duplicate ack maps to reconciliation required; recovery worker requeues expired leases | No real broker adapter; no proof that unknown broker state blocks entries until reconciliation | `paper_broker.py`, `runtime_supervisor.py`, `broker_reconciliation.py`, `runtime_repository.py` | Existing outbox error payload and reconciliation tables likely sufficient | Timeout, retry, reconciliation, and no-resubmit tests across restart | Partial | Unit tests exist; no production evidence |
| Broker positions and orders reconciled | `broker_reconciliation.py`; `BrokerReconciliationWorker`; `wca_broker_reconciliations`; reconciliation blocks new entries | Runtime worker currently uses `_RuntimeEmptyPaperBroker`; no Alpaca open-order/position client; accepted flow pending | `broker_reconciliation.py`, `runtime_supervisor.py`, future Alpaca client | Maybe persist external broker order snapshot payloads if current reconciliation payload insufficient | Tests with broker open orders, net positions, WCA lots, orphan/cross-algorithm states | Partial/PENDING | Existing reconciliation tests pass; final acceptance requires persisted evidence |
| Position/inventory isolation | `wca_owned_lots`, `wca_virtual_positions`, `wca_trade_ledger`; fills update WCA-owned lots | Need physical dedicated broker account verification before real paper submission; no broker-level netting proof | `repository.py`, `paper_account.py`, `paper_broker.py`, `broker_reconciliation.py` | Possibly no DB migration; evidence table may suffice | Account isolation and same-symbol SPY conflict tests | Partial | Unit tests, no dedicated paper account run evidence |
| Backtest, paper, replay use same production pipeline | `execution_pipeline.py` has paper/replay/backtest adapters; `backtest/engine.py` uses backtest adapter and parity proof helper | Paper execution parity remains unaccepted; WCA diagnostics tests currently fail; dynamic-settings parity still PENDING | `execution_pipeline.py`, `backtest/engine.py`, `service.py`, tests | None expected | Fix existing diagnostics tests; add paper/replay/backtest parity evidence tests | Partial/PENDING | Backtest tests exist, but WCA suite has diagnostics failures |
| Dynamic settings parity between paper and backtest | `resolve_dynamic_profile` is called in production pipeline; backtest adapter uses same pipeline | Need executed evidence comparing automatic paper runtime decisions and backtest/replay on same events | `dynamic_profile.py`, `execution_pipeline.py`, `backtest/engine.py`, tests | None expected | Parity tests with persisted runtime and backtest records | Partial/PENDING | Final acceptance requires dynamic settings parity evidence |
| Latency/cost/slippage controls | `latency.py`, `cost_model.py`, pipeline cost estimate, outbox broker latency payload fields | No accepted latency evidence across sessions; no real broker latency without paper transport | `latency.py`, `paper_broker.py`, `paper_stability.py`, `final_acceptance.py` | Existing payload fields may suffice | Paper run validation for event lag, decision latency, broker latency, slippage | Partial/PENDING | No accepted persisted rollout evidence |
| Account-level risk | `global_risk.py`, shared risk tests, pipeline global-risk proposal | Runtime automatic path needs authoritative account snapshot and explicit reservation contract proof | `global_risk.py`, `runtime_supervisor.py`, `execution_pipeline.py`, shared risk modules | Possible account snapshot persistence if absent | Global risk reservation/release/commit tests for WCA automatic paper | Partial | Shared backend tests pass |
| End-of-session exposure | `EndOfSessionWorker` exists; exits module has session exit handling | Need prove flatten/risk-reduction occurs in background with broker submission and reconciliation; no real paper transport | `runtime_supervisor.py`, `exits.py`, `position_management.py`, `paper_broker.py` | Maybe protective/end-of-session order state | End-of-session forced exit tests and paper evidence | Partial | Structural code exists, operational evidence missing |
| Rollout only through persisted evidence | `rollout.py` defines stages and required evidence; `final_acceptance.py` derives many statuses from evidence; repository has rollout tables | Need actual commands/workers to record all evidence IDs from executed paper runs; no stable paper evidence recorded | `rollout.py`, `final_acceptance.py`, `repository.py`, `paper_stability.py`, research worker | Existing `wca_rollout_evidence` may suffice; verify schema captures all evidence dimensions | Rollout promotion/rollback tests with persisted evidence | Partial/PENDING | Final checklist remains NOT COMPLETE |
| Real-money execution not implemented, enabled, exposed, or tested | WCA broker adapter has `WCA_REAL_MONEY_ENDPOINTS_AVAILABLE = False`; rollout live allowed false; final acceptance fails if live evidence enabled | Continue route/static scans after future broker transport addition | `paper_broker.py`, `rollout.py`, `final_acceptance.py`, API tests | None | No-live route/config scans | Implemented | Existing docs/tests support paper-only posture |

## Module-Level Audit Notes

| Area | Existing implementation | Exact gaps |
| --- | --- | --- |
| `runtime_supervisor.py` | Standalone logical workers for finalized bar consumption, decision, position/protective exits, global risk, execution outbox, reconciliation, recovery, heartbeat, end-of-session | Automatic worker still lacks real WCA Alpaca paper broker transport; account/risk state loading is incomplete; reconciliation worker uses empty broker stub; evidence not recorded as rollout proof |
| `runtime_events.py` | Immutable finalized-bar event contract rejects incomplete events | Need production publisher integration and stale-entry-age acceptance evidence |
| `runtime_repository.py` | Durable event/command queues, duplicate/stale/out-of-order checks, leases, checkpoints, health | Need production load/restart evidence and concurrency proof |
| `repository.py` | WCA-prefixed SQLite schema, migrations to `wca_authoritative_persistence_002`, outbox/idempotency indexes, decisions, fills, lots, reconciliation, backtest, rollout tables | May need migration for persisted paper-account validation or broker account snapshot if existing payload tables are insufficient |
| `execution_pipeline.py` | Single WCA production pipeline with adapters for paper/replay/backtest; final validation and global risk integrated | Caller-authoritative compatibility fields still feed service paper path; runtime input does not yet include authoritative account/broker state |
| `order_validation.py` | Final paper-only validation checks quote, quantity, buying power, position limits, daily loss/trades, spread, participation, idempotency, price geometry, cross-algorithm mutation | Need tests proving every override path is revalidated and missing/stale authoritative state blocks entries |
| `paper_broker.py` | Durable outbox adapter, deterministic simulator, stable client order ID, timeout/duplicate/rejection/fill states, redaction | No real WCA Alpaca paper transport; no broker account verification via Alpaca; no live-paper integration evidence |
| `broker_reconciliation.py` | WCA broker reconciliation result model and discrepancy handling | Runtime currently uses empty broker stub; accepted real paper broker reconciliation pending |
| `position_management.py` | WCA lots/virtual position reconstruction, pending protective exit proposals, circuit breaker state | Protective exits are not yet proven through real broker order submission and reconciliation |
| `exits.py` | Backtest exit evaluation, stop/target/session/emergency exit helpers | Need runtime broker-backed protective and end-of-session exit acceptance |
| `configuration.py` | Canonical configuration, schema/versioning, legacy migration helpers, strategy/modifier/filter settings alignment | Existing legacy API tests fail because API response behavior changed; reconcile tests/contracts |
| `dynamic_profile.py` | Defensive bounded overlays and hysteresis | Need parity evidence between runtime paper decisions and backtests |
| `rollout.py` | Staged rollout, evidence requirements, caps, rollback state helpers, paper-only live block | Need real persisted evidence for shadow, stable paper, latency, multi-condition sessions, rollback |
| `final_acceptance.py` | Conservative evidence-derived acceptance ledger | Some static items are PASS, but evidence-derived automatic paper items remain PENDING; baseline full WCA suite failure prevents treating critical tests as accepted |
| `backtest/` | Backend-authoritative backtest using production pipeline, next-bar fills, partial fill simulation, metrics, diagnostics, walk-forward/holdout | Diagnostics tests currently fail; paper/backtest parity evidence remains pending |
| WCA tests | Broad `test_wca*.py` suite covers steps 0-21, paper execution, reconciliation, stability, shadow evidence | Baseline has 4 failures that must be resolved or test expectations intentionally updated |
| `docs/wca/` | Architecture and step docs exist, including final checklist with PENDING items | Needs this Phase 0 matrix; existing old current-behavior docs still describe frontend-authoritative baseline and should remain historical unless clearly labelled |

## Proposed File-Level Changes After Phase 0

1. Reconcile legacy API tests with the transport-only API boundary:
   - `backend/tests/test_wca_step2_legacy_backend_engine.py`
   - `backend/app/algorithms/wca/api.py`
   - `backend/app/algorithms/wca/service.py`

2. Fix diagnostics/backtest fake-voter expectations without weakening diagnostics coverage:
   - `backend/tests/test_wca_step16_diagnostics.py`
   - `backend/app/algorithms/wca/backtest/engine.py`
   - `backend/app/algorithms/wca/backtest/metrics.py`

3. Remove or reject caller-authoritative paper execution fields from runtime entry paths:
   - `backend/app/algorithms/wca/contracts.py`
   - `backend/app/algorithms/wca/service.py`
   - `backend/app/algorithms/wca/runtime_supervisor.py`
   - `backend/app/algorithms/wca/repository.py`

4. Add authoritative account/broker/global-risk state loaders for automatic paper:
   - `backend/app/algorithms/wca/runtime_supervisor.py`
   - `backend/app/algorithms/wca/global_risk.py`
   - `backend/app/algorithms/wca/broker_reconciliation.py`
   - `backend/app/algorithms/wca/order_validation.py`

5. Replace deterministic runtime paper transport with a dedicated WCA Alpaca paper transport only after guard validation:
   - `backend/app/algorithms/wca/paper_account.py`
   - `backend/app/algorithms/wca/paper_broker.py`
   - `backend/app/algorithms/wca/runtime_supervisor.py`
   - `backend/.env.example`

6. Persist acceptance evidence from actual shadow/paper/rollback/latency runs:
   - `backend/app/algorithms/wca/rollout.py`
   - `backend/app/algorithms/wca/final_acceptance.py`
   - `backend/app/algorithms/wca/paper_stability.py`
   - `backend/app/algorithms/wca/research_worker.py`
   - `backend/app/algorithms/wca/repository.py`

## Migration Requirements

- No mandatory schema migration is proven necessary for Phase 0.
- Existing schema already includes runtime queues, execution outbox, broker orders, attributed fills/lots, broker reconciliations, paper stability validations, rollout status, and rollout evidence.
- Potential future migration: add first-class persisted WCA paper account validation/account snapshot rows if storing this only in outbox error payloads is not sufficient for rollout evidence.
- Potential future migration: add broker open-order snapshot linkage for protective/end-of-session exit reconciliation if current reconciliation payloads are insufficient.

## Requirements Already Fully Implemented Structurally

- Dedicated WCA backend package and algorithm identity.
- WCA-prefixed persistence inventory and migrations.
- Strategy/modifier/filter separation without adding new WCA strategies.
- Backend-authoritative backtest structure with next-bar fill rules.
- API routes largely enqueue background work rather than performing runtime actions inline.
- Runtime event queue rejects incomplete, duplicate, stale, and out-of-order finalized-bar events.
- Paper-only/no-live posture is present in rollout/final acceptance/paper broker constants.

## Phase 0 Stop Point

Do not proceed to implementation until the baseline failures are addressed or explicitly accepted as outdated tests, and until the next phase chooses which missing automatic-paper gap to close first.
