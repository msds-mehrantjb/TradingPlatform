"""Missing critical market data safety gate."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, critical_data_ready, pass_evidence


class MissingCriticalDataFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "missing_critical_data_filter"
    required_inputs = ("critical_data",)

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
        observed = {
            "pointInTime": snapshot.point_in_time,
            "hasCutoff": snapshot.source_cutoff_timestamp is not None,
            "hasCandles": bool(snapshot.candles.get("1m")),
            "hasVwap": snapshot.vwap is not None,
            "hasAtr": snapshot.atr.get("1m") is not None,
            "hasSpread": bool(snapshot.spread) or snapshot.spread_bps is not None,
            "hasLiquidity": bool(snapshot.liquidity),
        }
        threshold = {"allCriticalFieldsRequired": True}
        if not critical_data_ready(snapshot):
            return block_evidence(reason_code="meta_strategy.safety.missing_critical_data.blocked", observed=observed, threshold=threshold, existing_position_action="MONITOR", missing_data_safe=True)
        return pass_evidence(reason_code="meta_strategy.safety.missing_critical_data.pass", observed=observed, threshold=threshold)
