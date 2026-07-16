# Regime ML Policy

## Scope

Regime ML is not an unrestricted Buy/Sell generator. It may estimate:

- Probability of each market regime.
- Probability that the regime is transitioning.
- Confidence that the deterministic classification is stable.

The deterministic classifier remains the baseline and fallback.

## Modes

| Mode | Behavior |
| --- | --- |
| `off` | Do not load a model. |
| `shadow` | Build features, attempt prediction, and log diagnostics without changing decisions. This is the paper default. |
| `confirm_only` | May reduce confidence, block a transition, or reduce size. It may not create a trade or increase risk. |
| `active` | Reserved for formally promoted artifacts. It is not enabled automatically. |

Live trading defaults to off. Paper rollout defaults to shadow.

## Feature And Label Versions

| Item | Version |
| --- | --- |
| Feature schema | `regime_ml_features_v1` |
| Disabled artifact | `regime_ml_disabled_v1` |
| Label definition | Stored per offline label as `label_definition_version` |

Feature values are built from decision-time Regime outputs: raw and confirmed rule regime, confidence, hysteresis, score fields, family coverage, axes, evidence values, and missing-feature mask. Live feature building does not calculate future labels.

Offline labels store future observation window, thresholds, label timestamp, source candle range, realized regime, and transition flag. Future returns or future-regime labels are never included in decision-time features.

## Artifact Requirements

An artifact must include `algorithm_id = regime`, model version, feature schema version, label version, training and validation periods, test period, model type, hyperparameters, metrics, class distribution, calibration data, feature names, imputation policy, artifact hash, creation timestamp, promotion status, and trust flag.

Artifact loading rejects:

- Wrong algorithm ID.
- Feature schema mismatch.
- Missing or invalid artifact hash.
- Training end after the decision timestamp.
- Untrusted or untrusted-promotion artifact.
- Unsupported model type.
- Required feature unavailable with no imputation policy.

Approved initial model types are multinomial logistic regression, regularized transition logistic regression, and a tree baseline.

## Validation

Validation is time-ordered only:

- Expanding-window walk-forward.
- Rolling-window walk-forward.
- Purging around overlapping label windows.
- Embargo between train and validation periods.
- Untouched final test period.

Models are compared against most-common-regime, previous-regime, deterministic rule classifier, and random baselines.

Reported metrics include macro F1, per-regime precision/recall, balanced accuracy, log loss, Brier score, calibration error, confusion matrix, transition detection delay, confirm-only trading results, performance by year, and performance by volatility state.

## Promotion Criteria

Promotion beyond shadow requires leakage tests, sufficient class coverage, stable walk-forward results, acceptable calibration, preserved or improved drawdown and expectancy, no dependence on one isolated period, deterministic fallback availability, and retained rollback artifact.

The current policy can promote at most to `confirm_only`. A failing artifact remains shadow-only.
