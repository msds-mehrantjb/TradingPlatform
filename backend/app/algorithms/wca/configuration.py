"""WCA configuration contracts and defaults."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.algorithms.wca.contracts import WcaBaselineSettings, WcaEffectiveSettings, WcaEvaluationStatus, WcaMarketStatus
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile


WCA_CONFIGURATION_VERSION = "wca_legacy_configuration_v1"


def validate_baseline_settings(settings: WcaBaselineSettings | dict[str, object]) -> WcaBaselineSettings:
    return WcaBaselineSettings.model_validate(settings)


def default_baseline_settings() -> WcaBaselineSettings:
    return validate_baseline_settings({})


def default_effective_settings() -> WcaEffectiveSettings:
    baseline = default_baseline_settings()
    profile = resolve_dynamic_profile(
        baseline=baseline,
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
        calculation_timestamp=datetime.now(timezone.utc),
        config=WcaDynamicProfileConfig(enabled=False),
    )
    return profile.effective_settings
