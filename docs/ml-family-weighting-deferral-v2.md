# ML Family Weighting Deferral V2

Dynamic ML family weighting is intentionally disabled by default.

The current V2 ensemble remains the fixed family-aware deterministic baseline with equal family weights as the permanent fallback. The Step 34 deliverable adds only interfaces for later experiments; the current meta-label filter, order flow, and deterministic ensemble do not depend on ML family weighting.

## Future Interface

A later model may suggest bounded family multipliers only after the candidate meta-label filter has passed validation. Suggested multipliers must satisfy:

- finite non-negative values within configured lower and upper bounds,
- normalization to a mean multiplier of 1.0,
- minimum sample requirements,
- regime-specific validation when configured,
- testing against the fixed family-aware deterministic baseline in a separate experiment,
- equal deterministic family weights as the fallback.

Unvalidated, missing, out-of-bounds, negative, or unbounded suggestions are rejected and replaced with equal family weights.

## Default Behavior

The application feature flag `mlFamilyWeightingEnabled` defaults to `false`, and `MLFamilyWeightingConfig.mode` defaults to `OFF`. No current model can assign a family weight that is negative, unbounded, or silently active.
