"""Regime runtime command contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


RegimeRuntimeCommandType = Literal[
    "pause",
    "resume",
    "kill_switch_activate",
    "kill_switch_deactivate",
    "emergency_flatten",
    "disable_strategy",
    "enable_strategy",
    "rotate_settings_version",
    "set_rollout_stage",
    "set_automatic_paper",
    "recovery",
    "backtest_job",
    "daily_reset",
]


@dataclass(frozen=True)
class RegimeRuntimeCommand:
    algorithm_id: Literal["regime"]
    command_type: RegimeRuntimeCommandType
    payload: dict[str, Any]
    actor: str
    created_at: str
    command_id: str = ""

    def __post_init__(self) -> None:
        if self.algorithm_id != "regime":
            raise ValueError("Regime runtime commands require algorithm_id=regime")
        if not self.command_id:
            object.__setattr__(self, "command_id", _command_id(self.command_type, self.payload, self.created_at))

    @classmethod
    def create(cls, command_type: RegimeRuntimeCommandType, payload: dict[str, Any] | None = None, *, actor: str = "api") -> "RegimeRuntimeCommand":
        return cls(
            algorithm_id="regime",
            command_type=command_type,
            payload=dict(payload or {}),
            actor=actor,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "commandType": self.command_type,
            "payload": dict(self.payload),
            "actor": self.actor,
            "createdAt": self.created_at,
            "commandId": self.command_id,
        }


def _command_id(command_type: str, payload: dict[str, Any], created_at: str) -> str:
    encoded = json.dumps({"commandType": command_type, "payload": payload, "createdAt": created_at}, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"regime-command-{digest}"


__all__ = ["RegimeRuntimeCommand", "RegimeRuntimeCommandType"]
