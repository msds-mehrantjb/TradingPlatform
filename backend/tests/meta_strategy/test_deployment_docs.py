from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.meta_strategy.execution import _env_non_negative_int
from backend.app.algorithms.meta_strategy.market_clock import normalize_market_clock_payload
from backend.app.algorithms.meta_strategy.paper_readiness import build_meta_strategy_paper_entry_readiness_prerequisites
from backend.app.algorithms.meta_strategy.runtime_supervisor import _config_from_settings


ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"
RUNBOOK = ROOT / "docs" / "meta_strategy" / "automatic_paper_deployment.md"
README = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "README.md"


REQUIRED_ENV_DEFAULTS = {
    "META_STRATEGY_RUNTIME_ENABLED": "false",
    "META_STRATEGY_RUNTIME_MODE": "SHADOW",
    "META_STRATEGY_LIVE_TRADING_ENABLED": "false",
    "META_STRATEGY_PAPER_NEW_ENTRIES_ENABLED": "false",
    "META_STRATEGY_PAPER_BROKER": "LOCAL_LEDGER",
    "META_STRATEGY_LOCAL_LEDGER_MARKET_OPEN": "false",
    "META_STRATEGY_LOCAL_LEDGER_IMMEDIATE_FILLS": "false",
    "META_STRATEGY_LOCAL_PAPER_BASE_URL": "",
    "META_STRATEGY_LOCAL_PAPER_TOKEN": "",
    "META_STRATEGY_LOCAL_PAPER_ACCOUNT_PATH": "/account",
    "META_STRATEGY_LOCAL_PAPER_CLOCK_PATH": "/clock",
    "META_STRATEGY_LOCAL_PAPER_ORDERS_PATH": "/orders",
    "META_STRATEGY_LOCAL_PAPER_POSITIONS_PATH": "/positions",
    "META_STRATEGY_LOCAL_RISK_SNAPSHOT_PATH": "/risk/snapshot",
    "META_STRATEGY_LOCAL_RISK_APPROVAL_PATH": "/risk/approve",
    "ALPACA_TRADING_BASE_URL": "https://paper-api.alpaca.markets/v2",
    "META_STRATEGY_SYMBOLS": "SPY",
    "META_STRATEGY_MARKET_DATA_FEED": "iex",
    "DATABASE_URL": "sqlite:///./data/trading.db",
    "META_STRATEGY_WORKER_POLL_SECONDS": "1",
    "META_STRATEGY_RECONCILIATION_POLL_SECONDS": "15",
    "META_STRATEGY_STALE_ORDER_POLL_SECONDS": "30",
    "META_STRATEGY_INVENTORY_RECONCILIATION_POLL_SECONDS": "60",
    "META_STRATEGY_POSITION_MANAGEMENT_POLL_SECONDS": "15",
    "META_STRATEGY_HEARTBEAT_INTERVAL_SECONDS": "5",
    "META_STRATEGY_MAINTENANCE_INTERVAL_SECONDS": "15",
    "META_STRATEGY_CANDLE_POLL_SECONDS": "5",
    "META_STRATEGY_WORKER_LEASE_SECONDS": "60",
    "META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS": "60",
    "META_STRATEGY_CANDLE_FRESHNESS_LIMIT_SECONDS": "180",
    "META_STRATEGY_ORDER_INTENT_MAX_AGE_SECONDS": "300",
    "META_STRATEGY_DECISION_MAX_AGE_SECONDS": "300",
    "META_STRATEGY_GLOBAL_RISK_FRESHNESS_LIMIT_SECONDS": "30",
    "META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS": "30",
    "META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS": "75",
    "META_STRATEGY_DEAD_LETTER_THRESHOLD": "0",
}


def test_env_template_documents_meta_strategy_safe_defaults_without_credentials() -> None:
    env_template = ENV_EXAMPLE.read_text(encoding="utf-8")

    for name, value in REQUIRED_ENV_DEFAULTS.items():
        assert f"{name}={value}" in env_template
    assert "APCA_API_KEY_ID=\n" in env_template
    assert "APCA_API_SECRET_KEY=\n" in env_template
    assert "live-api.alpaca.markets" not in env_template


def test_runbook_documents_activation_fail_closed_behavior_and_required_variables() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    for name in REQUIRED_ENV_DEFAULTS:
        assert name in runbook
    for phrase in (
        "Do not automatically switch from `SHADOW` to `PAPER`",
        "Paper activation must be explicit, durable, and auditable",
        "META_STRATEGY_PAPER_BROKER=LOCAL_PAPER",
        "META_STRATEGY_PAPER_BROKER=LOCAL_LEDGER",
        "paperOnly=true",
        "local settings risk source is consumed through the existing Meta-Strategy execution pipeline",
        "Missing authoritative state",
        "Missing market clock",
        "Missing readiness evidence",
        "Turning the paper toggle OFF blocks new entry decisions",
        "does not block reconciliation",
        "does not enable live trading",
    ):
        assert phrase in runbook or phrase in readme
    assert "automatic_paper_deployment.md" in readme


