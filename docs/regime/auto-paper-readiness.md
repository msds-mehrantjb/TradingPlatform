# Regime Automatic Paper Readiness

This runbook covers the backend-owned Regime automatic paper runtime for SPY one-minute candles. It is paper-trading only. Live trading is not implemented, not documented as an option, and must remain disabled.

## Runtime Identity

The production paper runtime uses one explicit ownership identity:

| Field | Value |
| --- | --- |
| `algorithm_id` | `regime` |
| `algorithm_instance_id` | `regime-paper-default`, or `REGIME_PAPER_ALGORITHM_INSTANCE_ID` |
| `account_id` | `REGIME_ALPACA_PAPER_ACCOUNT_ID`, `REGIME_PAPER_ACCOUNT_ID`, or `ALPACA_PAPER_ACCOUNT_ID` |
| `runtime_mode` | `paper` |
| `symbol` | `SPY` |

Paper events, decisions, order intents, outbox records, broker observations, fills, positions, trades, commands, and runtime snapshots must carry this complete identity. A paper record must never fall back to the default shadow identity.

## Startup Dependencies

FastAPI startup constructs the Regime paper runtime through `backend.app.algorithms.regime.runtime_factory`, not by calling `RegimeRuntimeSupervisor()` without dependencies.

Startup dependency checks are fail-closed:

- Active Regime settings version is available.
- Runtime mode is `paper`.
- Paper algorithm instance ID is explicit.
- Paper account ID is configured.
- Supported symbol is `SPY`.
- Alpaca trading URL is the paper endpoint.
- Alpaca credentials exist.
- Alpaca account endpoint responds.
- Returned account matches the configured account identity.
- Account is allowed to trade.
- Market-data credentials are available.
- Paper order gateway is configured.
- Account snapshot provider is configured.

If any required dependency is unavailable, the backend may still start, but Regime stays in a blocked health state and new entries are not permitted.

## Required Environment Variables

Required for real Alpaca Paper submission:

```dotenv
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2
ALPACA_DATA_BASE_URL=https://data.alpaca.markets/v2
REGIME_ALPACA_PAPER_ACCOUNT_ID=
REGIME_PAPER_ALGORITHM_INSTANCE_ID=regime-paper-default
REGIME_PAPER_SYMBOL=SPY
DATABASE_URL=sqlite:///./data/trading.db
```

Runtime interval controls:

```dotenv
REGIME_RUNTIME_EXECUTION_POLL_INTERVAL_SECONDS=1
REGIME_RUNTIME_RECONCILIATION_POLL_INTERVAL_SECONDS=3
REGIME_RUNTIME_POSITION_MANAGEMENT_INTERVAL_SECONDS=5
REGIME_RUNTIME_HEALTH_INTERVAL_SECONDS=5
REGIME_RUNTIME_PUBLISHER_POLL_INTERVAL_SECONDS=1
REGIME_RUNTIME_CLOSED_MARKET_PUBLISHER_POLL_INTERVAL_SECONDS=300
REGIME_RUNTIME_ACCOUNT_SNAPSHOT_MAX_AGE_SECONDS=30
REGIME_RUNTIME_MAX_PROCESSING_LAG_SECONDS=75
REGIME_RUNTIME_MAX_DECISION_AGE_SECONDS=300
```

Publisher controls:

```dotenv
REGIME_PUBLISHER_FEED=iex
REGIME_PUBLISHER_FETCH_LIMIT=240
REGIME_PUBLISHER_WARMUP_BARS=120
REGIME_PUBLISHER_FINALIZATION_DELAY_SECONDS=5
REGIME_PUBLISHER_MAX_EVENT_AGE_SECONDS=300
REGIME_PUBLISHER_MATERIAL_GAP_MINUTES=2
```

Feature and rollout defaults:

