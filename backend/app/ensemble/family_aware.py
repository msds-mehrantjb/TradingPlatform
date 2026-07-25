"""Compatibility re-export for the Voting Ensemble family-aware aggregator.

The authoritative implementation lives in
``backend.app.algorithms.voting_ensemble.ensemble.family_aware``.
"""

from backend.app.algorithms.voting_ensemble.ensemble.family_aware import (
    FamilyAggregate,
    FamilyAwareDeterministicEnsemble,
    FamilyAwareEnsembleConfig,
    FamilyWeightingDecision,
    MLFamilyWeightSuggestion,
    MLFamilyWeightingConfig,
    deterministic_equal_family_weights,
    evaluate_ml_family_weight_suggestion,
    family_weighting_config_hash,
    multipliers_are_bounded,
    normalize_family_multipliers,
)

__all__ = [
    "FamilyAggregate",
    "FamilyAwareDeterministicEnsemble",
    "FamilyAwareEnsembleConfig",
    "FamilyWeightingDecision",
    "MLFamilyWeightSuggestion",
    "MLFamilyWeightingConfig",
    "deterministic_equal_family_weights",
    "evaluate_ml_family_weight_suggestion",
    "family_weighting_config_hash",
    "multipliers_are_bounded",
    "normalize_family_multipliers",
]
