"""Regime broker-submission attribution adapter."""

from __future__ import annotations

from dataclasses import dataclass

REGIME_BROKER_ADAPTER_VERSION = "regime_broker_adapter_v1"


@dataclass(frozen=True)
class RegimeBrokerSubmission:
    algorithm_id: str
    decision_id: str
    order_intent_id: str
    symbol: str
    side: str
    quantity: int
    algorithm_version: str
    settings_version: str
    approved_by_global_risk: bool
    submit_to_broker: bool


def build_regime_broker_submission(
    *,
    decision_id: str,
    order_intent_id: str,
    symbol: str,
    side: str,
    quantity: int,
    algorithm_version: str,
    settings_version: str,
    approved_by_global_risk: bool,
) -> RegimeBrokerSubmission:
    approved_quantity = max(0, int(quantity))
    return RegimeBrokerSubmission(
        algorithm_id="regime",
        decision_id=decision_id,
        order_intent_id=order_intent_id,
        symbol=symbol.upper(),
        side=side,
        quantity=approved_quantity,
        algorithm_version=algorithm_version,
        settings_version=settings_version,
        approved_by_global_risk=approved_by_global_risk,
        submit_to_broker=approved_by_global_risk and approved_quantity > 0,
    )


def regime_broker_adapter_inventory() -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "version": REGIME_BROKER_ADAPTER_VERSION,
        "sharedBoundary": "broker client may submit approved Regime proposals only",
        "requiresGlobalApproval": True,
        "preservesAttribution": ("algorithm_id", "decision_id", "order_intent_id", "settings_version", "algorithm_version"),
        "ownsSignals": False,
        "ownsSizing": False,
    }


__all__ = [
    "REGIME_BROKER_ADAPTER_VERSION",
    "RegimeBrokerSubmission",
    "build_regime_broker_submission",
    "regime_broker_adapter_inventory",
]