```dotenv
REGIME_V2_ENABLED=true
REGIME_DYNAMIC_PROFILE_ENABLED=true
REGIME_ML_MODE=shadow
REGIME_GLOBAL_RISK_MANAGER_ENABLED=true
REGIME_SHORT_ENTRIES_ENABLED=false
REGIME_PAPER_SUBMISSION_ENABLED=false
REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED=false
```

Do not put secrets in source control. The example files intentionally leave credentials and account IDs blank.

## Paper Endpoint Safety

The Regime broker adapter is paper-only:

- It accepts only an Alpaca Paper trading URL containing `paper-api.alpaca.markets`.
- It rejects live Alpaca URLs.
- It rejects `runtime_mode=live` and any non-paper mode for broker submission.
- It verifies the configured account before allowing the runtime to become healthy.
- It preserves Regime attribution fields.
- It uses deterministic client order IDs.
- It supports lookup by client order ID after an uncertain submission.
- It supports order status, cancellation, open-order retrieval, fills, and positions.
- It must not log API secrets or full broker payloads.

If verification fails, broker health reports the exact blocker and new entries are blocked. The runtime must not switch to a live endpoint or another account.

## Paper ON/OFF Semantics

The backend is the authority for automatic paper trading. The frontend calls the backend control and displays backend status.

Relevant endpoints:

- `POST /api/regime/rollout/automatic-paper`
- `GET /api/regime/runtime/status`
- `GET /api/regime/runtime/observability`
- `POST /api/regime/runtime/kill-switch/activate`
- `POST /api/regime/runtime/kill-switch/deactivate`
- `POST /api/regime/runtime/emergency-flatten`

Status exposes two states:

- `paperRequestedOn`: the operator request.
- `paperEffectiveOn`: true only when the request is ON and every operational gate passes.

When Paper is switched OFF:

- New entry intents stop immediately.
- Queued unsubmitted entry orders are prevented from submitting.
- Pending Regime entry orders are canceled where cancellation is safe.
- The paper runtime identity stays unchanged.
- Regime inventory and state remain intact.
- Order reconciliation and position monitoring continue.
- Protective and risk-reducing exits remain permitted where operationally possible.
- The OFF command is persisted with operator, timestamp, prior state, new state, and reason.

The UI should show "ON but blocked" when `paperRequestedOn=true` but `paperEffectiveOn=false`.

## Rollout Stages

Operational rollout stages are backend controlled:

| Stage | Behavior |
| --- | --- |
| `disabled` | No finalized-bar processing for execution. |
| `decision_shadow` | Decisions may be produced; no broker submission. |
| `simulated_execution` | Outbox uses the Regime simulated paper broker. |
| `limited_paper` | Real Alpaca Paper gateway may submit after promotion evidence passes. |
| `normal_paper` | Real Alpaca Paper gateway may submit after promotion evidence passes. |

The frontend may request audited administrative commands, but it must not write rollout tables or provide promotion evidence directly.

## Publisher Behavior

The Regime publisher runs in the backend without a browser or API request.

For SPY only, it:

- Reads the authoritative exchange clock and calendar.
- Publishes only during regular-session market hours.
- Determines the most recent completed one-minute boundary.
- Waits for the configured finalization delay.
- Retrieves enough historical bars for Regime features.
- Retrieves quote, spread, relative-strength, breadth, volatility, and event context where available.
- Validates timestamps, freshness, completeness, duplicates, missing bars, and ordering.
- Builds one immutable market payload.
- Creates a `RegimeFinalisedBarEvent` with the explicit paper identity.
- Persists the event before enqueueing it.
- Publishes each finalized bar exactly once.
- Records health, lag, and the last published bar.

The publisher must never publish a still-forming candle, premarket candle, after-hours candle, holiday candle, or early-close after-session candle.

## Inventory Ownership

Regime-owned inventory is reconstructed from Regime-attributed orders and fills in Regime-owned persistence. Every inventory read and write is scoped by:

- `algorithm_id`
- `algorithm_instance_id`
- `account_id`
- `runtime_mode`
- `symbol`

