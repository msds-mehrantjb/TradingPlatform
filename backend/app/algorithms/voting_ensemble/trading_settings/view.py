"""The operator-facing view of the one-minute trading settings.

One payload answers the three questions the dashboard has to render: what may be edited,
what the value currently is, and whether that value is the baseline or something the
operator set. It also carries the parameters that are deliberately *not* editable, with
the reason, so an operator looking for a control that is not there finds out why instead
of assuming it was forgotten.
"""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.trading_settings.baseline import (
    VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
    one_minute_baseline_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.editable import (
    EDITABLE_SETTINGS_CATALOG_VERSION,
    GROUP_DESCRIPTIONS,
    GROUP_LABELS,
    INERT_ONE_MINUTE_PARAMETERS,
    editable_field_groups,
    editable_fields_for_group,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import (
    effective_override_config,
    resolve_one_minute_trading_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.store import baseline_override_defaults


def trading_settings_view(record: dict[str, Any]) -> dict[str, Any]:
    """Build the editor payload from a stored override record."""
    overrides = dict(record.get("overrides") or {})
    baseline_values = baseline_override_defaults()
    resolved = resolve_one_minute_trading_settings(overrides)
    effective = _effective_values(overrides, baseline_values)
    baseline_resolved = resolve_one_minute_trading_settings({})

    groups = []
    for group in editable_field_groups():
        fields = []
        for spec in editable_fields_for_group(group):
            fields.append(
                {
                    **spec.describe(),
                    "baseline": baseline_values.get(spec.key),
                    "value": effective.get(spec.key),
                    "overridden": spec.key in overrides,
                }
            )
        groups.append(
            {
                "id": group,
                "label": GROUP_LABELS[group],
                "description": GROUP_DESCRIPTIONS[group],
                "fields": fields,
            }
        )

    baseline_config = one_minute_baseline_settings()
    return {
        "algorithmId": "voting_ensemble",
        "catalogVersion": EDITABLE_SETTINGS_CATALOG_VERSION,
        "settingsVersion": resolved.settingsVersion,
        "profileVersion": resolved.profileVersion,
        "baselineVersion": VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
        "configurationHash": resolved.configurationHash,
        "baselineConfigurationHash": baseline_resolved.configurationHash,
        "matchesBaseline": resolved.configurationHash == baseline_resolved.configurationHash,
        "groups": groups,
        "overrides": overrides,
        "overriddenKeys": sorted(overrides),
        "ignoredKeys": list(record.get("ignoredKeys") or []),
        "updatedAt": record.get("updatedAt") or "",
        "updatedBy": record.get("updatedBy") or "",
        "reason": record.get("reason") or "",
        "paperOnly": resolved.paperExecutionMode.paperOnly,
        "liveTradingEnabled": resolved.paperExecutionMode.liveTradingEnabled,
        "positionSizing": resolved.positionSizing,
        "inertParameters": [
            {
                "key": item.key,
                "label": item.label,
                "why": item.why,
                "baseline": baseline_config.get(item.key),
            }
            for item in INERT_ONE_MINUTE_PARAMETERS
        ],
        "resolved": resolved.model_dump(mode="json"),
        "reasonCodes": [
            "voting_ensemble.trading_settings.view_ready",
            *list(record.get("reasonCodes") or []),
        ],
    }


def _effective_values(overrides: dict[str, Any], baseline_values: dict[str, Any]) -> dict[str, Any]:
    """What each field resolves to once the overrides are applied.

    Read back through the resolver rather than trusting the raw override, so the number
    shown is the one that trades: out-of-range values are clamped and `maxTradesPerDay`
    is capped by what the daily allocation can fund.
    """
    config = effective_override_config(overrides)
    values: dict[str, Any] = {}
    for key, fallback in baseline_values.items():
        cursor: Any = config
        for segment in key.split("."):
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(segment)
        values[key] = fallback if cursor is None else cursor
    return values
