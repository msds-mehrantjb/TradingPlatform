"""Dedicated identity and service-boundary contract for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


WEIGHTED_VOTING_ALGORITHM_ID: Final[str] = "weighted_voting"
WEIGHTED_VOTING_SERVICE_VERSION: Final[str] = "weighted_voting_service_v2"
WEIGHTED_VOTING_API_NAMESPACE: Final[str] = "/api/weighted-voting"
WEIGHTED_VOTING_API_TAG: Final[str] = "weighted-voting"
WEIGHTED_VOTING_API_VERSION: Final[str] = "weighted_voting_api_v2"
WEIGHTED_VOTING_CONFIGURATION_VERSION: Final[str] = "weighted_voting_config_v1"
WEIGHTED_VOTING_STRATEGY_VERSION: Final[str] = "weighted_voting_catalog_v2"
WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION: Final[str] = "weighted_weights_v1"
WEIGHTED_VOTING_REASON_CODE_PREFIX: Final[str] = "weighted_voting."
WEIGHTED_VOTING_ERROR_CODE_PREFIX: Final[str] = "weighted_voting."
WEIGHTED_VOTING_ALGORITHM_CLASS: Final[str] = "rule_based_statistical_weighted_ensemble"
WEIGHTED_VOTING_EXCLUDED_COMPONENTS: Final[tuple[tuple[str, str], ...]] = (
    ("machine_learning_selector", "Machine-learning selector"),
    ("meta_label_model", "Meta-label model"),
    ("market_price_forecast_model", "Market-price forecast model"),
    ("voting_ensemble_output", "Voting Ensemble output"),
    ("wca_output", "WCA output"),
    ("regime_based_trading_output", "Regime-Based Trading output"),
    ("meta_strategy_output", "Meta-strategy output"),
    ("shared_strategy_weights", "Shared strategy weights"),
    ("shared_confidence_thresholds", "Shared confidence thresholds"),
    ("shared_algorithm_trade_state", "Shared algorithm trade state"),
    ("shared_algorithm_backtest_results", "Shared algorithm backtest results"),
    ("shared_mutable_performance_state", "Shared mutable performance state"),
    ("frontend_calculated_authoritative_signal", "Frontend-calculated authoritative signal"),
    ("frontend_calculated_authoritative_quantity", "Frontend-calculated authoritative quantity"),
)
WEIGHTED_VOTING_ALLOWED_SHARED_SERVICES: Final[tuple[tuple[str, str], ...]] = (
    ("raw_candle_and_quote_service", "read_only"),
    ("alpaca_broker_connection", "through_execution_adapter"),
    ("account_equity_and_buying_power", "read_only_snapshot"),
    ("economic_calendar", "read_only"),
    ("global_account_risk_gates", "controlled_proposal_response"),
    ("cross_algorithm_exposure_ledger", "ownership_aware"),
    ("broker_reconciliation", "algorithm_attributed"),
    ("database_connection", "namespaced_records"),
    ("logging_infrastructure", "algorithm_tagged"),
    ("api_server", "dedicated_router"),
    ("monitoring_dashboard", "read_only_presentation"),
)
WEIGHTED_VOTING_SHARED_SERVICE_ALLOWED_ACTIONS: Final[tuple[str, ...]] = (
    "provide_facts",
    "apply_account_wide_maximum_limits",
    "reduce_quantity",
    "reject_order",
    "execute_approved_order",
    "report_broker_status",
)
WEIGHTED_VOTING_SHARED_SERVICE_FORBIDDEN_ACTIONS: Final[tuple[str, ...]] = (
    "generate_weighted_voting_signal",
    "change_strategy_weights",
    "change_strategy_confidence",
    "reverse_trade_direction",
    "increase_requested_quantity",
    "change_local_settings",
    "update_weighted_voting_performance_using_another_algorithms_results",
    "modify_weighted_voting_positions_without_ownership_authorization",
)

WEIGHTED_VOTING_INPUT_MODELS: Final[tuple[str, ...]] = (
    "WeightedVotingEvaluateRequest",
    "WeightedVotingConfigUpdateRequest",
    "WeightedVotingBacktestRequest",
    "WeightedVotingDailyUpdateRequest",
)
WEIGHTED_VOTING_OUTPUT_MODELS: Final[tuple[str, ...]] = (
    "WeightedVotingDecision",
    "WeightedVotingSignal",
    "WeightedWeightState",
    "WeightedOrderProposal",
    "WeightedBacktestRun",
    "WeightedArtifactManifest",
)


@dataclass(frozen=True)
class WeightedVotingServiceBoundary:
    algorithm_id: str = WEIGHTED_VOTING_ALGORITHM_ID
    service_version: str = WEIGHTED_VOTING_SERVICE_VERSION
    api_namespace: str = WEIGHTED_VOTING_API_NAMESPACE
    api_tag: str = WEIGHTED_VOTING_API_TAG
    api_version: str = WEIGHTED_VOTING_API_VERSION
    reason_code_namespace: str = WEIGHTED_VOTING_REASON_CODE_PREFIX
    error_code_namespace: str = WEIGHTED_VOTING_ERROR_CODE_PREFIX
    configuration_version: str = WEIGHTED_VOTING_CONFIGURATION_VERSION
    strategy_version: str = WEIGHTED_VOTING_STRATEGY_VERSION
    active_weight_version: str = WEIGHTED_VOTING_ACTIVE_WEIGHT_VERSION
    algorithm_class: str = WEIGHTED_VOTING_ALGORITHM_CLASS
    excluded_components: tuple[str, ...] = tuple(component_id for component_id, _ in WEIGHTED_VOTING_EXCLUDED_COMPONENTS)
    allowed_shared_services: tuple[str, ...] = tuple(service_id for service_id, _ in WEIGHTED_VOTING_ALLOWED_SHARED_SERVICES)
    input_models: tuple[str, ...] = WEIGHTED_VOTING_INPUT_MODELS
    output_models: tuple[str, ...] = WEIGHTED_VOTING_OUTPUT_MODELS


def weighted_voting_service_boundary() -> WeightedVotingServiceBoundary:
    return WeightedVotingServiceBoundary()


def weighted_voting_exclusion_inventory() -> dict[str, object]:
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "algorithmClass": WEIGHTED_VOTING_ALGORITHM_CLASS,
        "mlDriven": False,
        "authoritativeFrontendLogicAllowed": False,
        "sharedAlgorithmStateAllowed": False,
        "excludedComponents": [
            {"componentId": component_id, "displayName": display_name}
            for component_id, display_name in WEIGHTED_VOTING_EXCLUDED_COMPONENTS
        ],
        "reasonCodes": ("weighted_voting.exclusions.inventory.ready",),
        "explanation": "Weighted Voting is a rule-based and statistical weighted ensemble; ML selectors, sibling algorithm outputs, shared mutable decision state, and frontend-authoritative signal or quantity calculations are explicitly excluded.",
    }


def weighted_voting_shared_service_boundary() -> dict[str, object]:
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "boundary": "shared_platform_services_with_weighted_voting_controls",
        "allowedSharedServices": [
            {"serviceId": service_id, "weightedVotingAccess": access}
            for service_id, access in WEIGHTED_VOTING_ALLOWED_SHARED_SERVICES
        ],
        "allowedSharedServiceActions": WEIGHTED_VOTING_SHARED_SERVICE_ALLOWED_ACTIONS,
        "forbiddenSharedServiceActions": WEIGHTED_VOTING_SHARED_SERVICE_FORBIDDEN_ACTIONS,
        "ownershipRequiredForPositionMutation": True,
        "globalLimitsMayOnlyReduceRisk": True,
        "sharedServicesMayGenerateSignal": False,
        "sharedServicesMayMutateWeights": False,
        "sharedServicesMayMutateLocalSettings": False,
        "sharedServicesMayUseForeignPerformanceForWeightedVoting": False,
        "reasonCodes": ("weighted_voting.shared_services.boundary.ready",),
        "explanation": "Shared services may provide facts, account-wide limits, execution, and status only through controlled Weighted Voting adapters; they may not generate signals, mutate weights/settings/confidence, reverse direction, increase quantity, or modify positions without Weighted Voting ownership authorization.",
    }


def weighted_voting_reason_code(code: str) -> str:
    normalized = str(code).strip()
    if not normalized:
        raise ValueError("Weighted Voting reason code cannot be empty")
    if normalized.startswith(WEIGHTED_VOTING_REASON_CODE_PREFIX):
        return normalized
    return f"{WEIGHTED_VOTING_REASON_CODE_PREFIX}{normalized}"


def is_weighted_voting_reason_code(code: str) -> bool:
    return str(code).startswith(WEIGHTED_VOTING_REASON_CODE_PREFIX)
