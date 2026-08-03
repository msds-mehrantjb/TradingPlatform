"""WCA-specific Alpaca paper account validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


WCA_ALPACA_PAPER_API_KEY_ID = "WCA_ALPACA_PAPER_API_KEY_ID"
WCA_ALPACA_PAPER_API_SECRET_KEY = "WCA_ALPACA_PAPER_API_SECRET_KEY"
WCA_ALPACA_PAPER_BASE_URL = "WCA_ALPACA_PAPER_BASE_URL"
WCA_ALPACA_PAPER_ACCOUNT_ID = "WCA_ALPACA_PAPER_ACCOUNT_ID"
WCA_AUTOMATIC_PAPER_ENABLED = "WCA_AUTOMATIC_PAPER_ENABLED"
WCA_ALPACA_PAPER_ACCOUNT_SHARED = "WCA_ALPACA_PAPER_ACCOUNT_SHARED"
WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED = "WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED"
WCA_REQUIRED_ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"

_GENERIC_ALPACA_KEY_ID = "APCA_API_KEY_ID"
_GENERIC_ALPACA_SECRET_KEY = "APCA_API_SECRET_KEY"


@dataclass(frozen=True)
class WcaPaperAccountValidation:
    verified: bool
    account_id: str | None
    base_url: str | None
    automatic_paper_enabled: bool
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, object]:
        return {
            "verified": self.verified,
            "account_id": self.account_id,
            "base_url": self.base_url,
            "automatic_paper_enabled": self.automatic_paper_enabled,
            "reason_codes": self.reason_codes,
        }


def validate_wca_automatic_paper_account(
    *,
    account_id: str,
    environ: Mapping[str, str] | None = None,
) -> WcaPaperAccountValidation:
    source = environ or os.environ
    key_id = _clean(source.get(WCA_ALPACA_PAPER_API_KEY_ID))
    secret = _clean(source.get(WCA_ALPACA_PAPER_API_SECRET_KEY))
    base_url = _clean(source.get(WCA_ALPACA_PAPER_BASE_URL))
    configured_account = _clean(source.get(WCA_ALPACA_PAPER_ACCOUNT_ID))
    automatic_enabled = _env_bool(source.get(WCA_AUTOMATIC_PAPER_ENABLED))
    shared_account = _env_bool(source.get(WCA_ALPACA_PAPER_ACCOUNT_SHARED))
    account_allocator_enabled = _env_bool(source.get(WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED))
    reasons: list[str] = ["wca.paper_account.validation"]

    if not automatic_enabled:
        reasons.append("wca.paper_account.automatic_paper_disabled")
    if not key_id:
        reasons.append("wca.paper_account.api_key_missing")
    if not secret:
        reasons.append("wca.paper_account.api_secret_missing")
    if not configured_account:
        reasons.append("wca.paper_account.account_id_missing")
    elif configured_account != account_id:
        reasons.append("wca.paper_account.account_id_mismatch")
    elif configured_account.lower() in {"paper", "default", "shared", "alpaca-paper", "global-paper"}:
        reasons.append("wca.paper_account.dedicated_account_id_required")
    if base_url != WCA_REQUIRED_ALPACA_PAPER_BASE_URL:
        reasons.append("wca.paper_account.paper_base_url_invalid")
    if _reuses_generic_alpaca_credentials(source, key_id=key_id, secret=secret):
        reasons.append("wca.paper_account.shared_alpaca_credentials_rejected")
    if shared_account and not account_allocator_enabled:
        reasons.append("wca.paper_account.shared_physical_account_requires_allocator")

    verified = reasons == ["wca.paper_account.validation"]
    if verified:
        reasons.append("wca.paper_account.verified")
    return WcaPaperAccountValidation(
        verified=verified,
        account_id=configured_account,
        base_url=base_url,
        automatic_paper_enabled=automatic_enabled,
        reason_codes=tuple(reasons),
    )


def _reuses_generic_alpaca_credentials(source: Mapping[str, str], *, key_id: str, secret: str) -> bool:
    generic_key = _clean(source.get(_GENERIC_ALPACA_KEY_ID))
    generic_secret = _clean(source.get(_GENERIC_ALPACA_SECRET_KEY))
    return bool((key_id and generic_key and key_id == generic_key) or (secret and generic_secret and secret == generic_secret))


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "WCA_ALPACA_PAPER_ACCOUNT_ID",
    "WCA_ALPACA_PAPER_ACCOUNT_SHARED",
    "WCA_ALPACA_PAPER_API_KEY_ID",
    "WCA_ALPACA_PAPER_API_SECRET_KEY",
    "WCA_ALPACA_PAPER_BASE_URL",
    "WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED",
    "WCA_AUTOMATIC_PAPER_ENABLED",
    "WCA_REQUIRED_ALPACA_PAPER_BASE_URL",
    "WcaPaperAccountValidation",
    "validate_wca_automatic_paper_account",
]
