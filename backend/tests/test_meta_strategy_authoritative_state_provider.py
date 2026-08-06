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

    def test_missing_authoritative_market_clock_blocks_without_local_calendar_fallback(self) -> None:
        fixture = provider_fixture(market_clock_source=None)
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-no-clock"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertFalse(context.operational_health["marketCalendar"]["isOpen"])
        self.assertIn("marketCalendar", context.event_state["missingMandatoryInputs"])
        self.assertIn("meta_strategy.state_provider.authoritative_market_clock_missing", context.event_state["reasonCodes"])
        self.assertFalse(context.operational_health["tradingAllowed"])

    def test_paper_control_off_blocks_decision_time_new_entry_permission(self) -> None:
        fixture = provider_fixture()
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-paper-off"), actor="test")
        fixture.jobs.update_paper_trading_control(
            new_paper_entries_enabled=False,
            updated_by="test",
            reason="meta_strategy.test.paper_off_at_decision",
            now=BAR_END,
        )
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertFalse(context.operational_health["tradingAllowed"])
        self.assertFalse(context.operational_health["paperControl"]["newPaperEntriesEnabled"])
        self.assertIn("meta_strategy.paper_control.new_entry_blocked_at_decision", context.event_state["reasonCodes"])

    def test_missing_paper_control_blocks_decision_time_new_entry_permission(self) -> None:
        fixture = provider_fixture(arm_control=False)
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-paper-missing"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertFalse(context.operational_health["tradingAllowed"])
        self.assertIn("meta_strategy.paper_control.state_unavailable", context.event_state["reasonCodes"])

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
        self.assertEqual(context.inventory_snapshot["lastTradeAt"], (BAR_END - timedelta(minutes=5)).isoformat())

    def test_source_timestamps_versions_and_runtime_health_are_persistable_evidence(self) -> None:
        fixture = provider_fixture()
        requested = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-evidence"), actor="test")
        active = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-active-evidence"), actor="test")
        fixture.settings_store.activate_settings(active.settings_version, actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=requested.settings_version))

        self.assertEqual(context.event_state["sourceVersions"]["eventSettingsVersion"], requested.settings_version)
        self.assertEqual(context.event_state["sourceVersions"]["activeSettingsVersion"], active.settings_version)
        self.assertEqual(context.event_state["sourceTimestamps"]["decisionCutoff"], BAR_END.isoformat())
        self.assertTrue(context.operational_health["runtimeHealth"]["ready"])
        self.assertIn("operationalControls", context.operational_health)

    def test_missing_authoritative_paper_account_state_blocks_new_entries(self) -> None:
        fixture = provider_fixture(account_source=None)
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-no-account"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version))

        self.assertFalse(context.operational_health["tradingAllowed"])
        self.assertIn("accountSnapshot", context.event_state["missingMandatoryInputs"])
        self.assertIn("meta_strategy.state_provider.account_snapshot_missing", context.event_state["reasonCodes"])

    def test_wrong_capital_partition_is_rejected_in_point_in_time_context(self) -> None:
        fixture = provider_fixture()
        settings = fixture.settings_store.create_baseline(build_meta_strategy_settings(settings_version="settings-wrong-partition"), actor="test")
        fixture.store.upsert_many(candle_rows("SPY", "1Min", count=90, end=BAR_END - timedelta(minutes=1)))
        fixture.store.upsert_many(candle_rows("SPY", "5Min", count=80, step=5, end=BAR_END - timedelta(minutes=5)))
        fixture.store.upsert_many(candle_rows("SPY", "15Min", count=80, step=15, end=BAR_END - timedelta(minutes=15)))

        context = fixture.provider.load_context(event(settings_version=settings.settings_version, capital_partition_id="weighted_voting.paper.default"))

        self.assertFalse(context.operational_health["tradingAllowed"])
        self.assertIn("meta_strategy.state_provider.wrong_capital_partition", context.event_state["reasonCodes"])


class StaticQuoteSource:
    def read_quote(self, *, symbol: str, at: datetime):
        return {"symbol": symbol, "bid": 100.0, "ask": 100.02, "quoteTimestamp": (at - timedelta(seconds=10)).isoformat(), "source": "test_quote_source"}


class StaticAccountSource:
    def read_account_snapshot(self, *, at: datetime):
        return {"accountEquity": 100_000.0, "buyingPower": 90_000.0, "cashAvailable": 50_000.0, "capturedAt": at.isoformat(), "source": "test_account_source"}


class StaticGlobalRiskSource:
    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str):
        return {"availableRiskDollars": 1_000.0, "maxQuantity": 10_000, "reject": False, "capturedAt": at.isoformat(), "source": "test_global_risk_source"}


class StaticMarketClockSource:
    def get_clock(self):
        return {"isOpen": True, "status": "open", "capturedAt": BAR_END.isoformat(), "source": "test_alpaca_paper_clock"}


def provider_fixture(
    *,
    quote_source=StaticQuoteSource(),
    account_source=StaticAccountSource(),
    market_clock_source=StaticMarketClockSource(),
    arm_control: bool = True,
):
    database_url = f"sqlite:///{temp_db_path()}"
    settings_path = temp_db_path(prefix="meta-strategy-state-settings")
    store = CandleStore(SimpleNamespace(database_url=database_url))
    inventory = MetaStrategySqliteRepository(database_url)
    jobs = MetaStrategyJobRepository(database_url)
    settings_store = MetaStrategySettingsStore(settings_path)
    if arm_control:
        jobs.update_paper_trading_control(
            new_paper_entries_enabled=True,
            updated_by="test",
            reason="meta_strategy.test.paper_on",
            now=BAR_END,
        )
    jobs.write_gateway_snapshot(
        "meta_strategy.runtime.readiness",
        {
            "algorithmId": "meta_strategy",
            "enabled": True,
            "ready": True,
            "status": "ready",
            "mode": "PAPER",
            "paperOrdersBlocked": False,
            "liveTradingEnabled": False,
            "capturedAt": BAR_END.isoformat(),
            "reasonCodes": ("meta_strategy.runtime.ready",),
        },
        now=BAR_END,
    )
    provider = MetaStrategyAuthoritativeDecisionStateProvider(
        candle_store=store,
        inventory_repository=inventory,
        job_repository=jobs,
        settings_store=settings_store,
        quote_source=quote_source,
        account_source=account_source,
        global_risk_source=StaticGlobalRiskSource(),
        market_clock_source=market_clock_source,
        history_limit=90,
        minimum_warmup=80,
    )
    return SimpleNamespace(provider=provider, store=store, inventory=inventory, jobs=jobs, settings_store=settings_store)


def event(*, settings_version: str, capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION) -> MetaStrategyFinalisedBarDecisionEvent:
    return MetaStrategyFinalisedBarDecisionEvent(
        event_id=f"event-{uuid4().hex}",
        job_id=f"job-{uuid4().hex}",
        mode="PAPER",
        symbol="SPY",
        timeframe="1m",
        bar_end=BAR_END,
        settings_version=settings_version,
        idempotency_key=f"meta_strategy:{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}:SPY:1m:{BAR_END.isoformat()}:{settings_version}",
        capital_partition_id=capital_partition_id,
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
