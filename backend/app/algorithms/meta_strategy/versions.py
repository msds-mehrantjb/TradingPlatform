"""Version constants for the Meta-Strategy package boundary."""

from __future__ import annotations

from typing import Final


META_STRATEGY_PACKAGE_VERSION: Final[str] = "meta_strategy_package_v1"
META_STRATEGY_BOUNDARY_VERSION: Final[str] = "meta_strategy_boundary_v1"
META_STRATEGY_CONTRACT_VERSION: Final[str] = "meta_strategy_contracts_v1"
META_STRATEGY_ALGORITHM_VERSION: Final[str] = "meta_strategy_algorithm_v1"
META_STRATEGY_STRATEGY_CATALOG_VERSION: Final[str] = "meta_strategy_strategy_catalog_v1"
META_STRATEGY_FEATURE_SCHEMA_VERSION: Final[str] = "meta_strategy_feature_schema_v1"
META_STRATEGY_LABEL_SPECIFICATION_VERSION: Final[str] = "meta_strategy_label_specification_v1"
META_STRATEGY_MODEL_VERSION: Final[str] = "meta_strategy_model_v1"
META_STRATEGY_MODEL_ARTIFACT_VERSION: Final[str] = "meta_strategy_model_artifact_v1"
META_STRATEGY_CONFIGURATION_VERSION: Final[str] = "meta_strategy_config_v1"
META_STRATEGY_DYNAMIC_PROFILE_VERSION: Final[str] = "meta_strategy_dynamic_profile_v1"
META_STRATEGY_POSITION_SIZING_VERSION: Final[str] = "meta_strategy_position_sizing_v1"
META_STRATEGY_EXIT_POLICY_VERSION: Final[str] = "meta_strategy_exit_policy_v1"
META_STRATEGY_BACKTEST_ENGINE_VERSION: Final[str] = "meta_strategy_backtest_engine_v1"
META_STRATEGY_VALIDATION_VERSION: Final[str] = "meta_strategy_validation_v1"
META_STRATEGY_OWNERSHIP_VERSION: Final[str] = "meta_strategy_ownership_v1"

META_STRATEGY_MANDATORY_VERSION_FIELDS: Final[tuple[str, ...]] = (
    "algorithmVersion",
    "strategyCatalogVersion",
    "featureSchemaVersion",
    "labelSpecificationVersion",
    "modelVersion",
    "modelArtifactVersion",
    "configurationVersion",
    "dynamicProfileVersion",
    "positionSizingVersion",
    "exitPolicyVersion",
    "backtestEngineVersion",
)

META_STRATEGY_VERSION_IDENTIFIER_ITEMS: Final[tuple[tuple[str, str], ...]] = (
    ("algorithmVersion", META_STRATEGY_ALGORITHM_VERSION),
    ("strategyCatalogVersion", META_STRATEGY_STRATEGY_CATALOG_VERSION),
    ("featureSchemaVersion", META_STRATEGY_FEATURE_SCHEMA_VERSION),
    ("labelSpecificationVersion", META_STRATEGY_LABEL_SPECIFICATION_VERSION),
    ("modelVersion", META_STRATEGY_MODEL_VERSION),
    ("modelArtifactVersion", META_STRATEGY_MODEL_ARTIFACT_VERSION),
    ("configurationVersion", META_STRATEGY_CONFIGURATION_VERSION),
    ("dynamicProfileVersion", META_STRATEGY_DYNAMIC_PROFILE_VERSION),
    ("positionSizingVersion", META_STRATEGY_POSITION_SIZING_VERSION),
    ("exitPolicyVersion", META_STRATEGY_EXIT_POLICY_VERSION),
    ("backtestEngineVersion", META_STRATEGY_BACKTEST_ENGINE_VERSION),
)


def meta_strategy_version_identifiers() -> dict[str, str]:
    return dict(META_STRATEGY_VERSION_IDENTIFIER_ITEMS)
