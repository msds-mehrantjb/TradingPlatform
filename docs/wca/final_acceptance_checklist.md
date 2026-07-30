# WCA Final Acceptance Checklist

Status: NOT COMPLETE

This checklist is the Step 21 completion gate for WCA modernization. WCA must not be declared complete until every required statement below is marked `PASS`.

Step 17 adds an evidence-derived rollout gate. Deployment-sensitive statuses are not marked `PASS` from code existence alone; `final_acceptance.py` derives them from executed test records plus persisted WCA rollout evidence. Real-money execution remains outside this rollout.

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
| Critical tests pass. | PENDING | Requires passing safety-critical tests, migration gate, registry parity, architecture-boundary scan, and acceptance evidence checks. |
| Paper trading is stable. | PENDING | No accepted multi-condition paper-trading stability run has been recorded. |
| Latency performance is accepted. | PENDING | Requires persisted acceptable event-lag, decision-latency, and broker-latency evidence. |
| Multi-condition paper evidence is accepted. | PENDING | Requires persisted paper evidence across opening, midday, closing, high-volatility, and economic-event sessions. |
| Rollback is tested. | PENDING | Requires persisted rollback evidence and proof that rollback restored a safe state. |
| Real-money execution remains disabled unless explicitly enabled through a separate controlled process. | PASS | WCA rollout flags default paper execution off and do not enable real-money execution. |

## Rollout Stages

WCA paper rollout advances only through persisted evidence:

1. `DISABLED`
2. `HISTORICAL_REPLAY`
3. `SHADOW`
4. `PAPER_RECOMMENDATION`
5. `MANUAL_PAPER`
6. `LIMITED_AUTOMATIC_PAPER`
7. `AUTOMATIC_PAPER`

Promotion evidence must include deterministic replay parity, zero unexplained decision mismatches, zero duplicate broker orders, zero cross-algorithm inventory mutations, restart recovery, reconciliation, no unprotected positions, event lag, decision and broker latency, realised slippage, multiple market conditions, opening/midday/closing coverage, high-volatility and economic-event sessions, minimum paper observation duration, sufficient paper trade count, and tested rollback.

Limited automatic paper mode keeps conservative caps: `SPY` only, max quantity `10`, max daily trades `3`, max daily loss `$100`, session windows `10:00-11:30 America/New_York` and `13:30-15:30 America/New_York`, and allowed strategies `C1`, `C4`, and `C7`.

Any critical failure stops new entries, keeps protective exits available, preserves evidence, opens the circuit breaker, and requires reconciliation plus healthy-state validation before resumption.

## Blocking Items

WCA modernization remains blocked by these required items:

- Accept one shared live/paper/backtest WCA engine path, including paper execution.
- Prove final order validation after every manual or system override.
- Prove atomic duplicate broker-order prevention for WCA paper execution.
- Accept WCA broker position and open-order reconciliation.
- Prove dynamic settings resolver parity between backtesting and paper trading.
- Record completed shadow comparison evidence.
- Record stable paper-trading validation evidence across multiple market conditions.
- Record accepted latency performance evidence.
- Record tested rollback evidence that restored a safe state.

## Phase 15 Rollout Acceptance

Promotion remains evidence-controlled. A user request, API call, feature flag, deployment, or code completion cannot promote WCA beyond the highest stage whose evidence has been recorded and accepted.

Required promotion evidence:

| Evidence | Status | Acceptance evidence |
| --- | --- | --- |
| `deterministic_replay_parity` | PENDING | Golden replay/parity fixtures must show identical decisions across replay, shadow, manual paper, limited automatic paper, and automatic paper. |
| `zero_unexplained_decision_mismatches` | PENDING | Shadow comparison evidence must show zero unexplained mismatches. |
| `zero_duplicate_broker_orders` | PENDING | Broker/order-outbox evidence must show one WCA intent creates at most one broker order. |
| `zero_cross_algorithm_inventory_mutations` | PENDING | Isolation tests must show no cross-algorithm inventory writes. |
| `successful_restart_recovery` | PENDING | Worker restart evidence must show recovery without duplicate decisions or orders. |
| `accepted_reconciliation` | PENDING | WCA broker/local reconciliation must be accepted. |
| `zero_unprotected_positions` | PENDING | Paper evidence must show no WCA position exceeded the protection tolerance. |
| `accepted_event_latency` | PENDING | Event latency metrics must be accepted. |
| `accepted_decision_latency` | PENDING | Decision latency metrics must be accepted. |
| `accepted_broker_latency` | PENDING | Broker latency metrics must be accepted. |
| `recorded_slippage` | PENDING | Paper fills must record slippage. |
| `opening_session_evidence` | PENDING | Opening-session paper evidence must be recorded. |
| `midday_evidence` | PENDING | Midday paper evidence must be recorded. |
| `closing_session_evidence` | PENDING | Closing-session paper evidence must be recorded. |
| `high_volatility_evidence` | PENDING | High-volatility paper evidence must be recorded. |
| `economic_event_session_evidence` | PENDING | Economic-event-session paper evidence must be recorded. |
| `minimum_paper_observation_duration` | PENDING | Minimum paper observation duration must be met. |
| `sufficient_paper_trade_count` | PENDING | Sufficient paper trade count must be met. |
| `tested_rollback` | PENDING | Rollback must be tested and safe state verified. |

The accepted stage sequence is `DISABLED`, `HISTORICAL_REPLAY`, `SHADOW`, `PAPER_RECOMMENDATION`, `MANUAL_PAPER`, `LIMITED_AUTOMATIC_PAPER`, `AUTOMATIC_PAPER`. Rollback to `SHADOW` or `DISABLED` must stop new entries, cancel WCA entry orders, preserve protective exits, reconcile broker and local state, preserve inventory, preserve evidence, verify safe state, and require explicit re-promotion.
