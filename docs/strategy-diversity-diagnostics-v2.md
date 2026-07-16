# Strategy Diversity Diagnostics V2

Step 22 adds `strategy_diversity_diagnostics`, a backend report generated from historical decision-time strategy outputs.

## Inputs

Each row must include:

- decision key
- UTC decision timestamp
- walk-forward fold id
- out-of-sample marker
- strategy id and family
- decision-time signal and direction
- eligibility
- optional setup id
- realized evaluation outcome in R units

Rows not marked `isOutOfSample=true` are ignored. If no out-of-sample rows are available, report generation fails.

## Measurements

The report includes:

- signal correlation
- directional agreement
- error correlation
- trade overlap
- setup overlap
- family overlap
- incremental expectancy
- incremental drawdown effect

It generates:

- add-one analysis
- leave-one-out analysis
- pairwise strategy correlation matrix
- family correlation matrix

## Guardrails

Diagnostics use historical replay or walk-forward outputs only. They do not run inside a live decision and do not use future candles to compute decision-time features.

Nearly identical strategy pairs are reported with `inclusionTestingOnly=true`. The diagnostic does not automatically remove or disable a strategy solely because signals correlate; it reports measured similarity and performance effects for later inclusion testing.
