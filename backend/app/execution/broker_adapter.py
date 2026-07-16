"""Neutral broker adapter protocol boundary."""

from __future__ import annotations

from typing import Protocol

from backend.app.execution.order_contracts import OrderIntent


class PaperBrokerAdapter(Protocol):
    def submit_paper_order(self, intent: OrderIntent) -> str:
        ...
