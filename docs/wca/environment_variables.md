# WCA Environment Variables

Use environment variables for deployment configuration only. Do not place credentials or real secrets in documentation.

## Rollout Flags

- `WCA_BACKEND_ENGINE_ENABLED`: enable the backend WCA engine.
- `WCA_CORRECTED_STRATEGY_CATALOG_ENABLED`: enable the corrected strategy catalog.
- `WCA_DYNAMIC_WEIGHTS_ENABLED`: enable dynamic WCA weights.
- `WCA_DYNAMIC_PROFILE_ENABLED`: enable the dynamic defensive overlay resolver.
- `GLOBAL_GATE_ENGINE_ENABLED`: enable shared global-risk gates.
- `WCA_BACKEND_BACKTEST_ENABLED`: enable backend WCA backtesting and replay.
- `WCA_PAPER_EXECUTION_ENABLED`: allow paper execution after evidence-controlled promotion.

## Broker Configuration

Configure the dedicated WCA paper account with deployment-secret storage. Example names:

- `WCA_ALPACA_PAPER_BASE_URL`
- `WCA_ALPACA_PAPER_ACCOUNT_ID`
- `WCA_ALPACA_PAPER_KEY_ID`
- `WCA_ALPACA_PAPER_SECRET_KEY`

The base URL must be an Alpaca paper endpoint. The runtime must refuse live URLs and must refuse automatic-paper stages when the configured account ID does not match the broker account.

## Runtime Settings

Keep mutable WCA settings in WCA-specific configuration, not shared global algorithm configuration. Rollout stage, broker account identity, entry windows, daily limits, latency limits, permitted order types, and strategy enablement must be versioned and persisted with decisions.
