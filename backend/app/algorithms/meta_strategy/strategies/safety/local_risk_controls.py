"""Local Meta-Strategy safety gates for risk and order-policy evidence."""

from __future__ import annotations

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.strategies.safety.common import SafetySnapshotStrategy, block_evidence, pass_evidence


class DailyLossLimitFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "daily_loss_limit_filter"
    required_inputs = ("daily_loss_state",)

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return super().safety_evidence(value, required_status)
        if isinstance(value, MetaStrategyEvaluationContext):
            pnl = float(value.algorithm_inventory_snapshot.realized_daily_pnl)
            limit = float(value.algorithm_inventory_snapshot.daily_loss_limit)
        else:
            snapshot = context_market_snapshot(value)
            pnl = float(snapshot.features.get("marketDailyPnl") or snapshot.features.get("dailyPnl") or 0.0)
            limit = float(snapshot.features.get("dailyLossLimit") or -abs(snapshot.features.get("maximumDailyLoss", 0.0) or 0.0))
        blocked = limit < 0.0 and pnl <= limit
        evidence = {"dailyPnl": pnl, "dailyLossLimit": limit}
        if blocked:
            return block_evidence(reason_code="meta_strategy.safety.daily_loss_limit_filter.block", observed=evidence, threshold={"dailyLossLimit": limit}, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.daily_loss_limit_filter.pass", observed=evidence, threshold={"dailyLossLimit": limit})


class TradeCountLimitFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "trade_count_limit_filter"
    required_inputs = ("trade_count_state",)

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return super().safety_evidence(value, required_status)
        if isinstance(value, MetaStrategyEvaluationContext):
            count = int(value.algorithm_inventory_snapshot.daily_trade_count)
            limit = int(value.algorithm_inventory_snapshot.daily_trade_limit)
        else:
            snapshot = context_market_snapshot(value)
            count = int(snapshot.features.get("tradeCount") or 0)
            limit = int(snapshot.features.get("tradeCountLimit") or 5)
        evidence = {"tradeCount": count, "tradeCountLimit": limit}
        if limit >= 0 and count >= limit:
            return block_evidence(reason_code="meta_strategy.safety.trade_count_limit_filter.block", observed=evidence, threshold={"tradeCountLimit": limit}, existing_position_action="ALLOW_MANAGE")
        return pass_evidence(reason_code="meta_strategy.safety.trade_count_limit_filter.pass", observed=evidence, threshold={"tradeCountLimit": limit})


class DuplicateOrderProtectionFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "duplicate_order_protection_filter"
    required_inputs = ("duplicate_order_state",)

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return super().safety_evidence(value, required_status)
        state = (
            {"duplicate": value.algorithm_inventory_snapshot.duplicate_order_blocked}
            if isinstance(value, MetaStrategyEvaluationContext)
            else context_market_snapshot(value).features.get("duplicateOrderState") or {}
        )
        duplicate = bool(state.get("duplicate") or state.get("isDuplicate"))
        if duplicate:
            return block_evidence(reason_code="meta_strategy.safety.duplicate_order_protection_filter.block", observed=dict(state), threshold={"duplicateAllowed": False}, existing_position_action="ALLOW_MANAGE")
        return pass_evidence(reason_code="meta_strategy.safety.duplicate_order_protection_filter.pass", observed=dict(state), threshold={"duplicateAllowed": False})


class ExistingPositionPolicyFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "existing_position_policy_filter"
    required_inputs = ("existing_position_state",)

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return super().safety_evidence(value, required_status)
        state = (
            {"policyAllowsEntry": value.algorithm_inventory_snapshot.existing_position_policy_allows_entry}
            if isinstance(value, MetaStrategyEvaluationContext)
            else context_market_snapshot(value).features.get("existingPositionState") or {}
        )
        allowed = bool(state.get("policyAllowsEntry", True))
        if not allowed:
            return block_evidence(reason_code="meta_strategy.safety.existing_position_policy_filter.block", observed=dict(state), threshold={"policyAllowsEntry": True}, existing_position_action="MANAGE_EXISTING_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.existing_position_policy_filter.pass", observed=dict(state), threshold={"policyAllowsEntry": True})


class LocalRiskBudgetFilterStrategy(SafetySnapshotStrategy):
    strategy_id = "local_risk_budget_filter"
    required_inputs = ("local_risk_budget",)

    def safety_evidence(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return super().safety_evidence(value, required_status)
        state = (
            {"remainingRiskDollars": value.algorithm_inventory_snapshot.remaining_risk_dollars}
            if isinstance(value, MetaStrategyEvaluationContext)
            else context_market_snapshot(value).features.get("localRiskBudget") or {}
        )
        remaining = float(state.get("remainingRiskDollars") or state.get("availableRiskDollars") or 0.0)
        if remaining <= 0.0:
            return block_evidence(reason_code="meta_strategy.safety.local_risk_budget_filter.block", observed=dict(state), threshold={"remainingRiskDollars": 0.0}, existing_position_action="REDUCE_ONLY")
        return pass_evidence(reason_code="meta_strategy.safety.local_risk_budget_filter.pass", observed=dict(state), threshold={"remainingRiskDollars": 0.0})
