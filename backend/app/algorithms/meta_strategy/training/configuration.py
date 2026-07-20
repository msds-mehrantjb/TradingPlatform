"""Training configuration owned by the Meta-Strategy package."""

from __future__ import annotations

from dataclasses import dataclass


LABELS = ["BUY", "SELL", "HOLD"]
LABEL_TO_INDEX = {label: index for index, label in enumerate(LABELS)}
DEFAULT_RANDOM_SEED = 17
DEFAULT_META_LABEL_VERSION = "candidate_triple_barrier_v1"
META_STRATEGY_FEATURE_SCHEMA_VERSION = "meta_strategy_training_feature_vector_v2"
META_MODEL_V2_TRAINING_REPORT_VERSION = "meta_model_v2_training_validation_v1"
DETERMINISTIC_V2_BASELINE_VERSION = "deterministic_v2_static_baseline_v1"
DETERMINISTIC_BASELINE_NAME = "family_aware_deterministic"
BASELINE_NAMES = [
    DETERMINISTIC_BASELINE_NAME,
    "simple_voting",
    "weighted_voting",
    "confidence_score_aggregation",
    "market_regime_strategy_selection",
]
LOGISTIC_HYPERPARAMETER_GRID = (
    {"epochs": 70, "learningRate": 0.035, "l2": 0.0005},
    {"epochs": 110, "learningRate": 0.025, "l2": 0.0010},
    {"epochs": 150, "learningRate": 0.020, "l2": 0.0015},
)
RANDOM_FOREST_HYPERPARAMETER_GRID = (
    {"treeCount": 40, "maxDepth": 4},
    {"treeCount": 70, "maxDepth": 5},
)


@dataclass(frozen=True)
class MetaTrainingConfig:
    minimumTotalCandidates: int = 120
    minimumBuyCandidates: int = 15
    minimumSellCandidates: int = 15
    minimumPositiveOutcomes: int = 20
    minimumNegativeOutcomes: int = 20
    minimumCandidatesPerOuterFold: int = 20
    minimumTradingSessions: int = 4
    minimumRegimesRepresented: int = 2
    minimumCalibrationRows: int = 60
    minimumIsotonicRows: int = 80
    maximumCalibrationBrier: float = 0.28
    maximumCalibrationLogLoss: float = 1.20
    maximumCalibrationEce: float = 0.12
    outerFolds: int = 3
    innerFolds: int = 3
    finalTestFraction: float = 0.20
    maximumHoldingHorizonMinutes: int = 30
    embargoMinutes: int = 30
    randomSeed: int = DEFAULT_RANDOM_SEED
    minimumNetExpectancyImprovement: float = 0.0
    maximumPromotionDrawdownMultiple: float = 1.05
    minimumPositiveEconomicOuterFolds: int = 2
    maximumSingleFoldProfitShare: float = 0.75
    maximumSingleRegimeProfitShare: float = 0.80
    minimumPromotionTradeCoverage: float = 0.05
    maximumPromotionTradeRejectionRate: float = 0.95
    minimumDirectionalTradesPerSide: int = 1

    def normalized(self) -> "MetaTrainingConfig":
        horizon = max(1, int(self.maximumHoldingHorizonMinutes))
        return MetaTrainingConfig(
            minimumTotalCandidates=max(1, int(self.minimumTotalCandidates)),
            minimumBuyCandidates=max(0, int(self.minimumBuyCandidates)),
            minimumSellCandidates=max(0, int(self.minimumSellCandidates)),
            minimumPositiveOutcomes=max(0, int(self.minimumPositiveOutcomes)),
            minimumNegativeOutcomes=max(0, int(self.minimumNegativeOutcomes)),
            minimumCandidatesPerOuterFold=max(2, int(self.minimumCandidatesPerOuterFold)),
            minimumTradingSessions=max(1, int(self.minimumTradingSessions)),
            minimumRegimesRepresented=max(1, int(self.minimumRegimesRepresented)),
            minimumCalibrationRows=max(1, int(self.minimumCalibrationRows)),
            minimumIsotonicRows=max(1, int(self.minimumIsotonicRows)),
            maximumCalibrationBrier=max(0.0, float(self.maximumCalibrationBrier)),
            maximumCalibrationLogLoss=max(0.0, float(self.maximumCalibrationLogLoss)),
            maximumCalibrationEce=max(0.0, float(self.maximumCalibrationEce)),
            outerFolds=max(1, int(self.outerFolds)),
            innerFolds=max(1, int(self.innerFolds)),
            finalTestFraction=min(0.5, max(0.05, float(self.finalTestFraction))),
            maximumHoldingHorizonMinutes=horizon,
            embargoMinutes=max(horizon, int(self.embargoMinutes)),
            randomSeed=int(self.randomSeed),
            minimumNetExpectancyImprovement=float(self.minimumNetExpectancyImprovement),
            maximumPromotionDrawdownMultiple=max(0.0, float(self.maximumPromotionDrawdownMultiple)),
            minimumPositiveEconomicOuterFolds=max(1, int(self.minimumPositiveEconomicOuterFolds)),
            maximumSingleFoldProfitShare=min(1.0, max(0.0, float(self.maximumSingleFoldProfitShare))),
            maximumSingleRegimeProfitShare=min(1.0, max(0.0, float(self.maximumSingleRegimeProfitShare))),
            minimumPromotionTradeCoverage=min(1.0, max(0.0, float(self.minimumPromotionTradeCoverage))),
            maximumPromotionTradeRejectionRate=min(1.0, max(0.0, float(self.maximumPromotionTradeRejectionRate))),
            minimumDirectionalTradesPerSide=max(0, int(self.minimumDirectionalTradesPerSide)),
        )


__all__ = [
    "BASELINE_NAMES",
    "DEFAULT_META_LABEL_VERSION",
    "DEFAULT_RANDOM_SEED",
    "DETERMINISTIC_BASELINE_NAME",
    "DETERMINISTIC_V2_BASELINE_VERSION",
    "LABELS",
    "LABEL_TO_INDEX",
    "LOGISTIC_HYPERPARAMETER_GRID",
    "META_MODEL_V2_TRAINING_REPORT_VERSION",
    "META_STRATEGY_FEATURE_SCHEMA_VERSION",
    "MetaTrainingConfig",
    "RANDOM_FOREST_HYPERPARAMETER_GRID",
]
