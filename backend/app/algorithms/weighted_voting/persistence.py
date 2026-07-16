"""Persistence boundary for authoritative Weighted Voting state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol

from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings


WEIGHTED_VOTING_PERSISTENCE_VERSION = "weighted_voting_persistence_v3"
WEIGHTED_VOTING_ALGORITHM_ID = "weighted_voting"
WEIGHTED_VOTING_SETTINGS_KEY = "weighted_voting.settings.effective"
WEIGHTED_VOTING_STORAGE_ROOT = Path("data") / "algorithms" / "weighted_voting"
WEIGHTED_VOTING_ARTIFACT_CATEGORIES = frozenset(
    {
        "configurations",
        "settings",
        "active_weights",
        "historical_weights",
        "strategy_outcomes",
        "strategy_statistics",
        "decisions",
        "order_proposals",
        "gate_results",
        "positions",
        "trades",
        "backtest_runs",
        "walk_forward_folds",
        "equity_curves",
        "daily_update_status",
        "snapshots",
    }
)


class WeightedVotingStateStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


@dataclass(frozen=True)
class WeightedVotingArtifactMetadata:
    artifact_id: str
    category: str
    run_id: str
    algorithm_id: str
    algorithm_version: str
    data_hash: str
    config_hash: str
    weight_version: str
    created_at: datetime
    payload_hash: str


class WeightedVotingFilesystemStateStore:
    """Filesystem-backed store rooted at data/algorithms/weighted_voting."""

    def __init__(
        self,
        *,
        root: Path | str = WEIGHTED_VOTING_STORAGE_ROOT,
        writer_algorithm_id: str = WEIGHTED_VOTING_ALGORITHM_ID,
        algorithm_version: str = WEIGHTED_VOTING_PERSISTENCE_VERSION,
    ) -> None:
        self.root = Path(root)
        self.writer_algorithm_id = writer_algorithm_id
        self.algorithm_version = algorithm_version

    def read_snapshot(self, key: str) -> dict:
        envelope = self.read_artifact("snapshots", key)
        return dict(envelope["payload"])

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.write_artifact(
            "snapshots",
            key,
            snapshot,
            run_id=_value(snapshot, "run_id", key),
            data_hash=_value(snapshot, "data_hash", _hash_payload(snapshot)),
            config_hash=_value(snapshot, "configuration_hash", _value(snapshot, "config_hash", "")),
            weight_version=_value(snapshot, "weight_version", ""),
        )

    def write_artifact(
        self,
        category: str,
        artifact_id: str,
        payload: dict,
        *,
        run_id: str,
        data_hash: str,
        config_hash: str,
        weight_version: str,
        created_at: datetime | None = None,
    ) -> WeightedVotingArtifactMetadata:
        self._validate_writer()
        self._validate_category(category)
        payload_json = _json_ready(payload)
        payload_hash = _hash_payload(payload_json)
        metadata = WeightedVotingArtifactMetadata(
            artifact_id=artifact_id,
            category=category,
            run_id=run_id,
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            algorithm_version=self.algorithm_version,
            data_hash=data_hash,
            config_hash=config_hash,
            weight_version=weight_version,
            created_at=created_at or datetime.now(timezone.utc),
            payload_hash=payload_hash,
        )
        envelope = {
            "metadata": _json_ready(metadata.__dict__),
            "payload": payload_json,
        }
        path = self._artifact_path(category, artifact_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":"), indent=2), encoding="utf-8")
        return metadata

    def read_artifact(self, category: str, artifact_id: str) -> dict:
        self._validate_category(category)
        path = self._artifact_path(category, artifact_id)
        if not path.exists():
            raise KeyError(f"Weighted Voting artifact not found: {category}/{artifact_id}")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        metadata = envelope.get("metadata", {})
        payload = envelope.get("payload", {})
        if metadata.get("algorithm_id") != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("artifact does not belong to Weighted Voting")
        if metadata.get("payload_hash") != _hash_payload(payload):
            raise ValueError("Weighted Voting artifact payload hash mismatch")
        return envelope

    def artifact_path(self, category: str, artifact_id: str) -> Path:
        return self._artifact_path(category, artifact_id)

    def _artifact_path(self, category: str, artifact_id: str) -> Path:
        safe_category = self._safe_component(category)
        safe_id = self._safe_component(artifact_id)
        root = self.root.resolve()
        path = (root / safe_category / f"{safe_id}.json").resolve()
        if root != path and root not in path.parents:
            raise ValueError("Weighted Voting artifact path escaped storage root")
        return path

    def _validate_writer(self) -> None:
        if self.writer_algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise PermissionError("only Weighted Voting may write to the Weighted Voting artifact store")

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in WEIGHTED_VOTING_ARTIFACT_CATEGORIES:
            raise ValueError(f"unsupported Weighted Voting artifact category: {category}")

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("invalid Weighted Voting artifact identifier")
        return cleaned


def persist_authoritative_artifact(
    store: WeightedVotingFilesystemStateStore,
    *,
    category: str,
    artifact_id: str,
    payload: dict,
    run_id: str,
    data_hash: str,
    config_hash: str,
    weight_version: str,
    created_at: datetime | None = None,
) -> WeightedVotingArtifactMetadata:
    return store.write_artifact(
        category,
        artifact_id,
        payload,
        run_id=run_id,
        data_hash=data_hash,
        config_hash=config_hash,
        weight_version=weight_version,
        created_at=created_at,
    )


def persist_effective_settings(
    store: WeightedVotingStateStore,
    settings: WeightedEffectiveSettings,
    *,
    key: str = WEIGHTED_VOTING_SETTINGS_KEY,
) -> None:
    store.write_snapshot(key, settings.model_dump(mode="json"))


def load_effective_settings(
    store: WeightedVotingStateStore,
    *,
    key: str = WEIGHTED_VOTING_SETTINGS_KEY,
) -> WeightedEffectiveSettings:
    return WeightedEffectiveSettings.model_validate(store.read_snapshot(key))


def _value(payload: dict, key: str, default: str) -> str:
    value = payload.get(key, default)
    return "" if value is None else str(value)


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value):
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
