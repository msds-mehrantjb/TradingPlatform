# Decision Snapshot V2 Persistence

Decision Snapshot V2 persistence keeps two complementary records for each
decision-time evaluation:

- a complete raw `DecisionSnapshotV2` JSON document for audit and replay
- normalized rows for querying strategies, context, regimes, gates, policies,
  orders, fills, outcomes, model training runs, and model predictions

The raw snapshot remains the source for exact replay. Normalized tables are
query indexes over that immutable raw record and must not be treated as a
replacement for the audit JSON.

## Migration

The first normalized persistence migration is:

`decision_snapshot_v2_normalized_001`

It creates:

- `decision_snapshots`
- `strategy_outputs`
- `context_outputs`
- `regime_states`
- `family_scores`
- `gate_results`
- `policy_decisions`
- `orders`
- `fills`
- `trade_outcomes`
- `model_training_runs`
- `model_predictions`

The migration is idempotent and records the applied version in
`schema_migrations`.

## V1 Compatibility

V1 tables are left in place and remain readable after the V2 migration. They are
not rewritten into V2 rows by default, and V1 snapshots remain incompatible with
V2 training unless an explicit migration creates V2 metadata.

V2 training queries must use V2 schema/version columns and must exclude demo,
fallback, or archived V1-only records.

## Idempotency

`decision_snapshots` rejects duplicate decisions by:

- `symbol`
- `decision_timestamp_utc`
- `algorithm_version`
- `strategy_schema_version`
- `configuration_hash`

Saving the exact same raw snapshot twice is idempotent. Saving a different raw
JSON document for the same decision identity raises a duplicate decision error.

Order decisions are also guarded by a unique key over:

- `symbol`
- `generated_at`
- `configuration_hash`
- `candidate_id`

This prevents duplicate order decisions while still preserving the raw snapshot
that produced the order plan.

## Audit Hashing

Each raw snapshot is stored with a SHA-256 `raw_snapshot_hash`. Effective changes
to strategy schema, feature schema, label version, gate version, policy version,
model version, code version, or configuration hash are represented on the
snapshot and therefore create a distinct persisted identity.

