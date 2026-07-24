"""State containers for the event-driven Session runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from backend.app.algorithms.session.calendar import parse_session_timestamp_utc, resolve_session_clock
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


FINALIZED_ONE_MINUTE_BAR = "finalized_one_minute_bar"
QUOTE_NBBO_UPDATE = "quote_nbbo_update"
MARKET_STATUS_CALENDAR_UPDATE = "market_status_calendar_update"
SCHEDULED_EVENT_RISK_UPDATE = "scheduled_event_risk_update"
SESSION_RESET = "session_reset"
REPLAY_RESET = "replay_reset"

ACCEPTED_SESSION_EVENT_TYPES = frozenset(
    {
        FINALIZED_ONE_MINUTE_BAR,
        QUOTE_NBBO_UPDATE,
        MARKET_STATUS_CALENDAR_UPDATE,
        SCHEDULED_EVENT_RISK_UPDATE,
        SESSION_RESET,
        REPLAY_RESET,
    }
)


@dataclass(frozen=True, order=True)
class SessionRuntimeKey:
    symbol: str
    session_date: str
    runtime_mode: str


@dataclass(frozen=True)
class SessionBar:
    event_id: str
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    revision: int = 0

    def as_candle(self, quote: QuoteSnapshot | None = None, *, decision_time: datetime | None = None) -> dict[str, Any]:
        candle: dict[str, Any] = {
            "timestamp": self.timestamp_utc.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        if quote and decision_time:
            candle.update(quote.as_candle_fields(decision_time=decision_time))
        return candle

    def content_hash(self) -> str:
        return stable_hash(
            {
                "timestamp": self.timestamp_utc.isoformat(),
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "revision": self.revision,
            }
        )


@dataclass(frozen=True)
class QuoteSnapshot:
    event_id: str
    timestamp_utc: datetime
    bid: float | None
    ask: float | None
    bid_size: float | None = None
    ask_size: float | None = None
    quote_age_ms: float | None = None
    latest_trade_timestamp_utc: datetime | None = None
    trade_count: float | None = None
    intended_order_quantity: float | None = None
    recent_estimated_slippage_bps: float | None = None
    recent_realized_slippage_bps: float | None = None
    partial_fill_rate: float | None = None

    def as_candle_fields(self, *, decision_time: datetime) -> dict[str, Any]:
        age_ms = self.quote_age_ms
        if age_ms is None:
            age_ms = max(0.0, (decision_time - self.timestamp_utc).total_seconds() * 1000)
        return {
            "bid": self.bid,
            "ask": self.ask,
            "bidSize": self.bid_size,
            "askSize": self.ask_size,
            "quoteTimestamp": self.timestamp_utc.isoformat(),
            "quoteAgeMs": age_ms,
            "latestTradeTimestamp": self.latest_trade_timestamp_utc.isoformat() if self.latest_trade_timestamp_utc else None,
            "tradeCount": self.trade_count,
            "intendedOrderQuantity": self.intended_order_quantity,
            "recentEstimatedSlippageBps": self.recent_estimated_slippage_bps,
            "recentRealizedSlippageBps": self.recent_realized_slippage_bps,
            "partialFillRate": self.partial_fill_rate,
        }

    def content_hash(self) -> str:
        return stable_hash(
            {
                "timestamp": self.timestamp_utc.isoformat(),
                "bid": self.bid,
                "ask": self.ask,
                "bidSize": self.bid_size,
                "askSize": self.ask_size,
                "quoteAgeMs": self.quote_age_ms,
                "latestTradeTimestampUtc": self.latest_trade_timestamp_utc.isoformat() if self.latest_trade_timestamp_utc else None,
                "tradeCount": self.trade_count,
                "intendedOrderQuantity": self.intended_order_quantity,
                "recentEstimatedSlippageBps": self.recent_estimated_slippage_bps,
                "recentRealizedSlippageBps": self.recent_realized_slippage_bps,
                "partialFillRate": self.partial_fill_rate,
            }
        )


@dataclass(frozen=True)
class MarketCalendarSnapshot:
    event_id: str
    timestamp_utc: datetime
    status: str
    session_date: str | None
    is_open: bool | None

    def content_hash(self) -> str:
        return stable_hash(
            {
                "timestamp": self.timestamp_utc.isoformat(),
                "status": self.status,
                "sessionDate": self.session_date,
                "isOpen": self.is_open,
            }
        )


@dataclass(frozen=True)
class EventRiskSnapshot:
    event_id: str
    timestamp_utc: datetime
    risk_state: str
    block_new_entries: bool
    reason_codes: tuple[str, ...] = ()

    def content_hash(self) -> str:
        return stable_hash(
            {
                "timestamp": self.timestamp_utc.isoformat(),
                "riskState": self.risk_state,
                "blockNewEntries": self.block_new_entries,
                "reasonCodes": self.reason_codes,
            }
        )


@dataclass(frozen=True)
class NormalizedSessionEvent:
    event_type: str
    symbol: str
    runtime_mode: str
    timestamp_utc: datetime
    event_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SessionRuntimeState:
    key: SessionRuntimeKey
    bars: tuple[SessionBar, ...] = ()
    quote: QuoteSnapshot | None = None
    calendar: MarketCalendarSnapshot | None = None
    event_risk: EventRiskSnapshot | None = None
    processed_event_ids: tuple[str, ...] = ()
    duplicate_event_ids: tuple[str, ...] = ()
    late_bar_event_ids: tuple[str, ...] = ()
    revised_bar_timestamps: tuple[str, ...] = ()
    ignored_unfinalized_event_ids: tuple[str, ...] = ()

    def apply_bar(self, bar: SessionBar, event_id: str) -> SessionRuntimeState:
        existing = {item.timestamp_utc: item for item in self.bars}
        previous = existing.get(bar.timestamp_utc)
        revised = previous is not None and previous.content_hash() != bar.content_hash()
        latest_timestamp = max(existing) if existing else None
        late = latest_timestamp is not None and bar.timestamp_utc < latest_timestamp
        existing[bar.timestamp_utc] = bar
        return replace(
            self,
            bars=tuple(existing[key] for key in sorted(existing)),
            processed_event_ids=_append_unique(self.processed_event_ids, event_id),
            late_bar_event_ids=_append_unique(self.late_bar_event_ids, event_id) if late else self.late_bar_event_ids,
            revised_bar_timestamps=_append_unique(self.revised_bar_timestamps, bar.timestamp_utc.isoformat()) if revised else self.revised_bar_timestamps,
        )

    def apply_quote(self, quote: QuoteSnapshot, event_id: str) -> SessionRuntimeState:
        current = self.quote
        if current and quote.timestamp_utc < current.timestamp_utc:
            quote = current
        return replace(self, quote=quote, processed_event_ids=_append_unique(self.processed_event_ids, event_id))

    def apply_calendar(self, calendar: MarketCalendarSnapshot, event_id: str) -> SessionRuntimeState:
        return replace(self, calendar=calendar, processed_event_ids=_append_unique(self.processed_event_ids, event_id))

    def apply_event_risk(self, event_risk: EventRiskSnapshot, event_id: str) -> SessionRuntimeState:
        return replace(self, event_risk=event_risk, processed_event_ids=_append_unique(self.processed_event_ids, event_id))

    def mark_duplicate(self, event_id: str) -> SessionRuntimeState:
        return replace(self, duplicate_event_ids=_append_unique(self.duplicate_event_ids, event_id))

    def mark_unfinalized(self, event_id: str) -> SessionRuntimeState:
        return replace(
            self,
            processed_event_ids=_append_unique(self.processed_event_ids, event_id),
            ignored_unfinalized_event_ids=_append_unique(self.ignored_unfinalized_event_ids, event_id),
        )

    def feature_snapshot_id(self, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> str:
        return "session-feature-" + stable_hash(
            {
                "key": self.key,
                "bars": [bar.content_hash() for bar in self.bars],
                "quote": self.quote.content_hash() if self.quote else None,
                "calendar": self.calendar.content_hash() if self.calendar else None,
                "eventRisk": self.event_risk.content_hash() if self.event_risk else None,
                "configHash": config.configuration_hash,
            }
        )

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "key": self.key.__dict__,
            "bars": [
                {
                    "eventId": bar.event_id,
                    "timestampUtc": bar.timestamp_utc.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "revision": bar.revision,
                }
                for bar in self.bars
            ],
            "quote": None
            if self.quote is None
            else {
                "eventId": self.quote.event_id,
                "timestampUtc": self.quote.timestamp_utc.isoformat(),
                "bid": self.quote.bid,
                "ask": self.quote.ask,
                "bidSize": self.quote.bid_size,
                "askSize": self.quote.ask_size,
                "quoteAgeMs": self.quote.quote_age_ms,
                "latestTradeTimestampUtc": self.quote.latest_trade_timestamp_utc.isoformat() if self.quote.latest_trade_timestamp_utc else None,
                "tradeCount": self.quote.trade_count,
                "intendedOrderQuantity": self.quote.intended_order_quantity,
                "recentEstimatedSlippageBps": self.quote.recent_estimated_slippage_bps,
                "recentRealizedSlippageBps": self.quote.recent_realized_slippage_bps,
                "partialFillRate": self.quote.partial_fill_rate,
            },
            "calendar": None
            if self.calendar is None
            else {
                "eventId": self.calendar.event_id,
                "timestampUtc": self.calendar.timestamp_utc.isoformat(),
                "status": self.calendar.status,
                "sessionDate": self.calendar.session_date,
                "isOpen": self.calendar.is_open,
            },
            "eventRisk": None
            if self.event_risk is None
            else {
                "eventId": self.event_risk.event_id,
                "timestampUtc": self.event_risk.timestamp_utc.isoformat(),
                "riskState": self.event_risk.risk_state,
                "blockNewEntries": self.event_risk.block_new_entries,
                "reasonCodes": self.event_risk.reason_codes,
            },
            "processedEventIds": self.processed_event_ids,
            "duplicateEventIds": self.duplicate_event_ids,
            "lateBarEventIds": self.late_bar_event_ids,
            "revisedBarTimestamps": self.revised_bar_timestamps,
            "ignoredUnfinalizedEventIds": self.ignored_unfinalized_event_ids,
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> SessionRuntimeState:
        key_payload = dict(payload["key"])
        quote_payload = payload.get("quote")
        calendar_payload = payload.get("calendar")
        event_risk_payload = payload.get("eventRisk")
        return cls(
            key=SessionRuntimeKey(**key_payload),
            bars=tuple(
                SessionBar(
                    event_id=item["eventId"],
                    timestamp_utc=parse_session_timestamp_utc(item["timestampUtc"]),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=None if item.get("volume") is None else float(item["volume"]),
                    revision=int(item.get("revision") or 0),
                )
                for item in payload.get("bars", ())
            ),
            quote=None
            if not quote_payload
            else QuoteSnapshot(
                event_id=quote_payload["eventId"],
                timestamp_utc=parse_session_timestamp_utc(quote_payload["timestampUtc"]),
                bid=None if quote_payload.get("bid") is None else float(quote_payload["bid"]),
                ask=None if quote_payload.get("ask") is None else float(quote_payload["ask"]),
                bid_size=None if quote_payload.get("bidSize") is None else float(quote_payload["bidSize"]),
                ask_size=None if quote_payload.get("askSize") is None else float(quote_payload["askSize"]),
                quote_age_ms=None if quote_payload.get("quoteAgeMs") is None else float(quote_payload["quoteAgeMs"]),
                latest_trade_timestamp_utc=None if quote_payload.get("latestTradeTimestampUtc") is None else parse_session_timestamp_utc(quote_payload["latestTradeTimestampUtc"]),
                trade_count=None if quote_payload.get("tradeCount") is None else float(quote_payload["tradeCount"]),
                intended_order_quantity=None if quote_payload.get("intendedOrderQuantity") is None else float(quote_payload["intendedOrderQuantity"]),
                recent_estimated_slippage_bps=None if quote_payload.get("recentEstimatedSlippageBps") is None else float(quote_payload["recentEstimatedSlippageBps"]),
                recent_realized_slippage_bps=None if quote_payload.get("recentRealizedSlippageBps") is None else float(quote_payload["recentRealizedSlippageBps"]),
                partial_fill_rate=None if quote_payload.get("partialFillRate") is None else float(quote_payload["partialFillRate"]),
            ),
            calendar=None
            if not calendar_payload
            else MarketCalendarSnapshot(
                event_id=calendar_payload["eventId"],
                timestamp_utc=parse_session_timestamp_utc(calendar_payload["timestampUtc"]),
                status=str(calendar_payload.get("status") or "unknown"),
                session_date=calendar_payload.get("sessionDate"),
                is_open=calendar_payload.get("isOpen"),
            ),
            event_risk=None
            if not event_risk_payload
            else EventRiskSnapshot(
                event_id=event_risk_payload["eventId"],
                timestamp_utc=parse_session_timestamp_utc(event_risk_payload["timestampUtc"]),
                risk_state=str(event_risk_payload.get("riskState") or "unknown"),
                block_new_entries=bool(event_risk_payload.get("blockNewEntries")),
                reason_codes=tuple(event_risk_payload.get("reasonCodes") or ()),
            ),
            processed_event_ids=tuple(payload.get("processedEventIds") or ()),
            duplicate_event_ids=tuple(payload.get("duplicateEventIds") or ()),
            late_bar_event_ids=tuple(payload.get("lateBarEventIds") or ()),
            revised_bar_timestamps=tuple(payload.get("revisedBarTimestamps") or ()),
            ignored_unfinalized_event_ids=tuple(payload.get("ignoredUnfinalizedEventIds") or ()),
        )


def normalize_session_event(raw_event: Mapping[str, Any], *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> NormalizedSessionEvent:
    event_type = str(raw_event.get("event_type") or raw_event.get("type") or "")
    if event_type not in ACCEPTED_SESSION_EVENT_TYPES:
        raise ValueError(f"Unsupported Session event type: {event_type}")
    payload = _payload(raw_event)
    symbol = str(raw_event.get("symbol") or payload.get("symbol") or "SPY").upper()
    runtime_mode = str(raw_event.get("runtime_mode") or raw_event.get("runtimeMode") or payload.get("runtimeMode") or "paper")
    timestamp = raw_event.get("timestamp") or raw_event.get("event_timestamp") or payload.get("timestamp")
    if timestamp is None:
        raise ValueError("Session events require a timezone-aware timestamp")
    timestamp_utc = parse_session_timestamp_utc(timestamp)
    event_id = str(raw_event.get("event_id") or raw_event.get("eventId") or stable_event_id(event_type, symbol, runtime_mode, timestamp_utc, payload))
    return NormalizedSessionEvent(event_type=event_type, symbol=symbol, runtime_mode=runtime_mode, timestamp_utc=timestamp_utc, event_id=event_id, payload=payload)


def runtime_key_for_event(event: NormalizedSessionEvent, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> SessionRuntimeKey:
    session_date = str(event.payload.get("sessionDate") or event.payload.get("session_date") or "")
    if not session_date:
        clock = resolve_session_clock(event.timestamp_utc, config=config)
        session_date = clock.session_date or event.timestamp_utc.date().isoformat()
    return SessionRuntimeKey(symbol=event.symbol, session_date=session_date, runtime_mode=event.runtime_mode)


def bar_from_event(event: NormalizedSessionEvent) -> SessionBar:
    payload = event.payload
    return SessionBar(
        event_id=event.event_id,
        timestamp_utc=event.timestamp_utc,
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume=None if payload.get("volume") is None else float(payload["volume"]),
        revision=int(payload.get("revision") or 0),
    )


def quote_from_event(event: NormalizedSessionEvent) -> QuoteSnapshot:
    payload = event.payload
    return QuoteSnapshot(
        event_id=event.event_id,
        timestamp_utc=event.timestamp_utc,
        bid=_float_or_none(payload.get("bid") if payload.get("bid") is not None else payload.get("bestBid") or payload.get("best_bid")),
        ask=_float_or_none(payload.get("ask") if payload.get("ask") is not None else payload.get("bestAsk") or payload.get("best_ask")),
        bid_size=_float_or_none(payload.get("bidSize") or payload.get("bid_size")),
        ask_size=_float_or_none(payload.get("askSize") or payload.get("ask_size")),
        quote_age_ms=_float_or_none(payload.get("quoteAgeMs") or payload.get("quote_age_ms")),
        latest_trade_timestamp_utc=None if not (payload.get("latestTradeTimestamp") or payload.get("latest_trade_timestamp")) else parse_session_timestamp_utc(payload.get("latestTradeTimestamp") or payload.get("latest_trade_timestamp")),
        trade_count=_float_or_none(payload.get("tradeCount") or payload.get("trade_count")),
        intended_order_quantity=_float_or_none(payload.get("intendedOrderQuantity") or payload.get("intended_order_quantity")),
        recent_estimated_slippage_bps=_float_or_none(payload.get("recentEstimatedSlippageBps") or payload.get("recent_estimated_slippage_bps")),
        recent_realized_slippage_bps=_float_or_none(payload.get("recentRealizedSlippageBps") or payload.get("recent_realized_slippage_bps")),
        partial_fill_rate=_float_or_none(payload.get("partialFillRate") or payload.get("partial_fill_rate")),
    )


def calendar_from_event(event: NormalizedSessionEvent) -> MarketCalendarSnapshot:
    payload = event.payload
    return MarketCalendarSnapshot(
        event_id=event.event_id,
        timestamp_utc=event.timestamp_utc,
        status=str(payload.get("status") or "unknown"),
        session_date=payload.get("sessionDate") or payload.get("session_date"),
        is_open=payload.get("isOpen") if "isOpen" in payload else payload.get("is_open"),
    )


def event_risk_from_event(event: NormalizedSessionEvent) -> EventRiskSnapshot:
    payload = event.payload
    return EventRiskSnapshot(
        event_id=event.event_id,
        timestamp_utc=event.timestamp_utc,
        risk_state=str(payload.get("riskState") or payload.get("risk_state") or "unknown"),
        block_new_entries=bool(payload.get("blockNewEntries") or payload.get("block_new_entries")),
        reason_codes=tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ()),
    )


def stable_event_id(event_type: str, symbol: str, runtime_mode: str, timestamp_utc: datetime, payload: Mapping[str, Any]) -> str:
    return "session-event-" + stable_hash(
        {
            "eventType": event_type,
            "symbol": symbol.upper(),
            "runtimeMode": runtime_mode,
            "timestampUtc": timestamp_utc.isoformat(),
            "payload": payload,
        }
    )


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _payload(raw_event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = raw_event.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return {key: value for key, value in raw_event.items() if key not in {"event_type", "type", "event_id", "eventId", "runtime_mode", "runtimeMode"}}


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return _jsonable(value.__dict__)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
