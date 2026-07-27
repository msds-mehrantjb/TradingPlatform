"""Shared base for Meta-Strategy safety modules."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.feature_contracts import has_required_input
from backend.app.algorithms.meta_strategy.settings import MetaStrategySafetyGateSettings, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult


class SafetySnapshotStrategy:
    strategy_id = "safety_snapshot_strategy"
    family = "SAFETY"
    required_inputs: tuple[str, ...] = ()

    def __init__(
        self,
        settings: MetaStrategySafetyGateSettings | None = None,
        *,
        settings_version: str = "meta_strategy_settings_v1",
        effective_settings_hash: str = "meta_strategy_settings_unresolved",
    ) -> None:
        injected = settings or build_meta_strategy_settings().safety_gates.get(self.strategy_id)
        self.safety_settings = injected
        self.settings_version = settings_version
        self.effective_settings_hash = effective_settings_hash
        if injected is not None:
            for name, value in injected.model_dump(mode="python").items():
                setattr(self, name, value)

    def evaluate(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> SnapshotEvaluationResult:
        snapshot = context_market_snapshot(value)
        required_status = self.required_input_status(value)
        evidence = self.safety_evidence(value, required_status)
        blocks_new_entries = bool(evidence["blocksNewEntries"])
        return SnapshotEvaluationResult(
            strategy_id=self.strategy_id,
            signal="HOLD",
            confidence=1.0 if blocks_new_entries else 0.0,
            eligible=not blocks_new_entries,
            settings_version=snapshot.settings_version,
            effective_settings_hash=snapshot.effective_settings_hash,
            family=self.family,
            evidence={
                **evidence,
                "canGenerateTrade": False,
                "castsIndependentVote": False,
            },
            required_input_status=required_status,
            reason_codes=(str(evidence["reasonCode"]),),
        )

    def required_input_status(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> dict[str, bool]:
        return {name: self.has_input(value, name) for name in self.required_inputs}

    def has_input(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, name: str) -> bool:
        if isinstance(value, MetaStrategyEvaluationContext):
            if name == "cash_available":
                return value.account_snapshot.cash_available is not None
            if name == "avoid_trading":
                return value.operational_health_snapshot.trading_allowed is not None
            if name == "operational_health":
                return value.operational_health_snapshot is not None
            if name in {"daily_loss_state", "trade_count_state", "duplicate_order_state", "existing_position_state", "local_risk_budget"}:
                return value.algorithm_inventory_snapshot is not None
        return has_required_input(context_market_snapshot(value), name)

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext, required_status: dict[str, bool]) -> dict[str, Any]:
        if not all(required_status.values()):
            return block_evidence(
                reason_code=f"meta_strategy.safety.{self.strategy_id}.missing_data",
                observed={"missingInputs": tuple(name for name, ready in required_status.items() if not ready)},
                threshold={},
                existing_position_action="MONITOR",
                missing_data_safe=True,
            )
        return pass_evidence(reason_code=f"meta_strategy.safety.{self.strategy_id}.pass", observed={}, threshold={})


def pass_evidence(*, reason_code: str, observed: dict[str, Any], threshold: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "blocksNewEntries": False,
        "entryBlocking": False,
        "existingPositionAction": "ALLOW_MANAGE",
        "missingDataSafe": False,
        "observed": observed,
        "threshold": threshold,
        "reasonCode": reason_code,
    }


def block_evidence(
    *,
    reason_code: str,
    observed: dict[str, Any],
    threshold: dict[str, Any],
    existing_position_action: str,
    missing_data_safe: bool = False,
) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "blocksNewEntries": True,
        "entryBlocking": True,
        "existingPositionAction": existing_position_action,
        "missingDataSafe": missing_data_safe,
        "observed": observed,
        "threshold": threshold,
        "reasonCode": reason_code,
    }


def missing_required_evidence(strategy_id: str, required_status: dict[str, bool]) -> dict[str, Any]:
    return block_evidence(
        reason_code=f"meta_strategy.safety.{strategy_id}.missing_data",
        observed={"missingInputs": tuple(name for name, ready in required_status.items() if not ready)},
        threshold={},
        existing_position_action="MONITOR",
        missing_data_safe=True,
    )


def critical_data_ready(snapshot: MetaStrategyMarketSnapshot) -> bool:
    return bool(
        snapshot.point_in_time
        and snapshot.source_cutoff_timestamp is not None
        and snapshot.candles.get("1m")
        and snapshot.vwap is not None
        and snapshot.atr.get("1m") is not None
        and (snapshot.spread or snapshot.spread_bps is not None)
        and snapshot.liquidity
    )


def spread_bps(snapshot: MetaStrategyMarketSnapshot) -> float | None:
    value = snapshot.spread.get("basisPoints") if snapshot.spread else snapshot.spread_bps
    return float(value) if value is not None else None


def liquidity_score(snapshot: MetaStrategyMarketSnapshot) -> float | None:
    value = snapshot.liquidity.get("score") if snapshot.liquidity else None
    return float(value) if value is not None else None


def atr_percent(snapshot: MetaStrategyMarketSnapshot) -> float | None:
    atr = snapshot.atr.get("1m")
    return float(atr) / snapshot.last_price if atr is not None else None
