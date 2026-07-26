# WCA Backtest And Replay Production Parity

Step 14 requires WCA backtests and historical replay to prove production parity rather than approximate the production decision path.

The WCA backtest engine builds point-in-time snapshots from completed one-minute bars and invokes the same production pipeline used by paper, shadow, and replay modes. Backtest runs pin the active WCA configuration version and hash, strategy versions, calibration versions, weight version, market-data manifest, cost-model version, execution-simulation version, and optional random seed.

Backtest execution is separated from decision authority. A signal created at bar `t` may fill no earlier than bar `t+1`; same-bar fills are not allowed. The deterministic execution simulator models limit eligibility, volume participation, partial fills, unfilled orders, cancelled orders, expired orders, slippage, fees, and market impact while preserving WCA attribution.

Production parity is checked by sending the same finalized-bar events through runtime shadow, historical replay, and backtest adapters, then comparing pre-broker decision hash, effective settings, strategy evaluations, weights, gates, side, and proposed quantity.

API backtest endpoints enqueue durable WCA research jobs and return a job ID. Expensive backtests, walk-forward runs, holdout runs, reports, and replay-style jobs are executed by the WCA research worker, not synchronously inside request handlers.
