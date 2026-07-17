from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


VOTING_ENSEMBLE_RELIABILITY_HISTORY_VERSION = "voting_ensemble_reliability_history_v1"


def reliability_history_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_RELIABILITY_HISTORY_VERSION,
        "voting_ensemble.reliability_history.windowed_outcomes",
        "voting_ensemble.reliability_history.regime_scoped",
        "voting_ensemble.reliability_history.neutral_fallback",
    )


@dataclass(frozen=True)
class VotingEnsembleReliabilityObservation:
    strategyId: str
    regimeKey: str
    outcomeR: float
    confidence: float
    observedAt: datetime
    source: str = "walk_forward"


@dataclass(frozen=True)
class VotingEnsembleReliabilityWindow:
    strategyId: str
    regimeKey: str
    sampleSize: int
    averageOutcomeR: float
    winRate: float
    reliability: float
    windowStart: datetime | None
    windowEnd: datetime | None
    version: str
    reasonCodes: tuple[str, ...]


class VotingEnsembleReliabilityHistory:
    def window_for(
        self,
        *,
        observations: list[VotingEnsembleReliabilityObservation] | list[dict[str, Any]],
        strategy_id: str,
        regime_key: str,
    ) -> VotingEnsembleReliabilityWindow:
        normalized = [
            observation if isinstance(observation, VotingEnsembleReliabilityObservation) else VotingEnsembleReliabilityObservation(**observation)
            for observation in observations
        ]
        matches = [item for item in normalized if item.strategyId == strategy_id and item.regimeKey == regime_key]
        if not matches:
            return VotingEnsembleReliabilityWindow(
                strategyId=strategy_id,
                regimeKey=regime_key,
                sampleSize=0,
                averageOutcomeR=0.0,
                winRate=0.0,
                reliability=0.5,
                windowStart=None,
                windowEnd=None,
                version=VOTING_ENSEMBLE_RELIABILITY_HISTORY_VERSION,
                reasonCodes=("voting_ensemble.reliability_history.no_history_neutral_fallback",),
            )
        wins = sum(1 for item in matches if item.outcomeR > 0)
        average_outcome = sum(float(item.outcomeR) for item in matches) / len(matches)
        win_rate = wins / len(matches)
        reliability = _clamp01(0.5 + (average_outcome * 0.20) + ((win_rate - 0.5) * 0.30))
        return VotingEnsembleReliabilityWindow(
            strategyId=strategy_id,
            regimeKey=regime_key,
            sampleSize=len(matches),
            averageOutcomeR=round(average_outcome, 6),
            winRate=round(win_rate, 6),
            reliability=round(reliability, 6),
            windowStart=min(item.observedAt for item in matches),
            windowEnd=max(item.observedAt for item in matches),
            version=VOTING_ENSEMBLE_RELIABILITY_HISTORY_VERSION,
            reasonCodes=reliability_history_reason_codes(),
        )


def reliability_history_payload(window: VotingEnsembleReliabilityWindow) -> dict[str, Any]:
    return {
        "version": window.version,
        "strategyId": window.strategyId,
        "regimeKey": window.regimeKey,
        "sampleSize": window.sampleSize,
        "averageOutcomeR": window.averageOutcomeR,
        "winRate": window.winRate,
        "reliability": window.reliability,
        "window": {
            "start": window.windowStart.isoformat() if window.windowStart else None,
            "end": window.windowEnd.isoformat() if window.windowEnd else None,
        },
        "reasonCodes": list(window.reasonCodes),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

