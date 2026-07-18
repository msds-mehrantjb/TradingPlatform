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

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings


WEIGHTED_VOTING_PERSISTENCE_VERSION = "weighted_voting_persistence_v4"
WEIGHTED_VOTING_SETTINGS_KEY = "weighted_voting.settings.effective"
WEIGHTED_VOTING_STORAGE_ROOT = Path("data") / "algorithms" / "weighted_voting"
WEIGHTED_VOTING_REQUIRED_COLLECTIONS = (
    "configurations",
    "dynamic_settings",
    "market_snapshots",
    "strategy_signals",
    "active_weights",
    "weight_history",
    "strategy_outcomes",
    "strategy_statistics",
    "market_condition_statistics",
    "decisions",
    "local_gate_results",
    "sizing_results",
    "order_proposals",
    "global_gate_applications",
    "orders",
    "fills",
    "positions",
    "trades",
    "performance",
    "backtest_runs",
    "walk_forward_folds",
    "equity_curves",
    "daily_updates",
    "observability",
    "migrations",
)
WEIGHTED_VOTING_COLLECTION_ALIASES = {
    "settings": "dynamic_settings",
    "historical_weights": "weight_history",
    "gate_results": "local_gate_results",
    "daily_update_status": "daily_updates",
    "snapshots": "observability",
}
WEIGHTED_VOTING_ARTIFACT_CATEGORIES = frozenset((*WEIGHTED_VOTING_REQUIRED_COLLECTIONS, *WEIGHTED_VOTING_COLLECTION_ALIASES))
WEIGHTED_VOTING_REQUIRED_RECORD_FIELDS = (
    "algorithm_id",
    "record_id",
    "created_at",
    "data_timestamp",
    "configuration_version",
    "settings_version",
    "strategy_version",
    "weight_version",
    "data_hash",
    "configuration_hash",
)


class WeightedVotingStateStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


