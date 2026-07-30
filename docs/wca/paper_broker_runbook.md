# WCA Paper Broker Runbook

## Account Setup

Configure a dedicated WCA paper account. The configured account ID must match the broker account returned by the paper adapter at startup, and the adapter must refuse non-paper endpoints.

Use placeholders in local documentation and examples. Do not paste real credentials into docs, tests, logs, screenshots, support tickets, or API responses.

## Operations

Inspect orders and fills from both sources before declaring broker state healthy:

- WCA local order intents, outbox rows, broker-order mappings, and fills.
- Broker open orders, completed orders, fills, account status, buying power, cash, and SPY position.

Every WCA client-order ID must use the WCA prefix and include enough deterministic identity to reconcile decision ID, order-intent ID, idempotency key, client-order ID, and broker-order ID.

## Failure Handling

On timeout after an uncertain submit, mark the local order `UNKNOWN`, search by client-order ID, and reconcile before any retry. Do not allow deterministic-broker fallback in `LIMITED_AUTOMATIC_PAPER` or `AUTOMATIC_PAPER`.

Diagnose a blocked entry by checking final validation reason codes, broker endpoint/account verification, reconciliation freshness, buying power, position state, open WCA entry orders, quote freshness, event freshness, and rollout permission.
