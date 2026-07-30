# WCA Paper Broker Outbox

Step 10 separates WCA order-intent persistence from paper-broker submission.

## Atomic Reservation

The service reserves paper execution with one SQLite transaction:

1. Persist the WCA decision snapshot.
2. Reserve the WCA order intent under a unique idempotency key.
3. Create a `wca_execution_outbox` record in `OUTBOX_RESERVED`.

This transaction does not call a broker and does not set `submitted=true`.

## Execution Worker

The execution worker claims one `OUTBOX_RESERVED` record, moves it to `SUBMITTING`, and only then calls the paper-broker transport. After an actual request is issued, the worker records one of:

- `BROKER_ACKNOWLEDGED`
- `PARTIALLY_FILLED`
- `FILLED`
- `REJECTED`
- `SUBMISSION_UNKNOWN`
- `RECONCILIATION_REQUIRED`

Timeouts become `SUBMISSION_UNKNOWN`. The runtime must not resubmit automatically until reconciliation proves that no broker-side order exists.

## Broker Payloads

The outbox stores the exact request payload. Broker acknowledgements are persisted separately in `wca_broker_orders` with request and response payload columns. Secret-like fields are redacted before broker request/response payloads are stored.

## Idempotency

The broker request uses a stable WCA-attributed `client_order_id` plus the WCA idempotency key. Duplicate reservation attempts return the existing outbox reservation and do not create a second economic order.

Cancellation requires a new cancellation idempotency key. Replacement requires a new order intent and new idempotency semantics.

## Paper Only

The WCA paper broker adapter exposes no real-money endpoint or credential path. The default transport is a deterministic paper simulator for tests and local dry runs.

Automatic runtime submission must also pass the dedicated WCA Alpaca paper-account guard before any broker transport is called. The guard requires:

- `WCA_AUTOMATIC_PAPER_ENABLED=true`
- `WCA_ALPACA_PAPER_API_KEY_ID`
- `WCA_ALPACA_PAPER_API_SECRET_KEY`
- `WCA_ALPACA_PAPER_ACCOUNT_ID` matching the runtime command account
- `WCA_ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets`

Generic `APCA_*` credentials are not used as a fallback for WCA automatic paper trading. A missing, live-looking, mismatched, or shared credential configuration leaves the outbox reservation persisted but disables broker submission.
