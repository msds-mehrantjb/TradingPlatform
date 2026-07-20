"""Versioned passive configuration for the Meta-Strategy package boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_CONFIGURATION_VERSION, meta_strategy_version_identifiers


META_STRATEGY_CONFIGURATION_NAMESPACE = "meta_strategy"
META_STRATEGY_BASELINE_CONFIGURATION_KEY = "meta_strategy.config.baseline"


@dataclass(frozen=True)
class MetaStrategyConfiguration:
    algorithm_id: str = ALGORITHM_ID
    algorithm_name: str = ALGORITHM_NAME
    config_version: str = META_STRATEGY_CONFIGURATION_VERSION
    configuration_namespace: str = META_STRATEGY_CONFIGURATION_NAMESPACE
    configuration_key: str = META_STRATEGY_BASELINE_CONFIGURATION_KEY
    enabled: bool = False
    operating_mode: str = "OFF"
    owns_runtime_behavior: bool = False
    production_behavior_changed: bool = False

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(self._configuration_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def baseline_configuration(self) -> dict[str, Any]:
        payload = self._configuration_payload()
        payload["configurationHash"] = self.configuration_hash
        payload["reasonCodes"] = ("meta_strategy.config.passive_boundary_ready",)
        payload["explanation"] = "Baseline Meta-Strategy package-boundary configuration is passive until migration activates owned runtime behavior."
        return payload

    def _configuration_payload(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "algorithmName": self.algorithm_name,
            "configVersion": self.config_version,
            "configurationNamespace": self.configuration_namespace,
            "configurationKey": self.configuration_key,
            "enabled": self.enabled,
            "operatingMode": self.operating_mode,
            "ownsRuntimeBehavior": self.owns_runtime_behavior,
            "productionBehaviorChanged": self.production_behavior_changed,
            "versions": meta_strategy_version_identifiers(),
        }


@dataclass(frozen=True)
class MetaStrategyBaselineSettings:
    configuration_version: str = META_STRATEGY_CONFIGURATION_VERSION
    entry_threshold: float = 0.55
    model_probability_threshold: float = 0.55
    risk_percentage: float = 0.005
    position_cap: float = 0.10
    stop_multiplier: float = 1.0
    target_multiplier: float = 2.0
    maximum_holding_minutes: int = 30
    spread_limit_bps: float = 15.0
    liquidity_requirement: float = 50_000.0
    trade_count_limit: int = 5
    allow_long: bool = True
    allow_short: bool = True

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
                raise ValueError(f"meta_strategy.config.{name}_must_be_finite")
        if self.risk_percentage < 0 or self.position_cap < 0:
            raise ValueError("meta_strategy.config.risk_and_position_cap_must_be_non_negative")
        if self.stop_multiplier <= 0 or self.target_multiplier <= 0:
            raise ValueError("meta_strategy.config.stop_and_target_multipliers_must_be_positive")
        if self.maximum_holding_minutes <= 0 or self.trade_count_limit < 0:
            raise ValueError("meta_strategy.config.invalid_time_or_trade_limit")

    @property
    def settings_hash(self) -> str:
        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "configurationVersion": self.configuration_version,
            "entryThreshold": self.entry_threshold,
            "modelProbabilityThreshold": self.model_probability_threshold,
            "riskPercentage": self.risk_percentage,
            "positionCap": self.position_cap,
            "stopMultiplier": self.stop_multiplier,
            "targetMultiplier": self.target_multiplier,
            "maximumHoldingMinutes": self.maximum_holding_minutes,
            "spreadLimitBps": self.spread_limit_bps,
            "liquidityRequirement": self.liquidity_requirement,
            "tradeCountLimit": self.trade_count_limit,
            "allowLong": self.allow_long,
            "allowShort": self.allow_short,
        }


def meta_strategy_configuration() -> MetaStrategyConfiguration:
    return MetaStrategyConfiguration()


def meta_strategy_baseline_settings() -> MetaStrategyBaselineSettings:
    return MetaStrategyBaselineSettings()
