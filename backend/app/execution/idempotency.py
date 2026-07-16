"""Neutral idempotency helpers."""

from __future__ import annotations

import hashlib


def idempotency_key(*parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
