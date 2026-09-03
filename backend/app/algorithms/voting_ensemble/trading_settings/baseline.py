"""Authoritative one-minute Voting Ensemble baseline settings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION = "voting_ensemble_baseline_settings_v1"


ONE_MINUTE_BASELINE_SETTINGS: dict[str, Any] = {
    "startingCapital": 25000.0,
    "riskPerTradePercent": 0.5,
    "maxDailyLossPercent": 2.0,
    # No fixed trade count. The day's activity is bounded by the daily-loss limit above
    # and by the drawdown and exposure caps, which is what a count was standing in for.
    # A positive value restores a hard cap.
    "maxTradesPerDay": 0,
    "sessionStart": "09:35",
    "newTradesUntil": "15:30",
    "forceClose": "15:55",
    "execution": "next candle open",
    "stopLossPercent": 0.35,
    "fixedStopDistanceDollars": 1.0,
    "takeProfitR": 1.5,
    "slippagePerShare": 0.02,
    "expenseModel": {
        "description": "Estimated SPY share expenses: adverse slippage is priced into entry/exit, plus extra liquidity reserve and sell-side regulatory fee estimates.",
        "additionalLiquidityCostPerSharePerSide": 0.01,
        "commissionPerSharePerSide": 0.0,
        "secFeeRateOnSellNotional": 0.0000278,
        "finraTafPerSellShare": 0.000166,
        "finraTafMaxPerTrade": 8.30,
    },
    "positionSizing": "shares = risk dollars / stop distance, capped by available capital",
    "entryConfirmationBars": 3,
    "warmupBars": 50,
    # Entries are bounded by sessionStart/newTradesUntil, not by an hour allow-list.
    # Kept as the full session so this value cannot silently narrow entries if a future
    # gate starts consulting it.
    "allowedEntryHours": ("09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"),
    "orderAllocationPercent": 10.0,
    "dailyAllocationPercent": 30.0,
    "riskBudgetPercentOfOrder": 50.0,
    "positionSizingMode": "allocation",
    "maximumPositionPercent": 50.0,
    "maxShareQuantity": 1000,
    "maximumSpreadBps": 25.0,
    "maximumSpreadDollars": 0.25,
    "maxSlippagePerShare": 1.0,
    "minimumStopDistanceDollars": 0.01,
    "maximumHoldingMinutes": 30,
    "minEligibleDirectionalVotes": 1,
    "minWinningVotes": 1,
    "minVoteEdge": 0.20,
    "holdBand": 0.0,
    "reliabilityWeightingMode": "active",
    "reliabilitySampleWindow": "rolling_60_trades",
    "minimumFamiliesForTrade": 1,
    "familyWeights": {
        "trend": 1.0,
        "breakout": 1.0,
        "reversal": 1.0,
        "mean_reversion": 1.0,
        "gap_session": 1.0,
    },
    "maxContextBoost": 0.20,
    "maxContextPenalty": 0.20,
    "maxPrimaryFeedAgeSeconds": 75,
    "maxAuxiliaryFeedAgeSeconds": 180,
    "maxDecisionLatencyMs": 1500,
    "maxQueueLatencyMs": 5000,
    "commandDeadlineSeconds": 30,
    "minimumNetEdgeR": 0.0,
    "minimumEdgeToCostRatio": 1.0,
    "maxConcurrentPositions": 1,
    "cancelUnfilledAfterSeconds": 60,
    "limitOrderOffsetBps": 0.0,
    "cooldownSeconds": 0,
    "maxReplacementAttempts": 1,
    "minimumRiskMultiplier": 0.0,
    "minimumAllocationMultiplier": 0.0,
    "maximumSlippageMultiplier": 2.0,
}


def one_minute_baseline_settings() -> dict[str, Any]:
    return deepcopy(ONE_MINUTE_BASELINE_SETTINGS)