@dataclass(frozen=True)
class WeightedVotingArtifactMetadata:
    artifact_id: str
    record_id: str
    category: str
    run_id: str
    algorithm_id: str
    algorithm_version: str
    data_hash: str
    config_hash: str
    configuration_hash: str
    configuration_version: str
    settings_version: str
    strategy_version: str
    weight_version: str
    data_timestamp: datetime
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
        category, artifact_id = snapshot_collection_for_key(key)
        envelope = self.read_artifact(category, artifact_id)
        return dict(envelope["payload"])

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        category, artifact_id = snapshot_collection_for_key(key)
        self.write_artifact(
            category,
            artifact_id,
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
        canonical_category = canonical_weighted_voting_collection(category)
        created = created_at or datetime.now(timezone.utc)
        data_timestamp = _datetime_value(payload_json, "data_timestamp", _datetime_value(payload_json, "dataTimestamp", created))
        configuration_hash = _value(payload_json, "configuration_hash", _value(payload_json, "configurationHash", config_hash))
        metadata = WeightedVotingArtifactMetadata(
            artifact_id=artifact_id,
            record_id=artifact_id,
            category=canonical_category,
            run_id=run_id,
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            algorithm_version=self.algorithm_version,
            data_hash=data_hash,
            config_hash=config_hash,
            configuration_hash=configuration_hash,
            configuration_version=_value(payload_json, "configuration_version", _value(payload_json, "configurationVersion", "")),
            settings_version=_value(payload_json, "settings_version", _value(payload_json, "settingsVersion", "")),
            strategy_version=_value(payload_json, "strategy_version", _value(payload_json, "strategyVersion", "")),
            weight_version=weight_version,
            data_timestamp=data_timestamp,
            created_at=created,
            payload_hash=payload_hash,
        )
        envelope = {
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "record_id": artifact_id,
            "created_at": metadata.created_at.isoformat(),
            "data_timestamp": metadata.data_timestamp.isoformat(),
            "configuration_version": metadata.configuration_version,
            "settings_version": metadata.settings_version,
            "strategy_version": metadata.strategy_version,
            "weight_version": metadata.weight_version,
            "data_hash": metadata.data_hash,
            "configuration_hash": metadata.configuration_hash,
            "metadata": _json_ready(metadata.__dict__),
            "payload": payload_json,
        }
        path = self._artifact_path(canonical_category, artifact_id)
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
        if envelope.get("algorithm_id", WEIGHTED_VOTING_ALGORITHM_ID) != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("artifact envelope does not belong to Weighted Voting")
        missing = [field for field in WEIGHTED_VOTING_REQUIRED_RECORD_FIELDS if field not in envelope]
        if missing:
            raise ValueError(f"Weighted Voting artifact missing required record fields: {missing}")
        if metadata.get("payload_hash") != _hash_payload(payload):
            raise ValueError("Weighted Voting artifact payload hash mismatch")
        return envelope

    def artifact_path(self, category: str, artifact_id: str) -> Path:
        return self._artifact_path(category, artifact_id)

    def _artifact_path(self, category: str, artifact_id: str) -> Path:
        safe_category = self._safe_component(canonical_weighted_voting_collection(category))
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
        if category not in WEIGHTED_VOTING_ARTIFACT_CATEGORIES and canonical_weighted_voting_collection(category) not in WEIGHTED_VOTING_REQUIRED_COLLECTIONS:
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


def canonical_weighted_voting_collection(category: str) -> str:
    return WEIGHTED_VOTING_COLLECTION_ALIASES.get(category, category)


def snapshot_collection_for_key(key: str) -> tuple[str, str]:
    if key == WEIGHTED_VOTING_SETTINGS_KEY or key.startswith("weighted_voting.settings."):
        return "dynamic_settings", key
    if key.startswith("weighted_voting.market_snapshots."):
        return "market_snapshots", key
    if key.startswith("weighted_voting.strategy_signals."):
        return "strategy_signals", key
    if key == "weighted_voting.weights.active":
        return "active_weights", key
    if key.startswith("weighted_voting.weights.history") or key.startswith("weighted_voting.weights.published_for_session.") or key.startswith("weighted_voting.weight_history."):
        return "weight_history", key
    if key.startswith("weighted_voting.outcomes.") or key.startswith("weighted_voting.strategy_outcomes."):
        return "strategy_outcomes", key
    if key.startswith("weighted_voting.statistics.") or key.startswith("weighted_voting.strategy_statistics."):
        return "strategy_statistics", key
    if key.startswith("weighted_voting.market_condition_statistics."):
        return "market_condition_statistics", key
    if key.startswith("weighted_voting.decisions."):
        return "decisions", key
    if key.startswith("weighted_voting.local_gate_results.") or key.startswith("weighted_voting.gate_results."):
        return "local_gate_results", key
    if key.startswith("weighted_voting.sizing_results."):
        return "sizing_results", key
    if key.startswith("weighted_voting.order_proposals."):
        return "order_proposals", key
    if key.startswith("weighted_voting.global_gate_applications."):
        return "global_gate_applications", key
    if key.startswith("weighted_voting.execution_gateway.command.") or key.startswith("weighted_voting.execution_gateway.submission.") or key.startswith("weighted_voting.execution_gateway.rejection.") or key.startswith("weighted_voting.position_trade_state.order."):
        return "orders", key
    if key.startswith("weighted_voting.execution_gateway.fill."):
        return "fills", key
    if key.startswith("weighted_voting.execution_gateway.position.") or key.startswith("weighted_voting.position_trade_state.position."):
        return "positions", key
    if key.startswith("weighted_voting.trades."):
        return "trades", key
    if key.startswith("weighted_voting.performance_tracker.") or key.startswith("weighted_voting.performance."):
        return "performance", key
    if key.startswith("weighted_voting.backtests."):
        return "backtest_runs", key
    if key.startswith("weighted_voting.walk_forward."):
        return "walk_forward_folds", key
    if key.startswith("weighted_voting.equity_curves."):
        return "equity_curves", key
    if key.startswith("weighted_voting.daily_update.") or key.startswith("weighted_voting.scheduler.daily_update.") or key.startswith("weighted_voting.scheduler.status.") or key.startswith("weighted_voting.scheduler.audit."):
        return "daily_updates", key
    if key.startswith("weighted_voting.migration."):
        return "migrations", key
    return "observability", key


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


def _datetime_value(payload: dict, key: str, default: datetime) -> datetime:
    value = payload.get(key)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return default


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
