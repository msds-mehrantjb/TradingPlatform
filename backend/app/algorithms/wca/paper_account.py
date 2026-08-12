"""Compatibility exports for WCA paper-account validation.

Legacy broker-paper code may still import this module. WCA local automatic paper
owns its validation constants in local_paper_account.py so it does not depend on
legacy Alpaca paper account state.
"""

from __future__ import annotations

from backend.app.algorithms.wca.local_paper_account import (
    WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED,
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_ACCOUNT_SHARED,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE,
    WCA_LOCAL_PAPER_ACCOUNT_ID,
    WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    WCA_LOCAL_PAPER_STARTING_BALANCE,
    WcaPaperAccountValidation,
    validate_wca_automatic_paper_account,
)

WCA_REQUIRED_ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"

__all__ = [
    "WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED",
    "WCA_ALPACA_PAPER_ACCOUNT_ID",
    "WCA_ALPACA_PAPER_ACCOUNT_SHARED",
    "WCA_ALPACA_PAPER_API_KEY_ID",
    "WCA_ALPACA_PAPER_API_SECRET_KEY",
    "WCA_ALPACA_PAPER_BASE_URL",
    "WCA_AUTOMATIC_PAPER_ENABLED",
    "WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE",
    "WCA_LOCAL_PAPER_ACCOUNT_ID",
    "WCA_LOCAL_PAPER_SOURCE_AUTHORITY",
    "WCA_LOCAL_PAPER_STARTING_BALANCE",
    "WCA_REQUIRED_ALPACA_PAPER_BASE_URL",
    "WcaPaperAccountValidation",
    "validate_wca_automatic_paper_account",
]