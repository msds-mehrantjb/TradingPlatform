"""Cash and avoid-trading safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class CashAvoidTradingFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "cash_avoid_trading_filter"
    required_inputs = ("cash_available", "avoid_trading")
    min_cash_available = 500.0

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        cash_available = float(snapshot.features["cashAvailable"])
        avoid_trading = bool(snapshot.features["avoidTrading"])
        observed = {"cashAvailable": cash_available, "avoidTrading": avoid_trading}
        threshold = {"minCashAvailable": self.min_cash_available}
        if avoid_trading or cash_available < self.min_cash_available:
            return block_evidence(reason_code="meta_strategy.safety.cash_avoid_trading.blocked", observed=observed, threshold=threshold, existing_position_action="ALLOW_MANAGE")
        return pass_evidence(reason_code="meta_strategy.safety.cash_avoid_trading.pass", observed=observed, threshold=threshold)
