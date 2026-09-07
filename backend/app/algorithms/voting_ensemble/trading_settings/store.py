"""Durable operator overrides for the one-minute trading settings.

The resolver stays pure: it takes a payload and returns settings, with no hidden state,
because replay and the tests depend on the same payload always producing the same hash.
This module is the other half -- where an operator's edits live between restarts -- and
the callers that trade (the finalised-bar producer, the evaluation service, the API) are
what put the two together.

Only the override keys are stored, never the resolved settings. A stored resolved blob
would go stale the moment the baseline moved; a stored override is still meaningful
against a newer baseline, and the file states what was changed rather than the whole
configuration.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from backend.app.algorithms.voting_ensemble.trading_settings.editable import (
    EDITABLE_ONE_MINUTE_FIELDS,
    LEGACY_IGNORED_OVERRIDE_KEYS,
    editable_field,
)


VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION = "voting_ensemble_trading_settings_overrides_v1"


def default_trading_settings_store_path() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "trading_settings.json"


class VotingEnsembleTradingSettingsRepository:
    """Reads and writes the override file, atomically, under a process lock."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else default_trading_settings_store_path()
        self._lock = Lock()

    def load_record(self) -> dict[str, Any]:
        with self._lock:
            return self._load_unlocked()

    def load_overrides(self) -> dict[str, Any]:
        return dict(self.load_record().get("overrides") or {})

    def save(self, overrides: dict[str, Any], *, updated_by: str = "operator", reason: str = "") -> dict[str, Any]:
        """Replace the stored overrides with `overrides`, keeping only known fields."""
        accepted, rejected = split_known_override_keys(overrides)
        record = {
            "version": VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION,
            "algorithmId": "voting_ensemble",
            "overrides": accepted,
            "updatedAt": datetime.now(UTC).isoformat(),
            "updatedBy": str(updated_by or "operator"),
            "reason": str(reason or ""),
            "ignoredKeys": rejected,
        }
        with self._lock:
            self._write_unlocked(record)
        return record

    def clear(self, *, updated_by: str = "operator", reason: str = "reset to baseline") -> dict[str, Any]:
        return self.save({}, updated_by=updated_by, reason=reason)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_record()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            # A corrupt override file must not stop the algorithm trading, and it must
            # not silently apply half of itself either. Fall back to the baseline and say
            # so, the same fail-closed-to-baseline choice the control store makes.
            return {
                **_empty_record(),
                "reasonCodes": ["voting_ensemble.trading_settings.override_file_unreadable_baseline_used"],
            }
        if not isinstance(payload, dict):
            return _empty_record()
        accepted, ignored = split_known_override_keys(payload.get("overrides") or {})
        return {
            "version": str(payload.get("version") or VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION),
            "algorithmId": "voting_ensemble",
            "overrides": accepted,
            "updatedAt": str(payload.get("updatedAt") or ""),
            "updatedBy": str(payload.get("updatedBy") or ""),
            "reason": str(payload.get("reason") or ""),
            "ignoredKeys": ignored,
            "reasonCodes": [],
        }

    def _write_unlocked(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, sort_keys=True, indent=2)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)
        except PermissionError:
            # OneDrive holds a transient lock on the replace often enough that a direct
            # write is the practical fallback; the same pattern the control store uses.
            self.path.write_text(encoded, encoding="utf-8")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def split_known_override_keys(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Split a payload into overrides the registry knows and keys it does not.

    Nested `familyWeights` objects are flattened to the dotted registry keys so the file
    holds one shape. Legacy keys a stale dashboard still sends are dropped quietly;
    anything else unknown is reported back so a typo is visible rather than silent.
    """
    if not isinstance(payload, dict):
        return {}, []
    accepted: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in payload.items():
        name = str(key)
        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                dotted = f"{name}.{inner_key}"
                if editable_field(dotted) is not None:
                    accepted[dotted] = inner_value
                else:
                    ignored.append(dotted)
            continue
        if editable_field(name) is not None:
            accepted[name] = value
        elif name not in LEGACY_IGNORED_OVERRIDE_KEYS:
            ignored.append(name)
    return accepted, sorted(set(ignored))


def override_validation_errors(overrides: dict[str, Any]) -> list[str]:
    """Field-level problems an operator should see, before the resolver clamps anything.

    The resolver deliberately discards a value it cannot read, because it runs on every
    evaluation and must not fail one. A save is the opposite: a value the operator typed
    that would be thrown away is a mistake worth refusing, so it is checked here.
    """
    errors: list[str] = []
    for key, value in (overrides or {}).items():
        spec = editable_field(str(key))
        if spec is None:
            continue
        if spec.kind == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{key}: expected true or false")
            continue
        if spec.kind == "choice":
            if str(value) not in spec.choices:
                errors.append(f"{key}: expected one of {', '.join(spec.choices)}")
            continue
        if spec.kind == "time":
            text = str(value)
            if len(text) != 5 or text[2] != ":" or not (text[:2].isdigit() and text[3:].isdigit()):
                errors.append(f"{key}: expected a HH:MM time")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{key}: expected a number")
            continue
        if number != number or number in (float("inf"), float("-inf")):
            errors.append(f"{key}: expected a finite number")
            continue
        if spec.minimum is not None and number < float(spec.minimum):
            errors.append(f"{key}: below the minimum of {spec.minimum}")
        if spec.maximum is not None and number > float(spec.maximum):
            errors.append(f"{key}: above the maximum of {spec.maximum}")
    return errors


def _empty_record() -> dict[str, Any]:
    return {
        "version": VOTING_ENSEMBLE_TRADING_SETTINGS_OVERRIDE_VERSION,
        "algorithmId": "voting_ensemble",
        "overrides": {},
        "updatedAt": "",
        "updatedBy": "",
        "reason": "",
        "ignoredKeys": [],
        "reasonCodes": [],
    }


_REPOSITORY: VotingEnsembleTradingSettingsRepository | None = None
_REPOSITORY_LOCK = Lock()


def trading_settings_repository() -> VotingEnsembleTradingSettingsRepository:
    global _REPOSITORY
    with _REPOSITORY_LOCK:
        if _REPOSITORY is None:
            _REPOSITORY = VotingEnsembleTradingSettingsRepository()
        return _REPOSITORY


def set_trading_settings_repository(repository: VotingEnsembleTradingSettingsRepository | None) -> None:
    """Point the process at another store. Tests use it; nothing in production does."""
    global _REPOSITORY
    with _REPOSITORY_LOCK:
        _REPOSITORY = repository


def stored_trading_settings_overrides() -> dict[str, Any]:
    """The overrides the trading paths resolve against.

    Every caller that evaluates a live bar goes through here, so an edit takes effect on
    the next bar without a restart. It never raises: a store that cannot be read leaves
    the algorithm on the baseline rather than stopping it.
    """
    try:
        return trading_settings_repository().load_overrides()
    except Exception:
        return {}


def merged_settings_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Stored overrides underneath an explicit request payload.

    A caller that names a value wins -- that is what a backtest sweep or a what-if
    evaluation is for -- and everything it does not name comes from the operator's saved
    configuration, so a manual evaluation and the live bar agree.
    """
    merged: dict[str, Any] = dict(stored_trading_settings_overrides())
    if isinstance(payload, dict):
        merged.update(deepcopy(payload))
    return merged


def baseline_override_defaults() -> dict[str, Any]:
    """Each editable field's baseline value, addressed by its registry key."""
    from backend.app.algorithms.voting_ensemble.trading_settings.baseline import one_minute_baseline_settings

    baseline = one_minute_baseline_settings()
    defaults: dict[str, Any] = {}
    for spec in EDITABLE_ONE_MINUTE_FIELDS:
        cursor: Any = baseline
        for segment in spec.path:
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(segment)
        defaults[spec.key] = cursor
    return defaults
