"""Execution-adjusted price helpers for Meta-Strategy labels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


CandidateSide = Literal["BUY", "SELL", "HOLD"]


class MetaStrategyLabelingError(ValueError):
    """Raised when a Meta-Strategy label cannot be created from valid inputs."""


@dataclass(frozen=True)
class LabelExecutionCosts:
    gross_pnl_per_share: float
    fees: float
    net_pnl_after_costs: float
    net_pnl_per_share: float


def execution_price(
    raw_price: float,
    side: CandidateSide,
    *,
    spread_dollars: float,
    slippage_per_share: float,
    is_entry: bool,
) -> float:
    require_finite_positive(raw_price, "raw_price")
    require_finite_non_negative(spread_dollars, "spread_dollars")
    require_finite_non_negative(slippage_per_share, "slippage_per_share")
    if side == "HOLD":
        raise MetaStrategyLabelingError("hold candidates do not have executable prices")
    adverse_cost = (spread_dollars / 2.0) + slippage_per_share
    if is_entry:
        return raw_price + adverse_cost if side == "BUY" else raw_price - adverse_cost
    return raw_price - adverse_cost if side == "BUY" else raw_price + adverse_cost


def execution_costs(
    *,
    side: CandidateSide,
    entry_price: float,
    exit_price: float,
    quantity: float,
    fees_per_share: float,
    flat_fee_per_order: float,
) -> LabelExecutionCosts:
    if side == "HOLD":
        raise MetaStrategyLabelingError("hold candidates do not have execution costs")
    require_finite_positive(entry_price, "entry_price")
    require_finite_positive(exit_price, "exit_price")
    require_finite_positive(quantity, "quantity")
    require_finite_non_negative(fees_per_share, "fees_per_share")
    require_finite_non_negative(flat_fee_per_order, "flat_fee_per_order")
    side_multiplier = 1.0 if side == "BUY" else -1.0
    gross_per_share = side_multiplier * (exit_price - entry_price)
    fees = (flat_fee_per_order * 2.0) + (fees_per_share * quantity * 2.0)
    net_pnl = (gross_per_share * quantity) - fees
    return LabelExecutionCosts(
        gross_pnl_per_share=gross_per_share,
        fees=fees,
        net_pnl_after_costs=net_pnl,
        net_pnl_per_share=net_pnl / quantity,
    )


def geometry_valid(side: CandidateSide, entry_price: float, stop_price: float, target_price: float) -> bool:
    if side == "HOLD":
        return False
    require_finite_positive(entry_price, "entry_price")
    require_finite_positive(stop_price, "stop_price")
    require_finite_positive(target_price, "target_price")
    if side == "BUY":
        return stop_price < entry_price < target_price
    return target_price < entry_price < stop_price


def require_timezone_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MetaStrategyLabelingError(f"{name} must be timezone-aware")


def require_finite_positive(value: float, name: str) -> None:
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise MetaStrategyLabelingError(f"{name} must be a finite positive number")


def require_finite_non_negative(value: float, name: str) -> None:
    if not math.isfinite(float(value)) or float(value) < 0:
        raise MetaStrategyLabelingError(f"{name} must be finite and non-negative")


__all__ = [
    "CandidateSide",
    "LabelExecutionCosts",
    "MetaStrategyLabelingError",
    "execution_costs",
    "execution_price",
    "geometry_valid",
    "require_finite_non_negative",
    "require_finite_positive",
    "require_timezone_aware",
]
