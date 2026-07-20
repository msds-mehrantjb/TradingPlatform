"""Walk-forward helpers for point-in-time model artifact selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class MetaStrategyArtifactTimeline:
    artifacts: tuple[dict[str, Any], ...] = ()

    def artifact_for(self, decision_timestamp: datetime) -> dict[str, Any] | None:
        return select_point_in_time_artifact(self.artifacts, decision_timestamp)


def select_point_in_time_artifact(artifacts: tuple[dict[str, Any], ...], decision_timestamp: datetime) -> dict[str, Any] | None:
    decision_time = decision_timestamp.astimezone(UTC)
    eligible = []
    for artifact in artifacts:
        timestamp = _artifact_available_at(artifact)
        if timestamp is None:
            continue
        if timestamp <= decision_time:
            eligible.append((timestamp, artifact))
    if not eligible:
        return None
    return max(eligible, key=lambda item: item[0])[1]


def _artifact_available_at(artifact: dict[str, Any]) -> datetime | None:
    for key in ("availableAt", "approvedAt", "createdAt", "artifactCreatedAt", "trainedAt"):
        value = artifact.get(key)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    window = artifact.get("trainingWindow") or {}
    if isinstance(window, dict):
        return _parse_datetime(window.get("end"))
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "MetaStrategyArtifactTimeline",
    "select_point_in_time_artifact",
]
