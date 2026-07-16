# Canonical Trading Settings V2

V2 uses one versioned settings schema:

- `canonical_trading_settings_v2`

The backend field inventory lives in `backend/app/domain/trading_settings.py`. The synchronized frontend field inventory lives in `frontend/src/trading-settings/schema.ts`. Backend regression tests compare both files so a frontend/backend field drift fails loudly.

Settings groups:

- baseline settings
- hard limits
- dynamic bounds

The settings hash includes all canonical setting fields plus effective artifact inputs that can change strategies, ensemble decisions, ML predictions, risk, sizing, entries, exits, gates, and backtests.

The old sizing defect is fixed. Signal/risk multipliers below `1.0` reduce risk rather than being clamped upward. For example, with `baseRiskPercent=1.0`, `accountEquity=10000`, and `signalMultiplier=0.25`, baseline risk is `100` and effective risk is `25`.
