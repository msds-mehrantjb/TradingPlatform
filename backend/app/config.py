from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    alpaca_key_id: str
    alpaca_secret_key: str
    alpaca_data_base_url: str
    alpaca_trading_base_url: str
    ollama_base_url: str
    ollama_model: str
    database_url: str
    allowed_origins: list[str]

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret_key)


def get_settings() -> Settings:
    origins = os.getenv(
        "ALLOWED_ORIGINS",
        ",".join(
            origin
            for port in range(5173, 5181)
            for origin in (f"http://localhost:{port}", f"http://127.0.0.1:{port}")
        ),
    )
    return Settings(
        alpaca_key_id=os.getenv("APCA_API_KEY_ID", ""),
        alpaca_secret_key=os.getenv("APCA_API_SECRET_KEY", ""),
        alpaca_data_base_url=os.getenv(
            "ALPACA_DATA_BASE_URL",
            "https://data.alpaca.markets/v2",
        ).rstrip("/"),
        alpaca_trading_base_url=os.getenv(
            "ALPACA_TRADING_BASE_URL",
            "https://paper-api.alpaca.markets/v2",
        ).rstrip("/"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"),
        allowed_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
    )
