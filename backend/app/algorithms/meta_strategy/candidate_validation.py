"""Validation for Meta-Strategy deterministic candidate geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["BUY", "SELL", "HOLD"]


class CandidateGeometryValidationError(ValueError):
    """Raised when candidate geometry violates deterministic execution invariants."""


@dataclass(frozen=True)
class CandidateGeometryDraft:
    side: Side
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    stop_distance: float
    target_distance: float
    estimated_cost: float
    reward_risk: float | None
    expected_net_reward_risk: float | None


@dataclass(frozen=True)
class CandidateGeometryValidation:
    valid: bool
    reason_codes: tuple[str, ...]


def validate_candidate_geometry(draft: CandidateGeometryDraft) -> CandidateGeometryValidation:
    reason_codes: list[str] = []
    if draft.side == "HOLD":
        return CandidateGeometryValidation(valid=True, reason_codes=("meta_strategy.geometry.hold_no_trade",))
    if draft.entry_price is None or draft.entry_price <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_entry_price")
    if draft.stop_price is None or draft.stop_price <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_stop_price")
    if draft.target_price is None or draft.target_price <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_target_price")
    if draft.stop_distance <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_stop_distance")
    if draft.target_distance <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_target_distance")
    if draft.estimated_cost < 0:
        reason_codes.append("meta_strategy.geometry.invalid_estimated_cost")
    if draft.reward_risk is None or draft.reward_risk <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_reward_risk")
    if draft.expected_net_reward_risk is None or draft.expected_net_reward_risk <= 0:
        reason_codes.append("meta_strategy.geometry.invalid_expected_net_reward_risk")
    if not reason_codes and draft.side == "BUY":
        if draft.stop_price >= draft.entry_price:
            reason_codes.append("meta_strategy.geometry.long_stop_must_be_below_entry")
        if draft.target_price <= draft.entry_price:
            reason_codes.append("meta_strategy.geometry.long_target_must_be_above_entry")
    if not reason_codes and draft.side == "SELL":
        if draft.stop_price <= draft.entry_price:
            reason_codes.append("meta_strategy.geometry.short_stop_must_be_above_entry")
        if draft.target_price >= draft.entry_price:
            reason_codes.append("meta_strategy.geometry.short_target_must_be_below_entry")
    if reason_codes:
        raise CandidateGeometryValidationError(";".join(reason_codes))
    return CandidateGeometryValidation(valid=True, reason_codes=("meta_strategy.geometry.valid",))


__all__ = [
    "CandidateGeometryDraft",
    "CandidateGeometryValidation",
    "CandidateGeometryValidationError",
    "validate_candidate_geometry",
]
