# Meta-Strategy Automatic Paper Deployment

This runbook documents the deployment contract for unattended Meta-Strategy automatic paper trading during the regular market session. It does not enable live trading, and no value in this file should contain a secret.

## Safe Defaults

Automatic paper entries are disabled unless every backend authority says otherwise.

| Setting | Safe default |
| --- | --- |
| Runtime enabled | `false` |
| Runtime mode | `SHADOW` |
| Paper new entries enabled | `false` |
| Live trading enabled | `false` |
| Missing authoritative state | Block new entries |
| Missing market clock | Block new entries |
| Missing readiness evidence | Block new entries |

Do not automatically switch from `SHADOW` to `PAPER`. Paper activation must be explicit, durable, and auditable through the backend-owned Meta-Strategy paper-control state.

## Required Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `META_STRATEGY_RUNTIME_ENABLED` | `false` | Starts the Meta-Strategy background runtime only when true. |
| `META_STRATEGY_RUNTIME_MODE` | `SHADOW` | Runtime mode; automatic broker submission requires `PAPER`. |
| `META_STRATEGY_LIVE_TRADING_ENABLED` | `false` | Safety declaration. Live order submission is not implemented or allowed. |
| `META_STRATEGY_PAPER_NEW_ENTRIES_ENABLED` | `false` | Bootstrap/default documentation for the durable paper toggle; the backend database record is authoritative. |
| `META_STRATEGY_PAPER_BROKER` | `ALPACA` | Paper order-transport adapter. Use `LOCAL_LEDGER` for the Meta-Strategy local paper ledger, or `LOCAL_PAPER` for an explicitly configured local paper broker service. |
| `META_STRATEGY_LOCAL_LEDGER_MARKET_OPEN` | `false` | Local-ledger broker clock switch for local paper testing. New entries remain blocked unless this is explicitly true. |
| `META_STRATEGY_LOCAL_LEDGER_IMMEDIATE_FILLS` | `false` | Optional local-ledger paper-fill simulator. Leave false unless testing full position lifecycle locally. |
| `META_STRATEGY_LOCAL_PAPER_BASE_URL` | blank | Base URL for an optional local PAPER-only broker service. Required when `META_STRATEGY_PAPER_BROKER=LOCAL_PAPER`. |
| `META_STRATEGY_LOCAL_PAPER_TOKEN` | blank | Optional local service bearer token. Do not commit the value. |
| `META_STRATEGY_LOCAL_PAPER_ACCOUNT_PATH` | `/account` | Local read-only paper account snapshot endpoint. |
| `META_STRATEGY_LOCAL_PAPER_CLOCK_PATH` | `/clock` | Local authoritative paper market-clock endpoint. |
| `META_STRATEGY_LOCAL_PAPER_ORDERS_PATH` | `/orders` | Local paper order endpoint used only by the background submission/reconciliation workers. |
| `META_STRATEGY_LOCAL_PAPER_POSITIONS_PATH` | `/positions` | Local read-only broker aggregate positions endpoint for reconciliation. |
| `META_STRATEGY_LOCAL_RISK_SNAPSHOT_PATH` | `/risk/snapshot` | Local read-only risk snapshot endpoint. |
| `META_STRATEGY_LOCAL_RISK_APPROVAL_PATH` | `/risk/approve` | Local risk approval endpoint used through the existing execution gate interface. |
| `ALPACA_TRADING_BASE_URL` | `https://paper-api.alpaca.markets/v2` | Paper broker endpoint. A live endpoint must not authorize Meta-Strategy entries. |
| `APCA_API_KEY_ID` | blank | Paper credential presence only; never commit the value. |
| `APCA_API_SECRET_KEY` | blank | Paper credential presence only; never commit the value. |
| `META_STRATEGY_SYMBOLS` | `SPY` | Comma-separated automatic paper symbols. |
| `META_STRATEGY_MARKET_DATA_FEED` | `iex` | Market-data feed for finalized candle production. |
| `DATABASE_URL` | `sqlite:///./data/trading.db` | Durable repository for controls, jobs, decisions, intents, outbox, risk reservations, orders, fills, and inventory. |
| `META_STRATEGY_WORKER_POLL_SECONDS` | `1` | Decision and execution worker poll interval. |
| `META_STRATEGY_RECONCILIATION_POLL_SECONDS` | `15` | Order reconciliation poll interval. |
| `META_STRATEGY_STALE_ORDER_POLL_SECONDS` | `30` | Stale-order cancellation poll interval. |
| `META_STRATEGY_INVENTORY_RECONCILIATION_POLL_SECONDS` | `60` | Inventory reconciliation poll interval. |
| `META_STRATEGY_POSITION_MANAGEMENT_POLL_SECONDS` | `15` | Stop, target, signal-exit, max-hold, and EOD management interval. |
| `META_STRATEGY_HEARTBEAT_INTERVAL_SECONDS` | `5` | Runtime heartbeat interval. |
| `META_STRATEGY_MAINTENANCE_INTERVAL_SECONDS` | `15` | Queue and readiness maintenance interval. |
| `META_STRATEGY_CANDLE_POLL_SECONDS` | `5` | Finalized one-minute candle producer poll interval. |
| `META_STRATEGY_WORKER_LEASE_SECONDS` | `60` | Durable worker lease timeout for idempotent recovery. |
| `META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS` | `60` | Execution-time guard quote freshness limit. |
| `META_STRATEGY_CANDLE_FRESHNESS_LIMIT_SECONDS` | `180` | Finalized candle maximum staleness limit. |
| `META_STRATEGY_ORDER_INTENT_MAX_AGE_SECONDS` | `300` | Execution-time guard order-intent maximum age. |
| `META_STRATEGY_DECISION_MAX_AGE_SECONDS` | `300` | Execution-time guard decision maximum age. |
| `META_STRATEGY_GLOBAL_RISK_FRESHNESS_LIMIT_SECONDS` | `30` | Execution-time guard global-risk approval freshness limit. |
| `META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS` | `30` | Authoritative broker/exchange market-clock freshness limit. |
| `META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS` | `75` | Readiness threshold for maximum queue lag. |
| `META_STRATEGY_DEAD_LETTER_THRESHOLD` | `0` | Readiness threshold for dead-letter jobs. |

