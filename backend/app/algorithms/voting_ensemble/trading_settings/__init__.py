"""Authoritative one-minute trading settings subsystem for Voting Ensemble."""

from backend.app.algorithms.voting_ensemble.trading_settings.baseline import (
    VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
    one_minute_baseline_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.editable import (
    EDITABLE_ONE_MINUTE_FIELDS,
    EDITABLE_SETTINGS_CATALOG_VERSION,
    INERT_ONE_MINUTE_PARAMETERS,
    EditableTradingSettingField,
    editable_field,
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
    effective_override_config,
    resolve_one_minute_trading_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.store import (
    VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION,
    VotingEnsembleTradingSettingsRepository,
    merged_settings_payload,
    override_validation_errors,
    stored_trading_settings_overrides,
    trading_settings_repository,
)
from backend.app.algorithms.voting_ensemble.trading_settings.view import trading_settings_view
from backend.app.algorithms.voting_ensemble.trading_settings.validation import validate_one_minute_settings


__all__ = [
    "EDITABLE_ONE_MINUTE_FIELDS",
    "EDITABLE_SETTINGS_CATALOG_VERSION",
    "INERT_ONE_MINUTE_PARAMETERS",
    "EditableTradingSettingField",
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
    "VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION",
    "VotingEnsembleTradingSettingsRepository",
    "editable_field",
    "effective_override_config",
    "merged_settings_payload",
    "override_validation_errors",
    "stored_trading_settings_overrides",
    "trading_settings_hash",
    "trading_settings_repository",
    "trading_settings_view",
    "validate_one_minute_settings",
]
