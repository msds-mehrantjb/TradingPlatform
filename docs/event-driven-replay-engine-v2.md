# Event-Driven Replay Engine V2

The V2 backtest path uses `EventDrivenReplayEngine` instead of maintaining a simplified strategy implementation for backtesting.

At each simulated decision timestamp, the engine:

- slices every market-data input to the current timestamp before decision code is called,
- computes completed-candle features with the shared point-in-time feature engine,
- runs the configured directional strategy modules through the family-aware ensemble runner,
- runs configured context modules and the regime classifier,
- evaluates global safety gates,
- creates the deterministic candidate from the family-aware ensemble output,
- applies safe ML inference according to the replay variant,
- creates an effective policy,
- validates an order plan,
- submits the simulated order only after the decision timestamp,
- simulates fills and exits from post-decision candles,
- records a full replay decision snapshot.

Every simulated trade stores `decisionSnapshotId`, linking the trade back to the exact decision snapshot that produced its order plan.

The replay engine rejects any decision call that contains candles later than the evaluation timestamp. Live-style and replay-style decisions can therefore be compared by calling `decide_at` with the same data prefix used by `replay_session`.
