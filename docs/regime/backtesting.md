# Regime Backtesting

## Dedicated Backend Path

Regime backtesting is authoritative only in Python:

- `backend/app/algorithms/regime/backtest/engine.py`
- `backend.app.algorithms.regime.backtest.engine.run_regime_backtest`

The backtest engine imports the backend Regime execution pipeline, so replay decisions use the same Python market snapshot, classifier, hysteresis, routing, strategy evaluation, confirmation handling, family aggregation, local gates, dynamic profile, sizing, order-intent, and trade-management code used by shadow and paper operation.

Frontend TypeScript does not run authoritative Regime replay. The frontend may enqueue a backend job, poll status, cache returned display results, and render trades/metrics.

## API And Jobs

`POST /api/regime/backtests/run` enqueues a Regime backtest job. It returns a Regime job receipt rather than running the replay inside the request handler.

`GET /api/regime/jobs/{job_id}` polls the job until it is `completed` or `failed`.

Existing frontend API clients keep the same exported function name, `runRegimeBacktestOnBackend`, but now handle the job receipt and polling internally.

## Replay Flow

For each historical timestamp, the backend worker:

```text
slice candles through t
-> execute backend Regime pipeline
-> manage any open backtest position
-> simulate entry no earlier than t+1
-> record decision and trade evidence
-> calculate metrics and walk-forward summary
```

The backtest remains deterministic and fails closed when required data, state, persistence, risk approval, or reconciliation is unavailable.

## Anti-Lookahead

- A signal calculated from candle `t` cannot fill on candle `t`.
- Default execution starts on the next bar.
- Historical candles are sliced only through the decision timestamp.
- If stop and target are both touched intrabar, stop handling is conservative.
- Gap-through-stop exits use the next candle open when applicable.
- ML remains disabled or shadow-only and does not alter decisions, sizing, or orders.

## Ownership

Backtest runs and trades are Regime-owned persistence records. Shared services may supply read-only market/account data and global-risk capacity, but they may not rewrite Regime classification, signals, settings, strategy outputs, profiles, order intents, or backtest results.
