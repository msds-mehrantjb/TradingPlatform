# Meta-Model Champion and Challenger Models V2

Step 31 keeps the deterministic ensemble as the source of candidate side and trains meta-models only as candidate-quality filters.

## Model roster

- Champion: regularized softmax logistic regression.
- Required challenger: deterministic random forest.
- Optional challengers: XGBoost and LightGBM, when their Python packages are installed.

The application must not require XGBoost or LightGBM to import, start, or train the champion. If either optional package is missing or fails to train, the artifact records an unavailable challenger entry with an explicit reason.

## Reproducibility

Training uses fixed random seeds, bounded hyperparameter grids, deterministic feature ordering, and stable model hashes. The artifact stores:

- training window,
- feature schema version and hash,
- label version,
- hyperparameters,
- calibration method,
- decision thresholds,
- metrics by fold,
- final untouched holdout metrics,
- per-model reproducible hash.

The top-level artifact and every saved model include the same feature schema hash. Loading rejects artifacts whose schema hash does not match the expected feature schema, preventing accidental use of a model against the wrong decision-time feature set.

## Calibration

The logistic champion remains the probability-sizing candidate and uses out-of-fold calibration from the nested walk-forward process. The random forest challenger is also calibrated from out-of-fold rows. Optional boosters are reported as uncalibrated challengers unless a later phase adds out-of-fold calibration for them.

No deep neural network is included in the initial champion/challenger roster.

## Economic Promotion

Model promotion is not based on accuracy alone. The training report compares the champion against the corrected family-aware deterministic baseline using net expectancy after costs, profit factor, maximum drawdown, worst day, net P&L, return per unit of risk, trade coverage, rejection rate, Buy/Sell behavior, regime and time-of-day performance, Brier score, and calibration error.

Promotion requires configurable evidence that the model improves net expectancy, keeps drawdown acceptable, works across multiple outer folds, avoids dependence on one fold or regime, retains enough trades to remain operational, handles both Buy and Sell candidates, and remains acceptably calibrated. The report records explicit rejection reason codes and warning flags, including drawdown vetoes and fold/regime concentration. Bootstrap expectancy intervals are included where retained trades are available.

## Safe Inference Modes

Runtime ML inference supports OFF, SHADOW, FILTER, ACTIVE, and FALLBACK modes. OFF ignores ML. SHADOW records predictions without changing paper orders. FILTER can only accept or reject the deterministic candidate. ACTIVE can accept or reject and apply a bounded risk cap. FALLBACK uses the deterministic baseline when configured fallback conditions occur.

ML inference cannot flip Buy to Sell, flip Sell to Buy, create a trade from Hold, specify unrestricted share quantity, or bypass hard gates. Model health, current-candidate probability, feature missingness, out-of-distribution score, and feature-schema compatibility are checked separately. Schema mismatch, unavailable models, or OOD conditions either fall back to the deterministic baseline or force no-trade according to configuration, with explicit reason codes.
