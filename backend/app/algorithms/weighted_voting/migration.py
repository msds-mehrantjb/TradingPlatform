"""One-time safe migration for legacy Weighted Voting state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from backend.app.algorithms.weighted_voting.dynamic_settings import (
    default_dynamic_envelope,
    default_hard_limits,
    migrate_legacy_weighted_settings,
    resolve_effective_settings,
)
from backend.app.algorithms.weighted_voting.models import WeightedSide, WeightedTradeRecord, WeightedWeightState
from backend.app.algorithms.weighted_voting.persistence import WEIGHTED_VOTING_SETTINGS_KEY, WeightedVotingStateStore, persist_effective_settings
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state


WEIGHTED_VOTING_MIGRATION_VERSION = "weighted_voting_state_migration_v1"
WEIGHTED_VOTING_MIGRATION_RECORD_KEY = "weighted_voting.migrations.v2_state"
WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY = "weighted_voting.weights.active"

LEGACY_WEIGHTED_SETTINGS_KEY = "weighted-voting-trading-settings-v1"
LEGACY_WEIGHTED_TARGET_ORDER_OVERRIDES_KEY = "weighted-voting-target-order-overrides-v1"
LEGACY_WEIGHT_STATE_KEY = "weighted-voting-daily-weights-short-cycle-v5"
LEGACY_STRATEGY_PERFORMANCE_KEY = "weighted-strategy-performance-short-cycle-v5"
LEGACY_WEIGHTED_TRADE_HISTORY_KEY = "trading-dashboard.weighted-trade-history.v1"
LEGACY_UI_STATE_KEY = "trading-dashboard-ui-state-v1"
LEGACY_WEIGHTED_ORDER_CONTROL_MODES_KEY = "trading-dashboard.weighted-order-control-modes.v1"
LEGACY_WEIGHTED_ORDER_CONTROL_OVERRIDES_KEY = "trading-dashboard.weighted-order-control-overrides.v1"

NON_WEIGHTED_LEGACY_PREFIXES = (
    "weighted-confidence-",
    "regime-selection-",
    "meta-strategy-",
    "voting-ensemble-",
    "trading-dashboard.confidence-",
    "trading-dashboard.regime-",
    "trading-dashboard.meta-",
    "trading-dashboard.trade-history.v1",
)

WEIGHTED_UI_FIELDS = (
    "weightedVotingExpanded",
    "weightedDataExpanded",
    "weightedGatesExpanded",
    "weightedControlsExpanded",
    "weightedTradingSettingsExpanded",
    "weightedDefaultSizingExpanded",
)


@dataclass(frozen=True)
class WeightedVotingMigrationResult:
    status: str
    migration_version: str
    settings_migrated: bool
    active_weights_initialized: bool
    untrusted_weight_history_archived: bool
    trustworthy_performance_migrated: bool
    trade_records_migrated: int
    ui_preferences_migrated: bool
    reason_codes: tuple[str, ...]


def migrate_existing_weighted_voting_state(
    *,
    store: WeightedVotingStateStore,
    legacy_state: Mapping[str, Any],
    migrated_at: datetime | None = None,
) -> WeightedVotingMigrationResult:
    """Migrate legacy Weighted Voting state into backend-owned snapshots.

    The input is expected to be a browser-storage export or an equivalent mapping.
    Only explicit Weighted Voting keys are read. Other algorithm keys are ignored.
    """

    existing_record = _read_optional(store, WEIGHTED_VOTING_MIGRATION_RECORD_KEY)
    if existing_record and existing_record.get("status") == "completed":
        return _result_from_record(existing_record, status="idempotent_noop")

    timestamp = migrated_at or datetime.now(timezone.utc)
    reason_codes: list[str] = ["weighted_voting.migration.started"]
    settings_migrated = _migrate_settings(store, legacy_state, timestamp, reason_codes)
    untrusted_weight_history_archived, trustworthy_performance_migrated = _migrate_weight_and_performance_history(store, legacy_state, timestamp, reason_codes)
    active_weights_initialized = _ensure_unseeded_active_weights(store, timestamp, reason_codes)
    trade_records_migrated = _migrate_trade_history(store, legacy_state, timestamp, reason_codes)
    ui_preferences_migrated = _migrate_ui_preferences(store, legacy_state, timestamp, reason_codes)

    record = {
        "status": "completed",
        "migration_version": WEIGHTED_VOTING_MIGRATION_VERSION,
        "migrated_at": timestamp.isoformat(),
        "settings_migrated": settings_migrated,
        "active_weights_initialized": active_weights_initialized,
        "untrusted_weight_history_archived": untrusted_weight_history_archived,
        "trustworthy_performance_migrated": trustworthy_performance_migrated,
        "trade_records_migrated": trade_records_migrated,
        "ui_preferences_migrated": ui_preferences_migrated,
        "reason_codes": tuple(dict.fromkeys([*reason_codes, "weighted_voting.migration.completed"])),
    }
    store.write_snapshot(WEIGHTED_VOTING_MIGRATION_RECORD_KEY, record)
    return _result_from_record(record, status="completed")


def _migrate_settings(store: WeightedVotingStateStore, legacy_state: Mapping[str, Any], timestamp: datetime, reason_codes: list[str]) -> bool:
    payload = _legacy_payload(legacy_state, LEGACY_WEIGHTED_SETTINGS_KEY, "weightedTradingSettings", "settings")
    if not isinstance(payload, dict):
        reason_codes.append("weighted_voting.migration.no_legacy_settings")
        return False
    try:
        defaults = migrate_legacy_weighted_settings(payload, timestamp=timestamp, settings_version="weighted_default_settings_migrated_from_legacy_v1")
        effective = resolve_effective_settings(
            default_settings=defaults,
            dynamic_envelope=default_dynamic_envelope(timestamp=timestamp),
            hard_limits=default_hard_limits(timestamp=timestamp),
            configuration_version=WEIGHTED_VOTING_MIGRATION_VERSION,
            timestamp=timestamp,
        )
        persist_effective_settings(store, effective, key=WEIGHTED_VOTING_SETTINGS_KEY)
        reason_codes.append("weighted_voting.migration.settings_promoted")
        return True
    except Exception as exc:
        _archive_untrusted(store, "settings", payload, timestamp, ("weighted_voting.migration.settings_validation_failed", str(exc)))
        reason_codes.append("weighted_voting.migration.settings_validation_failed")
        return False


def _migrate_weight_and_performance_history(
    store: WeightedVotingStateStore,
    legacy_state: Mapping[str, Any],
    timestamp: datetime,
    reason_codes: list[str],
) -> tuple[bool, bool]:
    weight_payload = _legacy_payload(legacy_state, LEGACY_WEIGHT_STATE_KEY, "weightedWeightState")
    performance_payload = _legacy_payload(legacy_state, LEGACY_STRATEGY_PERFORMANCE_KEY, "weightedStrategyPerformance", "performanceHistory")
    archived = False
    trustworthy_migrated = False

    if isinstance(weight_payload, dict):
        if _has_trustworthy_weight_provenance(weight_payload):
            try:
                state = WeightedWeightState.model_validate(_weight_state_contract_payload(weight_payload))
                store.write_snapshot(f"weighted_voting.weights.history.{state.weight_version}", state.model_dump(mode="json"))
                trustworthy_migrated = True
                reason_codes.append("weighted_voting.migration.trustworthy_weight_history_preserved")
            except Exception as exc:
                _archive_untrusted(store, "weight_history", weight_payload, timestamp, ("weighted_voting.migration.weight_history_validation_failed", str(exc)))
                archived = True
                reason_codes.append("weighted_voting.migration.weight_history_untrusted_archived")
        else:
            _archive_untrusted(store, "weight_history", weight_payload, timestamp, ("weighted_voting.migration.weight_history_missing_provenance",))
            archived = True
            reason_codes.append("weighted_voting.migration.weight_history_untrusted_archived")

    if performance_payload:
        _archive_untrusted(store, "performance_history", performance_payload, timestamp, ("weighted_voting.migration.performance_history_missing_provenance",))
        archived = True
        reason_codes.append("weighted_voting.migration.performance_history_untrusted_archived")

    if not archived and not trustworthy_migrated:
        reason_codes.append("weighted_voting.migration.no_legacy_weight_history")
    return archived, trustworthy_migrated


def _ensure_unseeded_active_weights(store: WeightedVotingStateStore, timestamp: datetime, reason_codes: list[str]) -> bool:
    active = _read_optional(store, WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY)
    if active:
        WeightedWeightState.model_validate(active)
        reason_codes.append("weighted_voting.migration.active_weights_already_present")
        return False
    state = create_unseeded_equal_weight_state(timestamp=timestamp, data_timestamp=timestamp)
    store.write_snapshot(WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY, state.model_dump(mode="json"))
    reason_codes.append("weighted_voting.migration.active_weights_initialized_unseeded_equal")
    return True


def _migrate_trade_history(store: WeightedVotingStateStore, legacy_state: Mapping[str, Any], timestamp: datetime, reason_codes: list[str]) -> int:
    payload = _legacy_payload(legacy_state, LEGACY_WEIGHTED_TRADE_HISTORY_KEY, "weightedTradeHistory", "tradeHistory")
    if not isinstance(payload, list):
        reason_codes.append("weighted_voting.migration.no_legacy_trade_history")
        return 0
    migrated: list[dict[str, Any]] = []
    rejected: list[Any] = []
    for index, row in enumerate(payload):
        try:
            record = _trade_record_from_legacy(row, index)
            store.write_snapshot(f"weighted_voting.trades.migrated.{record.trade_id}", record.model_dump(mode="json"))
            migrated.append(record.model_dump(mode="json"))
        except Exception as exc:
            rejected.append({"row": row, "error": str(exc)})
    if migrated:
        store.write_snapshot(
            "weighted_voting.trades.migrated.index",
            {
                "algorithm_id": "weighted_voting",
                "migration_version": WEIGHTED_VOTING_MIGRATION_VERSION,
                "migrated_at": timestamp.isoformat(),
                "trade_ids": [record["trade_id"] for record in migrated],
            },
        )
        reason_codes.append("weighted_voting.migration.trade_history_promoted")
    if rejected:
        _archive_untrusted(store, "rejected_trade_history", rejected, timestamp, ("weighted_voting.migration.trade_history_validation_failed",))
        reason_codes.append("weighted_voting.migration.trade_history_rejections_archived")
    if not migrated and not rejected:
        reason_codes.append("weighted_voting.migration.no_legacy_trade_history")
    return len(migrated)


def _migrate_ui_preferences(store: WeightedVotingStateStore, legacy_state: Mapping[str, Any], timestamp: datetime, reason_codes: list[str]) -> bool:
    ui_state = _legacy_payload(legacy_state, LEGACY_UI_STATE_KEY, "uiState")
    order_modes = _legacy_payload(legacy_state, LEGACY_WEIGHTED_ORDER_CONTROL_MODES_KEY, "weightedOrderControlModes")
    order_overrides = _legacy_payload(legacy_state, LEGACY_WEIGHTED_ORDER_CONTROL_OVERRIDES_KEY, "weightedOrderControlOverrides")
    target_overrides = _legacy_payload(legacy_state, LEGACY_WEIGHTED_TARGET_ORDER_OVERRIDES_KEY, "weightedTargetOrderOverrides")
    preferences: dict[str, Any] = {
        "algorithm_id": "weighted_voting",
        "migration_version": WEIGHTED_VOTING_MIGRATION_VERSION,
        "migrated_at": timestamp.isoformat(),
    }
    if isinstance(ui_state, dict):
        weighted_ui = {field: ui_state[field] for field in WEIGHTED_UI_FIELDS if isinstance(ui_state.get(field), bool)}
        if ui_state.get("algoTab") == "weighted":
            weighted_ui["preferredAlgoTab"] = "weighted"
        if ui_state.get("tradingWindowMode") == "weighted":
            weighted_ui["preferredTradingWindowMode"] = "weighted"
        if weighted_ui:
            preferences["ui_state"] = weighted_ui
    if isinstance(order_modes, dict):
        preferences["order_control_modes"] = order_modes
    if isinstance(order_overrides, dict):
        preferences["order_control_overrides"] = order_overrides
    if isinstance(target_overrides, dict):
        preferences["target_order_overrides"] = target_overrides
    if len(preferences) <= 3:
        reason_codes.append("weighted_voting.migration.no_legacy_ui_preferences")
        return False
    store.write_snapshot("weighted_voting.ui_preferences.migrated", preferences)
    reason_codes.append("weighted_voting.migration.ui_preferences_promoted")
    return True


def _trade_record_from_legacy(row: Any, index: int) -> WeightedTradeRecord:
    if not isinstance(row, dict):
        raise ValueError("trade row must be an object")
    if row.get("algorithmId") not in (None, "weighted_voting", "weighted"):
        raise ValueError("trade row belongs to another algorithm")
    side = _side(row.get("side"))
    quantity = int(float(row.get("quantity", 0)))
    price = float(row.get("price", row.get("tradePrice", row.get("executionPrice", 0))))
    timestamp = _parse_datetime(row.get("recordedAt") or row.get("timestamp") or row.get("tradeTimestamp"))
    legacy_id = str(row.get("id") or f"legacy-{index + 1}")
    return WeightedTradeRecord(
        trade_id=f"legacy-ui-{_safe_id(legacy_id)}",
        decision_id=str(row.get("decisionId") or f"legacy-ui-decision-{_safe_id(legacy_id)}"),
        order_id=str(row.get("orderId") or f"legacy-ui-order-{_safe_id(legacy_id)}"),
        symbol=str(row.get("symbol") or "SPY"),
        side=side,
        quantity=quantity,
        price=price,
        trade_timestamp=timestamp,
        reason_codes=("weighted_voting.migration.legacy_trade_history",),
        explanation="Legacy Weighted Voting UI trade history migrated as a historical backend record.",
    )


def _side(value: Any) -> WeightedSide:
    if value in {WeightedSide.BUY, WeightedSide.BUY.value, "BUY", "buy"}:
        return WeightedSide.BUY
    if value in {WeightedSide.SELL, WeightedSide.SELL.value, "SELL", "sell"}:
        return WeightedSide.SELL
    raise ValueError("trade side must be Buy or Sell")


def _legacy_payload(legacy_state: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in legacy_state:
            return _parse_json_value(legacy_state[key])
    browser_storage = legacy_state.get("browserStorage") or legacy_state.get("localStorage")
    if isinstance(browser_storage, Mapping):
        for key in keys:
            if key in browser_storage:
                return _parse_json_value(browser_storage[key])
    return None


def _parse_json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _has_trustworthy_weight_provenance(payload: dict[str, Any]) -> bool:
    required = ("algorithm_id", "weight_version", "strategy_weights", "data_timestamp", "last_updated_at")
    provenance = payload.get("provenance") or {}
    return (
        all(key in payload for key in required)
        and payload.get("algorithm_id") == "weighted_voting"
        and bool(payload.get("configuration_hash") or payload.get("config_hash"))
        and bool(payload.get("data_manifest_hash") or provenance.get("data_manifest_hash"))
        and provenance.get("source") in {"weighted_voting_walk_forward_v2", "weighted_voting_backend_v2"}
    )


def _weight_state_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "contract_version",
        "algorithm_id",
        "weight_version",
        "state_status",
        "strategy_weights",
        "active_session_date",
        "performance_metrics",
        "last_updated_at",
        "data_timestamp",
        "reason_codes",
        "explanation",
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _archive_untrusted(store: WeightedVotingStateStore, artifact_type: str, payload: Any, timestamp: datetime, reason_codes: tuple[str, ...]) -> None:
    archive_payload = {
        "algorithm_id": "weighted_voting",
        "migration_version": WEIGHTED_VOTING_MIGRATION_VERSION,
        "artifact_type": artifact_type,
        "trust_status": "untrusted",
        "archived_at": timestamp.isoformat(),
        "payload_hash": _hash_payload(payload),
        "payload": _json_ready(payload),
        "reason_codes": reason_codes,
    }
    store.write_snapshot(f"weighted_voting.migration.archive.{artifact_type}.{archive_payload['payload_hash'][:12]}", archive_payload)


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _result_from_record(record: Mapping[str, Any], *, status: str) -> WeightedVotingMigrationResult:
    return WeightedVotingMigrationResult(
        status=status,
        migration_version=str(record.get("migration_version") or WEIGHTED_VOTING_MIGRATION_VERSION),
        settings_migrated=bool(record.get("settings_migrated")),
        active_weights_initialized=bool(record.get("active_weights_initialized")),
        untrusted_weight_history_archived=bool(record.get("untrusted_weight_history_archived")),
        trustworthy_performance_migrated=bool(record.get("trustworthy_performance_migrated")),
        trade_records_migrated=int(record.get("trade_records_migrated") or 0),
        ui_preferences_migrated=bool(record.get("ui_preferences_migrated")),
        reason_codes=tuple(record.get("reason_codes") or ()),
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is required")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_id(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "._-" else "_" for character in value).strip("._")
    return cleaned or "legacy"


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
