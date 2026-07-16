# Point-In-Time Feature Engine

The shared V2 feature engine lives in `backend/app/domain/feature_engine.py`.

It is the canonical implementation for live paper decisions, historical replay, decision recording, ML feature generation, and backtesting. Callers provide the same `PointInTimeFeatureRequest`; the `executionStyle` field records the caller type but does not change formulas.

Core rules:

- Only completed candles at or before `evaluationTimestamp` are used.
- SPY, QQQ, IWM, and breadth inputs align to the latest completed SPY 1-minute anchor timestamp.
- Auxiliary data may align to the latest timestamp at or before the anchor only inside `maxAuxiliaryAgeSeconds`.
- Stale or missing required auxiliary data sets `dataReady=false`.
- Demo or fallback candles set `eligibleForTraining=false` for ML training snapshots.
- Every feature is returned as a `FeatureValue` with `value`, `sourceTimestamp`, `quality`, and `explanation`.
- Raw inputs used to compute the snapshot are preserved under `rawInputs`.
- Completed QQQ, IWM, and breadth component histories are preserved in `rawInputs` for context modules that need aligned multi-candle calculations.
- Empty breadth component inputs are marked missing for breadth context; proxy breadth must be explicitly labeled as a proxy, not a true breadth feed.
- Internal timestamps are UTC; `sessionDate` remains the explicit New York market-session date.
