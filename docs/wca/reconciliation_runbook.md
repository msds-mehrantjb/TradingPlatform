# WCA Reconciliation Runbook

## Scope

Inspect reconciliation at startup, after order submission, after fills, after cancellations, after worker recovery, periodically during the session, and before end-of-session completion.

Reconciliation compares the dedicated WCA paper account, account status, equity, buying power, SPY position, open WCA orders, partially filled WCA orders, recent completed orders, broker fills, local order intents, outbox rows, inventory ledger, protective orders, reserved risk, and daily state.

## Discrepancies

Any unexplained difference blocks new entries, opens the WCA circuit breaker, persists discrepancy evidence, and preserves protective exits. Do not assign another algorithm's order or position to WCA.

Common discrepancy checks:

- Local order missing at broker.
- Broker order missing locally.
- Local position differs from broker position.
- Partial fill, rejection, or cancellation not processed locally.
- Orphaned protective order or position without protection.
- Unknown WCA-prefixed broker order.
- Unexpected account-level SPY position.

Accepted reconciliation evidence is required before promotion into automatic-paper stages.
