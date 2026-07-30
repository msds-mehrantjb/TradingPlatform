"""Background publisher for finalized SPY one-minute WCA market events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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


@dataclass(frozen=True)
class WcaFinalizedOneMinutePublicationResult:
    accepted: bool
    status: str
    event: WcaFinalizedBarEvent | None
    reason_codes: tuple[str, ...]


class WcaFinalizedOneMinuteEventPublisher:
    def __init__(self, runtime_repository: WcaRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository

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
        missing = _missing_reason_codes(completed, quote, timestamp, filtered_external, external_input_timestamps or {}, market_breadth_inputs or {})
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
            decision_timestamp=timestamp,
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
    elif quote.timestamp.astimezone(timezone.utc) > timestamp:
        reasons.append("wca.market_event.quote_from_future")
    elif (timestamp - quote.timestamp.astimezone(timezone.utc)).total_seconds() > 60:
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


__all__ = [
    "WCA_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION",
    "WCA_REQUIRED_COMPLETED_HISTORY_BARS",
    "WcaFinalizedOneMinuteEventPublisher",
    "WcaFinalizedOneMinutePublicationResult",
]
