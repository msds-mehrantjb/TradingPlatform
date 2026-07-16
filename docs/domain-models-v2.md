# Domain Models V2

The canonical backend V2 domain models live in `backend/app/domain/models.py`. The manually synchronized frontend types live in `frontend/src/domain/models.ts`.

Rules for V2 decision data:

- Backend models are authoritative for validation.
- Frontend types must keep the same enum values and field names.
- Confidence-like values use the documented `0.0` to `1.0` inclusive range.
- `confidence`, `regimeFit`, and `reliability` remain separate values.
- `direction` is derived from `signal`; strategy-fit scores must not become direction.
- UTC timestamps are required internally.
- `sessionDate` is retained separately as the New York market session date.
- Buy geometry requires `stopPrice < entryPrice < targetPrice`.
- Sell geometry requires `targetPrice < entryPrice < stopPrice`.
- V2 strategy output must use `StrategySignal`; do not add strategy-specific one-off output shapes.

