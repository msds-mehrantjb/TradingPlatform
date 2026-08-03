# WCA Automatic-Paper Runbook

This runbook is for WCA automatic Alpaca paper trading only. It does not enable live trading, and it must not be adapted to live broker endpoints.

Paper ON is a request, not a guarantee of execution. The backend may keep effective automatic entries OFF whenever any safety gate fails.

## Required Environment

Set these values in the process environment for both the API process and the WCA runtime process. Prefer deployment-secret storage for credentials.

```powershell
$db = (Resolve-Path .\backend\data).Path + "\trading.db"
$env:DATABASE_URL = "sqlite:///$db"

$env:WCA_BACKEND_ENGINE_ENABLED = "true"
$env:WCA_CORRECTED_STRATEGY_CATALOG_ENABLED = "true"
$env:WCA_DYNAMIC_WEIGHTS_ENABLED = "true"
$env:WCA_DYNAMIC_PROFILE_ENABLED = "true"
$env:GLOBAL_GATE_ENGINE_ENABLED = "true"
$env:WCA_BACKEND_BACKTEST_ENABLED = "true"
$env:WCA_PAPER_EXECUTION_ENABLED = "true"
$env:WCA_AUTOMATIC_PAPER_ENABLED = "true"

$env:WCA_ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
$env:WCA_ALPACA_PAPER_ACCOUNT_ID = "<dedicated-wca-paper-account-id>"
$env:WCA_ALPACA_PAPER_API_KEY_ID = "<dedicated-wca-paper-key-id>"
$env:WCA_ALPACA_PAPER_API_SECRET_KEY = "<dedicated-wca-paper-secret-key>"
$env:WCA_ALPACA_PAPER_ACCOUNT_SHARED = "false"
```

The finalized-bar publisher also needs real market-data credentials configured through the platform's normal Alpaca data settings, such as `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, and `ALPACA_DATA_BASE_URL`.

## Dedicated Paper Account

Use a dedicated Alpaca paper account for WCA. The account returned by Alpaca must match `WCA_ALPACA_PAPER_ACCOUNT_ID`, every WCA order must use a `wca-` client-order prefix, and the base URL must be exactly `https://paper-api.alpaca.markets`.

If `WCA_ALPACA_PAPER_ACCOUNT_SHARED=true`, if generic shared credentials are used, if the account ID cannot be verified, or if the broker endpoint is not the required paper URL, WCA automatic entries must remain blocked. Protective exits and reconciliation may continue.

## Run Migrations

WCA migrations run when the WCA repository is constructed. Run them explicitly before startup:

```powershell
.\backend\.venv\Scripts\python -c "from backend.app.algorithms.wca.repository import WcaSqliteRepository; repo = WcaSqliteRepository(); print(f'WCA migrations applied to {repo.path}')"
```

Verify the API later reports the active persistence version under `/api/wca/status` at `persistence.migrationVersion`.

## Start The API

From the repository root, start the backend API with the same environment values:

```powershell
cd backend
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API is transport and presentation only. It may enqueue commands and report persisted state, but it must not calculate authoritative decisions, mutate inventory, or submit broker orders.

## Start WCA Runtime

Start WCA as an independent process from the repository root:

```powershell
.\backend\.venv\Scripts\python -m backend.app.algorithms.wca.runtime_main
```

For process supervisors, the repository includes:

```procfile
wca-runtime: python -m backend.app.algorithms.wca.runtime_main
```

The runtime must be supervised separately from the API, restarted after crashes, and run with one active owner/lease per WCA account and symbol.

## Verify Worker Health

Read backend-authoritative status:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.runtimeProcessStatus
$status.runtimeReadinessState
$status.workerHeartbeats
$status.queueDepths
$status.queueAges
$status.activeEntryBlockReasonCodes
```

Healthy automatic-paper readiness requires a fresh runtime heartbeat, current worker heartbeats, acceptable queue ages, no open WCA or global circuit breaker, clean reconciliation, active configuration, active weights, active calibration, and rollout evidence that permits `LIMITED_AUTOMATIC_PAPER` or `AUTOMATIC_PAPER`.

## Verify Market Clock

Verify the backend status fields:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.marketOpen
$status.entryWindowOpen
$status.marketSession
$status.paperBroker.reasonCodes
```

To directly verify the Alpaca paper clock through the WCA paper adapter:

```powershell
.\backend\.venv\Scripts\python -c "import os; from backend.app.algorithms.wca.alpaca_paper_broker import WcaAlpacaPaperBroker; broker = WcaAlpacaPaperBroker.from_env(account_id=os.environ['WCA_ALPACA_PAPER_ACCOUNT_ID']); print(broker.read_clock().model_dump_json(indent=2))"
```

New entries require the broker clock to be open, an exchange-calendar session to exist, current time inside the WCA entry window, fresh candle and quote data, an unexpired decision, and an unexpired runtime command.

## Startup Reconciliation

The runtime scheduler enqueues startup reconciliation automatically. To request one manually:

```powershell
$body = @{ accountId = $env:WCA_ALPACA_PAPER_ACCOUNT_ID; symbol = "SPY" } | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/reconciliation/request -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

