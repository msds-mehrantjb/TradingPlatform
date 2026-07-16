"""Base WCA modifier contract."""

from __future__ import annotations

from typing import Protocol

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaModifierEvaluation


class WcaModifier(Protocol):
    modifier_id: str

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaModifierEvaluation:
        ...