The broker account can contain an aggregate SPY position shared by several algorithms. Regime must not treat that aggregate position as its own inventory. Regime exits are capped at the Regime-owned quantity, and discrepancies block new entries rather than reassigning another algorithm's shares.

## Shared Versus Regime-Owned Infrastructure

Regime-owned authoritative modules:

- `backend/app/algorithms/regime/execution_pipeline.py`
- `backend/app/algorithms/regime/stateful_core.py`
- `backend/app/algorithms/regime/family_aggregation.py`
- `backend/app/algorithms/regime/sizing.py`
- `backend/app/algorithms/regime/execution_gateway.py`
- `backend/app/algorithms/regime/repository.py`
- `backend/app/algorithms/regime/persistence.py`
- `backend/app/algorithms/regime/runtime_factory.py`
- `backend/app/algorithms/regime/runtime_publisher.py`

Allowed shared infrastructure is read-only or reducing from Regime's perspective:

- Market data, quote, and candle services.
- Exchange clock and calendar.
- Backend account and buying-power snapshots.
- Global account-risk approvals and reservations.
- Broker transport for approved Regime order intents.
- Database, logging, metrics, and API framework plumbing.

The frontend must not classify regimes, evaluate strategies, calculate weights, size orders, create order intents, submit broker orders, maintain authoritative inventory, or provide account/runtime/order/fill state in Regime event payloads.

## Recovery Process

Before new entries can resume after startup, the runtime must:

1. Load the active Regime settings version.
2. Restore the paper runtime checkpoint.
3. Restore hysteresis state.
4. Restore cooldowns and daily counters.
5. Recover persisted finalized-bar events.
6. Recover unfinished execution-outbox records.
7. Detect abandoned leases.
8. Retrieve attributed Regime open orders.
9. Retrieve attributed Regime fills.
10. Reconstruct or verify the Regime-owned position.
11. Compare broker observations through Regime attribution and shared reconciliation.
12. Reconcile global-risk reservations.
13. Resume position management.
14. Clear entry blockers only after recovery and reconciliation succeed.

Restart must not duplicate an order after event persistence, decision commit, outbox claim, broker submission, acknowledgement, partial fill, or final inventory update.

## Kill Switch

Activating the kill switch:

- Blocks new entries immediately.
- Cancels pending Regime entry orders where safe.
- Continues reconciliation and monitoring.
- Permits risk-reducing exits.
- Does not flatten unless an explicit emergency-flatten command is submitted.
- Persists actor, timestamp, reason, and state version.

Deactivation requires an explicit audited command. It does not bypass readiness gates.

## End-Of-Day Behavior

The runtime uses the exchange calendar and session close, including early closes.

At end of day it:

- Stops creating new entries after the configured entry cutoff.
- Cancels stale unfilled entry orders before the close.
- Flattens only Regime-owned positions when active settings require flattening.
- Never flattens more than the Regime-owned quantity.
- Reconciles final fills.
- Persists end-of-day position, P&L, trade count, and risk usage.
- Resets daily counters only at the correct exchange-session boundary.
- Reports any unexpected remaining Regime position.

## Operator Checklist

Before switching Paper ON:

1. Confirm `ALPACA_TRADING_BASE_URL` is `https://paper-api.alpaca.markets/v2`.
2. Confirm credentials and `REGIME_ALPACA_PAPER_ACCOUNT_ID` are present in the deployed secret store.
3. Confirm `REGIME_PAPER_SYMBOL=SPY`.
4. Confirm `REGIME_PAPER_ALGORITHM_INSTANCE_ID=regime-paper-default` unless intentionally changed.
5. Confirm `/api/regime/runtime/status` reports `runtimeMode=paper` and `algorithmId=regime`.
6. Confirm active settings are loaded and immutable.
7. Confirm rollout stage is `limited_paper` or `normal_paper` with backend-recorded promotion evidence.
8. Confirm broker, publisher, account snapshot, database, inventory, open-order reconciliation, and recovery gates are healthy.
9. Confirm kill switch is inactive.
10. Confirm market is regular-session open and the publisher is processing finalized bars.
11. Switch Paper ON through `POST /api/regime/rollout/automatic-paper`.
12. Verify `paperRequestedOn=true` and `paperEffectiveOn=true`.

