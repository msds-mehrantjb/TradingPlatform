from __future__ import annotations

import os

import pytest

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsembleAlpacaPaperBroker,
    is_approved_alpaca_paper_endpoint,
)
from backend.app.config import ApplicationConfig, Settings


pytestmark = pytest.mark.alpaca_paper_smoke


def test_voting_ensemble_alpaca_paper_smoke_never_uses_live_endpoint() -> None:
    if os.getenv("RUN_VOTING_ENSEMBLE_ALPACA_PAPER_SMOKE") != "1":
        return
    settings = Settings(
        alpaca_key_id=os.getenv("ALPACA_API_KEY", ""),
        alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY", ""),
        alpaca_data_base_url=os.getenv("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets/v2"),
        alpaca_trading_base_url=os.getenv("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets/v2"),
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///:memory:",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )
    assert is_approved_alpaca_paper_endpoint(settings.alpaca_trading_base_url)
    assert settings.has_alpaca_credentials

    broker = VotingEnsembleAlpacaPaperBroker(settings=settings)
    try:
        assert broker.paper_endpoint
        assert broker.verify_paper_account()
    finally:
        broker.close()
