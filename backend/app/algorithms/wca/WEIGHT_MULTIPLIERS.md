# WCA Strategy Weight Multipliers

WCA strategy weights are long-term strategy multipliers, not portfolio shares.
The neutral multiplier is `1.00`; every registered active strategy starts at
exactly `1.00`.

## Multiplier Versus Share

- `final_multiplier` is consumed by runtime aggregation.
- `normalized_share` is derived from final multipliers for reporting,
  compatibility, and family-concentration checks.
- Final multipliers must average `1.00` across active strategies.
- Normalized shares must sum to `1.00`.

## Maturity Stages

The weight engine classifies each strategy as:

- `UNTESTED`: zero valid out-of-sample signals, fixed at `1.00`.
- `LOW_SAMPLE`: 1-99 signals, clamped to `0.90-1.10`.
- `LIMITED_ADJUSTMENT`: 100-299 signals, or 300+ signals without required
  walk-forward windows, calendar months, market regimes, or untouched holdout,
  clamped to `0.65-1.35`.
- `FULL_ADJUSTMENT`: 300+ signals with required walk-forward, month, regime,
  untouched holdout, and timestamp-integrity evidence, clamped to `0.25-2.00`.
- `INELIGIBLE`: leaked, corrupted, incomplete, or non-authoritative evidence,
  fixed at `1.00` and not promotable.

Sample reliability is deterministic:

```text
reliability = N / (N + bayesian_prior_signal_count)
sample_adjusted_multiplier = 1.00 + reliability * (target_multiplier - 1.00)
```

## Performance Metrics And Costs

Weights use completed out-of-sample records whose `outcome_available_at` is
strictly before the snapshot cutoff. In-sample and future outcomes are excluded.
Records with leakage or timestamp-integrity failures make the strategy
ineligible.

Quality is a bounded deterministic composite of net edge, profit factor after
costs, directional quality, confidence calibration, walk-forward stability, and
risk quality. Transaction costs include spread, fees, slippage, and market
impact before performance weighting.

## Correlation And Family Caps

Correlation is calculated only on aligned observations with a shared
`evaluation_id`, `signal_id`, or decision-bar timestamp. Sequence-position
zipping is not used. If aligned overlap is below the configured minimum, no
correlation penalty is applied.

Family caps are applied to `normalized_share`. After family caps, multipliers
are projected back to bounded mean-one values without violating maturity ranges.
Infeasible constraints raise an explicit error.

## Version Lifecycle

Weight computation runs only in the WCA research worker. API paths may enqueue
jobs and read persisted results, but they do not compute or activate weights
synchronously.

Lifecycle jobs:

- `compute_strategy_weight_candidate`
- `validate_strategy_weight_candidate`
- `promote_strategy_weight_version`
- `rollback_strategy_weight_version`

Candidates are stored in WCA-owned research candidate persistence. Promotion
requires persisted replay, walk-forward, holdout, transaction-cost, correlation,
numerical-invariant, and rollout/paper evidence. API-supplied booleans are not
authoritative.

Runtime reads the latest immutable ACTIVE WCA multiplier snapshot. If no active
snapshot exists, WCA uses a neutral generated snapshot. One snapshot is pinned
for the entire candle evaluation and changes are picked up only between
evaluations. Live trading remains disabled; WCA remains paper-only.

## Backward Compatibility

Version-1 normalized-share snapshots are never silently reinterpreted as
multipliers. They are adapted at read time using:

```text
multiplier_i = old_share_i * active_strategy_count
```

The adapted snapshot records `wca.weights.v1_share_snapshot_adapted` and does
not rewrite immutable historical records.
