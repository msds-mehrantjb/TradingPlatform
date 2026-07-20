"""Shared base for Meta-Strategy regime modules."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import (
    MetaStrategyMarketSnapshot,
    RegimeEvaluation,
    meta_strategy_persisted_result_envelope,
)
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult, hold_result


REGIME_STRATEGY_FAMILIES = ("TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION")


class RegimeSnapshotStrategy:
    strategy_id = "regime_snapshot_strategy"
    family = "REGIME"
    required_inputs: tuple[str, ...] = ()

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> SnapshotEvaluationResult:
        required_status = self.required_input_status(snapshot)
        if not snapshot.point_in_time:
            return hold_result(
                self.strategy_id,
                "meta_strategy.regime.snapshot_not_point_in_time",
                family=self.family,
                evidence=safe_regime_evidence(data_ready=False, missing_data_safe=False),
                required_input_status=required_status,
            )
        if not all(required_status.values()):
            return hold_result(
                self.strategy_id,
                "meta_strategy.regime.missing_required_inputs",
                family=self.family,
                evidence=safe_regime_evidence(data_ready=False, missing_data_safe=True),
                required_input_status=required_status,
            )

        evidence = self.regime_evidence(snapshot)
        evaluation = RegimeEvaluation(
            algorithm_id=snapshot.algorithm_id,
            algorithm_version=snapshot.algorithm_version,
            configuration_version=snapshot.configuration_version,
            strategy_catalog_version=snapshot.strategy_catalog_version,
            decision_id=snapshot.decision_id,
            snapshot_id=snapshot.snapshot_id,
            timestamp=snapshot.timestamp,
            regime_id=self.strategy_id,
            label=str(evidence["regimeLabel"]),
            direction=int(evidence["direction"]),
            volatility=str(evidence["volatility"]),
            confidence=float(evidence["regimeConfidence"]),
            features=evidence,
        )
        persisted = meta_strategy_persisted_result_envelope(
            result_type="regime_evaluation",
            payload={
                "strategyId": self.strategy_id,
                "regimeEvaluation": evaluation.model_dump(mode="json"),
                "evidence": evidence,
            },
        )
        complete_evidence = {
            **evidence,
            "dataReady": True,
            "canGenerateTrade": False,
            "castsIndependentVote": False,
            "persistedResult": persisted.model_dump(mode="json"),
        }
        return SnapshotEvaluationResult(
            strategy_id=self.strategy_id,
            signal="HOLD",
            confidence=round(float(evidence["regimeConfidence"]), 6),
            eligible=True,
            family=self.family,
            evidence=complete_evidence,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.regime.{self.strategy_id}.described",),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return {name: self.has_input(snapshot, name) for name in self.required_inputs}

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        if name == "adx":
            return snapshot.adx.get("1m") is not None
        if name == "atr":
            return snapshot.atr.get("1m") is not None
        if name == "moving_averages":
            return bool(snapshot.moving_averages.get("1m"))
        if name == "relative_volume":
            return snapshot.relative_volume.get("1m") is not None
        if name == "economic_event_state":
            return bool(snapshot.economic_event_state)
        return snapshot.features.get(name) is not None

    def regime_evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        return neutral_regime_evidence()


def neutral_regime_evidence() -> dict[str, Any]:
    return {
        "regimeLabel": "unknown",
        "direction": 0,
        "volatility": "NORMAL",
        "regimeConfidence": 0.0,
        "strategyFit": {family: 1.0 for family in REGIME_STRATEGY_FAMILIES},
        "reasonCodes": ("meta_strategy.regime.neutral",),
    }


def safe_regime_evidence(*, data_ready: bool, missing_data_safe: bool) -> dict[str, Any]:
    return {
        **neutral_regime_evidence(),
        "dataReady": data_ready,
        "missingDataSafe": missing_data_safe,
        "canGenerateTrade": False,
        "castsIndependentVote": False,
    }


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def bounded_strategy_fit(values: dict[str, float]) -> dict[str, float]:
    base = {family: 1.0 for family in REGIME_STRATEGY_FAMILIES}
    base.update(values)
    return {family: round(clamp(float(value), 0.0, 2.0), 6) for family, value in base.items()}
