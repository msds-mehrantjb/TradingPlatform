"""Ensemble aggregation package."""

from .diagnostics import (
    HistoricalDecisionTimeStrategyOutput,
    InclusionPerformanceDiagnostic,
    PairwiseDiversityDiagnostic,
    StrategyCorrelationDiagnostic,
    StrategyDiversityDiagnosticsReport,
    StrategySignalObservation,
    strategy_diversity_diagnostics,
    strategy_signal_correlation,
)
from .reliability import (
    ConservativeReliabilityConfig,
    ConservativeStrategyReliabilityEstimator,
    StrategyReliabilityEstimate,
    StrategyReliabilityOutcome,
)

__all__ = [
    "FamilyAwareDeterministicEnsemble",
    "FamilyAwareEnsembleConfig",
    "FamilyWeightingDecision",
    "MLFamilyWeightSuggestion",
    "MLFamilyWeightingConfig",
    "ConservativeReliabilityConfig",
    "ConservativeStrategyReliabilityEstimator",
    "HistoricalDecisionTimeStrategyOutput",
    "InclusionPerformanceDiagnostic",
    "PairwiseDiversityDiagnostic",
    "StrategyCorrelationDiagnostic",
    "StrategyDiversityDiagnosticsReport",
    "StrategyReliabilityEstimate",
    "StrategyReliabilityOutcome",
    "StrategySignalObservation",
    "deterministic_equal_family_weights",
    "evaluate_ml_family_weight_suggestion",
    "strategy_diversity_diagnostics",
    "strategy_signal_correlation",
]

_FAMILY_AWARE_EXPORTS = {
    "FamilyAwareDeterministicEnsemble",
    "FamilyAwareEnsembleConfig",
    "FamilyWeightingDecision",
    "MLFamilyWeightSuggestion",
    "MLFamilyWeightingConfig",
    "deterministic_equal_family_weights",
    "evaluate_ml_family_weight_suggestion",
}


def __getattr__(name: str):
    if name in _FAMILY_AWARE_EXPORTS:
        from backend.app.algorithms.voting_ensemble.ensemble import family_aware

        return getattr(family_aware, name)
    raise AttributeError(name)
