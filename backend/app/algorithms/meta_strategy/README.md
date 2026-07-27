# Meta-Strategy Ownership Boundary

Meta-Strategy owns only records carrying `algorithm_id="meta_strategy"` and the active `capital_partition_id`, `settings_version`, correlation IDs, order IDs, job IDs, event IDs, and model version relevant to that record.

Authoritative Meta-Strategy domains are `settings`, `inventory`, `strategy_state`, `decisions`, `order_intents`, `orders`, `fills`, `trades`, `model_artifacts`, `training`, `replay`, `backtesting`, and `promotion_evidence`.

Meta-Strategy must never read or mutate sibling algorithm private repositories, configuration, strategy implementations, inventory, positions, settings, signals, weights, thresholds, orders, fills, or mutable statistical/ML state. Shared dependencies must go through the explicit protocols in `interfaces.py`: market-data reader, account-data reader, global-risk client, broker gateway, logger, metrics, clock, and market calendar.

HTTP and UI handlers may validate commands, enqueue durable work, and return status. Trading evaluation, training, replay, backtesting, promotion, and broker reconciliation belong in durable background workers; this package boundary does not enable live trading.
