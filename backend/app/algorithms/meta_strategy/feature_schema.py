"""Meta-Strategy-owned candidate feature schema."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from backend.app.algorithms.meta_strategy.strategy_registry import DIRECTIONAL_STRATEGIES


META_STRATEGY_FEATURE_SCHEMA_VERSION = "candidate_meta_feature_schema_v1"
MISSING_CATEGORY = "__MISSING__"
META_STRATEGY_FAMILY_ORDER = ("TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION")


@dataclass(frozen=True)
class MetaStrategyFeatureSpec:
    name: str
    group: Literal["directional_strategy", "family", "context", "regime", "execution", "candidate", "upstream_forecast"]
    valueType: Literal["numeric", "categorical"]

    def model_dump(self, *, mode: str = "json") -> dict[str, str]:
        return {"name": self.name, "group": self.group, "valueType": self.valueType}


def meta_strategy_feature_schema() -> tuple[MetaStrategyFeatureSpec, ...]:
    specs: list[MetaStrategyFeatureSpec] = []
    for entry in DIRECTIONAL_STRATEGIES:
        prefix = f"strategy_{entry.strategy_id}"
        for name in ("direction", "confidence", "eligible", "active", "data_ready", "regime_fit", "reliability", "setup_detected"):
            specs.append(MetaStrategyFeatureSpec(name=f"{prefix}_{name}", group="directional_strategy", valueType="numeric"))

    for family in META_STRATEGY_FAMILY_ORDER:
        specs.append(MetaStrategyFeatureSpec(name=f"family_{family.lower()}_score", group="family", valueType="numeric"))
    for name in (
        "family_agreement",
        "supporting_family_count",
        "opposing_family_count",
        "directional_dispersion",
        "strongest_family_score",
        "weakest_family_score",
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="family", valueType="numeric"))
    specs.extend(
        [
            MetaStrategyFeatureSpec(name="strongest_family", group="family", valueType="categorical"),
            MetaStrategyFeatureSpec(name="weakest_family", group="family", valueType="categorical"),
        ]
    )

    for name, value_type in (
        ("spy_relative_strength_vs_qqq_iwm", "numeric"),
        ("relative_strength_normalized_score", "numeric"),
        ("breadth_score", "numeric"),
        ("breadth_coverage", "numeric"),
        ("economic_event_state", "categorical"),
        ("economic_event_importance", "categorical"),
        ("market_structure_state", "categorical"),
        ("market_structure_quality", "numeric"),
        ("volume_confirmation_score", "numeric"),
        ("volume_trend", "categorical"),
        ("vwap_position_state", "categorical"),
        ("vwap_distance_atr", "numeric"),
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="context", valueType=value_type))  # type: ignore[arg-type]

    for name, value_type in (
        ("regime_category", "categorical"),
        ("adx", "numeric"),
        ("atr_percentile", "numeric"),
        ("realized_volatility_percentile", "numeric"),
        ("trend_fit", "numeric"),
        ("breakout_fit", "numeric"),
        ("reversal_fit", "numeric"),
        ("mean_reversion_fit", "numeric"),
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="regime", valueType=value_type))  # type: ignore[arg-type]

    for name in (
        "spread_dollars",
        "relative_volume",
        "estimated_slippage",
        "time_of_day_minutes",
        "minutes_since_open",
        "minutes_until_close",
        "entry_distance",
        "stop_distance",
        "target_distance",
        "reward_risk_ratio",
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="execution", valueType="numeric"))

    for name, value_type in (
        ("candidate_side", "categorical"),
        ("deterministic_score", "numeric"),
        ("signal_margin", "numeric"),
        ("expected_transaction_cost", "numeric"),
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="candidate", valueType=value_type))  # type: ignore[arg-type]
    for name, value_type in (
        ("forecast_status", "categorical"),
        ("forecast_probability_buy_success", "numeric"),
        ("forecast_probability_sell_success", "numeric"),
        ("forecast_probability_timeout", "numeric"),
        ("forecast_training_end_age_minutes", "numeric"),
        ("forecast_artifact_id", "categorical"),
    ):
        specs.append(MetaStrategyFeatureSpec(name=name, group="upstream_forecast", valueType=value_type))  # type: ignore[arg-type]
    return tuple(specs)


def meta_strategy_feature_schema_hash() -> str:
    payload = [spec.model_dump(mode="json") for spec in meta_strategy_feature_schema()]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "META_STRATEGY_FAMILY_ORDER",
    "META_STRATEGY_FEATURE_SCHEMA_VERSION",
    "MISSING_CATEGORY",
    "MetaStrategyFeatureSpec",
    "meta_strategy_feature_schema",
    "meta_strategy_feature_schema_hash",
]
