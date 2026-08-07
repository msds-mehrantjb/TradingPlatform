from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .alpaca import AlpacaClient, demo_bars, local_market_status
from .algorithms.voting_ensemble.strategies.registry import (
    STRATEGY_ALIAS_MAP as VOTING_ENSEMBLE_ALIAS_MAP,
    VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
    VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    VOTING_ENSEMBLE_REGIME_STRATEGIES,
    VOTING_ENSEMBLE_SAFETY_STRATEGIES,
    resolve_strategy as resolve_voting_ensemble_strategy,
)
from .config import get_settings
from .database import CandleStore
from .market_context import compute_market_context
from .market_forecast import MARKET_FORECAST_SERVICE


settings = get_settings()
store = CandleStore(settings)
alpaca = AlpacaClient(settings)

MARKET_DATA_CACHE_TTL_SECONDS = 5.0
CANDLE_REFRESH_TIMEOUT_SECONDS = 8.0
DEFAULT_LOOKBACKS = {
    "1Hour": timedelta(days=180),
    "1Day": timedelta(days=900),
}
_RESPONSE_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

app = FastAPI(title="Trading Dashboard Market Data API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "market-data",
        "port": 8021,
        "generatedAt": _now_iso(),
    }


@app.get("/api/market-status")
async def market_status() -> dict[str, Any]:
    try:
        return await asyncio.wait_for(alpaca.get_market_status(), timeout=5.0)
    except httpx.HTTPStatusError as exc:
        return local_market_status(warning=exc.response.text)
    except (TimeoutError, httpx.HTTPError) as exc:
        return local_market_status(warning=str(exc))


@app.get("/api/candles")
async def candles(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    timeframe: Literal["1Min", "3Min", "5Min", "15Min", "1Hour", "1Day"] = "1Min",
    limit: int = Query(240, ge=10, le=1000),
    start: str | None = None,
    end: str | None = None,
    sort: Literal["asc", "desc"] = "asc",
    refresh: bool = True,
) -> dict[str, Any]:
    normalized_symbol = symbol.upper()
    cache_key = ("candles", normalized_symbol, feed, timeframe, limit, start or "", end or "", sort, refresh)
    cached_response = _read_response_cache(cache_key)
    if cached_response is not None:
        return cached_response

    cached = _dedupe_candles(await _store_latest(symbol=normalized_symbol, timeframe=timeframe, feed=feed, limit=limit))
    if cached and not refresh:
        return _write_response_cache(cache_key, {"source": "cache", "candles": cached})

    request_start = start
    request_end = end
    request_sort = sort
    if not request_start and not request_end and timeframe in DEFAULT_LOOKBACKS:
        now = datetime.now(UTC)
        request_start = (now - DEFAULT_LOOKBACKS[timeframe]).isoformat().replace("+00:00", "Z")
        request_end = now.isoformat().replace("+00:00", "Z")
        request_sort = "desc"

    if refresh:
        try:
            fresh = await asyncio.wait_for(
                alpaca.get_bars(
                    symbol=normalized_symbol,
                    timeframe=timeframe,
                    feed=feed,
                    limit=limit,
                    start=request_start,
                    end=request_end,
                    sort=request_sort,
                ),
                timeout=CANDLE_REFRESH_TIMEOUT_SECONDS,
            )
            fresh = _dedupe_candles(fresh)
            await _store_upsert(fresh)
            return _write_response_cache(
                cache_key,
                {
                    "source": fresh[0]["provider"] if fresh else "alpaca",
                    "candles": fresh or cached,
                },
            )
        except (TimeoutError, httpx.HTTPError) as exc:
            if cached:
                return _write_response_cache(
                    cache_key,
                    {
                        "source": "cache",
                        "warning": f"Live candle refresh unavailable from market-data service: {exc}",
                        "candles": cached,
                    },
                )

    fallback = _dedupe_candles(demo_bars(symbol=normalized_symbol, timeframe=timeframe, feed=feed, limit=limit))
    await _store_upsert(fallback)
    return _write_response_cache(
        cache_key,
        {
            "source": "demo",
            "warning": "No cached/live candles were available from the market-data service; showing deterministic demo candles.",
            "candles": fallback,
        },
    )


