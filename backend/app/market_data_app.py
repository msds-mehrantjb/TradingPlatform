from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

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

VOTING_ENSEMBLE_ALGORITHM_ID = "voting_ensemble"
VOTING_ENSEMBLE_CONTROL_NAMESPACE = "voting_ensemble.runtime.controls"
VOTING_ENSEMBLE_CONTROL_VERSION = "voting_ensemble_runtime_control_v1"

app = FastAPI(title="Trading Dashboard Market Data API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VotingEnsembleRuntimeControlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestedPaperTradingEnabled: bool
    localEntryBlockActive: bool | None = None
    localEntryBlockReasonCodes: list[str] | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "market-data",
        "port": 8021,
        "generatedAt": _now_iso(),
    }


@app.get("/api/voting-ensemble/runtime/control")
def voting_ensemble_runtime_control() -> dict[str, Any]:
    return _read_voting_ensemble_runtime_control()


@app.put("/api/voting-ensemble/runtime/control")
def update_voting_ensemble_runtime_control(payload: VotingEnsembleRuntimeControlUpdate) -> dict[str, Any]:
    return _write_voting_ensemble_runtime_control(
        requested_paper_trading_enabled=payload.requestedPaperTradingEnabled,
        local_entry_block_active=payload.localEntryBlockActive,
        local_entry_block_reason_codes=payload.localEntryBlockReasonCodes,
    )


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
            if quote is not None and _is_two_sided(quote):
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


@app.get("/api/news-summary")
async def news_summary(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    limit: int = Query(10, ge=3, le=20),
) -> dict[str, Any]:
    normalized_symbol = symbol.upper()
    now = _now_iso()
    news = await _lightweight_news_feed(normalized_symbol, limit)
    context = await market_context(symbol=normalized_symbol, feed="iex", refresh=False)
    forecast = await market_forecast_prediction(symbol=normalized_symbol, feed="iex", timeframe="1Min", limit=60, refresh=False)
    summary = _lightweight_trade_summary(symbol=normalized_symbol, news=news, context=context, forecast=forecast)
    return {
        "source": "Lightweight rule summary",
        "updatedAt": now,
        "symbol": normalized_symbol,
        "summary": summary,
        "snapshot": {
            "symbol": normalized_symbol,
            "news": news,
            "marketContext": context,
            "marketForecast": {
                "status": forecast.get("status"),
                "sourceAuthority": forecast.get("sourceAuthority"),
                "decision": forecast.get("decision"),
                "marketRegime": forecast.get("marketRegime"),
            },
        },
        "warning": "",
        "ollamaHealth": {
            "status": "not_required",
            "baseUrl": "",
            "model": "lightweight_rule_summary",
            "detail": "Summary is generated deterministically by the lightweight market-data service.",
            "action": "",
        },
    }


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


def _is_two_sided(quote: dict[str, Any]) -> bool:
    """Whether a quote has both sides and is not crossed.

    Outside regular hours a venue will return one side of the book -- a bid with no ask, or
    the reverse. Passing that straight through as `ready` hands callers an NBBO whose spread
    is the entire price, which is worse than admitting the feed had nothing: the fallback
    below at least derives a coherent two-sided quote from the last candle.
    """
    try:
        bid = float(quote.get("bid") or 0.0)
        ask = float(quote.get("ask") or 0.0)
    except (TypeError, ValueError):
        return False
    return bid > 0.0 and ask >= bid


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


