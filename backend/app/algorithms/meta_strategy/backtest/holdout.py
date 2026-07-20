"""Holdout-window validation for Meta-Strategy backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MetaStrategyHoldoutWindow:
    start: datetime
    end: datetime


def validate_holdout_window(window: MetaStrategyHoldoutWindow) -> MetaStrategyHoldoutWindow:
    if window.start.tzinfo is None or window.start.utcoffset() is None:
        raise ValueError("holdout start must be timezone-aware")
    if window.end.tzinfo is None or window.end.utcoffset() is None:
        raise ValueError("holdout end must be timezone-aware")
    if window.end <= window.start:
        raise ValueError("holdout end must be after start")
    return window


__all__ = ["MetaStrategyHoldoutWindow", "validate_holdout_window"]
