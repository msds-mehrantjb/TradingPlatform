"""Background publisher for finalized SPY one-minute WCA market events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from backend.app.algorithms.wca.contracts import WcaCandle, WcaMarketSnapshot, WcaQuote
from backend.app.algorithms.wca.runtime_events import (
    WCA_RUNTIME_EVENT_SCHEMA_VERSION,
    WCA_RUNTIME_EVENT_TIMEFRAME,
    WcaFinalizedBarEvent,
    deterministic_finalized_bar_event_id,
)
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeQueueResult, WcaRuntimeRepository


WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION = "wca_finalized_one_minute_event_publisher_v1"
WCA_REQUIRED_COMPLETED_HISTORY_BARS = 70
WCA_DEFAULT_PUBLISHER_FETCH_LIMIT = 180
WCA_DEFAULT_FINALIZATION_DELAY_SECONDS = 2


@dataclass(frozen=True)
class WcaFinalizedOneMinutePublicationResult:
    accepted: bool
    status: str
    event: WcaFinalizedBarEvent | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WcaFinalizedOneMinutePollConfig:
    symbol: str = "SPY"
    feed: str = "iex"
    timeframe: str = WCA_RUNTIME_EVENT_TIMEFRAME
    fetch_limit: int = WCA_DEFAULT_PUBLISHER_FETCH_LIMIT
    warmup_bars: int = WCA_REQUIRED_COMPLETED_HISTORY_BARS
    finalization_delay_seconds: int = WCA_DEFAULT_FINALIZATION_DELAY_SECONDS
    max_queue_depth: int = 200
    max_event_age_seconds: int = 300


@dataclass(frozen=True)
class WcaFinalizedOneMinutePollResult:
    status: str
    reason_codes: tuple[str, ...]
    publications: tuple[WcaFinalizedOneMinutePublicationResult, ...] = ()
    latest_finalized_candle: datetime | None = None

    @property
    def accepted_count(self) -> int:
        return sum(1 for publication in self.publications if publication.accepted)


class WcaMarketDataClient(Protocol):
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

    async def get_latest_quote(self, *, symbol: str, feed: str) -> dict[str, Any] | None:
        ...


class WcaCandleStore(Protocol):
    def upsert_many(self, candles: list[dict[str, Any]]) -> None:
        ...

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict[str, Any]]:
        ...

    def latest(self, *, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict[str, Any]]:
        ...


class WcaFinalizedOneMinuteEventPublisher:
    def __init__(
        self,
        runtime_repository: WcaRuntimeRepository,
        *,
        market_data_client: WcaMarketDataClient | None = None,
        candle_store: WcaCandleStore | None = None,
        config: WcaFinalizedOneMinutePollConfig | None = None,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.config = config or WcaFinalizedOneMinutePollConfig()

    async def poll_once(
        self,
        *,
        now: datetime | None = None,
        market_is_open: bool = True,
        triggered_by: str = "background_publisher",
    ) -> WcaFinalizedOneMinutePollResult:
        if triggered_by != "background_publisher":
            return WcaFinalizedOneMinutePollResult("rejected", ("wca.market_event.publisher.background_only",))
        if self.market_data_client is None or self.candle_store is None:
            return WcaFinalizedOneMinutePollResult("blocked", ("wca.market_event.publisher.market_data_client_missing",))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not market_is_open:
            return WcaFinalizedOneMinutePollResult("blocked", ("wca.market_event.publisher.market_closed",))
        symbol = self.config.symbol.upper()
        rows = await self.market_data_client.get_bars(
            symbol=symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=max(self.config.fetch_limit, self.config.warmup_bars + 20),
            start=None,
            end=current.isoformat(),
            sort="asc",
        )
        if rows:
            self.candle_store.upsert_many(rows)
        cached_rows = self.candle_store.latest(
            symbol=symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=max(self.config.fetch_limit, self.config.warmup_bars + 20),
        )
        candles = tuple(sorted((_candle_from_row(row) for row in cached_rows), key=lambda candle: candle.timestamp))
        cutoff = _finalized_candle_cutoff(current, finalization_delay_seconds=self.config.finalization_delay_seconds)
        completed = tuple(candle for candle in candles if candle.timestamp.astimezone(timezone.utc) <= cutoff)
        if not completed:
            return WcaFinalizedOneMinutePollResult("blocked", ("wca.market_event.no_completed_candle",))

        latest_published = _latest_published_finalized_candle(self.runtime_repository, symbol=symbol)
        candidates = tuple(candle for candle in completed if latest_published is None or candle.timestamp.astimezone(timezone.utc) > latest_published)
        if not candidates:
            return WcaFinalizedOneMinutePollResult("idle", ("wca.market_event.no_new_finalized_candle",), latest_finalized_candle=completed[-1].timestamp)

        quote = await self.market_data_client.get_latest_quote(symbol=symbol, feed=self.config.feed)
        quote_model = _quote_from_row(quote) if quote else None
        qqq_rows, iwm_rows = await self._load_context_rows(current)
        qqq = tuple(sorted((_candle_from_row(row) for row in qqq_rows), key=lambda candle: candle.timestamp))
        iwm = tuple(sorted((_candle_from_row(row) for row in iwm_rows), key=lambda candle: candle.timestamp))
        publications: list[WcaFinalizedOneMinutePublicationResult] = []
        for candle in candidates:
            finalized_at = candle.timestamp.astimezone(timezone.utc)
            if (current - finalized_at).total_seconds() > self.config.max_event_age_seconds:
                publications.append(WcaFinalizedOneMinutePublicationResult(False, "rejected", None, ("wca.runtime.event.stale",)))
                continue
            history = tuple(row for row in completed if row.timestamp.astimezone(timezone.utc) <= finalized_at)
            qqq_history = tuple(row for row in qqq if row.timestamp.astimezone(timezone.utc) <= finalized_at)
            iwm_history = tuple(row for row in iwm if row.timestamp.astimezone(timezone.utc) <= finalized_at)
            publications.append(
                self.publish_completed_candle(
                    candles=history,
                    finalized_candle_timestamp=finalized_at,
                    quote=quote_model,
                    publication_timestamp=current,
                    market_data_source=f"alpaca:{self.config.feed}",
                    triggered_by=triggered_by,
                    external_market_data={
                        "QQQ": qqq_history,
                        "IWM": iwm_history,
                        "SPY_5Min": _derive_complete_bars(history, minutes=5, timestamp=finalized_at),
                        "SPY_15Min": _derive_complete_bars(history, minutes=15, timestamp=finalized_at),
                    },
                    external_input_timestamps={
                        **({"QQQ": qqq_history[-1].timestamp} if qqq_history else {}),
                        **({"IWM": iwm_history[-1].timestamp} if iwm_history else {}),
                        **({"market_breadth": finalized_at} if qqq_history and iwm_history else {}),
                    },
                    market_breadth_inputs=_market_breadth_inputs(qqq=qqq_history, iwm=iwm_history),
                    max_queue_depth=self.config.max_queue_depth,
                    max_event_age_seconds=self.config.max_event_age_seconds,
                )
            )
        status = "published" if any(publication.accepted for publication in publications) else "blocked"
        reason_codes = tuple(dict.fromkeys((WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION, *(code for publication in publications for code in publication.reason_codes))))
        return WcaFinalizedOneMinutePollResult(status, reason_codes, tuple(publications), latest_finalized_candle=completed[-1].timestamp)

    async def _load_context_rows(self, current: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        context: list[list[dict[str, Any]]] = []
        for symbol in ("QQQ", "IWM"):
            try:
                rows = await self.market_data_client.get_bars(
                    symbol=symbol,
                    timeframe=self.config.timeframe,
                    feed=self.config.feed,
                    limit=max(self.config.fetch_limit, self.config.warmup_bars + 20),
                    start=None,
                    end=current.isoformat(),
                    sort="asc",
                )
            except Exception:
                rows = []
            if rows:
                self.candle_store.upsert_many(rows)
            context.append(
                self.candle_store.latest(
                    symbol=symbol,
                    timeframe=self.config.timeframe,
                    feed=self.config.feed,
                    limit=max(self.config.fetch_limit, self.config.warmup_bars + 20),
                )
            )
        return context[0], context[1]

    def publish_completed_candle(
        self,
        *,
        candles: tuple[WcaCandle, ...],
        finalized_candle_timestamp: datetime,
        quote: WcaQuote | None,
        publication_timestamp: datetime,
        market_data_source: str,
        triggered_by: str = "background_publisher",
        external_market_data: dict[str, tuple[WcaCandle, ...]] | None = None,
        external_input_timestamps: dict[str, datetime] | None = None,
        market_breadth_inputs: dict[str, float] | None = None,
        economic_event_reason_codes: tuple[str, ...] = (),
        max_queue_depth: int = 200,
        max_event_age_seconds: int = 300,
    ) -> WcaFinalizedOneMinutePublicationResult:
        if triggered_by != "background_publisher":
            return WcaFinalizedOneMinutePublicationResult(False, "rejected", None, ("wca.market_event.publisher.background_only",))
        timestamp = finalized_candle_timestamp.astimezone(timezone.utc)
        publication = publication_timestamp.astimezone(timezone.utc)
        completed = tuple(sorted((candle for candle in candles if candle.timestamp.astimezone(timezone.utc) <= timestamp), key=lambda candle: candle.timestamp))
        if not completed or completed[-1].timestamp.astimezone(timezone.utc) != timestamp:
            return WcaFinalizedOneMinutePublicationResult(False, "rejected", None, ("wca.market_event.finalized_candle_missing",))
        if publication < timestamp:
            return WcaFinalizedOneMinutePublicationResult(False, "rejected", None, ("wca.market_event.unfinished_candle",))

        filtered_external = {
            key: tuple(candle for candle in rows if candle.timestamp.astimezone(timezone.utc) <= timestamp)
            for key, rows in (external_market_data or {}).items()
        }
        filtered_external_input_timestamps = {
            key: value.astimezone(timezone.utc)
            for key, value in (external_input_timestamps or {}).items()
            if value.astimezone(timezone.utc) <= timestamp
        }
        reason_codes = [WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION, "wca.market_event.finalized_1m"]
        missing = _missing_reason_codes(completed, quote, timestamp, publication, filtered_external, external_input_timestamps or {}, market_breadth_inputs or {})
        reason_codes.extend(missing)
        reason_codes.extend(economic_event_reason_codes)
        data_ready = not any(
            code
            in missing
            for code in (
                "wca.market_event.core_spy_history_insufficient",
                "wca.market_event.quote_missing",
                "wca.market_event.quote_stale",
                "wca.market_event.quote_from_future",
                "wca.market_event.missing_minute_gap",
            )
        )
        snapshot = WcaMarketSnapshot(
            symbol="SPY",
            data_timestamp=timestamp,
            decision_timestamp=publication,
            candles=completed[-WCA_REQUIRED_COMPLETED_HISTORY_BARS:],
            quote=quote,
            external_market_data=filtered_external,
            external_input_timestamps=filtered_external_input_timestamps,
            market_breadth_inputs=market_breadth_inputs or {},
            source=market_data_source,
            data_ready=data_ready,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )
        event_id = deterministic_finalized_bar_event_id(
            symbol="SPY",
            timeframe=WCA_RUNTIME_EVENT_TIMEFRAME,
            candle_timestamp=timestamp,
            source=market_data_source,
            event_version=WCA_RUNTIME_EVENT_SCHEMA_VERSION,
        )
        event = WcaFinalizedBarEvent(
            event_id=event_id,
            symbol="SPY",
            timeframe=WCA_RUNTIME_EVENT_TIMEFRAME,
            candle_open_timestamp=timestamp - timedelta(minutes=1),
            candle_close_timestamp=timestamp,
            finalized_candle_timestamp=timestamp,
            data_manifest_hash=_snapshot_hash(snapshot),
            publication_timestamp=publication,
            event_version=WCA_RUNTIME_EVENT_SCHEMA_VERSION,
            market_data_source=market_data_source,
            source=market_data_source,
            replay_or_recovery=False,
            snapshot=snapshot,
            immutable_snapshot_reference=f"sha256:{_snapshot_hash(snapshot)}",
            data_readiness_result="READY" if data_ready else "BLOCKED",
            missing_input_reason_codes=tuple(code for code in reason_codes if code.startswith("wca.market_event.") and code not in {"wca.market_event.finalized_1m"}),
            payload={
                "publisher_version": WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION,
                "timeframe": WCA_RUNTIME_EVENT_TIMEFRAME,
                "opening_range": _opening_range(completed, timestamp),
                "prior_session": _prior_session(completed, timestamp),
                "gap": _gap(completed, timestamp),
                "quote": _quote_payload(quote),
                "data_source_timestamps": {key: value.isoformat() for key, value in filtered_external_input_timestamps.items()},
                "data_quality_flags": tuple(code for code in reason_codes if code.startswith("wca.market_event.")),
            },
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )
        queue_result: WcaRuntimeQueueResult = self.runtime_repository.publish_finalized_bar_event(
            event,
            max_queue_depth=max_queue_depth,
            max_event_age_seconds=max_event_age_seconds,
            now=publication,
        )
        return WcaFinalizedOneMinutePublicationResult(queue_result.accepted, queue_result.status, event, queue_result.reason_codes)


def _missing_reason_codes(
    completed: tuple[WcaCandle, ...],
    quote: WcaQuote | None,
    timestamp: datetime,
    decision_timestamp: datetime,
    external_market_data: dict[str, tuple[WcaCandle, ...]],
    external_input_timestamps: dict[str, datetime],
    market_breadth_inputs: dict[str, float],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(completed) < WCA_REQUIRED_COMPLETED_HISTORY_BARS:
        reasons.append("wca.market_event.core_spy_history_insufficient")
    if _has_missing_minute(completed):
        reasons.append("wca.market_event.missing_minute_gap")
    if quote is None:
        reasons.append("wca.market_event.quote_missing")
    elif quote.timestamp.astimezone(timezone.utc) > decision_timestamp:
        reasons.append("wca.market_event.quote_from_future")
    elif (decision_timestamp - quote.timestamp.astimezone(timezone.utc)).total_seconds() > 60:
        reasons.append("wca.market_event.quote_stale")
    for symbol in ("QQQ", "IWM"):
        if not external_market_data.get(symbol):
            reasons.append(f"wca.market_event.{symbol.lower()}_context_missing")
        elif symbol not in external_input_timestamps:
            reasons.append(f"wca.market_event.{symbol.lower()}_timestamp_missing")
        elif external_input_timestamps[symbol].astimezone(timezone.utc) > timestamp:
            reasons.append(f"wca.market_event.{symbol.lower()}_timestamp_from_future")
        elif external_market_data[symbol][-1].timestamp.astimezone(timezone.utc) != timestamp:
            reasons.append(f"wca.market_event.{symbol.lower()}_context_not_time_aligned")
    if not market_breadth_inputs:
        reasons.append("wca.market_event.market_breadth_context_missing")
    return tuple(reasons)


def _has_missing_minute(candles: tuple[WcaCandle, ...]) -> bool:
    if len(candles) < 2:
        return False
    selected = candles[-WCA_REQUIRED_COMPLETED_HISTORY_BARS:]
    for previous, current in zip(selected, selected[1:]):
        if current.timestamp.astimezone(timezone.utc) - previous.timestamp.astimezone(timezone.utc) > timedelta(minutes=1):
            return True
    return False


def _opening_range(candles: tuple[WcaCandle, ...], timestamp: datetime) -> dict[str, Any]:
    session_rows = [candle for candle in candles if candle.timestamp.date() == timestamp.date()]
    opening = session_rows[:30]
    if not opening:
        return {"available": False}
    return {"available": True, "high": max(candle.high for candle in opening), "low": min(candle.low for candle in opening), "bars": len(opening)}


def _prior_session(candles: tuple[WcaCandle, ...], timestamp: datetime) -> dict[str, Any]:
    prior = [candle for candle in candles if candle.timestamp.date() < timestamp.date()]
    if not prior:
        return {"available": False}
    return {"available": True, "close": prior[-1].close, "high": max(candle.high for candle in prior), "low": min(candle.low for candle in prior)}


def _gap(candles: tuple[WcaCandle, ...], timestamp: datetime) -> dict[str, Any]:
    prior = _prior_session(candles, timestamp)
    today = [candle for candle in candles if candle.timestamp.date() == timestamp.date()]
    if not prior.get("available") or not today:
        return {"available": False}
    return {"available": True, "gap": today[0].open - float(prior["close"])}


def _quote_payload(quote: WcaQuote | None) -> dict[str, Any]:
    if quote is None:
        return {"available": False}
    mid = (quote.bid + quote.ask) / 2
    return {"available": True, "bid": quote.bid, "ask": quote.ask, "mid": mid, "spread": quote.ask - quote.bid, "timestamp": quote.timestamp.isoformat()}


def _snapshot_hash(snapshot: WcaMarketSnapshot) -> str:
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _latest_published_finalized_candle(runtime_repository: WcaRuntimeRepository, *, symbol: str) -> datetime | None:
    with runtime_repository.repository.connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(finalized_candle_timestamp)
            FROM wca_runtime_event_queue
            WHERE algorithm_id = ? AND symbol = ? AND status IN ('queued', 'processing', 'decision_queued', 'completed')
            """,
            ("wca", symbol.upper()),
        ).fetchone()
    value = row[0] if row is not None else None
    return _timestamp(value) if value else None


