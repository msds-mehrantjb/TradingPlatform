# Regime V2 Staged Paper Deployment

Regime V2 remains paper-trading only. A successful backtest or shadow run must not enable live orders.

## Initial Feature Flags

| Flag | Initial value | Purpose |
| --- | --- | --- |
| `REGIME_V2_ENABLED` | `true` | Enables the isolated Regime V2 core. |
| `REGIME_DYNAMIC_PROFILE_ENABLED` | `true` | Enables effective-profile derivation from saved baseline settings. |
| `REGIME_ML_MODE` | `shadow` | Runs ML diagnostics without changing decisions. |
| `REGIME_GLOBAL_RISK_MANAGER_ENABLED` | `true` | Requires backend global risk evaluation before paper orders. |
| `REGIME_SHORT_ENTRIES_ENABLED` | `false` | Keeps short entries disabled during initial deployment. |

## Deployment Sequence

1. Replay historical characterization cases.
2. Run the full dedicated Regime backtest.
3. Run the untouched out-of-sample test.
4. Run Regime ML in shadow mode.
5. Run Regime decisions in paper shadow mode without submitting.
6. Compare old and new decisions.
7. Enable limited Regime paper orders only after explicit approval.
8. Monitor global gate behavior.
9. Collect enough trades across several regimes.
10. Review performance before any promotion.

## Rollback

Rollback is feature-flag first and preserves historical records.

- Full flag rollback: set `REGIME_V2_ENABLED=false`, `REGIME_DYNAMIC_PROFILE_ENABLED=false`, `REGIME_ML_MODE=off`, `REGIME_SHORT_ENTRIES_ENABLED=false`.
- Keep `REGIME_GLOBAL_RISK_MANAGER_ENABLED=true` unless the global service itself is being rolled back.
- Restore previous baseline settings from the last valid rollout snapshot.
- Restore the previous trusted Regime ML artifact, or set `REGIME_ML_MODE=off`.
- Roll back database migrations only where the migration is explicitly marked safe to reverse.
- Disable only dynamic profiles with `REGIME_DYNAMIC_PROFILE_ENABLED=false`.
- Disable only ML with `REGIME_ML_MODE=off`.
- Disable only Regime entries while preserving protective exits by setting the Regime entry control to disabled and leaving protective exit handling active.
