"""Dedicated Meta-Strategy position sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from math import floor
from typing import Literal

from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings
from backend.app.algorithms.meta_strategy.dynamic_profile import MetaStrategyEffectiveSettings
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_POSITION_SIZING_VERSION


MetaStrategySizingSide = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class MetaStrategySizingConfig:
    position_sizing_version: str = META_STRATEGY_POSITION_SIZING_VERSION
    maximum_share_quantity: int = 10_000
    liquidity_participation_rate: float = 0.10
    configuration_hash: str = "meta_strategy_sizing_v1"

    def __post_init__(self) -> None:
        if self.maximum_share_quantity < 0:
            raise ValueError("meta_strategy.sizing.maximum_share_quantity_must_be_non_negative")
        if not 0.0 <= float(self.liquidity_participation_rate) <= 1.0:
            raise ValueError("meta_strategy.sizing.liquidity_participation_rate_out_of_bounds")


@dataclass(frozen=True)
class MetaStrategySizingContext:
    side: MetaStrategySizingSide
    candidate_accepted: bool
    local_gates_passed: bool
    baseline_settings: MetaStrategyBaselineSettings
    effective_settings: MetaStrategyEffectiveSettings
    model_risk_multiplier: float
    account_equity: float
    available_buying_power: float
    entry_price: float
    stop_distance: float
    market_liquidity: float
    remaining_algorithm_risk: float
    global_available_risk: float
    global_quantity_cap: int

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.model_risk_multiplier) <= 1.0:
            raise ValueError("meta_strategy.sizing.model_risk_multiplier_out_of_bounds")
        for name in (
            "account_equity",
            "available_buying_power",
            "entry_price",
            "stop_distance",
            "market_liquidity",
            "remaining_algorithm_risk",
            "global_available_risk",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"meta_strategy.sizing.{name}_must_be_finite")
        if self.global_quantity_cap < 0:
            raise ValueError("meta_strategy.sizing.global_quantity_cap_must_be_non_negative")


@dataclass(frozen=True)
class MetaStrategySizingCap:
    cap_id: str
    quantity: int
    reason_code: str
    basis: str


@dataclass(frozen=True)
class MetaStrategySizingResult:
    position_sizing_version: str
    quantity: int
    limiting_cap: str
    caps: tuple[MetaStrategySizingCap, ...]
    base_risk_dollars: float
    dynamic_profile_risk_dollars: float
    ml_adjusted_risk_dollars: float
    model_risk_multiplier: float
    risk_based_quantity: int
    position_cap_quantity: int
    buying_power_quantity: int
    liquidity_quantity: int
    maximum_share_quantity: int
    remaining_algorithm_risk_quantity: int
    global_risk_quantity_cap: int
    reason_codes: tuple[str, ...]
    configuration_hash: str


def calculate_meta_strategy_position_size(
    context: MetaStrategySizingContext,
    *,
    config: MetaStrategySizingConfig | None = None,
) -> MetaStrategySizingResult:
    settings = config or MetaStrategySizingConfig()
    side = str(context.side).upper()
    if side == "HOLD" or not context.candidate_accepted:
        return _zero_result(context, settings, "candidate", ("meta_strategy.sizing.candidate_rejected_or_hold",))
    if not context.local_gates_passed:
        return _zero_result(context, settings, "local_gates", ("meta_strategy.sizing.local_gate_failed",))
    invalid_reason = _invalid_market_reason(context)
    if invalid_reason:
        return _zero_result(context, settings, "invalid_market", (invalid_reason,))

    base_risk = max(0.0, context.account_equity * context.baseline_settings.risk_percentage)
    dynamic_risk = min(base_risk, max(0.0, context.account_equity * context.effective_settings.risk_percentage))
    ml_risk = min(dynamic_risk, dynamic_risk * context.model_risk_multiplier)
    risk_based_quantity = _floor_quantity(ml_risk / context.stop_distance)
    position_cap_quantity = _floor_quantity((context.account_equity * context.effective_settings.position_cap) / context.entry_price)
    buying_power_quantity = _floor_quantity(context.available_buying_power / context.entry_price)
    liquidity_quantity = _floor_quantity(context.market_liquidity * settings.liquidity_participation_rate)
    maximum_share_quantity = int(settings.maximum_share_quantity)
    remaining_algorithm_risk_quantity = _floor_quantity(context.remaining_algorithm_risk / context.stop_distance)
    global_risk_quantity_cap = min(
        int(context.global_quantity_cap),
        _floor_quantity(context.global_available_risk / context.stop_distance),
    )
    caps = (
        _cap("risk_based_quantity", risk_based_quantity, "Risk dollars divided by stop distance."),
        _cap("position_cap_quantity", position_cap_quantity, "Effective profile position cap divided by entry price."),
        _cap("buying_power_quantity", buying_power_quantity, "Available buying power divided by entry price."),
        _cap("liquidity_quantity", liquidity_quantity, "Liquidity participation cap."),
        _cap("maximum_share_quantity", maximum_share_quantity, "Algorithm maximum share quantity."),
        _cap("remaining_algorithm_risk_quantity", remaining_algorithm_risk_quantity, "Remaining Meta-Strategy risk divided by stop distance."),
        _cap("global_risk_quantity_cap", global_risk_quantity_cap, "Read-only global risk quantity cap."),
    )
    limiting = min(caps, key=lambda cap: cap.quantity)
    reason_codes = (
        "meta_strategy.sizing.calculated",
        f"meta_strategy.sizing.limiting_cap.{limiting.cap_id}",
        limiting.reason_code,
        "meta_strategy.sizing.ml_cannot_increase_quantity",
    )
    return MetaStrategySizingResult(
        position_sizing_version=settings.position_sizing_version,
        quantity=max(0, limiting.quantity),
        limiting_cap=limiting.cap_id,
        caps=caps,
        base_risk_dollars=round(base_risk, 10),
        dynamic_profile_risk_dollars=round(dynamic_risk, 10),
        ml_adjusted_risk_dollars=round(ml_risk, 10),
        model_risk_multiplier=float(context.model_risk_multiplier),
        risk_based_quantity=risk_based_quantity,
        position_cap_quantity=position_cap_quantity,
        buying_power_quantity=buying_power_quantity,
        liquidity_quantity=liquidity_quantity,
        maximum_share_quantity=maximum_share_quantity,
        remaining_algorithm_risk_quantity=remaining_algorithm_risk_quantity,
        global_risk_quantity_cap=global_risk_quantity_cap,
        reason_codes=reason_codes,
        configuration_hash=settings.configuration_hash,
    )


def _zero_result(
    context: MetaStrategySizingContext,
    config: MetaStrategySizingConfig,
    limiting_cap: str,
    reason_codes: tuple[str, ...],
) -> MetaStrategySizingResult:
    zero_cap = _cap(limiting_cap, 0, "Zero quantity safety result.")
    base_risk = max(0.0, context.account_equity * context.baseline_settings.risk_percentage) if math.isfinite(context.account_equity) else 0.0
    dynamic_risk = min(base_risk, max(0.0, context.account_equity * context.effective_settings.risk_percentage)) if math.isfinite(context.account_equity) else 0.0
    return MetaStrategySizingResult(
        position_sizing_version=config.position_sizing_version,
        quantity=0,
        limiting_cap=limiting_cap,
        caps=(zero_cap,),
        base_risk_dollars=round(base_risk, 10),
        dynamic_profile_risk_dollars=round(dynamic_risk, 10),
        ml_adjusted_risk_dollars=0.0,
        model_risk_multiplier=float(context.model_risk_multiplier),
        risk_based_quantity=0,
        position_cap_quantity=0,
        buying_power_quantity=0,
        liquidity_quantity=0,
        maximum_share_quantity=0,
        remaining_algorithm_risk_quantity=0,
        global_risk_quantity_cap=0,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        configuration_hash=config.configuration_hash,
    )


def _invalid_market_reason(context: MetaStrategySizingContext) -> str:
    if context.entry_price <= 0:
        return "meta_strategy.sizing.invalid_entry_price"
    if context.stop_distance <= 0:
        return "meta_strategy.sizing.invalid_stop_distance"
    if context.account_equity <= 0:
        return "meta_strategy.sizing.invalid_account_equity"
    if context.available_buying_power < 0:
        return "meta_strategy.sizing.invalid_buying_power"
    if context.market_liquidity < 0:
        return "meta_strategy.sizing.invalid_liquidity"
    if context.remaining_algorithm_risk < 0:
        return "meta_strategy.sizing.invalid_remaining_algorithm_risk"
    if context.global_available_risk < 0:
        return "meta_strategy.sizing.invalid_global_risk"
    return ""


def _cap(cap_id: str, quantity: int, basis: str) -> MetaStrategySizingCap:
    return MetaStrategySizingCap(
        cap_id=cap_id,
        quantity=max(0, int(quantity)),
        reason_code=f"meta_strategy.sizing.cap.{cap_id}",
        basis=basis,
    )


def _floor_quantity(value: float) -> int:
    if not math.isfinite(value):
        return 0
    return max(0, floor(value))


__all__ = [
    "MetaStrategySizingCap",
    "MetaStrategySizingConfig",
    "MetaStrategySizingContext",
    "MetaStrategySizingResult",
    "MetaStrategySizingSide",
    "calculate_meta_strategy_position_size",
]
