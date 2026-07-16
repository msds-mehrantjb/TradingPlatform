# Meta-Training Nested Walk-Forward V2

The V2 meta-strategy trainer no longer relies on a single 75/25 split, tiny
holdouts, fixed row-count trust thresholds, or calibration fitted on the same
rows used to train the model.

## Validation Policy

The trainer uses nested chronological purged walk-forward validation:

- reserve the most recent sufficiently large period as the final untouched test
- build outer folds from earlier development data
- train each outer fold only on earlier rows
- remove training rows whose label window overlaps the validation period
- apply an embargo at least as large as the maximum holding horizon
- tune hyperparameters with inner chronological purged folds
- generate inner out-of-fold probabilities
- fit probability calibration from those out-of-fold probabilities
- evaluate the selected fold model on the next chronological outer validation
  period

The final holdout is not used for feature selection, hyperparameter tuning,
threshold selection, or calibration.

## Minimum Requirements

Minimum data requirements are configurable:

- total candidates
- Buy candidates
- Sell candidates
- positive outcomes
- negative outcomes
- candidates per outer fold
- trading sessions
- represented regimes

If the requirements or fold construction fail, the trainer returns
`trusted=false`.

## Trust Rule

A model is not trusted merely because it beats a tiny holdout. Trust requires
sufficient data, at least two validated outer walk-forward folds, out-of-fold
calibration, and final untouched test performance that beats reconstructed V1
baselines on directional trust metrics.

