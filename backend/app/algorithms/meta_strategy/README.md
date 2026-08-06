# Meta-Strategy Ownership Boundary

Meta-Strategy owns only records carrying `algorithm_id="meta_strategy"` and the active `capital_partition_id`, `settings_version`, correlation IDs, order IDs, job IDs, event IDs, and model version relevant to that record.

Authoritative Meta-Strategy domains are `settings`, `inventory`, `strategy_state`, `decisions`, `order_intents`, `orders`, `fills`, `trades`, `model_artifacts`, `training`, `replay`, `backtesting`, and `promotion_evidence`.

Meta-Strategy must never read or mutate sibling algorithm private repositories, configuration, strategy implementations, inventory, positions, settings, signals, weights, thresholds, orders, fills, or mutable statistical/ML state. Shared dependencies must go through the explicit protocols in `interfaces.py`: market-data reader, account-data reader, global-risk client, broker gateway, logger, metrics, clock, and market calendar.

HTTP and UI handlers may validate commands, enqueue durable work, and return status. Trading evaluation, training, replay, backtesting, promotion, and broker reconciliation belong in durable background workers; this package boundary does not enable live trading.

## Automatic Paper Deployment

The automatic paper deployment contract is documented in `docs/meta_strategy/automatic_paper_deployment.md`. Safe defaults are runtime disabled, mode `SHADOW`, paper new entries disabled, live trading disabled, and fail-closed behavior for missing authoritative state, market clock, or readiness evidence.

The Meta-Strategy paper on/off control is durable backend-owned state. Frontend state and environment variables are not authoritative for enabling new paper entries.
