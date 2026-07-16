# Decision Snapshot V2 and V1 Archive Policy

Step 24 expands `DecisionSnapshotV2` into the canonical V2 decision record.

## V2 Snapshot Contract

Every V2 snapshot records:

- schema versions for snapshot, strategy, feature, label, execution, gate, policy, and model payloads
- configuration, strategy-configuration, and trading-settings hashes
- code version or git commit
- symbol and market data feed
- UTC decision timestamp and explicit New York session date
- data quality
- raw market references
- feature snapshot payload
- all directional strategy outputs
- all context outputs
- regime state
- safety output
- family scores through `ensembleDecision`
- deterministic ensemble candidate
- global gate results
- ML prediction
- effective trading policy
- order plan
- broker submission result
- fills
- position state
- final outcome

The snapshot stores all eligible decision timestamps, including Hold/no-trade decisions. When sampling is used, the snapshot carries:

- `samplingProbability`
- `sampleWeight`
- `samplingReason`

## Training Compatibility

V2 snapshots are not automatically training-eligible. Training eligibility is explicit.

Snapshots become incompatible with V2 training when they contain:

- demo or fallback market data
- non-V2 schema metadata
- old duplicated or aggregator vote signals

The model rejects any snapshot that claims `eligibleForTraining=true` while carrying incompatible data.

## V1 Archive Policy

V1 snapshots must be archived separately with `V1SnapshotArchiveRecord`.

Archived V1 data is:

- preserved only for historical comparison
- marked incompatible with V2 training
- not migrated into V2 without explicit migration metadata
- kept separate from V2 training rows to avoid duplicated vote/self-vote contamination

## Reproducibility

A V2 snapshot carries raw market references, the feature snapshot, all model and policy versions, all strategy/context/regime/safety outputs, and the deterministic ensemble decision. This is enough to reproduce and audit the decision without relying on frontend vote logic.

`decision_snapshot_configuration_hash()` produces deterministic hashes from effective strategy, gate, label, model, policy, and trading-setting inputs. Hashes change whenever any effective component changes.
