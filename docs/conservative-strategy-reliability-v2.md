# Conservative Strategy Reliability V2

Step 23 adds `ConservativeStrategyReliabilityEstimator`.

## Inputs

Reliability estimates use only completed prior outcomes from:

- prior out-of-sample walk-forward evaluations
- completed paper-trading outcomes

Each outcome records:

- strategy id and family
- regime label
- decision timestamp
- completion timestamp
- outcome in R
- costs in R
- drawdown contribution
- probability uncertainty
- source

Outcomes with `completedAt >= decisionTimestamp` are ignored. The estimator therefore cannot use the current evaluation period's future outcome.

## Scoring

The estimator starts every strategy at neutral reliability, initially `0.50`, then applies bounded evidence from:

- net expectancy after costs
- sample size
- regime-specific performance
- recent performance
- maximum drawdown contribution
- probability uncertainty

Small samples are shrunk toward neutral using:

`sampleSize / (sampleSize + fullWeightSampleSize)`

Reliability is then clamped between configured lower and upper bounds. This prevents a few extreme trades from creating an extreme strategy weight.

## Modes

- `SHADOW`: reliability is reported in signal features and source metadata, but the ensemble continues using the signal's existing reliability.
- `ACTIVE`: estimated reliability replaces the signal reliability used by the ensemble.
- `FALLBACK`: all estimated signals use the configured neutral reliability for equal-weight behavior.
- `OFF`: available as a configuration state for callers that do not pass estimates.

Every estimate includes a reliability version, configuration hash, reason codes, and source window.
