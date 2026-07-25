"""Validation helpers for one-minute Voting Ensemble settings."""

from __future__ import annotations

import re

from backend.app.algorithms.voting_ensemble.trading_settings.models import VotingEnsembleOneMinuteSettings


TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
FORBIDDEN_ONE_MINUTE_RUNTIME_KEYS = {
    "1Hour",
    "1Day",
    "1Week",
    "hybridOneHour",
    "swing",
    "openCloseEvents",
    "directionalWinnerMinVotesByTimeframe",
}


def validate_one_minute_settings(settings: VotingEnsembleOneMinuteSettings) -> VotingEnsembleOneMinuteSettings:
    _validate_time_order(settings.sessionWindows.sessionStart, settings.sessionWindows.newTradesUntil, settings.sessionWindows.forceClose)
    if settings.riskPerTrade.riskPerTradePercent > 100.0:
        raise ValueError("risk per trade percent cannot exceed 100")
    if settings.riskPerTrade.riskBudgetPercentOfOrder > 100.0:
        raise ValueError("risk budget percent of order cannot exceed 100")
    if settings.dailyLossCap.maxDailyLossPercent > 100.0:
        raise ValueError("daily loss percent cannot exceed 100")
    if settings.positionNotionalCap.orderAllocationPercent > 100.0:
        raise ValueError("order allocation percent cannot exceed 100")
    if settings.positionNotionalCap.dailyAllocationPercent > 100.0:
        raise ValueError("daily allocation percent cannot exceed 100")
    if settings.positionNotionalCap.orderAllocationPercent > settings.positionNotionalCap.dailyAllocationPercent:
        raise ValueError("order allocation cannot exceed daily allocation")
    if settings.maximumTrades.maxTradesPerDay > 0 and settings.positionNotionalCap.dailyAllocationPercent < settings.positionNotionalCap.orderAllocationPercent:
        raise ValueError("daily allocation must support at least one order")
    if settings.slippageLimits.slippagePerShare > settings.slippageLimits.maxSlippagePerShare:
        raise ValueError("baseline slippage per share exceeds max slippage limit")
    if settings.stopPolicy.fixedStopDistanceDollars < settings.stopPolicy.minimumStopDistanceDollars:
        raise ValueError("fixed stop distance is below minimum stop distance")
    return settings


def reject_forbidden_runtime_keys(payload: dict) -> None:
    serialized_keys = _flatten_keys(payload)
    forbidden = sorted(key for key in serialized_keys if key in FORBIDDEN_ONE_MINUTE_RUNTIME_KEYS)
    if forbidden:
        raise ValueError(f"one-minute Voting Ensemble settings cannot include non-one-minute keys: {', '.join(forbidden)}")


def _validate_time_order(session_start: str, new_trades_until: str, force_close: str) -> None:
    for value in (session_start, new_trades_until, force_close):
        if not TIME_PATTERN.match(value):
            raise ValueError(f"invalid HH:MM session time: {value}")
    if not (session_start < new_trades_until < force_close):
        raise ValueError("sessionStart must be before newTradesUntil before forceClose")


def _flatten_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(str(key) for key in value)
        for inner in value.values():
            keys.update(_flatten_keys(inner))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for inner in value:
            keys.update(_flatten_keys(inner))
        return keys
    return set()