@app.get("/api/market-data/quotes/latest")
async def latest_market_data_quote(symbol: str = Query("SPY"), feed: str = Query("iex")) -> dict[str, Any]:
    normalized_symbol = symbol.upper().strip() or "SPY"
    normalized_feed = feed.lower().strip() or "iex"
    if settings.has_alpaca_credentials:
        try:
            quote = await asyncio.wait_for(alpaca.get_latest_quote(symbol=normalized_symbol, feed=normalized_feed), timeout=5.0)
            if quote is not None:
                return {
                    "status": "ready",
                    "symbol": normalized_symbol,
                    "feed": normalized_feed,
                    "quote": quote,
                    "reasonCodes": ["market_data.nbbo.latest_quote_ready", "market_data.service.lightweight_market_data"],
                }
        except (TimeoutError, httpx.HTTPError):
            pass

    fallback = _latest_quote_from_cached_or_demo_candle(symbol=normalized_symbol, feed=normalized_feed)
    return {
        "status": "ready",
        "symbol": normalized_symbol,
        "feed": normalized_feed,
        "quote": fallback,
        "reasonCodes": ["market_data.nbbo.cached_candle_quote_fallback_ready", "market_data.service.lightweight_market_data"],
        "warning": "Latest quote feed unavailable; using a conservative cached/demo candle bid/ask fallback.",
    }


@app.get("/api/market-context")
async def market_context(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    refresh: bool = True,
    as_of: str | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.upper()
    should_refresh = refresh and as_of is None
    cache_key = ("market_context", normalized_symbol, feed, as_of or "", should_refresh)
    if not should_refresh:
        cached_response = _read_response_cache(cache_key)
        if cached_response is not None:
            return cached_response
    daily = await _context_bars(symbol=normalized_symbol, feed=feed, timeframe="1Day", limit=300, refresh=should_refresh, as_of=as_of)
    intraday = await _context_bars(symbol=normalized_symbol, feed=feed, timeframe="1Min", limit=1000, refresh=should_refresh, as_of=as_of)
    context = await asyncio.to_thread(compute_market_context, normalized_symbol, daily, intraday)
    context["sourceAuthority"] = "lightweight_market_data_service"
    return _write_response_cache(cache_key, context)


@app.get("/api/v2/algorithms/strategy-fit/inventory")
def strategy_fit_inventory() -> dict[str, Any]:
    modules = _voting_ensemble_strategy_catalog_modules()
    return {
        "algorithmId": "strategy_fit",
        "engineVersion": "strategy_fit_inventory_v1",
        "contractVersion": "strategy_fit_inventory_contract_v1",
        "displayName": "Strategy Fit",
        "sourceAlgorithmId": "voting_ensemble",
        "sourceEngineVersion": "voting_ensemble_v2",
        "sourceAuthority": "lightweight_market_data_service.voting_ensemble.strategy_catalog",
        "sourceEndpoint": "/api/v2/algorithms/strategy-fit/inventory",
        "modules": modules,
    }


@app.get("/api/market-forecast/prediction")
async def market_forecast_prediction(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    timeframe: Literal["1Min"] = "1Min",
    limit: int = Query(240, ge=60, le=1000),
    refresh: bool = False,
) -> dict[str, Any]:
    normalized_symbol = symbol.upper()
    cache_key = ("market_forecast_prediction", normalized_symbol, feed, timeframe, limit, refresh)
    cached_response = _read_response_cache(cache_key)
    if cached_response is not None:
        return cached_response

    candles_for_prediction = _dedupe_candles(await _store_latest(symbol=normalized_symbol, timeframe=timeframe, feed=feed, limit=limit))
    if refresh and settings.has_alpaca_credentials:
        try:
            fresh = await asyncio.wait_for(
                alpaca.get_bars(
                    symbol=normalized_symbol,
                    timeframe=timeframe,
                    feed=feed,
                    limit=limit,
                    start=None,
                    end=None,
                    sort="asc",
                ),
                timeout=min(3.0, CANDLE_REFRESH_TIMEOUT_SECONDS),
            )
            fresh = _dedupe_candles(fresh)
            await _store_upsert(fresh)
            candles_for_prediction = fresh or candles_for_prediction
        except (TimeoutError, httpx.HTTPError):
            pass
    if not candles_for_prediction:
        candles_for_prediction = _dedupe_candles(demo_bars(symbol=normalized_symbol, timeframe=timeframe, feed=feed, limit=limit))

    try:
        forecast = await asyncio.wait_for(
            asyncio.to_thread(MARKET_FORECAST_SERVICE.predict, candles_for_prediction, microstructure_rows=[]),
            timeout=3.0,
        )
        forecast["sourceAuthority"] = "lightweight_market_data_service.market_forecast_advisory"
        return _write_response_cache(cache_key, forecast)
    except (TimeoutError, Exception) as exc:
        latest = candles_for_prediction[-1] if candles_for_prediction else None
        return _write_response_cache(
            cache_key,
            _lightweight_forecast_unavailable(
                symbol=normalized_symbol,
                latest=latest,
                reason=f"Forecast inference is still warming up on the lightweight service: {type(exc).__name__}",
            ),
        )


async def _context_bars(
    *,
    symbol: str,
    feed: Literal["iex", "sip", "otc"],
    timeframe: Literal["1Min", "1Day"],
    limit: int,
    refresh: bool,
    as_of: str | None,
) -> list[dict[str, Any]]:
    cached = (
        _dedupe_candles(await _store_latest_until(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit, end=as_of))
        if as_of
        else _dedupe_candles(await _store_latest(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit))
    )
    if cached and not refresh:
        return cached
    if refresh:
        now = datetime.now(UTC)
        lookback = timedelta(days=900) if timeframe == "1Day" else timedelta(days=10)
        try:
            fresh = await asyncio.wait_for(
                alpaca.get_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    feed=feed,
                    limit=limit,
                    start=(now - lookback).isoformat().replace("+00:00", "Z"),
                    end=now.isoformat().replace("+00:00", "Z"),
                    sort="asc",
                ),
                timeout=CANDLE_REFRESH_TIMEOUT_SECONDS,
            )
            fresh = _dedupe_candles(fresh)
            await _store_upsert(fresh)
            return fresh or cached
        except (TimeoutError, httpx.HTTPError):
            pass
    return cached or _dedupe_candles(demo_bars(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit))


