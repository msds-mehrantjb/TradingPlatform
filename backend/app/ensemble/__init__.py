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
from .family_aware import (
    FamilyAwareDeterministicEnsemble,
    FamilyAwareEnsembleConfig,
    FamilyWeightingDecision,
    MLFamilyWeightSuggestion,
    MLFamilyWeightingConfig,
    deterministic_equal_family_weights,
    evaluate_ml_family_weight_suggestion,
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
