"""Regime-owned finalized one-minute market-data publisher."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from backend.app.algorithms.regime.contracts import RegimeRuntimeMode
from backend.app.algorithms.regime.exchange_calendar import exchange_session
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent


REGIME_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION = "regime_finalized_one_minute_publisher_v1"
REGIME_REQUIRED_COMPLETED_HISTORY_BARS = 120
REGIME_DEFAULT_PUBLISHER_FETCH_LIMIT = 240
REGIME_DEFAULT_FINALIZATION_DELAY_SECONDS = 5
REGIME_MATERIAL_DATA_GAP_MINUTES = 2


@dataclass(frozen=True)
class RegimeFinalizedOneMinutePublisherConfig:
    symbol: str = "SPY"
    feed: str = "iex"
    timeframe: str = "1Min"
    fetch_limit: int = REGIME_DEFAULT_PUBLISHER_FETCH_LIMIT
    warmup_bars: int = REGIME_REQUIRED_COMPLETED_HISTORY_BARS
    finalization_delay_seconds: int = REGIME_DEFAULT_FINALIZATION_DELAY_SECONDS
    max_event_age_seconds: int = 300
    material_gap_minutes: int = REGIME_MATERIAL_DATA_GAP_MINUTES
    publisher_poll_interval_seconds: float = 1.0
    closed_market_poll_interval_seconds: float = 300.0


@dataclass(frozen=True)
class RegimePublicationResult:
    accepted: bool
    status: str
    event_id: str | None
    completed_bar_timestamp: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RegimePublisherPollResult:
    status: str
    reason_codes: tuple[str, ...]
    publications: tuple[RegimePublicationResult, ...] = ()
    latest_finalized_candle: str | None = None
    lag_seconds: float | None = None
    next_poll_after_seconds: float | None = None

    @property
    def accepted_count(self) -> int:
        return sum(1 for publication in self.publications if publication.accepted)


class RegimeMarketDataClient(Protocol):
    settings: Any

    async def get_market_status(self) -> dict[str, Any]:
        ...

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


class RegimeCandleStore(Protocol):
    def upsert_many(self, candles: list[dict[str, Any]]) -> None:
        ...

    def latest(self, *, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict[str, Any]]:
        ...

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict[str, Any]]:
        ...


class RegimePublisherRepository(Protocol):
    def ensure_active_settings_snapshot(self, identity: dict[str, Any]) -> dict[str, Any]:
        ...

    def read_runtime_event(self, identity: dict[str, Any], event_id: str) -> dict[str, Any] | None:
        ...

    def read_owned_records(self, table: str, identity: dict[str, Any]) -> list[dict[str, Any]]:
        ...

    def record_runtime_event(self, event: dict[str, Any]) -> dict[str, Any]:
        ...

    def write_runtime_snapshot(self, identity: dict[str, Any], key: str, snapshot: dict[str, Any]) -> None:
        ...


class RegimeFinalizedOneMinutePublisher:
    def __init__(
        self,
        *,
        identity: dict[str, str],
        repository: RegimePublisherRepository,
        market_data_client: RegimeMarketDataClient | None,
        candle_store: RegimeCandleStore | None,
        publish_completed_bar: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        config: RegimeFinalizedOneMinutePublisherConfig | None = None,
    ) -> None:
        self.identity = {**identity, "algorithmId": "regime", "runtimeMode": RegimeRuntimeMode.PAPER.value, "symbol": "SPY"}
        self.repository = repository
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.publish_completed_bar = publish_completed_bar
        self.config = config or RegimeFinalizedOneMinutePublisherConfig()

    async def poll_once(self, *, now: datetime | None = None, triggered_by: str = "background_publisher") -> RegimePublisherPollResult:
        current = _as_utc(now or datetime.now(UTC))
        if triggered_by != "background_publisher":
            return self._record_poll(
                "rejected",
                ("regime.publisher.background_only",),
                now=current,
                next_poll_after_seconds=self.config.publisher_poll_interval_seconds,
            )
        blockers = self._dependency_blockers()
        if blockers:
            return self._record_poll("blocked", blockers, now=current, next_poll_after_seconds=self.config.publisher_poll_interval_seconds)
        clock = await self._market_status()
        clock_time = _parse_timestamp(clock.get("timestamp")) or current
        if not bool(clock.get("isOpen")):
            return self._record_poll(
                "blocked",
                ("regime.publisher.market_closed",),
                now=current,
                clock=clock,
                next_poll_after_seconds=_closed_market_next_poll_seconds(clock, current, default_seconds=self.config.closed_market_poll_interval_seconds),
            )
        cutoff = _finalized_candle_cutoff(clock_time, finalization_delay_seconds=self.config.finalization_delay_seconds)
        session = exchange_session(_iso(cutoff))
        if session.status == "outside_regular":
            return self._record_poll("blocked", ("regime.publisher.outside_regular_session",), now=current, clock=clock)
        symbol = self.config.symbol.upper()
        if symbol != "SPY" or self.config.timeframe != "1Min":
            return self._record_poll("blocked", ("regime.publisher.spy_one_minute_scope_required",), now=current, clock=clock)
        try:
            rows = await self.market_data_client.get_bars(
                symbol=symbol,
                timeframe=self.config.timeframe,
                feed=self.config.feed,
                limit=max(self.config.fetch_limit, self.config.warmup_bars + 30),
                start=None,
                end=_iso(clock_time),
                sort="asc",
            )
            if rows:
                self.candle_store.upsert_many(rows)
            cached = self.candle_store.latest_until(
                symbol=symbol,
                timeframe=self.config.timeframe,
                feed=self.config.feed,
                limit=max(self.config.fetch_limit, self.config.warmup_bars + 30),
                end=_iso(cutoff),
            )
        except Exception as exc:
            return self._record_poll("blocked", ("regime.publisher.market_data_fetch_failed",), now=current, clock=clock, error=str(exc))
        candles = tuple(sorted((_publisher_candle(row) for row in cached), key=lambda row: row["timestamp"]))
        completed = tuple(row for row in candles if _parse_timestamp(row["timestamp"]) and _parse_timestamp(row["timestamp"]) <= cutoff)
        if not completed:
            return self._record_poll("blocked", ("regime.publisher.no_completed_candle",), now=current, clock=clock)
        latest_published = self._latest_published_bar_timestamp()
        candidates = tuple(row for row in completed if latest_published is None or _parse_timestamp(row["timestamp"]) > latest_published)
        if not candidates:
            return self._record_poll(
                "idle",
                ("regime.publisher.no_new_finalized_candle",),
                now=current,
                clock=clock,
                latest_finalized_candle=completed[-1]["timestamp"],
                lag_seconds=max(0.0, (current - (_parse_timestamp(completed[-1]["timestamp"]) or current)).total_seconds()),
            )
        data_quality_blockers = self._data_quality_blockers(completed, latest_published=latest_published)
        if data_quality_blockers:
            self._record_material_gap_if_needed(data_quality_blockers, completed[-1]["timestamp"], now=current)
            return self._record_poll("blocked", data_quality_blockers, now=current, clock=clock, latest_finalized_candle=completed[-1]["timestamp"])
        quote = await self._latest_quote(symbol)
        context_rows = await self._load_context_rows(clock_time)
        publications: list[RegimePublicationResult] = []
        for candle in candidates:
            candle_time = _parse_timestamp(candle["timestamp"])
            if candle_time is None:
                publications.append(RegimePublicationResult(False, "rejected", None, None, ("regime.publisher.invalid_candidate_timestamp",)))
                continue
            if (current - candle_time).total_seconds() > self.config.max_event_age_seconds:
                publications.append(RegimePublicationResult(False, "rejected", None, candle["timestamp"], ("regime.publisher.stale_candidate",)))
                continue
            history = tuple(row for row in completed if (_parse_timestamp(row["timestamp"]) or current) <= candle_time)
            publication = await self._publish_one(history=history, completed_bar_timestamp=candle_time, quote=quote, context_rows=context_rows, published_at=current, clock=clock)
            publications.append(publication)
        status = "published" if any(publication.accepted for publication in publications) else "blocked"
        reasons = tuple(dict.fromkeys((REGIME_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION, *(code for item in publications for code in item.reason_codes))))
        return self._record_poll(status, reasons, now=current, clock=clock, publications=tuple(publications), latest_finalized_candle=completed[-1]["timestamp"], lag_seconds=max(0.0, (current - (_parse_timestamp(completed[-1]["timestamp"]) or current)).total_seconds()))

    async def _publish_one(
        self,
        *,
        history: tuple[dict[str, Any], ...],
        completed_bar_timestamp: datetime,
        quote: dict[str, Any] | None,
        context_rows: dict[str, tuple[dict[str, Any], ...]],
        published_at: datetime,
        clock: dict[str, Any],
    ) -> RegimePublicationResult:
        selected = tuple(row for row in history if (_parse_timestamp(row["timestamp"]) or published_at) <= completed_bar_timestamp)
        if len(selected) < self.config.warmup_bars:
            return RegimePublicationResult(False, "blocked", None, _iso(completed_bar_timestamp), ("regime.publisher.history_insufficient",))
        market_payload = self._market_payload(
            candles=selected[-self.config.warmup_bars :],
            completed_bar_timestamp=completed_bar_timestamp,
            quote=quote,
            context_rows=context_rows,
            published_at=published_at,
            clock=clock,
        )
        data_manifest_hash = _hash_payload(market_payload)
        settings = self.repository.ensure_active_settings_snapshot(self.identity)
        settings_version = str(settings.get("settingsVersion") or "regime-settings-unavailable")
        event = RegimeFinalisedBarEvent(
            algorithm_id="regime",
            algorithm_instance_id=self.identity["algorithmInstanceId"],
            account_id=self.identity["accountId"],
            runtime_mode=RegimeRuntimeMode.PAPER.value,
            symbol="SPY",
            completed_bar_timestamp=completed_bar_timestamp,
            market_payload=market_payload,
            published_at=published_at,
            data_manifest_hash=data_manifest_hash,
            settings_version=settings_version,
            completed=True,
        )
        if self.repository.read_runtime_event(self.identity, event.event_id) is not None:
            return RegimePublicationResult(False, "duplicate", event.event_id, _iso(completed_bar_timestamp), ("regime.publisher.duplicate_suppressed",))
        result = await self.publish_completed_bar(event.as_dict())
        accepted = bool(result.get("accepted"))
        return RegimePublicationResult(
            accepted,
            "published" if accepted else "rejected",
            str(result.get("eventId") or event.event_id),
            _iso(completed_bar_timestamp),
            tuple(str(code) for code in result.get("reasonCodes") or ()),
        )

    def _market_payload(
        self,
        *,
        candles: tuple[dict[str, Any], ...],
        completed_bar_timestamp: datetime,
        quote: dict[str, Any] | None,
        context_rows: dict[str, tuple[dict[str, Any], ...]],
        published_at: datetime,
        clock: dict[str, Any],
    ) -> dict[str, Any]:
        quote_context = _quote_context(quote, published_at)
        qqq = context_rows.get("QQQ") or ()
        iwm = context_rows.get("IWM") or ()
        vix = context_rows.get("VIXY") or ()
        return {
            "symbol": "SPY",
            "timeframe": "1Min",
            "primaryCandles": list(candles),
            "oneMinuteCandles": list(candles),
            "completedBarTimestamp": _iso(completed_bar_timestamp),
            "observedAt": _iso(published_at),
            "publishedAt": _iso(published_at),
            "dataAgeMs": max(0, int((published_at - completed_bar_timestamp).total_seconds() * 1000)),
            "publisher": {
                "algorithmId": "regime",
                "publisherVersion": REGIME_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION,
                "source": f"alpaca:{self.config.feed}",
                "backgroundOnly": True,
                "browserRequired": False,
            },
            "exchangeClock": _redact_clock(clock),
            "contextFeeds": {
                "quoteFreshness": quote_context,
                "qqqRelativeStrength": _relative_strength(candles, qqq, "qqqRelativeStrength"),
                "iwmRelativeStrength": _relative_strength(candles, iwm, "iwmRelativeStrength"),
                "marketBreadth": _market_breadth(qqq, iwm),
                "vix": _vix_context(vix),
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999, "eventType": None, "source": "regime_publisher_default_no_event_feed"},
                "haltLuldCircuitBreaker": {"haltState": "unknown", "circuitBreakerState": "unknown", "newEntriesBlocked": False},
                "marketStructureLevels": _market_structure(candles, completed_bar_timestamp),
                "intradayVolatilityBaseline": _intraday_volatility(candles, completed_bar_timestamp),
            },
        }

    async def _market_status(self) -> dict[str, Any]:
        return await self.market_data_client.get_market_status()

    async def _latest_quote(self, symbol: str) -> dict[str, Any] | None:
        try:
            return await self.market_data_client.get_latest_quote(symbol=symbol, feed=self.config.feed)
        except Exception:
            return None

    async def _load_context_rows(self, current: datetime) -> dict[str, tuple[dict[str, Any], ...]]:
        context: dict[str, tuple[dict[str, Any], ...]] = {}
        for symbol in ("QQQ", "IWM", "VIXY"):
            try:
                rows = await self.market_data_client.get_bars(
                    symbol=symbol,
                    timeframe=self.config.timeframe,
                    feed=self.config.feed,
                    limit=max(self.config.fetch_limit, self.config.warmup_bars + 30),
                    start=None,
                    end=_iso(current),
                    sort="asc",
                )
            except Exception:
                rows = []
            if rows:
                self.candle_store.upsert_many(rows)
            cached = self.candle_store.latest_until(symbol=symbol, timeframe=self.config.timeframe, feed=self.config.feed, limit=self.config.fetch_limit, end=_iso(current))
            context[symbol] = tuple(sorted((_publisher_candle(row) for row in cached), key=lambda row: row["timestamp"]))
        return context

    def _dependency_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.market_data_client is None or self.candle_store is None:
            blockers.append("regime.publisher.market_data_dependencies_missing")
        settings = getattr(self.market_data_client, "settings", None)
        if settings is not None and not bool(getattr(settings, "has_alpaca_credentials", False)):
            blockers.append("regime.publisher.alpaca_market_data_credentials_missing")
        return tuple(blockers)

    def _data_quality_blockers(self, completed: tuple[dict[str, Any], ...], *, latest_published: datetime | None) -> tuple[str, ...]:
        blockers: list[str] = []
        timestamps = [_parse_timestamp(row["timestamp"]) for row in completed]
        valid = [item for item in timestamps if item is not None]
        if len(valid) != len(timestamps):
            blockers.append("regime.publisher.invalid_bar_timestamp")
        if len({item for item in valid}) != len(valid):
            blockers.append("regime.publisher.duplicate_bar_detected")
        if any(right <= left for left, right in zip(valid, valid[1:])):
            blockers.append("regime.publisher.out_of_order_bar_detected")
        missing = _missing_bar_count(valid[-self.config.warmup_bars :])
        if missing:
            blockers.append("regime.publisher.missing_bar_detected")
        if missing >= self.config.material_gap_minutes:
            blockers.append("regime.publisher.material_data_gap_detected")
        latest = valid[-1] if valid else None
        if (
            latest_published is not None
            and latest is not None
            and _same_exchange_session(latest_published, latest)
            and latest - latest_published > timedelta(minutes=self.config.material_gap_minutes)
        ):
            blockers.append("regime.publisher.material_data_gap_since_last_publish")
        return tuple(dict.fromkeys(blockers))

    def _latest_published_bar_timestamp(self) -> datetime | None:
        records = self.repository.read_owned_records("regime_runtime_events", self.identity)[-500:]
        timestamps: list[datetime] = []
        for record in records:
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
            if not (
                payload.get("completedBarTimestamp")
                and (payload.get("marketPayload") or payload.get("marketData") or payload.get("eventVersion"))
            ):
                continue
            timestamp = _parse_timestamp(payload.get("completedBarTimestamp") or payload.get("timestamp") or payload.get("dataTimestamp"))
            if timestamp is not None:
                timestamps.append(timestamp)
        return max(timestamps) if timestamps else None

    def _record_material_gap_if_needed(self, blockers: tuple[str, ...], latest: str, *, now: datetime) -> None:
        if not any("material_data_gap" in blocker for blocker in blockers):
            return
        self.repository.record_runtime_event(
            {
                **self.identity,
                "eventId": f"regime-publisher-material-gap-{latest}",
                "eventType": "publisher_material_data_gap",
                "processingStatus": "blocked",
                "timestamp": latest,
                "payload": {
                    "algorithmId": "regime",
                    "latestCandidateTimestamp": latest,
                    "newEntriesBlocked": True,
                    "reasonCodes": blockers,
                    "observedAt": _iso(now),
                },
            }
        )

    def _record_poll(
        self,
        status: str,
        reason_codes: tuple[str, ...],
        *,
        now: datetime,
        clock: dict[str, Any] | None = None,
        publications: tuple[RegimePublicationResult, ...] = (),
        latest_finalized_candle: str | None = None,
        lag_seconds: float | None = None,
        error: str | None = None,
        next_poll_after_seconds: float | None = None,
    ) -> RegimePublisherPollResult:
        resolved_next_poll = (
            next_poll_after_seconds
            if next_poll_after_seconds is not None
            else _open_market_next_poll_seconds(now, finalization_delay_seconds=self.config.finalization_delay_seconds)
        )
        snapshot = {
            "algorithmId": "regime",
            "publisherVersion": REGIME_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION,
            "status": status,
            "reasonCodes": list(reason_codes),
            "latestFinalizedCandle": latest_finalized_candle,
            "lagSeconds": lag_seconds,
            "nextPollAfterSeconds": resolved_next_poll,
            "acceptedCount": sum(1 for item in publications if item.accepted),
            "publicationCount": len(publications),
            "observedAt": _iso(now),
            "exchangeClock": _redact_clock(clock or {}),
            "error": error,
        }
        try:
            self.repository.write_runtime_snapshot(self.identity, "finalised_bar_publisher", snapshot)
        except Exception:
            pass
        return RegimePublisherPollResult(status, reason_codes, publications, latest_finalized_candle, lag_seconds, resolved_next_poll)


def _finalized_candle_cutoff(now: datetime, *, finalization_delay_seconds: int) -> datetime:
    adjusted = _as_utc(now) - timedelta(seconds=max(0, finalization_delay_seconds))
    return adjusted.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _open_market_next_poll_seconds(now: datetime, *, finalization_delay_seconds: int) -> float:
    current = _as_utc(now)
    target = current.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=max(0, finalization_delay_seconds))
    if target <= current:
        target += timedelta(minutes=1)
    return max(0.05, (target - current).total_seconds())


def _closed_market_next_poll_seconds(clock: dict[str, Any], now: datetime, *, default_seconds: float) -> float:
    current = _as_utc(now)
    next_open = _parse_timestamp(clock.get("nextOpen") or clock.get("next_open"))
    default_seconds = max(1.0, float(default_seconds))
    if next_open is None or next_open <= current:
        return default_seconds
    seconds_until_open = max(1.0, (next_open - current).total_seconds())
    return min(default_seconds, seconds_until_open)


def _publisher_candle(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _iso(_parse_timestamp(row.get("timestamp")) or datetime.now(UTC)),
        "open": float(row.get("open") or 0),
        "high": float(row.get("high") or row.get("close") or 0),
        "low": float(row.get("low") or row.get("close") or 0),
        "close": float(row.get("close") or 0),
        "volume": float(row.get("volume") or 0),
        "vwap": float(row["vwap"]) if row.get("vwap") is not None else None,
        "completed": True,
        "finalized": True,
    }


def _quote_context(quote: dict[str, Any] | None, observed_at: datetime) -> dict[str, Any]:
    if not quote:
        return {"status": "unknown", "ageMs": None, "maxAgeMs": 5000, "bid": None, "ask": None, "spreadBps": None, "expectedFillQuantity": None}
    quote_ts = _parse_timestamp(quote.get("quoteTimestamp") or quote.get("timestamp") or quote.get("marketDataReceiptTimestamp")) or observed_at
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    age_ms = max(0, int((_as_utc(observed_at) - quote_ts).total_seconds() * 1000))
    spread_bps = None
    if bid is not None and ask is not None and ask > bid:
        mid = (bid + ask) / 2
        spread_bps = ((ask - bid) / mid) * 10_000 if mid > 0 else None
    return {
        "status": "fresh" if age_ms <= 5000 else "stale",
        "ageMs": age_ms,
        "maxAgeMs": 5000,
        "bid": bid,
        "ask": ask,
        "spreadBps": spread_bps,
        "expectedFillQuantity": _number(quote.get("bidSize")) or _number(quote.get("askSize")),
        "source": quote.get("source") or "alpaca_latest_quote",
    }


def _relative_strength(primary: tuple[dict[str, Any], ...], other: tuple[dict[str, Any], ...], source: str) -> dict[str, Any]:
    if len(primary) < 2 or len(other) < 2 or primary[0]["close"] <= 0 or other[0]["close"] <= 0:
        return {"state": "unknown", "relativeToPrimaryPercent": None, "source": source}
    primary_change = (primary[-1]["close"] - primary[0]["close"]) / primary[0]["close"]
    other_change = (other[-1]["close"] - other[0]["close"]) / other[0]["close"]
    relative = (other_change - primary_change) * 100
    state = "outperforming" if relative >= 0.25 else "underperforming" if relative <= -0.25 else "neutral"
    return {"state": state, "relativeToPrimaryPercent": relative, "source": source}


def _market_breadth(qqq: tuple[dict[str, Any], ...], iwm: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if len(qqq) < 2 or len(iwm) < 2:
        return {"state": "unknown", "advanceDeclineRatio": None, "source": "qqq_iwm_proxy"}
    advancers = int(qqq[-1]["close"] >= qqq[0]["close"]) + int(iwm[-1]["close"] >= iwm[0]["close"])
    decliners = 2 - advancers
    ratio = advancers / max(1, decliners)
    state = "positive" if ratio >= 1.2 else "negative" if ratio <= 0.8 else "neutral"
    return {"state": state, "advanceDeclineRatio": ratio, "source": "qqq_iwm_proxy"}


def _vix_context(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not rows:
        return {"state": "unknown", "value": None, "source": "vix_proxy_unavailable"}
    value = rows[-1]["close"]
    state = "stress" if value >= 30 else "elevated" if value >= 20 else "calm" if value <= 13 else "normal"
    return {"state": state, "value": value, "source": "VIXY"}


def _market_structure(candles: tuple[dict[str, Any], ...], timestamp: datetime) -> dict[str, Any]:
    today = [row for row in candles if (_parse_timestamp(row["timestamp"]) or timestamp).date() == timestamp.date()]
    prior = [row for row in candles if (_parse_timestamp(row["timestamp"]) or timestamp).date() < timestamp.date()]
    opening = today[:30]
    return {
        "priorDayHigh": max((row["high"] for row in prior), default=None),
        "priorDayLow": min((row["low"] for row in prior), default=None),
        "openingRangeHigh": max((row["high"] for row in opening), default=None),
        "openingRangeLow": min((row["low"] for row in opening), default=None),
        "source": "regime_publisher",
    }


def _intraday_volatility(candles: tuple[dict[str, Any], ...], timestamp: datetime) -> dict[str, Any]:
    ranges = [max(0.0, row["high"] - row["low"]) for row in candles[-60:]]
    current_range = ranges[-1] if ranges else None
    expected = sum(ranges) / len(ranges) if ranges else None
    session = exchange_session(_iso(timestamp))
    return {
        "calibrationStatus": "ready" if expected else "missing",
        "currentRangeVsExpected": current_range / expected if current_range is not None and expected and expected > 0 else None,
        "expectedRange": expected,
        "sampleSize": len(ranges),
        "minuteOfSession": session.minutes_from_open,
        "source": "regime_publisher_intraday_proxy",
    }


def _missing_bar_count(times: list[datetime]) -> int:
    missing = 0
    for left, right in zip(times, times[1:]):
        gap = int((right - left).total_seconds() // 60)
        if gap > 1 and _same_exchange_session(left, right):
            missing += gap - 1
    return missing


def _same_exchange_session(left: datetime, right: datetime) -> bool:
    left_session = exchange_session(_iso(left))
    right_session = exchange_session(_iso(right))
    return (
        left_session.status != "outside_regular"
        and right_session.status != "outside_regular"
        and left_session.session_date == right_session.session_date
    )


def _redact_clock(clock: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "isOpen", "timestamp", "nextOpen", "nextClose", "session", "warning"}
    return {key: value for key, value in clock.items() if key in allowed}


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


__all__ = [
    "REGIME_FINALIZED_ONE_MINUTE_PUBLISHER_VERSION",
    "REGIME_REQUIRED_COMPLETED_HISTORY_BARS",
    "RegimeFinalizedOneMinutePublisher",
    "RegimeFinalizedOneMinutePublisherConfig",
    "RegimePublicationResult",
    "RegimePublisherPollResult",
]