def _latest_quote_from_cached_or_demo_candle(*, symbol: str, feed: str) -> dict[str, Any]:
    candles = _dedupe_candles(store.latest(symbol=symbol, timeframe="1Min", feed=feed, limit=1))
    if not candles:
        candles = _dedupe_candles(demo_bars(symbol=symbol, timeframe="1Min", feed=feed, limit=1))
    candle = candles[-1]
    close = max(0.01, float(candle.get("close") or 0.01))
    spread = max(0.01, close * 0.0001)
    observed_at = _now_iso()
    return {
        "provider": str(candle.get("provider") or "cache"),
        "feed": feed,
        "symbol": symbol,
        "bid": round(max(0.01, close - (spread / 2.0)), 4),
        "ask": round(close + (spread / 2.0), 4),
        "bidSize": max(1, int(candle.get("volume") or 1)),
        "askSize": max(1, int(candle.get("volume") or 1)),
        "quoteTimestamp": observed_at,
        "lastTradeTimestamp": str(candle.get("timestamp") or observed_at),
        "marketDataReceiptTimestamp": observed_at,
        "source": "lightweight_cached_candle_quote_fallback",
        "markPricePolicy": "cached_or_demo_last_close_with_conservative_one_basis_point_spread",
        "referenceCandleTimestamp": candle.get("timestamp"),
        "referenceClose": close,
    }


def _voting_ensemble_strategy_catalog_modules() -> dict[str, list[dict[str, Any]]]:
    return {
        "directional": [_voting_ensemble_strategy_module_payload(entry) for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES if entry.enabled],
        "context": [_voting_ensemble_strategy_module_payload(entry) for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES],
        "regime": [_voting_ensemble_strategy_module_payload(entry) for entry in VOTING_ENSEMBLE_REGIME_STRATEGIES],
        "safety": [_voting_ensemble_strategy_module_payload(entry) for entry in VOTING_ENSEMBLE_SAFETY_STRATEGIES],
        "aggregator": [_voting_ensemble_strategy_module_payload(entry) for entry in VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES],
    }