def _finalized_candle_cutoff(now: datetime, *, finalization_delay_seconds: int) -> datetime:
    adjusted = now.astimezone(timezone.utc) - timedelta(seconds=max(0, finalization_delay_seconds))
    return adjusted.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _derive_complete_bars(candles: tuple[WcaCandle, ...], *, minutes: int, timestamp: datetime) -> tuple[WcaCandle, ...]:
    selected = tuple(candle for candle in candles if candle.timestamp.astimezone(timezone.utc) <= timestamp.astimezone(timezone.utc))
    completed: list[WcaCandle] = []
    bucket: list[WcaCandle] = []
    for candle in selected:
        bucket.append(candle)
        if len(bucket) == minutes:
            completed.append(
                WcaCandle(
                    timestamp=bucket[-1].timestamp,
                    open=bucket[0].open,
                    high=max(row.high for row in bucket),
                    low=min(row.low for row in bucket),
                    close=bucket[-1].close,
                    volume=sum(row.volume for row in bucket),
                    vwap=_bucket_vwap(tuple(bucket)),
                )
            )
            bucket = []
    return tuple(completed)


def _bucket_vwap(candles: tuple[WcaCandle, ...]) -> float | None:
    weighted = [(candle.vwap or candle.close, candle.volume) for candle in candles if candle.volume > 0]
    volume = sum(row[1] for row in weighted)
    if volume <= 0:
        return None
    return sum(price * qty for price, qty in weighted) / volume


def _market_breadth_inputs(*, qqq: tuple[WcaCandle, ...], iwm: tuple[WcaCandle, ...]) -> dict[str, float]:
    if not qqq or not iwm or iwm[-1].close <= 0:
        return {}
    return {"qqq_iwm_relative_strength": qqq[-1].close / iwm[-1].close}


def _candle_from_row(row: dict[str, Any]) -> WcaCandle:
    return WcaCandle(
        timestamp=_timestamp(row["timestamp"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0),
        vwap=float(row["vwap"]) if row.get("vwap") is not None else None,
    )


def _quote_from_row(row: dict[str, Any]) -> WcaQuote:
    return WcaQuote(
        timestamp=_timestamp(row.get("quoteTimestamp") or row.get("timestamp") or row.get("marketDataReceiptTimestamp")),
        bid=float(row["bid"]),
        ask=float(row["ask"]),
    )


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION",
    "WCA_REQUIRED_COMPLETED_HISTORY_BARS",
    "WcaFinalizedOneMinutePollConfig",
    "WcaFinalizedOneMinutePollResult",
    "WcaFinalizedOneMinuteEventPublisher",
    "WcaFinalizedOneMinutePublicationResult",
]
