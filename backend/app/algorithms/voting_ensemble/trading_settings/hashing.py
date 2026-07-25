"""Deterministic hashing for Voting Ensemble one-minute settings."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


HASH_EXCLUDED_KEYS = {"configurationHash", "resolutionTimestamp", "resolvedAt"}


def trading_settings_hash(payload: dict[str, Any]) -> str:
    canonical = _canonicalize({key: value for key, value in payload.items() if key not in HASH_EXCLUDED_KEYS})
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _canonicalize(inner) for key, inner in sorted(value.items(), key=lambda item: str(item[0])) if key not in HASH_EXCLUDED_KEYS}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