def test_supervisor_config_consumes_documented_env_variables() -> None:
    with patch.dict(
        os.environ,
        {
            "META_STRATEGY_RUNTIME_ENABLED": "true",
            "META_STRATEGY_RUNTIME_MODE": "PAPER",
            "DATABASE_URL": "sqlite:///./data/meta-strategy-paper.db",
            "META_STRATEGY_WORKER_POLL_SECONDS": "2",
            "META_STRATEGY_RECONCILIATION_POLL_SECONDS": "16",
            "META_STRATEGY_STALE_ORDER_POLL_SECONDS": "31",
            "META_STRATEGY_INVENTORY_RECONCILIATION_POLL_SECONDS": "61",
            "META_STRATEGY_POSITION_MANAGEMENT_POLL_SECONDS": "17",
            "META_STRATEGY_HEARTBEAT_INTERVAL_SECONDS": "6",
            "META_STRATEGY_MAINTENANCE_INTERVAL_SECONDS": "18",
            "META_STRATEGY_CANDLE_POLL_SECONDS": "7",
            "META_STRATEGY_WORKER_LEASE_SECONDS": "62",
            "META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS": "76",
            "META_STRATEGY_DEAD_LETTER_THRESHOLD": "1",
            "META_STRATEGY_SYMBOLS": "spy, qqq, spy",
            "META_STRATEGY_MARKET_DATA_FEED": "sip",
            "META_STRATEGY_CANDLE_FRESHNESS_LIMIT_SECONDS": "181",
        },
    ):
        config = _config_from_settings(None)

    assert config.enabled is True
    assert config.mode.value == "PAPER"
    assert config.database_url == "sqlite:///./data/meta-strategy-paper.db"
    assert config.worker_poll_seconds == 2
    assert config.reconciliation_poll_seconds == 16
    assert config.stale_order_poll_seconds == 31
    assert config.inventory_poll_seconds == 61
    assert config.position_poll_seconds == 17
    assert config.heartbeat_interval_seconds == 6
    assert config.maintenance_interval_seconds == 18
    assert config.candle_poll_seconds == 7
    assert config.worker_lease_seconds == 62
    assert config.max_queue_lag_seconds == 76
    assert config.max_dead_letter_count == 1
    assert config.symbols == ("SPY", "QQQ")
    assert config.market_data_feed == "sip"
    assert config.candle_max_staleness_seconds == 181


def test_documented_execution_freshness_env_preserves_zero_and_rejects_negative() -> None:
    with patch.dict(os.environ, {"META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS": "0"}):
        assert _env_non_negative_int("META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS", 60) == 0
    with patch.dict(os.environ, {"META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS": "-5"}):
        assert _env_non_negative_int("META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS", 60) == 0
    with patch.dict(os.environ, {"META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS": "invalid"}):
        assert _env_non_negative_int("META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS", 60) == 60


def test_market_clock_freshness_limit_uses_documented_env_default() -> None:
    evaluated_at = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    payload = {
        "source": "alpaca_paper_clock",
        "isOpen": True,
        "status": "open",
        "capturedAt": evaluated_at.isoformat(),
        "dataSourceTimestamp": (evaluated_at - timedelta(seconds=45)).isoformat(),
        "authoritative": True,
    }
    with patch.dict(os.environ, {"META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS": "60"}):
        assert normalize_market_clock_payload(payload, evaluated_at=evaluated_at).fresh is True
    with patch.dict(os.environ, {"META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS": "30"}):
        assert normalize_market_clock_payload(payload, evaluated_at=evaluated_at).fresh is False


def test_readiness_thresholds_use_documented_env_values() -> None:
    snapshot = {
        "settings": {"paperExecution": {"enabled": True, "executionMode": "PAPER"}},
        "metrics": {"paperBrokerConnectivity": {"verified": True}},
        "queueHealth": {"queues": {}},
        "algorithmReadiness": {"readyToTrade": True},
        "inventory": {"consistency": {"consistent": True}},
    }
    runtime = {
        "queueLagSeconds": 10,
        "deadLetterCount": 1,
        "workers": {
            "finalised_bar_decisions": "healthy",
            "order_submission": "healthy",
            "order_reconciliation": "healthy",
            "stale_order_handling": "healthy",
            "inventory_reconciliation": "healthy",
            "position_management": "healthy",
        },
        "restartState": {"status": "OK"},
        "paperReadinessPrerequisites": {
            "authoritativeMarketDataHealthy": True,
            "marketClockHealthy": True,
            "globalRiskSourceCurrent": True,
            "requiredAcceptanceTestsPassed": True,
        },
    }

    with patch.dict(os.environ, {"META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS": "11", "META_STRATEGY_DEAD_LETTER_THRESHOLD": "1"}):
        ready = build_meta_strategy_paper_entry_readiness_prerequisites(snapshot, runtime)
    with patch.dict(os.environ, {"META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS": "9", "META_STRATEGY_DEAD_LETTER_THRESHOLD": "0"}):
        blocked = build_meta_strategy_paper_entry_readiness_prerequisites(snapshot, runtime)

    assert ready["queueLagBelowThreshold"] is True
    assert ready["deadLetterWithinThreshold"] is True
    assert blocked["queueLagBelowThreshold"] is False
    assert blocked["deadLetterWithinThreshold"] is False
