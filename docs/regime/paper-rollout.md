# Regime Paper Rollout

## Feature Flags

| Flag | Initial value | Purpose |
| --- | --- | --- |
| `REGIME_V2_ENABLED` | true | Allows Regime V2 code path. |
| `REGIME_DYNAMIC_PROFILE_ENABLED` | true | Allows dynamic profiles to derive effective settings. |
| `REGIME_ML_MODE` | shadow | Runs ML diagnostics without changing decisions. |
| `REGIME_GLOBAL_RISK_MANAGER_ENABLED` | true | Requires backend global risk evaluation. |
| `REGIME_SHORT_ENTRIES_ENABLED` | false | Keeps initial rollout long-only except exits. |

The rollout is paper-only. `live_trading_allowed` is always false in rollout status.

## Deployment Sequence

1. Replay historical characterization cases.
2. Run full dedicated Regime backtest.
3. Run untouched out-of-sample test.
4. Run ML in shadow mode.
5. Run Regime decisions in paper shadow mode without submitting orders.
6. Compare old and new decisions.
7. Enable limited Regime paper orders.
8. Monitor global gate behavior.
9. Collect enough trades across several regimes.
10. Review performance before any promotion.

Each phase is blocked until prerequisite evidence is marked passed. Limited paper orders also require passing tests and the global risk manager flag.

## Activation Steps

To activate limited paper Regime orders:

1. Keep live trading disabled at the broker and application level.
2. Confirm `REGIME_V2_ENABLED=true`.
3. Confirm `REGIME_DYNAMIC_PROFILE_ENABLED=true`.
4. Confirm `REGIME_ML_MODE=shadow`.
5. Confirm `REGIME_GLOBAL_RISK_MANAGER_ENABLED=true`.
6. Confirm `REGIME_SHORT_ENTRIES_ENABLED=false`.
7. Complete historical characterization, dedicated backtest, untouched OOS, paper shadow decisions, and old/new comparison evidence.
8. Confirm frontend build, frontend tests, and backend tests are passing.
9. Enable limited paper orders only through the staged rollout validation state.
10. Monitor global gate denials, resizes, reservations, duplicate IDs, and broker reconciliation.

Do not automatically enable live orders based on one successful backtest.

## Rollback

Rollback configuration:

| Control | Rollback value |
| --- | --- |
| `REGIME_V2_ENABLED` | false |
| `REGIME_DYNAMIC_PROFILE_ENABLED` | false |
| `REGIME_ML_MODE` | off |
| `REGIME_GLOBAL_RISK_MANAGER_ENABLED` | true |
| `REGIME_SHORT_ENTRIES_ENABLED` | false |
| Regime new entries | disabled |
| Protective exits | preserved |
| Previous settings | restore |
| Previous model artifact | restore |
| Database migration rollback | safe only |
| Historical records | do not delete |
| Live orders | false |

Selective rollback can disable only dynamic profiles, only ML, or only Regime entries while preserving exits.
