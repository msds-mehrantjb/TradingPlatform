from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.regime.contracts import REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import event_payload_has_forbidden_operational_state
from backend.app.algorithms.regime.runtime_factory import build_regime_paper_runtime
from backend.app.algorithms.regime.runtime_publisher import (
    RegimeFinalizedOneMinutePublisher,
    RegimeFinalizedOneMinutePublisherConfig,
    RegimePublisherPollResult,
)
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.runtime_supervisor import _publisher_sleep_seconds
from backend.app.algorithms.regime.service import RegimeApplicationService


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_runtime_publisher"
IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID,
    "accountId": "paper-account-123",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


def test_phase24_publisher_publishes_finalized_one_minute_bar_exactly_once() -> None:
    repository = _repository()
    service = RegimeApplicationService(repository)
    supervisor = RegimeRuntimeSupervisor(service=service, config=_paper_config())
    candle_store = _FakeCandleStore()
    now = datetime(2026, 7, 23, 15, 32, 10, tzinfo=UTC)
    market_data = _FakeMarketDataClient(now=now, bars={"SPY": _bars("SPY", datetime(2026, 7, 23, 13, 32, tzinfo=UTC), 120)})
    publisher = RegimeFinalizedOneMinutePublisher(
        identity=IDENTITY,
        repository=repository,
        market_data_client=market_data,
        candle_store=candle_store,
        publish_completed_bar=supervisor.publish_completed_bar,
        config=RegimeFinalizedOneMinutePublisherConfig(finalization_delay_seconds=5, warmup_bars=120, fetch_limit=130),
    )

    first = _run(publisher.poll_once(now=now))
    second = _run(publisher.poll_once(now=now))
    event = supervisor.event_queue.get_nowait()
    persisted = repository.read_runtime_event(IDENTITY, event.event_id)

    assert first.status == "published"
    assert first.accepted_count == 1
    assert first.next_poll_after_seconds == 55.0
    assert second.status == "idle"
    assert second.accepted_count == 0
    assert event.algorithm_id == "regime"
    assert event.algorithm_instance_id == "regime-paper-default"
    assert event.account_id == "paper-account-123"
    assert event.runtime_mode == "paper"
    assert event.symbol == "SPY"
    assert event.completed_bar_timestamp == datetime(2026, 7, 23, 15, 31, tzinfo=UTC)
    assert event.completed is True
    assert event_payload_has_forbidden_operational_state(event.as_dict()) is False
    assert persisted is not None


def test_phase24_publisher_blocks_closed_or_holiday_sessions() -> None:
    repository = _repository()
    closed_at = datetime(2026, 12, 25, 15, 32, 10, tzinfo=UTC)
    publisher = RegimeFinalizedOneMinutePublisher(
        identity=IDENTITY,
        repository=repository,
        market_data_client=_FakeMarketDataClient(now=closed_at, is_open=False, bars={}),
        candle_store=_FakeCandleStore(),
        publish_completed_bar=lambda payload: _accepted(payload),
    )

    result = _run(publisher.poll_once(now=closed_at))

    assert result.status == "blocked"
    assert result.reason_codes == ("regime.publisher.market_closed",)


def test_phase24_publisher_closed_market_poll_uses_next_open_with_configured_cap() -> None:
    repository = _repository()
    closed_at = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    next_open = closed_at + timedelta(seconds=123)
    publisher = RegimeFinalizedOneMinutePublisher(
        identity=IDENTITY,
        repository=repository,
        market_data_client=_FakeMarketDataClient(now=closed_at, is_open=False, next_open=next_open, bars={}),
        candle_store=_FakeCandleStore(),
        publish_completed_bar=lambda payload: _accepted(payload),
        config=RegimeFinalizedOneMinutePublisherConfig(closed_market_poll_interval_seconds=300),
    )

    result = _run(publisher.poll_once(now=closed_at))

    assert result.status == "blocked"
    assert result.next_poll_after_seconds == 123.0


def test_phase24_publisher_detects_same_session_material_data_gap_before_publish() -> None:
    repository = _repository()
    now = datetime(2026, 7, 23, 15, 32, 10, tzinfo=UTC)
    rows = _bars("SPY", datetime(2026, 7, 23, 13, 30, tzinfo=UTC), 122, skip_offsets={50, 51})
    publisher = RegimeFinalizedOneMinutePublisher(
        identity=IDENTITY,
        repository=repository,
        market_data_client=_FakeMarketDataClient(now=now, bars={"SPY": rows}),
        candle_store=_FakeCandleStore(),
        publish_completed_bar=lambda payload: _accepted(payload),
        config=RegimeFinalizedOneMinutePublisherConfig(finalization_delay_seconds=5, warmup_bars=120, fetch_limit=130, material_gap_minutes=2),
    )

    result = _run(publisher.poll_once(now=now))

    assert result.status == "blocked"
    assert "regime.publisher.missing_bar_detected" in result.reason_codes
    assert "regime.publisher.material_data_gap_detected" in result.reason_codes
    assert repository.read_owned_records("regime_runtime_events", IDENTITY)


def test_phase24_supervisor_blocks_entries_when_publisher_reports_material_gap() -> None:
    supervisor = RegimeRuntimeSupervisor(service=RegimeApplicationService(_repository()), config=_paper_config(), market_event_publisher=_FakePublisherResult())

    snapshot = _run(supervisor.poll_market_event_publisher_once())

    assert snapshot["status"] == "blocked"
    assert "regime.publisher.material_data_gap_detected" in supervisor.metrics.entry_block_reason_codes
    assert supervisor.metrics.component_health["market_event_publisher"]["status"] == "unhealthy"