Confirm `/api/wca/status` shows `inventoryReconciliationState.blocksNewEntries = false` and that `lastReconciliation` has no hard unexplained discrepancies before requesting Paper ON.

## Request Paper ON

Request global/WCA automatic paper through the backend control endpoint:

```powershell
$body = @{
  enabled = $true
  actor = "operator"
  reason = "startup_checks_complete"
  accountId = $env:WCA_ALPACA_PAPER_ACCOUNT_ID
  symbol = "SPY"
} | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/runtime/automatic-paper -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

Then read:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.requestedPaperState
$status.effectivePaperState
$status.automaticEntryPermission
$status.runtimeControl
```

`requestedPaperState = ON` means the operator request was accepted. It does not mean WCA can submit entries.

`effectivePaperState = ON` and `automaticEntryPermission.state = PERMITTED` mean the backend currently permits automatic entries. This can turn OFF again on market close, stale data, stale reconciliation, circuit breaker, rollout block, broker verification failure, runtime-health failure, or any final validation failure.

## Identify Active Blockers

Use these status fields first:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.activeEntryBlockReasonCodes
$status.paperReadyBlockingReasonCodes
$status.rolloutBlockers
$status.runtimeControl.reasonCodes
$status.inventoryReconciliationState.reasonCodes
$status.paperBroker.reasonCodes
```

Common blockers include market closed, entry window closed, stale finalized candle, stale quote, stale broker snapshot, stale reconciliation, missing worker heartbeat, missing active configuration, missing active weights, missing calibration, rollout evidence missing, WCA circuit breaker open, global circuit breaker open, account ID mismatch, shared account ambiguity, and paper endpoint verification failure.

## Pause And Resume Entries

Pause new WCA entries without disabling protective management:

```powershell
$body = @{ reason = "operator_pause" } | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/runtime/pause -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

Resume only clears the operator pause. It recalculates all readiness gates and does not force effective ON:

```powershell
$body = @{ reason = "operator_resume_after_checks" } | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/runtime/resume -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

## Emergency Risk Reduction

Use emergency risk reduction for suspected unsafe exposure, unprotected position, account mismatch, unexplained reconciliation discrepancy, or operational incident:

```powershell
$body = @{
  accountId = $env:WCA_ALPACA_PAPER_ACCOUNT_ID
  symbol = "SPY"
  reason = "operator_emergency_risk_reduction"
} | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/runtime/emergency-risk-reduction -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

Expected behavior: new entries are blocked, the WCA circuit breaker opens, WCA entry orders are cancelled, protective exits are preserved or created, WCA-owned exposure is reduced or flattened, and immediate reconciliation is scheduled. Do not use emergency risk reduction to touch another algorithm's position.

## Roll Back Configuration

Rollback activates a previously valid WCA configuration at a safe candle boundary. New entries remain blocked during rollback, open-position protection continues, and reconciliation plus healthy-state validation are required before resumption:

```powershell
$configurationVersion = "<previous-valid-wca-configuration-version>"
$cmd = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/wca/configuration/$configurationVersion/rollback"
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

After rollback, verify `configurationVersion`, `configurationHash`, `runtimeControlRevision`, `activeEntryBlockReasonCodes`, and `lastReconciliation` from `/api/wca/status`.

## Verify End-Of-Session Flattening

End-of-session handling is generated by the WCA runtime from the exchange calendar; it must not require an API request or an open browser.

After the cutoff or close workflow, verify:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.lastEndOfSessionResult
$status.wcaPosition
$status.reservedRisk
$status.lastReconciliation
$status.lastBrokerOrder
```

Safe EOD completion means WCA unfilled entries are cancelled, WCA-owned positions are flattened according to policy, protective orders are no longer orphaned, reserved WCA risk is released, and broker/local WCA inventory reconcile to zero. If any discrepancy remains, keep entries blocked and continue reconciliation.

## Return Safely To OFF

Switch Paper OFF through the same backend control path:

```powershell
$body = @{
  enabled = $false
  actor = "operator"
  reason = "operator_return_to_off"
  accountId = $env:WCA_ALPACA_PAPER_ACCOUNT_ID
  symbol = "SPY"
} | ConvertTo-Json
$cmd = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wca/runtime/automatic-paper -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/wca/commands/$($cmd.commandId)"
```

Confirm:

```powershell
$status = Invoke-RestMethod http://127.0.0.1:8000/api/wca/status
$status.requestedPaperState
$status.effectivePaperState
$status.automaticEntryPermission
$status.wcaPosition
$status.lastReconciliation
```

OFF blocks new entries immediately. It must not cancel required protective exits for already-open WCA positions. Continue reconciliation and protective management until WCA-owned inventory is either protected or flat.
