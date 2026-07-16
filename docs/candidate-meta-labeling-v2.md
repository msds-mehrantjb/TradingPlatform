# Candidate Meta-Labeling V2

The initial V2 ML objective is not to invent Buy, Sell, or Hold. The
deterministic family-aware ensemble proposes the candidate side first. ML is a
candidate filter that estimates:

`P(candidate reaches target before stop and remains profitable after costs)`

## Training Target

Only Buy and Sell candidates with complete trade geometry are eligible for the
binary training target:

- `strictOutcomeLabel = 1` when the side-correct profit target is hit before
  the protective stop
- `strictOutcomeLabel = 0` when the stop or vertical barrier is hit first
- `costAdjustedTrainingLabel = 1` only when the strict label is successful and
  the simulated trade remains profitable after spread, slippage, fees, latency,
  and fill behavior
- `costAdjustedTrainingLabel = 0` otherwise

Hold snapshots remain available for diagnostics, but their labels stay null and
`eligibleForTraining=false`. They must not be treated as failed candidate
trades.

## Triple Barrier

The first label version is `candidate_triple_barrier_v1`.

The barriers are side-normalized:

- profit-target barrier: the proposed target price
- protective-stop barrier: the proposed stop price
- vertical barrier: the configured maximum holding period after executable
  entry

For Buy candidates, the target price is above entry and the stop is below entry.
For Sell candidates, the target price is below entry and the stop is above
entry.

## Executable Entry

The label entry price is the next executable simulated price after the decision
timestamp and configured latency. It is not the decision candle close.

The current execution model uses `next_open_after_latency` and applies:

- spread
- slippage
- per-share fees
- flat per-order fees
- same-candle target/stop tie-break policy

Every label records its version, configuration hash, candidate side, executable
entry timestamp, barrier prices, first barrier hit, and a complete barrier
explanation.

