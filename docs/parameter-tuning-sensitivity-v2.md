# Parameter Tuning and Sensitivity V2

V2 parameter tuning is scoped to each training fold. Strategy thresholds, ensemble thresholds, ML thresholds, stop/target multipliers, and risk multipliers are selected from training-fold data only.

Configuration groups are stored separately:

- strategy detection
- ensemble aggregation
- ML filtering
- dynamic policy
- global risk
- execution assumptions

Outer validation data is used only after fold-level parameter choices are selected. Final-test data may be loaded only after all choices are frozen and the frozen configuration hash has been recorded.

Sensitivity reports are produced around the selected values. The selector favors broad stable parameter regions over a single sharp optimum by penalizing candidates that do not have nearby parameter configurations with comparable training-fold scores. Parameters with wide score ranges and too few stable neighboring values are flagged as unstable.

Every tuning report includes:

- fold-level selected configuration hashes
- the frozen configuration hash
- sensitivity reports
- unstable parameter list
- explicit reason codes showing validation and final-test leakage controls

Configuration hashes uniquely identify the effective experiment settings and must be included with the experiment matrix result.
