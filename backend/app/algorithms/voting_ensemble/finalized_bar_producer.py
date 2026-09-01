"""Authoritative finalized one-minute bar producer for Voting Ensemble."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.algorithms.voting_ensemble.event_calendar import (
    event_calendar_from_payload,
    resolve_event_veto,
)
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol

_EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")


from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.market_feed import active_instrument
from backend.app.algorithms.voting_ensemble.session_segments import (
    resolve_session_segment,
    session_profile_for_instrument,
    session_segment_boundaries_from_payload,
)
from backend.app.algorithms.voting_ensemble.snapshot.builder import build_point_in_time_snapshot
from backend.app.algorithms.voting_ensemble.runtime.commands import (
    VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
    VotingEnsembleRuntimeCommand,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings


VOTING_ENSEMBLE_FINALIZED_BAR_PRODUCER_VERSION = "voting_ensemble_finalized_bar_producer_v1"
VOTING_ENSEMBLE_FINALIZED_BAR_EVENT_STORE_VERSION = "voting_ensemble_finalized_bar_event_store_v1"
VOTING_ENSEMBLE_MARKET_EVENT_CONTRACT_VERSION = "voting_ensemble_market_bar_finalized_event_v1"
VOTING_ENSEMBLE_DEFAULT_SYMBOL = "SPY"
VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY = "voting_ensemble.local_paper_account"


class VotingEnsembleMarketDataClient(Protocol):
    async def get_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        limit: int,
        start: str | None,
        end: str | None,
        sort: str,
    ) -> list[dict[str, Any]]:
        ...


class VotingEnsembleCandleStore(Protocol):
    def upsert_many(self, candles: list[dict]) -> None:
        ...

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict]:
        ...


class VotingEnsembleFinalizedBarMarketEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1)
    eventType: Literal["market.bar.finalized"] = "market.bar.finalized"
    symbol: str = Field(min_length=1)
    timeframe: Literal["1Min"] = "1Min"
    barStartTimestamp: datetime
    barEndTimestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    feed: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    receivedAt: datetime
    finalizedAt: datetime
    sourceAuthority: str = Field(min_length=1)
    eventContractVersion: str = VOTING_ENSEMBLE_MARKET_EVENT_CONTRACT_VERSION

    @model_validator(mode="after")
    def validate_finalized_one_minute_bar(self) -> "VotingEnsembleFinalizedBarMarketEvent":
        start = _utc(self.barStartTimestamp)
        end = _utc(self.barEndTimestamp)
        finalized = _utc(self.finalizedAt)
        received = _utc(self.receivedAt)
        if self.symbol.upper() != VOTING_ENSEMBLE_DEFAULT_SYMBOL:
            raise ValueError("Voting Ensemble finalized market events must be for SPY")
        if end - start != timedelta(minutes=1):
            raise ValueError("Voting Ensemble finalized market events must describe exactly one complete minute")
        if finalized < end:
            raise ValueError("Voting Ensemble finalized market events cannot finalize before bar end")
        if received < end:
            raise ValueError("Voting Ensemble finalized market events cannot be received before bar end")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("Voting Ensemble finalized market event OHLC geometry is invalid")
        return self

    def snapshot(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["symbol"] = self.symbol.upper()
        payload["timeframe"] = "1Min"
        return payload


@dataclass(frozen=True)
class VotingEnsembleFinalizedBarProducerConfig:
    symbols: tuple[str, ...] = (VOTING_ENSEMBLE_DEFAULT_SYMBOL,)
    auxiliary_symbols: tuple[str, ...] = ("QQQ", "IWM", "XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC")
    feed: str = "iex"
    timeframe: str = "1Min"
    fetch_limit: int = 450
    history_limit: int = 390
    poll_seconds: float = 5.0
    finalization_delay_seconds: int = 2
    decision_deadline_seconds: int = 20
    source_authority: str = "backend.alpaca.finalized_bar_producer"


@dataclass(frozen=True)
class VotingEnsembleFinalizedBarProductionResult:
    algorithmId: str
    eventId: str | None
    status: str
    accepted: bool
    duplicate: bool
    stale: bool
    jobId: str | None = None
    barEndTimestamp: str | None = None
    reasonCodes: tuple[str, ...] = ()
    event: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithmId,
            "producerVersion": VOTING_ENSEMBLE_FINALIZED_BAR_PRODUCER_VERSION,
            "eventId": self.eventId,
            "status": self.status,
            "accepted": self.accepted,
            "duplicate": self.duplicate,
            "stale": self.stale,
            "jobId": self.jobId,
            "barEndTimestamp": self.barEndTimestamp,
            "reasonCodes": list(self.reasonCodes),
            "event": self.event,
            "receipt": self.receipt,
        }


class VotingEnsembleFinalizedBarEventStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else default_finalized_bar_event_store_path()
        self._lock = Lock()
        self._receipts: list[dict[str, Any]] = []
        self._dedup_index: dict[str, str] = {}
        self._load()

    def claim_event(
        self,
        event: VotingEnsembleFinalizedBarMarketEvent,
        *,
        settings_hash: str,
        received_at: datetime,
        decision_deadline_seconds: int,
    ) -> tuple[dict[str, Any], bool]:
        dedup_key = finalized_bar_dedup_key(event, settings_hash=settings_hash)
        stale = _utc(received_at) > _utc(event.finalizedAt) + timedelta(seconds=decision_deadline_seconds)
        with self._lock:
            duplicate_of = self._dedup_index.get(dedup_key)
            status = "stale" if stale else "duplicate" if duplicate_of else "accepted"
            receipt = {
                "algorithmId": "voting_ensemble",
                "eventStoreVersion": VOTING_ENSEMBLE_FINALIZED_BAR_EVENT_STORE_VERSION,
                "eventId": event.eventId,
                "eventType": event.eventType,
                "symbol": event.symbol.upper(),
                "timeframe": event.timeframe,
                "barStartTimestamp": _iso(event.barStartTimestamp),
                "barEndTimestamp": _iso(event.barEndTimestamp),
                "settingsHash": settings_hash,
                "resultContractVersion": VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
                "dedupKey": dedup_key,
                "duplicateOfEventId": duplicate_of,
                "status": status,
                "accepted": status == "accepted",
                "duplicate": status == "duplicate",
                "stale": status == "stale",
                "receivedAt": _iso(received_at),
                "recordedAt": _now(),
                "event": event.snapshot(),
                "reasonCodes": [f"voting_ensemble.finalized_bar_event.{status}"],
            }
            if status == "accepted":
                self._dedup_index[dedup_key] = event.eventId
            self._receipts.append(receipt)
            self._receipts = self._receipts[-500:]
            self._save_unlocked()
            return dict(receipt), status == "accepted"

    def mark_enqueued(self, event_id: str, job: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            for index in range(len(self._receipts) - 1, -1, -1):
                if self._receipts[index].get("eventId") != event_id:
                    continue
                updated = {
                    **self._receipts[index],
                    "status": "enqueued" if job.get("accepted") else "duplicate" if job.get("deduplicated") else "blocked",
                    "jobId": job.get("jobId"),
                    "commandId": job.get("commandId"),
                    "accepted": bool(job.get("accepted")),
                    "duplicate": bool(job.get("deduplicated")),
                    "updatedAt": _now(),
                    "reasonCodes": list(job.get("reasonCodes") or self._receipts[index].get("reasonCodes") or []),
                }
                self._receipts[index] = updated
                self._save_unlocked()
                return dict(updated)
        return None

    def summary(self) -> dict[str, Any]:
        with self._lock:
            latest = self._receipts[-1] if self._receipts else None
            counts: dict[str, int] = {}
            for receipt in self._receipts:
                status = str(receipt.get("status") or "unknown")
                counts[status] = counts.get(status, 0) + 1
            return {
                "algorithmId": "voting_ensemble",
                "eventStoreVersion": VOTING_ENSEMBLE_FINALIZED_BAR_EVENT_STORE_VERSION,
                "receiptCount": len(self._receipts),
                "statusCounts": counts,
                "latestReceipt": dict(latest) if latest else None,
            }

    def receipts(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(receipt) for receipt in self._receipts)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        receipts = payload.get("receipts") if isinstance(payload, dict) else None
        if not isinstance(receipts, list):
            return
        self._receipts = [dict(receipt) for receipt in receipts if isinstance(receipt, dict)]
        self._dedup_index = {
            str(receipt["dedupKey"]): str(receipt["eventId"])
            for receipt in self._receipts
            if receipt.get("dedupKey") and receipt.get("eventId") and receipt.get("status") in {"accepted", "enqueued"}
        }

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {
                "algorithmId": "voting_ensemble",
                "eventStoreVersion": VOTING_ENSEMBLE_FINALIZED_BAR_EVENT_STORE_VERSION,
                "receipts": self._receipts,
            },
            sort_keys=True,
            indent=2,
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)
        except PermissionError:
            self.path.write_text(encoded, encoding="utf-8")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class VotingEnsembleFinalizedBarProducer:
    def __init__(
        self,
        *,
        market_data_client: VotingEnsembleMarketDataClient,
        candle_store: VotingEnsembleCandleStore,
        publish_event: Callable[[VotingEnsembleFinalizedBarMarketEvent, str, int], dict[str, Any]],
        event_store: VotingEnsembleFinalizedBarEventStore | None = None,
        config: VotingEnsembleFinalizedBarProducerConfig | None = None,
        settings_hash_provider: Callable[[], str] | None = None,
    ) -> None:
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.publish_event = publish_event
        self.event_store = event_store or VotingEnsembleFinalizedBarEventStore()
        self.config = config or VotingEnsembleFinalizedBarProducerConfig()
        self.settings_hash_provider = settings_hash_provider or (lambda: "voting_ensemble_default_settings")

    async def poll_once(self, *, now: datetime | None = None) -> tuple[dict[str, Any], ...]:
        current = _utc(now or datetime.now(UTC))
        results = []
        for symbol in self.config.auxiliary_symbols:
            await self._refresh_symbol_history(symbol, now=current)
        for symbol in self.config.symbols:
            results.append((await self.process_symbol(symbol, now=current)).to_dict())
        return tuple(results)

    async def process_symbol(self, symbol: str, *, now: datetime | None = None) -> VotingEnsembleFinalizedBarProductionResult:
        current = _utc(now or datetime.now(UTC))
        normalized_symbol = symbol.upper()
        valid = await self._refresh_symbol_history(normalized_symbol, now=current)
        finalized = [row for row in valid if _is_complete_one_minute_bar(row, now=current, finalization_delay_seconds=self.config.finalization_delay_seconds)]
        if not finalized:
            return VotingEnsembleFinalizedBarProductionResult(
                algorithmId="voting_ensemble",
                eventId=None,
                status="blocked",
                accepted=False,
                duplicate=False,
                stale=False,
                reasonCodes=("voting_ensemble.finalized_bar_event.no_complete_one_minute_bar",),
            )
        candle = finalized[-1]
        event = finalized_market_event_from_candle(
            candle,
            sequence=len(finalized),
            received_at=current,
            finalized_at=_bar_end(candle) + timedelta(seconds=self.config.finalization_delay_seconds),
            source_authority=self.config.source_authority,
        )
        settings_hash = self.settings_hash_provider()
        receipt, accepted = self.event_store.claim_event(
            event,
            settings_hash=settings_hash,
            received_at=current,
            decision_deadline_seconds=self.config.decision_deadline_seconds,
        )
        if not accepted:
            return VotingEnsembleFinalizedBarProductionResult(
                algorithmId="voting_ensemble",
                eventId=event.eventId,
                status=str(receipt["status"]),
                accepted=False,
                duplicate=bool(receipt["duplicate"]),
                stale=bool(receipt["stale"]),
                barEndTimestamp=_iso(event.barEndTimestamp),
                reasonCodes=tuple(receipt.get("reasonCodes") or ()),
                event=event.snapshot(),
                receipt=receipt,
            )
        job = self.publish_event(event, settings_hash, self.config.decision_deadline_seconds)
        updated_receipt = self.event_store.mark_enqueued(event.eventId, job) or receipt
        return VotingEnsembleFinalizedBarProductionResult(
            algorithmId="voting_ensemble",
            eventId=event.eventId,
            status="enqueued" if job.get("accepted") else "blocked",
            accepted=bool(job.get("accepted")),
            duplicate=bool(job.get("deduplicated")),
            stale=False,
            jobId=str(job.get("jobId")) if job.get("jobId") else None,
            barEndTimestamp=_iso(event.barEndTimestamp),
            reasonCodes=tuple(job.get("reasonCodes") or updated_receipt.get("reasonCodes") or ()),
            event=event.snapshot(),
            receipt=updated_receipt,
        )

    async def _refresh_symbol_history(self, symbol: str, *, now: datetime) -> list[dict[str, Any]]:
        normalized_symbol = symbol.upper()
        try:
            rows = await self.market_data_client.get_bars(
                symbol=normalized_symbol,
                timeframe=self.config.timeframe,
                feed=self.config.feed,
                limit=self.config.fetch_limit,
                start=None,
                end=now.isoformat(),
                sort="asc",
            )
        except Exception:
            return self._cached_symbol_history(normalized_symbol, now=now, limit=self.config.history_limit)
        valid = [_normalize_candle(row, symbol=symbol.upper(), timeframe=self.config.timeframe, feed=self.config.feed) for row in rows]
        valid = [row for row in valid if row is not None]
        if valid:
            self.candle_store.upsert_many(valid)
        return valid

    def _cached_symbol_history(self, symbol: str, *, now: datetime, limit: int) -> list[dict[str, Any]]:
        try:
            cached = self.candle_store.latest_until(
                symbol=symbol.upper(),
                timeframe=self.config.timeframe,
                feed=self.config.feed,
                limit=limit,
                end=now.isoformat(),
            )
        except Exception:
            return []
        valid = [_normalize_candle(row, symbol=symbol.upper(), timeframe=self.config.timeframe, feed=self.config.feed) for row in cached]
        return [row for row in valid if row is not None]


class VotingEnsembleAutomaticEvaluationPayloadBuilder:
    forbidden_caller_authoritative_fields = frozenset(
        {
            "accountRiskSnapshot",
            "positionSnapshot",
            "inventorySnapshot",
            "operationalHealthSnapshot",
            "globalGateDecision",
            "upstreamGlobalGateDecision",
            "automaticPaperTradingEnabled",
            "marketOpen",
            "tradingEnabled",
        }
    )

    def __init__(
        self,
        *,
        candle_store: VotingEnsembleCandleStore,
        control_snapshot_provider: Callable[[], dict[str, Any]],
        paper_inventory_provider: Callable[[], dict[str, Any]],
        market_status_provider: Callable[[], dict[str, Any]] | None = None,
        account_snapshot_provider: Callable[[], dict[str, Any] | None] | None = None,
        quote_provider: Callable[..., dict[str, Any] | None] | None = None,
        last_trade_provider: Callable[..., dict[str, Any] | None] | None = None,
        global_risk_provider: Callable[..., dict[str, Any] | None] | None = None,
        breadth_symbols: tuple[str, ...] = ("XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC"),
        history_limit: int = 390,
        feed: str = "iex",
        max_quote_age_seconds: float = 5.0,
        max_trade_age_seconds: float = 10.0,
        max_auxiliary_age_seconds: float = 90.0,
    ) -> None:
        self.candle_store = candle_store
        self.control_snapshot_provider = control_snapshot_provider
        self.paper_inventory_provider = paper_inventory_provider
        self.market_status_provider = market_status_provider
        self.account_snapshot_provider = account_snapshot_provider
        self.quote_provider = quote_provider
        self.last_trade_provider = last_trade_provider
        self.global_risk_provider = global_risk_provider
        self.breadth_symbols = tuple(symbol.upper() for symbol in breadth_symbols)
        self.history_limit = history_limit
        self.feed = feed
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_trade_age_seconds = max_trade_age_seconds
        self.max_auxiliary_age_seconds = max_auxiliary_age_seconds

    def build(self, command: VotingEnsembleRuntimeCommand) -> dict[str, Any]:
        raw_event = command.payload.get("marketEvent") if isinstance(command.payload, dict) else None
        event = VotingEnsembleFinalizedBarMarketEvent.model_validate(raw_event or command.payload)
        feed = event.feed or self.feed
        observation_time = _utc(event.receivedAt)
        failures: list[str] = []
        stale: list[str] = []
        malformed: list[str] = []

        candles = self._load_candles("SPY", feed, self.history_limit, event, failures, mandatory=True)
        five_minute = _aggregate_completed_bars(candles, 5, _utc(event.barEndTimestamp))
        fifteen_minute = _aggregate_completed_bars(candles, 15, _utc(event.barEndTimestamp))
        if not five_minute:
            failures.append("voting_ensemble.automatic_snapshot.missing_completed_spy_five_minute_candles")
        if not fifteen_minute:
            failures.append("voting_ensemble.automatic_snapshot.missing_completed_spy_fifteen_minute_candles")

        qqq = self._load_candles("QQQ", feed, 240, event, failures, mandatory=True)
        iwm = self._load_candles("IWM", feed, 240, event, failures, mandatory=True)
        breadth_components = {
            symbol: self._load_candles(symbol, feed, 240, event, failures, mandatory=True)
            for symbol in self.breadth_symbols
        }
        _require_synchronized_latest("QQQ", qqq, event, failures, stale, self.max_auxiliary_age_seconds)
        _require_synchronized_latest("IWM", iwm, event, failures, stale, self.max_auxiliary_age_seconds)
        for symbol, component in breadth_components.items():
            _require_synchronized_latest(symbol, component, event, failures, stale, self.max_auxiliary_age_seconds)

        control = self.control_snapshot_provider()
        inventory = self.paper_inventory_provider()
        try:
            market_status = self.market_status_provider() if self.market_status_provider is not None else {}
        except Exception:
            market_status = {}
        if not isinstance(market_status, dict) or "isOpen" not in market_status:
            failures.append("voting_ensemble.automatic_snapshot.backend_market_status_missing")
            market_status = {"isOpen": False, "status": "unknown", "reasonCodes": ["voting_ensemble.automatic_snapshot.backend_market_status_missing"]}

        try:
            account_snapshot = self.account_snapshot_provider() if self.account_snapshot_provider is not None else None
        except Exception:
            account_snapshot = None
        account_risk = _account_snapshot_from_backend_account(account_snapshot, inventory, event)
        if account_risk.get("sourceAuthority") != VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY:
            failures.append("voting_ensemble.automatic_snapshot.local_paper_account_snapshot_missing")

        quote = _call_market_provider(self.quote_provider, symbol=event.symbol.upper(), feed=feed)
        trade = _call_market_provider(self.last_trade_provider, symbol=event.symbol.upper(), feed=feed)
        # The quote and last trade are fetched here, at the end of a snapshot build that
        # first loads SPY, QQQ, IWM and every breadth component. That takes seconds, so
        # anchoring the point-in-time cutoff to the bar-arrival time marks legitimately
        # fresh market data as future-dated and fail-closes every evaluation.
        #
        # The cutoff is instead the latest moment any input was observed. That keeps
        # point-in-time integrity (nothing in the snapshot post-dates the decision) and
        # stays deterministic for replay and tests, unlike a wall-clock reading. The
        # feed-sanity guard survives too: a quote whose own timestamp runs ahead of the
        # receipt it arrived on is still rejected below.
        market_data_observed_at = _latest_observation(observation_time, quote, trade)
        nbbo = _nbbo_from_quote_and_trade(
            quote,
            trade,
            event=event,
            observation_time=market_data_observed_at,
            max_quote_age_seconds=self.max_quote_age_seconds,
            max_trade_age_seconds=self.max_trade_age_seconds,
            failures=failures,
            stale=stale,
            malformed=malformed,
        )
        breadth_feed = _breadth_feed_from_components(breadth_components, event, observation_time)
        daily_counters = _daily_counters_from_inventory(inventory, event)
        settings = resolve_one_minute_trading_settings({})
        try:
            global_gate = (
                self.global_risk_provider(event=event.snapshot(), control=control, accountRiskSnapshot=account_risk, inventory=inventory)
                if self.global_risk_provider is not None
                else None
            )
        except Exception:
            global_gate = None
        if not isinstance(global_gate, dict):
            global_gate = _server_global_gate(event, control, market_status)

        market_forecast = _market_forecast_context(candles)
        ignored_fields = _ignored_authoritative_fields(command.payload, self.forbidden_caller_authoritative_fields)
        synchronization = {
            "algorithmId": "voting_ensemble",
            "snapshotSynchronized": not failures and not stale and not malformed,
            "mandatoryFailures": list(dict.fromkeys(failures)),
            "staleInputs": list(dict.fromkeys(stale)),
            "malformedInputs": list(dict.fromkeys(malformed)),
            "ignoredCallerAuthoritativeFields": ignored_fields,
            "barEndTimestamp": _iso(event.barEndTimestamp),
            "observedAt": _iso(observation_time),
            "reasonCodes": [
                "voting_ensemble.automatic_snapshot.synchronized"
                if not failures and not stale and not malformed
                else "voting_ensemble.automatic_snapshot.fail_closed",
            ],
        }
        operational = _operational_snapshot(
            control,
            market_status,
            inventory,
            global_gate,
            synchronization,
            entry_window_open=_entry_window_open(
                market_open=bool(market_status.get("isOpen")),
                settings=settings,
                bar_end=_utc(event.barEndTimestamp),
            ),
        )
        context = {
            "settingsHash": command.settingsHash,
            "settingsVersion": settings.settingsVersion,
            "settings": settings.model_dump(mode="json"),
            "marketEvent": event.snapshot(),
            "sourceAuthority": event.sourceAuthority,
            "sessionState": _session_state(market_status, settings=settings, bar_end=_utc(event.barEndTimestamp)),
            # The economic-event key the snapshot builder reads. Without it _event_state
            # produced an empty dict, so the blackout gate and the 32 event policies could
            # never fire on the automatic path no matter what was on the calendar.
            "event": _event_veto_state(command, settings, event),
            "marketForecast": market_forecast,
            "backendMarketStatus": market_status,
            "accountRiskSnapshot": account_risk,
            "operationalHealthSnapshot": operational,
            "globalGateDecision": global_gate,
            "upstreamGlobalGateDecision": global_gate,
            "authoritativeInventory": inventory,
            "votingEnsembleInventory": inventory,
            "votingEnsembleDailyCounters": daily_counters,
            "dataFreshnessAndSynchronization": synchronization,
            "priorDayOHLC": _prior_day_levels(candles, event),
            "premarket": _premarket_levels(candles, event),
            "openingRange": _opening_range_levels(candles),
        }
        payload = {
            "symbol": event.symbol.upper(),
            "data_timestamp": _iso(market_data_observed_at),
            "candles": candles,
            "spy_5m_candles": five_minute,
            "spy_15m_candles": fifteen_minute,
            "qqq_candles": qqq,
            "iwm_candles": iwm,
            "breadth_components": breadth_components,
            "market_context": context,
            "external_breadth_feed": breadth_feed,
            "nbbo": nbbo,
        }
        try:
            snapshot = build_point_in_time_snapshot(payload)
            evaluate_payload = snapshot.to_evaluate_payload()
        except Exception as exc:
            failures.append("voting_ensemble.automatic_snapshot.point_in_time_snapshot_malformed")
            synchronization["mandatoryFailures"] = list(dict.fromkeys(failures))
            synchronization["malformedInputs"] = list(dict.fromkeys([*malformed, str(exc)]))
            synchronization["snapshotSynchronized"] = False
            raise VotingEnsembleAutomaticSnapshotError(
                "Voting Ensemble automatic snapshot is malformed.",
                ["voting_ensemble.automatic_snapshot.fail_closed", *synchronization["mandatoryFailures"]],
                snapshot={"dataFreshnessAndSynchronization": synchronization, "marketEvent": event.snapshot()},
            ) from exc

        snapshot_payload = snapshot.model_dump(mode="json")
        automatic_snapshot = {
            **snapshot_payload,
            "automaticSnapshotVersion": "voting_ensemble_backend_automatic_snapshot_v1",
            "immutable": True,
            "marketEvent": event.snapshot(),
            "freshSpyQuote": quote,
            "freshSpyLastTrade": trade,
            "backendMarketStatus": market_status,
            "backendAccountSnapshot": account_risk,
            "localPaperAccountSnapshot": account_risk,
            "votingEnsembleInventory": inventory,
            "votingEnsembleDailyCounters": daily_counters,
            "sharedReadOnlyGlobalRiskDecision": global_gate,
            "settingsVersion": settings.settingsVersion,
            "settingsHash": command.settingsHash,
            "dataFreshnessAndSynchronization": synchronization,
        }
        evaluate_context = dict(evaluate_payload.get("market_context") or {})
        evaluate_context.update(
            {
                "automaticRuntimeSnapshot": automatic_snapshot,
                "marketEvent": event.snapshot(),
                "sourceAuthority": event.sourceAuthority,
                "backendMarketStatus": market_status,
                "backendAccountSnapshot": account_risk,
                "localPaperAccountSnapshot": account_risk,
                "votingEnsembleInventory": inventory,
                "votingEnsembleDailyCounters": daily_counters,
                "sharedReadOnlyGlobalRiskDecision": global_gate,
                "dataFreshnessAndSynchronization": synchronization,
                "ignoredCallerAuthoritativeFields": ignored_fields,
                "settingsVersion": settings.settingsVersion,
            }
        )
        evaluate_context["operationalHealthSnapshot"] = {
            **dict(evaluate_context.get("operationalHealthSnapshot") or {}),
            **operational,
            "pointInTimeSnapshotHash": snapshot.snapshotHash,
        }
        evaluate_payload["market_context"] = evaluate_context
        evaluate_payload["data_timestamp"] = _iso(snapshot.evaluationTimestamp)
        evaluate_payload["nbbo"] = nbbo
        evaluate_payload["runtimeMode"] = "automatic_finalized_bar"
        evaluate_payload["brokerSubmissionAllowed"] = True
        if not snapshot.dataReadiness.ready or failures or stale or malformed:
            reason_codes = [
                "voting_ensemble.automatic_snapshot.fail_closed",
                *list(snapshot.dataReadiness.mandatoryFailures),
                *list(snapshot.dataReadiness.staleInputs),
                *list(snapshot.dataReadiness.malformedInputs),
                *synchronization["mandatoryFailures"],
                *synchronization["staleInputs"],
                *synchronization["malformedInputs"],
            ]
            raise VotingEnsembleAutomaticSnapshotError(
                "Voting Ensemble automatic snapshot failed mandatory freshness and synchronization checks.",
                list(dict.fromkeys(reason_codes)),
                snapshot=automatic_snapshot,
            )
        return evaluate_payload

    def _load_candles(
        self,
        symbol: str,
        feed: str,
        limit: int,
        event: VotingEnsembleFinalizedBarMarketEvent,
        failures: list[str],
        *,
        mandatory: bool,
    ) -> list[dict[str, Any]]:
        rows = self.candle_store.latest_until(
            symbol=symbol.upper(),
            timeframe="1Min",
            feed=feed,
            limit=limit,
            end=_iso(event.barStartTimestamp),
        )
        candles = [_candle_payload(row, finalized_at=event.finalizedAt) for row in rows]
        latest_timestamp = _utc(_parse_timestamp(candles[-1]["timestamp"])) if candles else None
        if mandatory and latest_timestamp != _utc(event.barStartTimestamp):
            failures.append(f"voting_ensemble.automatic_snapshot.{symbol.lower()}_finalized_one_minute_candle_missing_or_unsynchronized")
        return candles


class VotingEnsembleAutomaticSnapshotError(RuntimeError):
    def __init__(self, message: str, reason_codes: list[str] | tuple[str, ...], *, snapshot: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason_codes = list(reason_codes)
        self.snapshot = snapshot


def finalized_market_event_from_candle(
    candle: dict[str, Any],
    *,
    sequence: int,
    received_at: datetime,
    finalized_at: datetime,
    source_authority: str,
) -> VotingEnsembleFinalizedBarMarketEvent:
    start = _parse_timestamp(candle["timestamp"])
    end = start + timedelta(minutes=1)
    payload = {
        "eventId": deterministic_finalized_bar_event_id(
            symbol=str(candle.get("symbol") or VOTING_ENSEMBLE_DEFAULT_SYMBOL),
            timeframe="1Min",
            bar_end=end,
            feed=str(candle.get("feed") or "iex"),
            source_authority=source_authority,
        ),
        "eventType": "market.bar.finalized",
        "symbol": str(candle.get("symbol") or VOTING_ENSEMBLE_DEFAULT_SYMBOL).upper(),
        "timeframe": "1Min",
        "barStartTimestamp": _iso(start),
        "barEndTimestamp": _iso(end),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": int(candle.get("volume") or 0),
        "feed": str(candle.get("feed") or "iex"),
        "sequence": sequence,
        "receivedAt": _iso(received_at),
        "finalizedAt": _iso(finalized_at),
        "sourceAuthority": source_authority,
    }
    return VotingEnsembleFinalizedBarMarketEvent.model_validate(payload)


def deterministic_finalized_bar_event_id(*, symbol: str, timeframe: str, bar_end: datetime, feed: str, source_authority: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "eventType": "market.bar.finalized",
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "barEndTimestamp": _iso(bar_end),
                "feed": feed,
                "sourceAuthority": source_authority,
                "eventContractVersion": VOTING_ENSEMBLE_MARKET_EVENT_CONTRACT_VERSION,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"ve-finalized-bar-{digest}"


def finalized_bar_dedup_key(event: VotingEnsembleFinalizedBarMarketEvent, *, settings_hash: str) -> str:
    payload = {
        "symbol": event.symbol.upper(),
        "timeframe": event.timeframe,
        "barEndTimestamp": _iso(event.barEndTimestamp),
        "settingsHash": settings_hash,
        "resultContractVersion": VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def default_finalized_bar_event_store_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "finalized_bar_events.json"


def _is_complete_one_minute_bar(candle: dict[str, Any], *, now: datetime, finalization_delay_seconds: int) -> bool:
    if str(candle.get("timeframe") or "") != "1Min":
        return False
    return _bar_end(candle) + timedelta(seconds=finalization_delay_seconds) <= _utc(now)


def _bar_end(candle: dict[str, Any]) -> datetime:
    return _parse_timestamp(candle["timestamp"]) + timedelta(minutes=1)


def _normalize_candle(row: dict[str, Any], *, symbol: str, timeframe: str, feed: str) -> dict[str, Any] | None:
    try:
        candle = {
            "provider": str(row.get("provider") or "alpaca"),
            "feed": str(row.get("feed") or feed),
            "symbol": str(row.get("symbol") or symbol).upper(),
            "timeframe": str(row.get("timeframe") or timeframe),
            "timestamp": _iso(_parse_timestamp(row["timestamp"])),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row.get("volume") or 0),
            "trade_count": row.get("trade_count"),
            "vwap": row.get("vwap"),
        }
    except Exception:
        return None
    if candle["timeframe"] != "1Min":
        return None
    return candle


def _candle_payload(row: dict[str, Any], *, finalized_at: datetime | None = None) -> dict[str, Any]:
    payload = {
        "timestamp": _iso(_parse_timestamp(row["timestamp"])),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": int(row.get("volume") or 0),
    }
    if finalized_at is not None:
        payload["finalizationTimestamp"] = _iso(finalized_at)
    return payload


def _aggregate_completed_bars(candles: list[dict[str, Any]], minutes: int, event_end: datetime) -> list[dict[str, Any]]:
    by_start = {_utc(_parse_timestamp(candle["timestamp"])): candle for candle in candles}
    completed: list[dict[str, Any]] = []
    for row_start in sorted(by_start):
        candidate_end = row_start + timedelta(minutes=1)
        if candidate_end > _utc(event_end) or not _aligned_time(candidate_end, minutes):
            continue
        group_start = candidate_end - timedelta(minutes=minutes)
        expected = [group_start + timedelta(minutes=index) for index in range(minutes)]
        if not all(timestamp in by_start for timestamp in expected):
            continue
        group = [by_start[timestamp] for timestamp in expected]
        completed.append(
            {
                "timestamp": group[-1]["timestamp"],
                "open": group[0]["open"],
                "high": max(float(item["high"]) for item in group),
                "low": min(float(item["low"]) for item in group),
                "close": group[-1]["close"],
                "volume": sum(int(item.get("volume") or 0) for item in group),
                "finalizationTimestamp": group[-1].get("finalizationTimestamp"),
            }
        )
    return completed


def _aligned_time(value: datetime, minutes: int) -> bool:
    return value.second == 0 and value.microsecond == 0 and (value.hour * 60 + value.minute) % minutes == 0


def _require_synchronized_latest(
    symbol: str,
    candles: list[dict[str, Any]],
    event: VotingEnsembleFinalizedBarMarketEvent,
    failures: list[str],
    stale: list[str],
    max_age_seconds: float,
) -> None:
    if not candles:
        return
    latest = _utc(_parse_timestamp(candles[-1]["timestamp"]))
    if latest > _utc(event.barStartTimestamp):
        failures.append(f"voting_ensemble.automatic_snapshot.{symbol.lower()}_future_candle")
        return
    age = (_utc(event.barStartTimestamp) - latest).total_seconds()
    if age > max_age_seconds:
        stale.append(f"voting_ensemble.automatic_snapshot.{symbol.lower()}_stale_or_unsynchronized")


def _call_market_provider(provider: Callable[..., dict[str, Any] | None] | None, **kwargs: Any) -> dict[str, Any] | None:
    if provider is None:
        return None
    try:
        value = provider(**kwargs)
    except TypeError:
        try:
            value = provider(kwargs.get("symbol"), kwargs.get("feed"))
        except Exception:
            return None
    except Exception:
        return None
    return dict(value) if isinstance(value, dict) else None


def _ignored_authoritative_fields(payload: Any, forbidden: frozenset[str]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    ignored = {key for key in forbidden if key in payload}
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    ignored.update(f"market_context.{key}" for key in forbidden if key in context)
    return sorted(ignored)


def _nbbo_from_quote_and_trade(
    quote: dict[str, Any] | None,
    trade: dict[str, Any] | None,
    *,
    event: VotingEnsembleFinalizedBarMarketEvent,
    observation_time: datetime,
    max_quote_age_seconds: float,
    max_trade_age_seconds: float,
    failures: list[str],
    stale: list[str],
    malformed: list[str],
) -> dict[str, Any] | None:
    if not isinstance(quote, dict):
        failures.append("voting_ensemble.automatic_snapshot.spy_quote_missing")
        return None
    if not isinstance(trade, dict):
        failures.append("voting_ensemble.automatic_snapshot.spy_last_trade_missing")
        return None
    bid = _positive_float(quote.get("bid"))
    ask = _positive_float(quote.get("ask"))
    bid_size = _positive_float(quote.get("bidSize") or quote.get("bid_size"))
    ask_size = _positive_float(quote.get("askSize") or quote.get("ask_size"))
    quote_timestamp = _optional_timestamp(quote.get("quoteTimestamp") or quote.get("timestamp"))
    quote_receipt = _optional_timestamp(quote.get("marketDataReceiptTimestamp") or quote.get("receivedAt"))
    trade_timestamp = _optional_timestamp(trade.get("tradeTimestamp") or trade.get("lastTradeTimestamp") or trade.get("timestamp"))
    trade_receipt = _optional_timestamp(trade.get("marketDataReceiptTimestamp") or trade.get("receivedAt"))
    if None in (bid, ask, bid_size, ask_size, quote_timestamp, quote_receipt, trade_timestamp, trade_receipt):
        malformed.append("voting_ensemble.automatic_snapshot.spy_quote_or_trade_malformed")
        return None
    assert bid is not None and ask is not None and bid_size is not None and ask_size is not None
    assert quote_timestamp is not None and quote_receipt is not None and trade_timestamp is not None and trade_receipt is not None
    if ask < bid:
        malformed.append("voting_ensemble.automatic_snapshot.spy_quote_crossed")
        return None
    tolerance = timedelta(seconds=1)
    if quote_timestamp > observation_time + tolerance or quote_receipt > observation_time + tolerance:
        stale.append("voting_ensemble.automatic_snapshot.future_spy_quote")
    if trade_timestamp > observation_time + tolerance or trade_receipt > observation_time + tolerance:
        stale.append("voting_ensemble.automatic_snapshot.future_spy_last_trade")
    quote_age = (observation_time - quote_timestamp).total_seconds()
    trade_age = (observation_time - trade_timestamp).total_seconds()
    if quote_age > max_quote_age_seconds:
        stale.append("voting_ensemble.automatic_snapshot.stale_spy_quote")
    if trade_age > max_trade_age_seconds:
        stale.append("voting_ensemble.automatic_snapshot.stale_spy_last_trade")
    return {
        **quote,
        "provider": quote.get("provider") or "alpaca",
        "feed": quote.get("feed") or event.feed,
        "symbol": event.symbol.upper(),
        "bid": bid,
        "ask": ask,
        "bidSize": bid_size,
        "askSize": ask_size,
        "quoteTimestamp": _iso(quote_timestamp),
        "lastTradeTimestamp": _iso(trade_timestamp),
        "lastTradePrice": _positive_float(trade.get("price")),
        "lastTradeSize": _positive_float(trade.get("size")),
        "marketDataReceiptTimestamp": _iso(max(quote_receipt, trade_receipt)),
        "maxQuoteAgeSeconds": max_quote_age_seconds,
        "maxReceiptAgeSeconds": max(max_quote_age_seconds, max_trade_age_seconds),
        "source": "backend_authoritative_spy_quote_and_last_trade",
    }


def _breadth_feed_from_components(components: dict[str, list[dict[str, Any]]], event: VotingEnsembleFinalizedBarMarketEvent, observation_time: datetime) -> dict[str, Any]:
    total = 0
    advancing = 0
    for rows in components.values():
        if len(rows) < 2:
            continue
        total += 1
        advancing += 1 if float(rows[-1]["close"]) > float(rows[-2]["close"]) else 0
    percentage = (advancing / total) if total else None
    return {
        "timestamp": _iso(event.barEndTimestamp),
        "providerTimestamp": _iso(event.barEndTimestamp),
        "receiptTimestamp": _iso(observation_time),
        "source": "backend_authoritative_sector_breadth_components",
        "componentCount": total,
        "advancingCount": advancing,
        "percentageAdvancing": percentage,
    }


def _daily_counters_from_inventory(inventory: dict[str, Any], event: VotingEnsembleFinalizedBarMarketEvent) -> dict[str, Any]:
    session = _iso(event.barEndTimestamp)[:10]
    orders = [item for item in inventory.get("orders") or [] if _record_session(item, session)]
    fills = [item for item in inventory.get("fills") or [] if _record_session(item, session)]
    positions = inventory.get("positions") if isinstance(inventory.get("positions"), list) else []
    unrealized = sum(float(position.get("unrealizedPnl") or 0.0) for position in positions if isinstance(position, dict))
    realized = sum(float(fill.get("realizedPnl") or 0.0) for fill in fills if isinstance(fill, dict))
    loss_count = sum(1 for fill in fills if isinstance(fill, dict) and float(fill.get("realizedPnl") or 0.0) < 0.0)
    return {
        "algorithmId": "voting_ensemble",
        "sessionDate": session,
        "tradesToday": len(fills),
        "ordersToday": len(orders),
        "realizedPnlToday": round(realized, 6),
        "unrealizedPnlToday": round(unrealized, 6),
        "dailyNetPnlAfterExitCosts": round(realized + unrealized, 6),
        "dailyLossCount": loss_count,
        "generatedAt": _now(),
    }


def _record_session(record: Any, session: str) -> bool:
    if not isinstance(record, dict):
        return False
    for key in ("filledAt", "submittedAt", "createdAt", "updatedAt"):
        if str(record.get(key) or "").startswith(session):
            return True
    return False


def _account_snapshot_from_backend_account(account: dict[str, Any] | None, inventory: dict[str, Any], event: VotingEnsembleFinalizedBarMarketEvent) -> dict[str, Any]:
    fallback = _account_snapshot_from_inventory(inventory, event)
    if _inventory_has_local_account(inventory):
        return fallback
    if not isinstance(account, dict):
        return fallback
    if _normalized_source_authority(account.get("sourceAuthority")) != VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY:
        return fallback
    equity = _positive_or_zero(account.get("equity") or account.get("portfolio_value") or account.get("portfolioValue"))
    buying_power = _positive_or_zero(account.get("buying_power") or account.get("buyingPower"))
    open_notional = _positive_or_zero(account.get("openPositionNotional") or fallback.get("openPositionNotional"))
    total_open_risk_percent = _positive_or_zero(account.get("totalOpenRiskPercent") or fallback.get("totalOpenRiskPercent"))
    total_spy_notional_percent = _percent(open_notional, equity)
    return {
        **fallback,
        "accountId": str(account.get("id") or account.get("accountId") or fallback["accountId"]),
        "equity": equity,
        "buyingPower": buying_power,
        "realizedPnlToday": float(account.get("realizedPnlToday") or fallback.get("realizedPnlToday") or 0.0),
        "unrealizedPnlToday": float(account.get("unrealizedPnlToday") or account.get("unrealizedPnl") or fallback.get("unrealizedPnlToday") or 0.0),
        "dailyNetPnlAfterExitCosts": float(account.get("dailyNetPnlAfterExitCosts") or account.get("dailyNetPnl") or fallback.get("dailyNetPnlAfterExitCosts") or 0.0),
        "intradayEquityHigh": _positive_or_zero(account.get("intradayEquityHigh") or fallback.get("intradayEquityHigh") or equity),
        "drawdownPercent": _positive_or_zero(account.get("drawdownPercent") or fallback.get("drawdownPercent")),
        "drawdownFromIntradayHighPercent": _positive_or_zero(account.get("drawdownFromIntradayHighPercent") or account.get("drawdownPercent") or fallback.get("drawdownFromIntradayHighPercent")),
        "openPositionNotional": open_notional,
        "totalOpenRiskPercent": total_open_risk_percent,
        "totalSpyNotionalPercent": total_spy_notional_percent,
        "sameDirectionExposurePercent": total_spy_notional_percent,
        "tradesToday": int(account.get("tradesToday") or fallback.get("tradesToday") or 0),
        "observedAt": str(account.get("observedAt") or _iso(event.receivedAt)),
        "sessionDate": _iso(event.barEndTimestamp)[:10],
        "sourceAuthority": VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY,
        "capitalPartitionId": str(account.get("capitalPartitionId") or fallback.get("capitalPartitionId") or "voting_ensemble.paper.default"),
        "paperAccount": True,
        "localPaperAccount": True,
        "externalBrokerAccount": False,
    }


def _event_veto_state(command: Any, settings: Any, event: Any) -> dict[str, Any]:
    """Resolve the scheduled-event veto for this bar into the state the gates read.

    A caller-supplied calendar wins over settings so a replay is judged against the
    calendar as it stood for that bar, not as it stands when the replay runs.
    """
    payload = getattr(command, "payload", None)
    context = payload.get("market_context") if isinstance(payload, dict) and isinstance(payload.get("market_context"), dict) else {}
    configured = None
    for candidate in (
        context.get("eventCalendar") if isinstance(context, dict) else None,
        payload.get("event_calendar") if isinstance(payload, dict) else None,
        getattr(settings, "eventCalendar", None),
    ):
        if isinstance(candidate, dict):
            configured = candidate
            break
    decision = resolve_event_veto(
        bar_end=_utc(event.barEndTimestamp),
        settings=event_calendar_from_payload(configured),
    )
    return decision.as_event_state()


def _entry_window_open(*, market_open: bool, settings: Any, bar_end: datetime) -> bool:
    """Whether a new entry may still be opened on the bar that just closed.

    This used to be market_open, which said nothing the market-open gate did not
    already say, and left the live path taking new entries until the closing bell
    while run_voting_ensemble_backtest stopped at newTradesUntil. The two ran
    different entry rules, so replay understated late-session activity.

    The window is evaluated on the bar's own end timestamp, not the wall clock, so a
    replayed bar is judged by the session it belongs to rather than by when it is
    being processed.
    """
    if not market_open:
        return False
    windows = getattr(settings, "sessionWindows", None)
    opens_at = _minute_of_day(getattr(windows, "sessionStart", None))
    closes_at = _minute_of_day(getattr(windows, "newTradesUntil", None))
    if opens_at is None or closes_at is None:
        # No window resolved. Keep the previous behaviour rather than silently
        # halting every entry on a settings problem: the loss from trading a little
        # late is bounded, an unexplained full stop is not.
        return True
    minute = _new_york_minute_of_day(bar_end)
    return opens_at <= minute <= closes_at


def _minute_of_day(value: Any) -> int | None:
    """Parse an "HH:MM" session boundary into minutes past midnight."""
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    hours, _, minutes = text.partition(":")
    try:
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


def _new_york_minute_of_day(value: datetime) -> int:
    """Exchange-local minute past midnight, DST included."""
    local = _utc(value).astimezone(_EXCHANGE_TIMEZONE)
    return local.hour * 60 + local.minute


def _operational_snapshot(
    control: dict[str, Any],
    market_status: dict[str, Any],
    inventory: dict[str, Any],
    global_gate: dict[str, Any],
    synchronization: dict[str, Any],
    entry_window_open: bool | None = None,
) -> dict[str, Any]:
    market_open = bool(market_status.get("isOpen"))
    synchronized = bool(synchronization.get("snapshotSynchronized"))
    trading_enabled = bool(control.get("newEntriesEnabled")) and market_open and synchronized and bool(global_gate.get("eligible", global_gate.get("status") == "PASS"))
    # The instrument the application is actually on, not the symbol in the payload.
    # Switching the app to MES must stop entries everywhere, not only where a symbol
    # happens to be checked.
    active = active_instrument()
    return {
        "status": "ready" if trading_enabled else "blocked",
        "instrumentTradeable": active.trade_ready,
        "instrumentId": active.instrument_id,
        "instrumentMissingCapabilities": list(active.missing_capabilities),
        "tradingEnabled": trading_enabled,
        "automaticPaperTradingEnabled": bool(control.get("effectivePaperTradingEnabled")) and synchronized,
        "requestedPaperTradingEnabled": bool(control.get("requestedPaperTradingEnabled")),
        "effectivePaperTradingEnabled": bool(control.get("effectivePaperTradingEnabled")) and synchronized,
        "paperTradingMode": True,
        "liveTradingEnabled": False,
        "marketOpen": market_open,
        "entryWindowOpen": market_open if entry_window_open is None else entry_window_open,
        "validSession": market_open,
        "feedDegraded": not synchronized,
        "clockDisagreement": False,
        "executionFailureCooldownActive": False,
        "globalGateDecision": global_gate,
        "upstreamGlobalGateDecision": global_gate,
        "authoritativeInventory": inventory,
        "permissionContractVersion": "voting_ensemble_backend_authoritative_permission_contract_v1",
        "dataFreshnessAndSynchronization": synchronization,
        "decisionAgeSeconds": 0.0,
    }


def _session_state(market_status: dict[str, Any], *, settings: Any = None, bar_end: datetime | None = None) -> dict[str, Any]:
    market_open = bool(market_status.get("isOpen"))
    state = {
        "phase": "regular" if market_open else "closed",
        "marketClosed": not market_open,
        "marketStatus": "open" if market_open else str(market_status.get("status") or "closed"),
        "backendClock": market_status,
    }
    if bar_end is not None:
        # The label the session policy keys on. `phase` is left exactly as it was:
        # the regime classifier reads that key, and repurposing it would change
        # regime output as a side effect of adding a session label.
        # The profile comes from the instrument the app is on: an index future keeps
        # Globex hours, so labelling its 02:00 bar with an equity profile would call a
        # liquid overnight session `premarket` and its maintenance halt tradable.
        profile = session_profile_for_instrument(_active_instrument_or_none())
        state["sessionSegment"] = resolve_session_segment(
            bar_end,
            boundaries=session_segment_boundaries_from_payload(_session_segment_config(settings)),
            profile=profile if profile.name != "equity_rth" else None,
        )
        state["sessionProfile"] = profile.name
    return state


def _active_instrument_or_none() -> Any | None:
    """The active instrument, or None if the registry cannot be consulted."""
    try:
        return active_instrument()
    except Exception:
        return None


def _session_segment_config(settings: Any) -> dict[str, Any] | None:
    """The configured segment boundaries, if the settings carry any."""
    windows = getattr(settings, "sessionWindows", None)
    payload = getattr(windows, "sessionSegments", None) if windows is not None else None
    if payload is None:
        payload = getattr(settings, "sessionSegments", None)
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload if isinstance(payload, dict) else None


def _prior_day_levels(candles: list[dict[str, Any]], event: VotingEnsembleFinalizedBarMarketEvent) -> dict[str, Any]:
    session = _utc(event.barEndTimestamp).date()
    previous = [candle for candle in candles if _utc(_parse_timestamp(candle["timestamp"])).date() < session]
    if not previous:
        return {}
    day = max(_utc(_parse_timestamp(candle["timestamp"])).date() for candle in previous)
    rows = [candle for candle in previous if _utc(_parse_timestamp(candle["timestamp"])).date() == day]
    return {
        "high": max(float(row["high"]) for row in rows),
        "low": min(float(row["low"]) for row in rows),
        "open": float(rows[0]["open"]),
        "close": float(rows[-1]["close"]),
        "source": "backend_authoritative_completed_spy_candles",
    }


def _premarket_levels(candles: list[dict[str, Any]], event: VotingEnsembleFinalizedBarMarketEvent) -> dict[str, Any]:
    session = _utc(event.barEndTimestamp).date()
    rows = [
        candle
        for candle in candles
        if _utc(_parse_timestamp(candle["timestamp"])).date() == session and _utc(_parse_timestamp(candle["timestamp"])).hour < 14
    ]
    if not rows:
        return {}
    return {
        "high": max(float(row["high"]) for row in rows),
        "low": min(float(row["low"]) for row in rows),
        "open": float(rows[0]["open"]),
        "close": float(rows[-1]["close"]),
        "source": "backend_authoritative_completed_spy_candles",
    }


def _opening_range_levels(candles: list[dict[str, Any]]) -> dict[str, Any]:
    opening = candles[:15]
    if not opening:
        return {}
    return {
        "high": max(float(row["high"]) for row in opening),
        "low": min(float(row["low"]) for row in opening),
        "open": float(opening[0]["open"]),
        "close": float(opening[-1]["close"]),
        "source": "backend_authoritative_completed_spy_candles",
    }


def _server_global_gate(event: VotingEnsembleFinalizedBarMarketEvent, control: dict[str, Any], market_status: dict[str, Any]) -> dict[str, Any]:
    market_open = bool(market_status.get("isOpen"))
    eligible = bool(control.get("newEntriesEnabled")) and market_open
    reasons = list(control.get("reasonCodes") or [])
    return {
        "status": "PASS" if eligible else "FAIL",
        "eligible": eligible,
        "dataReady": True,
        "gateResults": [],
        "reasonCodes": ["voting_ensemble.backend_global_gate.passed"] if eligible else ["voting_ensemble.backend_global_gate.blocked", *reasons],
        "explanation": "Backend-authoritative Voting Ensemble runtime control was evaluated for a finalized market event.",
        "checkedAt": _iso(event.receivedAt),
        "sessionDate": _iso(event.barEndTimestamp)[:10],
        "configurationHash": finalized_bar_dedup_key(event, settings_hash=str(control.get("controlVersion") or "runtime_control")),
    }


def _account_snapshot_from_inventory(inventory: dict[str, Any], event: VotingEnsembleFinalizedBarMarketEvent) -> dict[str, Any]:
    account = None
    if isinstance(inventory, dict):
        account = inventory.get("localPaperAccount") or inventory.get("account")
    if isinstance(account, dict):
        equity = _positive_or_zero(account.get("equity"))
        open_notional = _positive_or_zero(account.get("openPositionNotional"))
        total_spy_notional_percent = _percent(open_notional, equity)
        drawdown = _positive_or_zero(account.get("drawdownPercent") or account.get("drawdownFromIntradayHighPercent"))
        return {
            "algorithmId": "voting_ensemble",
            "algorithm_id": "voting_ensemble",
            "capitalPartitionId": str(account.get("capitalPartitionId") or "voting_ensemble.paper.default"),
            "accountId": str(account.get("accountId") or "voting_ensemble.paper.default.account"),
            "equity": equity,
            "buyingPower": _positive_or_zero(account.get("buyingPower")),
            "openPositionNotional": open_notional,
            "realizedPnlToday": float(account.get("realizedPnlToday") or 0.0),
            "unrealizedPnlToday": float(account.get("unrealizedPnlToday") or 0.0),
            "dailyNetPnlAfterExitCosts": float(account.get("dailyNetPnlAfterExitCosts") or account.get("dailyNetPnl") or 0.0),
            "intradayEquityHigh": _positive_or_zero(account.get("intradayEquityHigh") or equity),
            "drawdownPercent": drawdown,
            "drawdownFromIntradayHighPercent": drawdown,
            "totalOpenRiskPercent": _positive_or_zero(account.get("totalOpenRiskPercent")),
            "totalSpyNotionalPercent": total_spy_notional_percent,
            "sameDirectionExposurePercent": total_spy_notional_percent,
            "estimatedExitCosts": 0.0,
            "tradesToday": int(account.get("tradesToday") or 0),
            "observedAt": str(account.get("observedAt") or _iso(event.receivedAt)),
            "sessionDate": str(account.get("sessionDate") or _iso(event.barEndTimestamp)[:10]),
            "sourceAuthority": VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY,
            "paperAccount": True,
            "localPaperAccount": True,
            "externalBrokerAccount": False,
        }
    positions = inventory.get("positions") if isinstance(inventory, dict) else []
    open_notional = 0.0
    if isinstance(positions, list):
        for position in positions:
            if not isinstance(position, dict):
                continue
            open_notional += abs(float(position.get("notional") or 0.0))
    return {
        "algorithmId": "voting_ensemble",
        "algorithm_id": "voting_ensemble",
        "capitalPartitionId": "voting_ensemble.paper.default",
        "accountId": "voting-ensemble-paper-account",
        "equity": 0.0,
        "buyingPower": 0.0,
        "openPositionNotional": open_notional,
        "realizedPnlToday": 0.0,
        "unrealizedPnlToday": 0.0,
        "dailyNetPnlAfterExitCosts": 0.0,
        "intradayEquityHigh": 0.0,
        "drawdownPercent": 0.0,
        "drawdownFromIntradayHighPercent": 0.0,
        "totalOpenRiskPercent": 0.0,
        "totalSpyNotionalPercent": 0.0,
        "sameDirectionExposurePercent": 0.0,
        "estimatedExitCosts": 0.0,
        "tradesToday": len(inventory.get("orders") or []) if isinstance(inventory, dict) else 0,
        "observedAt": _iso(event.receivedAt),
        "sessionDate": _iso(event.barEndTimestamp)[:10],
        "sourceAuthority": "voting_ensemble.local_paper_account.missing",
        "paperAccount": True,
        "localPaperAccount": True,
        "externalBrokerAccount": False,
    }


def _inventory_has_local_account(inventory: dict[str, Any]) -> bool:
    account = None
    if isinstance(inventory, dict):
        account = inventory.get("localPaperAccount") or inventory.get("account")
    return isinstance(account, dict) and bool(account.get("accountId"))


def _normalized_source_authority(value: Any) -> str:
    raw = str(value or "")
    if raw in {VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY, "voting_ensemble_local_paper_account"}:
        return VOTING_ENSEMBLE_LOCAL_ACCOUNT_RISK_SOURCE_AUTHORITY
    return raw


def _percent(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, numerator) / denominator * 100.0, 6)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def _positive_or_zero(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 and math.isfinite(parsed) else 0.0


def _optional_timestamp(value: Any) -> datetime | None:
    try:
        return _parse_timestamp(value)
    except Exception:
        return None


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str):
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError("timestamp is required")


def _market_forecast_context(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Multi-horizon forecast for the snapshot, derived from the SPY candles already loaded.

    Advisory input only: the ensemble consumes it as a context signal, which can nudge an
    existing directional candidate but can never authorise an entry on its own. A forecast
    failure must never fail-close trading, so any error degrades to an empty payload and
    the context module falls back to neutral.
    """
    if not candles:
        return {}
    try:
        from backend.app.market_forecast import MARKET_FORECAST_SERVICE

        forecast = MARKET_FORECAST_SERVICE.predict(list(candles))
    except Exception:
        return {}
    if not isinstance(forecast, dict):
        return {}
    multi = forecast.get("multiHorizonForecast")
    return {
        "status": forecast.get("status"),
        "inferenceStatus": forecast.get("inferenceStatus"),
        "inferencePerformed": bool(forecast.get("inferencePerformed")),
        "horizonMinutes": forecast.get("horizonMinutes"),
        "multiHorizonForecast": multi if isinstance(multi, dict) else {},
        "modelStatus": (forecast.get("model") or {}).get("status") if isinstance(forecast.get("model"), dict) else None,
    }


def _latest_observation(observation_time: datetime, *sources: Any) -> datetime:
    """Latest moment any snapshot input was observed, used as the point-in-time cutoff."""
    latest = observation_time
    for source in sources:
        if not isinstance(source, dict):
            continue
        receipt = _optional_timestamp(source.get("marketDataReceiptTimestamp") or source.get("receivedAt"))
        if receipt is not None and receipt > latest:
            latest = receipt
    return _utc(latest)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return _iso(datetime.now(UTC))
