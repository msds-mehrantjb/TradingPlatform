"""Deterministic candidate geometry calculation for Meta-Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.algorithms.meta_strategy.candidate_validation import (
    CandidateGeometryDraft,
    CandidateGeometryValidation,
    CandidateGeometryValidationError,
    validate_candidate_geometry,
)
from backend.app.algorithms.meta_strategy.contracts import CandidateGeometry, DeterministicCandidate, MetaStrategyMarketSnapshot


Side = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class CandidateGeometryConfig:
    atr_stop_multiplier: float = 1.0
    gap_stop_multiplier: float = 1.25
    volatility_stop_multiplier: float = 1.5
    minimum_stop_percent: float = 0.0025
    maximum_stop_percent: float = 0.10
    spread_stop_multiplier: float = 2.0
    target_reward_risk: float = 2.0
    base_maximum_holding_minutes: int = 30
    gap_maximum_holding_minutes: int = 20
    volatility_maximum_holding_minutes: int = 15
    commission_per_share: float = 0.005
    slippage_bps: float = 1.0
    minimum_expected_net_reward_risk: float = 1.0
    default_quantity: float = 1.0


@dataclass(frozen=True)
class CandidateGeometryResult:
    geometry: CandidateGeometry
    entry_reference: float | None
    stop_distance: float
    target_distance: float
    maximum_holding_minutes: int
    estimated_cost: float
    expected_net_reward_risk: float | None
    validation: CandidateGeometryValidation
    evidence: dict[str, object]
    reason_codes: tuple[str, ...]


def calculate_candidate_geometry(
    snapshot: MetaStrategyMarketSnapshot,
    deterministic_candidate: DeterministicCandidate,
    *,
    config: CandidateGeometryConfig | None = None,
) -> CandidateGeometryResult:
    settings = config or CandidateGeometryConfig()
    if deterministic_candidate.signal == "HOLD" or not deterministic_candidate.eligible:
        validation = validate_candidate_geometry(
            CandidateGeometryDraft(
                side="HOLD",
                entry_price=None,
                stop_price=None,
                target_price=None,
                stop_distance=0.0,
                target_distance=0.0,
                estimated_cost=0.0,
                reward_risk=None,
                expected_net_reward_risk=None,
            )
        )
        geometry = CandidateGeometry(
            algorithm_id=snapshot.algorithm_id,
            algorithm_version=snapshot.algorithm_version,
            configuration_version=snapshot.configuration_version,
            strategy_catalog_version=snapshot.strategy_catalog_version,
            decision_id=snapshot.decision_id,
            snapshot_id=snapshot.snapshot_id,
            timestamp=snapshot.timestamp,
            candidate_id=_candidate_geometry_id(snapshot, "HOLD"),
            side="HOLD",
            quantity=0.0,
            risk_reward=None,
        )
        return CandidateGeometryResult(
            geometry=geometry,
            entry_reference=None,
            stop_distance=0.0,
            target_distance=0.0,
            maximum_holding_minutes=0,
            estimated_cost=0.0,
            expected_net_reward_risk=None,
            validation=validation,
            evidence={"validationCannotBeBypassedByMl": True, "mlInvoked": False},
            reason_codes=validation.reason_codes,
        )

    side: Side = deterministic_candidate.signal
    entry = _entry_reference(snapshot, side)
    stop_distance = _stop_distance(snapshot, entry, settings)
    maximum_stop = entry * settings.maximum_stop_percent
    if stop_distance > maximum_stop:
        stop_distance = maximum_stop
    target_distance = stop_distance * settings.target_reward_risk
    cost = _estimated_cost(entry, settings)
    reward_risk = target_distance / stop_distance if stop_distance > 0 else None
    expected_net_reward_risk = (target_distance - cost) / (stop_distance + cost) if stop_distance + cost > 0 else None
    stop_price, target_price = _stop_and_target(side, entry, stop_distance, target_distance)
    draft = CandidateGeometryDraft(
        side=side,
        entry_price=entry,
        stop_price=stop_price,
        target_price=target_price,
        stop_distance=stop_distance,
        target_distance=target_distance,
        estimated_cost=cost,
        reward_risk=reward_risk,
        expected_net_reward_risk=expected_net_reward_risk,
    )
    validation = validate_candidate_geometry(draft)
    if expected_net_reward_risk is None or expected_net_reward_risk < settings.minimum_expected_net_reward_risk:
        raise CandidateGeometryValidationError("meta_strategy.geometry.expected_net_reward_risk_below_minimum")
    geometry = CandidateGeometry(
        algorithm_id=snapshot.algorithm_id,
        algorithm_version=snapshot.algorithm_version,
        configuration_version=snapshot.configuration_version,
        strategy_catalog_version=snapshot.strategy_catalog_version,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        timestamp=snapshot.timestamp,
        candidate_id=_candidate_geometry_id(snapshot, side),
        side=side,
        entry_price=round(entry, 6),
        stop_price=round(stop_price, 6),
        target_price=round(target_price, 6),
        quantity=settings.default_quantity,
        risk_reward=round(reward_risk or 0.0, 6),
    )
    boundary = _boundary_state(snapshot)
    reason_codes = (
        *validation.reason_codes,
        "meta_strategy.geometry.gap_boundary" if boundary["gapBoundary"] else "",
        "meta_strategy.geometry.volatility_boundary" if boundary["volatilityBoundary"] else "",
        "meta_strategy.geometry.calculated",
    )
    evidence = {
        "entryReference": round(entry, 6),
        "stopDistance": round(stop_distance, 6),
        "targetDistance": round(target_distance, 6),
        "maximumHoldingMinutes": _maximum_holding_minutes(snapshot, settings),
        "estimatedCost": round(cost, 6),
        "expectedNetRewardRisk": round(expected_net_reward_risk, 6),
        "rewardRisk": round(reward_risk or 0.0, 6),
        "boundaryState": boundary,
        "validationCannotBeBypassedByMl": True,
        "mlInvoked": False,
    }
    return CandidateGeometryResult(
        geometry=geometry,
        entry_reference=entry,
        stop_distance=round(stop_distance, 6),
        target_distance=round(target_distance, 6),
        maximum_holding_minutes=int(evidence["maximumHoldingMinutes"]),
        estimated_cost=round(cost, 6),
        expected_net_reward_risk=round(expected_net_reward_risk, 6),
        validation=validation,
        evidence=evidence,
        reason_codes=tuple(code for code in reason_codes if code),
    )


def _entry_reference(snapshot: MetaStrategyMarketSnapshot, side: Side) -> float:
    if side == "BUY" and snapshot.ask_price is not None:
        return float(snapshot.ask_price)
    if side == "SELL" and snapshot.bid_price is not None:
        return float(snapshot.bid_price)
    return float(snapshot.last_price)


def _stop_distance(snapshot: MetaStrategyMarketSnapshot, entry: float, settings: CandidateGeometryConfig) -> float:
    atr = float(snapshot.atr.get("1m") or 0.0)
    spread_dollars = float((snapshot.spread or {}).get("dollars") or 0.0)
    minimum_stop = entry * settings.minimum_stop_percent
    multiplier = settings.atr_stop_multiplier
    boundary = _boundary_state(snapshot)
    if boundary["gapBoundary"]:
        multiplier = max(multiplier, settings.gap_stop_multiplier)
    if boundary["volatilityBoundary"]:
        multiplier = max(multiplier, settings.volatility_stop_multiplier)
    return max(atr * multiplier, minimum_stop, spread_dollars * settings.spread_stop_multiplier)


def _stop_and_target(side: Side, entry: float, stop_distance: float, target_distance: float) -> tuple[float, float]:
    if side == "BUY":
        return max(0.01, entry - stop_distance), entry + target_distance
    return entry + stop_distance, max(0.01, entry - target_distance)


def _estimated_cost(entry: float, settings: CandidateGeometryConfig) -> float:
    slippage = entry * settings.slippage_bps / 10_000.0
    return settings.commission_per_share + slippage


def _maximum_holding_minutes(snapshot: MetaStrategyMarketSnapshot, settings: CandidateGeometryConfig) -> int:
    boundary = _boundary_state(snapshot)
    if boundary["volatilityBoundary"]:
        return settings.volatility_maximum_holding_minutes
    if boundary["gapBoundary"]:
        return settings.gap_maximum_holding_minutes
    return settings.base_maximum_holding_minutes


def _boundary_state(snapshot: MetaStrategyMarketSnapshot) -> dict[str, object]:
    gap_percent = abs(float(snapshot.gap_state.get("gapPercent") or 0.0))
    atr_percent = float(snapshot.atr.get("1m") or 0.0) / snapshot.last_price
    return {
        "gapPercent": round(gap_percent, 6),
        "atrPercent": round(atr_percent, 6),
        "gapBoundary": gap_percent >= 1.5,
        "volatilityBoundary": atr_percent >= 0.025,
    }


def _candidate_geometry_id(snapshot: MetaStrategyMarketSnapshot, side: Side) -> str:
    return f"{snapshot.decision_id}:{snapshot.snapshot_id}:{side.lower()}:geometry"


__all__ = [
    "CandidateGeometryConfig",
    "CandidateGeometryResult",
    "calculate_candidate_geometry",
]
