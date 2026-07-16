# WCA Final Acceptance Checklist

Status: NOT COMPLETE

This checklist is the Step 21 completion gate for WCA modernization. WCA must not be declared complete until every required statement below is marked `PASS`.

## Architecture

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| WCA is an isolated backend algorithm. | PASS | `backend/app/algorithms/wca`, WCA isolation tests. |
| Strategies are isolated modules. | PASS | `backend/app/algorithms/wca/strategies`, strategy isolation tests. |
| Frontend is presentation-only. | PASS | WCA display/configuration lives in `frontend/src/features/wca`; legacy frontend WCA evaluator and shadow parity functions were removed from `frontend/src/main.ts`. |
| Live, paper, and backtest use the same engine. | PENDING | Backend backtesting exists, but WCA paper execution is not yet fully accepted on the same path. |
| WCA does not depend on ML. | PASS | ML and forecast decoupling tests cover evaluation and backtesting. |

## Strategies

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| Only primary alpha strategies cast votes. | PASS | Corrected strategy registry and catalog tests. |
| Context indicators are modifiers. | PASS | Modifier package and catalog tests. |
| Risk filters are gates. | PASS | WCA local gate package and tests. |
| Duplicate strategy logic is removed. | PASS | Corrected primary-voter catalog tests. |
| Hold and Not Applicable are different. | PASS | Contract and aggregation tests distinguish deliberate Hold from ineligible signals. |
| Strategy-family concentration is controlled. | PASS | Aggregation and weight tests cover family concentration. |

## Confidence And Weights

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| Confidence is statistically calibrated. | PASS | Beta-binomial confidence calibration module and tests. |
| Weights are leakage-free. | PASS | Weight tests enforce prior-data cutoffs. |
| Weights use sample reliability and shrinkage. | PASS | Performance weight engine and tests. |
| Family and strategy caps are enforced. | PASS | Weight and property-style coverage tests. |
| Weight snapshots are versioned and reproducible. | PASS | WCA contracts and persistence repository. |

## Settings

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| User defaults remain the baseline. | PASS | Dynamic profile tests verify baseline settings are not overwritten. |
| Dynamic profiles are bounded. | PASS | Dynamic profile resolver clamps risk and thresholds. |
| Effective settings do not overwrite defaults. | PASS | Effective settings are calculated read-only snapshots. |
| Initial dynamic behavior is defensive only. | PASS | Dynamic overlays reduce or tighten; they do not increase baseline risk. |
| Profile changes use hysteresis. | PASS | Market-status transition tests cover defensive and recovery behavior. |

## Risk And Execution

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| Local and global gates are separate. | PASS | WCA-local gates and shared global gates are separate modules. |
| Account risk is aggregated across algorithms. | PASS | Shared account risk ledger and global-gate tests. |
| New entries and risk-reducing exits use separate permissions. | PASS | Global-gate contracts and tests cover entry and exit permissions. |
| Protective stops cannot be overridden or delayed by forecasts. | PASS | WCA forecast decoupling and exit tests. |
| Final order validation occurs after every override. | PENDING | Shared order validation exists, but the complete WCA override-to-final-validation path is not accepted yet. |
| Duplicate broker orders are prevented atomically. | PENDING | Idempotency contracts exist; WCA atomic paper-order submission proof is still pending. |
| Broker positions and orders are reconciled. | PENDING | Shared reconciliation scaffolding exists; accepted WCA reconciliation flow is still pending. |

## Backtesting

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| The backtest is backend-authoritative. | PASS | Backend WCA backtest engine and tests. |
| There is no same-candle signal/fill bias. | PASS | Backtest leakage tests enforce next-bar fills. |
| Early-session strategies receive proper warm-up data. | PASS | Backend backtest tests cover early-session strategy windows. |
| Costs and open-position drawdown are included. | PASS | Backtest metrics and diagnostics tests. |
| Full-history, walk-forward, and holdout results exist. | PASS | Backtest mode tests cover labeled run types. |
| Dynamic settings use the same resolver as paper trading. | PENDING | The resolver is shared by backend components, but WCA paper execution parity is not yet accepted. |
| Smoke-test results are not used as profitability proof. | PASS | Backtest reports label smoke tests as operational checks only. |

## ML Isolation

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| ML may read WCA outputs. | PASS | Read-only WCA feature snapshots exist. |
| ML cannot write into WCA. | PASS | Feature snapshots are one-way and tests cover no write-back behavior. |
| ML cannot block WCA entries. | PASS | WCA local gates contain no ML forecast gate. |
| ML cannot delay WCA exits. | PASS | Forecast override removal tests protect exits. |
| ML failure cannot stop WCA evaluation or backtesting. | PASS | WCA evaluates and backtests with ML unavailable. |

## Deployment

| Statement | Status | Evidence or limitation |
| --- | --- | --- |
| Shadow comparison completed. | PENDING | Rollout support exists, but completed validation evidence has not been recorded. |
| Critical tests pass. | PASS | Step 19 and Step 21 tests are registered in safety-critical CI coverage. |
| Paper trading is stable. | PENDING | No accepted multi-condition paper-trading stability run has been recorded. |
| Rollback is tested. | PASS | Step 20 rollout tests cover rollback behavior. |
| Real-money execution remains disabled unless explicitly enabled through a separate controlled process. | PASS | WCA rollout flags default paper execution off and do not enable real-money execution. |

## Blocking Items

WCA modernization remains blocked by these required items:

- Accept one shared live/paper/backtest WCA engine path, including paper execution.
- Prove final order validation after every manual or system override.
- Prove atomic duplicate broker-order prevention for WCA paper execution.
- Accept WCA broker position and open-order reconciliation.
- Prove dynamic settings resolver parity between backtesting and paper trading.
- Record completed shadow comparison evidence.
- Record stable paper-trading validation evidence across multiple market conditions.
