"""Regime adapter for shared global account-risk infrastructure."""

from __future__ import annotations

from dataclasses import dataclass

REGIME_GLOBAL_RISK_ADAPTER_VERSION = "regime_global_risk_adapter_v1"


@dataclass(frozen=True)
class RegimeGlobalRiskRequest:
    decision_id: str
    order_intent_id: str
    symbol: str
    requested_quantity: int
    requested_risk_dollars: float
    algorithm_version: str
    settings_version: str
    global_quantity_cap: int | None = None


@dataclass(frozen=True)
class RegimeGlobalRiskApproval:
    algorithm_id: str
    decision_id: str
    order_intent_id: str
    approved_quantity: int
    rejected: bool
    reason_codes: tuple[str, ...]
    signal_rewritten: bool = False
    settings_rewritten: bool = False
    stops_rewritten: bool = False


def evaluate_regime_global_risk_request(request: RegimeGlobalRiskRequest) -> RegimeGlobalRiskApproval:
    approved = max(0, request.requested_quantity)
    reason_codes: list[str] = []
    if request.global_quantity_cap is not None:
        approved = min(approved, max(0, request.global_quantity_cap))
    if approved < request.requested_quantity:
        reason_codes.append("regime.global_risk_adapter.quantity_reduced")
    if approved <= 0 and request.requested_quantity > 0:
        reason_codes.append("regime.global_risk_adapter.rejected_by_global_cap")
    return RegimeGlobalRiskApproval(
        algorithm_id="regime",
        decision_id=request.decision_id,
        order_intent_id=request.order_intent_id,
        approved_quantity=approved,
        rejected=approved <= 0 and request.requested_quantity > 0,
        reason_codes=tuple(reason_codes),
    )


def regime_global_risk_adapter_inventory() -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "version": REGIME_GLOBAL_RISK_ADAPTER_VERSION,
        "sharedBoundary": "global account-risk engine may reduce or reject quantity only",
        "mayRewriteSignals": False,
        "mayRewriteSettings": False,
        "mayRewriteStops": False,
        "requiresAttribution": ("algorithm_id", "decision_id", "order_intent_id", "settings_version", "algorithm_version"),
    }


__all__ = [
    "REGIME_GLOBAL_RISK_ADAPTER_VERSION",
    "RegimeGlobalRiskApproval",
    "RegimeGlobalRiskRequest",
    "evaluate_regime_global_risk_request",
    "regime_global_risk_adapter_inventory",
]
