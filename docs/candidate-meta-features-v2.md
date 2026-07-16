# Candidate Meta-Features V2

The V2 ML feature builder creates decision-time features for the candidate
meta-label filter. It does not create labels and it does not use future market
data, fills, final outcomes, or post-decision state.

The first feature schema is `candidate_meta_feature_schema_v1`.

## Feature Groups

The schema contains:

- directional strategy features for the ten registered V2 directional
  strategies
- family scores and family agreement features
- context features for relative strength, breadth, economic events, market
  structure, volume confirmation, and VWAP position
- regime features including ADX, ATR percentile, realized-volatility percentile,
  and family-fit values
- execution features including spread, relative volume, slippage estimate,
  session clock, entry distance, stop distance, target distance, and reward/risk
- candidate features including candidate side, deterministic ensemble score,
  signal margin, and expected transaction cost

Every base feature has a companion `<feature>__missing` indicator. Missing
numeric features are emitted as `0.0`; missing categorical features are emitted
as `__MISSING__`.

## Leakage Policy

The builder rejects snapshots that contain:

- fill results or fill arrays
- final outcomes or final P&L fields
- broker submission results
- upstream meta-model predictions
- gate, policy, order, or candidate timestamps after the decision timestamp
- recursively named future, post-decision, label, outcome, fill, or P&L fields
  inside raw feature payloads

The schema hash is derived from the ordered feature specification and is stable
until the effective feature schema changes.

