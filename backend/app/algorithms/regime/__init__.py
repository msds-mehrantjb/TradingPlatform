"""Regime algorithm backend boundary."""

from backend.app.algorithms.regime.contracts import REGIME_ALGORITHM_ID, REGIME_ALGORITHM_VERSION
from backend.app.algorithms.regime.broker_adapter import (
    RegimeBrokerSubmission,
    build_regime_broker_submission,
    regime_broker_adapter_inventory,
)
from backend.app.algorithms.regime.condition_monitoring import (
    REGIME_CONDITION_MONITORING_VERSION,
    RegimeConditionMonitoringPolicy,
    regime_condition_monitoring_alerts,
)
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline
from backend.app.algorithms.regime.feed_validation import (
    REGIME_REAL_FEED_VALIDATION_VERSION,
    RealFeedValidationPolicy,
    validate_real_quote_trade_feeds,
)
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
from backend.app.algorithms.regime.occupancy_validation import (
    DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS,
    REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION,
    GoldenRegimeOccupancyBound,
    validate_golden_regime_occupancy,
)
from backend.app.algorithms.regime.paper_trading_ledger import (
    REGIME_PAPER_TRADING_LEDGER_VERSION,
    PaperTradingProofPolicy,
    normalize_paper_trading_proof_record,
    read_regime_paper_trading_proof_ledger,
    record_regime_paper_trading_proof,
    validate_regime_paper_trading_proof_ledger,
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
from backend.app.algorithms.regime.volatility_calibration import (
    INACTIVE_UNTIL_LIVE_PAPER_TRADING,
    INTRADAY_VOLATILITY_CALIBRATION_VERSION,
    build_intraday_volatility_calibration_artifact,
    build_intraday_volatility_context_feed,
)

__all__ = [
    "REGIME_MODULE_INVENTORY",
    "RegimeApplicationService",
    "RegimeBrokerSubmission",
    "RegimeGlobalRiskApproval",
    "RegimeGlobalRiskRequest",
    "GoldenRegimeOccupancyBound",
    "PaperTradingProofPolicy",
    "RegimeConditionMonitoringPolicy",
    "RealFeedValidationPolicy",
    "RegimeMlCandidateArtifact",
    "RegimeMlPromotionDecision",
    "RegimeMlPromotionEvidence",
    "RegimeModuleInventory",
    "RegimeModuleLifecycleStatus",
    "RegimeModuleStatus",
    "RegimeRepository",
    "REGIME_ALGORITHM_ID",
    "REGIME_ALGORITHM_VERSION",
    "REGIME_CONDITION_MONITORING_VERSION",
    "DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS",
    "INACTIVE_UNTIL_LIVE_PAPER_TRADING",
    "INTRADAY_VOLATILITY_CALIBRATION_VERSION",
    "REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION",
    "REGIME_PAPER_TRADING_LEDGER_VERSION",
    "REGIME_REAL_FEED_VALIDATION_VERSION",
    "build_intraday_volatility_calibration_artifact",
    "build_intraday_volatility_context_feed",
    "build_regime_broker_submission",
    "execute_regime_pipeline",
    "evaluate_regime_ml_promotion_policy",
    "evaluate_regime_global_risk_request",
    "normalize_paper_trading_proof_record",
    "read_regime_paper_trading_proof_ledger",
    "record_regime_paper_trading_proof",
    "regime_condition_monitoring_alerts",
    "regime_backend_inventory",
    "regime_broker_adapter_inventory",
    "regime_global_risk_adapter_inventory",
    "regime_repository_inventory",
    "regime_strategy_inventory",
    "validate_real_quote_trade_feeds",
    "validate_golden_regime_occupancy",
    "validate_regime_paper_trading_proof_ledger",
]
