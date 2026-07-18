"""Final backend Regime order validation."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeOrderIntent


def validate_regime_order_intent(intent: RegimeOrderIntent | None, settings: dict) -> tuple[bool, tuple[str, ...]]:
    if intent is None:
        return False, ("regime.order_validation.no_order_intent",)
    reasons: list[str] = []
    if intent.algorithm_id != "regime":
        reasons.append("regime.order_validation.invalid_algorithm_id")
    if intent.quantity <= 0:
        reasons.append("regime.order_validation.non_positive_quantity")
    if intent.side == "Sell" and not settings.get("shortEntriesEnabled", False):
        reasons.append("regime.order_validation.short_entries_disabled")
    if intent.stop_price is None or intent.target_price is None:
        reasons.append("regime.order_validation.missing_protection")
    return not reasons, tuple(reasons)