## Activation Checklist

1. Apply database migrations and confirm the Meta-Strategy repository opens the configured `DATABASE_URL`.
2. Set `META_STRATEGY_RUNTIME_ENABLED=true` only after deployment dependencies are available.
3. Set `META_STRATEGY_RUNTIME_MODE=PAPER` only for automatic paper trading. `SHADOW` records diagnostics but blocks paper broker submission.
4. Keep `META_STRATEGY_LIVE_TRADING_ENABLED=false`; this subsystem must never submit live orders.
5. Confirm `ALPACA_TRADING_BASE_URL` points to the paper endpoint and paper credentials are present in the runtime environment.
6. Promote active Meta-Strategy settings for paper use.
7. Verify worker health, queue lag, dead-letter count, market data, authoritative market clock, inventory reconciliation, global risk, and readiness evidence.
8. Turn the durable Meta-Strategy paper-control state ON through the backend command path only when new paper entries should be allowed.

## Local Paper Account And Risk

For local automatic-paper testing, the Meta-Strategy trading settings panel is the default local paper account and local risk authority. `Total balance` records the Meta-Strategy capital partition, `Base risk %`, daily-loss limits, max-share limits, and reserved risk determine available risk, and the Meta-Strategy inventory repository remains the only authority for positions, lots, orders, fills, P&L, daily trade count, and reserved risk.

The local settings risk source is consumed through the existing Meta-Strategy execution pipeline; it does not create a parallel submission path and it does not enable live trading. Missing or zero local paper capital, zero buying power, zero remaining Meta-Strategy risk, stale readiness, or a blocked paper toggle still fail closed.

