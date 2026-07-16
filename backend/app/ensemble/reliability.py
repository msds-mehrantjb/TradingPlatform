from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from math import tanh
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.domain.models import DomainModel, OperatingMode, StrategyFamily, StrategySignal, _require_utc


ReliabilityOutcomeSource = Literal["prior_out_of_sample", "completed_paper_trade"]


class ConservativeReliabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "conservative_strategy_reliability_v1"
    neutralReliability: float = Field(default=0.50, ge=0, le=1)
    lowerBound: float = Field(default=0.35, ge=0, le=1)
    upperBound: float = Field(default=0.75, ge=0, le=1)
    fullWeightSampleSize: int = Field(default=60, ge=1)
    maxLookbackDays: int = Field(default=180, ge=1)
    recencyHalfLifeDays: int = Field(default=30, ge=1)
    expectancyWeight: float = Field(default=0.20, ge=0, le=1)
    regimeWeight: float = Field(default=0.10, ge=0, le=1)
    recentWeight: float = Field(default=0.10, ge=0, le=1)
    drawdownPenaltyWeight: float = Field(default=0.10, ge=0, le=1)
    uncertaintyPenaltyWeight: float = Field(default=0.10, ge=0, le=1)
    allowedSources: tuple[ReliabilityOutcomeSource, ...] = ("prior_out_of_sample", "completed_paper_trade")

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class StrategyReliabilityOutcome(DomainModel):
    strategyId: str = Field(min_length=1)
    family: StrategyFamily
    regimeLabel: str | None = None
    outcomeR: float
    costsR: float = 0.0
    maxDrawdownContributionR: float = Field(default=0.0, ge=0)
    probabilityUncertainty: float = Field(default=0.5, ge=0, le=1)
    decisionTimestamp: datetime
    completedAt: datetime
    source: ReliabilityOutcomeSource

    @field_validator("decisionTimestamp", "completedAt")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class StrategyReliabilityEstimate(DomainModel):
    strategyId: str = Field(min_length=1)
    reliability: float = Field(ge=0, le=1)
    appliedReliability: float = Field(ge=0, le=1)
    neutralReliability: float = Field(ge=0, le=1)
    sampleSize: int = Field(ge=0)
    effectiveSampleSize: float = Field(ge=0)
    sourceWindowStart: datetime | None = None
    sourceWindowEnd: datetime | None = None
    mode: OperatingMode
    reliabilityVersion: str
    configurationHash: str
    components: dict[str, float] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @field_validator("sourceWindowStart", "sourceWindowEnd")
    @classmethod
    def source_window_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class ConservativeStrategyReliabilityEstimator:
    version = "conservative_strategy_reliability_v1"

    def __init__(self, config: ConservativeReliabilityConfig | None = None) -> None:
        self.config = config or ConservativeReliabilityConfig()

    def estimate(
        self,
        *,
        strategyIds: list[str],
        outcomes: list[StrategyReliabilityOutcome] | list[dict[str, Any]],
        decisionTimestamp: datetime,
        currentRegimeLabel: str | None = None,
        mode: OperatingMode = OperatingMode.SHADOW,
    ) -> dict[str, StrategyReliabilityEstimate]:
        decision_at = _require_utc(decisionTimestamp)
        normalized = [
            outcome if isinstance(outcome, StrategyReliabilityOutcome) else StrategyReliabilityOutcome(**outcome)
            for outcome in outcomes
        ]
        return {
            strategy_id: self._estimate_one(
                strategy_id=strategy_id,
                outcomes=normalized,
                decision_at=decision_at,
                current_regime_label=currentRegimeLabel,
                mode=mode,
            )
            for strategy_id in strategyIds
        }

    def apply_to_signals(
        self,
        strategySignals: list[StrategySignal],
        estimates: dict[str, StrategyReliabilityEstimate],
        *,
        mode: OperatingMode,
    ) -> list[StrategySignal]:
        applied: list[StrategySignal] = []
        for signal in strategySignals:
            estimate = estimates.get(signal.strategyId)
            if not estimate:
                applied.append(signal)
                continue
            source_window = _source_window_payload(estimate)
            features = {
                **signal.features,
                "estimatedReliability": estimate.reliability,
                "reliabilityMode": mode.value if isinstance(mode, OperatingMode) else str(mode),
                "reliabilityReasonCodes": estimate.reasonCodes,
            }
            if mode == OperatingMode.ACTIVE:
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
            elif mode == OperatingMode.FALLBACK:
                applied.append(
                    signal.model_copy(
                        update={
                            "reliability": self.config.neutralReliability,
                            "reliabilityVersion": f"{self.version}_equal_weight_fallback",
                            "reliabilitySourceWindow": source_window,
                            "features": features,
                        }
                    )
                )
            else:
                applied.append(
                    signal.model_copy(
                        update={
                            "reliabilityVersion": f"{self.version}_{mode.value.lower()}",
                            "reliabilitySourceWindow": source_window,
                            "features": {**features, "shadowReliability": estimate.reliability},
                        }
                    )
                )
        return applied

    def _estimate_one(
        self,
        *,
        strategy_id: str,
        outcomes: list[StrategyReliabilityOutcome],
        decision_at: datetime,
        current_regime_label: str | None,
        mode: OperatingMode,
    ) -> StrategyReliabilityEstimate:
        lower_bound = min(self.config.lowerBound, self.config.neutralReliability)
        upper_bound = max(self.config.upperBound, self.config.neutralReliability)
        lookback_start = decision_at - timedelta(days=self.config.maxLookbackDays)
        usable = [
            outcome
            for outcome in outcomes
            if outcome.strategyId == strategy_id
            and outcome.source in self.config.allowedSources
            and outcome.completedAt < decision_at
            and outcome.completedAt >= lookback_start
        ]
        if not usable:
            return self._neutral(strategy_id, mode, ["reliability.no_prior_completed_outcomes"])

        net_values = [outcome.outcomeR - outcome.costsR for outcome in usable]
        net_expectancy = sum(net_values) / len(net_values)
        regime_rows = [outcome for outcome in usable if current_regime_label and outcome.regimeLabel == current_regime_label]
        regime_values = [outcome.outcomeR - outcome.costsR for outcome in regime_rows]
        regime_expectancy = sum(regime_values) / len(regime_values) if regime_values else net_expectancy
        recent_expectancy, effective_sample_size = self._recent_expectancy(usable, decision_at)
        drawdown_penalty = sum(outcome.maxDrawdownContributionR for outcome in usable) / len(usable)
        uncertainty_penalty = sum(outcome.probabilityUncertainty for outcome in usable) / len(usable)
        raw_reliability = self.config.neutralReliability
        raw_reliability += self.config.expectancyWeight * tanh(net_expectancy)
        raw_reliability += self.config.regimeWeight * tanh(regime_expectancy)
        raw_reliability += self.config.recentWeight * tanh(recent_expectancy)
        raw_reliability -= self.config.drawdownPenaltyWeight * min(drawdown_penalty, 1.0)
        raw_reliability -= self.config.uncertaintyPenaltyWeight * min(uncertainty_penalty, 1.0)
        shrunken = self.config.neutralReliability + (raw_reliability - self.config.neutralReliability) * self._shrinkage(len(usable))
        reliability = _clamp(shrunken, lower_bound, upper_bound)
        starts = [outcome.completedAt for outcome in usable]
        return StrategyReliabilityEstimate(
            strategyId=strategy_id,
            reliability=round(reliability, 4),
            appliedReliability=round(reliability if mode == OperatingMode.ACTIVE else self.config.neutralReliability, 4),
            neutralReliability=self.config.neutralReliability,
            sampleSize=len(usable),
            effectiveSampleSize=round(effective_sample_size, 4),
            sourceWindowStart=min(starts),
            sourceWindowEnd=max(starts),
            mode=mode,
            reliabilityVersion=self.version,
            configurationHash=self.config.configurationHash,
            components={
                "netExpectancyAfterCosts": round(net_expectancy, 4),
                "regimeSpecificExpectancy": round(regime_expectancy, 4),
                "recentExpectancy": round(recent_expectancy, 4),
                "drawdownPenalty": round(drawdown_penalty, 4),
                "probabilityUncertainty": round(uncertainty_penalty, 4),
                "sampleShrinkage": round(self._shrinkage(len(usable)), 4),
            },
            reasonCodes=["reliability.prior_completed_outcomes_only", f"reliability.mode:{mode.value}"],
            explanation=(
                f"Reliability for {strategy_id} uses {len(usable)} prior completed outcome(s) before "
                f"{decision_at.isoformat()} with shrinkage toward neutral {self.config.neutralReliability:.2f}."
            ),
        )

    def _neutral(self, strategy_id: str, mode: OperatingMode, reason_codes: list[str]) -> StrategyReliabilityEstimate:
        return StrategyReliabilityEstimate(
            strategyId=strategy_id,
            reliability=self.config.neutralReliability,
            appliedReliability=self.config.neutralReliability,
            neutralReliability=self.config.neutralReliability,
            sampleSize=0,
            effectiveSampleSize=0.0,
            sourceWindowStart=None,
            sourceWindowEnd=None,
            mode=mode,
            reliabilityVersion=self.version,
            configurationHash=self.config.configurationHash,
            components={
                "netExpectancyAfterCosts": 0.0,
                "regimeSpecificExpectancy": 0.0,
                "recentExpectancy": 0.0,
                "drawdownPenalty": 0.0,
                "probabilityUncertainty": 0.0,
                "sampleShrinkage": 0.0,
            },
            reasonCodes=reason_codes,
            explanation=f"Reliability for {strategy_id} falls back to neutral because no eligible prior completed outcomes exist.",
        )

    def _recent_expectancy(self, outcomes: list[StrategyReliabilityOutcome], decision_at: datetime) -> tuple[float, float]:
        weighted_sum = 0.0
        weight_total = 0.0
        for outcome in outcomes:
            age_days = max(0.0, (decision_at - outcome.completedAt).total_seconds() / 86400)
            weight = 0.5 ** (age_days / self.config.recencyHalfLifeDays)
            weighted_sum += (outcome.outcomeR - outcome.costsR) * weight
            weight_total += weight
        return (weighted_sum / weight_total if weight_total else 0.0, weight_total)

    def _shrinkage(self, sample_size: int) -> float:
        return sample_size / (sample_size + self.config.fullWeightSampleSize)


def _source_window_payload(estimate: StrategyReliabilityEstimate) -> dict[str, Any]:
    return {
        "sourceWindowStart": estimate.sourceWindowStart.isoformat().replace("+00:00", "Z") if estimate.sourceWindowStart else None,
        "sourceWindowEnd": estimate.sourceWindowEnd.isoformat().replace("+00:00", "Z") if estimate.sourceWindowEnd else None,
        "sampleSize": estimate.sampleSize,
        "effectiveSampleSize": estimate.effectiveSampleSize,
        "version": estimate.reliabilityVersion,
        "configurationHash": estimate.configurationHash,
    }


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
