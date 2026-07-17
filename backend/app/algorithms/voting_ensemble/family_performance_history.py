from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


VOTING_ENSEMBLE_FAMILY_PERFORMANCE_HISTORY_VERSION = "voting_ensemble_family_performance_history_v1"


def family_performance_history_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_FAMILY_PERFORMANCE_HISTORY_VERSION,
        "voting_ensemble.family_performance_history.windowed_outcomes",
        "voting_ensemble.family_performance_history.family_scoped",
        "voting_ensemble.family_performance_history.neutral_fallback",
    )


@dataclass(frozen=True)
class VotingEnsembleFamilyPerformanceObservation:
    family: str
    regimeKey: str
    outcomeR: float
    supportDirection: int
    observedAt: datetime
    source: str = "walk_forward"


@dataclass(frozen=True)
class VotingEnsembleFamilyPerformanceWindow:
    family: str
    regimeKey: str
    sampleSize: int
    averageOutcomeR: float
    winRate: float
    performanceScore: float
    windowStart: datetime | None
    windowEnd: datetime | None
    version: str
    reasonCodes: tuple[str, ...]


class VotingEnsembleFamilyPerformanceHistory:
    def window_for(
        self,
        *,
        observations: list[VotingEnsembleFamilyPerformanceObservation] | list[dict[str, Any]],
        family: str,
        regime_key: str,
    ) -> VotingEnsembleFamilyPerformanceWindow:
        normalized = [
            observation if isinstance(observation, VotingEnsembleFamilyPerformanceObservation) else VotingEnsembleFamilyPerformanceObservation(**observation)
            for observation in observations
        ]
        family_key = family.lower()
        matches = [item for item in normalized if item.family.lower() == family_key and item.regimeKey == regime_key]
        if not matches:
            return VotingEnsembleFamilyPerformanceWindow(
                family=family_key,
                regimeKey=regime_key,
                sampleSize=0,
                averageOutcomeR=0.0,
                winRate=0.0,
                performanceScore=0.5,
                windowStart=None,
                windowEnd=None,
                version=VOTING_ENSEMBLE_FAMILY_PERFORMANCE_HISTORY_VERSION,
                reasonCodes=("voting_ensemble.family_performance_history.no_history_neutral_fallback",),
            )
        wins = sum(1 for item in matches if item.outcomeR > 0)
        average_outcome = sum(float(item.outcomeR) for item in matches) / len(matches)
        win_rate = wins / len(matches)
        score = _clamp01(0.5 + (average_outcome * 0.20) + ((win_rate - 0.5) * 0.30))
        return VotingEnsembleFamilyPerformanceWindow(
            family=family_key,
            regimeKey=regime_key,
            sampleSize=len(matches),
            averageOutcomeR=round(average_outcome, 6),
            winRate=round(win_rate, 6),
            performanceScore=round(score, 6),
            windowStart=min(item.observedAt for item in matches),
            windowEnd=max(item.observedAt for item in matches),
            version=VOTING_ENSEMBLE_FAMILY_PERFORMANCE_HISTORY_VERSION,
            reasonCodes=family_performance_history_reason_codes(),
        )


def family_performance_history_payload(window: VotingEnsembleFamilyPerformanceWindow) -> dict[str, Any]:
    return {
        "version": window.version,
        "family": window.family,
        "regimeKey": window.regimeKey,
        "sampleSize": window.sampleSize,
        "averageOutcomeR": window.averageOutcomeR,
        "winRate": window.winRate,
        "performanceScore": window.performanceScore,
        "window": {
            "start": window.windowStart.isoformat() if window.windowStart else None,
            "end": window.windowEnd.isoformat() if window.windowEnd else None,
        },
        "reasonCodes": list(window.reasonCodes),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

