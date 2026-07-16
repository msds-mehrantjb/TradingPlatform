# Backtest Experiment Matrix V2

The V2 replay program compares five required variants over the same market periods, cost assumptions, and decision timestamp universe.

| Variant | Purpose | Promotion role |
| --- | --- | --- |
| A | Existing V1 ensemble, reference only | Not eligible; V1 signal schema is incompatible with V2 training |
| B | Corrected family-aware deterministic ensemble with static baseline settings | Primary V2 promotion baseline |
| C | Variant B plus ML trade filter | Compared to B to isolate ML filter contribution |
| D | Corrected ensemble plus deterministic dynamic trading policy | Compared to B to isolate dynamic policy contribution |
| E | Variant D plus ML filter and bounded risk modifier | Compared to D to isolate bounded ML risk contribution |

Variant A must never be used as the ML promotion baseline. V1 remains a historical reference because V1 and V2 decision snapshots cannot be mixed for model training or evaluation.

Required diagnostic runs:

- add-one strategy tests
- leave-one-out strategy tests
- context ablations
- regime-filter ablations
- family-normalization ablation
- global-gate ablation for diagnostics only
- static-versus-dynamic policy comparison

The experiment report must include fold-level and aggregate metrics. It rejects runs where any variant uses a different market period, cost assumption hash, or candidate universe hash. The candidate universe is defined by fold, symbol, and decision timestamp so that strategy repair, ML filtering, policy changes, and gate effects are isolated from sampling differences.
