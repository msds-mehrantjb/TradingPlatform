# Ensemble V2 Structure

This branch introduces behavior-preserving boundaries for the future V2 trading engine. V1 remains the active implementation.

## Backend Packages

- `backend/app/domain`: market feature calculation facades.
- `backend/app/domain/models.py`: canonical V2 domain models and validation.
- `backend/app/domain/feature_engine.py`: shared point-in-time feature engine for live, replay, recording, ML, and backtesting.
- `backend/app/strategies`: strategy catalog, fit, and signal facades.
- `backend/app/strategies/registry.py`: canonical V2 strategy registry and alias migration map.
- `backend/app/strategies/base.py`: shared Phase 3 directional strategy contract helpers.
- `backend/app/ensemble`: ensemble vote aggregation facades.
- `backend/app/ml`: ML feature/inference facades.
- `backend/app/backtesting`: backtest facades.
- `backend/app/trading_policy`: dynamic trading policy and risk config facades.
- `backend/app/gates`: gate evaluation facades.
- `backend/app/execution`: quantity calculation and order construction facades.
- `backend/app/api`: engine interface selection for V1 now and V2 later.

The new backend V1 adapters use lazy imports into `backend.app.main` so the current API module does not import the new packages and no circular import is introduced.

## Frontend Modules

- `frontend/src/api`: API constants and backend config client contracts.
- `frontend/src/domain`: shared trading signal helpers and decision contracts.
- `frontend/src/domain/models.ts`: manually synchronized V2 frontend domain types.
- `frontend/src/ensemble`: ensemble API contracts for V1/V2 switching.
- `frontend/src/trading-settings`: settings persistence contracts.
- `frontend/src/gates`: gate display/evaluation contracts.
- `frontend/src/components`: presentation boundary marker.

`frontend/src/main.ts` still hosts the existing V1 UI and paper-trading behavior, but newly introduced V2-facing contracts and extracted signal helpers live outside the monolith.
