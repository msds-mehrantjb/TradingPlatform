from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
import math
from typing import Any, Iterable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.domain.models import (
    ContextSignal,
    Direction,
    EnsembleDecision,
    FamilyScore,
    GateStatus,
    GlobalGateDecision,
    OperatingMode,
    RegimeState,
    Signal,
    StrategyFamily,
    StrategyRole,
    StrategySignal,
)
from backend.app.ensemble.reliability import StrategyReliabilityEstimate
from backend.app.algorithms.voting_ensemble.strategies.base import StrategyEvaluationContext
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, directional_strategy_input_ids, resolve_strategy


FAMILY_ORDER: tuple[StrategyFamily, ...] = (
    StrategyFamily.TREND,
    StrategyFamily.BREAKOUT,
    StrategyFamily.REVERSAL,
    StrategyFamily.MEAN_REVERSION,
    StrategyFamily.GAP_SESSION,
)

REGIME_FIT_KEYS = {
    StrategyFamily.TREND.value: "trendFit",
    StrategyFamily.BREAKOUT.value: "breakoutFit",
    StrategyFamily.REVERSAL.value: "reversalFit",
    StrategyFamily.MEAN_REVERSION.value: "meanReversionFit",
    StrategyFamily.GAP_SESSION.value: "gapSessionFit",
}


class DirectionalStrategyRunner(Protocol):
    registryEntry: Any

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        ...


class FamilyAwareEnsembleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "family_aware_deterministic_ensemble_v2"
    minimumFinalScore: float = Field(default=0.20, ge=0, le=1)
    minimumIndependentSupportingFamilies: int = Field(default=2, ge=1)
    minimumFamilyAgreement: float = Field(default=0.10, ge=0, le=1)
    maximumContextConflict: float = Field(default=0.20, ge=0, le=1)
    minimumEligibleDirectionalStrategies: int = Field(default=2, ge=1)
    maxContextAdjustmentPerSignal: float = Field(default=0.08, ge=0, le=0.25)
    reliabilityMode: OperatingMode = OperatingMode.SHADOW
    neutralReliability: float = Field(default=0.50, ge=0, le=1)
    familyWeights: dict[StrategyFamily, float] = Field(default_factory=lambda: {family: 1.0 for family in FAMILY_ORDER})
    enableTrendOverlapControl: bool = True
    sameEventAdditionalStrategyWeight: float = Field(default=0.25, ge=0.0, le=1.0)
    trendCorrelatedEventCap: float = Field(default=0.85, ge=0.0, le=1.0)
    trendDiversityBonusPerRole: float = Field(default=0.06, ge=0.0, le=0.25)
    maximumTrendDiversityBonus: float = Field(default=0.18, ge=0.0, le=0.50)

    @field_validator("familyWeights")
    @classmethod
    def family_weights_must_be_bounded_positive(cls, value: dict[StrategyFamily, float]) -> dict[StrategyFamily, float]:
        normalized = {family: float(value.get(family, value.get(family.value, 1.0))) for family in FAMILY_ORDER}
        for family, weight in normalized.items():
            if not math.isfinite(weight) or weight <= 0.0 or weight > 10.0:
                raise ValueError(f"family weight for {family.value} must be finite, positive, and <= 10")
        return normalized

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class MLFamilyWeightingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "ml_family_weighting_interface_v1"
    mode: OperatingMode = OperatingMode.OFF
    lowerBound: float = Field(default=0.50, ge=0.0)
    upperBound: float = Field(default=1.50, gt=0.0)
    normalizationRule: Literal["mean_one"] = "mean_one"
    minimumSampleRequirement: int = Field(default=500, ge=1)
    requireRegimeSpecificValidation: bool = True
    equalWeightFallback: bool = True
    experimentBaseline: str = "family_aware_deterministic_equal_weights_v1"

    @model_validator(mode="after")
    def bounds_must_be_ordered_and_disabled_by_default(self) -> "MLFamilyWeightingConfig":
        if self.upperBound < self.lowerBound:
            raise ValueError("family multiplier upper bound must be >= lower bound")
        return self


class MLFamilyWeightSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    multipliers: dict[StrategyFamily, float]
    sampleSize: int = Field(ge=0)
    regimeValidationPassed: bool
    testedAgainstBaseline: str = Field(min_length=1)
    reasonCodes: list[str] = Field(default_factory=list)


class FamilyWeightingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    appliedWeights: dict[StrategyFamily, float]
    fallbackWeights: dict[StrategyFamily, float]
    mode: OperatingMode
    reasonCodes: list[str]
    explanation: str
    configurationHash: str


def deterministic_equal_family_weights() -> dict[StrategyFamily, float]:
    return {family: 1.0 for family in FAMILY_ORDER}


def evaluate_ml_family_weight_suggestion(
    suggestion: MLFamilyWeightSuggestion | None,
    config: MLFamilyWeightingConfig | None = None,
) -> FamilyWeightingDecision:
    resolved_config = config or MLFamilyWeightingConfig()
    fallback = deterministic_equal_family_weights()
    reason_codes: list[str] = []
    enabled = resolved_config.mode == OperatingMode.ACTIVE
    if resolved_config.mode == OperatingMode.OFF:
        reason_codes.append("family_weighting.disabled_by_default")
    if suggestion is None:
        reason_codes.append("family_weighting.no_suggestion")
    elif suggestion.testedAgainstBaseline != resolved_config.experimentBaseline:
        reason_codes.append("family_weighting.not_tested_against_fixed_baseline")
    elif suggestion.sampleSize < resolved_config.minimumSampleRequirement:
        reason_codes.append("family_weighting.insufficient_sample_size")
    elif resolved_config.requireRegimeSpecificValidation and not suggestion.regimeValidationPassed:
        reason_codes.append("family_weighting.regime_validation_missing")
    elif not multipliers_are_bounded(suggestion.multipliers, resolved_config):
        reason_codes.append("family_weighting.multiplier_out_of_bounds")
    if reason_codes or not enabled:
        return FamilyWeightingDecision(
            enabled=False,
            appliedWeights=fallback,
            fallbackWeights=fallback,
            mode=resolved_config.mode,
            reasonCodes=reason_codes or ["family_weighting.shadow_only"],
            explanation="ML family weighting is disabled or not validated; equal deterministic family weights remain active.",
            configurationHash=family_weighting_config_hash(resolved_config),
        )
    return FamilyWeightingDecision(
        enabled=True,
        appliedWeights=normalize_family_multipliers(suggestion.multipliers),
        fallbackWeights=fallback,
        mode=resolved_config.mode,
        reasonCodes=["family_weighting.validated_bounded_multipliers"],
        explanation="Validated bounded family multipliers are available for a future separate experiment.",
        configurationHash=family_weighting_config_hash(resolved_config),
    )


def multipliers_are_bounded(multipliers: dict[StrategyFamily, float], config: MLFamilyWeightingConfig) -> bool:
    for family in FAMILY_ORDER:
        weight = float(multipliers.get(family, multipliers.get(family.value, 1.0)))
        if not math.isfinite(weight) or weight < config.lowerBound or weight > config.upperBound:
            return False
    return True


def normalize_family_multipliers(multipliers: dict[StrategyFamily, float]) -> dict[StrategyFamily, float]:
    raw = {family: float(multipliers.get(family, multipliers.get(family.value, 1.0))) for family in FAMILY_ORDER}
    average = sum(raw.values()) / len(raw)
    if average <= 0.0 or not math.isfinite(average):
        return deterministic_equal_family_weights()
    return {family: round(weight / average, 6) for family, weight in raw.items()}


def family_weighting_config_hash(config: MLFamilyWeightingConfig) -> str:
    serialized = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class FamilyAggregate:
    family: StrategyFamily
    value: float
    confidence: float
    reliability: float
    eligibleSignals: list[StrategySignal]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class ControlledSignalContribution:
    signal: StrategySignal
    value: float


