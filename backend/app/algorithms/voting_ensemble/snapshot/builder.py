"""Snapshot builders for Voting Ensemble live, replay, and backtest evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.algorithms.voting_ensemble.snapshot.features import session_features
from backend.app.algorithms.voting_ensemble.snapshot.models import (
    AggregatedTimeframeEvidence,
    BreadthPointInTimeData,
    EventStateSnapshot,
    FinalizedCandleEvidence,
    LevelSnapshot,
    NBBOSnapshot,
    SymbolPointInTimeData,
    VotingEnsembleEvaluationSnapshot,
    VotingEnsembleReadinessDecision,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.algorithms.voting_ensemble.trading_settings.hashing import trading_settings_hash


VOTING_ENSEMBLE_SNAPSHOT_BUILDER_VERSION = "voting_ensemble_snapshot_builder_v1"
MAX_QUOTE_AGE_SECONDS = 5.0
MAX_RECEIPT_AGE_SECONDS = 10.0


def build_live_paper_snapshot(payload: dict[str, Any]) -> VotingEnsembleEvaluationSnapshot:
    return build_point_in_time_snapshot(payload)


def build_replay_snapshot(payload: dict[str, Any]) -> VotingEnsembleEvaluationSnapshot:
    return build_point_in_time_snapshot(payload)


def build_backtest_snapshot(payload: dict[str, Any]) -> VotingEnsembleEvaluationSnapshot:
    return build_point_in_time_snapshot(payload)


def build_point_in_time_snapshot(payload: dict[str, Any]) -> VotingEnsembleEvaluationSnapshot:
    evaluation_timestamp = _payload_evaluation_timestamp(payload)
    settings_hash = _payload_settings_hash(payload)
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    failures: list[str] = []
    stale: list[str] = []
    malformed: list[str] = []

    spy = _finalized_prefix(payload.get("candles") or [], evaluation_timestamp, failures, "SPY")
    five = _finalized_prefix(payload.get("spy_5m_candles") or [], evaluation_timestamp, failures, "SPY_5M")
    if not five:
        five = _aggregate_finalized(spy, 5, evaluation_timestamp)
    fifteen = _finalized_prefix(payload.get("spy_15m_candles") or [], evaluation_timestamp, failures, "SPY_15M")
    if not fifteen:
        fifteen = _aggregate_finalized(spy, 15, evaluation_timestamp)
    qqq = _symbol_data("QQQ", payload.get("qqq_candles") or [], evaluation_timestamp, failures)
    iwm = _symbol_data("IWM", payload.get("iwm_candles") or [], evaluation_timestamp, failures)
    breadth = _breadth_data(payload, evaluation_timestamp, failures, stale, malformed)
    nbbo = _nbbo(payload, context, evaluation_timestamp, failures, stale, malformed)
    event_state = _event_state(context, evaluation_timestamp, failures, stale, malformed)
    prior_day = _levels(context.get("priorDayOHLC") if isinstance(context.get("priorDayOHLC"), dict) else context, ("priorDayHigh", "priorDayLow", "priorDayOpen", "priorDayClose"))
    premarket = _levels(context.get("premarket") if isinstance(context.get("premarket"), dict) else context, ("premarketHigh", "premarketLow", "premarketOpen", "premarketClose"))
    opening_range = _opening_range(context, spy)
    latest_finalization = spy[-1].finalizationTimestamp if spy else evaluation_timestamp

    if not spy:
        failures.append("missing_finalised_spy_one_minute_candles")
    if not qqq.candles:
        stale.append("missing_qqq_point_in_time_data")
    if not iwm.candles:
        stale.append("missing_iwm_point_in_time_data")
    if breadth.timestamp is None:
        stale.append("missing_breadth_timestamp")

    ready = not failures and not stale and not malformed
    readiness = VotingEnsembleReadinessDecision(
        ready=ready,
        status="ready" if ready else "fail_closed",
        mandatoryFailures=tuple(dict.fromkeys(failures)),
        staleInputs=tuple(dict.fromkeys(stale)),
        malformedInputs=tuple(dict.fromkeys(malformed)),
        reasonCodes=("voting_ensemble.snapshot.ready" if ready else "voting_ensemble.snapshot.fail_closed",),
    )
    snapshot_without_hash = {
        "algorithmId": "voting_ensemble",
        "snapshotVersion": "voting_ensemble_point_in_time_snapshot_v1",
        "symbol": "SPY",
        "spyOneMinuteCandles": spy,
        "aggregatedFiveMinuteEvidence": AggregatedTimeframeEvidence(timeframe="5Min", candles=tuple(five)),
        "aggregatedFifteenMinuteEvidence": AggregatedTimeframeEvidence(timeframe="15Min", candles=tuple(fifteen)),
        "nbbo": nbbo,
        "qqq": qqq,
        "iwm": iwm,
        "breadth": breadth,
        "features": session_features(tuple(item.candle for item in spy)),
        "priorDayLevels": prior_day,
        "premarketLevels": premarket,
        "openingRangeLevels": opening_range,
        "economicEventState": event_state,
        "sessionState": dict(context.get("sessionState") or {}),
        "marketForecast": dict(context.get("marketForecast") or {}),
        "accountRiskSnapshot": dict(context.get("accountRiskSnapshot") or context.get("accountRisk") or {}),
        "operationalHealthSnapshot": dict(context.get("operationalHealthSnapshot") or context.get("operationalHealth") or {}),
        "settingsHash": settings_hash,
        "evaluationTimestamp": evaluation_timestamp,
        "barFinalizationTimestamp": latest_finalization,
        "feedHealthStatus": readiness.status,
        "dataReadiness": readiness,
        "snapshotHash": "pending",
        "reasonCodes": (
            VOTING_ENSEMBLE_SNAPSHOT_BUILDER_VERSION,
            "voting_ensemble.snapshot.point_in_time",
            *readiness.reasonCodes,
        ),
    }
    snapshot_hash = trading_settings_hash(snapshot_without_hash)
    return VotingEnsembleEvaluationSnapshot.model_validate({**snapshot_without_hash, "snapshotHash": snapshot_hash})


def _payload_evaluation_timestamp(payload: dict[str, Any]) -> datetime:
    raw = payload.get("data_timestamp")
    if raw is None and isinstance(payload.get("candles"), list) and payload["candles"]:
        raw = payload["candles"][-1].get("timestamp") if isinstance(payload["candles"][-1], dict) else getattr(payload["candles"][-1], "timestamp", None)
    parsed = _timestamp(raw)
    if parsed is None:
        raise ValueError("Voting Ensemble snapshot requires an evaluation timestamp")
    return parsed


def _payload_settings_hash(payload: dict[str, Any]) -> str:
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    explicit = context.get("settingsHash") or context.get("settings_hash") or payload.get("settingsHash")
    if explicit:
        return str(explicit)
    return resolve_one_minute_trading_settings(payload.get("settings") or payload.get("tradingSettings") or {}).configurationHash


def _finalized_prefix(raw_candles: Iterable[Any], evaluation_timestamp: datetime, failures: list[str], label: str) -> tuple[FinalizedCandleEvidence, ...]:
    result: list[FinalizedCandleEvidence] = []
    for raw in raw_candles:
        try:
            candle = raw if isinstance(raw, VotingCandle) else VotingCandle.model_validate(_candle_payload(raw))
        except ValueError:
            failures.append(f"malformed_candle:{label}")
            continue
        candle_ts = _utc(candle.timestamp)
        if candle_ts > evaluation_timestamp:
            continue
        completion = candle_ts
        finalized_at = _timestamp(_raw_value(raw, "finalizationTimestamp", "finalizedAt", "finalisedAt")) or completion
        finalized_at = _utc(finalized_at)
        if finalized_at > evaluation_timestamp:
            continue
        result.append(
            FinalizedCandleEvidence(
                candle=candle,
                completionTimestamp=completion,
                finalizationTimestamp=finalized_at,
                finalizationLagSeconds=round(max(0.0, (finalized_at - completion).total_seconds()), 6),
            )
        )
    return tuple(sorted(result, key=lambda item: item.candle.timestamp))


def _aggregate_finalized(source: tuple[FinalizedCandleEvidence, ...], size: int, evaluation_timestamp: datetime) -> tuple[FinalizedCandleEvidence, ...]:
    aggregated: list[FinalizedCandleEvidence] = []
    for index in range(size - 1, len(source), size):
        bucket = source[index - size + 1 : index + 1]
        candle = VotingCandle(
            timestamp=bucket[-1].candle.timestamp,
            open=bucket[0].candle.open,
            high=max(item.candle.high for item in bucket),
            low=min(item.candle.low for item in bucket),
            close=bucket[-1].candle.close,
            volume=sum(item.candle.volume for item in bucket),
        )
        finalized_at = max(item.finalizationTimestamp for item in bucket)
        if finalized_at <= evaluation_timestamp:
            aggregated.append(
                FinalizedCandleEvidence(
                    candle=candle,
                    completionTimestamp=max(item.completionTimestamp for item in bucket),
                    finalizationTimestamp=finalized_at,
                    finalizationLagSeconds=round(max(0.0, (finalized_at - max(item.completionTimestamp for item in bucket)).total_seconds()), 6),
                )
            )
    return tuple(aggregated)


def _symbol_data(symbol: str, candles: Iterable[Any], evaluation_timestamp: datetime, failures: list[str]) -> SymbolPointInTimeData:
    finalized = _finalized_prefix(candles, evaluation_timestamp, failures, symbol)
    latest = finalized[-1].candle if finalized else None
    return SymbolPointInTimeData(
        symbol=symbol,
        candles=finalized,
        latestClose=latest.close if latest else None,
        latestTimestamp=latest.timestamp if latest else None,
    )


def _breadth_data(payload: dict[str, Any], evaluation_timestamp: datetime, failures: list[str], stale: list[str], malformed: list[str]) -> BreadthPointInTimeData:
    components = {
        str(symbol).upper(): _symbol_data(str(symbol).upper(), candles, evaluation_timestamp, failures)
        for symbol, candles in (payload.get("breadth_components") or {}).items()
    }
    feed = payload.get("external_breadth_feed")
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    if not isinstance(feed, dict):
        for key in ("externalBreadthFeed", "breadthFeed", "marketBreadth"):
            if isinstance(context.get(key), dict):
                feed = context[key]
                break
    provider_ts = _timestamp(_feed_value(feed, "providerTimestamp", "sourceTimestamp", "timestamp")) if isinstance(feed, dict) else None
    receipt_ts = _timestamp(_feed_value(feed, "receiptTimestamp", "receivedAt", "marketDataReceiptTimestamp")) if isinstance(feed, dict) else None
    timestamp = provider_ts or receipt_ts or max((data.latestTimestamp for data in components.values() if data.latestTimestamp), default=None)
    for name, candidate in (("breadth_provider_timestamp", provider_ts), ("breadth_receipt_timestamp", receipt_ts)):
        if candidate and candidate > evaluation_timestamp:
            stale.append(f"future_{name}")
    if isinstance(feed, dict) and provider_ts is None and receipt_ts is None:
        malformed.append("breadth_event_missing_provider_or_receipt_timestamp")
    return BreadthPointInTimeData(
        components={symbol: data for symbol, data in components.items() if data.candles},
        externalFeed=feed if isinstance(feed, dict) else None,
        timestamp=timestamp,
        providerTimestamp=provider_ts,
        receiptTimestamp=receipt_ts,
    )


def _nbbo(payload: dict[str, Any], context: dict[str, Any], evaluation_timestamp: datetime, failures: list[str], stale: list[str], malformed: list[str]) -> NBBOSnapshot | None:
    source = payload.get("nbbo") if isinstance(payload.get("nbbo"), dict) else context.get("nbbo")
    if not isinstance(source, dict):
        failures.append("missing_spy_nbbo")
        return None
    bid = _positive_number(source, "bid")
    ask = _positive_number(source, "ask")
    bid_size = _positive_number(source, "bidSize", "bid_size")
    ask_size = _positive_number(source, "askSize", "ask_size")
    quote_ts = _timestamp(_feed_value(source, "quoteTimestamp", "timestamp"))
    trade_ts = _timestamp(_feed_value(source, "lastTradeTimestamp", "last_trade_timestamp"))
    receipt_ts = _timestamp(_feed_value(source, "marketDataReceiptTimestamp", "receiptTimestamp", "receivedAt"))
    if None in (bid, ask, bid_size, ask_size, quote_ts, trade_ts, receipt_ts):
        malformed.append("malformed_spy_nbbo")
        return None
    assert bid is not None and ask is not None and bid_size is not None and ask_size is not None and quote_ts is not None and trade_ts is not None and receipt_ts is not None
    if quote_ts > evaluation_timestamp or trade_ts > evaluation_timestamp or receipt_ts > evaluation_timestamp:
        stale.append("future_spy_nbbo_timestamp")
        return None
    quote_age = (evaluation_timestamp - quote_ts).total_seconds()
    receipt_age = (evaluation_timestamp - receipt_ts).total_seconds()
    if quote_age > float(source.get("maxQuoteAgeSeconds") or MAX_QUOTE_AGE_SECONDS):
        stale.append("stale_spy_quote")
    if receipt_age > float(source.get("maxReceiptAgeSeconds") or MAX_RECEIPT_AGE_SECONDS):
        stale.append("stale_market_data_receipt")
    midpoint = (bid + ask) / 2.0
    spread = ask - bid
    try:
        return NBBOSnapshot(
            bid=bid,
            ask=ask,
            bidSize=bid_size,
            askSize=ask_size,
            spreadDollars=round(spread, 6),
            spreadBasisPoints=round((spread / midpoint) * 10000.0, 6) if midpoint > 0 else 0.0,
            quoteTimestamp=quote_ts,
            lastTradeTimestamp=trade_ts,
            marketDataReceiptTimestamp=receipt_ts,
            marketDataAgeSeconds=round(receipt_age, 6),
            quoteAgeSeconds=round(quote_age, 6),
        )
    except ValueError:
        malformed.append("malformed_spy_nbbo")
        return None


def _unwrap_event_state(event: dict[str, Any]) -> dict[str, Any]:
    """Accept either a raw event state or a serialised EventStateSnapshot.

    `to_evaluate_payload` re-emits the snapshot's own dump under "event", so a rebuild from
    that payload sees {"state": {...}} where the first build saw {...}. Anything evaluated
    through the payload therefore lost the blackout the first build had recorded, silently:
    the flag is nested one level below where the gate reads it. Unwrapping here keeps
    build(to_evaluate_payload(build(x))) equal to build(x), which is the invariant replay
    depends on to reproduce a gated live run.

    A raw state carries `state` as a string, so a dict under that key is unambiguously the
    wrapper rather than a legitimate value.
    """
    inner = event.get("state")
    if not isinstance(inner, dict):
        return event
    unwrapped = dict(inner)
    for key in ("providerTimestamp", "receiptTimestamp"):
        if unwrapped.get(key) is None and event.get(key) is not None:
            unwrapped[key] = event[key]
    return unwrapped


def _event_state(context: dict[str, Any], evaluation_timestamp: datetime, failures: list[str], stale: list[str], malformed: list[str]) -> EventStateSnapshot:
    event = context.get("event") if isinstance(context.get("event"), dict) else {}
    event = _unwrap_event_state(event)
    provider_ts = _timestamp(_feed_value(event, "providerTimestamp", "sourceTimestamp", "timestamp")) if event else None
    receipt_ts = _timestamp(_feed_value(event, "receiptTimestamp", "receivedAt")) if event else None
    if event and provider_ts is None and receipt_ts is None:
        malformed.append("economic_event_missing_provider_or_receipt_timestamp")
    if provider_ts and provider_ts > evaluation_timestamp:
        stale.append("future_economic_event_provider_timestamp")
    if receipt_ts and receipt_ts > evaluation_timestamp:
        stale.append("future_economic_event_receipt_timestamp")
    return EventStateSnapshot(state=dict(event), providerTimestamp=provider_ts, receiptTimestamp=receipt_ts)


def _levels(source: dict[str, Any] | None, keys: tuple[str, str, str, str]) -> LevelSnapshot:
    source = source or {}
    return LevelSnapshot(
        high=_number(source, "high", keys[0]),
        low=_number(source, "low", keys[1]),
        open=_number(source, "open", keys[2]),
        close=_number(source, "close", keys[3]),
    )


def _opening_range(context: dict[str, Any], spy: tuple[FinalizedCandleEvidence, ...]) -> LevelSnapshot:
    existing = context.get("openingRange") if isinstance(context.get("openingRange"), dict) else None
    if existing:
        return _levels(existing, ("high", "low", "open", "close"))
    opening = spy[:15]
    if not opening:
        return LevelSnapshot()
    return LevelSnapshot(
        high=max(item.candle.high for item in opening),
        low=min(item.candle.low for item in opening),
        open=opening[0].candle.open,
        close=opening[-1].candle.close,
    )


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str) and value:
        try:
            return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _raw_value(raw: Any, *keys: str) -> Any:
    if isinstance(raw, dict):
        for key in keys:
            if key in raw:
                return raw[key]
    for key in keys:
        if hasattr(raw, key):
            return getattr(raw, key)
    return None


def _candle_payload(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    return {key: raw[key] for key in ("timestamp", "open", "high", "low", "close", "volume") if key in raw}


def _feed_value(feed: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(feed, dict):
        return None
    for key in keys:
        if key in feed:
            return feed[key]
    return None


def _positive_number(source: dict[str, Any], *keys: str) -> float | None:
    value = _number(source, *keys)
    return value if value is not None and value > 0 else None


def _number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in source:
            continue
        try:
            value = float(source[key])
        except (TypeError, ValueError):
            return None
        return value
    return None
