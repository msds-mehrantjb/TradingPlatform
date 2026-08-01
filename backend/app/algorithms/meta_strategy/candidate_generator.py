"""Deterministic candidate generation for Meta-Strategy."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from backend.app.algorithms.meta_strategy.candidate_geometry import CandidateGeometryConfig, CandidateGeometryResult, calculate_candidate_geometry
from backend.app.algorithms.meta_strategy.candidate_validation import CandidateGeometryValidationError
from backend.app.algorithms.meta_strategy.contracts import DeterministicCandidate
from backend.app.algorithms.meta_strategy.family_aggregation import (
    FamilyAggregationConfig,
    FamilyAggregationResult,
    StrategyContribution,
    aggregate_family_scores,
)
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategy_registry import (
    ACTIVE_DIRECTIONAL_STRATEGIES,
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
    geometry: CandidateGeometryConfig = field(default_factory=CandidateGeometryConfig)
    maximum_data_age_seconds: int = 60
    maximum_spread_bps: float = 15.0
    minimum_liquidity: float = 50_000.0
    minimum_reward_risk: float = 1.0
    minimum_expected_reward_cost_margin: float = 0.25


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
    settings: MetaStrategySettings | None = None,
) -> GeneratedDeterministicCandidate:
    active_settings = settings or build_meta_strategy_settings(
        settings_version=snapshot.settings_version,
        status="ACTIVE",
    )
    generation_config = config or _candidate_generation_config(active_settings)
    directional_outputs = evaluate_registry_group(snapshot, DIRECTIONAL_STRATEGIES, settings=active_settings)
    context_outputs = evaluate_registry_group(snapshot, CONTEXT_STRATEGIES, settings=active_settings)
    active_context_outputs = tuple(
        output
        for output, entry in zip(context_outputs, CONTEXT_STRATEGIES, strict=True)
        if entry.enabled
    )
    regime_outputs = evaluate_registry_group(snapshot, REGIME_STRATEGIES, settings=active_settings)
    safety_outputs = evaluate_registry_group(snapshot, SAFETY_STRATEGIES, settings=active_settings)
    safety_blockers = tuple(output for output in safety_outputs if bool((output.evidence or {}).get("blocksNewEntries")))
    aggregation = aggregate_family_scores(
        _directional_contributions(directional_outputs, DIRECTIONAL_STRATEGIES, active_context_outputs, regime_outputs),
        config=generation_config.aggregation,
    )
    safety_blocks = generation_config.block_new_entries_on_safety_failure and bool(safety_blockers)
    provisional_direction: Direction = "HOLD" if safety_blocks else aggregation.signal
    provisional_eligible = aggregation.eligible and not safety_blocks
    base_reason_codes = tuple(
        code
        for code in (
            *aggregation.reason_codes,
            *(code for output in safety_blockers for code in output.reason_codes),
            "meta_strategy.candidate.safety_blocked" if safety_blocks else "",
        )
        if code
    )
    supporting_families, opposing_families = _family_alignment(aggregation)
    winning_score, opposing_score = _winning_scores(provisional_direction if provisional_direction != "HOLD" else aggregation.signal, aggregation)
    provisional_edge = round(max(0.0, winning_score - opposing_score) if provisional_direction != "HOLD" else 0.0, 6)
    provisional_confidence = round(aggregation.confidence if provisional_eligible else 0.0, 6)
    family_scores = aggregation.to_deterministic_candidate(
        algorithm_version=snapshot.algorithm_version,
        configuration_version=snapshot.configuration_version,
        strategy_catalog_version=snapshot.strategy_catalog_version,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        timestamp=snapshot.timestamp,
        settings_version=snapshot.settings_version,
        effective_settings_hash=snapshot.effective_settings_hash,
    ).family_scores
    provisional_contract = DeterministicCandidate(
        algorithm_id=snapshot.algorithm_id,
        algorithm_version=snapshot.algorithm_version,
        configuration_version=snapshot.configuration_version,
        strategy_catalog_version=snapshot.strategy_catalog_version,
        settings_version=snapshot.settings_version,
        effective_settings_hash=snapshot.effective_settings_hash,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        timestamp=snapshot.timestamp,
        signal=provisional_direction,
        confidence=provisional_confidence,
        eligible=provisional_eligible,
        family_scores=family_scores,
        reason_codes=base_reason_codes,
    )
    normalized = _normalized_candidate(
        snapshot,
        provisional_contract,
        aggregation=aggregation,
        directional_outputs=directional_outputs,
        context_outputs=context_outputs,
        regime_outputs=regime_outputs,
        supporting_families=supporting_families,
        opposing_families=opposing_families,
        safety_blocks=safety_blocks,
        config=generation_config,
    )
    rejected = bool(normalized["rejected"])
    direction: Direction = "HOLD" if rejected else provisional_direction
    eligible = provisional_eligible and not rejected
    confidence = provisional_confidence if eligible else 0.0
    edge = provisional_edge if eligible else 0.0
    reason_codes = tuple(
        dict.fromkeys(
            (
                *base_reason_codes,
                *tuple(normalized.get("rejectionReasonCodes") or ()),
                "meta_strategy.candidate.generated_without_ml" if eligible else "meta_strategy.candidate.hold_without_ml",
            )
        )
    )
    candidate_contract = provisional_contract.model_copy(update={"signal": direction, "confidence": confidence, "eligible": eligible, "reason_codes": reason_codes})
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
        "normalizedCandidate": normalized | {"direction": direction, "aggregateConfidence": confidence, "reasonCodes": reason_codes},
        "documentedImprovements": (
            "Meta-Strategy candidate generation is package-owned, normalized before ML, applies safety and geometry preflight after deterministic aggregation, and caps correlated family influence.",
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
    *,
    settings: MetaStrategySettings | None = None,
) -> tuple[SnapshotEvaluationResult, ...]:
    active_settings = settings or build_meta_strategy_settings(
        settings_version=snapshot.settings_version,
        status="ACTIVE",
    )
    return tuple(instantiate_meta_strategy(entry, active_settings).evaluate(snapshot) for entry in entries)


def instantiate_meta_strategy(entry: MetaStrategyRegistryEntry, settings: MetaStrategySettings | None = None):
    active_settings = settings or build_meta_strategy_settings(status="ACTIVE")
    module = importlib.import_module(entry.implementation_module)
    strategy_class = getattr(module, entry.implementation_class)
    if str(entry.role) == "DIRECTIONAL":
        strategy_settings = active_settings.directional_strategies[entry.strategy_id]
    elif str(entry.role) == "CONTEXT":
        strategy_settings = active_settings.context_strategies[entry.strategy_id]
    elif str(entry.role) == "REGIME":
        strategy_settings = active_settings.regime_classification[entry.strategy_id]
    else:
        strategy_settings = active_settings.safety_gates[entry.strategy_id]
    return strategy_class(
        strategy_settings,
        settings_version=active_settings.settings_version,
        effective_settings_hash=active_settings.effective_settings_hash,
    )


def _instantiate(entry: MetaStrategyRegistryEntry):
    return instantiate_meta_strategy(entry)


def _candidate_generation_config(settings: MetaStrategySettings) -> CandidateGenerationConfig:
    aggregation = settings.candidate_aggregation
    correlation = settings.correlation_controls
    return CandidateGenerationConfig(
        aggregation=FamilyAggregationConfig(
            strategy_contribution_cap=correlation.strategy_contribution_cap,
            family_contribution_cap=correlation.family_contribution_cap,
            correlation_group_cap=correlation.correlation_group_cap,
            minimum_active_strategies=aggregation.minimum_active_strategies,
            minimum_independent_families=aggregation.minimum_independent_families,
            maximum_abstention_rate=aggregation.maximum_abstention_rate,
            minimum_conflict_edge=aggregation.minimum_conflict_edge,
        ),
        block_new_entries_on_safety_failure=aggregation.block_new_entries_on_safety_failure,
        geometry=CandidateGeometryConfig(
            atr_stop_multiplier=settings.entry_exit_management.stop_multiplier,
            target_reward_risk=settings.entry_exit_management.target_multiplier,
            base_maximum_holding_minutes=settings.entry_exit_management.maximum_holding_minutes,
            minimum_expected_net_reward_risk=settings.local_risk.risk_percentage * 0.0 + 1.0,
        ),
        maximum_spread_bps=settings.local_risk.spread_limit_bps,
        minimum_liquidity=settings.local_risk.liquidity_requirement,
        minimum_reward_risk=max(1.0, settings.entry_exit_management.target_multiplier / max(settings.entry_exit_management.stop_multiplier, 1e-9) * 0.5),
    )


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
                orderable=entry in ACTIVE_DIRECTIONAL_STRATEGIES,
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
    return entry.correlation_group


def _normalized_candidate(
    snapshot: MetaStrategyMarketSnapshot,
    candidate: DeterministicCandidate,
    *,
    aggregation: FamilyAggregationResult,
    directional_outputs: tuple[SnapshotEvaluationResult, ...],
    context_outputs: tuple[SnapshotEvaluationResult, ...],
    regime_outputs: tuple[SnapshotEvaluationResult, ...],
    supporting_families: tuple[str, ...],
    opposing_families: tuple[str, ...],
    safety_blocks: bool,
    config: CandidateGenerationConfig,
) -> dict[str, Any]:
    supporting_strategy_ids = _supporting_strategy_ids(aggregation, candidate.signal)
    conflicting_strategy_ids = _conflicting_strategy_ids(aggregation, candidate.signal)
    context_adjustments = _context_adjustments(context_outputs)
    regime_compatibility = _regime_compatibility(regime_outputs, supporting_families)
    geometry_result: CandidateGeometryResult | None = None
    geometry_error: str | None = None
    if candidate.eligible and candidate.signal in {"BUY", "SELL"}:
        try:
            geometry_result = calculate_candidate_geometry(snapshot, candidate, config=config.geometry)
        except CandidateGeometryValidationError as exc:
            geometry_error = str(exc)

    rejection_codes = list(
        _candidate_rejection_codes(
            snapshot,
            candidate,
            aggregation=aggregation,
            geometry=geometry_result,
            geometry_error=geometry_error,
            regime_compatible=bool(regime_compatibility["compatible"]),
            safety_blocks=safety_blocks,
            config=config,
        )
    )
    rejected = bool(rejection_codes)
    side = candidate.signal if not rejected and candidate.signal in {"BUY", "SELL"} else "HOLD"
    return {
        "direction": side,
        "aggregateConfidence": candidate.confidence if not rejected else 0.0,
        "supportingStrategyIds": supporting_strategy_ids,
        "supportingIndependentFamilies": supporting_families,
        "conflictingStrategies": conflicting_strategy_ids,
        "regimeCompatibility": regime_compatibility,
        "contextAdjustments": context_adjustments,
        "entryReference": _round_or_none(geometry_result.entry_reference if geometry_result else _evidence_reference(directional_outputs, candidate.signal, "entryReference")),
        "stopReference": _round_or_none(geometry_result.geometry.stop_price if geometry_result else _evidence_reference(directional_outputs, candidate.signal, "suggestedStopReference")),
        "targetReference": _round_or_none(geometry_result.geometry.target_price if geometry_result else None),
        "expectedHoldingPeriod": int(geometry_result.maximum_holding_minutes if geometry_result else 0),
        "estimatedSpread": {"basisPoints": _round_or_none(snapshot.spread_bps), "dollars": _round_or_none((snapshot.spread or {}).get("dollars"))},
        "estimatedSlippage": _round_or_none(_estimated_slippage(snapshot, config)),
        "estimatedTransactionCost": _round_or_none(geometry_result.estimated_cost if geometry_result else None),
        "expectedRewardToRisk": _round_or_none(geometry_result.geometry.risk_reward if geometry_result else None),
        "expectedNetRewardToRisk": _round_or_none(geometry_result.expected_net_reward_risk if geometry_result else None),
        "expectedRewardAfterCosts": _round_or_none(_expected_reward_after_costs(geometry_result)),
        "rejected": rejected,
        "rejectionReasonCodes": tuple(rejection_codes),
        "geometryReasonCodes": geometry_result.reason_codes if geometry_result else (),
        "mlInvoked": False,
    }


def _candidate_rejection_codes(
    snapshot: MetaStrategyMarketSnapshot,
    candidate: DeterministicCandidate,
    *,
    aggregation: FamilyAggregationResult,
    geometry: CandidateGeometryResult | None,
    geometry_error: str | None,
    regime_compatible: bool,
    safety_blocks: bool,
    config: CandidateGenerationConfig,
) -> tuple[str, ...]:
    codes: list[str] = []
    if safety_blocks:
        codes.append("meta_strategy.candidate.reject.safety_gates_failed")
    if not aggregation.eligible:
        codes.append("meta_strategy.candidate.reject.independent_evidence_insufficient")
    if "meta_strategy.aggregation.buy_sell_conflict" in aggregation.reason_codes:
        codes.append("meta_strategy.candidate.reject.conflict_excessive")
    if not _data_fresh(snapshot, config.maximum_data_age_seconds):
        codes.append("meta_strategy.candidate.reject.data_stale")
    spread = float(snapshot.spread_bps if snapshot.spread_bps is not None else (snapshot.spread or {}).get("basisPoints") or 0.0)
    if spread > config.maximum_spread_bps:
        codes.append("meta_strategy.candidate.reject.spread_unacceptable")
    if _liquidity(snapshot) < config.minimum_liquidity:
        codes.append("meta_strategy.candidate.reject.liquidity_unacceptable")
    if candidate.signal in {"BUY", "SELL"} and geometry is None:
        codes.extend(_split_reason_codes(geometry_error) or ("meta_strategy.candidate.reject.geometry_unavailable",))
    if geometry is not None:
        if geometry.stop_distance <= 0.0:
            codes.append("meta_strategy.candidate.reject.invalid_stop_distance")
        if (geometry.geometry.risk_reward or 0.0) < config.minimum_reward_risk:
            codes.append("meta_strategy.candidate.reject.reward_to_risk_below_minimum")
        reward_after_costs = _expected_reward_after_costs(geometry)
        if reward_after_costs is None or reward_after_costs <= config.minimum_expected_reward_cost_margin:
            codes.append("meta_strategy.candidate.reject.expected_reward_below_cost_margin")
    if not regime_compatible and candidate.signal in {"BUY", "SELL"}:
        codes.append("meta_strategy.candidate.reject.regime_incompatible")
    if _conflicting_exposure(snapshot, candidate.signal):
        codes.append("meta_strategy.candidate.reject.conflicting_exposure")
    if _duplicate_or_cooldown(snapshot):
        codes.append("meta_strategy.candidate.reject.duplicate_or_cooldown")
    return tuple(dict.fromkeys(codes))


def _supporting_strategy_ids(aggregation: FamilyAggregationResult, direction: Direction) -> tuple[str, ...]:
    return tuple(
        item.strategy_id
        for item in aggregation.contribution_audit
        if item.counted and item.signal == direction and item.capped_contribution > 0.0
    )


def _split_reason_codes(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip() for code in str(raw).split(";") if code.strip())


def _conflicting_strategy_ids(aggregation: FamilyAggregationResult, direction: Direction) -> tuple[str, ...]:
    opposite = "SELL" if direction == "BUY" else "BUY" if direction == "SELL" else ""
    return tuple(
        item.strategy_id
        for item in aggregation.contribution_audit
        if item.counted and item.signal == opposite and item.capped_contribution > 0.0
    )


def _context_adjustments(outputs: tuple[SnapshotEvaluationResult, ...]) -> dict[str, Any]:
    return {
        output.strategy_id: {
            "eligible": output.eligible,
            "confidenceAdjustment": float((output.evidence or {}).get("confidenceAdjustment") or 0.0),
            "riskAdjustment": float((output.evidence or {}).get("riskAdjustment") or 0.0),
            "familyWeightMultiplier": float((output.evidence or {}).get("familyWeightMultiplier") or 1.0),
            "reasonCodes": output.reason_codes,
        }
        for output in outputs
    }


def _regime_compatibility(outputs: tuple[SnapshotEvaluationResult, ...], supporting_families: tuple[str, ...]) -> dict[str, Any]:
    labels = tuple(str((output.evidence or {}).get("regimeLabel") or "UNKNOWN") for output in outputs)
    fits: dict[str, float] = {}
    for output in outputs:
        strategy_fit = (output.evidence or {}).get("strategyFit") or {}
        if isinstance(strategy_fit, dict):
            fits.update({str(key): float(value) for key, value in strategy_fit.items()})
    family_thresholds = {family: fits.get(family, 1.0) for family in supporting_families}
    compatible = all(output.eligible for output in outputs) and all(value > 0.0 for value in family_thresholds.values())
    return {"compatible": compatible, "labels": labels, "familyFit": family_thresholds, "allFamilyFit": fits}


def _evidence_reference(outputs: tuple[SnapshotEvaluationResult, ...], direction: Direction, key: str) -> float | None:
    for output in outputs:
        if output.signal == direction and output.evidence and output.evidence.get(key) is not None:
            try:
                return float(output.evidence[key])
            except (TypeError, ValueError):
                return None
    return None


def _data_fresh(snapshot: MetaStrategyMarketSnapshot, maximum_age_seconds: int) -> bool:
    timestamps = [snapshot.source_cutoff_timestamp]
    quote = snapshot.quote or {}
    quote_timestamp = quote.get("timestamp") if isinstance(quote, dict) else None
    if quote_timestamp:
        try:
            timestamps.append(datetime.fromisoformat(str(quote_timestamp).replace("Z", "+00:00")))
        except ValueError:
            return False
    present = [value for value in timestamps if value is not None]
    if not present:
        return False
    return all(value.tzinfo is not None and (snapshot.timestamp - value).total_seconds() <= maximum_age_seconds for value in present)


def _liquidity(snapshot: MetaStrategyMarketSnapshot) -> float:
    return float((snapshot.liquidity or {}).get("dollarVolume") or (snapshot.liquidity or {}).get("shareVolume") or snapshot.volume or 0.0)


def _conflicting_exposure(snapshot: MetaStrategyMarketSnapshot, direction: Direction) -> bool:
    state = snapshot.features.get("existingPositionState") or snapshot.features.get("algorithmExposureState") or {}
    if not isinstance(state, dict):
        return False
    if not bool(state.get("policyAllowsEntry", True)):
        return True
    side = str(state.get("side") or state.get("direction") or "").upper()
    return (direction == "BUY" and side in {"SHORT", "SELL"}) or (direction == "SELL" and side in {"LONG", "BUY"})


def _duplicate_or_cooldown(snapshot: MetaStrategyMarketSnapshot) -> bool:
    duplicate = snapshot.features.get("duplicateOrderState") or {}
    cooldown = snapshot.features.get("cooldownState") or snapshot.features.get("lastSignalCooldownState") or {}
    duplicate_active = isinstance(duplicate, dict) and bool(duplicate.get("duplicate") or duplicate.get("isDuplicate"))
    cooldown_active = isinstance(cooldown, dict) and bool(cooldown.get("active") or cooldown.get("cooldownActive"))
    return duplicate_active or cooldown_active


def _estimated_slippage(snapshot: MetaStrategyMarketSnapshot, config: CandidateGenerationConfig) -> float:
    return float(snapshot.last_price) * float(config.geometry.slippage_bps) / 10_000.0


def _expected_reward_after_costs(geometry: CandidateGeometryResult | None) -> float | None:
    if geometry is None:
        return None
    return round(float(geometry.target_distance) - float(geometry.estimated_cost), 6)


def _round_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


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
        "contributionAudit": {
            item.strategy_id: {
                "family": item.family,
                "signal": item.signal,
                "confidence": item.confidence,
                "weight": item.weight,
                "rawContribution": item.raw_contribution,
                "strategyCappedContribution": item.strategy_capped_contribution,
                "cappedContribution": item.capped_contribution,
                "canonicalInfluenceId": item.canonical_influence_id,
                "correlationKey": item.correlation_key,
                "eligible": item.eligible,
                "orderable": item.orderable,
                "counted": item.counted,
                "capsApplied": item.caps_applied,
                "reasonCodes": item.reason_codes,
            }
            for item in aggregation.contribution_audit
        },
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
    "instantiate_meta_strategy",
]