When Paper is ON but blocked, read `paperEffectiveBlockers` and `entryBlockReasonCodes`; do not assume orders are active from the UI toggle alone.

## Implementation Order

Future changes to automatic Regime paper trading should preserve this order:

1. Add tests that reproduce the current runtime-wiring failures.
2. Create the explicit Regime paper identity and composition root.
3. Inject the account snapshot provider.
4. Inject and verify the Alpaca Paper gateway.
5. Implement the finalized-bar publisher.
6. Start and stop the publisher through application lifecycle hooks.
7. Fix outbox identity selection and gateway-processing behavior.
8. Harden weighted family aggregation and evidence.
9. Enforce Regime inventory isolation during reconciliation and exits.
10. Correct Paper ON/OFF behavior.
11. Complete recovery and end-of-day behavior.
12. Add observability and UI/API status.
13. Run integration and restart-failure tests.
14. Run a simulated full-session soak test.
15. Update documentation.

## Test And Soak-Test Commands

Focused runtime and paper readiness tests:

```powershell
.\backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase23_runtime_factory.py backend\tests\regime\test_phase24_runtime_publisher.py backend\tests\regime\test_phase25_execution_outbox_processing.py backend\tests\regime\test_phase35_paper_session_acceptance.py -q
```

Full automatic paper acceptance slice:

```powershell
.\backend\.venv\Scripts\python -m pytest backend\tests\regime\test_phase34_automatic_paper_acceptance.py backend\tests\regime\test_phase35_paper_session_acceptance.py -q
```

Inventory and reconciliation regression slice:

```powershell
.\backend\.venv\Scripts\python -m pytest backend\tests\regime\test_step7_paper_execution_positions.py backend\tests\regime\test_phase21_staged_paper_rollout.py backend\tests\regime\test_persistence_isolation_boundary.py backend\tests\regime\test_phase27_inventory_isolation.py backend\tests\regime\test_phase35_paper_session_acceptance.py -q
```

Compile check:

```powershell
.\backend\.venv\Scripts\python -m compileall backend\app\algorithms\regime
```

Programmatic deterministic acceptance harness:

```python
import asyncio
from pathlib import Path

from backend.app.algorithms.regime.paper_session_acceptance import (
    RegimePaperSessionHarnessConfig,
    run_regime_paper_session_acceptance,
    run_regime_paper_session_soak,
)

report = asyncio.run(
    run_regime_paper_session_acceptance(
        RegimePaperSessionHarnessConfig(repository_path=Path("backend/.pytest_regime_acceptance/session.sqlite3"))
    )
)
print(report.as_dict())

soak = asyncio.run(
    run_regime_paper_session_soak(
        RegimePaperSessionHarnessConfig(
            repository_path=Path("backend/.pytest_regime_acceptance/soak.sqlite3"),
            soak_minutes=390,
        )
    )
)
print(soak.as_dict())
```

## Known Limitations

- Scope is SPY only.
- Primary candle timeframe is one minute only.
- Live trading is intentionally unavailable.
- The Alpaca Paper adapter depends on configured Alpaca credentials and the paper account endpoint.
- Current soak mode can run deterministically with a fake broker; a real full-session soak requires market hours, valid data credentials, and an operator-reviewed readiness report.
- Aggregate broker SPY positions are not treated as Regime inventory.
- Paper ON is not sufficient by itself; `paperEffectiveOn` must also be true.
- ML may run only as disabled, shadow, or confirmation-only diagnostics and must not alter Regime weighted aggregation for automatic paper trading.
