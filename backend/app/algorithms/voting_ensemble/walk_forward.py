from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

from pydantic import Field

from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter
from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.domain.models import DomainModel


VOTING_ENSEMBLE_WALK_FORWARD_VERSION = "voting_ensemble_walk_forward_v1"


def walk_forward_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_WALK_FORWARD_VERSION,
        "voting_ensemble.walk_forward.chronological_folds",
        "voting_ensemble.walk_forward.train_before_validation",
        "voting_ensemble.walk_forward.test_after_validation",
        "voting_ensemble.walk_forward.dedicated_backtest_adapter",
    )


class VotingEnsembleWalkForwardConfig(DomainModel):
    runId: str = Field(default="voting-ensemble-walk-forward", min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    trainSessions: int = Field(default=20, ge=1)
    validationSessions: int = Field(default=5, ge=1)
    testSessions: int = Field(default=5, ge=1)
    stepSessions: int = Field(default=5, ge=1)
    timeframe: str = Field(default="1Min", min_length=1)
    riskConfigOverride: dict[str, Any] | None = None
    configVersion: str = VOTING_ENSEMBLE_WALK_FORWARD_VERSION


class VotingEnsembleWalkForwardFold(DomainModel):
    foldId: str
    trainStart: date
    trainEnd: date
    validationStart: date
    validationEnd: date
    testStart: date
    testEnd: date
    trainSessionCount: int = Field(ge=1)
    validationSessionCount: int = Field(ge=1)
    testSessionCount: int = Field(ge=1)
    reasonCodes: list[str]


class VotingEnsembleWalkForwardFoldResult(DomainModel):
    foldId: str
    fold: VotingEnsembleWalkForwardFold
    testResult: dict[str, Any]
    calibrationAvailableThrough: date
    validationAvailableThrough: date
    reasonCodes: list[str]


class VotingEnsembleWalkForwardResult(DomainModel):
    runId: str
    walkForwardVersion: str
    configVersion: str
    symbol: str
    foldCount: int = Field(ge=0)
    folds: list[VotingEnsembleWalkForwardFold]
    foldResults: list[VotingEnsembleWalkForwardFoldResult]
    aggregateMetrics: dict[str, Any]
    reproducibilityKey: str
    reasonCodes: list[str]
    explanation: str


@dataclass
class VotingEnsembleWalkForwardEvaluator:
    adapter: VotingEnsembleBacktestingAdapter = VotingEnsembleBacktestingAdapter()

    def evaluate(
        self,
        *,
        candles: list[dict[str, Any] | VotingCandle],
        config: VotingEnsembleWalkForwardConfig | None = None,
    ) -> VotingEnsembleWalkForwardResult:
        effective_config = config or VotingEnsembleWalkForwardConfig()
        ordered = _sort_voting_candles(candles)
        sessions = _sessions_by_date(ordered)
        folds = _folds(sessions, effective_config)
        fold_results: list[VotingEnsembleWalkForwardFoldResult] = []
        for fold in folds:
            test_candles = _candles_for_range(sessions, fold.testStart, fold.testEnd)
            test_result = self.adapter.run_backtest(
                [candle.model_dump(mode="json") for candle in test_candles],
                timeframe=effective_config.timeframe,
                risk_config_override=effective_config.riskConfigOverride,
            )
            fold_results.append(
                VotingEnsembleWalkForwardFoldResult(
                    foldId=fold.foldId,
                    fold=fold,
                    testResult=test_result,
                    calibrationAvailableThrough=fold.trainEnd,
                    validationAvailableThrough=fold.validationEnd,
                    reasonCodes=[
                        "voting_ensemble.walk_forward.test_window_only",
                        "voting_ensemble.walk_forward.calibration_precedes_test",
                    ],
                )
            )

        return VotingEnsembleWalkForwardResult(
            runId=effective_config.runId,
            walkForwardVersion=VOTING_ENSEMBLE_WALK_FORWARD_VERSION,
            configVersion=effective_config.configVersion,
            symbol=effective_config.symbol.upper(),
            foldCount=len(folds),
            folds=folds,
            foldResults=fold_results,
            aggregateMetrics=_aggregate_metrics(fold_results),
            reproducibilityKey=_reproducibility_key(effective_config, folds),
            reasonCodes=list(walk_forward_reason_codes()),
            explanation="Voting Ensemble walk-forward evaluation used chronological train, validation, and unseen test windows, with each test fold routed through the dedicated Voting Ensemble backtesting adapter.",
        )


def run_voting_ensemble_walk_forward(
    candles: list[dict[str, Any] | VotingCandle],
    *,
    config: VotingEnsembleWalkForwardConfig | None = None,
) -> VotingEnsembleWalkForwardResult:
    return VotingEnsembleWalkForwardEvaluator().evaluate(candles=candles, config=config)


def _sort_voting_candles(rows: list[dict[str, Any] | VotingCandle]) -> tuple[VotingCandle, ...]:
    candles = [row if isinstance(row, VotingCandle) else VotingCandle.model_validate(row) for row in rows]
    return tuple(sorted(candles, key=lambda candle: candle.timestamp))


def _sessions_by_date(candles: tuple[VotingCandle, ...]) -> dict[date, list[VotingCandle]]:
    sessions: dict[date, list[VotingCandle]] = {}
    for candle in candles:
        sessions.setdefault(candle.timestamp.date(), []).append(candle)
    return sessions


def _folds(sessions: dict[date, list[VotingCandle]], config: VotingEnsembleWalkForwardConfig) -> list[VotingEnsembleWalkForwardFold]:
    session_dates = sorted(sessions)
    window = config.trainSessions + config.validationSessions + config.testSessions
    folds: list[VotingEnsembleWalkForwardFold] = []
    start = 0
    fold_number = 1
    while start + window <= len(session_dates):
        train = session_dates[start : start + config.trainSessions]
        validation = session_dates[start + config.trainSessions : start + config.trainSessions + config.validationSessions]
        test = session_dates[start + config.trainSessions + config.validationSessions : start + window]
        folds.append(
            VotingEnsembleWalkForwardFold(
                foldId=f"fold-{fold_number}",
                trainStart=train[0],
                trainEnd=train[-1],
                validationStart=validation[0],
                validationEnd=validation[-1],
                testStart=test[0],
                testEnd=test[-1],
                trainSessionCount=len(train),
                validationSessionCount=len(validation),
                testSessionCount=len(test),
                reasonCodes=[
                    "voting_ensemble.walk_forward.no_shuffle",
                    "voting_ensemble.walk_forward.validation_after_train",
                    "voting_ensemble.walk_forward.test_after_validation",
                ],
            )
        )
        fold_number += 1
        start += config.stepSessions
    return folds


def _candles_for_range(sessions: dict[date, list[VotingCandle]], start: date, end: date) -> tuple[VotingCandle, ...]:
    candles: list[VotingCandle] = []
    for session_date in sorted(sessions):
        if start <= session_date <= end:
            candles.extend(sessions[session_date])
    return tuple(sorted(candles, key=lambda candle: candle.timestamp))


def _aggregate_metrics(fold_results: list[VotingEnsembleWalkForwardFoldResult]) -> dict[str, Any]:
    total_trades = sum(int(result.testResult.get("totalTrades") or 0) for result in fold_results)
    total_pnl = round(sum(float(result.testResult.get("totalPnL") or 0.0) for result in fold_results), 2)
    decision_count = sum(int(result.testResult.get("decisionCount") or 0) for result in fold_results)
    return {
        "folds": len(fold_results),
        "totalTrades": total_trades,
        "totalPnL": total_pnl,
        "decisionCount": decision_count,
        "averageTradesPerFold": round(total_trades / len(fold_results), 2) if fold_results else 0.0,
        "averagePnLPerFold": round(total_pnl / len(fold_results), 2) if fold_results else 0.0,
    }


def _reproducibility_key(config: VotingEnsembleWalkForwardConfig, folds: list[VotingEnsembleWalkForwardFold]) -> str:
    payload = {
        "version": VOTING_ENSEMBLE_WALK_FORWARD_VERSION,
        "config": config.model_dump(mode="json"),
        "folds": [fold.model_dump(mode="json") for fold in folds],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:16]

