from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.decision_worker import MetaStrategyFinalisedBarDecisionEvent
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.state_provider import MetaStrategyAuthoritativeDecisionStateProvider
from backend.app.database import CandleStore


BAR_END = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyAuthoritativeDecisionStateProviderTest(unittest.TestCase):
    def test_loads_exact_event_settings_version_and_never_active_fallback(self) -> None:
        fixture = provider_fixture()
        requested = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-requested"), actor="test")
        active = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-active"), actor="test")
        fixture.settings_store.activate_settings(active.settings_version, actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=requested.settings_version))

        self.assertEqual(context.settings.settings_version, "settings-requested")
        self.assertNotEqual(context.settings.settings_version, "settings-active")
        self.assertEqual(context.market_snapshot_request.strategy_catalog_version, "meta_strategy_strategy_catalog_v1")
        self.assertEqual(context.event.capital_partition_id, META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

    def test_candles_quotes_and_relative_inputs_are_point_in_time_clipped(self) -> None:
        fixture = provider_fixture()
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-pit"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1), drift=0.01))
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=1, end=BAR_END, drift=100.0))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))
        fixture.store.upsert_many(candle_rows("QQQ", "1Min", count=90, end=BAR_END - timedelta(minutes=1), drift=0.02))
        fixture.store.upsert_many(candle_rows("IWM", "1Min", count=90, end=BAR_END - timedelta(minutes=1), drift=0.03))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertEqual(context.market_snapshot_request.decision_timestamp, BAR_END)
        self.assertEqual(context.market_snapshot_request.one_minute_candles[-1].timestamp, BAR_END - timedelta(minutes=1))
        self.assertLess(context.market_snapshot_request.one_minute_candles[-1].close, 102.0)
        self.assertTrue(all(candle.timestamp + timedelta(minutes=1) <= BAR_END for candle in context.market_snapshot_request.one_minute_candles))
        self.assertTrue(all(quote.timestamp <= BAR_END for quote in context.market_snapshot_request.quotes))
        self.assertTrue(context.market_snapshot_request.qqq_candles)
        self.assertTrue(context.market_snapshot_request.iwm_candles)

    def test_missing_mandatory_quote_blocks_without_guessing_quote(self) -> None:
        fixture = provider_fixture(quote_source=None)
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-no-quote"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertEqual(context.market_snapshot_request.quotes, ())
        self.assertIn("latestQuote", context.event_state["missingMandatoryInputs"])
        self.assertEqual(context.event_state["dataQualityState"], "BLOCKED")
        self.assertTrue(context.global_risk_snapshot["reject"])
        self.assertFalse(context.operational_health["tradingAllowed"])

    def test_inventory_snapshot_uses_only_meta_strategy_ledger_records_at_bar_end(self) -> None:
        fixture = provider_fixture()
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-inventory"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))
        fixture.inventory.ingest_broker_fill(fill_payload("fill-before", timestamp=BAR_END - timedelta(minutes=5), quantity=3, price=100.0, settings_version=settings.settings_version))
        fixture.inventory.ingest_broker_fill(fill_payload("fill-after", timestamp=BAR_END + timedelta(minutes=5), quantity=7, price=101.0, settings_version=settings.settings_version))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertEqual(len(context.inventory_snapshot["fills"]), 1)
        self.assertEqual(context.inventory_snapshot["fills"][0]["brokerFillId"], "fill-before")
        self.assertEqual(context.inventory_snapshot["positions"][0]["quantity"], 3.0)
        self.assertEqual(context.inventory_snapshot["capitalPartitionId"], META_STRATEGY_DEFAULT_CAPITAL_PARTITION)


class StaticQuoteSource:
    def read_quote(self, *, symbol: str, at: datetime):
        return {"symbol": symbol, "bid": 100.0, "ask": 100.02, "quoteTimestamp": (at - timedelta(seconds=10)).isoformat(), "source": "test_quote_source"}


class StaticAccountSource:
    def read_account_snapshot(self, *, at: datetime):
        return {"accountEquity": 100_000.0, "buyingPower": 90_000.0, "cashAvailable": 50_000.0, "capturedAt": at.isoformat(), "source": "test_account_source"}


class StaticGlobalRiskSource:
    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str):
        return {"availableRiskDollars": 1_000.0, "maxQuantity": 10_000, "reject": False, "capturedAt": at.isoformat(), "source": "test_global_risk_source"}


def provider_fixture(*, quote_source=StaticQuoteSource()):
    database_url = f"sqlite:///{temp_db_path()}"
    settings_path = temp_db_path(prefix="meta-strategy-state-settings")
    store = CandleStore(SimpleNamespace(database_url=database_url))
    inventory = MetaStrategySqliteRepository(database_url)
    jobs = MetaStrategyJobRepository(database_url)
    settings_store = MetaStrategySettingsStore(settings_path)
    provider = MetaStrategyAuthoritativeDecisionStateProvider(
        candle_store=store,
        inventory_repository=inventory,
        job_repository=jobs,
        settings_store=settings_store,
        quote_source=quote_source,
        account_source=StaticAccountSource(),
        global_risk_source=StaticGlobalRiskSource(),
        history_limit=90,
        minimum_warmup=80,
    )
    return SimpleNamespace(provider=provider, store=store, inventory=inventory, jobs=jobs, settings_store=settings_store)


def event(*, settings_version: str) -> MetaStrategyFinalisedBarDecisionEvent:
    return MetaStrategyFinalisedBarDecisionEvent(
        event_id=f"event-{uuid4().hex}",
        job_id=f"job-{uuid4().hex}",
        mode="PAPER",
        symbol="SPY",
        timeframe="1m",
        bar_end=BAR_END,
        settings_version=settings_version,
        idempotency_key=f"meta_strategy:{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}:SPY:1m:{BAR_END.isoformat()}:{settings_version}",
        capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
    )


def candle_rows(symbol: str, timeframe: str, *, count: int, end: datetime, step: int = 1, drift: float = 0.01) -> list[dict]:
    start = end - timedelta(minutes=step * (count - 1))
    rows = []
    for index in range(count):
        timestamp = start + timedelta(minutes=step * index)
        base = 100.0 + index * drift
        rows.append(
            {
                "provider": "test",
                "feed": "iex",
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": base,
                "high": base + 0.1,
                "low": base - 0.1,
                "close": base + 0.02,
                "volume": 100_000,
                "trade_count": None,
                "vwap": None,
            }
        )
    return rows


def fill_payload(fill_id: str, *, timestamp: datetime, quantity: float, price: float, settings_version: str) -> dict:
    return {
        "algorithmId": "meta_strategy",
        "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "settingsVersion": settings_version,
        "correlationId": fill_id,
        "decisionId": f"decision-{fill_id}",
        "jobId": f"job-{fill_id}",
        "eventId": f"event-{fill_id}",
        "orderIntentId": f"intent-{fill_id}",
        "brokerFillId": fill_id,
        "symbol": "SPY",
        "side": "BUY",
        "quantity": quantity,
        "price": price,
        "status": "FILLED",
        "timestamp": timestamp.isoformat(),
    }


def temp_db_path(*, prefix: str = "meta-strategy-state") -> str:
    return str((Path("data/test_tmp") / f"{prefix}-{uuid4().hex}.sqlite").resolve())

