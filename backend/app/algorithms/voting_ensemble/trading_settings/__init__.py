"""Authoritative one-minute trading settings subsystem for Voting Ensemble."""

from backend.app.algorithms.voting_ensemble.trading_settings.baseline import (
    VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
    one_minute_baseline_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.hashing import trading_settings_hash
from backend.app.algorithms.voting_ensemble.trading_settings.legacy import legacy_multi_timeframe_compatibility_config
from backend.app.algorithms.voting_ensemble.trading_settings.models import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION,
    VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION,
    VotingEnsembleOneMinuteSettings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.profiles import resolve_dynamic_trading_profile
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import (
    apply_dynamic_trading_profile,
    dynamic_risk_config,
    resolve_one_minute_trading_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.validation import validate_one_minute_settings


__all__ = [
    "VOTING_ENSEMBLE_ALGORITHM_ID",
    "VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION",
    "VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION",
    "VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION",
    "VotingEnsembleOneMinuteSettings",
    "apply_dynamic_trading_profile",
    "dynamic_risk_config",
    "legacy_multi_timeframe_compatibility_config",
    "one_minute_baseline_settings",
    "resolve_dynamic_trading_profile",
    "resolve_one_minute_trading_settings",
    "trading_settings_hash",
    "validate_one_minute_settings",
]
