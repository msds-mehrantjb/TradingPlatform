from __future__ import annotations

from datetime import UTC, datetime
from math import tanh
from typing import Any

from backend.app.algorithms.voting_ensemble.reliability.models import (
    VOTING_ENSEMBLE_RELIABILITY_VERSION,
    ReliabilitySampleWindow,
    StrategyReliabilityEstimate,
    VotingEnsembleReliabilityConfig,
    VotingEnsembleReliabilityObservation,
)
from backend.app.domain.models import OperatingMode, Signal


class VotingEnsembleReliabilityEstimator:
    version = VOTING_ENSEMBLE_RELIABILITY_VERSION

    def __init__(self, config: VotingEnsembleReliabilityConfig | None = None) -> None:
        self.config = config or VotingEnsembleReliabilityConfig()

    def estimate_one(
        self,
        *,
        observations: list[VotingEnsembleReliabilityObservation] | list[dict[str, Any]],
        strategy_id: str,
        direction: Signal,
        regime: str,
        session_segment: str,
        volatility_state: str,
        sample_window: ReliabilitySampleWindow | None,
        evaluation_timestamp: datetime,
        mode: OperatingMode | None = None,
    ) -> StrategyReliabilityEstimate:
        evaluation_at = _utc(evaluation_timestamp)
        resolved_window = sample_window or self.config.sampleWindow
        resolved_mode = mode or self.config.mode
        normalized = _normalize_observations(observations)
        matching = [
            observation
            for observation in normalized
            if observation.strategyId == strategy_id
            and observation.direction == direction.value
            and observation.regime == regime
            and observation.sessionSegment == session_segment
            and observation.volatilityState == volatility_state
            and observation.sampleWindow == resolved_window
            and observation.completedAt < evaluation_at
        ]
        matching.sort(key=lambda observation: observation.completedAt)
        matching = matching[-_window_size(resolved_window) :]
        if not matching:
            return self._neutral(
                strategy_id=strategy_id,
                direction=direction,
                regime=regime,
                session_segment=session_segment,
                volatility_state=volatility_state,
                sample_window=resolved_window,
                mode=resolved_mode,
                reason_codes=["voting_ensemble.reliability.no_point_in_time_history_neutral_fallback"],
            )

        weights = _recency_weights(len(matching), self.config.recencyHalfLifeSamples)
        effective_sample_size = sum(weights)
        if len(matching) < self.config.minimumSampleSize or effective_sample_size < self.config.minimumEffectiveSampleSize:
            return self._neutral(
                strategy_id=strategy_id,
                direction=direction,
                regime=regime,
                session_segment=session_segment,
                volatility_state=volatility_state,
                sample_window=resolved_window,
                mode=resolved_mode,
                reason_codes=["voting_ensemble.reliability.insufficient_effective_sample_neutral_fallback"],
                sample_size=len(matching),
                effective_sample_size=effective_sample_size,
                source_window_start=matching[0].completedAt,
                source_window_end=matching[-1].completedAt,
            )

        weighted_net = sum((row.outcomeR - row.transactionCostR) * weight for row, weight in zip(matching, weights, strict=True)) / effective_sample_size
        weighted_win_rate = sum((1.0 if row.outcomeR - row.transactionCostR > 0 else 0.0) * weight for row, weight in zip(matching, weights, strict=True)) / effective_sample_size
        raw = self.config.neutralReliability + (0.18 * tanh(weighted_net)) + (0.14 * (weighted_win_rate - 0.5))
        reliability = _clamp(raw, self.config.minimumReliability, self.config.maximumReliability)
        applied = reliability if resolved_mode == OperatingMode.ACTIVE else self.config.neutralReliability
        return StrategyReliabilityEstimate(
            strategyId=strategy_id,
            direction=direction,
            regime=regime,
            sessionSegment=session_segment,
            volatilityState=volatility_state,
            sampleWindow=resolved_window,
            reliability=round(reliability, 4),
            appliedReliability=round(applied, 4),
            neutralReliability=self.config.neutralReliability,
            sampleSize=len(matching),
            effectiveSampleSize=round(effective_sample_size, 4),
            sourceWindowStart=matching[0].completedAt,
            sourceWindowEnd=matching[-1].completedAt,
            mode=resolved_mode,
            reliabilityVersion=self.version,
            configurationHash=self.config.configurationHash,
            components={
                "weightedNetOutcomeR": round(weighted_net, 4),
                "weightedWinRate": round(weighted_win_rate, 4),
                "recencyWeightTotal": round(effective_sample_size, 4),
            },
            reasonCodes=[
                "voting_ensemble.reliability.point_in_time_history",
                "voting_ensemble.reliability.strategy_direction_regime_session_volatility_window_scoped",
                f"voting_ensemble.reliability.mode:{resolved_mode.value}",
            ],
            explanation=(
                f"Voting Ensemble reliability for {strategy_id} uses {len(matching)} prior completed "
                f"{direction.value} observation(s) before {evaluation_at.isoformat()}."
            ),
        )

    def estimate_for_signals(
        self,
        *,
        observations: list[VotingEnsembleReliabilityObservation] | list[dict[str, Any]],
        strategy_ids: list[str],
        direction: Signal,
        regime: str,
        session_segment: str,
        volatility_state: str,
        sample_window: ReliabilitySampleWindow | None,
        evaluation_timestamp: datetime,
        mode: OperatingMode | None = None,
    ) -> dict[str, StrategyReliabilityEstimate]:
        return {
            strategy_id: self.estimate_one(
                observations=observations,
                strategy_id=strategy_id,
                direction=direction,
                regime=regime,
                session_segment=session_segment,
                volatility_state=volatility_state,
                sample_window=sample_window,
                evaluation_timestamp=evaluation_timestamp,
                mode=mode,
            )
            for strategy_id in strategy_ids
        }

    def _neutral(
        self,
        *,
        strategy_id: str,
        direction: Signal,
        regime: str,
        session_segment: str,
        volatility_state: str,
        sample_window: ReliabilitySampleWindow,
        mode: OperatingMode,
        reason_codes: list[str],
        sample_size: int = 0,
        effective_sample_size: float = 0.0,
        source_window_start: datetime | None = None,
        source_window_end: datetime | None = None,
    ) -> StrategyReliabilityEstimate:
        return StrategyReliabilityEstimate(
            strategyId=strategy_id,
            direction=direction,
            regime=regime,
            sessionSegment=session_segment,
            volatilityState=volatility_state,
            sampleWindow=sample_window,
            reliability=self.config.neutralReliability,
            appliedReliability=self.config.neutralReliability,
            neutralReliability=self.config.neutralReliability,
            sampleSize=sample_size,
            effectiveSampleSize=round(effective_sample_size, 4),
            sourceWindowStart=source_window_start,
            sourceWindowEnd=source_window_end,
            mode=mode,
            reliabilityVersion=self.version,
            configurationHash=self.config.configurationHash,
            components={"weightedNetOutcomeR": 0.0, "weightedWinRate": 0.0, "recencyWeightTotal": round(effective_sample_size, 4)},
            reasonCodes=reason_codes,
            explanation="Voting Ensemble reliability falls back to neutral because point-in-time evidence is insufficient.",
        )


def _normalize_observations(observations: list[VotingEnsembleReliabilityObservation] | list[dict[str, Any]]) -> list[VotingEnsembleReliabilityObservation]:
    normalized: list[VotingEnsembleReliabilityObservation] = []
    for observation in observations:
        if isinstance(observation, VotingEnsembleReliabilityObservation):
            normalized.append(observation)
        elif isinstance(observation, dict) and observation.get("algorithmId", "voting_ensemble") == "voting_ensemble":
            normalized.append(VotingEnsembleReliabilityObservation.model_validate(observation))
    return normalized


def _recency_weights(count: int, half_life_samples: float) -> list[float]:
    return [0.5 ** ((count - index - 1) / half_life_samples) for index in range(count)]


def _window_size(sample_window: ReliabilitySampleWindow) -> int:
    return {
        "rolling_20_trades": 20,
        "rolling_60_trades": 60,
        "rolling_120_trades": 120,
    }[sample_window]


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise ValueError("timestamp must be timezone-aware UTC")
    return timestamp.astimezone(UTC)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
