"""Background finalized one-minute candle producer for Meta-Strategy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRecord, MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
from backend.app.database import CandleStore


META_STRATEGY_FINALIZED_CANDLE_PRODUCER_VERSION = "meta_strategy_finalized_candle_producer_v1"


class MetaStrategyMarketDataClient(Protocol):
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


@dataclass(frozen=True)
class MetaStrategyFinalizedCandleProducerConfig:
    symbols: tuple[str, ...] = ("SPY",)
    feed: str = "iex"
    timeframe: str = "1Min"
    event_timeframe: str = "1m"
    mode: str = "SHADOW"
    capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION
    warmup_bars: int = 30
    fetch_limit: int = 120
    finalization_delay_seconds: int = 2
    max_staleness_seconds: int = 180


@dataclass(frozen=True)
class MetaStrategyCandleProductionResult:
    symbol: str
    status: str
    reason_codes: tuple[str, ...]
    bar_end: str | None = None
    event_id: str | None = None
    job_id: str | None = None
    duplicate: bool = False
    data_quality_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": ALGORITHM_ID,
            "producerVersion": META_STRATEGY_FINALIZED_CANDLE_PRODUCER_VERSION,
            "symbol": self.symbol,
            "status": self.status,
            "reasonCodes": self.reason_codes,
            "barEnd": self.bar_end,
            "eventId": self.event_id,
            "jobId": self.job_id,
            "duplicate": self.duplicate,
            "dataQualityState": self.data_quality_state,
        }


class MetaStrategyFinalizedCandleProducer:
    def __init__(
        self,
        *,
        market_data_client: MetaStrategyMarketDataClient,
        candle_store: CandleStore,
        job_repository: MetaStrategyJobRepository,
        settings_store: MetaStrategySettingsStore,
        config: MetaStrategyFinalizedCandleProducerConfig | None = None,
    ) -> None:
        self.market_data_client = market_data_client
        self.candle_store = candle_store
        self.job_repository = job_repository
        self.settings_store = settings_store
        self.config = config or MetaStrategyFinalizedCandleProducerConfig()

    async def poll_once(self, *, now: datetime | None = None) -> tuple[dict[str, Any], ...]:
        current = _as_utc(now or datetime.now(UTC))
        results = []
        for symbol in self.config.symbols:
            results.append((await self.process_symbol(symbol, now=current)).to_dict())
        return tuple(results)

    async def process_symbol(self, symbol: str, *, now: datetime | None = None) -> MetaStrategyCandleProductionResult:
        current = _as_utc(now or datetime.now(UTC))
        normalized_symbol = symbol.upper()
        fetched = await self.market_data_client.get_bars(
            symbol=normalized_symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=max(self.config.fetch_limit, self.config.warmup_bars + 20),
            start=None,
            end=current.isoformat(),
            sort="asc",
        )
        valid_rows, invalid = _valid_candles(fetched, symbol=normalized_symbol, timeframe=self.config.timeframe, feed=self.config.feed, now=current)
        if valid_rows:
            self.candle_store.upsert_many(valid_rows)
            self._persist_derived_timeframes(normalized_symbol, now=current)
        if invalid:
            self._record_data_quality(normalized_symbol, "INVALID_CANDLES", invalid, now=current)

        final_rows = [row for row in valid_rows if _is_final(row, now=current, delay_seconds=self.config.finalization_delay_seconds)]
        if not final_rows:
            state = {"status": "NO_FINALIZED_CANDLE", "invalidCount": len(invalid)}
            self._record_data_quality(normalized_symbol, "NO_FINALIZED_CANDLE", state, now=current)
            return MetaStrategyCandleProductionResult(normalized_symbol, "BLOCKED", ("meta_strategy.candle.no_finalized_candle",), data_quality_state=state)

        candidate = final_rows[-1]
        bar_end = _parse_dt(str(candidate["timestamp"]))
        stale_seconds = int((current - bar_end).total_seconds())
        if stale_seconds > self.config.max_staleness_seconds:
            state = {"status": "STALE", "barEnd": bar_end.isoformat(), "staleSeconds": stale_seconds}
            self._record_data_quality(normalized_symbol, "STALE_FINALIZED_CANDLE", state, now=current)
            return MetaStrategyCandleProductionResult(normalized_symbol, "BLOCKED", ("meta_strategy.candle.stale",), bar_end=bar_end.isoformat(), data_quality_state=state)

        history = self.candle_store.latest_until(
            symbol=normalized_symbol,
            timeframe=self.config.timeframe,
            feed=self.config.feed,
            limit=self.config.warmup_bars,
            end=bar_end.isoformat(),
        )
        quality = _history_quality(history, required=self.config.warmup_bars, bar_end=bar_end)
        if quality["status"] != "OK":
            self._record_data_quality(normalized_symbol, str(quality["status"]), quality, now=current)
            return MetaStrategyCandleProductionResult(
                normalized_symbol,
                "BLOCKED",
                tuple(quality["reasonCodes"]),
                bar_end=bar_end.isoformat(),
                data_quality_state=quality,
            )

        latest_enqueued = self._latest_enqueued_bar_end(normalized_symbol)
        if latest_enqueued is not None and bar_end < latest_enqueued:
            state = {"status": "OUT_OF_ORDER", "barEnd": bar_end.isoformat(), "latestEnqueuedBarEnd": latest_enqueued.isoformat()}
            self._record_data_quality(normalized_symbol, "OUT_OF_ORDER_FINALIZED_CANDLE", state, now=current)
            return MetaStrategyCandleProductionResult(normalized_symbol, "BLOCKED", ("meta_strategy.candle.out_of_order",), bar_end=bar_end.isoformat(), data_quality_state=state)

        settings = self.settings_store.get_active_settings()
        started = perf_counter()
        finalization_delay_ms = int(max(0.0, (current - bar_end).total_seconds()) * 1000)
        job = self.job_repository.enqueue_finalised_bar_decision(
            mode=self.config.mode,
            symbol=normalized_symbol,
            timeframe=self.config.event_timeframe,
            bar_end=bar_end,
            settings_version=settings.settings_version,
            capital_partition_id=self.config.capital_partition_id,
            payload={
                "source": "meta_strategy_background_finalized_candle_producer",
                "producerVersion": META_STRATEGY_FINALIZED_CANDLE_PRODUCER_VERSION,
                "candle": dict(candidate),
                "dataQualityState": quality,
                "higherTimeframePolicy": "derived_point_in_time_from_finalized_one_minute",
                "derivedHigherTimeframes": self._derived_availability(normalized_symbol, bar_end=bar_end),
                "latencyMeasurements": {
                    "candleFinalizationDelayMs": finalization_delay_ms,
                    "queueDelayMs": 0,
                    "producerEnqueueDurationMs": int((perf_counter() - started) * 1000),
                    "snapshotBuildingTimeMs": None,
                    "strategyEvaluationTimeMs": None,
                    "inferenceTimeMs": None,
                    "decisionPersistenceTimeMs": None,
                    "orderSubmissionTimeMs": None,
                },
            },
            now=current,
        )
        event_id = _job_event_id(self.job_repository, job)
        self.job_repository.enqueue_job(
            job_type="position_management",
            idempotency_key=f"meta_strategy.position_management.finalized_candle.{self.config.capital_partition_id}.{normalized_symbol}.{bar_end.isoformat()}.{settings.settings_version}",
            payload={
                "source": "meta_strategy_background_finalized_candle_producer",
                "trigger": "finalized_one_minute_candle",
                "capitalPartitionId": self.config.capital_partition_id,
                "settingsVersion": settings.settings_version,
                "eventId": event_id,
                "decisionId": f"meta_strategy.position_management.{normalized_symbol}.{bar_end.isoformat()}",
                "correlationId": f"{normalized_symbol}:{bar_end.isoformat()}",
                "symbol": normalized_symbol,
                "candle": dict(candidate),
                "markPrices": {normalized_symbol: float(candidate["close"])},
                "mode": self.config.mode,
            },
            now=current,
        )
        self.job_repository.record_operational_event(
            "finalised_candle_enqueued",
            {
                "symbol": normalized_symbol,
                "barEnd": bar_end.isoformat(),
                "jobId": job.job_id,
                "eventId": event_id,
                "duplicate": job.duplicate,
                "capitalPartitionId": self.config.capital_partition_id,
                "settingsVersion": settings.settings_version,
                "latencyMeasurements": {"candleFinalizationDelayMs": finalization_delay_ms},
            },
            correlation_id=f"{normalized_symbol}:{bar_end.isoformat()}",
            now=current,
        )
        return MetaStrategyCandleProductionResult(
            normalized_symbol,
            "ENQUEUED" if not job.duplicate else "DUPLICATE",
            ("meta_strategy.candle.finalized_event_enqueued" if not job.duplicate else "meta_strategy.candle.duplicate_suppressed",),
            bar_end=bar_end.isoformat(),
            event_id=event_id,
            job_id=job.job_id,
            duplicate=job.duplicate,
            data_quality_state=quality,
        )

    def _persist_derived_timeframes(self, symbol: str, *, now: datetime) -> None:
        start = (now - timedelta(minutes=max(60, self.config.warmup_bars + 20))).isoformat()
        one_minute = self.candle_store.range(symbol=symbol, timeframe=self.config.timeframe, feed=self.config.feed, start=start, end=now.isoformat())
        derived = []
        for timeframe, minutes in (("5Min", 5), ("15Min", 15)):
            derived.extend(_derive_complete_bars(one_minute, timeframe=timeframe, minutes=minutes, now=now))
        if derived:
            self.candle_store.upsert_many(derived)

    def _derived_availability(self, symbol: str, *, bar_end: datetime) -> dict[str, Any]:
        return {
            "fiveMinute": self._latest_complete_derived(symbol, timeframe="5Min", bar_end=bar_end),
            "fifteenMinute": self._latest_complete_derived(symbol, timeframe="15Min", bar_end=bar_end),
        }

    def _latest_complete_derived(self, symbol: str, *, timeframe: str, bar_end: datetime) -> dict[str, Any] | None:
        rows = self.candle_store.latest_until(symbol=symbol, timeframe=timeframe, feed=self.config.feed, limit=1, end=bar_end.isoformat())
        return rows[-1] if rows else None

    def _latest_enqueued_bar_end(self, symbol: str) -> datetime | None:
        for event in self.job_repository.operational_events(event_type="finalised_candle_enqueued", limit=200):
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            if str(payload.get("symbol") or "").upper() == symbol.upper() and payload.get("barEnd"):
                return _parse_dt(str(payload["barEnd"]))
        return None

    def _record_data_quality(self, symbol: str, status: str, payload: Mapping[str, Any], *, now: datetime) -> None:
        self.job_repository.record_operational_event(
            "finalised_candle_data_quality",
            {"symbol": symbol, "status": status, "payload": dict(payload)},
            status=status,
            correlation_id=f"{symbol}:{status}",
            now=now,
        )


def _valid_candles(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    feed: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid = []
    invalid = []
    for row in rows:
        candidate = {**dict(row), "symbol": symbol, "timeframe": timeframe, "feed": feed, "provider": str(row.get("provider") or "market_data")}
        reasons = _candle_rejection_reasons(candidate, now=now)
        if reasons:
            invalid.append({"timestamp": candidate.get("timestamp"), "reasonCodes": reasons})
        else:
            valid.append(_normalize_candle(candidate))
    return sorted(valid, key=lambda item: item["timestamp"]), invalid


def _candle_rejection_reasons(candle: Mapping[str, Any], *, now: datetime) -> tuple[str, ...]:
    reasons: list[str] = []
    try:
        timestamp = _parse_dt(str(candle.get("timestamp") or ""))
    except Exception:
        timestamp = None
        reasons.append("meta_strategy.candle.timestamp_required")
    if timestamp is not None and timestamp > now:
        reasons.append("meta_strategy.candle.future_timestamp")
    if candle.get("finalized") is False or candle.get("finalised") is False:
        reasons.append("meta_strategy.candle.not_final")
    try:
        open_, high, low, close = (float(candle[key]) for key in ("open", "high", "low", "close"))
        volume = float(candle.get("volume") or 0)
    except Exception:
        return tuple(dict.fromkeys([*reasons, "meta_strategy.candle.ohlcv_required"]))
    if min(open_, high, low, close) <= 0 or volume < 0:
        reasons.append("meta_strategy.candle.invalid_ohlcv")
    if high < max(open_, close, low) or low > min(open_, close, high):
        reasons.append("meta_strategy.candle.invalid_ohlc_relationship")
    return tuple(dict.fromkeys(reasons))


def _normalize_candle(candle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(candle.get("provider") or "market_data"),
        "feed": str(candle["feed"]),
        "symbol": str(candle["symbol"]).upper(),
        "timeframe": str(candle["timeframe"]),
        "timestamp": _parse_dt(str(candle["timestamp"])).isoformat(),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": int(float(candle.get("volume") or 0)),
        "trade_count": int(candle["trade_count"]) if candle.get("trade_count") is not None else None,
        "vwap": float(candle["vwap"]) if candle.get("vwap") is not None else None,
    }


def _is_final(candle: Mapping[str, Any], *, now: datetime, delay_seconds: int) -> bool:
    return _parse_dt(str(candle["timestamp"])) <= now - timedelta(seconds=max(0, delay_seconds))


def _history_quality(history: list[dict[str, Any]], *, required: int, bar_end: datetime) -> dict[str, Any]:
    timestamps = [_parse_dt(str(row["timestamp"])) for row in history]
    gaps = []
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != timedelta(minutes=1):
            gaps.append({"previous": previous.isoformat(), "current": current.isoformat()})
    if gaps:
        return {"status": "SEQUENCE_GAP", "gaps": gaps, "reasonCodes": ("meta_strategy.candle.sequence_gap",)}
    if len(history) < required:
        return {
            "status": "WARMUP_MISSING",
            "availableBars": len(history),
            "requiredBars": required,
            "reasonCodes": ("meta_strategy.candle.warmup_missing",),
        }
    if timestamps[-1] != bar_end:
        return {"status": "LATEST_BAR_MISMATCH", "reasonCodes": ("meta_strategy.candle.latest_bar_mismatch",)}
    return {"status": "OK", "availableBars": len(history), "requiredBars": required, "reasonCodes": ("meta_strategy.candle.quality_ok",)}


def _derive_complete_bars(one_minute: list[dict[str, Any]], *, timeframe: str, minutes: int, now: datetime) -> list[dict[str, Any]]:
    by_timestamp = {_parse_dt(str(row["timestamp"])): row for row in one_minute}
    derived = []
    for end in sorted(by_timestamp):
        if end > now or end.minute % minutes != 0:
            continue
        interval = [end - timedelta(minutes=offset) for offset in range(minutes - 1, -1, -1)]
        if any(timestamp not in by_timestamp for timestamp in interval):
            continue
        rows = [by_timestamp[timestamp] for timestamp in interval]
        derived.append(
            {
                "provider": rows[-1]["provider"],
                "feed": rows[-1]["feed"],
                "symbol": rows[-1]["symbol"],
                "timeframe": timeframe,
                "timestamp": end.isoformat(),
                "open": float(rows[0]["open"]),
                "high": max(float(row["high"]) for row in rows),
                "low": min(float(row["low"]) for row in rows),
                "close": float(rows[-1]["close"]),
                "volume": sum(int(row.get("volume") or 0) for row in rows),
                "trade_count": None,
                "vwap": None,
            }
        )
    return derived


def _job_event_id(repository: MetaStrategyJobRepository, job: MetaStrategyJobRecord) -> str:
    payload = repository.read_payload(job.payload_reference)
    nested = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else payload
    return str(nested.get("eventId") or nested.get("event_id") or "")


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("meta_strategy.candle.timestamp_timezone_required")
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("meta_strategy.candle.timestamp_timezone_required")
    return value.astimezone(UTC)


__all__ = [
    "META_STRATEGY_FINALIZED_CANDLE_PRODUCER_VERSION",
    "MetaStrategyCandleProductionResult",
    "MetaStrategyFinalizedCandleProducer",
    "MetaStrategyFinalizedCandleProducerConfig",
    "MetaStrategyMarketDataClient",
]
