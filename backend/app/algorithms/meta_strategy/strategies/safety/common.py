"""Shared base for Meta-Strategy safety modules."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult


class SafetySnapshotStrategy:
    strategy_id = "safety_snapshot_strategy"
    family = "SAFETY"
    required_inputs: tuple[str, ...] = ()

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> SnapshotEvaluationResult:
        required_status = self.required_input_status(snapshot)
        evidence = self.safety_evidence(snapshot, required_status)
        blocks_new_entries = bool(evidence["blocksNewEntries"])
        return SnapshotEvaluationResult(
            strategy_id=self.strategy_id,
            signal="HOLD",
            confidence=1.0 if blocks_new_entries else 0.0,
            eligible=not blocks_new_entries,
            family=self.family,
            evidence={
                **evidence,
                "canGenerateTrade": False,
                "castsIndependentVote": False,
            },
            required_input_status=required_status,
            reason_codes=(str(evidence["reasonCode"]),),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return {name: self.has_input(snapshot, name) for name in self.required_inputs}

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        if name == "spread":
            return bool(snapshot.spread) or snapshot.spread_bps is not None
        if name == "liquidity":
            return bool(snapshot.liquidity)
        if name == "economic_event_state":
            return bool(snapshot.economic_event_state)
        if name == "session_phase":
            return bool(snapshot.session_phase)
        if name == "source_cutoff_timestamp":
            return snapshot.source_cutoff_timestamp is not None
        if name == "atr":
            return snapshot.atr.get("1m") is not None
        if name == "relative_volume":
            return snapshot.relative_volume.get("1m") is not None
        if name == "halt_luld_state":
            return snapshot.features.get("haltLuldState") is not None
        if name == "operational_health":
            return snapshot.features.get("operationalHealth") is not None
        if name == "cash_available":
            return snapshot.features.get("cashAvailable") is not None
        if name == "avoid_trading":
            return snapshot.features.get("avoidTrading") is not None
        if name == "critical_data":
            return critical_data_ready(snapshot)
        return snapshot.features.get(name) is not None

    def safety_evidence(self, snapshot: MetaStrategyMarketSnapshot, required_status: dict[str, bool]) -> dict[str, Any]:
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
