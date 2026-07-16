# Known Limitations

## Data Coverage

- QQQ/IWM relative strength, market breadth, VIX state, ES futures state, scheduled event state, and quote freshness are represented as unknown unless a point-in-time feed supplies them.
- Event blackout, circuit breaker, and stale-data safety checks have conservative placeholders when no live state feed is attached.
- Sector exposure is evaluated only when sector data is present on positions.

## Backtest Fidelity

- The backtest fill model is deterministic and conservative but simplified.
- `execution-simulator.ts` remains a placeholder; the current implementation is embedded in the dedicated engine.
- Partial fills use volume participation; full broker microstructure, queue priority, and venue-specific behavior are not modeled.
- External feeds must be aligned by callers before they are used in historical simulation.
- Walk-forward summaries are lightweight and should be expanded before promotion decisions.

## ML

- Regime ML starts in shadow mode and cannot create trades.
- Promotion policy currently advances at most to confirm-only.
- No trusted production artifact is assumed available.
- Live feature building does not generate labels; labels require offline processing.

## Rollout

- Regime short entries are disabled by default.
- Live trading is not enabled by the rollout module.
- Limited paper orders require explicit staged validation evidence.
- Global settings defaults often use `0` to mean disabled; production paper rollout should set explicit hard limits before order submission.

## Operations

- Existing deprecation warnings from FastAPI/Starlette/Pydantic remain in the backend test suite.
- Vite emits an existing chunk-size warning for the frontend bundle.
- Database-backed implementations may need production locking/migration hardening beyond the in-memory test stores before real broker submission.
