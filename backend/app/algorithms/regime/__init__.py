"""Regime algorithm backend authority boundary."""

from backend.app.algorithms.regime.contracts import (
    REGIME_ALLOWED_RUNTIME_MODE_VALUES,
    REGIME_ALGORITHM_ID,
    REGIME_ALGORITHM_VERSION,
    RegimeRuntimeMode,
    normalize_regime_runtime_mode,
)
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
from backend.app.algorithms.regime.configuration import (
    REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
    REGIME_SETTINGS_MODEL_VERSION,
    RegimeTradingSettings,
    build_default_regime_trading_settings,
    flatten_regime_trading_settings,
    validate_regime_trading_settings_snapshot,
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
from backend.app.algorithms.regime.runtime import (
    REGIME_BACKGROUND_RUNTIME,
    REGIME_BACKGROUND_RUNTIME_VERSION,
    REGIME_BACKGROUND_WORKERS,
    REGIME_PRODUCTION_BACKTEST_CORE,
    REGIME_PRODUCTION_DECISION_CORE,
    REGIME_PRODUCTION_STATE_TRANSITION_CORE,
    RegimeBackgroundJobManager,
    regime_runtime_inventory,
)
from backend.app.algorithms.regime.runtime_state import (
    REGIME_RUNTIME_STATE_SCHEMA_VERSION,
    RegimeRuntimeState,
    migrate_regime_runtime_state,
)
from backend.app.algorithms.regime.service import RegimeApplicationService, regime_backend_inventory
from backend.app.algorithms.regime.stateful_core import (
    REGIME_STATEFUL_CORE_VERSION,
    deterministic_regime_decision_id,
    process_completed_bar,
    process_regime_bar,
)
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
    "RegimeRuntimeState",
    "RegimeTradingSettings",
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
    "RegimeRuntimeMode",
    "REGIME_ALLOWED_RUNTIME_MODE_VALUES",
    "REGIME_ALGORITHM_ID",
    "REGIME_ALGORITHM_VERSION",
    "REGIME_BACKGROUND_RUNTIME",
    "REGIME_BACKGROUND_RUNTIME_VERSION",
    "REGIME_BACKGROUND_WORKERS",
    "REGIME_CONDITION_MONITORING_VERSION",
    "DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS",
    "INACTIVE_UNTIL_LIVE_PAPER_TRADING",
    "INTRADAY_VOLATILITY_CALIBRATION_VERSION",
    "REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION",
    "REGIME_PAPER_TRADING_LEDGER_VERSION",
    "REGIME_PRODUCTION_BACKTEST_CORE",
    "REGIME_PRODUCTION_DECISION_CORE",
    "REGIME_PRODUCTION_STATE_TRANSITION_CORE",
    "REGIME_REAL_FEED_VALIDATION_VERSION",
    "REGIME_RUNTIME_STATE_SCHEMA_VERSION",
    "REGIME_SETTINGS_AUTHORITATIVE_SOURCE",
    "REGIME_SETTINGS_MODEL_VERSION",
    "REGIME_STATEFUL_CORE_VERSION",
    "RegimeBackgroundJobManager",
    "build_default_regime_trading_settings",
    "build_intraday_volatility_calibration_artifact",
    "build_intraday_volatility_context_feed",
    "build_regime_broker_submission",
    "deterministic_regime_decision_id",
    "execute_regime_pipeline",
    "evaluate_regime_ml_promotion_policy",
    "evaluate_regime_global_risk_request",
    "flatten_regime_trading_settings",
    "migrate_regime_runtime_state",
    "normalize_paper_trading_proof_record",
    "normalize_regime_runtime_mode",
    "read_regime_paper_trading_proof_ledger",
    "record_regime_paper_trading_proof",
    "regime_condition_monitoring_alerts",
    "regime_backend_inventory",
    "regime_broker_adapter_inventory",
    "regime_global_risk_adapter_inventory",
    "regime_runtime_inventory",
    "regime_repository_inventory",
    "regime_strategy_inventory",
    "process_completed_bar",
    "process_regime_bar",
    "validate_real_quote_trade_feeds",
    "validate_golden_regime_occupancy",
    "validate_regime_paper_trading_proof_ledger",
    "validate_regime_trading_settings_snapshot",
]