If you want the local ledger to be the broker transport instead of Alpaca paper, set `META_STRATEGY_PAPER_BROKER=LOCAL_LEDGER`. The local-ledger broker verifies that the Meta-Strategy paper gateway ledger is writable, writes deterministic paper order acknowledgements, exposes broker events to reconciliation, and reports positions back to restart recovery. With `META_STRATEGY_LOCAL_LEDGER_IMMEDIATE_FILLS=true`, it also creates deterministic local paper fills so the Meta-Strategy inventory ledger can open and manage positions end-to-end.

Local-ledger paper mode is still paper-only. Every order, fill, and broker event is stamped with `algorithmId=meta_strategy`, the Meta-Strategy capital partition, deterministic client-order ownership, `paperOnly=true`, and `liveTradingEnabled=false`.

New entries still require the runtime to be in `PAPER`, the durable paper toggle to be ON, readiness to pass, fresh decision/quote state, positive local paper risk, and market-open authorization. For local-ledger testing, `META_STRATEGY_LOCAL_LEDGER_MARKET_OPEN=true` is the explicit market-open authorization switch; leave it false outside controlled local tests.

The local service must expose paper-only, algorithm-owned data. The account endpoint must identify a paper account and must not report `liveTradingEnabled=true` or `accountType=live`. The risk endpoints are the approval source for automatic entries, but they are still treated as an execution gate: missing, stale, rejected, zero, or contradictory risk data blocks new entries.

Minimum optional local broker endpoint contract:

| Endpoint | Required fields |
| --- | --- |
| `GET /account` | `accountId`, `accountEquity`, `buyingPower`, `capturedAt`, paper/live flags. |
| `GET /clock` | `isOpen`, `dataSourceTimestamp`, `nextOpen`, `nextClose`, regular session open/close, freshness/authority flags. |
| `GET /risk/snapshot` | `availableRiskDollars`, `maxQuantity`, `capturedAt`, `reasonCodes`. |
| `POST /risk/approve` | `action`, `maximumAllowedQuantity`, `maximumAdditionalRiskDollars`, `evaluatedAt`, `configurationHash`. |
| `POST /orders` | Accepts Meta-Strategy-owned paper orders only; returns `clientOrderId`, broker order id, status, and timestamp. |
| `GET /orders/{clientOrderId}` | Current status/fill fields for reconciliation. |
| `GET /orders?status=all&limit=100` | Nonterminal and recent terminal paper order events. |
| `GET /positions` | Broker aggregate paper positions for reconciliation only; these are not treated as Meta-Strategy local inventory. |

Every submitted order includes `algorithmId=meta_strategy`, `capitalPartitionId`, deterministic `clientOrderId`, and `paperOnly=true`. Fill allocation and local position state remain owned by the Meta-Strategy repositories and must stay isolated from sibling algorithms.

## Failure Behavior

New entries fail closed when authoritative state is missing, stale, contradictory, or unavailable. That includes paper account equity, buying power, remaining Meta-Strategy risk, global available risk, fresh quote, fresh market clock, readiness evidence, runtime health, promoted paper settings, and the durable paper toggle.

Turning the paper toggle OFF blocks new entry decisions and queued unsubmitted entry intents. It does not block reconciliation, fill allocation, stale-order cancellation, end-of-day liquidation, protective exits, inventory recovery, or other risk-reducing management for existing Meta-Strategy positions.

Protective and risk-reducing orders must still pass ownership and paper-account checks, must belong to Meta-Strategy, and must not increase absolute Meta-Strategy exposure or open an opposite-direction position.

## Observability

Monitor API health separately from Meta-Strategy runtime health, paper readiness, paper-toggle state, market-open state, new-entry permission, and exit-management health. Every finalized one-minute candle must reach one terminal observable outcome, and every blocked submission must persist stable reason codes with the outbox or decision evidence.

Run the focused CI gate before paper activation:

```powershell
python scripts/meta_strategy_paper_readiness_gate.py
```
