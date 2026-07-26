# WCA Canonical Configuration

Step 2 establishes `WcaConfiguration` as the single authoritative settings revision for Weighted Confidence Aggregation.

## Runtime Rule

Paper, shadow, replay-style execution, and backtests must load an active `WcaConfiguration` revision before making a runtime decision. If no valid active revision is available, WCA blocks new entries with `wca.configuration.missing_active_revision`; protective and risk-reducing exits may still be handled by their dedicated paths.

Runtime code must not construct `default_baseline_settings()` after initialization. Legacy request fields are accepted only at the API compatibility boundary and are migrated into a canonical candidate revision before activation.

## Revision Metadata

Each revision contains:

- `algorithm_id`
- `configuration_id`
- `configuration_version`
- `created_at`
- `activation_timestamp`
- `content_hash`
- `schema_version`
- `creator`
- `source`
- `lifecycle`

The content hash is computed from the complete canonical configuration payload, excluding `content_hash` itself.

## Settings Hierarchy

- `WcaAggregationSettings`
- `WcaRiskSettings`
- `WcaSizingSettings`
- `WcaExecutionSettings`
- `WcaExitSettings`
- `WcaDynamicProfileSettings`
- `WcaCalibrationSettings`
- `WcaWeightSettings`
- `WcaRuntimeSettings`
- `WcaPrimaryStrategySettings`
- `WcaModifierSettings`
- `WcaHardFilterSettings`

The primary strategy, modifier, and hard-filter settings sections have exactly the same slugs as the authoritative WCA module catalog:

- Primary strategies: 11
- Contextual modifiers: 11
- Hard filters: 7

## Persistence

`WcaSqliteRepository` owns configuration revisions in `wca_configuration_versions` and the single active pointer in `wca_active_configuration`.

Repository operations:

- save a candidate revision
- validate a revision
- activate a revision atomically
- read the active configuration
- read a configuration by version
- roll back to a prior complete revision

Every market snapshot, strategy evaluation, decision, proposed order, backtest run, and backtest trade carries the active `configuration_version` and `configuration_hash`.

## Dynamic Settings

Dynamic settings always begin from the WCA-owned baseline inside the active `WcaConfiguration`. Dynamic overlays may only change effective values within hard limits, and risk-expanding overlays are disabled by default.

