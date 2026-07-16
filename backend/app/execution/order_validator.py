"""Neutral order validation boundary."""

from __future__ import annotations

from backend.app.execution.order_contracts import OrderIntent


def validate_paper_order_intent(intent: OrderIntent) -> OrderIntent:
    if intent.quantity <= 0:
        return intent.model_copy(update={"status": "REJECTED"})
    return intent.model_copy(update={"status": "VALIDATED"})
