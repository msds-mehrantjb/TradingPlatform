"""Backward-compatible facade for Voting Ensemble one-minute settings.

Authoritative low-frequency one-minute settings live in
``backend.app.algorithms.voting_ensemble.trading_settings``. Legacy hourly,
daily, weekly, swing, and hybrid fields are retained only as labelled
compatibility data and are not consumed by the one-minute resolver/runtime.
"""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.trading_settings.baseline import (
    VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
    one_minute_baseline_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.hashing import trading_settings_hash
from backend.app.algorithms.voting_ensemble.trading_settings.legacy import legacy_multi_timeframe_compatibility_config
from backend.app.algorithms.voting_ensemble.trading_settings.models import VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION
from backend.app.algorithms.voting_ensemble.trading_settings.profiles import (
    BASELINE_TRADING_PROFILE,
    TRADING_PROFILE_PRESETS,
    TradingProfileOverlay as _TradingProfileOverlay,
    resolve_dynamic_trading_profile,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import (
    apply_dynamic_trading_profile,
    dynamic_risk_config,
    resolve_one_minute_trading_settings,
)


VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION = VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION
VOTING_ENSEMBLE_TRADING_PROFILE_VERSION = VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION
VOTING_ENSEMBLE_RISK_CONFIG: dict[str, Any] = one_minute_baseline_settings()
VOTING_ENSEMBLE_LEGACY_MULTI_TIMEFRAME_CONFIG: dict[str, Any] = legacy_multi_timeframe_compatibility_config()


def risk_config_hash(config: dict[str, Any]) -> str:
    return trading_settings_hash(config)


__all__ = [
    "BASELINE_TRADING_PROFILE",
    "TRADING_PROFILE_PRESETS",
    "VOTING_ENSEMBLE_BASELINE_SETTINGS_VERSION",
    "VOTING_ENSEMBLE_LEGACY_MULTI_TIMEFRAME_CONFIG",
    "VOTING_ENSEMBLE_RISK_CONFIG",
    "VOTING_ENSEMBLE_TRADING_PROFILE_VERSION",
    "_TradingProfileOverlay",
    "apply_dynamic_trading_profile",
    "dynamic_risk_config",
    "resolve_dynamic_trading_profile",
    "resolve_one_minute_trading_settings",
    "risk_config_hash",
]
