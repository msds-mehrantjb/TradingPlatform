"""Dedicated identity and service-boundary contract for Meta-Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_BOUNDARY_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_CONTRACT_VERSION,
    META_STRATEGY_PACKAGE_VERSION,
)


ALGORITHM_ID: Final[str] = "meta_strategy"
ALGORITHM_NAME: Final[str] = "Meta-Strategy"
META_STRATEGY_ALGORITHM_ID: Final[str] = ALGORITHM_ID
META_STRATEGY_ALGORITHM_NAME: Final[str] = ALGORITHM_NAME
META_STRATEGY_API_NAMESPACE: Final[str] = "/api/meta-strategy"
META_STRATEGY_API_TAG: Final[str] = "meta-strategy"
META_STRATEGY_REASON_CODE_PREFIX: Final[str] = "meta_strategy."
META_STRATEGY_ERROR_CODE_PREFIX: Final[str] = "meta_strategy."

META_STRATEGY_OWNED_CAPABILITIES: Final[tuple[str, ...]] = (
    "strategy_implementations",
    "deterministic_candidate_generation",
    "feature_generation",
    "label_generation",
    "model_training",
    "model_artifacts",
    "inference_policy",
    "configuration",
    "dynamic_profiles",
    "local_gates",
    "position_sizing",
    "trade_management",
    "order_intents",
    "persistence",
    "backtesting",
    "rollout_and_promotion",
)
META_STRATEGY_ALLOWED_SHARED_SERVICES: Final[tuple[str, ...]] = (
    "read_only_market_data",
    "read_only_account_data",
    "logging",
    "global_account_risk_controls",
    "broker_transport",
)
META_STRATEGY_FORBIDDEN_PRIVATE_STATE: Final[tuple[str, ...]] = (
    "wca_private_state",
    "regime_private_state",
    "weighted_voting_private_state",
    "voting_ensemble_private_state",
)


@dataclass(frozen=True)
class MetaStrategyServiceBoundary:
    algorithm_id: str = ALGORITHM_ID
    algorithm_name: str = ALGORITHM_NAME
    package_version: str = META_STRATEGY_PACKAGE_VERSION
    boundary_version: str = META_STRATEGY_BOUNDARY_VERSION
    contract_version: str = META_STRATEGY_CONTRACT_VERSION
    configuration_version: str = META_STRATEGY_CONFIGURATION_VERSION
    api_namespace: str = META_STRATEGY_API_NAMESPACE
    api_tag: str = META_STRATEGY_API_TAG
    reason_code_namespace: str = META_STRATEGY_REASON_CODE_PREFIX
    error_code_namespace: str = META_STRATEGY_ERROR_CODE_PREFIX
    owned_capabilities: tuple[str, ...] = META_STRATEGY_OWNED_CAPABILITIES
    allowed_shared_services: tuple[str, ...] = META_STRATEGY_ALLOWED_SHARED_SERVICES
    forbidden_private_state: tuple[str, ...] = META_STRATEGY_FORBIDDEN_PRIVATE_STATE
    production_behavior_changed: bool = False


def meta_strategy_service_boundary() -> MetaStrategyServiceBoundary:
    return MetaStrategyServiceBoundary()


def meta_strategy_reason_code(code: str) -> str:
    normalized = str(code).strip()
    if not normalized:
        raise ValueError("Meta-Strategy reason code cannot be empty")
    if normalized.startswith(META_STRATEGY_REASON_CODE_PREFIX):
        return normalized
    return f"{META_STRATEGY_REASON_CODE_PREFIX}{normalized}"


def is_meta_strategy_reason_code(code: str) -> bool:
    return str(code).startswith(META_STRATEGY_REASON_CODE_PREFIX)