class FamilyAwareDeterministicEnsemble:
    engineVersion = "family_aware_deterministic_ensemble_v1"
    registryEntry = resolve_strategy("ensemble_strategy_voting")

    def __init__(self, config: FamilyAwareEnsembleConfig | None = None) -> None:
        self.config = config or FamilyAwareEnsembleConfig()

    def run_directional_strategies(
        self,
        context: StrategyEvaluationContext,
        strategies: Iterable[DirectionalStrategyRunner],
    ) -> list[StrategySignal]:
        strategy_list = list(strategies)
        expected_ids = directional_strategy_input_ids()
        actual_ids = [strategy.registryEntry.strategyId for strategy in strategy_list]
        if len(actual_ids) != len(expected_ids) or set(actual_ids) != set(expected_ids):
            raise ValueError("family-aware V2 ensemble must run the ten registered directional strategies exactly once")
        for strategy in strategy_list:
            entry = strategy.registryEntry
            if entry.collection != StrategyCollection.DIRECTIONAL.value or entry.role != StrategyRole.DIRECTIONAL.value:
                raise ValueError(f"{entry.strategyName} is not a directional ensemble input")
            if entry.strategyId == self.registryEntry.strategyId:
                raise ValueError("aggregator cannot vote for itself")
        return [
            strategy.evaluate(
                StrategyEvaluationContext(
                    registryEntry=strategy.registryEntry,
                    featureSnapshot=context.featureSnapshot,
                    configurationHash=context.configurationHash,
                )
            )
            for strategy in strategy_list
        ]

    def aggregate(
        self,
        *,
        strategySignals: list[StrategySignal],
        contextSignals: list[ContextSignal],
        regimeState: RegimeState | None,
        safetyDecision: GlobalGateDecision | None,
        reliabilityEstimates: dict[str, StrategyReliabilityEstimate] | None = None,
        decidedAt: datetime,
        sessionDate: date,
    ) -> EnsembleDecision:
        decided_at = decidedAt.astimezone(UTC) if decidedAt.tzinfo else decidedAt.replace(tzinfo=UTC)
        self._validate_signal_inputs(strategySignals)
        scored_signals = self._apply_reliability_estimates(strategySignals, reliabilityEstimates or {})
        eligible_signals = self._eligible_directional_signals(scored_signals)
        family_aggregates = self._family_aggregates(eligible_signals, regimeState)
        raw_score = self._weighted_family_mean(family_aggregates)
        candidate_side = _side_for_score(raw_score, self.config.minimumFinalScore)
        context_adjustments = self._context_adjustments(contextSignals, candidate_side)
        context_delta = sum(float(row["adjustment"]) for row in context_adjustments)
        context_conflict = sum(abs(float(row["adjustment"])) for row in context_adjustments if float(row["adjustment"]) < 0)
        final_score = _clamp_signed(raw_score + context_delta)
        supporting_families, opposing_families = self._family_support(family_aggregates, final_score)
        diagnostic_signals = self._signals_with_family_diagnostics(scored_signals, family_aggregates)
        signal, reason_codes = self._decision_signal(
            raw_score=raw_score,
            final_score=final_score,
            eligible_strategy_count=len(eligible_signals),
            supporting_families=supporting_families,
            opposing_families=opposing_families,
            context_conflict=context_conflict,
            safety_decision=safetyDecision,
        )
        confidence = abs(final_score) if signal != Signal.HOLD else max(0.0, 1.0 - abs(final_score))
        buy_confidence = max(final_score, 0.0) if signal == Signal.BUY else 0.0
        sell_confidence = abs(min(final_score, 0.0)) if signal == Signal.SELL else 0.0
        hold_confidence = max(0.0, 1.0 - abs(final_score)) if signal == Signal.HOLD else max(0.0, 1.0 - confidence)
        safety_status = safetyDecision.status if safetyDecision else GateStatus.INFO
        data_ready = bool(
            len(eligible_signals) >= self.config.minimumEligibleDirectionalStrategies
            and (safetyDecision.dataReady if safetyDecision else True)
        )
        return EnsembleDecision(
            decisionId=self._decision_id(decided_at, scored_signals, contextSignals),
            signal=signal,
            direction=_direction_for_signal(signal),
            confidence=round(_clamp01(confidence), 4),
            rawScore=round(raw_score, 4),
            finalScore=round(final_score, 4),
            buyConfidence=round(_clamp01(buy_confidence), 4),
            sellConfidence=round(_clamp01(sell_confidence), 4),
            holdConfidence=round(_clamp01(hold_confidence), 4),
            supportingFamilies=supporting_families,
            opposingFamilies=opposing_families,
            eligibleStrategyCount=len(eligible_signals),
            familyScores=[self._family_score(row) for row in family_aggregates],
            strategySignals=diagnostic_signals,
            contextAdjustments=context_adjustments,
            safetyStatus=safety_status,
            reasonCodes=reason_codes,
            explanation=self._explanation(signal, raw_score, final_score, supporting_families, opposing_families, reason_codes),
            dataReady=data_ready,
            eligible=signal != Signal.HOLD and data_ready and not self._safety_blocks(safetyDecision),
            decidedAt=decided_at,
            sessionDate=sessionDate,
            configurationHash=self.config.configurationHash,
            engineVersion=self.engineVersion,
        )

    def _validate_signal_inputs(self, strategy_signals: list[StrategySignal]) -> None:
        for signal in strategy_signals:
            if signal.role == StrategyRole.AGGREGATOR.value or signal.strategyId == self.registryEntry.strategyId:
                raise ValueError("aggregator cannot vote for itself")
            if signal.role in {StrategyRole.CONTEXT.value, StrategyRole.REGIME.value, StrategyRole.SAFETY.value}:
                raise ValueError(f"{signal.strategyName} is not a directional ensemble vote")

    def _eligible_directional_signals(self, strategy_signals: list[StrategySignal]) -> list[StrategySignal]:
        return [
            signal
            for signal in strategy_signals
            if signal.active
            and signal.eligible
            and signal.dataReady
            and signal.role == StrategyRole.DIRECTIONAL.value
            and signal.family in {family.value for family in FAMILY_ORDER}
            and signal.signal != Signal.HOLD.value
        ]

    def _apply_reliability_estimates(
        self,
        strategy_signals: list[StrategySignal],
        estimates: dict[str, StrategyReliabilityEstimate],
    ) -> list[StrategySignal]:
        if not estimates:
            return strategy_signals
        applied: list[StrategySignal] = []
        for signal in strategy_signals:
            estimate = estimates.get(signal.strategyId)
            if not estimate:
                applied.append(signal)
                continue
            source_window = {
                "sourceWindowStart": estimate.sourceWindowStart.isoformat().replace("+00:00", "Z") if estimate.sourceWindowStart else None,
                "sourceWindowEnd": estimate.sourceWindowEnd.isoformat().replace("+00:00", "Z") if estimate.sourceWindowEnd else None,
                "sampleSize": estimate.sampleSize,
                "effectiveSampleSize": estimate.effectiveSampleSize,
                "version": estimate.reliabilityVersion,
                "configurationHash": estimate.configurationHash,
            }
            features = {
                **signal.features,
                "estimatedReliability": estimate.reliability,
                "reliabilityMode": self.config.reliabilityMode.value,
                "reliabilityReasonCodes": estimate.reasonCodes,
            }
            if self.config.reliabilityMode == OperatingMode.ACTIVE:
                applied.append(
                    signal.model_copy(
                        update={
                            "reliability": estimate.reliability,
                            "reliabilityVersion": estimate.reliabilityVersion,
                            "reliabilitySourceWindow": source_window,
                            "features": features,
                        }
                    )
                )
            elif self.config.reliabilityMode == OperatingMode.FALLBACK:
                applied.append(
                    signal.model_copy(
                        update={
                            "reliability": self.config.neutralReliability,
                            "reliabilityVersion": "family_aware_equal_weight_fallback",
                            "reliabilitySourceWindow": source_window,
                            "features": features,
                        }
                    )
                )
            else:
                applied.append(
                    signal.model_copy(
                        update={
                            "reliabilityVersion": f"{estimate.reliabilityVersion}_{self.config.reliabilityMode.value.lower()}",
                            "reliabilitySourceWindow": source_window,
                            "features": {**features, "shadowReliability": estimate.reliability},
                        }
                    )
                )
        return applied

    def _family_aggregates(self, signals: list[StrategySignal], regime_state: RegimeState | None) -> list[FamilyAggregate]:
        aggregates: list[FamilyAggregate] = []
        for family in FAMILY_ORDER:
            family_signals = [signal for signal in signals if signal.family == family.value]
            if not family_signals:
                continue
            contributions, diagnostics = self._family_contributions(family, family_signals)
            values = [row.value for row in contributions]
            family_regime_fit = _family_regime_fit(regime_state, family)
            value = _clamp_signed((sum(values) / len(values)) * family_regime_fit)
            aggregates.append(
                FamilyAggregate(
                    family=family,
                    value=value,
                    confidence=abs(value),
                    reliability=sum(float(signal.reliability) for signal in family_signals) / len(family_signals),
                    eligibleSignals=[row.signal for row in contributions],
                    diagnostics=diagnostics,
                )
            )
        return aggregates

    def _signals_with_family_diagnostics(
        self,
        scored_signals: list[StrategySignal],
        family_aggregates: list[FamilyAggregate],
    ) -> list[StrategySignal]:
        replacements: dict[str, StrategySignal] = {}
        for aggregate in family_aggregates:
            for signal in aggregate.eligibleSignals:
                if "trendOverlapControl" in signal.features:
                    replacements[signal.strategyId] = signal
        if not replacements:
            return scored_signals
        return [replacements.get(signal.strategyId, signal) for signal in scored_signals]

    def _family_contributions(
        self,
        family: StrategyFamily,
        family_signals: list[StrategySignal],
    ) -> tuple[list[ControlledSignalContribution], dict[str, Any]]:
        if not self.config.enableTrendOverlapControl or family != StrategyFamily.TREND:
            return [ControlledSignalContribution(signal=signal, value=_strategy_value(signal)) for signal in family_signals], {
                "overlapControlApplied": False,
                "reason": "not_trend_family" if family != StrategyFamily.TREND else "disabled",
            }
        grouped: dict[str, list[StrategySignal]] = {}
        for signal in family_signals:
            grouped.setdefault(_event_correlation_id(signal), []).append(signal)

        contributions: list[ControlledSignalContribution] = []
        groups: list[dict[str, Any]] = []
        for event_id, group in grouped.items():
            values = [_strategy_value(signal) for signal in group]
            signs = {1 if value > 0 else -1 if value < 0 else 0 for value in values}
            same_direction = len(signs - {0}) <= 1
            roles = sorted({_trend_evidence_role(signal) for signal in group})
            unique_strategy_count = len({signal.strategyId for signal in group})
            diversity_bonus = min(
                self.config.maximumTrendDiversityBonus,
                max(0, len(roles) - 1) * self.config.trendDiversityBonusPerRole,
            )
            if len(group) == 1 or unique_strategy_count == 1 or not same_direction:
                group_value = sum(values) / len(values)
                adjustment = "duplicate_strategy_deduplicated" if unique_strategy_count == 1 and len(group) > 1 else "none"
            else:
                side = 1 if values[0] > 0 else -1
                magnitudes = sorted((abs(value) for value in values), reverse=True)
                primary = magnitudes[0]
                secondary = sum(magnitudes[1:]) / len(magnitudes[1:]) if len(magnitudes) > 1 else 0.0
                capped_magnitude = min(
                    self.config.trendCorrelatedEventCap,
                    primary + (secondary * self.config.sameEventAdditionalStrategyWeight) + diversity_bonus,
                )
                group_value = side * capped_magnitude
                adjustment = "same_direction_confidence_aggregation"
            leave_one_out = {
                signal.strategyId: round(_leave_one_strategy_out_group_value(signal, group, self.config), 4)
                for signal in group
            }
            groups.append(
                {
                    "eventCorrelationId": event_id,
                    "strategyIds": [signal.strategyId for signal in group],
                    "evidenceRoles": roles,
                    "sameDirection": same_direction,
                    "rawValues": [round(value, 4) for value in values],
                    "groupValue": round(group_value, 4),
                    "trendFamilyVoteCap": self.config.trendCorrelatedEventCap,
                    "diversityBonus": round(diversity_bonus, 4),
                    "adjustment": adjustment,
                    "leaveOneStrategyOutGroupValue": leave_one_out,
                }
            )
            representative = max(group, key=lambda signal: abs(_strategy_value(signal)))
            features = {
                **representative.features,
                "eventCorrelationId": event_id,
                "trendOverlapControl": groups[-1],
            }
            reason_codes = list(dict.fromkeys([*representative.reasonCodes, "ensemble.trend_overlap_control_applied"]))
            contributions.append(
                ControlledSignalContribution(
                    signal=representative.model_copy(update={"features": features, "reasonCodes": reason_codes}),
                    value=_clamp_signed(group_value),
                )
            )
        return contributions, {
            "overlapControlApplied": True,
            "eventGroupCount": len(groups),
            "groups": groups,
            "method": "event_correlation_id_plus_trend_family_vote_cap",
        }

    def _weighted_family_mean(self, aggregates: list[FamilyAggregate]) -> float:
        weighted_sum = 0.0
        weight_total = 0.0
        for aggregate in aggregates:
            weight = float(self.config.familyWeights.get(aggregate.family, 1.0))
            if weight <= 0:
                continue
            weighted_sum += aggregate.value * weight
            weight_total += weight
        return 0.0 if weight_total == 0 else _clamp_signed(weighted_sum / weight_total)

    def _context_adjustments(self, context_signals: list[ContextSignal], candidate_side: Signal) -> list[dict[str, Any]]:
        if candidate_side == Signal.HOLD:
            return [
                {
                    "contextId": signal.contextId,
                    "adjustment": 0.0,
                    "reason": "context_has_no_vote_without_directional_candidate",
                }
                for signal in context_signals
            ]
        adjustments: list[dict[str, Any]] = []
        for signal in context_signals:
            effect = str(signal.features.get("contextEffect") or "neutral").lower()
            if not signal.dataReady:
                adjustments.append({"contextId": signal.contextId, "adjustment": 0.0, "reason": "context_unavailable"})
                continue
            max_adjustment = min(
                self.config.maxContextAdjustmentPerSignal,
                float(signal.features.get("maxConfidenceAdjustment") or self.config.maxContextAdjustmentPerSignal),
            )
            sign = self._context_sign(effect, candidate_side)
            adjustment = sign * max_adjustment * float(signal.confidence)
            adjustments.append(
                {
                    "contextId": signal.contextId,
                    "effect": effect,
                    "adjustment": round(adjustment, 4),
                    "boundedBy": round(max_adjustment, 4),
                    "reason": "bounded_context_effect",
                }
            )
        return adjustments

    def _context_sign(self, effect: str, candidate_side: Signal) -> int:
        if "neutral" in effect:
            return 0
        long_terms = ("long", "bull", "buy")
        short_terms = ("short", "bear", "sell")
        conflict_terms = short_terms if candidate_side == Signal.BUY else long_terms
        confirm_terms = long_terms if candidate_side == Signal.BUY else short_terms
        if any(term in effect for term in conflict_terms) or "conflict" in effect or "veto" in effect or "reduce" in effect:
            return -1
        if any(term in effect for term in confirm_terms) or "confirm" in effect or "strengthen" in effect:
            return 1
        return 0

    def _family_support(self, aggregates: list[FamilyAggregate], final_score: float) -> tuple[list[StrategyFamily], list[StrategyFamily]]:
        if abs(final_score) < self.config.minimumFinalScore:
            return [], []
        side = 1 if final_score > 0 else -1
        supporting = [
            aggregate.family
            for aggregate in aggregates
            if aggregate.value * side >= self.config.minimumFamilyAgreement
        ]
        opposing = [
            aggregate.family
            for aggregate in aggregates
            if aggregate.value * side <= -self.config.minimumFamilyAgreement
        ]
        return supporting, opposing

    def _decision_signal(
        self,
        *,
        raw_score: float,
        final_score: float,
        eligible_strategy_count: int,
        supporting_families: list[StrategyFamily],
        opposing_families: list[StrategyFamily],
        context_conflict: float,
        safety_decision: GlobalGateDecision | None,
    ) -> tuple[Signal, list[str]]:
        reason_codes: list[str] = []
        if eligible_strategy_count < self.config.minimumEligibleDirectionalStrategies:
            reason_codes.append("ensemble.insufficient_eligible_directional_strategies")
        if abs(raw_score) < self.config.minimumFinalScore:
            reason_codes.append("ensemble.weak_raw_score")
        if abs(final_score) < self.config.minimumFinalScore:
            reason_codes.append("ensemble.weak_final_score")
        if len(supporting_families) < self.config.minimumIndependentSupportingFamilies:
            reason_codes.append("ensemble.insufficient_independent_family_support")
        if opposing_families:
            reason_codes.append("ensemble.conflicting_families")
        if context_conflict > self.config.maximumContextConflict:
            reason_codes.append("ensemble.context_conflict_exceeds_limit")
        if self._safety_blocks(safety_decision):
            reason_codes.append("ensemble.safety_blocked_new_entry")
        if reason_codes:
            return Signal.HOLD, reason_codes
        return (Signal.BUY if final_score > 0 else Signal.SELL), ["ensemble.family_aware_candidate"]

    def _safety_blocks(self, safety_decision: GlobalGateDecision | None) -> bool:
        return bool(safety_decision and (not safety_decision.eligible or safety_decision.status == GateStatus.FAIL.value))

    def _family_score(self, aggregate: FamilyAggregate) -> FamilyScore:
        value = aggregate.value
        return FamilyScore(
            family=aggregate.family,
            buyScore=round(max(value, 0.0), 4),
            sellScore=round(abs(min(value, 0.0)), 4),
            holdScore=round(max(0.0, 1.0 - abs(value)), 4),
            confidence=round(abs(value), 4),
            reliability=round(_clamp01(aggregate.reliability), 4),
            explanation=f"{aggregate.family.value} family weighted mean from {len(aggregate.eligibleSignals)} eligible strategy signal(s).",
        )

    def _decision_id(self, decided_at: datetime, strategy_signals: list[StrategySignal], context_signals: list[ContextSignal]) -> str:
        payload = {
            "decidedAt": decided_at.isoformat(),
            "strategyIds": [signal.strategyId for signal in strategy_signals],
            "contextIds": [signal.contextId for signal in context_signals],
            "config": self.config.configurationHash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return f"ensemble-v2-{digest}"

    def _explanation(
        self,
        signal: Signal,
        raw_score: float,
        final_score: float,
        supporting_families: list[StrategyFamily],
        opposing_families: list[StrategyFamily],
        reason_codes: list[str],
    ) -> str:
        support = ", ".join(family.value for family in supporting_families) or "none"
        opposition = ", ".join(family.value for family in opposing_families) or "none"
        return (
            f"{signal.value} from family-aware deterministic ensemble; rawScore={raw_score:.4f}, "
            f"finalScore={final_score:.4f}, supportingFamilies={support}, opposingFamilies={opposition}; "
            f"reasons={', '.join(reason_codes)}."
        )


def _event_correlation_id(signal: StrategySignal) -> str:
    direct = _first_string(
        signal.features,
        (
            "eventCorrelationId",
            "trendEventCorrelationId",
            "correlationId",
            "setupCorrelationId",
        ),
    )
    if direct:
        return direct
    setup_id = _strategy_setup_id(signal)
    if setup_id:
        return f"{signal.family}:{signal.direction}:{setup_id}"
    timestamp = _first_nested_string(
        signal.features,
        (
            ("firstPullbackConfirmationBar", "barEndTimestamp"),
            ("firstPullbackConfirmationBar", "barStartTimestamp"),
            ("vwapContinuation", "confirmationTimestamp"),
            ("vwapContinuation", "pullbackTimestamp"),
            ("multiTimeframeBarEvidence", "roles", "longSetup", "triggerTimestamp"),
            ("multiTimeframeBarEvidence", "roles", "shortSetup", "triggerTimestamp"),
        ),
    )
    if timestamp:
        return f"{signal.family}:{signal.direction}:{timestamp}"
    return f"{signal.strategyId}:{signal.direction}:{signal.evaluatedAt.isoformat()}"


def _strategy_setup_id(signal: StrategySignal) -> str | None:
    if setup_id := _first_string(signal.features, ("setupId", "eventId")):
        return setup_id
    first_pullback_state = signal.features.get("firstPullbackPersistentState")
    if isinstance(first_pullback_state, dict):
        if setup_id := _first_string(first_pullback_state, ("setupId", "eventId")):
            return setup_id
    mtf_evidence = signal.features.get("multiTimeframeBarEvidence")
    if isinstance(mtf_evidence, dict):
        roles = mtf_evidence.get("roles")
        if isinstance(roles, dict):
            for key in ("longSetup", "shortSetup"):
                setup = roles.get(key)
                if isinstance(setup, dict) and setup.get("setupId"):
                    return str(setup["setupId"])
    return None


def _trend_evidence_role(signal: StrategySignal) -> str:
    explicit = _first_string(signal.features, ("trendEvidenceRole", "evidenceRole", "strategyEvidenceRole"))
    if explicit:
        return explicit
    if signal.strategyId == "first_pullback_after_open":
        return "pattern_first_pullback"
    if signal.strategyId == "multi_timeframe_trend_alignment":
        return "timeframe_agreement"
    if signal.strategyId == "vwap_trend_continuation":
        return "anchor_behavior"
    return signal.strategyId


def _leave_one_strategy_out_group_value(
    omitted_signal: StrategySignal,
    group: list[StrategySignal],
    config: FamilyAwareEnsembleConfig,
) -> float:
    remaining = [signal for signal in group if signal.strategyId != omitted_signal.strategyId]
    if not remaining:
        return 0.0
    values = [_strategy_value(signal) for signal in remaining]
    signs = {1 if value > 0 else -1 if value < 0 else 0 for value in values}
    if len(remaining) == 1 or len(signs - {0}) > 1:
        return _clamp_signed(sum(values) / len(values))
    side = 1 if values[0] > 0 else -1
    magnitudes = sorted((abs(value) for value in values), reverse=True)
    primary = magnitudes[0]
    secondary = sum(magnitudes[1:]) / len(magnitudes[1:]) if len(magnitudes) > 1 else 0.0
    roles = sorted({_trend_evidence_role(signal) for signal in remaining})
    diversity_bonus = min(config.maximumTrendDiversityBonus, max(0, len(roles) - 1) * config.trendDiversityBonusPerRole)
    return _clamp_signed(
        side
        * min(
            config.trendCorrelatedEventCap,
            primary + (secondary * config.sameEventAdditionalStrategyWeight) + diversity_bonus,
        )
    )


def _first_string(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _first_nested_string(source: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str | None:
    for path in paths:
        value: Any = source
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value is not None and str(value):
            return str(value)
    return None


def _strategy_value(signal: StrategySignal) -> float:
    return _clamp_signed(
        float(signal.direction)
        * float(signal.confidence)
        * float(signal.reliability)
        * float(signal.regimeFit)
    )


def _family_regime_fit(regime_state: RegimeState | None, family: StrategyFamily) -> float:
    if not regime_state:
        return 1.0
    key = REGIME_FIT_KEYS.get(family.value)
    value = regime_state.features.get(key) if key else None
    return _clamp01(float(value)) if isinstance(value, int | float) else 1.0


def _side_for_score(score: float, threshold: float) -> Signal:
    if score >= threshold:
        return Signal.BUY
    if score <= -threshold:
        return Signal.SELL
    return Signal.HOLD


def _direction_for_signal(signal: Signal) -> Direction:
    if signal == Signal.BUY:
        return Direction.LONG
    if signal == Signal.SELL:
        return Direction.SHORT
    return Direction.FLAT


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clamp_signed(value: float) -> float:
    return max(-1.0, min(1.0, value))