async def _lightweight_news_feed(symbol: str, limit: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "source": "Lightweight dashboard fallback",
        "updatedAt": _now_iso(),
        "symbol": symbol,
        "items": [
            {
                "id": f"{symbol.lower()}-market-context",
                "headline": f"{symbol} market context and price action are updating from local market data",
                "summary": "The lightweight dashboard service is using chart, quote, regime, session, event, and forecast context.",
                "url": "",
                "source": "Lightweight dashboard fallback",
                "publishedAt": now.isoformat().replace("+00:00", "Z"),
                "symbols": [symbol],
            },
            {
                "id": f"{symbol.lower()}-risk-context",
                "headline": f"{symbol} risk read is driven by market context while live news providers warm up",
                "summary": "Use VWAP, opening range, spread, and local algorithm risk gates before acting.",
                "url": "",
                "source": "Lightweight dashboard fallback",
                "publishedAt": (now - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
                "symbols": [symbol],
            },
        ][:limit],
        "sources": [
            {
                "name": "Lightweight Summary",
                "kind": "local",
                "status": "ready",
                "note": "Deterministic summary is available without Ollama.",
            }
        ],
        "warning": "",
    }


def _lightweight_trade_summary(
    *,
    symbol: str,
    news: dict[str, Any],
    context: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    regime = context.get("regime") if isinstance(context.get("regime"), dict) else {}
    session = context.get("session") if isinstance(context.get("session"), dict) else {}
    event = context.get("event") if isinstance(context.get("event"), dict) else {}
    forecast_decision = forecast.get("decision") if isinstance(forecast.get("decision"), dict) else {}
    forecast_action = str(forecast_decision.get("action") or "no_trade")
    risk_state = _context_risk_state(context)
    regime_label = str(regime.get("label") or "Regime unavailable")
    session_label = str(session.get("label") or "Session unavailable")
    event_label = str(event.get("label") or "Event unavailable")
    headlines = news.get("items") if isinstance(news.get("items"), list) else []
    bias = _summary_bias(regime, forecast_action, risk_state)
    confidence = "Medium" if forecast.get("status") == "ready" and risk_state.lower() == "normal" else "Low"
    return {
        "bias": bias,
        "confidence": confidence,
        "conclusion": (
            f"{symbol} summary is using the lightweight market-data service. "
            f"Regime is {regime_label}; forecast action is {forecast_action.replace('_', ' ')}. "
            "Treat this as advisory context, not an order authorization."
        ),
        "drivers": [
            f"Regime: {regime_label}.",
            f"Session: {session_label}.",
            f"Event window: {event_label}.",
            f"Forecast status: {forecast.get('status', 'unknown')}.",
            f"Local headline/context items available: {len(headlines)}.",
        ],
        "risks": [
            f"Risk state: {risk_state}.",
            "Live LLM/Ollama summary is not required for this deterministic read.",
            "Use price confirmation, spread, and local paper risk gates before entry.",
        ],
        "actionPlan": [
            "Wait for strategy signal and local paper risk approval.",
            "Prefer VWAP/opening-range confirmation when bias and price action agree.",
            "Keep exits risk-reducing even when new entries are blocked.",
        ],
    }


def _summary_bias(regime: dict[str, Any], forecast_action: str, risk_state: str) -> str:
    label = str(regime.get("label") or "").lower()
    direction = str(regime.get("directionBias") or regime.get("direction_bias") or "").lower()
    if risk_state.lower() not in {"normal", "low", ""}:
        return "Cautious"
    if forecast_action == "sell" or direction == "short" or "down" in label:
        return "Bearish"
    if forecast_action == "buy" or direction == "long" or "up" in label:
        return "Bullish"
    return "Neutral"


def _context_label(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    if isinstance(value, str):
        return value or fallback
    return str(value) or fallback


def _context_risk_state(context: dict[str, Any]) -> str:
    for key in ("riskState", "risk_state"):
        value = context.get(key)
        if value:
            return str(value)
    for container_key in ("risk", "riskState", "marketRisk"):
        container = context.get(container_key)
        if isinstance(container, dict):
            value = container.get("label") or container.get("state") or container.get("status")
            if value:
                return str(value)
    return "Normal"


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


def _read_voting_ensemble_runtime_control() -> dict[str, Any]:
    path = _voting_ensemble_control_store_path()
    if not path.exists():
        return _default_voting_ensemble_runtime_control()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    control = payload if isinstance(payload, dict) else {}
    if control.get("algorithmId", control.get("algorithm_id", VOTING_ENSEMBLE_ALGORITHM_ID)) != VOTING_ENSEMBLE_ALGORITHM_ID:
        control = {}
    return _normalize_voting_ensemble_runtime_control(control)


def _write_voting_ensemble_runtime_control(
    *,
    requested_paper_trading_enabled: bool,
    local_entry_block_active: bool | None,
    local_entry_block_reason_codes: list[str] | None,
) -> dict[str, Any]:
    control = _read_voting_ensemble_runtime_control()
    requested = bool(requested_paper_trading_enabled)
    control["requestedPaperTradingEnabled"] = requested
    if not requested:
        control["effectivePaperTradingEnabled"] = False
        control["newEntriesEnabled"] = False
    control["liveTradingEnabled"] = False
    control["updatedAt"] = _now_iso()
    control["updatedBy"] = "market_data_control_api"
    control["reasonCodes"] = [
        "voting_ensemble.control.paper_requested_on" if requested else "voting_ensemble.control.paper_requested_off"
    ]
    if local_entry_block_active is not None:
        control["localEntryBlockActive"] = bool(local_entry_block_active)
        if local_entry_block_active is False:
            control["localEntryBlockReasonCodes"] = []
            control["reasonCodes"].append("voting_ensemble.control.local_entry_block_cleared_by_operator")
    if local_entry_block_reason_codes is not None:
        control["localEntryBlockReasonCodes"] = [str(code) for code in local_entry_block_reason_codes]
    control = _normalize_voting_ensemble_runtime_control(control)
    path = _voting_ensemble_control_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    encoded = json.dumps(control, sort_keys=True, indent=2)
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    except PermissionError:
        path.write_text(encoded, encoding="utf-8")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return control


def _default_voting_ensemble_runtime_control() -> dict[str, Any]:
    return {
        "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
        "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
        "requestedPaperTradingEnabled": False,
        "effectivePaperTradingEnabled": False,
        "liveTradingEnabled": False,
        "newEntriesEnabled": False,
        "killSwitchActive": False,
        "controlVersion": VOTING_ENSEMBLE_CONTROL_VERSION,
        "updatedAt": _now_iso(),
        "updatedBy": "market_data_control_api",
        "reasonCodes": ["voting_ensemble.control.default_paper_off"],
        "localEntryBlockActive": False,
        "localEntryBlockReasonCodes": [],
        "namespace": VOTING_ENSEMBLE_CONTROL_NAMESPACE,
    }


def _normalize_voting_ensemble_runtime_control(payload: dict[str, Any]) -> dict[str, Any]:
    default = _default_voting_ensemble_runtime_control()
    control = {**default, **payload}
    control["algorithmId"] = VOTING_ENSEMBLE_ALGORITHM_ID
    control["algorithm_id"] = VOTING_ENSEMBLE_ALGORITHM_ID
    control["requestedPaperTradingEnabled"] = bool(control.get("requestedPaperTradingEnabled"))
    control["effectivePaperTradingEnabled"] = bool(control.get("effectivePaperTradingEnabled"))
    control["liveTradingEnabled"] = False
    control["newEntriesEnabled"] = bool(control.get("newEntriesEnabled"))
    control["killSwitchActive"] = bool(control.get("killSwitchActive"))
    control["controlVersion"] = str(control.get("controlVersion") or VOTING_ENSEMBLE_CONTROL_VERSION)
    control["updatedAt"] = str(control.get("updatedAt") or _now_iso())
    control["updatedBy"] = str(control.get("updatedBy") or "market_data_control_api")
    control["reasonCodes"] = [str(code) for code in control.get("reasonCodes") or []]
    control["localEntryBlockActive"] = bool(control.get("localEntryBlockActive"))
    control["localEntryBlockReasonCodes"] = [str(code) for code in control.get("localEntryBlockReasonCodes") or []]
    control["namespace"] = VOTING_ENSEMBLE_CONTROL_NAMESPACE
    return control


def _voting_ensemble_control_store_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "control.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
