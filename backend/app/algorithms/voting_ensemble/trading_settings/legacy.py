"""Labelled legacy Voting Ensemble settings not consumed by one-minute runtime."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


LEGACY_MULTI_TIMEFRAME_COMPATIBILITY_CONFIG: dict[str, Any] = {
    "entryConfirmationBarsByTimeframe": {
        "1Min": 3,
        "5Min": 3,
        "1Hour": 2,
    },
    "warmupBarsByTimeframe": {
        "1Min": 50,
        "5Min": 20,
        "1Hour": 2,
        "1Day": 50,
        "1Week": 20,
    },
    "directionalWinnerMinVotesByTimeframe": {
        "1Hour": 2,
        "1Day": 3,
        "1Week": 3,
    },
    "allowedEntryHoursByTimeframe": {
        "1Min": ["10:00", "11:00"],
        "5Min": ["13:00", "14:00"],
        "1Hour": [],
    },
    "hybridOneHour": {
        "label": "1h filter + 5m execution",
        "directionTimeframe": "1Hour",
        "executionTimeframe": "5Min",
        "blockedDirectionHours": ["12:00", "14:00"],
        "blockedRegimes": ["VWAP Chop"],
        "requireDailyTrendAlignment": True,
        "allowedDailySignals": ["Buy"],
        "takeProfitR": 2.0,
        "atrPeriod": 14,
        "atrMultiplier": 0.75,
        "minDirectionalVotes": 2,
    },
    "swing": {
        "1Day": {
            "label": "Daily swing vote",
            "maxHoldingBars": 5,
            "stopPercent": 1.0,
            "atrPeriod": 14,
            "atrMultiplier": 1.5,
            "takeProfitR": 2.0,
        },
        "1Week": {
            "label": "Weekly swing vote",
            "maxHoldingBars": 8,
            "stopPercent": 2.0,
            "atrPeriod": 10,
            "atrMultiplier": 1.0,
            "takeProfitR": 2.5,
        },
    },
    "openCloseEvents": {
        "label": "Opening/Closing Event Ensemble",
        "weeklyFilter": "approved weekly vote",
        "openingWindow": "09:45-10:30",
        "closingWindow": "15:30-15:50",
        "openingRangeMinutes": 15,
        "closingStart": "15:30",
        "closingEnd": "15:50",
        "openingEnd": "10:30",
        "forceClose": "15:55",
        "takeProfitR": 1.5,
        "stopLossPercent": 0.35,
        "fixedStopDistanceDollars": 1.0,
        "maxTradesPerDay": 2,
        "minOpeningWeeklyDirectionalVotes": 3,
        "minClosingWeeklyDirectionalVotes": 4,
        "enableClosingEvents": True,
        "blockedRegimes": ["Mixed"],
    },
}


def legacy_multi_timeframe_compatibility_config() -> dict[str, Any]:
    return deepcopy(LEGACY_MULTI_TIMEFRAME_COMPATIBILITY_CONFIG)

