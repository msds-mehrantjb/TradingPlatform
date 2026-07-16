from __future__ import annotations

from backend.app.domain.models import BaselineTradingSettings, TradeCandidate


def baseline_risk_dollars(
    *,
    account_equity: float,
    settings: BaselineTradingSettings,
    candidate: TradeCandidate,
) -> float:
    return max(0.0, float(account_equity)) * (max(0.0, settings.baseRiskPercent) / 100.0)


def baseline_target_r(settings: BaselineTradingSettings) -> float:
    return max(0.01, settings.baseTargetR)


def baseline_holding_minutes(settings: BaselineTradingSettings) -> int:
    return max(1, int(settings.baseMaximumHoldingMinutes))