def _lightweight_forecast_unavailable(*, symbol: str, latest: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    now = _now_iso()
    close = float((latest or {}).get("close") or 0.0)
    timestamp = str((latest or {}).get("timestamp") or now)
    horizons = [_lightweight_forecast_horizon(close, horizon, reason) for horizon in (5, 10, 15)]
    return {
        "forecastInvocationId": f"{symbol}|{now}|lightweight-fallback",
        "eventTimestamp": timestamp,
        "barFinalizationTimestamp": timestamp,
        "featureReadyTimestamp": None,
        "inferenceStartTimestamp": None,
        "inferenceEndTimestamp": None,
        "decisionTimestamp": now,
        "status": "INFERENCE_NOT_RUN",
        "forecastStatus": "INFERENCE_NOT_RUN",
        "forecast_status": "INFERENCE_NOT_RUN",
        "symbol": symbol,
        "horizonMinutes": 5,
        "probabilitySuccess": None,
        "probabilityBuySuccess": None,
        "probabilitySellSuccess": None,
        "probabilityStop": None,
        "probabilityTimeout": None,
        "probability_buy": None,
        "probability_sell": None,
        "outcome": {
            "predicted": "timeout_no_edge",
            "probabilities": {"target_hit_first": None, "stop_hit_first": None, "timeout_no_edge": None},
            "labels": {"target_hit_first": 1, "stop_hit_first": -1, "timeout_no_edge": 0},
        },
        "decision": {
            "action": "no_trade",
            "candidateAction": "no_trade",
            "confidence": None,
            "edgeGap": None,
            "minimumConfidence": 0.55,
            "minimumEdgeGap": 0.05,
            "modelDisagreement": None,
            "maximumModelDisagreement": 0.1,
            "expectedValue": None,
            "positionSizeMultiplier": 0.0,
            "reasons": [reason],
        },
        "threshold": 0.55,
        "minimumEdgeGap": 0.05,
        "maximumModelDisagreement": 0.1,
        "expectedValue": None,
        "buyExpectedValue": None,
        "sellExpectedValue": None,
        "barriers": {
            "targetDistance": None,
            "stopDistance": None,
            "fixedTargetDollars": 0.35,
            "minTargetPct": 0.0005,
            "minStopPct": 0.0005,
            "targetAtrMultiplier": 1.0,
            "stopAtrMultiplier": 1.0,
        },
        "expectedMove": None,
        "futurePricePrediction": _lightweight_no_edge_price_prediction(close, 5, reason),
        "multiHorizonForecast": {
            "status": "INFERENCE_NOT_RUN",
            "forecastStatus": "INFERENCE_NOT_RUN",
            "activationPolicy": "advisory_only_until_live_paper_validation",
            "positionManagementAuthority": "advisory_only",
            "entryAuthorization": False,
            "forecastAppliedToOrder": False,
            "positionManagementAppliedToOrder": False,
            "summary": {
                "primaryBias": "MODEL_UNAVAILABLE",
                "longPosition": "NO_ML_ADVICE",
                "shortPosition": "NO_ML_ADVICE",
                "newLongEntry": "WAIT_FOR_VALIDATED_MODEL",
                "readyHorizons": 0,
            },
            "horizons": horizons,
        },
        "costs": 0.0,
        "allowed": False,
        "inferencePerformed": False,
        "inference_performed": False,
        "forecastAppliedToOrder": False,
        "forecast_applied_to_order": False,
        "model": {
            "status": "warming_up",
            "kind": "lightweight_advisory_fallback",
            "message": reason,
        },
        "regime": {
            "trend": "unknown",
            "volatility": "unknown",
            "vwap": "unknown",
            "timeOfDay": "unknown",
        },
        "marketRegime": {
            "trend": "unknown",
            "volatility": "unknown",
            "session": "unknown",
            "allowedLong": False,
            "allowedShort": False,
            "thresholdAdjustment": 0.0,
            "positionSizeMultiplier": 0.0,
            "notes": [reason],
        },
        "uncertainty": {
            "modelCount": 0,
            "modelDisagreement": None,
            "maximumModelDisagreement": 0.1,
            "members": [],
        },
        "features": {},
        "topDrivers": [reason, "Chart, quote, regime, session, and event context remain served by the lightweight market-data service."],
        "heuristicEstimate": {
            "status": "not_computed",
            "forecast_applied_to_order": False,
        },
        "sourceAuthority": "lightweight_market_data_service.market_forecast_advisory_fallback",
    }


def _lightweight_forecast_horizon(close: float, horizon_minutes: int, reason: str) -> dict[str, Any]:
    return {
        "status": "INFERENCE_NOT_RUN",
        "horizonMinutes": horizon_minutes,
        "modelApplied": False,
        "probabilityUp": None,
        "probabilityDown": None,
        "probabilityFlatOrNoEdge": None,
        "probabilityBuySuccess": None,
        "probabilitySellSuccess": None,
        "probabilityTimeout": None,
        "predictedDirection": "unavailable",
        "predictedPrice": None,
        "predictedChangeDollars": None,
        "buyExpectedValue": None,
        "sellExpectedValue": None,
        "advice": {
            "longPosition": "NO_ML_ADVICE",
            "shortPosition": "NO_ML_ADVICE",
            "newLongEntry": "WAIT_FOR_VALIDATED_MODEL",
            "newShortEntry": "WAIT_FOR_VALIDATED_MODEL",
            "flatMarket": "WAIT_FOR_VALIDATED_MODEL",
            "reasonCodes": ["market_forecast.lightweight_inference_not_ready"],
        },
        "activationPolicy": "advisory_only_until_live_paper_validation",
        "reason": reason,
        "futurePricePrediction": _lightweight_no_edge_price_prediction(close, horizon_minutes, reason),
    }


def _lightweight_no_edge_price_prediction(close: float, horizon_minutes: int, reason: str) -> dict[str, Any]:
    price = round(max(0.0, close), 4)
    return {
        "horizonMinutes": horizon_minutes,
        "predictedPrice": price,
        "predictedChange": 0.0,
        "predictedChangeDollars": 0.0,
        "expectedPriceDirection": "unavailable",
        "direction": "unavailable",
        "reason": reason,
    }


def _voting_ensemble_strategy_module_payload(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.strategyId,
        "name": entry.strategyName,
        "version": entry.strategyVersion,
        "family": entry.family,
        "role": entry.role,
        "collection": _enum_value(entry.collection).lower(),
        "status": entry.status,
        "enabled": entry.enabled,
        "requiredInputs": list(entry.requiredInputs),
        "evidence": list(entry.evidence),
        "aliases": _voting_ensemble_strategy_alias_metadata(entry.strategyId),
    }


def _voting_ensemble_strategy_alias_metadata(target_id: str) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    for alias, canonical_id in VOTING_ENSEMBLE_ALIAS_MAP.items():
        entry = resolve_voting_ensemble_strategy(canonical_id)
        if entry.strategyId != target_id or alias in {entry.strategyId, entry.strategyName}:
            continue
        aliases.append({"name": alias, "status": "deprecated_alias", "aliasFor": entry.strategyId})
    return aliases


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


async def _store_latest(*, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(store.latest, symbol=symbol, timeframe=timeframe, feed=feed, limit=limit)


async def _store_latest_until(*, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(store.latest_until, symbol=symbol, timeframe=timeframe, feed=feed, limit=limit, end=end)


async def _store_upsert(candles: list[dict[str, Any]]) -> None:
    if candles:
        await asyncio.to_thread(store.upsert_many, candles)


def _dedupe_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, Any]] = {}
    for candle in candles:
        timestamp = str(candle.get("timestamp") or "")
        if not timestamp:
            continue
        by_time[timestamp] = dict(candle)
    return sorted(by_time.values(), key=lambda row: str(row.get("timestamp") or ""))


def _read_response_cache(key: tuple[Any, ...]) -> dict[str, Any] | None:
    cached = _RESPONSE_CACHE.get(key)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at < time.monotonic():
        _RESPONSE_CACHE.pop(key, None)
        return None
    return deepcopy(payload)


def _write_response_cache(key: tuple[Any, ...], payload: dict[str, Any]) -> dict[str, Any]:
    _RESPONSE_CACHE[key] = (time.monotonic() + MARKET_DATA_CACHE_TTL_SECONDS, deepcopy(payload))
    return payload


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
