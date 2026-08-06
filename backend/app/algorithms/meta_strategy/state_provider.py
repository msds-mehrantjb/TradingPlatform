"""Authoritative point-in-time state provider for Meta-Strategy decisions."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.decision_worker import (
    MetaStrategyDecisionWorkerContext,
    MetaStrategyFinalisedBarDecisionEvent,
)
from backend.app.algorithms.meta_strategy.feature_schema import meta_strategy_feature_schema_hash
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.market_snapshot import (
    MetaStrategyMarketSnapshotRequest,
    MetaStrategySnapshotCandle,
    MetaStrategySnapshotQuote,
)
from backend.app.algorithms.meta_strategy.market_clock import read_market_clock_snapshot
from backend.app.algorithms.meta_strategy.models import load_runtime_model_artifact_data
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.session import EXCHANGE_TIMEZONE, meta_strategy_session_at
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings, MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_MODEL_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)
from backend.app.database import CandleStore


class MetaStrategyReadOnlyQuoteSource(Protocol):
    def read_quote(self, *, symbol: str, at: datetime) -> Mapping[str, Any] | None:
        ...


class MetaStrategyReadOnlyAccountSource(Protocol):
    def read_account_snapshot(self, *, at: datetime) -> Mapping[str, Any] | None:
        ...


class MetaStrategyReadOnlyGlobalRiskSource(Protocol):
    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str) -> Mapping[str, Any] | None:
        ...


class MetaStrategyReadOnlyOperationalHealthSource(Protocol):
    def read_operational_health(self, *, at: datetime) -> Mapping[str, Any] | None:
        ...


class MetaStrategyReadOnlyEconomicEventSource(Protocol):
    def read_economic_event_state(self, *, at: datetime, symbol: str) -> Mapping[str, Any] | None:
        ...


BREADTH_COMPONENT_SYMBOLS: tuple[str, ...] = ("XLK", "XLF", "XLY", "XLI", "XLV")
MANDATORY_CONTEXT_FIELDS: tuple[str, ...] = (
    "settings",
    "oneMinuteCandles",
    "warmupHistory",
    "latestQuote",
    "accountSnapshot",
    "globalRiskSnapshot",
    "marketCalendar",
    "runtimeHealth",
)
TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "DONE_FOR_DAY"})


class MetaStrategyAuthoritativeDecisionStateProvider:
    """Constructs Meta-Strategy worker context from durable and read-only sources.

    The provider accepts only the finalized-bar event as decision input. All market,
    inventory, risk, settings, and model context is reconstructed internally at the
    event's point-in-time cutoff.
    """

    def __init__(
        self,
        *,
        candle_store: CandleStore | None = None,
        inventory_repository: MetaStrategySqliteRepository | None = None,
        job_repository: Any | None = None,
        settings_store: MetaStrategySettingsStore | None = None,
        quote_source: MetaStrategyReadOnlyQuoteSource | Any | None = None,
        account_source: MetaStrategyReadOnlyAccountSource | Any | None = None,
        global_risk_source: MetaStrategyReadOnlyGlobalRiskSource | Any | None = None,
        operational_health_source: MetaStrategyReadOnlyOperationalHealthSource | Any | None = None,
        market_clock_source: Any | None = None,
        economic_event_source: MetaStrategyReadOnlyEconomicEventSource | Any | None = None,
        app_settings: Any | None = None,
        feed: str = "iex",
        history_limit: int = 240,
        minimum_warmup: int = 80,
        quote_max_age_seconds: int = 60,
    ) -> None:
        self.app_settings = app_settings or _database_settings()
        self.candle_store = candle_store or CandleStore(self.app_settings)
        self.inventory_repository = inventory_repository or MetaStrategySqliteRepository(self.app_settings.database_url)
        self.job_repository = job_repository
        self.settings_store = settings_store or MetaStrategySettingsStore(Path("./data/meta_strategy_settings.db"))
        self.quote_source = quote_source
        self.account_source = account_source
        self.global_risk_source = global_risk_source
        self.operational_health_source = operational_health_source
        self.market_clock_source = market_clock_source
        self.economic_event_source = economic_event_source
        self.feed = feed
        self.history_limit = max(minimum_warmup, int(history_limit))
        self.minimum_warmup = max(1, int(minimum_warmup))
        self.quote_max_age_seconds = max(0, int(quote_max_age_seconds))

    def load_context(self, event: MetaStrategyFinalisedBarDecisionEvent) -> MetaStrategyDecisionWorkerContext:
        bar_end = _aware_utc(event.bar_end)
        capital_partition_id = _capital_partition_id(event)
        decision_id = f"meta_strategy.decision.{event.idempotency_key}"
        snapshot_id = f"meta_strategy.snapshot.{event.idempotency_key}"
        missing: dict[str, bool] = {field: False for field in MANDATORY_CONTEXT_FIELDS}
        reason_codes: list[str] = ["meta_strategy.state_provider.point_in_time_context_built"]

        settings, settings_reasons = self._settings(event.settings_version)
        active_settings_version = self._active_settings_version()
        reason_codes.extend(settings_reasons)

        one_minute = self._candles(event.symbol, "1Min", end=bar_end, limit=self.history_limit)
        five_minute = self._candles(event.symbol, "5Min", end=bar_end, limit=max(80, self.history_limit // 5))
        fifteen_minute = self._candles(event.symbol, "15Min", end=bar_end, limit=max(80, self.history_limit // 15))
        qqq = self._candles("QQQ", "1Min", end=bar_end, limit=self.history_limit)
        iwm = self._candles("IWM", "1Min", end=bar_end, limit=self.history_limit)
        breadth = {
            symbol: candles
            for symbol in BREADTH_COMPONENT_SYMBOLS
            if (candles := self._candles(symbol, "1Min", end=bar_end, limit=self.history_limit))
        }

        data_quality_reasons = self._market_data_quality(one_minute, five_minute, fifteen_minute, bar_end)
        reason_codes.extend(data_quality_reasons)
        missing["oneMinuteCandles"] = not one_minute
        missing["warmupHistory"] = len(one_minute) < max(self.minimum_warmup, _settings_warmup(settings))
        prior_close = self._previous_day_close(event.symbol, bar_end)

        quote, quote_state = self._quote(event.symbol, bar_end)
        missing["latestQuote"] = quote is None
        reason_codes.extend(quote_state["reasonCodes"])
        mark_price = _mark_price(one_minute, quote)

        inventory = self._inventory_snapshot_at(
            capital_partition_id=capital_partition_id,
            settings_version=event.settings_version,
            symbol=event.symbol,
            bar_end=bar_end,
            mark_price=mark_price,
        )
        account = self._account_snapshot(bar_end)
        global_risk = self._global_risk_snapshot(bar_end, capital_partition_id=capital_partition_id)
        market_calendar = self._market_calendar_snapshot(bar_end)
        operational = self._operational_health(bar_end)
        runtime_health = self._runtime_health(bar_end)
        operational_controls = self._operational_controls(bar_end)
        paper_control = self._paper_control(capital_partition_id=capital_partition_id)
        economic = self._economic_event_state(event.symbol, bar_end)
        artifact, artifact_state = self._active_or_shadow_model_artifact(bar_end)

        missing["accountSnapshot"] = not bool(account.get("authoritativeReadOnly"))
        missing["globalRiskSnapshot"] = not bool(global_risk.get("authoritativeReadOnly"))
        missing["marketCalendar"] = market_calendar.get("authoritativeReadOnly") is not True
        missing["runtimeHealth"] = runtime_health.get("authoritativeReadOnly") is not True or runtime_health.get("ready") is not True
        reason_codes.extend(account["reasonCodes"])
        reason_codes.extend(global_risk["reasonCodes"])
        reason_codes.extend(operational["reasonCodes"])
        reason_codes.extend(runtime_health["reasonCodes"])
        reason_codes.extend(operational_controls["reasonCodes"])
        reason_codes.extend(paper_control["reasonCodes"])
        reason_codes.extend(artifact_state["reasonCodes"])
        paper_control_blocks_entry = (
            event.mode.upper() == "PAPER"
            and paper_control.get("newPaperEntriesEnabled") is not True
        )
        wrong_partition = capital_partition_id != META_STRATEGY_DEFAULT_CAPITAL_PARTITION

        blocked_reasons = tuple(
            reason
            for field, reason in (
                ("settings", "meta_strategy.state_provider.settings_version_missing"),
                ("oneMinuteCandles", "meta_strategy.state_provider.one_minute_candles_missing"),
                ("warmupHistory", "meta_strategy.state_provider.warmup_history_missing"),
                ("latestQuote", "meta_strategy.state_provider.latest_quote_missing"),
                ("accountSnapshot", "meta_strategy.state_provider.account_snapshot_missing"),
                ("globalRiskSnapshot", "meta_strategy.state_provider.global_risk_snapshot_missing"),
                ("marketCalendar", "meta_strategy.state_provider.authoritative_market_clock_missing"),
                ("runtimeHealth", "meta_strategy.state_provider.runtime_health_missing_or_not_ready"),
            )
            if missing.get(field)
        )
        if wrong_partition:
            blocked_reasons = (*blocked_reasons, "meta_strategy.state_provider.wrong_capital_partition")
        data_blocked = bool(blocked_reasons)
        source_timestamps = _source_timestamps(
            settings=settings,
            inventory=inventory,
            account=account,
            global_risk=global_risk,
            quote=quote,
            market_calendar=market_calendar,
            operational=operational,
            runtime_health=runtime_health,
            paper_control=paper_control,
            economic=economic,
            artifact=artifact,
            bar_end=bar_end,
        )
        source_versions = _source_versions(
            settings=settings,
            event_settings_version=event.settings_version,
            artifact=artifact,
            snapshot_request_versions={
                "strategyCatalogVersion": META_STRATEGY_STRATEGY_CATALOG_VERSION,
                "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
                "activeSettingsVersion": active_settings_version,
            },
        )
        event_state = {
            **economic,
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": capital_partition_id,
            "eventId": event.event_id,
            "jobId": event.job_id,
            "decisionId": decision_id,
            "correlationId": event.idempotency_key,
            "barEnd": bar_end.isoformat(),
            "settingsVersion": event.settings_version,
            "strategyCatalogVersion": META_STRATEGY_STRATEGY_CATALOG_VERSION,
            "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "modelVersion": str((artifact or {}).get("modelVersion") or "shadow-only"),
            "dataQualityState": "BLOCKED" if data_blocked else "OK",
            "missingMandatoryInputs": tuple(field for field, is_missing in missing.items() if is_missing),
            "optionalFeatureMissingness": self._optional_feature_missingness(qqq=qqq, iwm=iwm, breadth=breadth, prior_close=prior_close, artifact=artifact),
            "sourceTimestamps": source_timestamps,
            "sourceVersions": source_versions,
            "reasonCodes": tuple(
                dict.fromkeys(
                    (
                        *reason_codes,
                        *blocked_reasons,
                        *(("meta_strategy.paper_control.new_entry_blocked_at_decision",) if paper_control_blocks_entry else ()),
                    )
                )
            ),
        }
        operational_health = {
            **operational,
            "status": "BLOCKED" if data_blocked else operational.get("status", "OK"),
            "tradingAllowed": (
                bool(operational.get("tradingAllowed", True))
                and runtime_health.get("ready") is True
                and not _any_new_entry_control_active(operational_controls)
                and not data_blocked
                and not paper_control_blocks_entry
            ),
            "marketCalendar": market_calendar,
            "paperControl": paper_control,
            "runtimeHealth": runtime_health,
            "operationalControls": operational_controls,
            "dataQualityState": event_state["dataQualityState"],
            "missingMandatoryInputs": event_state["missingMandatoryInputs"],
            "sourceTimestamps": source_timestamps,
            "sourceVersions": source_versions,
            "reasonCodes": tuple(
                dict.fromkeys(
                    (
                        *tuple(operational.get("reasonCodes") or ()),
                        *tuple(runtime_health.get("reasonCodes") or ()),
                        *tuple(operational_controls.get("reasonCodes") or ()),
                        *paper_control["reasonCodes"],
                        *(("meta_strategy.paper_control.new_entry_blocked_at_decision",) if paper_control_blocks_entry else ()),
                    )
                )
            ),
        }
        global_risk = {
            **global_risk,
            "reject": bool(global_risk.get("reject")) or data_blocked,
            "tradingHalt": bool(global_risk.get("tradingHalt")) or data_blocked,
            "reasonCodes": tuple(dict.fromkeys((*global_risk["reasonCodes"], *blocked_reasons))),
        }
        request = MetaStrategyMarketSnapshotRequest(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=event.symbol,
            decision_timestamp=bar_end,
            one_minute_candles=one_minute,
            five_minute_candles=five_minute,
            fifteen_minute_candles=fifteen_minute,
            quotes=(quote,) if quote is not None else (),
            qqq_candles=qqq,
            iwm_candles=iwm,
            breadth_components=breadth,
            prior_close=prior_close,
            economic_event_state=event_state,
            finalization_lag_seconds=0,
            configuration_version=settings.configuration_version,
            strategy_catalog_version=META_STRATEGY_STRATEGY_CATALOG_VERSION,
        )
        return MetaStrategyDecisionWorkerContext(
            event=event,
            settings=settings,
            market_snapshot_request=request,
            inventory_snapshot=inventory,
            account_snapshot=account,
            global_risk_snapshot=global_risk,
            event_state=event_state,
            operational_health=operational_health,
            active_model_artifact=artifact,
        )

    def _settings(self, settings_version: str) -> tuple[MetaStrategySettings, tuple[str, ...]]:
        try:
            return self.settings_store.get_settings(settings_version), ("meta_strategy.state_provider.settings_version_loaded",)
        except KeyError:
            disabled = build_meta_strategy_settings(
                settings_version=settings_version,
                local_risk={"risk_percentage": 0.0, "trade_count_limit": 0, "allow_long": False, "allow_short": False},
                position_sizing={"position_cap": 0.0, "maximum_share_quantity": 0},
                paper_execution={"enabled": False, "local_diagnostics_only": True},
                ml_inference={"mode": "DISABLED", "fallback_behavior": "NO_TRADE"},
            )
            return disabled, ("meta_strategy.state_provider.settings_version_missing",)

    def _active_settings_version(self) -> str | None:
        try:
            return self.settings_store.get_active_settings().settings_version
        except Exception:
            return None

    def _paper_control(self, *, capital_partition_id: str) -> dict[str, Any]:
        if self.job_repository is None:
            return {
                "algorithmId": ALGORITHM_ID,
                "capitalPartitionId": capital_partition_id,
                "newPaperEntriesEnabled": False,
                "automaticPaperTradingEnabled": False,
                "paperEntriesAllowed": False,
                "paperOnly": True,
                "liveExecutionEnabled": False,
                "available": False,
                "reasonCodes": (
                    "meta_strategy.paper_control.state_unavailable",
                    "meta_strategy.paper_control.new_entry_blocked_at_decision",
                ),
            }
        try:
            record = self.job_repository.read_paper_trading_control(capital_partition_id=capital_partition_id)
        except Exception:
            record = None
        if record is None:
            return {
                "algorithmId": ALGORITHM_ID,
                "capitalPartitionId": capital_partition_id,
                "newPaperEntriesEnabled": False,
                "automaticPaperTradingEnabled": False,
                "paperEntriesAllowed": False,
                "paperOnly": True,
                "liveExecutionEnabled": False,
                "available": False,
                "reasonCodes": (
                    "meta_strategy.paper_control.state_unavailable",
                    "meta_strategy.paper_control.new_entry_blocked_at_decision",
                ),
            }
        payload = record.to_dict()
        return {**payload, "available": True, "reasonCodes": tuple(payload.get("reasonCodes") or ())}

    def _candles(self, symbol: str, timeframe: str, *, end: datetime, limit: int) -> tuple[MetaStrategySnapshotCandle, ...]:
        query_end = end.isoformat().replace("+00:00", "Z")
        rows = self.candle_store.latest_until(
            symbol=symbol.upper(),
            timeframe=timeframe,
            feed=self.feed,
            limit=limit + 2,
            end=query_end,
        )
        candles = tuple(_snapshot_candle(row) for row in rows)
        return tuple(candle for candle in candles if _bar_end(candle) <= end)[-limit:]

    def _market_data_quality(
        self,
        one_minute: tuple[MetaStrategySnapshotCandle, ...],
        five_minute: tuple[MetaStrategySnapshotCandle, ...],
        fifteen_minute: tuple[MetaStrategySnapshotCandle, ...],
        bar_end: datetime,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if not one_minute:
            return ("meta_strategy.state_provider.one_minute_candles_missing",)
        if len(one_minute) < self.minimum_warmup:
            reasons.append("meta_strategy.state_provider.warmup_history_missing")
        if _bar_end(one_minute[-1]) != bar_end:
            reasons.append("meta_strategy.state_provider.latest_one_minute_bar_mismatch")
        if not _sequence_complete(one_minute, timedelta(minutes=1)):
            reasons.append("meta_strategy.state_provider.one_minute_sequence_gap")
        if five_minute and any(_bar_end(candle) > bar_end for candle in five_minute):
            reasons.append("meta_strategy.state_provider.future_five_minute_bar_rejected")
        if fifteen_minute and any(_bar_end(candle) > bar_end for candle in fifteen_minute):
            reasons.append("meta_strategy.state_provider.future_fifteen_minute_bar_rejected")
        return tuple(reasons)

    def _quote(self, symbol: str, bar_end: datetime) -> tuple[MetaStrategySnapshotQuote | None, dict[str, Any]]:
        payload = _call_quote_source(self.quote_source, symbol=symbol.upper(), at=bar_end)
        if payload is None:
            return None, {"reasonCodes": ("meta_strategy.state_provider.latest_quote_missing",)}
        try:
            timestamp = _quote_timestamp(payload)
            quote = MetaStrategySnapshotQuote(
                timestamp=timestamp,
                bid=float(_first(payload, "bid", "bp", "bidPrice", "bid_price") or 0.0),
                ask=float(_first(payload, "ask", "ap", "askPrice", "ask_price") or 0.0),
                symbol=symbol.upper(),
                provider=str(payload.get("provider") or payload.get("source") or "read_only_quote_source"),
            )
        except Exception:
            return None, {"reasonCodes": ("meta_strategy.state_provider.latest_quote_invalid",)}
        age = (bar_end - quote.timestamp).total_seconds()
        if quote.timestamp > bar_end:
            return None, {"reasonCodes": ("meta_strategy.state_provider.latest_quote_after_bar_end",)}
        if age > self.quote_max_age_seconds:
            return None, {"reasonCodes": ("meta_strategy.state_provider.latest_quote_stale",)}
        return quote, {"reasonCodes": ("meta_strategy.state_provider.latest_quote_loaded",), "quoteAgeSeconds": age}

    def _previous_day_close(self, symbol: str, bar_end: datetime) -> float | None:
        local_day = bar_end.astimezone(EXCHANGE_TIMEZONE).date()
        rows = self.candle_store.range(
            symbol=symbol.upper(),
            timeframe="1Min",
            feed=self.feed,
            end=bar_end.isoformat().replace("+00:00", "Z"),
        )
        prior = [
            _snapshot_candle(row)
            for row in rows
            if _snapshot_candle(row).timestamp.astimezone(EXCHANGE_TIMEZONE).date() < local_day and _bar_end(_snapshot_candle(row)) <= bar_end
        ]
        return prior[-1].close if prior else None

    def _inventory_snapshot_at(
        self,
        *,
        capital_partition_id: str,
        settings_version: str,
        symbol: str,
        bar_end: datetime,
        mark_price: float | None,
    ) -> dict[str, Any]:
        rows_by_type = {
            record_type: _inventory_records_at(
                self.inventory_repository,
                record_type,
                capital_partition_id=capital_partition_id,
                at=bar_end,
                limit=500,
            )
            for record_type in (
                "order_intents",
                "orders",
                "fills",
                "risk_reservations",
                "allocated_capital",
                "reconciliation_checkpoints",
            )
        }
        open_lots, realised = _lots_and_realised(rows_by_type["fills"])
        positions = _positions_from_lots(open_lots, mark_prices={symbol.upper(): mark_price} if mark_price is not None else {})
        reserved = round(sum(_reserved_risk_delta(row) for row in rows_by_type["risk_reservations"]), 10)
        allocated = _latest_float(rows_by_type["allocated_capital"], "allocatedCapital", "allocated_capital")
        daily_trade_count = sum(
            1
            for fill in rows_by_type["fills"]
            if str(fill.get("side") or "").upper() == "SELL" and _parse_timestamp(str(fill["timestamp"])).astimezone(EXCHANGE_TIMEZONE).date() == bar_end.astimezone(EXCHANGE_TIMEZONE).date()
        )
        pending_intents = tuple(row for row in rows_by_type["order_intents"] if str(row.get("status") or "").upper() not in TERMINAL_ORDER_STATUSES)
        open_orders = tuple(row for row in rows_by_type["orders"] if str(row.get("status") or "").upper() not in TERMINAL_ORDER_STATUSES)
        latest_checkpoint = rows_by_type["reconciliation_checkpoints"][-1] if rows_by_type["reconciliation_checkpoints"] else None
        last_trade_at = _last_trade_timestamp(rows_by_type["fills"])
        return {
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": capital_partition_id,
            "settingsVersion": settings_version,
            "snapshotId": f"meta_strategy.inventory.pit.{capital_partition_id}.{bar_end.isoformat()}",
            "pointInTimeCutoff": bar_end.isoformat(),
            "rebuiltFromLedger": True,
            "currentVirtualPositions": positions,
            "positions": positions,
            "positionLots": open_lots,
            "pendingOrderIntents": pending_intents,
            "submittedAndOpenOrders": open_orders,
            "openOrders": open_orders,
            "fills": tuple(rows_by_type["fills"]),
            "reservedRiskLedger": tuple(rows_by_type["risk_reservations"]),
            "reservedRiskDollars": reserved,
            "remainingRiskDollars": None,
            "remainingRiskSource": "meta_strategy_inventory_risk_ledger_unavailable",
            "allocatedCapital": allocated,
            "realizedPnl": round(realised, 10),
            "realisedPnl": round(realised, 10),
            "unrealizedPnl": round(sum(float(row["unrealisedPnl"]) for row in positions), 10),
            "dailyTradeCount": daily_trade_count,
            "daily_trade_count": daily_trade_count,
            "lastTradeAt": last_trade_at,
            "strategyExposure": {"meta_strategy": round(sum(abs(float(row["quantity"]) * float(row["marketPrice"])) for row in positions), 10)},
            "symbolExposure": {row["symbol"]: round(abs(float(row["quantity"]) * float(row["marketPrice"])), 10) for row in positions},
            "lastSignalAndCooldownState": self._last_signal_state(bar_end, capital_partition_id),
            "exitManagementState": {
                "latestReconciliationCheckpoint": latest_checkpoint,
                "managedPositionCount": len(positions),
            },
        }

    def _last_signal_state(self, bar_end: datetime, capital_partition_id: str) -> dict[str, Any]:
        repository = self.job_repository
        if repository is None or not hasattr(repository, "connect"):
            return {"source": "meta_strategy_decision_ledger_unavailable", "lastSignal": None, "cooldownActive": False}
        with repository.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM meta_strategy_worker_decisions
                WHERE algorithm_id = ? AND bar_end <= ?
                ORDER BY bar_end DESC, processing_timestamp DESC
                LIMIT 1
                """,
                (ALGORITHM_ID, bar_end.isoformat()),
            ).fetchone()
        if row is None:
            return {"source": "meta_strategy_decision_ledger", "lastSignal": None, "cooldownActive": False}
        payload = json.loads(str(row["payload_json"]))
        candidate = ((payload.get("stages") or {}).get("aggregateCandidate") or {})
        signal = (candidate.get("deterministicCandidate") or {}).get("direction") or candidate.get("direction")
        return {"source": "meta_strategy_decision_ledger", "lastSignal": signal, "lastDecisionId": payload.get("decisionId"), "cooldownActive": False}

    def _account_snapshot(self, at: datetime) -> dict[str, Any]:
        payload = _call_reader(self.account_source, "read_account_snapshot", at=at)
        if payload is None:
            return {
                "source": "read_only_account_view_unavailable",
                "authoritativeReadOnly": False,
                "accountEquity": None,
                "buyingPower": None,
                "reasonCodes": ("meta_strategy.state_provider.account_snapshot_missing",),
            }
        captured_at = _payload_time(payload, at)
        if captured_at > at:
            return {"source": "read_only_account_view", "authoritativeReadOnly": False, "reasonCodes": ("meta_strategy.state_provider.account_snapshot_after_bar_end",)}
        return {
            "source": str(payload.get("source") or "read_only_account_view"),
            "authoritativeReadOnly": True,
            "capturedAt": captured_at.isoformat(),
            "accountEquity": _float_or_none(_first(payload, "accountEquity", "account_equity", "equity")),
            "buyingPower": _float_or_none(_first(payload, "buyingPower", "buying_power")),
            "cashAvailable": _float_or_none(_first(payload, "cashAvailable", "cash_available", "cash")),
            "reasonCodes": ("meta_strategy.state_provider.account_snapshot_loaded",),
        }

    def _global_risk_snapshot(self, at: datetime, *, capital_partition_id: str) -> dict[str, Any]:
        payload = _call_global_risk(self.global_risk_source, at=at, capital_partition_id=capital_partition_id)
        if payload is None:
            return {
                "source": "read_only_global_risk_unavailable",
                "authoritativeReadOnly": False,
                "availableRiskDollars": 0.0,
                "maxQuantity": 0,
                "reject": True,
                "reasonCodes": ("meta_strategy.state_provider.global_risk_snapshot_missing",),
            }
        captured_at = _payload_time(payload, at)
        if captured_at > at:
            return {"source": "read_only_global_risk", "authoritativeReadOnly": False, "reject": True, "reasonCodes": ("meta_strategy.state_provider.global_risk_snapshot_after_bar_end",)}
        return {
            "source": str(payload.get("source") or "read_only_global_risk"),
            "authoritativeReadOnly": True,
            "capturedAt": captured_at.isoformat(),
            "availableRiskDollars": (
                _float_or_none(_first(payload, "availableRiskDollars", "available_risk_dollars"))
                if _float_or_none(_first(payload, "availableRiskDollars", "available_risk_dollars")) is not None
                else 0.0
            ),
            "maxQuantity": (
                int(_first(payload, "maxQuantity", "max_quantity", "globalQuantityCap", "global_quantity_cap"))
                if _first(payload, "maxQuantity", "max_quantity", "globalQuantityCap", "global_quantity_cap") is not None
                else 0
            ),
            "reject": bool(payload.get("reject") or payload.get("rejected") or payload.get("tradingHalt") or payload.get("trading_halt")),
            "tradingHalt": bool(payload.get("tradingHalt") or payload.get("trading_halt")),
            "reasonCodes": tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ("meta_strategy.state_provider.global_risk_snapshot_loaded",)),
        }

    def _market_calendar_snapshot(self, at: datetime) -> dict[str, Any]:
        try:
            normalized = read_market_clock_snapshot(self.market_clock_source or self.operational_health_source, evaluated_at=at)
        except Exception:
            normalized = None
        if normalized is None:
            return {
                "source": "authoritative_market_clock_unavailable",
                "capturedAt": at.isoformat(),
                "dataSourceTimestamp": at.isoformat(),
                "sessionPhase": "UNKNOWN",
                "isOpen": False,
                "authoritativeReadOnly": False,
                "fresh": False,
                "canAuthorizeNewEntries": False,
                "reasonCodes": ("meta_strategy.state_provider.authoritative_market_clock_missing",),
            }
        session = meta_strategy_session_at(at)
        payload = normalized.as_dict()
        reasons = tuple(payload.get("reasonCodes") or ())
        if payload.get("authoritativeReadOnly") is not True or payload.get("fresh") is not True:
            reasons = (*reasons, "meta_strategy.state_provider.market_clock_not_authorizing")
        return {
            **payload,
            "sessionPhase": session.value,
            "reasonCodes": tuple(dict.fromkeys((*reasons, "meta_strategy.state_provider.authoritative_market_clock_loaded" if payload.get("authoritativeReadOnly") is True else "meta_strategy.state_provider.authoritative_market_clock_degraded"))),
        }

    def _operational_health(self, at: datetime) -> dict[str, Any]:
        payload = _call_reader(self.operational_health_source, "read_operational_health", at=at)
        if payload is None:
            return {
                "source": "meta_strategy_worker_local_health",
                "status": "OK",
                "brokerConnected": True,
                "dataConnected": True,
                "tradingAllowed": True,
                "capturedAt": at.isoformat(),
                "reasonCodes": ("meta_strategy.state_provider.operational_health_local",),
            }
        captured_at = _payload_time(payload, at)
        status = str(payload.get("status") or "OK").upper()
        return {
            "source": str(payload.get("source") or "read_only_operational_health"),
            "status": "BLOCKED" if captured_at > at else status,
            "brokerConnected": bool(payload.get("brokerConnected", payload.get("broker_connected", True))),
            "dataConnected": bool(payload.get("dataConnected", payload.get("data_connected", True))),
            "tradingAllowed": bool(payload.get("tradingAllowed", payload.get("trading_allowed", True))) and captured_at <= at,
            "capturedAt": min(captured_at, at).isoformat(),
            "reasonCodes": ("meta_strategy.state_provider.operational_health_loaded",) if captured_at <= at else ("meta_strategy.state_provider.operational_health_after_bar_end",),
        }

    def _runtime_health(self, at: datetime) -> dict[str, Any]:
        payload = _gateway_snapshot_at(self.job_repository, "meta_strategy.runtime.readiness", at)
        if payload is None:
            return {
                "source": "meta_strategy_runtime_readiness_unavailable",
                "authoritativeReadOnly": False,
                "enabled": False,
                "ready": False,
                "mode": "PAPER",
                "paperOrdersBlocked": True,
                "capturedAt": at.isoformat(),
                "reasonCodes": ("meta_strategy.state_provider.runtime_health_missing_or_not_ready",),
            }
        captured_at = _payload_time(payload, at)
        ready = payload.get("ready") is True and str(payload.get("mode") or "").upper() == "PAPER" and payload.get("paperOrdersBlocked") is not True
        return {
            **dict(payload),
            "source": str(payload.get("source") or "meta_strategy.runtime.readiness"),
            "authoritativeReadOnly": captured_at <= at,
            "ready": ready and captured_at <= at,
            "capturedAt": min(captured_at, at).isoformat(),
            "reasonCodes": (
                "meta_strategy.state_provider.runtime_health_loaded"
                if ready and captured_at <= at
                else "meta_strategy.state_provider.runtime_health_missing_or_not_ready"
            ),
        }

    def _operational_controls(self, at: datetime) -> dict[str, Any]:
        controls: dict[str, Any] = {}
        reasons: list[str] = []
        for name in ("PAUSE_NEW_ENTRIES", "EXIT_ONLY", "STOP_META_RUNTIME"):
            key = f"meta_strategy.controls.{name}"
            payload = _gateway_snapshot_at(self.job_repository, key, at)
            if payload is None:
                controls[name] = {"available": False, "active": False, "capturedAt": at.isoformat()}
                reasons.append(f"meta_strategy.state_provider.control_{name.lower()}_unavailable")
                continue
            state = payload.get("state") if isinstance(payload.get("state"), Mapping) else payload
            state_dict = dict(state) if isinstance(state, Mapping) else {}
            captured_at = _payload_time(payload, at)
            active = captured_at <= at and (
                state_dict.get("newEntriesPaused") is True
                or state_dict.get("exitOnly") is True
                or state_dict.get("runtimeStopRequested") is True
                or state_dict.get("paperOrdersBlocked") is True
            )
            controls[name] = {
                **state_dict,
                "available": captured_at <= at,
                "active": active,
                "capturedAt": min(captured_at, at).isoformat(),
            }
            reasons.append(f"meta_strategy.state_provider.control_{name.lower()}_loaded" if captured_at <= at else f"meta_strategy.state_provider.control_{name.lower()}_after_bar_end")
        return {"source": "meta_strategy.operational_controls", "controls": controls, "reasonCodes": tuple(reasons)}

    def _economic_event_state(self, symbol: str, at: datetime) -> dict[str, Any]:
        payload = _call_economic(self.economic_event_source, symbol=symbol.upper(), at=at)
        if payload is None:
            return {"state": "none", "severity": "none", "active": False, "capturedAt": at.isoformat(), "source": "economic_event_source_unavailable"}
        captured_at = _payload_time(payload, at)
        if captured_at > at:
            return {"state": "unknown", "severity": "unknown", "active": True, "capturedAt": at.isoformat(), "source": "economic_event_source_future_rejected"}
        return {**dict(payload), "capturedAt": captured_at.isoformat(), "source": str(payload.get("source") or "read_only_economic_event_source")}

    def _active_or_shadow_model_artifact(self, at: datetime) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        repository = self.job_repository
        if repository is None or not hasattr(repository, "active_model_pointer") or not hasattr(repository, "connect"):
            return None, {"reasonCodes": ("meta_strategy.state_provider.model_shadow_only",), "missing": True}
        pointer = repository.active_model_pointer()
        artifact_id = str(pointer.get("modelArtifactId") or "shadow-only")
        if artifact_id == "shadow-only":
            return None, {"reasonCodes": ("meta_strategy.state_provider.model_shadow_only",), "missing": True}
        with repository.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, created_at FROM meta_strategy_workflow_artifacts
                WHERE algorithm_id = ? AND artifact_id = ? AND created_at <= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (ALGORITHM_ID, artifact_id, at.isoformat()),
            ).fetchone()
        if row is None:
            return None, {"reasonCodes": ("meta_strategy.state_provider.active_model_artifact_not_point_in_time",), "missing": True}
        payload = json.loads(str(row["payload_json"]))
        candidate = _artifact_candidate(payload)
        try:
            loaded = load_runtime_model_artifact_data(dict(candidate), expected_feature_schema_hash=meta_strategy_feature_schema_hash())
            return dict(loaded.payload), {"reasonCodes": ("meta_strategy.state_provider.active_model_artifact_loaded",), "missing": False}
        except Exception as exc:
            return None, {"reasonCodes": ("meta_strategy.state_provider.active_model_artifact_incompatible", str(exc)), "missing": True}

    def _optional_feature_missingness(
        self,
        *,
        qqq: tuple[MetaStrategySnapshotCandle, ...],
        iwm: tuple[MetaStrategySnapshotCandle, ...],
        breadth: Mapping[str, tuple[MetaStrategySnapshotCandle, ...]],
        prior_close: float | None,
        artifact: Mapping[str, Any] | None,
    ) -> dict[str, bool]:
        return {
            "qqqRelativeStrength": not bool(qqq),
            "iwmRelativeStrength": not bool(iwm),
            "marketBreadthInputs": not bool(breadth),
            "previousDayHighLow": prior_close is None,
            "premarketHighLow": False,
            "recentSwingLevels": False,
            "economicEventState": self.economic_event_source is None,
            "modelArtifact": artifact is None,
        }

