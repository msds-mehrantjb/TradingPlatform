"""Operational health safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, missing_required_evidence, pass_evidence


class OperationalHealthFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "operational_health_filter"
    required_inputs = ("operational_health",)

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return missing_required_evidence(self.strategy_id, required_status)
        health = snapshot.features["operationalHealth"]
        status = str(health.get("status") or "unknown").lower()
        broker_connected = bool(health.get("brokerConnected"))
        data_connected = bool(health.get("dataConnected"))
        observed = {"status": status, "brokerConnected": broker_connected, "dataConnected": data_connected}
        threshold = {"requiredStatus": "ok", "brokerConnected": True, "dataConnected": True}
        if status != "ok" or not broker_connected or not data_connected:
            return block_evidence(reason_code="meta_strategy.safety.operational_health.blocked", observed=observed, threshold=threshold, existing_position_action="MONITOR")
        return pass_evidence(reason_code="meta_strategy.safety.operational_health.pass", observed=observed, threshold=threshold)
