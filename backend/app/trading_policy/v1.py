from __future__ import annotations

from typing import Any


def dynamic_risk_config(settings_payload: dict[str, Any]) -> dict[str, Any]:
    from backend.app import main

    return main.dynamic_risk_config(settings_payload)


def risk_config_hash(config: dict[str, Any]) -> str:
    from backend.app import main

    return main.risk_config_hash(config)

