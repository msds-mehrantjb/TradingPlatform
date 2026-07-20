"""Embargo policy helpers for Meta-Strategy training."""

from __future__ import annotations

from datetime import datetime, timedelta


def embargo_cutoff(validation_start: datetime, embargo_minutes: int) -> datetime:
    return validation_start - timedelta(minutes=max(0, int(embargo_minutes)))


def row_is_before_embargo(row_timestamp: datetime, validation_start: datetime, embargo_minutes: int) -> bool:
    return row_timestamp < embargo_cutoff(validation_start, embargo_minutes)


__all__ = ["embargo_cutoff", "row_is_before_embargo"]