MetaStrategyCandleStoreStateProvider = MetaStrategyAuthoritativeDecisionStateProvider


def _snapshot_candle(row: Mapping[str, Any]) -> MetaStrategySnapshotCandle:
    return MetaStrategySnapshotCandle(
        timestamp=_parse_timestamp(str(row["timestamp"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0.0),
        symbol=str(row["symbol"]).upper(),
        timeframe=str(row["timeframe"]),
        provider=str(row.get("provider") or "market_data"),
    )


def _settings_warmup(settings: MetaStrategySettings) -> int:
    warmups = [int(item.minimum_warmup) for item in settings.directional_strategies.values()]
    return max(warmups, default=0)


def _bar_end(candle: MetaStrategySnapshotCandle) -> datetime:
    return candle.timestamp + _timeframe_duration(candle.timeframe)


def _timeframe_duration(timeframe: str) -> timedelta:
    normalized = timeframe.lower()
    if normalized in {"5min", "5m"}:
        return timedelta(minutes=5)
    if normalized in {"15min", "15m"}:
        return timedelta(minutes=15)
    return timedelta(minutes=1)


def _sequence_complete(candles: tuple[MetaStrategySnapshotCandle, ...], step: timedelta) -> bool:
    return all((right.timestamp - left.timestamp) == step for left, right in zip(candles, candles[1:]))


def _inventory_records_at(
    repository: MetaStrategySqliteRepository,
    record_type: str,
    *,
    capital_partition_id: str,
    at: datetime,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    rows = repository.inventory_records(record_type, limit=limit)
    eligible = [
        row
        for row in rows
        if str(row.get("capitalPartitionId") or "") == capital_partition_id and _parse_timestamp(str(row["timestamp"])) <= at
    ]
    return tuple(sorted(eligible, key=lambda row: (str(row["timestamp"]), str(row["recordId"]))))


def _reserved_risk_delta(row: Mapping[str, Any]) -> float:
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    value = payload.get("reservedRiskDelta")
    if value is None:
        value = payload.get("reserved_risk_delta")
    if value is None:
        value = payload.get("reservedRiskDollars")
    if value is None:
        value = payload.get("reserved_risk_dollars")
    return float(value) if value is not None else 0.0


def _last_trade_timestamp(fills: tuple[dict[str, Any], ...]) -> str | None:
    timestamps = [
        _parse_timestamp(str(fill["timestamp"]))
        for fill in fills
        if fill.get("timestamp") is not None
    ]
    return max(timestamps).isoformat() if timestamps else None


def _lots_and_realised(fills: tuple[dict[str, Any], ...]) -> tuple[tuple[dict[str, Any], ...], float]:
    lots: list[dict[str, Any]] = []
    realised = 0.0
    for fill in fills:
        side = str(fill.get("side") or "").upper()
        quantity_value = fill.get("quantity")
        price_value = fill.get("price")
        qty = abs(float(quantity_value) if quantity_value is not None else 0.0)
        price = float(price_value) if price_value is not None else 0.0
        if qty <= 0.0:
            continue
        if side == "BUY":
            lots.append(
                {
                    "lotId": f"meta_strategy.lot.{fill.get('brokerFillId') or fill.get('recordId')}",
                    "symbol": str(fill.get("symbol") or "").upper(),
                    "side": "LONG",
                    "quantity": qty,
                    "averagePrice": price,
                    "openedAt": str(fill.get("timestamp")),
                    "orderIntentId": str(fill.get("orderIntentId") or ""),
                    "brokerFillId": str(fill.get("brokerFillId") or ""),
                    "settingsVersion": str(fill.get("settingsVersion") or ""),
                    "capitalPartitionId": str(fill.get("capitalPartitionId") or ""),
                    "correlationId": str(fill.get("correlationId") or ""),
                }
            )
        elif side == "SELL":
            remaining = qty
            for lot in lots:
                if remaining <= 0.0 or lot["symbol"] != str(fill.get("symbol") or "").upper() or lot["quantity"] <= 0.0:
                    continue
                consumed = min(float(lot["quantity"]), remaining)
                realised += (price - float(lot["averagePrice"])) * consumed
                lot["quantity"] = round(float(lot["quantity"]) - consumed, 10)
                remaining = round(remaining - consumed, 10)
            if remaining > 0.0:
                lots.append(
                    {
                        "lotId": f"meta_strategy.short_lot.{fill.get('brokerFillId') or fill.get('recordId')}",
                        "symbol": str(fill.get("symbol") or "").upper(),
                        "side": "SHORT",
                        "quantity": remaining,
                        "averagePrice": price,
                        "openedAt": str(fill.get("timestamp")),
                        "orderIntentId": str(fill.get("orderIntentId") or ""),
                        "brokerFillId": str(fill.get("brokerFillId") or ""),
                        "settingsVersion": str(fill.get("settingsVersion") or ""),
                        "capitalPartitionId": str(fill.get("capitalPartitionId") or ""),
                        "correlationId": str(fill.get("correlationId") or ""),
                    }
                )
    return tuple(lot for lot in lots if abs(float(lot["quantity"])) > 1e-9), realised


def _mark_price(candles: tuple[MetaStrategySnapshotCandle, ...], quote: MetaStrategySnapshotQuote | None) -> float | None:
    if candles:
        return candles[-1].close
    if quote is not None:
        return (quote.bid + quote.ask) / 2.0
    return None


def _positions_from_lots(lots: tuple[dict[str, Any], ...], *, mark_prices: Mapping[str, float]) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for lot in lots:
        grouped.setdefault(str(lot["symbol"]).upper(), []).append(lot)
    positions = []
    for symbol, symbol_lots in sorted(grouped.items()):
        signed = sum(float(lot["quantity"]) if lot["side"] == "LONG" else -float(lot["quantity"]) for lot in symbol_lots)
        if abs(signed) <= 1e-9:
            continue
        total_abs = sum(float(lot["quantity"]) for lot in symbol_lots)
        average = sum(float(lot["quantity"]) * float(lot["averagePrice"]) for lot in symbol_lots) / total_abs if total_abs else 0.0
        mark = float(mark_prices.get(symbol, average))
        side = "LONG" if signed > 0 else "SHORT"
        unrealised = (mark - average) * abs(signed) if side == "LONG" else (average - mark) * abs(signed)
        first = symbol_lots[0]
        positions.append(
            {
                "positionId": f"meta_strategy.position.{first.get('capitalPartitionId')}.{symbol}",
                "symbol": symbol,
                "side": side,
                "quantity": round(abs(signed), 10),
                "averagePrice": round(average, 10),
                "marketPrice": round(mark, 10),
                "unrealisedPnl": round(unrealised, 10),
                "unrealizedPnl": round(unrealised, 10),
                "capitalPartitionId": first.get("capitalPartitionId"),
                "settingsVersion": first.get("settingsVersion"),
                "correlationId": first.get("correlationId"),
            }
        )
    return tuple(positions)


def _latest_float(rows: tuple[dict[str, Any], ...], *keys: str) -> float:
    if not rows:
        return 0.0
    payload = rows[-1].get("payload") or {}
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return 0.0


def _artifact_candidate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = dict(payload.get("payload") or payload)
    for key in ("candidateArtifact", "candidate_artifact", "modelArtifact", "model_artifact", "artifact"):
        if isinstance(data.get(key), Mapping):
            return data[key]
    return data


def _call_quote_source(source: Any | None, *, symbol: str, at: datetime) -> Mapping[str, Any] | None:
    if source is None:
        return None
    if hasattr(source, "read_quote"):
        result = source.read_quote(symbol=symbol, at=at)
    elif hasattr(source, "read_market_snapshot"):
        result = source.read_market_snapshot(symbol=symbol, at=at)
        result = (result or {}).get("quote") if isinstance(result, Mapping) else result
    elif hasattr(source, "get_latest_quote_sync"):
        result = source.get_latest_quote_sync(symbol=symbol, feed="iex")
    else:
        return None
    return dict(result) if isinstance(result, Mapping) else None


def _call_reader(source: Any | None, method_name: str, *, at: datetime) -> Mapping[str, Any] | None:
    if source is None or not hasattr(source, method_name):
        return None
    result = getattr(source, method_name)(at=at)
    return dict(result) if isinstance(result, Mapping) else None


def _call_global_risk(source: Any | None, *, at: datetime, capital_partition_id: str) -> Mapping[str, Any] | None:
    if source is None:
        return None
    if hasattr(source, "read_global_risk_snapshot"):
        result = source.read_global_risk_snapshot(at=at, capital_partition_id=capital_partition_id)
    elif hasattr(source, "read_snapshot"):
        result = source.read_snapshot(at=at, capital_partition_id=capital_partition_id)
    else:
        return None
    return dict(result) if isinstance(result, Mapping) else None


def _call_market_clock(source: Any | None, *, at: datetime) -> Mapping[str, Any] | None:
    if source is None:
        return None
    for method_name in ("read_market_clock", "get_market_clock", "get_clock", "market_clock"):
        method = getattr(source, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(at=at)
        except TypeError:
            result = method()
        return dict(result) if isinstance(result, Mapping) else None
    return None


def _call_economic(source: Any | None, *, symbol: str, at: datetime) -> Mapping[str, Any] | None:
    if source is None or not hasattr(source, "read_economic_event_state"):
        return None
    result = source.read_economic_event_state(symbol=symbol, at=at)
    return dict(result) if isinstance(result, Mapping) else None


def _payload_time(payload: Mapping[str, Any], default: datetime) -> datetime:
    value = _first(payload, "capturedAt", "captured_at", "timestamp", "asOf", "as_of")
    return _parse_timestamp(str(value)) if value else default


def _clock_is_open(payload: Mapping[str, Any]) -> bool | None:
    for key in ("isOpen", "is_open", "marketOpen", "market_open"):
        if key in payload:
            return bool(payload[key])
    status = str(payload.get("status") or payload.get("state") or "").lower()
    if status in {"open", "regular", "regular_session"}:
        return True
    if status in {"closed", "pre_market", "post_market", "halted"}:
        return False
    return None


def _quote_timestamp(payload: Mapping[str, Any]) -> datetime:
    value = _first(payload, "quoteTimestamp", "quote_timestamp", "timestamp", "t")
    if not value:
        raise ValueError("quote timestamp missing")
    return _parse_timestamp(str(value))


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _gateway_snapshot_at(repository: Any | None, key: str, at: datetime) -> dict[str, Any] | None:
    if repository is None or not hasattr(repository, "read_gateway_snapshot"):
        return None
    try:
        payload = repository.read_gateway_snapshot(key)
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    captured_at = _payload_time(payload, at)
    if captured_at > at:
        return {**dict(payload), "capturedAt": captured_at.isoformat(), "metaStrategyRejectedFutureSnapshot": True}
    return dict(payload)


def _source_timestamps(
    *,
    settings: MetaStrategySettings,
    inventory: Mapping[str, Any],
    account: Mapping[str, Any],
    global_risk: Mapping[str, Any],
    quote: MetaStrategySnapshotQuote | None,
    market_calendar: Mapping[str, Any],
    operational: Mapping[str, Any],
    runtime_health: Mapping[str, Any],
    paper_control: Mapping[str, Any],
    economic: Mapping[str, Any],
    artifact: Mapping[str, Any] | None,
    bar_end: datetime,
) -> dict[str, Any]:
    return {
        "decisionCutoff": bar_end.isoformat(),
        "settingsCreatedAt": _timestamp_string(getattr(settings, "created_at", None)),
        "inventoryPointInTimeCutoff": inventory.get("pointInTimeCutoff"),
        "lastTradeAt": inventory.get("lastTradeAt"),
        "accountCapturedAt": account.get("capturedAt"),
        "globalRiskCapturedAt": global_risk.get("capturedAt"),
        "quoteTimestamp": quote.timestamp.isoformat() if quote is not None else None,
        "marketClockCapturedAt": market_calendar.get("capturedAt"),
        "operationalHealthCapturedAt": operational.get("capturedAt"),
        "runtimeHealthCapturedAt": runtime_health.get("capturedAt"),
        "paperControlUpdatedAt": paper_control.get("updatedAt"),
        "economicEventCapturedAt": economic.get("capturedAt"),
        "modelArtifactCreatedAt": (artifact or {}).get("createdAt") or (artifact or {}).get("created_at"),
    }


def _source_versions(
    *,
    settings: MetaStrategySettings,
    event_settings_version: str,
    artifact: Mapping[str, Any] | None,
    snapshot_request_versions: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "eventSettingsVersion": event_settings_version,
        "loadedSettingsVersion": settings.settings_version,
        "activeSettingsVersion": snapshot_request_versions.get("activeSettingsVersion"),
        "effectiveSettingsHash": settings.effective_settings_hash,
        "configurationVersion": settings.configuration_version,
        "strategyCatalogVersion": snapshot_request_versions.get("strategyCatalogVersion"),
        "featureSchemaVersion": snapshot_request_versions.get("featureSchemaVersion"),
        "modelArtifactId": (artifact or {}).get("modelArtifactId") or (artifact or {}).get("artifactId"),
        "modelVersion": (artifact or {}).get("modelVersion") or (artifact or {}).get("model_version"),
    }


def _timestamp_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _any_new_entry_control_active(operational_controls: Mapping[str, Any]) -> bool:
    controls = operational_controls.get("controls") if isinstance(operational_controls.get("controls"), Mapping) else {}
    return any(
        isinstance(state, Mapping) and state.get("active") is True
        for state in controls.values()
    )


def _capital_partition_id(event: MetaStrategyFinalisedBarDecisionEvent) -> str:
    return str(getattr(event, "capital_partition_id", META_STRATEGY_DEFAULT_CAPITAL_PARTITION) or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    parsed = value if value.tzinfo is not None and value.utcoffset() is not None else value.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class _DatabaseSettings:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url


def _database_settings() -> _DatabaseSettings:
    return _DatabaseSettings(os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"))


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, time(9, 30), tzinfo=EXCHANGE_TIMEZONE)
    end = datetime.combine(session_date, time(16, 0), tzinfo=EXCHANGE_TIMEZONE)
    return start.astimezone(UTC), end.astimezone(UTC)


__all__ = [
    "MetaStrategyAuthoritativeDecisionStateProvider",
    "MetaStrategyCandleStoreStateProvider",
    "MetaStrategyReadOnlyAccountSource",
    "MetaStrategyReadOnlyEconomicEventSource",
    "MetaStrategyReadOnlyGlobalRiskSource",
    "MetaStrategyReadOnlyOperationalHealthSource",
    "MetaStrategyReadOnlyQuoteSource",
]
