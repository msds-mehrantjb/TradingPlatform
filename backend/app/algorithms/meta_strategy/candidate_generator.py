"""Deterministic candidate generation for Meta-Strategy."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.app.algorithms.meta_strategy.contracts import DeterministicCandidate
from backend.app.algorithms.meta_strategy.family_aggregation import (
    FamilyAggregationConfig,
    FamilyAggregationResult,
    StrategyContribution,
    aggregate_family_scores,
)
from backend.app.algorithms.meta_strategy.strategy_registry import (
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
    MetaStrategyRegistryEntry,
)
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult
from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot


Direction = Literal["BUY", "SELL", "HOLD"]
EXECUTION_SEQUENCE = (
    "market_snapshot",
    "directional_strategies",
    "context_modules",
    "regime_modules",
    "safety_modules",
    "family_aggregation",
    "deterministic_candidate",
)


@dataclass(frozen=True)
class CandidateGenerationConfig:
    aggregation: FamilyAggregationConfig = field(default_factory=lambda: FamilyAggregationConfig(maximum_abstention_rate=0.85))
    block_new_entries_on_safety_failure: bool = True


@dataclass(frozen=True)
class GeneratedDeterministicCandidate:
    direction: Direction
    deterministic_confidence: float
    winning_score: float
    opposing_score: float
    edge: float
    supporting_families: tuple[str, ...]
    opposing_families: tuple[str, ...]
    evidence: dict[str, Any]
    reason_codes: tuple[str, ...]
    deterministic_candidate: DeterministicCandidate


def generate_deterministic_candidate(
    snapshot: MetaStrategyMarketSnapshot,
    *,
    config: CandidateGenerationConfig | None = None,
) -> GeneratedDeterministicCandidate:
    settings = config or CandidateGenerationConfig()
    directional_outputs = evaluate_registry_group(snapshot, DIRECTIONAL_STRATEGIES)
    context_outputs = evaluate_registry_group(snapshot, CONTEXT_STRATEGIES)
    regime_outputs = evaluate_registry_group(snapshot, REGIME_STRATEGIES)
    safety_outputs = evaluate_registry_group(snapshot, SAFETY_STRATEGIES)
    safety_blockers = tuple(output for output in safety_outputs if bool((output.evidence or {}).get("blocksNewEntries")))
    aggregation = aggregate_family_scores(
        _directional_contributions(directional_outputs, DIRECTIONAL_STRATEGIES, context_outputs, regime_outputs),
        config=settings.aggregation,
    )
    safety_blocks = settings.block_new_entries_on_safety_failure and bool(safety_blockers)
    direction: Direction = "HOLD" if safety_blocks else aggregation.signal
    eligible = aggregation.eligible and not safety_blocks
    reason_codes = tuple(
        code
        for code in (
            *aggregation.reason_codes,
            *(code for output in safety_blockers for code in output.reason_codes),
            "meta_strategy.candidate.safety_blocked" if safety_blocks else "",
            "meta_strategy.candidate.generated_without_ml" if eligible else "meta_strategy.candidate.hold_without_ml",
        )
        if code
    )
    supporting_families, opposing_families = _family_alignment(aggregation)
    winning_score, opposing_score = _winning_scores(direction if direction != "HOLD" else aggregation.signal, aggregation)
    edge = round(max(0.0, winning_score - opposing_score) if direction != "HOLD" else 0.0, 6)
    confidence = round(aggregation.confidence if eligible else 0.0, 6)
    candidate_contract = DeterministicCandidate(
        algorithm_id=snapshot.algorithm_id,
        algorithm_version=snapshot.algorithm_version,
        configuration_version=snapshot.configuration_version,
        strategy_catalog_version=snapshot.strategy_catalog_version,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        timestamp=snapshot.timestamp,
        signal=direction,
        confidence=confidence,
        eligible=eligible,
        family_scores=aggregation.to_deterministic_candidate(
            algorithm_version=snapshot.algorithm_version,
            configuration_version=snapshot.configuration_version,
            strategy_catalog_version=snapshot.strategy_catalog_version,
            decision_id=snapshot.decision_id,
            snapshot_id=snapshot.snapshot_id,
            timestamp=snapshot.timestamp,
        ).family_scores,
        reason_codes=reason_codes,
    )
    evidence = {
        "executionSequence": EXECUTION_SEQUENCE,
        "snapshotId": snapshot.snapshot_id,
        "symbol": snapshot.symbol,
        "mlInvoked": False,
        "rawAggregationSignal": aggregation.signal,
        "safetyBlocked": safety_blocks,
        "directionalOutputs": _output_map(directional_outputs),
        "contextOutputs": _output_map(context_outputs),
        "regimeOutputs": _output_map(regime_outputs),
        "safetyOutputs": _output_map(safety_outputs),
        "familyAggregation": _aggregation_evidence(aggregation),
        "safetyBlockers": tuple(output.strategy_id for output in safety_blockers),
        "documentedImprovements": (
            "Meta-Strategy candidate generation is package-owned, never calls ML, applies safety after deterministic aggregation, and caps correlated family influence.",
        ),
    }
    return GeneratedDeterministicCandidate(
        direction=direction,
        deterministic_confidence=confidence,
        winning_score=winning_score,
        opposing_score=opposing_score,
        edge=edge,
        supporting_families=supporting_families,
        opposing_families=opposing_families,
        evidence=evidence,
        reason_codes=reason_codes,
        deterministic_candidate=candidate_contract,
    )


def evaluate_registry_group(
    snapshot: MetaStrategyMarketSnapshot,
    entries: tuple[MetaStrategyRegistryEntry, ...],
) -> tuple[SnapshotEvaluationResult, ...]:
    return tuple(_instantiate(entry).evaluate(snapshot) for entry in entries)


def _instantiate(entry: MetaStrategyRegistryEntry):
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


def _directional_contributions(
    outputs: tuple[SnapshotEvaluationResult, ...],
    entries: tuple[MetaStrategyRegistryEntry, ...],
    context_outputs: tuple[SnapshotEvaluationResult, ...],
    regime_outputs: tuple[SnapshotEvaluationResult, ...],
) -> tuple[StrategyContribution, ...]:
    by_id = {entry.strategy_id: entry for entry in entries}
    context_multiplier = _context_family_multiplier(context_outputs)
    regime_fit = _regime_family_fit(regime_outputs)
    contributions: list[StrategyContribution] = []
    for output in outputs:
        entry = by_id[output.strategy_id]
        family = str(entry.family)
        weight = round(context_multiplier * regime_fit.get(family, 1.0), 6)
        contributions.append(
            StrategyContribution(
                strategy_id=output.strategy_id,
                family=family,
                signal=output.signal if output.signal in {"BUY", "SELL"} else "HOLD",
                confidence=output.confidence,
                eligible=output.eligible,
                weight=weight,
                canonical_influence_id=entry.canonical_influence_id,
                correlation_key=_correlation_key(entry),
            )
        )
    return tuple(contributions)


def _context_family_multiplier(outputs: tuple[SnapshotEvaluationResult, ...]) -> float:
    multipliers = [float((output.evidence or {}).get("familyWeightMultiplier", 1.0)) for output in outputs if output.eligible]
    if not multipliers:
        return 1.0
    return round(max(0.5, min(1.5, sum(multipliers) / len(multipliers))), 6)


def _regime_family_fit(outputs: tuple[SnapshotEvaluationResult, ...]) -> dict[str, float]:
    fits: dict[str, list[float]] = {}
    for output in outputs:
        if not output.eligible:
            continue
        strategy_fit = (output.evidence or {}).get("strategyFit") or {}
        if not isinstance(strategy_fit, dict):
            continue
        for family, value in strategy_fit.items():
            fits.setdefault(str(family), []).append(float(value))
    return {family: round(max(0.0, min(2.0, sum(values) / len(values))), 6) for family, values in fits.items() if values}


def _correlation_key(entry: MetaStrategyRegistryEntry) -> str:
    return f"{entry.family}:{entry.strategy_id}"


def _family_alignment(aggregation: FamilyAggregationResult) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if aggregation.signal == "BUY":
        supporting = tuple(score.family for score in aggregation.family_scores if score.buy_score > score.sell_score and score.buy_score > 0.0)
        opposing = tuple(score.family for score in aggregation.family_scores if score.sell_score > 0.0)
    elif aggregation.signal == "SELL":
        supporting = tuple(score.family for score in aggregation.family_scores if score.sell_score > score.buy_score and score.sell_score > 0.0)
        opposing = tuple(score.family for score in aggregation.family_scores if score.buy_score > 0.0)
    else:
        supporting = ()
        opposing = tuple(score.family for score in aggregation.family_scores if score.buy_score > 0.0 or score.sell_score > 0.0)
    return supporting, opposing


def _winning_scores(direction: Direction, aggregation: FamilyAggregationResult) -> tuple[float, float]:
    if direction == "BUY":
        return aggregation.buy_score, aggregation.sell_score
    if direction == "SELL":
        return aggregation.sell_score, aggregation.buy_score
    return max(aggregation.buy_score, aggregation.sell_score, aggregation.hold_score), max(min(aggregation.buy_score, aggregation.sell_score), 0.0)


def _output_map(outputs: tuple[SnapshotEvaluationResult, ...]) -> dict[str, dict[str, Any]]:
    return {
        output.strategy_id: {
            "signal": output.signal,
            "confidence": output.confidence,
            "eligible": output.eligible,
            "family": output.family,
            "reasonCodes": output.reason_codes,
            "evidence": output.evidence or {},
        }
        for output in outputs
    }


def _aggregation_evidence(aggregation: FamilyAggregationResult) -> dict[str, Any]:
    return {
        "signal": aggregation.signal,
        "eligible": aggregation.eligible,
        "confidence": aggregation.confidence,
        "buyScore": aggregation.buy_score,
        "sellScore": aggregation.sell_score,
        "holdScore": aggregation.hold_score,
        "activeStrategyCount": aggregation.active_strategy_count,
        "activeFamilyCount": aggregation.active_family_count,
        "abstentionRate": aggregation.abstention_rate,
        "familyScores": {
            score.family: {
                "buyScore": score.buy_score,
                "sellScore": score.sell_score,
                "holdScore": score.hold_score,
                "activeStrategyCount": score.active_strategy_count,
                "capped": score.capped,
            }
            for score in aggregation.family_scores
        },
        "reasonCodes": aggregation.reason_codes,
    }


__all__ = [
    "CandidateGenerationConfig",
    "EXECUTION_SEQUENCE",
    "GeneratedDeterministicCandidate",
    "evaluate_registry_group",
    "generate_deterministic_candidate",
]
