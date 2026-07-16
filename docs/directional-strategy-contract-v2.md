# Directional Strategy Contract V2

All Phase 3 directional strategies must evaluate through the shared contract in `backend/app/strategies/base.py`.

Required behavior:

- Return canonical `StrategySignal` only.
- Use independent setup evidence from point-in-time features.
- Return `BUY`, `SELL`, or `HOLD`.
- Keep `confidence`, `regimeFit`, and `reliability` separate.
- Expose raw feature values under `features`.
- Provide `structuralInvalidationPrice` when the setup has one.
- Return `HOLD`, `eligible=false`, and `dataReady=false` when required features are missing or unready.
- Never use `session.directionBias`, `event.directionBias`, or equivalent proxy inputs as a directional substitute.
- Include synthetic Buy, Sell, Hold, missing-data, and boundary tests for each concrete strategy.

Shared helpers:

- `strategy_signal(...)` builds canonical Buy/Sell/Hold signals and falls back to unavailable Hold when required features are unready.
- `hold_signal(...)` builds canonical no-setup Hold signals.
- `unavailable_signal(...)` builds canonical missing-data Hold signals.
- `validate_no_direction_proxy_inputs(...)` rejects session/event direction proxy feature names.

