# Broker Reconciliation and Idempotent Orders V2

V2 paper-order submission uses a broker reconciliation boundary before any order is sent. This does not enable live trading.

## Pre-Submission Refresh

Before submission the backend refreshes:

- account state,
- broker positions,
- open orders,
- symbol tradability,
- buying power.

It then rebuilds global account risk, re-evaluates critical global gates, and recalculates remaining risk/notional caps from the broker-authoritative state.

## Idempotent Client Order IDs

Client order IDs are deterministic:

- symbol,
- decision timestamp,
- algorithm version,
- setup ID,
- side.

The idempotency ledger blocks duplicate submissions caused by frontend refresh, repeated candle evaluation, and network retries. If another algorithm attempts the same candidate with a different algorithm version, broker-visible pending exposure is still refreshed and can block the second entry through the global exposure gates.

## Post-Submission Reconciliation

After submission the backend confirms broker acceptance, refreshes order/fill state, refreshes positions/open orders, and checks for local-vs-broker divergence.

- Partial fills update protective-order quantity to actual filled quantity.
- Rejected entries do not create local positions.
- Canceled/rejected orders with lingering local or broker exposure generate hard operational warnings.
- Orphan positions or fill/position mismatches generate hard operational warnings until reconciled.
