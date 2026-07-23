"""Regime algorithm backend boundary."""

from backend.app.algorithms.regime.contracts import REGIME_ALGORITHM_ID, REGIME_ALGORITHM_VERSION
from backend.app.algorithms.regime.broker_adapter import (
    RegimeBrokerSubmission,
    build_regime_broker_submission,
    regime_broker_adapter_inventory,
)
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline
from backend.app.algorithms.regime.global_risk_adapter import (
    RegimeGlobalRiskApproval,
    RegimeGlobalRiskRequest,
    evaluate_regime_global_risk_request,
    regime_global_risk_adapter_inventory,
)
from backend.app.algorithms.regime.ml import (
    RegimeMlCandidateArtifact,
    RegimeMlPromotionDecision,
    RegimeMlPromotionEvidence,
    evaluate_regime_ml_promotion_policy,
)
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory
from backend.app.algorithms.regime.service import RegimeApplicationService, regime_backend_inventory
from backend.app.algorithms.regime.strategy_registry import (
    REGIME_MODULE_INVENTORY,
    RegimeModuleInventory,
    RegimeModuleLifecycleStatus,
    RegimeModuleStatus,
    regime_strategy_inventory,
)

__all__ = [
    "REGIME_MODULE_INVENTORY",
    "RegimeApplicationService",
    "RegimeBrokerSubmission",
    "RegimeGlobalRiskApproval",
    "RegimeGlobalRiskRequest",
    "RegimeMlCandidateArtifact",
    "RegimeMlPromotionDecision",
    "RegimeMlPromotionEvidence",
    "RegimeModuleInventory",
    "RegimeModuleLifecycleStatus",
    "RegimeModuleStatus",
    "RegimeRepository",
    "REGIME_ALGORITHM_ID",
    "REGIME_ALGORITHM_VERSION",
    "build_regime_broker_submission",
    "execute_regime_pipeline",
    "evaluate_regime_ml_promotion_policy",
    "evaluate_regime_global_risk_request",
    "regime_backend_inventory",
    "regime_broker_adapter_inventory",
    "regime_global_risk_adapter_inventory",
    "regime_repository_inventory",
    "regime_strategy_inventory",
]