def test_phase24_factory_composes_publisher_with_existing_market_data_infrastructure() -> None:
    market_data = _FakeMarketDataClient(now=datetime(2026, 7, 23, 15, 32, 10, tzinfo=UTC), bars={})
    candle_store = _FakeCandleStore()
    supervisor = build_regime_paper_runtime(
        service=RegimeApplicationService(_repository()),
        config=_paper_config(),
        broker=_VerifiedBroker(),
        market_data_client=market_data,
        candle_store=candle_store,
    )

    assert supervisor.market_event_publisher is not None
    assert supervisor.market_event_publisher.market_data_client is market_data
    assert supervisor.market_event_publisher.candle_store is candle_store


def test_phase24_runtime_uses_separate_configurable_poll_cadences() -> None:
    config = RegimeRuntimeSupervisorConfig(default_runtime_mode="paper")

    assert config.publisher_poll_interval_seconds == 1.0
    assert config.execution_poll_interval_seconds == 1.0
    assert config.reconciliation_poll_interval_seconds == 3.0
    assert config.position_management_interval_seconds == 5.0
    assert config.health_interval_seconds == 5.0
    assert config.maintenance_interval_seconds == 30.0
    assert _publisher_sleep_seconds(config, {"reasonCodes": ["regime.publisher.no_new_finalized_candle"], "nextPollAfterSeconds": 55.0}) == 55.0
    assert _publisher_sleep_seconds(config, {"reasonCodes": ["regime.publisher.market_closed"], "nextPollAfterSeconds": 1000.0}) == 300.0


class _FakeMarketDataClient:
    def __init__(self, *, now: datetime, bars: dict[str, list[dict]], is_open: bool = True, next_open: datetime | None = None) -> None:
        self.settings = SimpleNamespace(has_alpaca_credentials=True)
        self.now = now
        self.bars = bars
        self.is_open = is_open
        self.next_open = next_open

    async def get_market_status(self) -> dict:
        return {
            "status": "open" if self.is_open else "holiday",
            "isOpen": self.is_open,
            "timestamp": self.now.isoformat().replace("+00:00", "Z"),
            "nextOpen": self.next_open.isoformat().replace("+00:00", "Z") if self.next_open else None,
        }

    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str) -> list[dict]:
        rows = list(self.bars.get(symbol, []))
        return rows[-limit:]

    async def get_latest_quote(self, *, symbol: str, feed: str) -> dict | None:
        return {"symbol": symbol, "bid": 500.0, "ask": 500.05, "bidSize": 100, "askSize": 100, "quoteTimestamp": self.now.isoformat().replace("+00:00", "Z")}


class _FakeCandleStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, candles: list[dict]) -> None:
        existing = {(row["symbol"], row["timeframe"], row["feed"], row["timestamp"]): row for row in self.rows}
        for candle in candles:
            existing[(candle["symbol"], candle["timeframe"], candle["feed"], candle["timestamp"])] = candle
        self.rows = list(existing.values())

    def latest(self, *, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict]:
        rows = [row for row in self.rows if row["symbol"] == symbol and row["timeframe"] == timeframe and row["feed"] == feed]
        return sorted(rows, key=lambda row: row["timestamp"])[-limit:]

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict]:
        rows = [row for row in self.latest(symbol=symbol, timeframe=timeframe, feed=feed, limit=10_000) if row["timestamp"] <= end]
        return rows[-limit:]


class _FakePublisherResult:
    async def poll_once(self) -> RegimePublisherPollResult:
        return RegimePublisherPollResult(status="blocked", reason_codes=("regime.publisher.material_data_gap_detected",), latest_finalized_candle="2026-07-23T15:31:00Z")


class _VerifiedBroker:
    broker_kind = "regime_alpaca_paper"
    account_type = "paper"
    paper_only = True
    live_trading_enabled = False
    credentials_verified = True
    account_matches_configured_identity = True
    account_allowed_to_trade = True
    market_data_credentials_configured = True

    def startup_verification(self) -> dict:
        return {
            "verified": True,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "configured": True,
            "accountMatchesConfiguredIdentity": True,
            "accountAllowedToTrade": True,
            "marketDataCredentialsConfigured": True,
            "reasonCodes": ["regime.alpaca_paper.account_verified"],
        }

    def paper_trading_configuration(self) -> dict:
        return self.startup_verification()

    def refresh_account_snapshot(self) -> dict:
        return {
            "sourceAuthority": "broker",
            "accountId": "paper-account-123",
            "runtimeMode": "paper",
            "equity": 100000.0,
            "cash": 100000.0,
            "buyingPower": 100000.0,
            "availableBuyingPower": 100000.0,
            "globalRiskCapacityQuantity": 1000,
            "dailyAccountPnl": 0.0,
            "buyingPowerCurrent": True,
            "accountSnapshotFresh": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": False,
        }


async def _accepted(payload: dict) -> dict:
    return {"accepted": True, "eventId": payload["eventId"], "reasonCodes": ["regime.runtime.event.enqueued"]}


def _bars(symbol: str, start: datetime, count: int, *, skip_offsets: set[int] | None = None) -> list[dict]:
    rows: list[dict] = []
    skip_offsets = skip_offsets or set()
    for offset in range(count):
        if offset in skip_offsets:
            continue
        timestamp = start + timedelta(minutes=offset)
        price = 500 + offset * 0.01
        rows.append(
            {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": symbol,
                "timeframe": "1Min",
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price + 0.02,
                "volume": 1000 + offset,
                "vwap": price + 0.01,
            }
        )
    return rows


def _paper_config() -> RegimeRuntimeSupervisorConfig:
    return RegimeRuntimeSupervisorConfig(
        default_algorithm_instance_id="regime-paper-default",
        default_account_id="paper-account-123",
        default_runtime_mode="paper",
        symbol="SPY",
    )


def _repository() -> RegimeRepository:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
