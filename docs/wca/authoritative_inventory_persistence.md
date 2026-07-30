# WCA Authoritative Inventory Persistence

Step 6 makes the WCA SQLite repository the single writer for WCA inventory and persistence records.

## Durable Record Families

The authoritative record inventory is `WCA_PERSISTENCE_RECORD_INVENTORY` in `backend/app/algorithms/wca/repository.py`. It covers active and historical configurations, per-strategy settings versions, calibration tables, weight snapshots, finalized-bar event receipts, runtime checkpoints, market-status snapshots, effective settings, strategy and modifier evaluations, local gates, global-risk responses, decisions, order intents, execution outbox records, broker orders, fills, WCA-owned lots, virtual positions, trade ledger, exit state, reconciliation results, runtime health, background jobs, backtest records, shadow and paper evidence, rollout evidence, and rollout status.

Every mutable WCA record is tagged with `algorithm_id="wca"` and carries the available symbol, decision, configuration, event or run identifiers. Account-scoped records carry `account_id`; older tables receive a `paper` default through migration for backward compatibility.

## Transaction Boundaries

The repository uses SQLite transactions for finalized-bar event claim, decision persistence, order-intent reservation, execution outbox creation, broker-order recording, fill application, and position or lot updates. Runtime checkpoints use compare-and-swap version checks.

Uniqueness is enforced for event IDs, decision IDs, order intent IDs, idempotency keys, broker order IDs, and fill IDs. Duplicate claims, outbox records, broker orders, and fills are treated as idempotent no-ops.

## Inventory Rules

WCA may reduce or close only lots recorded in `wca_owned_lots` with `algorithm_id="wca"`, matching account, symbol, open status, and sufficient quantity. The repository never infers ownership from broker net SPY exposure.

Broker reconciliation may compare broker net exposure against attributed inventories, but it must preserve WCA attribution. An unexplained reconciliation discrepancy or hard operational warning blocks new WCA entries while leaving protective exits possible.

Frontend local storage is never authoritative for WCA settings, orders, fills, positions, trades, backtests, or rollout state.

## Inspection

Inspect inventory by querying the WCA inventory ledger and daily-state projection with both `algorithm_id="wca"` and the configured broker-account identity. Reads without both identities are not acceptance evidence.

Inspect orders and fills through the WCA order intent, outbox, broker-order, fill, and inventory-ledger records. Broker fills remain auditable even when projections are rebuilt.

Inspect reconciliation by reading the latest WCA reconciliation result, discrepancy evidence, reconciliation watermark, broker account snapshot, SPY position snapshot, WCA open orders, and ledger projection version. New entries must remain blocked until startup reconciliation has completed successfully.
