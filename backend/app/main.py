from __future__ import annotations

import asyncio
import csv
import ctypes
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from math import exp
from pathlib import Path
from typing import Literal
from uuid import uuid4
from xml.etree import ElementTree

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .alpaca import AlpacaClient, demo_bars, local_market_status
from .config import get_settings
from .database import CandleStore
from .market_forecast import (
    FUTURE_MARKET_PREDICTION_LEDGER_NAME,
    FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
    MODEL_VERSION as MARKET_FORECAST_MODEL_VERSION,
    load_microstructure_rows_for_candles,
    market_forecast_prediction,
    market_forecast_artifact_path,
    prediction_log_day,
    read_market_forecast_prediction_log,
    record_market_forecast_prediction,
    resolve_market_forecast_prediction_day,
)
from .market_context import STRATEGY_CLASSIFICATION, compute_market_context


settings = get_settings()
store = CandleStore(settings)
alpaca = AlpacaClient(settings)

app = FastAPI(title="Trading Dashboard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DAILY_BACKTEST_REFRESH_STATUS: dict = {
    "status": "idle",
    "lastRunAt": None,
    "lastTargetDate": None,
    "nextRunAt": None,
    "scheduledTargetDate": None,
    "artifactStatus": "idle",
    "artifactJob": None,
    "dynamicArtifactStatus": "idle",
    "dynamicArtifactJob": None,
    "message": "Daily backtest refresh has not run yet.",
    "result": None,
}

MARKET_FORECAST_LEDGER_SYMBOL = "SPY"
MARKET_FORECAST_LEDGER_FEED: Literal["iex", "sip", "otc"] = "iex"
MARKET_FORECAST_LEDGER_TIMEFRAME: Literal["1Min"] = "1Min"
MARKET_FORECAST_LEDGER_LIMIT = 240
MARKET_FORECAST_LEDGER_POLL_SECONDS = 60
MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS = 5 * 60
MARKET_FORECAST_LEDGER_STATUS: dict = {
    "status": "idle",
    "symbol": MARKET_FORECAST_LEDGER_SYMBOL,
    "feed": MARKET_FORECAST_LEDGER_FEED,
    "timeframe": MARKET_FORECAST_LEDGER_TIMEFRAME,
    "lastRunAt": None,
    "lastPredictionTimestamp": None,
    "lastSaved": None,
    "lastResult": None,
    "nextRunAt": None,
    "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
    "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
    "message": "Future Market Prediction Ledger has not started yet.",
}
MARKET_FORECAST_LEDGER_TICK_LOCK = asyncio.Lock()

DEFAULT_TRADING_SETTINGS: dict = {
    "startingCapital": 25000,
    "orderAllocationPercent": 10,
    "dailyAllocationPercent": 30,
    "riskBudgetPercentOfOrder": 50,
    "maxTradesPerDay": 3,
    "stopLossPercent": 0.35,
    "fixedStopDistanceDollars": 1.0,
    "takeProfitR": 1.5,
    "slippagePerShare": 0.02,
    "positionSizingMode": "allocation",
}


@app.on_event("startup")
async def start_daily_backtest_refresh_scheduler() -> None:
    asyncio.create_task(end_of_day_backtest_refresh_scheduler())
    asyncio.create_task(market_forecast_ledger_scheduler())


DEFAULT_LOOKBACKS = {
    "1Hour": timedelta(days=180),
    "1Day": timedelta(days=900),
}

TRADE_HALTS_RSS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
YAHOO_FINANCE_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
VIX_QUOTE_CSV_URL = "https://stooq.com/q/l/?s=^vix&f=sd2t2ohlcv&h&e=csv"
ES_QUOTE_CSV_URL = "https://stooq.com/q/l/?s=es.f&f=sd2t2ohlcv&h&e=csv"
BACKTEST_EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "backtests"
ARTIFACT_JOB_DIR = BACKTEST_EXPORT_DIR / "_artifact_jobs"
BROWSER_STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "browser_state"
TRADE_HISTORY_ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "trade_history_archives"
DECISION_SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "data" / "decision_snapshots"
ARTIFACT_JOB_EMPTY_LOG_STALE_AFTER = timedelta(minutes=20)
ARTIFACT_JOB_STALE_AFTER = timedelta(hours=3)
SPY_NEWS_FALLBACK = [
    {
        "id": "fallback-spy-flows",
        "headline": "ETF flows remain active as traders watch broad-market momentum",
        "summary": "Fallback headline shown while live news providers are unavailable.",
        "url": "",
        "source": "Dashboard fallback",
        "publishedAt": None,
        "symbols": ["SPY"],
    },
    {
        "id": "fallback-index-futures",
        "headline": "Index futures steady ahead of session catalysts",
        "summary": "Fallback headline shown while live news providers are unavailable.",
        "url": "",
        "source": "Dashboard fallback",
        "publishedAt": None,
        "symbols": ["SPY", "SPX"],
    },
]

EDT = timezone(timedelta(hours=-4), "EDT")
EST = timezone(timedelta(hours=-5), "EST")


def nth_sunday(year: int, month: int, nth: int) -> int:
    first = datetime(year, month, 1)
    first_sunday = 1 + ((6 - first.weekday()) % 7)
    return first_sunday + ((nth - 1) * 7)


def eastern_tz_for_date(year: int, month: int, day: int) -> timezone:
    if month < 3 or month > 11:
        return EST
    if 3 < month < 11:
        return EDT
    if month == 3:
        return EDT if day >= nth_sunday(year, month, 2) else EST
    return EDT if day < nth_sunday(year, month, 1) else EST


def eastern_release_time(year: int, month: int, day: int) -> datetime:
    eastern = eastern_tz_for_date(year, month, day)
    return datetime(year, month, day, 8, 30, tzinfo=eastern)

MACRO_RELEASES_2026 = [
    {
        "id": "empsit-2026-06",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "June 2026",
        "releaseAt": eastern_release_time(2026, 7, 2),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-06",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "June 2026",
        "releaseAt": eastern_release_time(2026, 7, 14),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "empsit-2026-07",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "July 2026",
        "releaseAt": eastern_release_time(2026, 8, 7),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-07",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "July 2026",
        "releaseAt": eastern_release_time(2026, 8, 12),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "empsit-2026-08",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "August 2026",
        "releaseAt": eastern_release_time(2026, 9, 4),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-08",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "August 2026",
        "releaseAt": eastern_release_time(2026, 9, 11),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "empsit-2026-09",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "September 2026",
        "releaseAt": eastern_release_time(2026, 10, 2),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-09",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "September 2026",
        "releaseAt": eastern_release_time(2026, 10, 14),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "empsit-2026-10",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "October 2026",
        "releaseAt": eastern_release_time(2026, 11, 6),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-10",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "October 2026",
        "releaseAt": eastern_release_time(2026, 11, 10),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "empsit-2026-11",
        "category": "jobs",
        "title": "Employment Situation",
        "referenceMonth": "November 2026",
        "releaseAt": eastern_release_time(2026, 12, 4),
        "importance": "high",
        "source": "BLS",
    },
    {
        "id": "cpi-2026-11",
        "category": "cpi",
        "title": "Consumer Price Index",
        "referenceMonth": "November 2026",
        "releaseAt": eastern_release_time(2026, 12, 10),
        "importance": "high",
        "source": "BLS",
    },
]

FED_EVENTS_2026 = [
    {
        "id": "fed-waller-2026-06-22",
        "category": "speech",
        "title": "Speech - Governor Christopher J. Waller",
        "detail": "Welcoming Remarks",
        "releaseAt": eastern_release_time(2026, 6, 22).replace(hour=9, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fed-barr-2026-06-22",
        "category": "speech",
        "title": "Speech - Governor Michael S. Barr",
        "detail": "Supervision and Regulation",
        "releaseAt": eastern_release_time(2026, 6, 22).replace(hour=12, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-minutes-2026-07-08",
        "category": "fomc",
        "title": "FOMC Minutes",
        "detail": "Meeting of June 16-17",
        "releaseAt": eastern_release_time(2026, 7, 8).replace(hour=14, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-press-2026-07-29",
        "category": "fomc",
        "title": "FOMC Press Conference",
        "detail": "July FOMC decision press conference",
        "releaseAt": eastern_release_time(2026, 7, 29).replace(hour=14, minute=30),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-meeting-2026-07-29",
        "category": "fomc",
        "title": "FOMC Meeting",
        "detail": "Two-day meeting, July 28-29",
        "releaseAt": eastern_release_time(2026, 7, 29).replace(hour=14, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-meeting-2026-09-16",
        "category": "fomc",
        "title": "FOMC Meeting",
        "detail": "Two-day meeting, September 15-16; SEP meeting",
        "releaseAt": eastern_release_time(2026, 9, 16).replace(hour=14, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-meeting-2026-10-28",
        "category": "fomc",
        "title": "FOMC Meeting",
        "detail": "Two-day meeting, October 27-28",
        "releaseAt": eastern_release_time(2026, 10, 28).replace(hour=14, minute=0),
        "source": "Federal Reserve",
    },
    {
        "id": "fomc-meeting-2026-12-09",
        "category": "fomc",
        "title": "FOMC Meeting",
        "detail": "Two-day meeting, December 8-9; SEP meeting",
        "releaseAt": eastern_release_time(2026, 12, 9).replace(hour=14, minute=0),
        "source": "Federal Reserve",
    },
]

CIRCUIT_BREAKER_RULES = [
    {
        "level": 1,
        "percent": 7,
        "label": "Level 1",
        "action": "15-minute market-wide halt if triggered before 3:25 p.m. ET",
    },
    {
        "level": 2,
        "percent": 13,
        "label": "Level 2",
        "action": "15-minute market-wide halt if triggered before 3:25 p.m. ET",
    },
    {
        "level": 3,
        "percent": 20,
        "label": "Level 3",
        "action": "Trading halts for the remainder of the day",
    },
]

MOC_IMBALANCE_FIELDS = [
    "symbol",
    "auction",
    "side",
    "imbalanceShares",
    "pairedShares",
    "referencePrice",
    "indicativePrice",
    "publishedAt",
]

VIX_RISK_LEVELS = [
    {
        "label": "Calm",
        "min": 0,
        "max": 15,
        "severity": "low",
        "alert": "Complacent volatility regime",
    },
    {
        "label": "Normal",
        "min": 15,
        "max": 20,
        "severity": "normal",
        "alert": "Routine volatility regime",
    },
    {
        "label": "Elevated",
        "min": 20,
        "max": 30,
        "severity": "elevated",
        "alert": "Risk is elevated; expect wider ranges",
    },
    {
        "label": "Stress",
        "min": 30,
        "max": 40,
        "severity": "high",
        "alert": "Volatility stress; reduce size and widen risk controls",
    },
    {
        "label": "Shock",
        "min": 40,
        "max": None,
        "severity": "extreme",
        "alert": "Volatility shock regime; preserve capital",
    },
]

ES_DIRECTION_LEVELS = [
    {
        "label": "Strong Bullish",
        "minPercent": 0.75,
        "maxPercent": None,
        "severity": "strong_up",
        "alert": "Premarket futures are strongly bid",
    },
    {
        "label": "Bullish",
        "minPercent": 0.25,
        "maxPercent": 0.75,
        "severity": "up",
        "alert": "Premarket futures point higher",
    },
    {
        "label": "Flat",
        "minPercent": -0.25,
        "maxPercent": 0.25,
        "severity": "flat",
        "alert": "Premarket futures are near unchanged",
    },
    {
        "label": "Bearish",
        "minPercent": -0.75,
        "maxPercent": -0.25,
        "severity": "down",
        "alert": "Premarket futures point lower",
    },
    {
        "label": "Strong Bearish",
        "minPercent": None,
        "maxPercent": -0.75,
        "severity": "strong_down",
        "alert": "Premarket futures are under pressure",
    },
]


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "database": "memory" if store.using_memory else str(store.path),
        "alpacaConfigured": settings.has_alpaca_credentials,
    }


@app.post("/api/browser-state/snapshot")
def save_browser_state_snapshot(payload: dict = Body(...)) -> dict:
    items = payload.get("items")
    if not isinstance(items, dict):
        raise HTTPException(status_code=422, detail="items must be an object of browser storage keys and values")

    clean_items: dict[str, str] = {}
    for key, value in items.items():
        if not isinstance(key, str):
            continue
        clean_items[key] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    snapshot = {
        "version": 1,
        "savedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "origin": str(payload.get("origin") or ""),
        "userAgent": str(payload.get("userAgent") or ""),
        "reason": str(payload.get("reason") or "manual"),
        "itemCount": len(clean_items),
        "items": clean_items,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Browser state snapshot is larger than 10 MB")

    BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_path = BROWSER_STATE_DIR / f"browser_state_{run_id}.json"
    latest_path = BROWSER_STATE_DIR / "latest.json"
    write_json(snapshot_path, snapshot)
    write_json(latest_path, snapshot)
    return {
        "ok": True,
        "path": str(snapshot_path),
        "latestPath": str(latest_path),
        "savedAt": snapshot["savedAt"],
        "itemCount": snapshot["itemCount"],
    }


@app.get("/api/browser-state/latest")
def latest_browser_state_snapshot() -> dict:
    latest_path = BROWSER_STATE_DIR / "latest.json"
    if not latest_path.exists():
        raise HTTPException(status_code=404, detail="No browser state snapshot has been saved yet")
    return json.loads(latest_path.read_text(encoding="utf-8"))


@app.post("/api/decision-snapshots")
def save_decision_snapshot(payload: dict = Body(...)) -> dict:
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=422, detail="snapshot must be an object")

    encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
    if len(encoded_snapshot.encode("utf-8")) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Decision snapshot is larger than 15 MB")

    captured_at = str(snapshot.get("capturedAt") or datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    session_date = str(snapshot.get("sessionDate") or captured_at[:10] or "unknown")
    safe_session = re.sub(r"[^0-9A-Za-z_-]+", "-", session_date).strip("-") or "unknown"
    symbol = str(snapshot.get("symbol") or "UNKNOWN").upper()
    safe_symbol = re.sub(r"[^0-9A-Za-z_-]+", "-", symbol).strip("-") or "UNKNOWN"

    record = {
        "version": 1,
        "savedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "snapshot": snapshot,
    }
    DECISION_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    session_dir = DECISION_SNAPSHOT_DIR / safe_session
    session_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = session_dir / f"{safe_symbol}_decision_snapshots.jsonl"
    latest_path = DECISION_SNAPSHOT_DIR / "latest.json"

    with jsonl_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    write_json(latest_path, record)

    return {
        "ok": True,
        "path": str(jsonl_path),
        "latestPath": str(latest_path),
        "sessionDate": safe_session,
        "symbol": safe_symbol,
        "savedAt": record["savedAt"],
    }


@app.post("/api/decision-snapshots/label")
async def label_decision_snapshots_endpoint(payload: dict | None = Body(None)) -> dict:
    request = payload or {}
    symbol = str(request.get("symbol") or "SPY").upper()
    feed = str(request.get("feed") or "iex")
    session_date = str(request.get("sessionDate") or previous_completed_market_session_date())[:10]
    market_status = await alpaca.get_market_status()
    if market_status.get("isOpen") and end_of_day_refresh_target_date(market_status, datetime.now(UTC).astimezone(eastern_tz_for_date(datetime.now(UTC).year, datetime.now(UTC).month, datetime.now(UTC).day))) != session_date:
        raise HTTPException(status_code=409, detail="Decision snapshots are labeled only after the market session is closed")
    return label_decision_snapshots_for_session(symbol=symbol, feed=feed, session_date=session_date)


@app.post("/api/meta-strategy/backfill-snapshots")
async def backfill_meta_strategy_snapshots_endpoint(payload: dict | None = Body(None)) -> dict:
    request = payload or {}
    symbol = str(request.get("symbol") or "SPY").upper()
    feed = str(request.get("feed") or "iex")
    end_date = str(request.get("endDate") or previous_completed_market_session_date())[:10]
    start_date = str(request.get("startDate") or "")[:10] or None
    session_limit = int(request.get("sessionLimit") or 20)
    interval_minutes = int(request.get("intervalMinutes") or 5)
    warmup_minutes = int(request.get("warmupMinutes") or 120)
    max_snapshots = int(request.get("maxSnapshots") or 1200)
    overwrite_existing_dates = bool(request.get("overwriteExistingDates") or False)
    buy_candidate_sampler = bool(request.get("buyCandidateSampler", True))
    buy_candidate_min_score = float(request.get("buyCandidateMinScore") or 0.25)
    buy_candidate_scan_minutes = int(request.get("buyCandidateScanMinutes") or 3)
    buy_candidate_min_gap_minutes = int(request.get("buyCandidateMinGapMinutes") or 3)
    max_buy_candidate_snapshots_per_session = int(request.get("maxBuyCandidateSnapshotsPerSession") or 30)
    result = backfill_meta_strategy_decision_snapshots(
        symbol=symbol,
        feed=feed,
        start_date=start_date,
        end_date=end_date,
        session_limit=session_limit,
        interval_minutes=interval_minutes,
        warmup_minutes=warmup_minutes,
        max_snapshots=max_snapshots,
        overwrite_existing_dates=overwrite_existing_dates,
        buy_candidate_sampler=buy_candidate_sampler,
        buy_candidate_min_score=buy_candidate_min_score,
        buy_candidate_scan_minutes=buy_candidate_scan_minutes,
        buy_candidate_min_gap_minutes=buy_candidate_min_gap_minutes,
        max_buy_candidate_snapshots_per_session=max_buy_candidate_snapshots_per_session,
    )
    write_json(DECISION_SNAPSHOT_DIR / "latest_meta_strategy_backfill.json", result)
    return result


@app.post("/api/meta-strategy/train-baselines")
async def train_meta_strategy_baselines_endpoint(payload: dict | None = Body(None)) -> dict:
    request = payload or {}
    symbol = str(request.get("symbol") or "SPY").upper()
    session_date = str(request.get("sessionDate") or "").strip()[:10] or None
    if session_date:
        market_status = await safe_market_status()
    else:
        market_status = {"isOpen": False}
    if session_date and market_status.get("isOpen"):
        eastern_now = datetime.now(UTC).astimezone(eastern_tz_for_date(datetime.now(UTC).year, datetime.now(UTC).month, datetime.now(UTC).day))
        if end_of_day_refresh_target_date(market_status, eastern_now) != session_date:
            raise HTTPException(status_code=409, detail="Meta-strategy training uses only closed-session labels")
    return run_meta_strategy_training(
        symbol=symbol,
        session_date=session_date,
        min_rows=int(request.get("minRows") or 30),
    )


@app.get("/api/meta-strategy/training-status")
async def meta_strategy_training_status_endpoint() -> dict:
    latest_path = DECISION_SNAPSHOT_DIR / "latest_meta_strategy_training.json"
    if not latest_path.exists():
        return {
            "status": "not_trained",
            "trusted": False,
            "message": "No Meta-Strategy training artifact has been created yet.",
            "latestPath": str(latest_path),
        }
    try:
        result = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Meta-Strategy training artifact is not valid JSON: {exc}") from exc
    return {
        **compact_meta_strategy_training_status(result),
        "trainedAt": result.get("trainedAt"),
        "symbol": result.get("symbol"),
        "sessionDate": result.get("sessionDate"),
        "labelCounts": result.get("labelCounts"),
        "trainingLabelCounts": result.get("trainingLabelCounts"),
        "validationLabelCounts": result.get("validationLabelCounts"),
        "labelPolicy": result.get("labelPolicy"),
        "metrics": result.get("metrics"),
    }


def label_decision_snapshots_for_session(*, symbol: str, feed: str, session_date: str) -> dict:
    normalized_symbol = symbol.upper()
    snapshot_path = decision_snapshot_jsonl_path(symbol=normalized_symbol, session_date=session_date)
    if not snapshot_path.exists():
        return {
            "status": "no_snapshots",
            "symbol": normalized_symbol,
            "sessionDate": session_date,
            "message": "No decision snapshots were recorded for this session.",
            "rows": 0,
        }

    records = read_decision_snapshot_records(snapshot_path)
    candles = decision_label_candles(symbol=normalized_symbol, feed=feed, session_date=session_date)
    labeled_rows = [
        labeled_decision_snapshot(record, candles)
        for record in records
    ]
    labeled_rows = [row for row in labeled_rows if row is not None]
    output_path = decision_label_jsonl_path(symbol=normalized_symbol, session_date=session_date)
    write_jsonl(output_path, labeled_rows)
    latest_path = DECISION_SNAPSHOT_DIR / "latest_labels.json"
    summary = {
        "status": "ready",
        "symbol": normalized_symbol,
        "sessionDate": session_date,
        "rows": len(labeled_rows),
        "labelCounts": {
            "BUY": sum(1 for row in labeled_rows if row["label"] == "BUY"),
            "SELL": sum(1 for row in labeled_rows if row["label"] == "SELL"),
            "HOLD": sum(1 for row in labeled_rows if row["label"] == "HOLD"),
        },
        "trainingLabelCounts": {
            "BUY": sum(1 for row in labeled_rows if row.get("trainingLabel") == "BUY"),
            "SELL": sum(1 for row in labeled_rows if row.get("trainingLabel") == "SELL"),
            "HOLD": sum(1 for row in labeled_rows if row.get("trainingLabel") == "HOLD"),
        },
        "path": str(output_path),
        "latestPath": str(latest_path),
        "labeledAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "policy": "Strict validation label plus ATR-friendly training label; labels are generated only after the session closes.",
    }
    summary["trainingStatus"] = compact_meta_strategy_training_status(
        run_meta_strategy_training(
            symbol=normalized_symbol,
            session_date=None,
            min_rows=30,
        )
    )
    write_json(latest_path, summary)
    return summary


def compact_meta_strategy_training_status(result: dict) -> dict:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    trusted = metrics.get("trusted")
    if trusted is None:
        trusted = result.get("trusted")
    return {
        "status": result.get("status"),
        "rows": result.get("rows"),
        "trainRows": result.get("trainRows"),
        "testRows": result.get("testRows"),
        "featureCount": result.get("featureCount"),
        "artifactPath": result.get("artifactPath"),
        "latestPath": result.get("latestPath"),
        "bestModel": metrics.get("bestModel"),
        "trusted": bool(trusted),
        "bestBaselineMacroF1": metrics.get("bestBaselineMacroF1"),
        "message": result.get("message"),
    }


def run_meta_strategy_training(*, symbol: str, session_date: str | None, min_rows: int) -> dict:
    from . import meta_strategy_training

    reloaded = importlib.reload(meta_strategy_training)
    result = reloaded.train_meta_strategy_baselines(
        decision_snapshot_dir=DECISION_SNAPSHOT_DIR,
        symbol=symbol,
        session_date=session_date,
        min_rows=min_rows,
    )
    return {
        **result,
        "trainerSourcePath": str(Path(reloaded.__file__).resolve()),
        "trainerVersion": "directional_trust_v2",
    }


def backfill_meta_strategy_decision_snapshots(
    *,
    symbol: str,
    feed: str,
    start_date: str | None,
    end_date: str,
    session_limit: int,
    interval_minutes: int,
    warmup_minutes: int,
    max_snapshots: int,
    overwrite_existing_dates: bool,
    buy_candidate_sampler: bool = True,
    buy_candidate_min_score: float = 0.25,
    buy_candidate_scan_minutes: int = 3,
    buy_candidate_min_gap_minutes: int = 3,
    max_buy_candidate_snapshots_per_session: int = 30,
) -> dict:
    normalized_symbol = symbol.upper()
    interval_minutes = max(1, min(interval_minutes, 60))
    warmup_minutes = max(60, min(warmup_minutes, 390))
    max_snapshots = max(1, min(max_snapshots, 10000))
    buy_candidate_min_score = max(0.01, min(float(buy_candidate_min_score), 1.0))
    buy_candidate_scan_minutes = max(1, min(int(buy_candidate_scan_minutes), interval_minutes))
    buy_candidate_min_gap_minutes = max(1, min(int(buy_candidate_min_gap_minutes), 60))
    max_buy_candidate_snapshots_per_session = max(0, min(int(max_buy_candidate_snapshots_per_session), 390))
    historical = historical_meta_strategy_candles(
        symbol=normalized_symbol,
        feed=feed,
        start_date=start_date,
        end_date=end_date,
        session_limit=max(1, min(session_limit, 252)),
    )
    session_summaries = []
    written_sessions = []
    total_written = 0
    for session_date, session_candles in historical["sessions"].items():
        if total_written >= max_snapshots:
            break
        snapshot_path = decision_snapshot_jsonl_path(symbol=normalized_symbol, session_date=session_date)
        if snapshot_path.exists() and not overwrite_existing_dates:
            session_summaries.append({
                "sessionDate": session_date,
                "status": "skipped_existing",
                "path": str(snapshot_path),
                "message": "Existing decision snapshots preserved; set overwriteExistingDates=true to replace this session.",
            })
            continue
        records = historical_meta_strategy_records_for_session(
            symbol=normalized_symbol,
            session_date=session_date,
            candles=session_candles,
            interval_minutes=interval_minutes,
            warmup_minutes=warmup_minutes,
            max_records=max_snapshots - total_written,
            buy_candidate_sampler=buy_candidate_sampler,
            buy_candidate_min_score=buy_candidate_min_score,
            buy_candidate_scan_minutes=buy_candidate_scan_minutes,
            buy_candidate_min_gap_minutes=buy_candidate_min_gap_minutes,
            max_buy_candidate_snapshots=max_buy_candidate_snapshots_per_session,
        )
        if not records:
            session_summaries.append({
                "sessionDate": session_date,
                "status": "no_records",
                "message": "Not enough candles to create historical Meta-Strategy snapshots.",
            })
            continue
        write_jsonl(snapshot_path, records)
        write_json(DECISION_SNAPSHOT_DIR / "latest.json", records[-1])
        total_written += len(records)
        label_result = label_decision_snapshots_for_session(symbol=normalized_symbol, feed=feed, session_date=session_date)
        written_sessions.append(session_date)
        session_summaries.append({
            "sessionDate": session_date,
            "status": "ready",
            "snapshots": len(records),
            "buyCandidateSnapshots": sum(1 for record in records if ((record.get("snapshot") or {}).get("backfillReason") or {}).get("type") == "buy_candidate_sampler"),
            "path": str(snapshot_path),
            "labelCounts": label_result.get("labelCounts"),
            "labelPath": label_result.get("path"),
        })
    training = run_meta_strategy_training(
        symbol=normalized_symbol,
        session_date=None,
        min_rows=30,
    )
    return {
        "status": "ready",
        "symbol": normalized_symbol,
        "feed": feed,
        "source": historical["source"],
        "startDate": historical["startDate"],
        "endDate": historical["endDate"],
        "requestedSessionLimit": session_limit,
        "intervalMinutes": interval_minutes,
        "warmupMinutes": warmup_minutes,
        "buyCandidateSampler": buy_candidate_sampler,
        "buyCandidateMinScore": buy_candidate_min_score,
        "buyCandidateScanMinutes": buy_candidate_scan_minutes,
        "buyCandidateMinGapMinutes": buy_candidate_min_gap_minutes,
        "maxBuyCandidateSnapshotsPerSession": max_buy_candidate_snapshots_per_session,
        "overwriteExistingDates": overwrite_existing_dates,
        "sessionsWritten": len(written_sessions),
        "snapshotsWritten": total_written,
        "sessions": session_summaries,
        "trainingStatus": compact_meta_strategy_training_status(training),
        "trainingPath": training.get("latestPath"),
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def historical_meta_strategy_candles(*, symbol: str, feed: str, start_date: str | None, end_date: str, session_limit: int) -> dict:
    manifest = best_backtest_manifest_or_none(symbol) or latest_backtest_manifest_or_none(symbol)
    candles: list[dict] = []
    source = "store"
    if manifest:
        path = Path(str((manifest.get("files") or {}).get("continuous1mJsonl") or ""))
        if path.exists():
            candles = read_jsonl(path)
            source = str(path)
    if not candles:
        search_start = start_date or (datetime.fromisoformat(end_date).date() - timedelta(days=max(session_limit * 3, 30))).isoformat()
        start, _ = session_date_window_utc(search_start)
        _, end = session_date_window_utc(end_date)
        candles = store.range(symbol=symbol, timeframe="1Min", feed=feed, start=start, end=end)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for candle in candles:
        if str(candle.get("symbol") or symbol).upper() != symbol:
            continue
        session_date = candle_session_date(candle)
        if start_date and session_date < start_date:
            continue
        if session_date > end_date:
            continue
        grouped[session_date].append(candle)
    selected_dates = sorted(grouped)[-session_limit:]
    return {
        "source": source,
        "startDate": selected_dates[0] if selected_dates else start_date,
        "endDate": selected_dates[-1] if selected_dates else end_date,
        "sessions": {
            session_date: sorted(grouped[session_date], key=lambda candle: str(candle.get("timestamp") or ""))
            for session_date in selected_dates
        },
    }


def historical_meta_strategy_records_for_session(
    *,
    symbol: str,
    session_date: str,
    candles: list[dict],
    interval_minutes: int,
    warmup_minutes: int,
    max_records: int,
    buy_candidate_sampler: bool,
    buy_candidate_min_score: float,
    buy_candidate_scan_minutes: int,
    buy_candidate_min_gap_minutes: int,
    max_buy_candidate_snapshots: int,
) -> list[dict]:
    normalized = sorted(candles, key=lambda candle: str(candle.get("timestamp") or ""))
    records = []
    emitted_timestamps: set[str] = set()
    last_regular_emitted: datetime | None = None
    last_buy_candidate_emitted: datetime | None = None
    last_buy_candidate_scan: datetime | None = None
    buy_candidate_count = 0
    max_horizon = max(META_LABEL_FAMILY_HORIZONS.values())
    for index, candle in enumerate(normalized):
        if len(records) >= max_records:
            break
        if index < warmup_minutes:
            continue
        timestamp = parse_market_datetime(str(candle.get("timestamp") or ""))
        if timestamp is None:
            continue
        timestamp_key = str(candle.get("timestamp") or "")
        regular_due = last_regular_emitted is None or timestamp >= last_regular_emitted + timedelta(minutes=interval_minutes)
        sampler_scan_due = (
            buy_candidate_sampler
            and buy_candidate_count < max_buy_candidate_snapshots
            and (last_buy_candidate_scan is None or timestamp >= last_buy_candidate_scan + timedelta(minutes=buy_candidate_scan_minutes))
        )
        if not regular_due and not sampler_scan_due:
            continue
        if not any(
            (future_timestamp := parse_market_datetime(str(future.get("timestamp") or ""))) is not None
            and timestamp < future_timestamp <= timestamp + timedelta(minutes=max_horizon)
            for future in normalized[index + 1:]
        ):
            continue
        if sampler_scan_due:
            last_buy_candidate_scan = timestamp
        forecast = market_forecast_prediction(normalized[: index + 1], microstructure_rows=[])
        if forecast.get("status") == "insufficient_data":
            continue
        buy_candidate = buy_candidate_snapshot_reason(forecast, min_score=buy_candidate_min_score)
        buy_candidate_due = (
            bool(buy_candidate)
            and buy_candidate_count < max_buy_candidate_snapshots
            and (last_buy_candidate_emitted is None or timestamp >= last_buy_candidate_emitted + timedelta(minutes=buy_candidate_min_gap_minutes))
        )
        if not regular_due and not buy_candidate_due:
            continue
        if timestamp_key in emitted_timestamps:
            continue
        backfill_reason = (
            {
                "type": "buy_candidate_sampler",
                **buy_candidate,
                "minScore": buy_candidate_min_score,
            }
            if buy_candidate_due and not regular_due
            else {"type": "regular_interval", "intervalMinutes": interval_minutes}
        )
        snapshot = historical_meta_strategy_snapshot(symbol=symbol, session_date=session_date, candles=normalized[: index + 1], forecast=forecast, backfill_reason=backfill_reason)
        records.append({
            "version": 1,
            "savedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "snapshot": snapshot,
        })
        emitted_timestamps.add(timestamp_key)
        if backfill_reason["type"] == "buy_candidate_sampler":
            buy_candidate_count += 1
            last_buy_candidate_emitted = timestamp
        if regular_due:
            last_regular_emitted = timestamp
    return records


def buy_candidate_snapshot_reason(forecast: dict, *, min_score: float) -> dict | None:
    family_scores = normalized_family_aggregation(((forecast.get("algorithmSignals") or {}).get("familyScores") or {}))
    weighted_scores = ((forecast.get("algorithmSignals") or {}).get("weightedScores") or {})
    buy_scores = {
        "trend_buy_score": float(family_scores.get("trend_buy_score") or 0.0),
        "breakout_buy_score": float(family_scores.get("breakout_buy_score") or 0.0),
        "mean_reversion_buy_score": float(family_scores.get("mean_reversion_buy_score") or 0.0),
        "reversal_buy_score": float(family_scores.get("reversal_buy_score") or 0.0),
        "weighted_buy_score": parse_float_value(weighted_scores.get("buy")) or 0.0,
    }
    sell_pressure = max(
        float(family_scores.get("trend_sell_score") or 0.0),
        float(family_scores.get("breakout_sell_score") or 0.0),
        float(family_scores.get("mean_reversion_sell_score") or 0.0),
        float(family_scores.get("reversal_sell_score") or 0.0),
        parse_float_value(weighted_scores.get("sell")) or 0.0,
    )
    strongest_name, strongest_score = max(buy_scores.items(), key=lambda item: item[1])
    weighted_buy = parse_float_value(weighted_scores.get("buy")) or 0.0
    weighted_sell = parse_float_value(weighted_scores.get("sell")) or 0.0
    family_buy_total = round(
        float(family_scores.get("trend_buy_score") or 0.0)
        + float(family_scores.get("breakout_buy_score") or 0.0)
        + float(family_scores.get("mean_reversion_buy_score") or 0.0)
        + float(family_scores.get("reversal_buy_score") or 0.0),
        4,
    )
    family_sell_total = round(
        float(family_scores.get("trend_sell_score") or 0.0)
        + float(family_scores.get("breakout_sell_score") or 0.0)
        + float(family_scores.get("mean_reversion_sell_score") or 0.0)
        + float(family_scores.get("reversal_sell_score") or 0.0),
        4,
    )
    if strongest_score < min_score and not (weighted_buy > weighted_sell and weighted_buy >= min_score * 0.8):
        return None
    if family_buy_total <= family_sell_total:
        return None
    if sell_pressure > strongest_score and weighted_sell >= weighted_buy:
        return None
    return {
        "strongestBuyFeature": strongest_name,
        "strongestBuyScore": round(strongest_score, 4),
        "familyBuyTotal": family_buy_total,
        "familySellTotal": family_sell_total,
        "weightedBuyScore": round(weighted_buy, 4),
        "weightedSellScore": round(weighted_sell, 4),
        "sellPressure": round(sell_pressure, 4),
    }


def historical_meta_strategy_snapshot(*, symbol: str, session_date: str, candles: list[dict], forecast: dict, backfill_reason: dict | None = None) -> dict:
    latest = candles[-1]
    latest_close = float(latest["close"])
    barriers = forecast.get("barriers") or {}
    target_distance = parse_float_value(barriers.get("targetDistance")) or max(latest_close * 0.003, 0.25)
    stop_distance = parse_float_value(barriers.get("stopDistance")) or max(latest_close * 0.002, 0.25)
    decision = forecast.get("decision") or {}
    candidate = str(decision.get("action") or decision.get("candidateAction") or "no_trade").lower()
    meta_signal = "Buy" if candidate == "buy" else "Sell" if candidate == "sell" else "Hold"
    family_aggregation = normalized_family_aggregation(((forecast.get("algorithmSignals") or {}).get("familyScores") or {}))
    meta_family_scores = meta_family_scores_from_aggregation(family_aggregation)
    weighted_scores = ((forecast.get("algorithmSignals") or {}).get("weightedScores") or {})
    weighted_signal = weighted_signal_from_scores(weighted_scores)
    net_score = round(
        float(family_aggregation.get("trend_buy_score", 0))
        + float(family_aggregation.get("breakout_buy_score", 0))
        + float(family_aggregation.get("mean_reversion_buy_score", 0))
        + float(family_aggregation.get("reversal_buy_score", 0))
        - float(family_aggregation.get("trend_sell_score", 0))
        - float(family_aggregation.get("breakout_sell_score", 0))
        - float(family_aggregation.get("mean_reversion_sell_score", 0))
        - float(family_aggregation.get("reversal_sell_score", 0)),
        4,
    )
    return {
        "capturedAt": str(latest.get("timestamp")),
        "sessionDate": session_date,
        "symbol": symbol,
        "timeframe": "1Min",
        "source": "historical_meta_strategy_backfill",
        "backfillReason": backfill_reason or {"type": "regular_interval"},
        "candles": {"session": candles[-120:]},
        "indicators": {
            "latest": latest,
            "atr": {"stopDistance": round(stop_distance, 4), "targetDistance": round(target_distance, 4)},
        },
        "finalDecision": {
            "activeMode": "meta",
            "activeAlgorithmLabel": "Meta-Strategy",
            "activeTargetOrder": historical_meta_target_order(symbol=symbol, latest_close=latest_close, side=meta_signal, target_distance=target_distance, stop_distance=stop_distance, eligible=meta_signal != "Hold"),
            "voting": {"signal": weighted_signal, "scores": {"Buy": weighted_scores.get("buy"), "Sell": weighted_scores.get("sell"), "Hold": weighted_scores.get("hold")}},
            "weighted": {
                "signal": weighted_signal,
                "buyScore": weighted_scores.get("buy"),
                "sellScore": weighted_scores.get("sell"),
                "holdScore": weighted_scores.get("hold"),
                "margin": weighted_scores.get("winnerMargin"),
            },
            "confidence": {"signal": meta_signal, "decisionLabel": meta_signal, "normalizedNetScore": net_score},
            "regime": {
                "signal": meta_signal,
                "aggregateSignal": meta_signal.lower(),
                "confidence": decision.get("confidence"),
                "scoreEdge": decision.get("edgeGap"),
            },
            "meta": {"signal": meta_signal, "decisionLabel": meta_signal, "netScore": net_score, "edge": abs(net_score)},
        },
        "familyScores": {"meta": family_aggregation, "forecast": family_aggregation},
        "metaModelFeatures": {
            "familyAggregation": family_aggregation,
            "familyScores": meta_family_scores,
            "contextMultiplier": 1,
            "activeDirectionalCount": active_meta_family_count(meta_family_scores),
            "safetyGates": [],
            "forecastFeatures": forecast.get("features") or {},
        },
        "paperTradeResult": {"historicalBackfill": {"status": "synthetic_snapshot", "forecastStatus": forecast.get("status")}},
    }


def historical_meta_target_order(*, symbol: str, latest_close: float, side: str, target_distance: float, stop_distance: float, eligible: bool) -> dict:
    if side == "Sell":
        target_price = latest_close - target_distance
        stop_price = latest_close + stop_distance
    else:
        target_price = latest_close + target_distance
        stop_price = latest_close - stop_distance
    return {
        "eligible": eligible,
        "side": side,
        "orderType": f"{side} historical setup" if eligible else "No order",
        "symbol": symbol,
        "quantity": 0,
        "triggerPrice": round(latest_close, 4),
        "limitPrice": round(latest_close, 4),
        "stopPrice": round(stop_price, 4),
        "targetPrice": round(target_price, 4),
        "orderLimitDollars": 0,
        "dailyLimitDollars": 0,
        "riskDollars": 0,
        "orderNotional": 0,
        "plannedStopRiskDollars": 0,
        "estimatedSlippage": 0,
        "submitMode": "Historical backfill",
        "failedGates": [] if eligible else ["Historical Meta-Strategy signal is Hold"],
        "summary": "Historical Meta-Strategy backfill target order.",
    }


def normalized_family_aggregation(source: dict) -> dict:
    keys = [
        "trend_buy_score",
        "trend_sell_score",
        "breakout_buy_score",
        "breakout_sell_score",
        "mean_reversion_buy_score",
        "mean_reversion_sell_score",
        "reversal_buy_score",
        "reversal_sell_score",
        "confirmation_score",
        "regime_score",
    ]
    return {key: round(parse_float_value(source.get(key)) or 0.0, 4) for key in keys}


def meta_family_scores_from_aggregation(family_aggregation: dict) -> dict:
    return {
        "trend": {"buy": family_aggregation["trend_buy_score"], "sell": family_aggregation["trend_sell_score"], "hold": 0, "capped": False},
        "breakout": {"buy": family_aggregation["breakout_buy_score"], "sell": family_aggregation["breakout_sell_score"], "hold": 0, "capped": False},
        "mean_reversion": {"buy": family_aggregation["mean_reversion_buy_score"], "sell": family_aggregation["mean_reversion_sell_score"], "hold": 0, "capped": False},
        "reversal": {"buy": family_aggregation["reversal_buy_score"], "sell": family_aggregation["reversal_sell_score"], "hold": 0, "capped": False},
        "volume_confirmation": {"buy": 0, "sell": 0, "hold": 0, "capped": False},
        "market_regime": {"buy": 0, "sell": 0, "hold": 0, "capped": False},
        "vwap": {"buy": 0, "sell": 0, "hold": 0, "capped": False},
        "event": {"buy": 0, "sell": 0, "hold": 0, "capped": False},
        "safety": {"buy": 0, "sell": 0, "hold": 0, "capped": False},
    }


def active_meta_family_count(family_scores: dict) -> int:
    return sum(1 for score in family_scores.values() if isinstance(score, dict) and (float(score.get("buy") or 0) > 0.01 or float(score.get("sell") or 0) > 0.01))


def weighted_signal_from_scores(scores: dict) -> str:
    buy = parse_float_value(scores.get("buy")) or 0.0
    sell = parse_float_value(scores.get("sell")) or 0.0
    hold = parse_float_value(scores.get("hold")) or 0.0
    if buy >= sell and buy >= hold and buy > 0:
        return "Buy"
    if sell >= buy and sell >= hold and sell > 0:
        return "Sell"
    return "Hold"


def decision_snapshot_jsonl_path(*, symbol: str, session_date: str) -> Path:
    safe_session = re.sub(r"[^0-9A-Za-z_-]+", "-", session_date).strip("-") or "unknown"
    safe_symbol = re.sub(r"[^0-9A-Za-z_-]+", "-", symbol.upper()).strip("-") or "UNKNOWN"
    return DECISION_SNAPSHOT_DIR / safe_session / f"{safe_symbol}_decision_snapshots.jsonl"


def decision_label_jsonl_path(*, symbol: str, session_date: str) -> Path:
    safe_session = re.sub(r"[^0-9A-Za-z_-]+", "-", session_date).strip("-") or "unknown"
    safe_symbol = re.sub(r"[^0-9A-Za-z_-]+", "-", symbol.upper()).strip("-") or "UNKNOWN"
    return DECISION_SNAPSHOT_DIR / safe_session / f"{safe_symbol}_decision_labels.jsonl"


def read_decision_snapshot_records(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot = record.get("snapshot") if isinstance(record, dict) else None
            if isinstance(snapshot, dict):
                rows.append(record)
    return rows


def decision_label_candles(*, symbol: str, feed: str, session_date: str) -> list[dict]:
    start, end = session_date_window_utc(session_date)
    candles = store.range(symbol=symbol, timeframe="1Min", feed=feed, start=start, end=end)
    if candles:
        return sorted(candles, key=lambda candle: candle["timestamp"])
    manifest = latest_backtest_manifest_or_none(symbol)
    if not manifest:
        return []
    path = Path(str((manifest.get("files") or {}).get("continuous1mJsonl") or ""))
    if not path.exists():
        return []
    return [
        candle
        for candle in read_jsonl(path)
        if candle_session_date(candle) == session_date
    ]


META_LABEL_FAMILY_HORIZONS = {
    "breakout": 5,
    "vwap": 10,
    "reversal": 10,
    "mean_reversion": 15,
    "trend": 20,
    "event": 20,
}
DEFAULT_META_LABEL_HORIZON_MINUTES = 5


def labeled_decision_snapshot(record: dict, candles: list[dict]) -> dict | None:
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    entry = snapshot_entry(snapshot)
    if not entry:
        return None
    entry_time = parse_market_datetime(str(entry.get("timestamp") or ""))
    if entry_time is None:
        return None
    horizon = snapshot_label_horizon(snapshot)
    future = [
        candle for candle in candles
        if (parsed := parse_market_datetime(str(candle.get("timestamp") or ""))) is not None
        and entry_time < parsed <= entry_time + timedelta(minutes=int(horizon["minutes"]))
    ]
    stop_distance, target_distance = snapshot_barrier_distances(snapshot, float(entry["close"]))
    outcome = horizon_barrier_label(
        entry_price=float(entry["close"]),
        future_candles=future,
        stop_distance=stop_distance,
        target_distance=target_distance,
        horizon_minutes=int(horizon["minutes"]),
    )
    training_atr = snapshot_training_atr(snapshot, float(entry["close"]), stop_distance)
    training_outcome = atr_friendly_training_label(
        entry_price=float(entry["close"]),
        future_candles=future,
        atr_value=training_atr,
        horizon_minutes=int(horizon["minutes"]),
    )
    return {
        "version": 1,
        "snapshotId": snapshot_identifier(snapshot),
        "capturedAt": snapshot.get("capturedAt"),
        "sessionDate": snapshot.get("sessionDate"),
        "symbol": snapshot.get("symbol"),
        "entry": entry,
        "horizonMinutes": int(horizon["minutes"]),
        "horizonMode": "family_aware",
        "horizonFamily": horizon["primaryFamily"],
        "horizonFamilies": horizon["families"],
        "horizonReason": horizon["reason"],
        "barriers": {
            "stopDistance": round(stop_distance, 4),
            "targetDistance": round(target_distance, 4),
        },
        "label": outcome["label"],
        "validationLabel": outcome["label"],
        "labelReason": outcome["reason"],
        "longOutcome": outcome["longOutcome"],
        "shortOutcome": outcome["shortOutcome"],
        "trainingLabel": training_outcome["label"],
        "trainingLabelMode": "atr_friendly",
        "trainingLabelReason": training_outcome["reason"],
        "trainingAtr": round(training_atr, 4),
        "trainingBarriers": training_outcome["barriers"],
        "trainingLongOutcome": training_outcome["longOutcome"],
        "trainingShortOutcome": training_outcome["shortOutcome"],
        "futureCandleCount": len(future),
        "futureEndAt": future[-1]["timestamp"] if future else None,
        "finalDecision": snapshot.get("finalDecision"),
        "strategyOutputs": snapshot.get("strategyOutputs"),
        "familyScores": snapshot.get("familyScores"),
        "metaModelFeatures": snapshot.get("metaModelFeatures"),
        "paperTradeResult": snapshot.get("paperTradeResult"),
        "labeledAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def snapshot_label_horizon(snapshot: dict) -> dict:
    side = snapshot_label_side(snapshot)
    family_scores = (((snapshot.get("metaModelFeatures") or {}).get("familyScores") or {}))
    candidates = []
    if isinstance(family_scores, dict):
        for family, minutes in META_LABEL_FAMILY_HORIZONS.items():
            score = family_scores.get(family)
            if not isinstance(score, dict):
                continue
            buy_score = parse_float_value(score.get("buy")) or 0.0
            sell_score = parse_float_value(score.get("sell")) or 0.0
            directional_score = buy_score if side == "buy" else sell_score if side == "sell" else max(buy_score, sell_score)
            if directional_score > 0.01:
                candidates.append({
                    "family": family,
                    "score": round(directional_score, 4),
                    "horizonMinutes": minutes,
                })
    if not candidates:
        return {
            "minutes": DEFAULT_META_LABEL_HORIZON_MINUTES,
            "primaryFamily": "default",
            "families": [],
            "reason": f"No active directional family found; using {DEFAULT_META_LABEL_HORIZON_MINUTES} minute default horizon.",
        }
    max_minutes = max(int(candidate["horizonMinutes"]) for candidate in candidates)
    primary = max(candidates, key=lambda candidate: (float(candidate["score"]), int(candidate["horizonMinutes"])))
    active_names = ", ".join(candidate["family"] for candidate in candidates if int(candidate["horizonMinutes"]) == max_minutes)
    return {
        "minutes": max_minutes,
        "primaryFamily": primary["family"],
        "families": candidates,
        "reason": f"Family-aware horizon uses {max_minutes} minutes for active {active_names} directional context.",
    }


def snapshot_label_side(snapshot: dict) -> str | None:
    meta_signal = str((((snapshot.get("finalDecision") or {}).get("meta") or {}).get("signal") or "")).lower()
    if meta_signal == "buy":
        return "buy"
    if meta_signal == "sell":
        return "sell"
    active_side = str(((snapshot.get("finalDecision") or {}).get("activeTargetOrder") or {}).get("side") or "").lower()
    if active_side.startswith("buy"):
        return "buy"
    if active_side.startswith("sell"):
        return "sell"
    return None


def snapshot_identifier(snapshot: dict) -> str:
    parts = [
        str(snapshot.get("symbol") or ""),
        str(snapshot.get("timeframe") or ""),
        str(snapshot.get("capturedAt") or ""),
        str(((snapshot.get("indicators") or {}).get("latest") or {}).get("timestamp") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def snapshot_entry(snapshot: dict) -> dict | None:
    latest = (snapshot.get("indicators") or {}).get("latest")
    if isinstance(latest, dict) and latest.get("timestamp") and latest.get("close") is not None:
        return {
            "timestamp": latest["timestamp"],
            "open": latest.get("open"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "close": float(latest["close"]),
        }
    for group in ["weightedOneMinute", "session", "chart"]:
        candles = (snapshot.get("candles") or {}).get(group)
        if isinstance(candles, list) and candles:
            candle = candles[-1]
            if isinstance(candle, dict) and candle.get("timestamp") and candle.get("close") is not None:
                return {
                    "timestamp": candle["timestamp"],
                    "open": candle.get("open"),
                    "high": candle.get("high"),
                    "low": candle.get("low"),
                    "close": float(candle["close"]),
                }
    return None


def snapshot_barrier_distances(snapshot: dict, entry_price: float) -> tuple[float, float]:
    active_order = (snapshot.get("finalDecision") or {}).get("activeTargetOrder") or {}
    stop_price = parse_float_value(active_order.get("stopPrice"))
    target_price = parse_float_value(active_order.get("targetPrice"))
    stop_distance = abs(entry_price - stop_price) if stop_price is not None else None
    target_distance = abs(target_price - entry_price) if target_price is not None else None

    atr = ((snapshot.get("indicators") or {}).get("atr") or {})
    atr_stop = parse_float_value(atr.get("stopDistance")) if isinstance(atr, dict) else None
    if stop_distance is None or stop_distance <= 0:
        stop_distance = atr_stop if atr_stop and atr_stop > 0 else max(entry_price * 0.0035, 0.25)
    if target_distance is None or target_distance <= 0:
        target_distance = stop_distance * 1.5
    return max(stop_distance, 0.01), max(target_distance, 0.01)


def snapshot_training_atr(snapshot: dict, entry_price: float, fallback_distance: float) -> float:
    atr = ((snapshot.get("indicators") or {}).get("atr") or {})
    if isinstance(atr, dict):
        for key in ["atr", "atrValue", "atr_1m", "averageTrueRange"]:
            value = parse_float_value(atr.get(key))
            if value and value > 0:
                return value
    volatility = (((snapshot.get("metaModelFeatures") or {}).get("forecastFeatures") or {}).get("volatility") or {})
    if isinstance(volatility, dict):
        value = parse_float_value(volatility.get("atr_1m"))
        if value and value > 0:
            return value
    return max(float(fallback_distance or 0), entry_price * 0.0015, 0.05)


def parse_float_value(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def horizon_barrier_label(*, entry_price: float, future_candles: list[dict], stop_distance: float, target_distance: float, horizon_minutes: int) -> dict:
    if not future_candles:
        return {
            "label": "HOLD",
            "reason": "No future candles are available after this snapshot",
            "longOutcome": "unknown",
            "shortOutcome": "unknown",
        }
    long_target = entry_price + target_distance
    long_stop = entry_price - stop_distance
    short_target = entry_price - target_distance
    short_stop = entry_price + stop_distance
    long_outcome = barrier_outcome(future_candles, target=lambda candle: float(candle["high"]) >= long_target, stop=lambda candle: float(candle["low"]) <= long_stop)
    short_outcome = barrier_outcome(future_candles, target=lambda candle: float(candle["low"]) <= short_target, stop=lambda candle: float(candle["high"]) >= short_stop)
    if long_outcome["result"] == "target" and short_outcome["result"] != "target":
        return {"label": "BUY", "reason": f"Long profit target hit first within {horizon_minutes} minutes", "longOutcome": long_outcome, "shortOutcome": short_outcome}
    if short_outcome["result"] == "target" and long_outcome["result"] != "target":
        return {"label": "SELL", "reason": f"Short profit target hit first within {horizon_minutes} minutes", "longOutcome": long_outcome, "shortOutcome": short_outcome}
    return {"label": "HOLD", "reason": f"No clean {horizon_minutes}-minute target-before-stop edge", "longOutcome": long_outcome, "shortOutcome": short_outcome}


def atr_friendly_training_label(*, entry_price: float, future_candles: list[dict], atr_value: float, horizon_minutes: int) -> dict:
    atr_value = max(float(atr_value or 0), 0.01)
    target_distance = atr_value * 0.25
    stop_distance = atr_value * 0.15
    if not future_candles:
        return {
            "label": "HOLD",
            "reason": "No future candles are available after this snapshot",
            "barriers": {
                "atr": round(atr_value, 4),
                "targetDistance": round(target_distance, 4),
                "stopDistance": round(stop_distance, 4),
            },
            "longOutcome": "unknown",
            "shortOutcome": "unknown",
        }
    long_target = entry_price + target_distance
    long_stop = entry_price - stop_distance
    short_target = entry_price - target_distance
    short_stop = entry_price + stop_distance
    long_outcome = barrier_outcome(future_candles, target=lambda candle: float(candle["high"]) >= long_target, stop=lambda candle: float(candle["low"]) <= long_stop)
    short_outcome = barrier_outcome(future_candles, target=lambda candle: float(candle["low"]) <= short_target, stop=lambda candle: float(candle["high"]) >= short_stop)
    barriers = {
        "atr": round(atr_value, 4),
        "targetDistance": round(target_distance, 4),
        "stopDistance": round(stop_distance, 4),
        "buyTrigger": round(long_target, 4),
        "buyAdverse": round(long_stop, 4),
        "sellTrigger": round(short_target, 4),
        "sellAdverse": round(short_stop, 4),
    }
    long_target_first = long_outcome["result"] == "target"
    short_target_first = short_outcome["result"] == "target"
    if long_target_first and not short_target_first:
        return {"label": "BUY", "reason": f"Future high moved +0.25 ATR before a -0.15 ATR adverse move within {horizon_minutes} minutes", "barriers": barriers, "longOutcome": long_outcome, "shortOutcome": short_outcome}
    if short_target_first and not long_target_first:
        return {"label": "SELL", "reason": f"Future low moved -0.25 ATR before a +0.15 ATR adverse move within {horizon_minutes} minutes", "barriers": barriers, "longOutcome": long_outcome, "shortOutcome": short_outcome}
    if long_target_first and short_target_first:
        long_bars = int(long_outcome.get("bars") or 0)
        short_bars = int(short_outcome.get("bars") or 0)
        if long_bars and short_bars and long_bars < short_bars:
            return {"label": "BUY", "reason": f"BUY +0.25 ATR move resolved before SELL move within {horizon_minutes} minutes", "barriers": barriers, "longOutcome": long_outcome, "shortOutcome": short_outcome}
        if long_bars and short_bars and short_bars < long_bars:
            return {"label": "SELL", "reason": f"SELL -0.25 ATR move resolved before BUY move within {horizon_minutes} minutes", "barriers": barriers, "longOutcome": long_outcome, "shortOutcome": short_outcome}
    return {"label": "HOLD", "reason": f"No clean ATR-friendly directional move within {horizon_minutes} minutes", "barriers": barriers, "longOutcome": long_outcome, "shortOutcome": short_outcome}


def barrier_outcome(candles: list[dict], *, target, stop) -> dict:
    for index, candle in enumerate(candles, start=1):
        target_hit = target(candle)
        stop_hit = stop(candle)
        if target_hit and stop_hit:
            return {"result": "ambiguous", "bars": index, "timestamp": candle.get("timestamp")}
        if target_hit:
            return {"result": "target", "bars": index, "timestamp": candle.get("timestamp")}
        if stop_hit:
            return {"result": "stop", "bars": index, "timestamp": candle.get("timestamp")}
    return {"result": "timeout", "bars": len(candles), "timestamp": candles[-1].get("timestamp") if candles else None}


@app.post("/api/trade-history/archive")
def save_trade_history_archive(payload: dict = Body(...)) -> dict:
    algorithms = payload.get("algorithms")
    if not isinstance(algorithms, dict):
        raise HTTPException(status_code=422, detail="algorithms must include trade history grouped by algorithm")

    archive = {
        "version": 1,
        "savedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sessionDate": str(payload.get("sessionDate") or ""),
        "reason": str(payload.get("reason") or "market-close"),
        "symbol": str(payload.get("symbol") or ""),
        "marketStatus": str(payload.get("marketStatus") or ""),
        "appContext": payload.get("appContext") if isinstance(payload.get("appContext"), dict) else {},
        "algorithms": algorithms,
    }
    encoded = json.dumps(archive, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Trade history archive is larger than 10 MB")

    TRADE_HISTORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    safe_session = re.sub(r"[^0-9A-Za-z_-]+", "-", archive["sessionDate"] or "unknown").strip("-") or "unknown"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = TRADE_HISTORY_ARCHIVE_DIR / f"trade_history_{safe_session}_{run_id}.json"
    latest_path = TRADE_HISTORY_ARCHIVE_DIR / "latest.json"
    write_json(archive_path, archive)
    write_json(latest_path, archive)
    return {
        "ok": True,
        "path": str(archive_path),
        "latestPath": str(latest_path),
        "savedAt": archive["savedAt"],
        "sessionDate": archive["sessionDate"],
    }


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
) -> dict:
    normalized_symbol = symbol.upper()
    cached = store.latest(
        symbol=normalized_symbol,
        timeframe=timeframe,
        feed=feed,
        limit=limit,
    )

    if cached and not refresh:
        return {"source": "cache", "candles": cached}

    request_start = start
    request_end = end
    request_sort = sort
    if not request_start and not request_end and timeframe in DEFAULT_LOOKBACKS:
        now = datetime.now(UTC)
        request_start = (now - DEFAULT_LOOKBACKS[timeframe]).isoformat().replace("+00:00", "Z")
        request_end = now.isoformat().replace("+00:00", "Z")
        request_sort = "desc"

    try:
        fresh = await alpaca.get_bars(
            symbol=normalized_symbol,
            timeframe=timeframe,
            feed=feed,
            limit=limit,
            start=request_start,
            end=request_end,
            sort=request_sort,
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        if cached:
            return {"source": "cache", "warning": detail, "candles": cached}
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        if cached:
            return {"source": "cache", "warning": str(exc), "candles": cached}
        fallback = demo_bars(
            symbol=normalized_symbol,
            timeframe=timeframe,
            feed=feed,
            limit=limit,
        )
        store.upsert_many(fallback)
        return {"source": "demo", "warning": str(exc), "candles": fallback}

    store.upsert_many(fresh)
    return {
        "source": fresh[0]["provider"] if fresh else "alpaca",
        "candles": fresh or cached,
    }


@app.get("/api/market-forecast/prediction")
async def market_forecast_prediction_endpoint(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    timeframe: Literal["1Min"] = "1Min",
    limit: int = Query(240, ge=60, le=1000),
    refresh: bool = False,
) -> dict:
    normalized_symbol = symbol.upper()
    cached = store.latest(symbol=normalized_symbol, timeframe=timeframe, feed=feed, limit=limit)
    candles_for_prediction = cached

    if refresh or not candles_for_prediction:
        try:
            fresh = await alpaca.get_bars(
                symbol=normalized_symbol,
                timeframe=timeframe,
                feed=feed,
                limit=limit,
                start=None,
                end=None,
                sort="asc",
            )
            store.upsert_many(fresh)
            candles_for_prediction = fresh
        except httpx.HTTPError:
            candles_for_prediction = cached

    microstructure_rows = load_microstructure_rows_for_candles(normalized_symbol, feed, candles_for_prediction)
    forecast = market_forecast_prediction(candles_for_prediction, microstructure_rows=microstructure_rows)
    forecast["performanceLog"] = record_market_forecast_prediction(
        normalized_symbol,
        feed,
        timeframe,
        candles_for_prediction,
        forecast,
    )
    return forecast


@app.get("/api/market-forecast/history")
async def market_forecast_history_endpoint(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    feed: Literal["iex", "sip", "otc"] | None = None,
    timeframe: Literal["1Min"] | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> dict:
    return read_market_forecast_prediction_log(
        symbol.upper(),
        date=date,
        feed=feed,
        timeframe=timeframe,
        limit=limit,
    )


@app.get("/api/market-forecast/ledger/status")
async def market_forecast_ledger_status_endpoint() -> dict:
    return dict(MARKET_FORECAST_LEDGER_STATUS)


@app.post("/api/market-forecast/ledger/start")
async def market_forecast_ledger_start_endpoint() -> dict:
    market_status = await safe_market_status()
    async with MARKET_FORECAST_LEDGER_TICK_LOCK:
        wait_seconds = await run_market_forecast_ledger_tick(market_status)
    return {
        **dict(MARKET_FORECAST_LEDGER_STATUS),
        "triggered": True,
        "reason": "manual_or_wake_start",
        "pollSeconds": wait_seconds,
    }


@app.get("/api/market-status")
async def market_status() -> dict:
    try:
        return await alpaca.get_market_status()
    except httpx.HTTPStatusError as exc:
        return local_market_status(warning=exc.response.text)
    except httpx.HTTPError as exc:
        return local_market_status(warning=str(exc))


@app.post("/api/system/sleep-if-market-closed")
async def sleep_if_market_closed(reason: str = Body("wake_market_closed", embed=True)) -> dict:
    market = await safe_market_status()
    if market.get("isOpen") or market.get("status") == "open":
        return {
            "sleepRequested": False,
            "reason": "market_open",
            "marketStatus": market,
        }
    if os.name != "nt":
        return {
            "sleepRequested": False,
            "reason": "unsupported_os",
            "marketStatus": market,
        }
    try:
        subprocess.Popen(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return {
            "sleepRequested": False,
            "reason": f"sleep_failed: {exc}",
            "marketStatus": market,
        }
    return {
        "sleepRequested": True,
        "reason": reason,
        "marketStatus": market,
    }


@app.post("/api/backtest-data/prepare")
async def prepare_backtest_data(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    lookback_days: int = Query(30, ge=1, le=2500),
    daily_lookback_days: int = Query(900, ge=30, le=2500),
    max_pages: int = Query(25, ge=1, le=300),
    start_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    normalized_symbol = symbol.upper()
    now = datetime.now(UTC)
    end_datetime = parse_backtest_end_datetime(end_date) if end_date else now
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    output_dir = BACKTEST_EXPORT_DIR / normalized_symbol / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    start_datetime = parse_backtest_start_datetime(start_date) if start_date else end_datetime - timedelta(days=lookback_days)
    intraday_start = start_datetime.isoformat().replace("+00:00", "Z")
    daily_start_datetime = min(start_datetime, end_datetime - timedelta(days=daily_lookback_days))
    daily_start = daily_start_datetime.isoformat().replace("+00:00", "Z")
    end = end_datetime.isoformat().replace("+00:00", "Z")

    for timeframe, start in [("1Min", intraday_start), ("5Min", intraday_start), ("1Day", daily_start)]:
        try:
            fresh = await alpaca.get_bars_window(
                symbol=normalized_symbol,
                timeframe=timeframe,
                feed=feed,
                start=start,
                end=end,
                max_pages=max_pages,
            )
            store.upsert_many(fresh)
            if not fresh:
                warnings.append(f"No fresh Alpaca bars returned for {timeframe}. Cached data will be used if available.")
        except httpx.HTTPError as exc:
            warnings.append(f"{timeframe} Alpaca fetch failed: {exc}")

    continuous_1m = store.range(symbol=normalized_symbol, timeframe="1Min", feed=feed, start=intraday_start, end=end)
    continuous_5m = store.range(symbol=normalized_symbol, timeframe="5Min", feed=feed, start=intraday_start, end=end)
    daily = store.range(symbol=normalized_symbol, timeframe="1Day", feed=feed, start=daily_start, end=end)

    if not continuous_5m and continuous_1m:
        continuous_5m = aggregate_candles(continuous_1m, timeframe="5Min", minutes=5)
        warnings.append("5Min bars were aggregated from 1Min bars because native 5Min data was unavailable.")

    latest_day = latest_session_date(continuous_1m) or latest_session_date(continuous_5m)
    latest_1m = filter_session_date(continuous_1m, latest_day)
    latest_5m = filter_session_date(continuous_5m, latest_day)
    previous_close = previous_daily_close(daily, latest_day)

    enriched_latest_1m = enrich_backtest_candles(latest_1m, previous_close=previous_close)
    enriched_latest_5m = enrich_backtest_candles(latest_5m, previous_close=previous_close)

    files = {
        "continuous1mJsonl": write_jsonl(output_dir / "continuous_1m.jsonl", continuous_1m),
        "continuous5mJsonl": write_jsonl(output_dir / "continuous_5m.jsonl", continuous_5m),
        "dailyJsonl": write_jsonl(output_dir / "daily_context.jsonl", daily),
        "latestDay1mCsv": write_csv(output_dir / "latest_day_1m_enriched.csv", enriched_latest_1m),
        "latestDay5mCsv": write_csv(output_dir / "latest_day_5m_enriched.csv", enriched_latest_5m),
        "latestDay1mJson": write_json(output_dir / "latest_day_1m_enriched.json", enriched_latest_1m),
        "latestDay5mJson": write_json(output_dir / "latest_day_5m_enriched.json", enriched_latest_5m),
    }

    manifest = {
        "preparedAt": now.isoformat(),
        "symbol": normalized_symbol,
        "feed": feed,
        "lookbackDays": lookback_days,
        "dailyLookbackDays": daily_lookback_days,
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "dataStart": intraday_start,
        "dataEnd": end,
        "latestSessionDate": latest_day,
        "previousDailyClose": previous_close,
        "coverage": {
            "oneMinute": coverage_summary(continuous_1m),
            "fiveMinute": coverage_summary(continuous_5m),
            "daily": coverage_summary(daily),
            "latestDayOneMinute": coverage_summary(latest_1m),
            "latestDayFiveMinute": coverage_summary(latest_5m),
        },
        "fields": backtest_field_manifest(),
        "files": files,
        "warnings": warnings,
    }
    manifest_path = write_json(output_dir / "manifest.json", manifest)
    manifest["manifest"] = manifest_path
    return manifest


@app.get("/api/backtest-data/daily-refresh/status")
def backtest_daily_refresh_status() -> dict:
    status = dict(DAILY_BACKTEST_REFRESH_STATUS)
    job = status.get("artifactJob")
    if isinstance(job, dict) and job.get("jobId"):
        latest_job = read_artifact_job_status(str(job["jobId"]))
        if latest_job:
            status["artifactJob"] = latest_job
            status["artifactStatus"] = latest_job.get("status", status.get("artifactStatus"))
            if isinstance(status.get("result"), dict):
                status["result"]["artifactStatus"] = status["artifactStatus"]
                status["result"]["artifactJob"] = latest_job
    result = status.get("result")
    dynamic_job = result.get("dynamicArtifactJob") if isinstance(result, dict) else None
    if isinstance(dynamic_job, dict) and dynamic_job.get("jobId"):
        latest_dynamic_job = read_artifact_job_status(str(dynamic_job["jobId"]))
        if latest_dynamic_job:
            status["dynamicArtifactStatus"] = latest_dynamic_job.get("status", status.get("dynamicArtifactStatus"))
            if isinstance(status.get("result"), dict):
                status["result"]["dynamicArtifactStatus"] = status["dynamicArtifactStatus"]
                status["result"]["dynamicArtifactJob"] = latest_dynamic_job
    forecast_job = result.get("forecastTrainingJob") if isinstance(result, dict) else status.get("forecastTrainingJob")
    if isinstance(forecast_job, dict) and forecast_job.get("jobId"):
        latest_forecast_job = read_artifact_job_status(str(forecast_job["jobId"]))
        if latest_forecast_job:
            status["forecastTrainingStatus"] = latest_forecast_job.get("status", status.get("forecastTrainingStatus"))
            status["forecastTrainingJob"] = latest_forecast_job
            if isinstance(status.get("result"), dict):
                status["result"]["forecastTrainingStatus"] = status["forecastTrainingStatus"]
                status["result"]["forecastTrainingJob"] = latest_forecast_job
    return status


@app.post("/api/backtest-data/daily-refresh")
async def backtest_daily_refresh(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    max_pages: int = Query(25, ge=1, le=300),
    force: bool = Query(False),
) -> dict:
    return await run_daily_backtest_refresh(
        symbol=symbol.upper(),
        feed=feed,
        start_date=start_date,
        end_date=end_date,
        max_pages=max_pages,
        force=force,
    )


@app.get("/api/backtest-data/latest")
def latest_backtest_data_manifest(symbol: str = Query("SPY", min_length=1, max_length=12)) -> dict:
    manifest = best_backtest_manifest_or_none(symbol.upper())
    if not manifest:
        raise HTTPException(status_code=404, detail="No prepared backtest data found")
    return manifest


@app.get("/api/backtest-data/candles")
def backtest_data_candles(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    timeframe: Literal["1Min", "5Min", "1Day"] = "1Min",
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    normalized_symbol = symbol.upper()
    manifest = backtest_data_manifest_for_range(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    file_key = "dailyJsonl" if timeframe == "1Day" else "continuous5mJsonl" if timeframe == "5Min" else "continuous1mJsonl"
    data_path = Path(manifest.get("files", {}).get(file_key, ""))
    if not data_path.exists():
        raise HTTPException(status_code=404, detail=f"{timeframe} prepared backtest candles missing")
    return {
        "source": "prepared-backtest-data",
        "symbol": normalized_symbol,
        "timeframe": timeframe,
        "startDate": start_date,
        "endDate": end_date,
        "sourceManifest": manifest.get("manifest") or str(data_path.parent / "manifest.json"),
        "candles": read_jsonl(data_path),
    }


@app.post("/api/backtest-data/artifacts/regenerate")
def regenerate_backtest_data_artifacts(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol.upper(), start_date=start_date, end_date=end_date)
    job = start_artifact_regeneration_job(
        manifest=manifest,
        symbol=symbol.upper(),
        start_date=start_date,
        end_date=end_date,
        reason="manual",
    )
    DAILY_BACKTEST_REFRESH_STATUS["artifactStatus"] = job["status"]
    DAILY_BACKTEST_REFRESH_STATUS["artifactJob"] = job
    return job


@app.get("/api/backtest-data/artifacts/jobs/{job_id}")
def backtest_data_artifact_job(job_id: str) -> dict:
    job = read_artifact_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Artifact job not found")
    return job


@app.get("/api/backtest-data/artifacts/latest")
def latest_backtest_data_artifact_job() -> dict:
    job = latest_artifact_job_status()
    if not job:
        raise HTTPException(status_code=404, detail="No artifact jobs found")
    return job


@app.get("/api/voting-ensemble/backtest")
def voting_ensemble_backtest(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    timeframe: Literal["1Min", "5Min", "1Hour", "1Day", "1Week"] = "1Min",
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    max_trades: int = Query(200, ge=20, le=1000),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    file_key = "dailyJsonl" if timeframe in {"1Day", "1Week"} else "continuous5mJsonl" if timeframe == "5Min" else "continuous1mJsonl"
    data_path = Path(manifest.get("files", {}).get(file_key, ""))
    if not data_path.exists():
        raise HTTPException(status_code=404, detail=f"{timeframe} backtest dataset missing")

    result = cached_voting_ensemble_backtest(
        data_path=data_path,
        manifest=manifest,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    total_trades = len(result["trades"])
    response = {
        **result,
        "trades": result["trades"][-max_trades:],
        "totalTrades": total_trades,
        "displayedTrades": min(total_trades, max_trades),
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(data_path.parent / "manifest.json"),
        "startDate": start_date,
        "endDate": end_date,
        "timeframe": timeframe,
        "rangeLabel": f"{start_date} to {end_date}",
    }
    return response


@app.get("/api/open-close-events/backtest")
def open_close_events_backtest(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    max_trades: int = Query(200, ge=20, le=1000),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    data_path = Path(manifest.get("files", {}).get("continuous1mJsonl", ""))
    daily_path = Path(manifest.get("files", {}).get("dailyJsonl", ""))
    if not data_path.exists() or not daily_path.exists():
        raise HTTPException(status_code=404, detail="Opening/closing event dataset missing")

    result = cached_open_close_events_backtest(
        data_path=data_path,
        daily_path=daily_path,
        manifest=manifest,
        start_date=start_date,
        end_date=end_date,
    )
    total_trades = len(result["trades"])
    return {
        **result,
        "trades": result["trades"][-max_trades:],
        "totalTrades": total_trades,
        "displayedTrades": min(total_trades, max_trades),
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(data_path.parent / "manifest.json"),
        "startDate": start_date,
        "endDate": end_date,
        "timeframe": "Event",
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.get("/api/voting-ensemble/ml-comparison")
def voting_ensemble_ml_comparison(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"ml_comparison_v2_{symbol.upper()}_{start_date}_{end_date}.json",
        label="ML comparison",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.post("/api/voting-ensemble/dynamic-artifact")
def voting_ensemble_dynamic_artifact(payload: dict = Body(...)) -> dict:
    return build_dynamic_trading_artifact(payload)


@app.post("/api/voting-ensemble/dynamic-artifact/jobs")
def start_voting_ensemble_dynamic_artifact_job(payload: dict = Body(...)) -> dict:
    return start_dynamic_trading_artifact_job(payload)


@app.get("/api/voting-ensemble/dynamic-artifact/jobs/{job_id}")
def voting_ensemble_dynamic_artifact_job(job_id: str) -> dict:
    job = read_artifact_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Dynamic artifact job not found")
    artifact_path = str(job.get("artifactPath") or "")
    if str(job.get("status") or "").lower() == "ready" and artifact_path and Path(artifact_path).exists():
        job["artifact"] = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    return job


@app.get("/api/voting-ensemble/dynamic-artifact/latest")
def latest_voting_ensemble_dynamic_artifact(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    normalized_symbol = symbol.upper()
    manifest = backtest_data_manifest_for_range(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    settings = DEFAULT_TRADING_SETTINGS
    config_hash = risk_config_hash(dynamic_risk_config(settings))
    artifact_path = backtest_cache_dir(manifest) / f"dynamic_trading_artifact_v1_{normalized_symbol}_{start_date}_{end_date}_{config_hash}.json"
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        return {
            **artifact,
            "artifactPath": str(artifact_path),
            "sourceManifest": manifest.get("manifest") or artifact.get("sourceManifest"),
        }

    latest_job = latest_dynamic_artifact_job_status(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        config_hash=config_hash,
    )
    latest_artifact_path_value = str((latest_job or {}).get("artifactPath") or "")
    latest_artifact_path = Path(latest_artifact_path_value) if latest_artifact_path_value else None
    if latest_artifact_path and latest_artifact_path.exists():
        artifact = json.loads(latest_artifact_path.read_text(encoding="utf-8"))
        return {
            **artifact,
            "artifactPath": str(latest_artifact_path),
            "sourceManifest": artifact.get("sourceManifest") or manifest.get("manifest"),
        }
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Daily Trading Settings artifact is not ready yet.",
            "expectedPath": str(artifact_path),
            "latestJob": latest_job,
        },
    )


def build_dynamic_trading_artifact(payload: dict) -> dict:
    symbol = str(payload.get("symbol") or "SPY").upper()
    start_date = str(payload.get("startDate") or "2020-07-28")
    end_date = str(payload.get("endDate") or "2026-06-18")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) or not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
        raise HTTPException(status_code=422, detail="startDate and endDate must use YYYY-MM-DD")

    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    risk_config = dynamic_risk_config(payload.get("settings") or {})
    config_hash = risk_config_hash(risk_config)
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)
    backtests = dynamic_backtest_results(replay_data, risk_config)
    ml_comparison = dynamic_ml_comparison(replay_data, backtests, risk_config, symbol=symbol)
    cache_dir = backtest_cache_dir(manifest)
    artifact = {
        "status": "Ready",
        "version": "dynamic_trading_artifact_v1",
        "artifactId": f"{symbol}_{start_date}_{end_date}_{config_hash}",
        "configHash": config_hash,
        "createdAt": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
        "riskConfig": risk_config,
        "backtests": {timeframe: compact_backtest_result(result) for timeframe, result in backtests.items()},
        "mlComparison": ml_comparison,
        "sourceManifest": manifest.get("manifest") or str(cache_dir / "manifest.json"),
    }
    artifact_path = cache_dir / f"dynamic_trading_artifact_v1_{symbol}_{start_date}_{end_date}_{config_hash}.json"
    artifact["artifactPath"] = write_json(artifact_path, artifact)
    return artifact


@app.get("/api/voting-ensemble/candidate-dataset")
def voting_ensemble_candidate_dataset(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"candidate_dataset_v1_{symbol.upper()}_{start_date}_{end_date}_manifest.json",
        label="Candidate dataset",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.get("/api/voting-ensemble/ml-diagnostics")
def voting_ensemble_ml_diagnostics(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"ml_diagnostics_v1_{symbol.upper()}_{start_date}_{end_date}.json",
        label="ML diagnostics",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.get("/api/voting-ensemble/daily-refinement")
def voting_ensemble_daily_refinement(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"daily_refinement_v1_{symbol.upper()}_{start_date}_{end_date}.json",
        label="Daily refinement",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.get("/api/voting-ensemble/event-refinement")
def voting_ensemble_event_refinement(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"event_refinement_v1_{symbol.upper()}_{start_date}_{end_date}.json",
        label="Event refinement",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.get("/api/voting-ensemble/weekly-risk-tuning")
def voting_ensemble_weekly_risk_tuning(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    start_date: str = Query("2020-07-28", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query("2026-06-18", pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict:
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    result = read_required_artifact_json(
        manifest=manifest,
        filename=f"weekly_risk_tuning_v1_{symbol.upper()}_{start_date}_{end_date}.json",
        label="Weekly risk tuning",
    )
    return {
        **result,
        "symbol": symbol.upper(),
        "sourceManifest": manifest.get("manifest") or str(Path(str(manifest.get("manifest") or "")).resolve()),
        "startDate": start_date,
        "endDate": end_date,
        "rangeLabel": f"{start_date} to {end_date}",
    }


@app.post("/api/voting-ensemble/trading-rag")
async def voting_ensemble_trading_rag(payload: dict | None = Body(None)) -> dict:
    request = payload or {}
    symbol = str(request.get("symbol") or "SPY").upper()
    start_date = str(request.get("startDate") or "2020-07-28")[:10]
    end_date = str(request.get("endDate") or "2026-06-18")[:10]
    query = str(
        request.get("query")
        or "Given today's SPY condition and current strategy votes, which strategy historically worked best?"
    )
    manifest = backtest_data_manifest_for_range(symbol=symbol, start_date=start_date, end_date=end_date)
    corpus = cached_trading_rag_corpus(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    current = {
        "winner": request.get("winner"),
        "votes": request.get("votes", []),
        "voteCounts": request.get("voteCounts", {}),
        "marketContext": request.get("marketContext", {}),
        "selectedTimeframe": request.get("selectedTimeframe"),
    }
    retrieved = retrieve_trading_rag_docs(corpus.get("documents", []), query=query, current=current, limit=5)
    try:
        answer = await ask_local_model_for_trading_rag(query=query, current=current, retrieved=retrieved)
        source = "Local RAG"
        warning = ""
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        answer = fallback_trading_rag_answer(query=query, current=current, retrieved=retrieved)
        source = "Local RAG fallback"
        warning = f"Local model unavailable: {exc}"
    return {
        "source": source,
        "updatedAt": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "query": query,
        "answer": answer,
        "retrieved": retrieved,
        "corpus": {
            "documentCount": len(corpus.get("documents", [])),
            "path": corpus.get("path"),
            "createdAt": corpus.get("createdAt"),
            "range": corpus.get("range"),
        },
        "warning": warning,
    }


def backtest_data_manifest_for_range(*, symbol: str, start_date: str, end_date: str) -> dict:
    root = BACKTEST_EXPORT_DIR / symbol.upper()
    if not root.exists():
        raise HTTPException(status_code=404, detail="No prepared backtest data found")
    for run in sorted([path for path in root.iterdir() if path.is_dir()], reverse=True):
        manifest_path = run / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["manifest"] = str(manifest_path)
        requested_start = str(manifest.get("requestedStartDate") or "")[:10]
        requested_end = str(manifest.get("requestedEndDate") or "")[:10]
        if requested_start and requested_end and requested_start <= start_date and requested_end >= end_date:
            return manifest
    raise HTTPException(
        status_code=404,
        detail=f"No prepared dataset covers {start_date} to {end_date}. Prepare backtest data first.",
    )


async def end_of_day_backtest_refresh_scheduler() -> None:
    while True:
        try:
            wait_seconds, target_date, scheduled_for, market_status = await next_end_of_day_refresh_schedule()
            DAILY_BACKTEST_REFRESH_STATUS.update(
                {
                    "status": "scheduled",
                    "nextRunAt": scheduled_for.isoformat(),
                    "scheduledTargetDate": target_date,
                    "message": f"End-of-day refresh scheduled for {scheduled_for.isoformat()} after market close.",
                    "marketStatus": market_status,
                }
            )
            await asyncio.sleep(wait_seconds)
            await maybe_run_end_of_day_backtest_refresh()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive scheduler guard
            DAILY_BACKTEST_REFRESH_STATUS.update(
                {
                    "status": "error",
                    "lastRunAt": datetime.now(UTC).isoformat(),
                    "message": f"End-of-day refresh scheduler failed: {exc}",
                }
            )
            await asyncio.sleep(5 * 60)


async def market_forecast_ledger_scheduler() -> None:
    while True:
        wait_seconds = MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS
        try:
            market_status = await safe_market_status()
            async with MARKET_FORECAST_LEDGER_TICK_LOCK:
                wait_seconds = await run_market_forecast_ledger_tick(market_status)
        except Exception as exc:  # pragma: no cover - defensive scheduler guard
            now = datetime.now(UTC)
            wait_seconds = MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS
            MARKET_FORECAST_LEDGER_STATUS.update(
                {
                    "status": "error",
                    "lastRunAt": now.isoformat().replace("+00:00", "Z"),
                    "nextRunAt": (now + timedelta(seconds=wait_seconds)).isoformat().replace("+00:00", "Z"),
                    "message": f"Future Market Prediction Ledger failed: {exc}",
                }
            )
        await asyncio.sleep(max(15, wait_seconds))


async def run_market_forecast_ledger_tick(market_status: dict) -> float:
    now = datetime.now(UTC)
    normalized_symbol = MARKET_FORECAST_LEDGER_SYMBOL.upper()
    is_open = bool(market_status.get("isOpen"))
    candles = await load_market_forecast_ledger_candles(normalized_symbol)
    if not candles:
        wait_seconds = MARKET_FORECAST_LEDGER_POLL_SECONDS if is_open else seconds_until_next_market_open(market_status, fallback=MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS)
        MARKET_FORECAST_LEDGER_STATUS.update(
            {
                "status": "waiting_for_data" if is_open else "waiting_for_open",
                "lastRunAt": now.isoformat().replace("+00:00", "Z"),
                "lastResult": None,
                "nextRunAt": (now + timedelta(seconds=wait_seconds)).isoformat().replace("+00:00", "Z"),
                "marketStatus": market_status,
                "message": "No candles available for Future Market Prediction Ledger.",
            }
        )
        return wait_seconds

    if is_open:
        microstructure_rows = load_microstructure_rows_for_candles(normalized_symbol, MARKET_FORECAST_LEDGER_FEED, candles)
        forecast = market_forecast_prediction(candles, microstructure_rows=microstructure_rows)
        result = record_market_forecast_prediction(
            normalized_symbol,
            MARKET_FORECAST_LEDGER_FEED,
            MARKET_FORECAST_LEDGER_TIMEFRAME,
            candles,
            forecast,
        )
        wait_seconds = seconds_until_next_ledger_poll(now, market_status)
        MARKET_FORECAST_LEDGER_STATUS.update(
            {
                "status": "recording",
                "lastRunAt": now.isoformat().replace("+00:00", "Z"),
                "lastPredictionTimestamp": (candles[-1] or {}).get("timestamp"),
                "lastSaved": bool(result.get("saved")),
                "lastResult": result,
                "nextRunAt": (now + timedelta(seconds=wait_seconds)).isoformat().replace("+00:00", "Z"),
                "marketStatus": market_status,
                "message": "Future Market Prediction Ledger is recording 5-minute prediction rows while the market is open.",
            }
        )
        return wait_seconds

    resolve_result = resolve_market_forecast_ledger_pending(normalized_symbol, candles)
    wait_seconds = seconds_until_next_market_open(market_status, fallback=MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS)
    MARKET_FORECAST_LEDGER_STATUS.update(
        {
            "status": "waiting_for_open",
            "lastRunAt": now.isoformat().replace("+00:00", "Z"),
            "lastPredictionTimestamp": (candles[-1] or {}).get("timestamp"),
            "lastSaved": False,
            "lastResult": resolve_result,
            "nextRunAt": (now + timedelta(seconds=wait_seconds)).isoformat().replace("+00:00", "Z"),
            "marketStatus": market_status,
            "message": "Market is closed; Future Market Prediction Ledger is not creating new rows and has resolved pending records where possible.",
        }
    )
    return wait_seconds


async def safe_market_status() -> dict:
    try:
        return await alpaca.get_market_status()
    except httpx.HTTPStatusError as exc:
        return local_market_status(warning=exc.response.text)
    except httpx.HTTPError as exc:
        return local_market_status(warning=str(exc))


async def load_market_forecast_ledger_candles(symbol: str) -> list[dict]:
    cached = store.latest(
        symbol=symbol,
        timeframe=MARKET_FORECAST_LEDGER_TIMEFRAME,
        feed=MARKET_FORECAST_LEDGER_FEED,
        limit=MARKET_FORECAST_LEDGER_LIMIT,
    )
    try:
        fresh = await alpaca.get_bars(
            symbol=symbol,
            timeframe=MARKET_FORECAST_LEDGER_TIMEFRAME,
            feed=MARKET_FORECAST_LEDGER_FEED,
            limit=MARKET_FORECAST_LEDGER_LIMIT,
            start=None,
            end=None,
            sort="asc",
        )
        if fresh:
            store.upsert_many(fresh)
            return fresh
    except httpx.HTTPError:
        pass
    return cached


def resolve_market_forecast_ledger_pending(symbol: str, candles: list[dict]) -> dict:
    days = sorted({prediction_log_day(str(candle.get("timestamp") or "")) for candle in candles if candle.get("timestamp")})
    results = [resolve_market_forecast_prediction_day(symbol, day, candles) for day in days]
    return {
        "saved": False,
        "mode": "resolve_only",
        "days": days,
        "updatedFiles": [str(result["path"]) for result in results if result.get("updated")],
        "resolvedRecords": sum(int(result.get("resolved") or 0) for result in results),
        "pendingRecords": sum(int(result.get("pending") or 0) for result in results),
    }


def seconds_until_next_ledger_poll(now: datetime, market_status: dict) -> float:
    close_at = market_session_close_datetime_utc(market_status)
    next_poll = now.replace(second=20, microsecond=0) + timedelta(minutes=1)
    if close_at and next_poll > close_at + timedelta(minutes=6):
        return MARKET_FORECAST_LEDGER_CLOSED_POLL_SECONDS
    return max(15.0, (next_poll - now).total_seconds())


def seconds_until_next_market_open(market_status: dict, *, fallback: float) -> float:
    next_open = parse_market_datetime(str(market_status.get("nextOpen") or ""))
    if not next_open:
        return fallback
    now = datetime.now(UTC)
    return max(60.0, min((next_open - now).total_seconds(), fallback))


async def maybe_run_end_of_day_backtest_refresh() -> None:
    if not settings.has_alpaca_credentials:
        DAILY_BACKTEST_REFRESH_STATUS.update(
            {
                "status": "idle",
                "message": "End-of-day refresh skipped because Alpaca credentials are not configured.",
            }
        )
        return
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    market_status = await alpaca.get_market_status()
    target_date = end_of_day_refresh_target_date(market_status, eastern_now)
    if not target_date:
        DAILY_BACKTEST_REFRESH_STATUS.update(
            {
                "status": "scheduled",
                "lastRunAt": now.isoformat(),
                "message": "End-of-day refresh is waiting for the current market session to close.",
                "marketStatus": market_status,
            }
        )
        return
    if DAILY_BACKTEST_REFRESH_STATUS.get("lastTargetDate") == target_date and DAILY_BACKTEST_REFRESH_STATUS.get("status") == "ready":
        return
    refresh_result = await run_daily_backtest_refresh(
        symbol="SPY",
        feed="iex",
        start_date="2020-07-28",
        end_date=target_date,
        max_pages=25,
        force=False,
    )
    label_result = label_decision_snapshots_for_session(symbol="SPY", feed="iex", session_date=target_date)
    DAILY_BACKTEST_REFRESH_STATUS["decisionLabelStatus"] = label_result
    if isinstance(refresh_result, dict):
        DAILY_BACKTEST_REFRESH_STATUS["result"] = {**refresh_result, "decisionLabelStatus": label_result}


async def next_end_of_day_refresh_schedule() -> tuple[float, str, datetime, dict]:
    now = datetime.now(UTC)
    market_status = await alpaca.get_market_status()
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    completed_target = str(DAILY_BACKTEST_REFRESH_STATUS.get("lastTargetDate") or "")
    target_date = end_of_day_refresh_target_date(market_status, eastern_now)
    if target_date and completed_target != target_date:
        close_at = market_session_close_datetime_utc(market_status)
        scheduled_for = (close_at + timedelta(minutes=10)) if close_at else now
        scheduled_for = max_datetime(now, scheduled_for)
        return max(0.0, (scheduled_for - now).total_seconds()), target_date, scheduled_for, market_status

    close_at = market_session_close_datetime_utc(market_status)
    if close_at and close_at > now:
        target = close_at.astimezone(eastern_tz_for_date(close_at.year, close_at.month, close_at.day)).date().isoformat()
        scheduled_for = close_at + timedelta(minutes=10)
        return max(0.0, (scheduled_for - now).total_seconds()), target, scheduled_for, market_status

    next_close = parse_market_datetime(str(market_status.get("nextClose") or ""))
    if next_close:
        target = next_close.astimezone(eastern_tz_for_date(next_close.year, next_close.month, next_close.day)).date().isoformat()
        scheduled_for = next_close + timedelta(minutes=10)
        return max(0.0, (scheduled_for - now).total_seconds()), target, scheduled_for, market_status

    scheduled_for = now + timedelta(minutes=5)
    return 5 * 60, previous_completed_market_session_date(), scheduled_for, market_status


def end_of_day_refresh_target_date(market_status: dict, eastern_now: datetime) -> str | None:
    if today_market_is_closed_after_session(market_status, eastern_now):
        return eastern_now.date().isoformat()
    if market_status.get("isOpen"):
        return None
    session = market_status.get("session") or {}
    if session.get("holiday") or not session.get("close"):
        return previous_completed_market_session_date()
    return None


def today_market_is_closed_after_session(market_status: dict, eastern_now: datetime) -> bool:
    if market_status.get("isOpen"):
        return False
    if str(market_status.get("status") or "").lower() != "closed":
        return False
    session = market_status.get("session") or {}
    session_date = str(session.get("date") or "")
    if session_date != eastern_now.date().isoformat():
        return False
    close_text = str(session.get("close") or "")
    if not close_text:
        return True
    close_parts = close_text.split(":")
    normalized_close = close_text if len(close_parts) >= 3 else f"{close_text}:00"
    close_time = datetime.fromisoformat(f"{session_date}T{normalized_close}").replace(tzinfo=eastern_now.tzinfo)
    return eastern_now >= close_time


def market_session_close_datetime_utc(market_status: dict) -> datetime | None:
    session = market_status.get("session") or {}
    session_date = str(session.get("date") or "")
    close_text = str(session.get("close") or "")
    if not session_date or not close_text:
        return None
    close_parts = close_text.split(":")
    normalized_close = close_text if len(close_parts) >= 3 else f"{close_text}:00"
    parsed_date = datetime.fromisoformat(session_date).date()
    eastern = eastern_tz_for_date(parsed_date.year, parsed_date.month, parsed_date.day)
    return datetime.fromisoformat(f"{session_date}T{normalized_close}").replace(tzinfo=eastern).astimezone(UTC)


def parse_market_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def max_datetime(left: datetime, right: datetime) -> datetime:
    return left if left >= right else right


async def run_daily_backtest_refresh(
    *,
    symbol: str,
    feed: str,
    start_date: str,
    end_date: str | None,
    max_pages: int,
    force: bool,
) -> dict:
    normalized_symbol = symbol.upper()
    if not settings.has_alpaca_credentials:
        result = {
            "status": "skipped",
            "message": "Daily refresh skipped because Alpaca credentials are not configured.",
            "symbol": normalized_symbol,
        }
        DAILY_BACKTEST_REFRESH_STATUS.update(
            {
                "status": "skipped",
                "lastRunAt": datetime.now(UTC).isoformat(),
                "message": result["message"],
                "result": result,
            }
        )
        return result

    target_date = end_date or previous_completed_market_session_date()
    DAILY_BACKTEST_REFRESH_STATUS.update(
        {
            "status": "running",
            "lastRunAt": datetime.now(UTC).isoformat(),
            "lastTargetDate": target_date,
            "message": f"Refreshing {normalized_symbol} through {target_date}.",
        }
    )
    latest_manifest = best_backtest_manifest_or_none(normalized_symbol)
    latest_end = str((latest_manifest or {}).get("requestedEndDate") or "")[:10]
    if latest_end and latest_end >= target_date and not force:
        artifact_job = passive_daily_ml_artifact_status(
            manifest=latest_manifest,
            symbol=normalized_symbol,
            start_date=start_date,
            end_date=latest_end,
            reason="daily_refresh_up_to_date",
        )
        dynamic_job = passive_daily_dynamic_artifact_status(
            symbol=normalized_symbol,
            start_date=start_date,
            end_date=latest_end,
            reason="daily_refresh_up_to_date",
        )
        forecast_job = ensure_daily_market_forecast_training_job(
            symbol=normalized_symbol,
            feed=feed,
            start_date=start_date,
            end_date=latest_end,
            reason="daily_refresh_up_to_date",
        )
        result = {
            "status": "up_to_date",
            "message": f"Backtest dataset already covers {latest_end}; ML artifact status is {artifact_job.get('status')}, daily Trading Settings artifact is {dynamic_job.get('status')}, and future forecast training is {forecast_job.get('status')}.",
            "symbol": normalized_symbol,
            "targetDate": target_date,
            "manifest": latest_manifest,
            "artifactStatus": artifact_job.get("status"),
            "artifactJob": artifact_job,
            "dynamicArtifactStatus": dynamic_job.get("status"),
            "dynamicArtifactJob": dynamic_job,
            "forecastTrainingStatus": forecast_job.get("status"),
            "forecastTrainingJob": forecast_job,
        }
        DAILY_BACKTEST_REFRESH_STATUS.update(
            {
                "status": "ready",
                "lastRunAt": datetime.now(UTC).isoformat(),
                "lastTargetDate": target_date,
                "artifactStatus": artifact_job.get("status"),
                "artifactJob": artifact_job,
                "dynamicArtifactStatus": dynamic_job.get("status"),
                "dynamicArtifactJob": dynamic_job,
                "forecastTrainingStatus": forecast_job.get("status"),
                "forecastTrainingJob": forecast_job,
                "message": result["message"],
                "result": result,
            }
        )
        return result

    warnings: list[str] = []
    session_start, session_end = session_date_window_utc(target_date)
    for timeframe in ["1Min", "5Min", "1Day"]:
        try:
            fresh = await alpaca.get_bars_window(
                symbol=normalized_symbol,
                timeframe=timeframe,
                feed=feed,
                start=session_start,
                end=session_end,
                max_pages=max_pages,
            )
            store.upsert_many(fresh)
            if not fresh:
                warnings.append(f"No Alpaca bars returned for {timeframe} on {target_date}.")
        except httpx.HTTPError as exc:
            warnings.append(f"{timeframe} Alpaca fetch failed for {target_date}: {exc}")

    try:
        manifest = export_backtest_dataset_from_store(
            symbol=normalized_symbol,
            feed=feed,
            start_date=start_date,
            end_date=target_date,
            warnings=warnings,
            refresh_mode="daily_incremental",
        )
    except ValueError as exc:
        result = {
            "status": "error",
            "message": str(exc),
            "symbol": normalized_symbol,
            "targetDate": target_date,
            "artifactStatus": "not_queued",
            "warnings": warnings,
        }
        DAILY_BACKTEST_REFRESH_STATUS.update(
            {
                "status": "error",
                "lastRunAt": datetime.now(UTC).isoformat(),
                "lastTargetDate": target_date,
                "artifactStatus": "not_queued",
                "message": result["message"],
                "result": result,
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    artifact_job = ensure_daily_ml_artifact_job(
        manifest=manifest,
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=target_date,
        reason="daily_refresh",
    )
    dynamic_artifact_job = ensure_daily_dynamic_artifact_job(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=target_date,
        reason="daily_refresh",
    )
    forecast_training_job = ensure_daily_market_forecast_training_job(
        symbol=normalized_symbol,
        feed=feed,
        start_date=start_date,
        end_date=target_date,
        reason="daily_refresh",
    )
    result = {
        "status": "ready",
        "message": f"Daily dataset refreshed through {target_date}; ML artifact status is {artifact_job.get('status')}, Trading Settings artifact is {dynamic_artifact_job.get('status')}, and future forecast training is {forecast_training_job.get('status')}.",
        "symbol": normalized_symbol,
        "targetDate": target_date,
        "manifest": manifest,
        "artifactStatus": artifact_job["status"],
        "artifactJob": artifact_job,
        "dynamicArtifactStatus": dynamic_artifact_job.get("status"),
        "dynamicArtifactJob": dynamic_artifact_job,
        "forecastTrainingStatus": forecast_training_job.get("status"),
        "forecastTrainingJob": forecast_training_job,
        "warnings": warnings,
    }
    DAILY_BACKTEST_REFRESH_STATUS.update(
        {
            "status": "ready",
            "lastRunAt": datetime.now(UTC).isoformat(),
            "lastTargetDate": target_date,
            "artifactStatus": artifact_job["status"],
            "artifactJob": artifact_job,
            "dynamicArtifactStatus": dynamic_artifact_job.get("status"),
            "dynamicArtifactJob": dynamic_artifact_job,
            "forecastTrainingStatus": forecast_training_job.get("status"),
            "forecastTrainingJob": forecast_training_job,
            "message": result["message"],
            "result": result,
        }
    )
    return result


def latest_backtest_manifest_or_none(symbol: str) -> dict | None:
    root = BACKTEST_EXPORT_DIR / symbol.upper()
    if not root.exists():
        return None
    runs = sorted([path for path in root.iterdir() if path.is_dir()], reverse=True)
    for run in runs:
        manifest_path = run / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifest"] = str(manifest_path)
            return manifest
    return None


def best_backtest_manifest_or_none(symbol: str) -> dict | None:
    root = BACKTEST_EXPORT_DIR / symbol.upper()
    if not root.exists():
        return None
    manifests = []
    for run in [path for path in root.iterdir() if path.is_dir()]:
        manifest_path = run / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        manifest["manifest"] = str(manifest_path)
        coverage = manifest.get("coverage", {})
        one_minute = int(coverage.get("oneMinute", {}).get("count") or 0)
        daily = int(coverage.get("daily", {}).get("count") or 0)
        requested_end = str(manifest.get("requestedEndDate") or "")[:10]
        manifests.append((one_minute + daily, requested_end, str(manifest_path), manifest))
    if not manifests:
        return None
    return sorted(manifests, key=lambda item: (item[0], item[1], item[2]), reverse=True)[0][3]


def manifest_coverage_counts(manifest: dict | None) -> dict:
    coverage = (manifest or {}).get("coverage", {})
    return {
        "oneMinute": int(coverage.get("oneMinute", {}).get("count") or 0),
        "fiveMinute": int(coverage.get("fiveMinute", {}).get("count") or 0),
        "daily": int(coverage.get("daily", {}).get("count") or 0),
    }


def build_history_preservation_report(
    *,
    base_manifest: dict | None,
    continuous_1m: list[dict],
    continuous_5m: list[dict],
    daily: list[dict],
    start_date: str,
    end_date: str,
) -> dict:
    base_counts = manifest_coverage_counts(base_manifest)
    new_counts = {
        "oneMinute": len(continuous_1m),
        "fiveMinute": len(continuous_5m),
        "daily": len(daily),
    }
    dropped = {
        key: {"before": before, "after": new_counts[key]}
        for key, before in base_counts.items()
        if before and new_counts[key] < before
    }
    if dropped:
        raise ValueError(f"Daily refresh refused to publish because history coverage dropped: {dropped}")
    return {
        "status": "preserved",
        "mode": "full_history_plus_latest_session",
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "baseManifest": (base_manifest or {}).get("manifest"),
        "baseRequestedEndDate": (base_manifest or {}).get("requestedEndDate"),
        "baseLatestSessionDate": (base_manifest or {}).get("latestSessionDate"),
        "baseCoverage": base_counts,
        "newCoverage": new_counts,
        "coverageDelta": {key: new_counts[key] - base_counts.get(key, 0) for key in new_counts},
    }


def previous_completed_market_session_date() -> str:
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    candidate = previous_weekday(eastern_now.date())
    return candidate.isoformat()


def previous_weekday(value) -> object:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def session_date_window_utc(session_date: str) -> tuple[str, str]:
    parsed = datetime.fromisoformat(session_date).date()
    eastern = eastern_tz_for_date(parsed.year, parsed.month, parsed.day)
    start = datetime(parsed.year, parsed.month, parsed.day, 0, 0, 0, tzinfo=eastern).astimezone(UTC)
    end = datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=eastern).astimezone(UTC)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def export_backtest_dataset_from_store(
    *,
    symbol: str,
    feed: str,
    start_date: str,
    end_date: str,
    warnings: list[str],
    refresh_mode: str,
) -> dict:
    now = datetime.now(UTC)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    output_dir = BACKTEST_EXPORT_DIR / symbol / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    start_datetime = parse_backtest_start_datetime(start_date)
    end_datetime = parse_backtest_end_datetime(end_date)
    intraday_start = start_datetime.isoformat().replace("+00:00", "Z")
    daily_start = intraday_start
    end = end_datetime.isoformat().replace("+00:00", "Z")

    base_manifest = best_backtest_manifest_or_none(symbol)
    base_files = (base_manifest or {}).get("files", {})
    base_1m = read_jsonl_if_exists(Path(str(base_files.get("continuous1mJsonl", ""))))
    base_5m = read_jsonl_if_exists(Path(str(base_files.get("continuous5mJsonl", ""))))
    base_daily = read_jsonl_if_exists(Path(str(base_files.get("dailyJsonl", ""))))
    fresh_1m = store.range(symbol=symbol, timeframe="1Min", feed=feed, start=intraday_start, end=end)
    fresh_5m = store.range(symbol=symbol, timeframe="5Min", feed=feed, start=intraday_start, end=end)
    fresh_daily = store.range(symbol=symbol, timeframe="1Day", feed=feed, start=daily_start, end=end)
    continuous_1m = merge_candle_rows(base_1m, fresh_1m, start=intraday_start, end=end, symbol=symbol, timeframe="1Min", feed=feed)
    continuous_5m = merge_candle_rows(base_5m, fresh_5m, start=intraday_start, end=end, symbol=symbol, timeframe="5Min", feed=feed)
    daily = merge_candle_rows(base_daily, fresh_daily, start=daily_start, end=end, symbol=symbol, timeframe="1Day", feed=feed)
    if not continuous_5m and continuous_1m:
        continuous_5m = aggregate_candles(continuous_1m, timeframe="5Min", minutes=5)
        warnings.append("5Min bars were aggregated from 1Min bars because native 5Min data was unavailable.")
    history_preservation = build_history_preservation_report(
        base_manifest=base_manifest,
        continuous_1m=continuous_1m,
        continuous_5m=continuous_5m,
        daily=daily,
        start_date=start_date,
        end_date=end_date,
    )

    latest_day = latest_session_date(continuous_1m) or latest_session_date(continuous_5m)
    latest_1m = filter_session_date(continuous_1m, latest_day)
    latest_5m = filter_session_date(continuous_5m, latest_day)
    previous_close = previous_daily_close(daily, latest_day)
    enriched_latest_1m = enrich_backtest_candles(latest_1m, previous_close=previous_close)
    enriched_latest_5m = enrich_backtest_candles(latest_5m, previous_close=previous_close)
    files = {
        "continuous1mJsonl": write_jsonl(output_dir / "continuous_1m.jsonl", continuous_1m),
        "continuous5mJsonl": write_jsonl(output_dir / "continuous_5m.jsonl", continuous_5m),
        "dailyJsonl": write_jsonl(output_dir / "daily_context.jsonl", daily),
        "latestDay1mCsv": write_csv(output_dir / "latest_day_1m_enriched.csv", enriched_latest_1m),
        "latestDay5mCsv": write_csv(output_dir / "latest_day_5m_enriched.csv", enriched_latest_5m),
        "latestDay1mJson": write_json(output_dir / "latest_day_1m_enriched.json", enriched_latest_1m),
        "latestDay5mJson": write_json(output_dir / "latest_day_5m_enriched.json", enriched_latest_5m),
    }
    manifest = {
        "preparedAt": now.isoformat(),
        "refreshMode": refresh_mode,
        "symbol": symbol,
        "feed": feed,
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "dataStart": intraday_start,
        "dataEnd": end,
        "latestSessionDate": latest_day,
        "previousDailyClose": previous_close,
        "historyPreservation": history_preservation,
        "coverage": {
            "oneMinute": coverage_summary(continuous_1m),
            "fiveMinute": coverage_summary(continuous_5m),
            "daily": coverage_summary(daily),
            "latestDayOneMinute": coverage_summary(latest_1m),
            "latestDayFiveMinute": coverage_summary(latest_5m),
        },
        "fields": backtest_field_manifest(),
        "files": files,
        "warnings": warnings,
    }
    manifest_path = write_json(output_dir / "manifest.json", manifest)
    manifest["manifest"] = manifest_path
    return manifest


def regenerate_backtest_ml_artifacts(
    *,
    manifest: dict,
    symbol: str,
    start_date: str,
    end_date: str,
    progress_callback=None,
) -> dict:
    def progress(stage: str) -> None:
        if progress_callback:
            progress_callback(stage)

    progress("ml_comparison")
    artifacts = {
        "mlComparison": cached_ml_comparison(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date),
    }
    progress("candidate_dataset")
    artifacts["candidateDataset"] = cached_candidate_dataset(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("ml_diagnostics")
    artifacts["mlDiagnostics"] = cached_ml_diagnostics(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("daily_refinement")
    artifacts["dailyRefinement"] = cached_daily_refinement(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("event_refinement")
    artifacts["eventRefinement"] = cached_event_refinement(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("weekly_risk_tuning")
    artifacts["weeklyRiskTuning"] = cached_weekly_risk_tuning(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("train_test_export")
    train_test = write_train_test_datasets(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    progress("complete")
    return {
        "sourceManifest": manifest.get("manifest"),
        "sourceRange": {
            "startDate": start_date,
            "endDate": end_date,
            "manifestStartDate": manifest.get("requestedStartDate"),
            "manifestEndDate": manifest.get("requestedEndDate"),
            "latestSessionDate": manifest.get("latestSessionDate"),
        },
        "sourceCoverage": manifest.get("coverage"),
        "historyPreservation": manifest.get("historyPreservation"),
        "mlComparisonRows": len(artifacts["mlComparison"].get("rows", [])),
        "candidateRows": artifacts["candidateDataset"].get("rows"),
        "diagnosticFeatures": artifacts["mlDiagnostics"].get("model", {}).get("featureCount"),
        "dailyBest": artifacts["dailyRefinement"].get("best"),
        "eventProfitBest": artifacts["eventRefinement"].get("profitPreservingBest"),
        "weeklyRiskAdjusted": artifacts["weeklyRiskTuning"].get("bestRiskAdjusted"),
        "trainTest": train_test,
    }


def read_required_artifact_json(*, manifest: dict, filename: str, label: str) -> dict:
    cache_path = backtest_cache_dir(manifest) / filename
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    latest_job = latest_artifact_job_status()
    raise HTTPException(
        status_code=409,
        detail={
            "message": f"{label} artifact is not ready. Regenerate ML artifacts to create it.",
            "artifact": label,
            "expectedPath": str(cache_path),
            "latestJob": latest_job,
        },
    )


def artifact_job_path(job_id: str) -> Path:
    safe_job_id = "".join(char for char in job_id if char.isalnum() or char in {"-", "_"})
    if safe_job_id != job_id or not safe_job_id:
        raise ValueError("Invalid artifact job id")
    return ARTIFACT_JOB_DIR / f"{safe_job_id}.json"


def read_artifact_job_status(job_id: str) -> dict | None:
    try:
        path = artifact_job_path(job_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        return enrich_artifact_job_status(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {
            "jobId": job_id,
            "status": "error",
            "message": "Artifact job status file is not valid JSON.",
            "path": str(path),
        }


def enrich_artifact_job_status(job: dict) -> dict:
    if job.get("status") == "running" and job.get("pid") and not process_is_running(int(job["pid"])):
        label = "Dynamic artifact worker" if job.get("jobType") == "dynamic_trading_artifact" else "ML artifact worker"
        job = {
            **job,
            "status": "stopped",
            "completedAt": job.get("completedAt") or datetime.now(UTC).isoformat(),
            "message": f"{label} is no longer running before reporting completion.",
            "error": job.get("error") or "Worker process stopped before completion.",
        }
    if job.get("status") == "running" and artifact_job_is_stale(job):
        label = "Dynamic artifact worker" if job.get("jobType") == "dynamic_trading_artifact" else "ML artifact worker"
        job = {
            **job,
            "status": "stalled",
            "completedAt": job.get("completedAt") or datetime.now(UTC).isoformat(),
            "message": f"{label} stopped reporting progress and will be replaced on the next daily refresh check.",
            "error": job.get("error") or "Worker status did not update before the stale timeout.",
        }
    raw_manifest_path = str(job.get("manifestPath") or "")
    if not raw_manifest_path:
        return job
    manifest_path = Path(raw_manifest_path)
    if not manifest_path.exists() or not manifest_path.is_file():
        return job
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return job
    if not job.get("sourceRange"):
        job["sourceRange"] = {
            "startDate": job.get("startDate"),
            "endDate": job.get("endDate"),
            "manifestStartDate": manifest.get("requestedStartDate"),
            "manifestEndDate": manifest.get("requestedEndDate"),
            "latestSessionDate": manifest.get("latestSessionDate"),
        }
    if not job.get("sourceCoverage"):
        job["sourceCoverage"] = manifest.get("coverage")
    if not job.get("historyPreservation"):
        job["historyPreservation"] = manifest.get("historyPreservation") or {
            "status": "legacy_manifest",
            "note": "This manifest was created before explicit preservation reports were added.",
            "coverage": manifest.get("coverage"),
        }
    return job


def artifact_job_is_stale(job: dict) -> bool:
    status_time = parse_market_datetime(str(job.get("updatedAt") or job.get("startedAt") or job.get("createdAt") or ""))
    if not status_time:
        return False
    age = datetime.now(UTC) - status_time
    if age < ARTIFACT_JOB_EMPTY_LOG_STALE_AFTER:
        return False
    log_path = Path(str(job.get("logPath") or ""))
    if log_path.exists():
        try:
            if log_path.stat().st_size <= 0:
                return True
            log_updated = datetime.fromtimestamp(log_path.stat().st_mtime, tz=UTC)
            if datetime.now(UTC) - log_updated < ARTIFACT_JOB_STALE_AFTER:
                return False
        except OSError:
            return False
    return age >= ARTIFACT_JOB_STALE_AFTER


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def windows_process_is_running(pid: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access can be denied for pythonw children in this local environment. If
        # Windows will not let us inspect the process, avoid falsely marking a
        # running artifact job as stopped.
        return True
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def write_artifact_job_status(job_id: str, status: dict) -> dict:
    path = artifact_job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**status, "jobId": job_id, "statusPath": str(path)}
    write_json(path, payload)
    return payload


def latest_artifact_job_status() -> dict | None:
    if not ARTIFACT_JOB_DIR.exists():
        return None
    jobs = sorted(ARTIFACT_JOB_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in jobs:
        try:
            job = enrich_artifact_job_status(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if str(job.get("jobType") or "") in {"dynamic_trading_artifact", "market_forecast_training"}:
            continue
        return job
    return None


def latest_dynamic_artifact_job_status(*, symbol: str, start_date: str, end_date: str, config_hash: str | None = None) -> dict | None:
    if not ARTIFACT_JOB_DIR.exists():
        return None
    jobs = sorted(ARTIFACT_JOB_DIR.glob("dynamic-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in jobs:
        try:
            job = enrich_artifact_job_status(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        job_symbol = str(job.get("symbol") or payload.get("symbol") or "").upper()
        if job_symbol != symbol.upper():
            continue
        if str(job.get("startDate") or payload.get("startDate") or "") != start_date:
            continue
        if str(job.get("endDate") or payload.get("endDate") or "") != end_date:
            continue
        if config_hash and job.get("configHash") and job.get("configHash") != config_hash:
            continue
        return job
    return None


def latest_ml_artifact_job_status(*, symbol: str, start_date: str, end_date: str) -> dict | None:
    if not ARTIFACT_JOB_DIR.exists():
        return None
    jobs = sorted(ARTIFACT_JOB_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in jobs:
        if path.name.startswith("dynamic-"):
            continue
        try:
            job = enrich_artifact_job_status(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if str(job.get("jobType") or "") == "dynamic_trading_artifact":
            continue
        if str(job.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(job.get("startDate") or "") != start_date:
            continue
        if str(job.get("endDate") or "") != end_date:
            continue
        return job
    return None


def latest_market_forecast_training_job_status(*, symbol: str, start_date: str, end_date: str) -> dict | None:
    if not ARTIFACT_JOB_DIR.exists():
        return None
    jobs = sorted(ARTIFACT_JOB_DIR.glob("forecast-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in jobs:
        try:
            job = enrich_artifact_job_status(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if str(job.get("jobType") or "") != "market_forecast_training":
            continue
        if str(job.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(job.get("startDate") or "") != start_date:
            continue
        if str(job.get("endDate") or "") != end_date:
            continue
        return job
    return None


def market_forecast_artifact_ready(*, symbol: str, end_date: str) -> tuple[bool, str, dict | None]:
    artifact_path = market_forecast_artifact_path(symbol)
    if not artifact_path.exists():
        return False, f"{MARKET_FORECAST_MODEL_VERSION} artifact is missing.", None
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"{MARKET_FORECAST_MODEL_VERSION} artifact cannot be read: {exc}", None
    if artifact.get("version") != MARKET_FORECAST_MODEL_VERSION:
        return False, f"Forecast artifact version is {artifact.get('version')}; expected {MARKET_FORECAST_MODEL_VERSION}.", artifact
    artifact_end = str(((artifact.get("dateRange") or {}).get("endDate")) or "")[:10]
    if artifact_end and artifact_end < end_date:
        return False, f"Forecast artifact ends at {artifact_end}; needs {end_date}.", artifact
    model_file = str(artifact.get("modelFile") or "")
    if model_file and not Path(model_file).exists():
        return False, f"Forecast model file is missing: {model_file}", artifact
    return True, "Future market forecast model is ready.", artifact


def required_daily_ml_artifact_paths(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> list[Path]:
    cache_dir = backtest_cache_dir(manifest)
    normalized_symbol = symbol.upper()
    return [
        cache_dir / f"ml_comparison_v2_{normalized_symbol}_{start_date}_{end_date}.json",
        cache_dir / f"candidate_dataset_v1_{normalized_symbol}_{start_date}_{end_date}.jsonl",
        cache_dir / f"candidate_dataset_v1_{normalized_symbol}_{start_date}_{end_date}.csv",
        cache_dir / f"candidate_dataset_v1_{normalized_symbol}_{start_date}_{end_date}_manifest.json",
        cache_dir / f"ml_diagnostics_v1_{normalized_symbol}_{start_date}_{end_date}.json",
        cache_dir / f"daily_refinement_v1_{normalized_symbol}_{start_date}_{end_date}.json",
        cache_dir / f"event_refinement_v1_{normalized_symbol}_{start_date}_{end_date}.json",
        cache_dir / f"weekly_risk_tuning_v1_{normalized_symbol}_{start_date}_{end_date}.json",
    ]


def passive_daily_ml_artifact_status(*, manifest: dict, symbol: str, start_date: str, end_date: str, reason: str) -> dict:
    normalized_symbol = symbol.upper()
    required_paths = required_daily_ml_artifact_paths(
        manifest=manifest,
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
    )
    missing_paths = [path for path in required_paths if not path.exists()]
    if not missing_paths:
        return {
            "status": "ready",
            "reason": reason,
            "symbol": normalized_symbol,
            "startDate": start_date,
            "endDate": end_date,
            "message": "Daily ML artifacts already exist.",
            "artifacts": {path.stem: str(path) for path in required_paths},
        }
    latest_job = latest_ml_artifact_job_status(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    if latest_job:
        return latest_job
    return {
        "status": "not_queued",
        "reason": reason,
        "symbol": normalized_symbol,
        "startDate": start_date,
        "endDate": end_date,
        "message": f"Daily ML artifacts are missing {len(missing_paths)} files; no worker started for passive status check.",
        "missingArtifacts": [str(path) for path in missing_paths],
    }


def passive_daily_dynamic_artifact_status(*, symbol: str, start_date: str, end_date: str, reason: str) -> dict:
    normalized_symbol = symbol.upper()
    try:
        manifest = backtest_data_manifest_for_range(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    except HTTPException as exc:
        return {
            "status": "not_queued",
            "reason": reason,
            "symbol": normalized_symbol,
            "startDate": start_date,
            "endDate": end_date,
            "message": str(exc.detail),
        }
    settings = dict(DEFAULT_TRADING_SETTINGS)
    config_hash = risk_config_hash(dynamic_risk_config(settings))
    artifact_path = backtest_cache_dir(manifest) / f"dynamic_trading_artifact_v1_{normalized_symbol}_{start_date}_{end_date}_{config_hash}.json"
    if artifact_path.exists():
        return {
            "status": "ready",
            "reason": reason,
            "symbol": normalized_symbol,
            "startDate": start_date,
            "endDate": end_date,
            "configHash": config_hash,
            "artifactPath": str(artifact_path),
            "message": "Daily Trading Settings artifact already exists.",
        }
    latest_job = latest_dynamic_artifact_job_status(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        config_hash=config_hash,
    )
    if latest_job:
        return latest_job
    return {
        "status": "not_queued",
        "reason": reason,
        "symbol": normalized_symbol,
        "startDate": start_date,
        "endDate": end_date,
        "configHash": config_hash,
        "message": "Daily Trading Settings artifact is missing; no worker started for passive status check.",
    }


def ensure_daily_market_forecast_training_job(
    *,
    symbol: str,
    feed: str,
    start_date: str,
    end_date: str,
    reason: str,
) -> dict:
    normalized_symbol = symbol.upper()
    ready, message, artifact = market_forecast_artifact_ready(symbol=normalized_symbol, end_date=end_date)
    if ready:
        return {
            "status": "ready",
            "reason": reason,
            "jobType": "market_forecast_training",
            "symbol": normalized_symbol,
            "feed": feed,
            "startDate": start_date,
            "endDate": end_date,
            "modelVersion": MARKET_FORECAST_MODEL_VERSION,
            "artifactPath": str(market_forecast_artifact_path(normalized_symbol)),
            "trainedAt": artifact.get("trainedAt") if isinstance(artifact, dict) else None,
            "message": message,
        }
    latest_job = latest_market_forecast_training_job_status(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    if latest_job and str(latest_job.get("status") or "").lower() in {"queued", "running", "ready"}:
        return latest_job
    return start_market_forecast_training_job(
        symbol=normalized_symbol,
        feed=feed,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        trigger=message,
    )


def ensure_daily_ml_artifact_job(*, manifest: dict, symbol: str, start_date: str, end_date: str, reason: str) -> dict:
    normalized_symbol = symbol.upper()
    required_paths = required_daily_ml_artifact_paths(
        manifest=manifest,
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
    )
    missing_paths = [path for path in required_paths if not path.exists()]
    if not missing_paths:
        return {
            "status": "ready",
            "reason": reason,
            "symbol": normalized_symbol,
            "startDate": start_date,
            "endDate": end_date,
            "message": "Daily ML artifacts already exist.",
            "artifacts": {path.stem: str(path) for path in required_paths},
        }
    latest_job = latest_ml_artifact_job_status(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    if latest_job and str(latest_job.get("status") or "").lower() in {"queued", "running"}:
        return latest_job
    if latest_job and str(latest_job.get("status") or "").lower() == "ready" and not missing_paths:
        return latest_job
    return start_artifact_regeneration_job(
        manifest=manifest,
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
    )


def ensure_daily_dynamic_artifact_job(*, symbol: str, start_date: str, end_date: str, reason: str) -> dict:
    normalized_symbol = symbol.upper()
    manifest = backtest_data_manifest_for_range(symbol=normalized_symbol, start_date=start_date, end_date=end_date)
    settings = dict(DEFAULT_TRADING_SETTINGS)
    config_hash = risk_config_hash(dynamic_risk_config(settings))
    artifact_path = backtest_cache_dir(manifest) / f"dynamic_trading_artifact_v1_{normalized_symbol}_{start_date}_{end_date}_{config_hash}.json"
    if artifact_path.exists():
        return {
            "status": "ready",
            "reason": reason,
            "symbol": normalized_symbol,
            "startDate": start_date,
            "endDate": end_date,
            "configHash": config_hash,
            "artifactPath": str(artifact_path),
            "message": "Daily Trading Settings artifact already exists.",
        }
    latest_job = latest_dynamic_artifact_job_status(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        config_hash=config_hash,
    )
    if latest_job and str(latest_job.get("status") or "").lower() in {"queued", "running", "ready"}:
        return latest_job
    payload = {
        "symbol": normalized_symbol,
        "startDate": start_date,
        "endDate": end_date,
        "settings": settings,
        "reason": reason,
    }
    return start_dynamic_trading_artifact_job(payload)


def start_artifact_regeneration_job(
    *,
    manifest: dict,
    symbol: str,
    start_date: str,
    end_date: str,
    reason: str,
) -> dict:
    manifest_path = str(manifest.get("manifest") or "")
    if not manifest_path:
        raise HTTPException(status_code=500, detail="Cannot regenerate artifacts without a manifest path")
    resolved_manifest_path = str(Path(manifest_path).resolve())
    if not Path(resolved_manifest_path).exists():
        raise HTTPException(status_code=404, detail="Artifact source manifest does not exist")

    job_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    project_root = Path(__file__).resolve().parents[2]
    created_at = datetime.now(UTC).isoformat()
    queued = write_artifact_job_status(
        job_id,
        {
            "status": "queued",
            "reason": reason,
            "symbol": symbol.upper(),
            "startDate": start_date,
            "endDate": end_date,
            "manifestPath": resolved_manifest_path,
            "logPath": str(ARTIFACT_JOB_DIR / f"{job_id}.log"),
            "sourceRange": {
                "startDate": start_date,
                "endDate": end_date,
                "manifestStartDate": manifest.get("requestedStartDate"),
                "manifestEndDate": manifest.get("requestedEndDate"),
                "latestSessionDate": manifest.get("latestSessionDate"),
            },
            "sourceCoverage": manifest.get("coverage"),
            "historyPreservation": manifest.get("historyPreservation"),
            "createdAt": created_at,
            "startedAt": None,
            "completedAt": None,
            "pid": None,
            "message": "ML artifact regeneration queued.",
            "artifacts": None,
            "error": None,
        },
    )
    command = [
        sys.executable,
        "-m",
        "backend.app.artifact_worker",
        "--job-id",
        job_id,
        "--manifest",
        resolved_manifest_path,
        "--symbol",
        symbol.upper(),
        "--start-date",
        start_date,
        "--end-date",
        end_date,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    log_path = ARTIFACT_JOB_DIR / f"{job_id}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        failed = write_artifact_job_status(
            job_id,
            {
                **queued,
                "status": "error",
                "completedAt": datetime.now(UTC).isoformat(),
                "message": f"Could not start ML artifact worker: {exc}",
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=failed["message"]) from exc

    return write_artifact_job_status(
        job_id,
        {
            **queued,
            "pid": process.pid,
            "message": "ML artifact regeneration worker started.",
        },
    )


def start_market_forecast_training_job(
    *,
    symbol: str,
    feed: str,
    start_date: str,
    end_date: str,
    reason: str,
    trigger: str,
) -> dict:
    job_id = f"forecast-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    project_root = Path(__file__).resolve().parents[2]
    created_at = datetime.now(UTC).isoformat()
    queued = write_artifact_job_status(
        job_id,
        {
            "status": "queued",
            "jobType": "market_forecast_training",
            "reason": reason,
            "trigger": trigger,
            "symbol": symbol.upper(),
            "feed": feed,
            "startDate": start_date,
            "endDate": end_date,
            "modelVersion": MARKET_FORECAST_MODEL_VERSION,
            "artifactPath": str(market_forecast_artifact_path(symbol)),
            "logPath": str(ARTIFACT_JOB_DIR / f"{job_id}.log"),
            "createdAt": created_at,
            "startedAt": None,
            "completedAt": None,
            "pid": None,
            "message": "Future market forecast retraining queued.",
            "summary": None,
            "error": None,
        },
    )
    command = [
        sys.executable,
        "-m",
        "backend.app.market_forecast_worker",
        "--job-id",
        job_id,
        "--symbol",
        symbol.upper(),
        "--feed",
        feed,
        "--start-date",
        start_date,
        "--end-date",
        end_date,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    log_path = ARTIFACT_JOB_DIR / f"{job_id}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        failed = write_artifact_job_status(
            job_id,
            {
                **queued,
                "status": "error",
                "completedAt": datetime.now(UTC).isoformat(),
                "message": f"Could not start future market forecast worker: {exc}",
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=failed["message"]) from exc

    return write_artifact_job_status(
        job_id,
        {
            **queued,
            "status": "running",
            "startedAt": datetime.now(UTC).isoformat(),
            "pid": process.pid,
            "message": "Future market forecast retraining worker started.",
        },
    )


def start_dynamic_trading_artifact_job(payload: dict) -> dict:
    symbol = str(payload.get("symbol") or "SPY").upper()
    start_date = str(payload.get("startDate") or "2020-07-28")
    end_date = str(payload.get("endDate") or "2026-06-18")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) or not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
        raise HTTPException(status_code=422, detail="startDate and endDate must use YYYY-MM-DD")

    job_id = f"dynamic-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    project_root = Path(__file__).resolve().parents[2]
    created_at = datetime.now(UTC).isoformat()
    queued = write_artifact_job_status(
        job_id,
        {
            "status": "queued",
            "jobType": "dynamic_trading_artifact",
            "reason": "Trading Settings backtest and ML replay",
            "symbol": symbol,
            "startDate": start_date,
            "endDate": end_date,
            "payload": {**payload, "symbol": symbol, "startDate": start_date, "endDate": end_date},
            "logPath": str(ARTIFACT_JOB_DIR / f"{job_id}.log"),
            "createdAt": created_at,
            "startedAt": None,
            "completedAt": None,
            "pid": None,
            "message": "Dynamic Trading Settings artifact queued.",
            "artifactPath": None,
            "error": None,
        },
    )
    command = [sys.executable, "-m", "backend.app.dynamic_artifact_worker", "--job-id", job_id]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    log_path = ARTIFACT_JOB_DIR / f"{job_id}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        failed = write_artifact_job_status(
            job_id,
            {
                **queued,
                "status": "error",
                "completedAt": datetime.now(UTC).isoformat(),
                "message": f"Could not start dynamic artifact worker: {exc}",
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=failed["message"]) from exc

    return write_artifact_job_status(
        job_id,
        {
            **queued,
            "status": "running",
            "startedAt": datetime.now(UTC).isoformat(),
            "pid": process.pid,
            "message": "Dynamic Trading Settings artifact worker started.",
        },
    )


async def regenerate_backtest_ml_artifacts_background(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> None:
    DAILY_BACKTEST_REFRESH_STATUS["artifactStatus"] = "running"
    if isinstance(DAILY_BACKTEST_REFRESH_STATUS.get("result"), dict):
        DAILY_BACKTEST_REFRESH_STATUS["result"]["artifactStatus"] = "running"
    try:
        artifacts = await asyncio.to_thread(
            regenerate_backtest_ml_artifacts,
            manifest=manifest,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        DAILY_BACKTEST_REFRESH_STATUS["artifactStatus"] = "ready"
        DAILY_BACKTEST_REFRESH_STATUS["message"] = f"Daily dataset refreshed through {end_date}; ML artifacts regenerated."
        if isinstance(DAILY_BACKTEST_REFRESH_STATUS.get("result"), dict):
            DAILY_BACKTEST_REFRESH_STATUS["result"]["artifactStatus"] = "ready"
            DAILY_BACKTEST_REFRESH_STATUS["result"]["artifacts"] = artifacts
            DAILY_BACKTEST_REFRESH_STATUS["result"]["message"] = DAILY_BACKTEST_REFRESH_STATUS["message"]
    except Exception as exc:  # pragma: no cover - background status guard
        DAILY_BACKTEST_REFRESH_STATUS["artifactStatus"] = "error"
        DAILY_BACKTEST_REFRESH_STATUS["message"] = f"ML artifact regeneration failed: {exc}"
        if isinstance(DAILY_BACKTEST_REFRESH_STATUS.get("result"), dict):
            DAILY_BACKTEST_REFRESH_STATUS["result"]["artifactStatus"] = "error"
            DAILY_BACKTEST_REFRESH_STATUS["result"]["artifactError"] = str(exc)


def read_jsonl_if_exists(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return read_jsonl(path)


def merge_candle_rows(
    base_rows: list[dict],
    fresh_rows: list[dict],
    *,
    start: str,
    end: str,
    symbol: str,
    timeframe: str,
    feed: str,
) -> list[dict]:
    start_dt = parse_utc_datetime(start)
    end_dt = parse_utc_datetime(end)
    merged: dict[str, dict] = {}
    for row in [*base_rows, *fresh_rows]:
        if str(row.get("symbol") or "").upper() != symbol:
            continue
        if str(row.get("timeframe") or "") != timeframe:
            continue
        if str(row.get("feed") or "").lower() != feed:
            continue
        timestamp = str(row.get("timestamp") or "")
        if not timestamp:
            continue
        timestamp_dt = parse_utc_datetime(timestamp)
        if start_dt <= timestamp_dt <= end_dt:
            merged[timestamp] = row
    return [merged[key] for key in sorted(merged)]


def write_train_test_datasets(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    candidate_manifest_path = cache_dir / f"candidate_dataset_v1_{symbol}_{start_date}_{end_date}_manifest.json"
    candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    candidate_rows = read_jsonl(Path(candidate_manifest["files"]["jsonl"]))
    labeled_rows = [row for row in candidate_rows if row.get("labelAvailable")]
    test_year = int(end_date[:4])
    train_rows = [row for row in labeled_rows if int(row.get("year") or 0) < test_year]
    test_rows = [row for row in labeled_rows if int(row.get("year") or 0) == test_year]
    if len(test_rows) < 20 and labeled_rows:
        cutoff = max(1, int(len(labeled_rows) * 0.8))
        train_rows = labeled_rows[:cutoff]
        test_rows = labeled_rows[cutoff:]
    paths = {
        "trainJsonl": write_jsonl(cache_dir / f"train_dataset_v1_{symbol}_{start_date}_{end_date}.jsonl", train_rows),
        "testJsonl": write_jsonl(cache_dir / f"test_dataset_v1_{symbol}_{start_date}_{end_date}.jsonl", test_rows),
        "trainCsv": write_csv(cache_dir / f"train_dataset_v1_{symbol}_{start_date}_{end_date}.csv", train_rows),
        "testCsv": write_csv(cache_dir / f"test_dataset_v1_{symbol}_{start_date}_{end_date}.csv", test_rows),
    }
    split_manifest = {
        "version": "train_test_dataset_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "startDate": start_date,
        "endDate": end_date,
        "labelSource": candidate_manifest["files"]["jsonl"],
        "splitPolicy": f"Train before {test_year}; test during {test_year}. Falls back to 80/20 if the test year has too few labels.",
        "trainRows": len(train_rows),
        "testRows": len(test_rows),
        "files": paths,
    }
    paths["manifest"] = write_json(cache_dir / f"train_test_dataset_v1_{symbol}_{start_date}_{end_date}_manifest.json", split_manifest)
    return {**split_manifest, "files": paths}


VOTING_ENSEMBLE_RISK_CONFIG = {
    "startingCapital": 25000.0,
    "riskPerTradePercent": 0.5,
    "maxDailyLossPercent": 2.0,
    "maxTradesPerDay": 3,
    "sessionStart": "09:35",
    "newTradesUntil": "15:30",
    "forceClose": "15:55",
    "execution": "next candle open",
    "stopLossPercent": 0.35,
    "fixedStopDistanceDollars": 1.0,
    "takeProfitR": 1.5,
    "slippagePerShare": 0.02,
    "expenseModel": {
        "description": "Estimated SPY share expenses: adverse slippage is priced into entry/exit, plus extra liquidity reserve and sell-side regulatory fee estimates.",
        "additionalLiquidityCostPerSharePerSide": 0.01,
        "commissionPerSharePerSide": 0.0,
        "secFeeRateOnSellNotional": 0.0000278,
        "finraTafPerSellShare": 0.000166,
        "finraTafMaxPerTrade": 8.30,
    },
    "positionSizing": "shares = risk dollars / stop distance, capped by available capital",
    "entryConfirmationBars": 3,
    "entryConfirmationBarsByTimeframe": {
        "1Min": 3,
        "5Min": 3,
        "1Hour": 2,
    },
    "warmupBarsByTimeframe": {
        "1Min": 50,
        "5Min": 20,
        "1Hour": 2,
        "1Day": 50,
        "1Week": 20,
    },
    "directionalWinnerMinVotesByTimeframe": {
        "1Hour": 2,
        "1Day": 3,
        "1Week": 3,
    },
    "signalFadeExit": "disabled",
    "allowedEntryHoursByTimeframe": {
        "1Min": ["10:00", "11:00"],
        "5Min": ["13:00", "14:00"],
        "1Hour": [],
    },
    "hybridOneHour": {
        "label": "1h filter + 5m execution",
        "directionTimeframe": "1Hour",
        "executionTimeframe": "5Min",
        "blockedDirectionHours": ["12:00", "14:00"],
        "blockedRegimes": ["VWAP Chop"],
        "requireDailyTrendAlignment": True,
        "allowedDailySignals": ["Buy"],
        "takeProfitR": 2.0,
        "atrPeriod": 14,
        "atrMultiplier": 0.75,
        "minDirectionalVotes": 2,
    },
    "swing": {
        "1Day": {
            "label": "Daily swing vote",
            "maxHoldingBars": 5,
            "stopPercent": 1.0,
            "atrPeriod": 14,
            "atrMultiplier": 1.5,
            "takeProfitR": 2.0,
        },
        "1Week": {
            "label": "Weekly swing vote",
            "maxHoldingBars": 8,
            "stopPercent": 2.0,
            "atrPeriod": 10,
            "atrMultiplier": 1.0,
            "takeProfitR": 2.5,
        },
    },
    "openCloseEvents": {
        "label": "Opening/Closing Event Ensemble",
        "weeklyFilter": "approved weekly vote",
        "openingWindow": "09:45-10:30",
        "closingWindow": "15:30-15:50",
        "openingRangeMinutes": 15,
        "closingStart": "15:30",
        "closingEnd": "15:50",
        "openingEnd": "10:30",
        "forceClose": "15:55",
        "takeProfitR": 1.5,
        "stopLossPercent": 0.35,
        "fixedStopDistanceDollars": 1.0,
        "maxTradesPerDay": 2,
        "minOpeningWeeklyDirectionalVotes": 3,
        "minClosingWeeklyDirectionalVotes": 4,
        "enableClosingEvents": True,
        "blockedRegimes": ["Mixed"],
    },
}


VOTING_STRATEGY_NAMES = [
    "Multi-Timeframe Trend Alignment",
    "First Pullback After Open",
    "Failed Breakout Strategy",
    "Liquidity Sweep Reversal",
    "Bollinger Band Reversion",
    "ATR Overextension Reversion",
    "Relative Strength vs QQQ/IWM",
    "Market Breadth Momentum",
    "Economic Event Reaction Strategy",
    "Ensemble Strategy Voting",
]


def dynamic_risk_config(settings_payload: dict) -> dict:
    config = deepcopy(VOTING_ENSEMBLE_RISK_CONFIG)
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}

    def number(name: str, default: float, *, minimum: float, maximum: float) -> float:
        try:
            value = float(settings_dict.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    config["startingCapital"] = number("startingCapital", 25000.0, minimum=1000.0, maximum=10_000_000.0)
    config["orderAllocationPercent"] = number("orderAllocationPercent", 10.0, minimum=0.1, maximum=100.0)
    config["dailyAllocationPercent"] = number("dailyAllocationPercent", 30.0, minimum=0.1, maximum=100.0)
    config["riskBudgetPercentOfOrder"] = number("riskBudgetPercentOfOrder", 50.0, minimum=0.1, maximum=100.0)
    config["riskPerTradePercent"] = number("riskPerTradePercent", 0.5, minimum=0.01, maximum=100.0)
    config["maxDailyLossPercent"] = number("maxDailyLossPercent", 2.0, minimum=0.1, maximum=100.0)
    requested_max_trades = int(number("maxTradesPerDay", 3, minimum=1, maximum=50))
    allocation_trade_cap = max(1, int(config["dailyAllocationPercent"] // max(config["orderAllocationPercent"], 0.1)))
    config["maxTradesPerDay"] = min(requested_max_trades, allocation_trade_cap)
    config["stopLossPercent"] = number("stopLossPercent", 0.35, minimum=0.01, maximum=20.0)
    config["fixedStopDistanceDollars"] = number("fixedStopDistanceDollars", 1.0, minimum=0.0, maximum=100.0)
    config["takeProfitR"] = number("takeProfitR", 1.5, minimum=0.1, maximum=20.0)
    config["slippagePerShare"] = number("slippagePerShare", 0.02, minimum=0.0, maximum=10.0)
    config["positionSizingMode"] = str(settings_dict.get("positionSizingMode") or "allocation")
    config["positionSizing"] = (
        "shares = per-order allocation dollars / entry price, with planned risk checked against order risk budget"
        if config["positionSizingMode"] == "allocation"
        else VOTING_ENSEMBLE_RISK_CONFIG["positionSizing"]
    )
    config.setdefault("openCloseEvents", {})
    config["openCloseEvents"]["maxTradesPerDay"] = min(int(config["openCloseEvents"].get("maxTradesPerDay", 2)), config["maxTradesPerDay"])
    config["openCloseEvents"].setdefault("fixedStopDistanceDollars", config["fixedStopDistanceDollars"])
    return config


def configured_stop_distance(config: dict, entry_price: float, override: dict | None = None, percent_key: str = "stopLossPercent") -> float:
    override = override or {}
    fixed_value = override.get("fixedStopDistanceDollars", config.get("fixedStopDistanceDollars", 0))
    try:
        fixed_distance = float(fixed_value)
    except (TypeError, ValueError):
        fixed_distance = 0.0
    if fixed_distance > 0:
        return fixed_distance
    percent_value = override.get(percent_key, config.get("stopLossPercent", 0.35))
    try:
        stop_percent = float(percent_value)
    except (TypeError, ValueError):
        stop_percent = float(config.get("stopLossPercent", 0.35))
    return entry_price * (stop_percent / 100)


def risk_config_hash(config: dict) -> str:
    serialized = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


def compact_backtest_result(result: dict, max_trades: int = 40) -> dict:
    trades = list(result.get("trades", []))
    return {
        key: value
        for key, value in {
            **result,
            "trades": trades[-max_trades:],
            "totalTrades": len(trades),
            "displayedTrades": min(len(trades), max_trades),
        }.items()
        if key != "diagnostics" or value
    }


def dynamic_backtest_results(replay_data: dict[str, list[dict]], risk_config: dict) -> dict[str, dict]:
    one_minute = replay_data.get("1Min", [])
    five_minute = replay_data.get("5Min", [])
    daily = replay_data.get("1Day", [])
    weekly = replay_data.get("1Week", [])
    return {
        "1Min": run_voting_ensemble_backtest(one_minute, timeframe="1Min", risk_config_override=risk_config),
        "5Min": run_voting_ensemble_backtest(five_minute, timeframe="5Min", risk_config_override=risk_config),
        "1Hour": run_one_hour_filter_backtest(
            replay_data.get("1HourExecution", []),
            replay_data.get("1HourDirection", []),
            risk_config_override=risk_config,
        ),
        "1Day": run_swing_voting_ensemble_backtest(daily, timeframe="1Day", risk_config_override=risk_config),
        "1Week": run_swing_voting_ensemble_backtest(weekly, timeframe="1Week", risk_config_override=risk_config),
        "Event": run_open_close_events_backtest(
            replay_data.get("EventIntraday", one_minute),
            replay_data.get("EventWeekly", weekly),
            risk_config_override=risk_config,
        ),
    }


def dynamic_ml_comparison(
    replay_data: dict[str, list[dict]],
    base_results: dict[str, dict],
    risk_config: dict,
    *,
    symbol: str,
) -> dict:
    rows = sorted(
        [
            ml_trade_row(timeframe, trade)
            for timeframe, result in base_results.items()
            for trade in result.get("trades", [])
            if trade.get("entryAt")
        ],
        key=lambda row: row["entryAt"],
    )
    rows = add_walk_forward_probabilities(rows)
    model_map = build_walk_forward_model_map(rows)
    comparison_rows = []
    best_by_timeframe = []
    for timeframe in ML_COMPARISON_TIMEFRAMES:
        timeframe_rows = [row for row in rows if row["timeframe"] == timeframe]
        base_metrics = ml_metric_row(timeframe, "Base", None, timeframe_rows, timeframe_rows, risk_config=risk_config)
        comparison_rows.append(base_metrics)
        threshold_metrics = []
        for threshold in ML_COMPARISON_THRESHOLDS:
            kept_rows = [row for row in timeframe_rows if float(row.get("mlProbability") or 0) >= threshold]
            metric = ml_metric_row(
                timeframe,
                f"ML >= {threshold:.2f}",
                threshold,
                kept_rows,
                timeframe_rows,
                risk_config=risk_config,
            )
            metric["pnlChange"] = round(float(metric["pnl"]) - float(base_metrics["pnl"]), 2)
            metric["drawdownChange"] = round(float(metric["maxDrawdown"]) - float(base_metrics["maxDrawdown"]), 2)
            metric["verdict"] = ml_verdict(base_metrics, metric)
            metric["replayMode"] = "dynamic_trade_filter"
            comparison_rows.append(metric)
            threshold_metrics.append(metric)
        best = (
            sorted(
                threshold_metrics,
                key=lambda row: (
                    1 if row["verdict"] == "Improved" else 0,
                    float(row["expectancy"]),
                    float(row["pnl"]),
                    -float(row["maxDrawdown"]),
                ),
                reverse=True,
            )[0]
            if threshold_metrics
            else None
        )
        best_by_timeframe.append(
            {
                "timeframe": timeframe,
                "basePnl": base_metrics["pnl"],
                "baseProfitFactor": base_metrics["profitFactor"],
                "baseMaxDrawdown": base_metrics["maxDrawdown"],
                "bestVariant": best["variant"] if best else "NA",
                "bestPnl": best["pnl"] if best else 0,
                "bestProfitFactor": best["profitFactor"] if best else None,
                "bestMaxDrawdown": best["maxDrawdown"] if best else 0,
                "verdict": best["verdict"] if best else "Inconclusive",
            }
        )
    return {
        "model": {
            "name": "Shared walk-forward logistic regression",
            "role": "Trade-quality filter using current Trading Settings",
            "trainingPolicy": "For each test year, train only on dynamic-config trades from earlier years.",
            "thresholds": ML_COMPARISON_THRESHOLDS,
            "rows": len(rows),
            "positiveRows": len([row for row in rows if row["target"] == 1]),
            "featureCount": len({feature for row in rows for feature in row.get("features", {})}),
            "note": "Dynamic replay uses submitted Trading Settings for sizing, exits, expenses, then applies the ML filter to the completed trade set so the interactive run can finish promptly.",
        },
        "rows": comparison_rows,
        "bestByTimeframe": best_by_timeframe,
    }

HISTORICAL_STRATEGY_FIT_CATALOG = [
    {
        "name": "Multi-Timeframe Trend Alignment",
        "tags": ["trend-up", "trend-down", "above-vwap", "below-vwap", "momentum"],
        "blocks": ["vwap-chop", "cash-filter", "low-volume"],
    },
    {
        "name": "First Pullback After Open",
        "tags": ["opening-range-active", "trend-up", "trend-down", "above-vwap", "below-vwap", "pullback"],
        "blocks": ["vwap-chop", "range-compression", "low-volume"],
    },
    {
        "name": "Failed Breakout Strategy",
        "tags": ["failed-breakout", "vwap-chop", "mean-reversion"],
        "blocks": ["trend-up", "trend-down", "volume-breakout"],
    },
    {
        "name": "Liquidity Sweep Reversal",
        "tags": ["liquidity-sweep", "mean-reversion", "volume-expansion"],
        "blocks": ["trend-up", "trend-down"],
    },
    {
        "name": "Bollinger Band Reversion",
        "tags": ["bollinger-overextension", "mean-reversion", "vwap-chop"],
        "blocks": ["trend-up", "trend-down", "volume-breakout"],
    },
    {
        "name": "ATR Overextension Reversion",
        "tags": ["atr-overextension", "mean-reversion", "volume-expansion"],
        "blocks": ["volume-breakout"],
    },
    {
        "name": "Relative Strength vs QQQ/IWM",
        "tags": ["relative-strength-up", "relative-strength-down", "momentum"],
        "blocks": ["vwap-chop", "cash-filter"],
    },
    {
        "name": "Market Breadth Momentum",
        "tags": ["breadth-up", "breadth-down", "momentum", "volume-expansion"],
        "blocks": ["vwap-chop", "cash-filter"],
    },
    {
        "name": "Economic Event Reaction Strategy",
        "tags": ["event-reaction", "gap-up", "gap-down", "opening-range-active", "volume-expansion"],
        "blocks": ["range-compression", "low-volume"],
    },
    {
        "name": "Ensemble Strategy Voting",
        "tags": ["momentum", "event-reaction", "mean-reversion", "volume-expansion"],
        "blocks": ["cash-filter"],
    },
]

ML_COMPARISON_THRESHOLDS = [0.55, 0.58, 0.60, 0.63, 0.65]
ML_COMPARISON_TIMEFRAMES = ["1Min", "5Min", "1Hour", "1Day", "1Week", "Event"]
DAILY_REFINEMENT_THRESHOLDS = [0.62, 0.63, 0.64, 0.65, 0.66, 0.67, 0.68, 0.69, 0.70]
EVENT_REFINEMENT_THRESHOLDS = [0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70]
WEEKLY_RISK_PERCENTS = [0.25, 0.35, 0.50]
WEEKLY_ATR_MULTIPLIERS = [0.75, 1.00, 1.25, 1.50]
WEEKLY_TAKE_PROFIT_R = [2.00, 2.50, 3.00]
WEEKLY_HOLDING_BARS = [6, 8, 10]
WEEKLY_DRAWDOWN_STOPS = [0.0, 4.0, 6.0]


def cached_voting_ensemble_backtest(*, data_path: Path, manifest: dict, timeframe: str, start_date: str, end_date: str) -> dict:
    cache_path = data_path.parent / f"voting_ensemble_risk_v16_{timeframe}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    start = parse_backtest_start_datetime(start_date)
    end = parse_backtest_end_datetime(end_date)
    candles = [
        candle
        for candle in read_jsonl(data_path)
        if start <= parse_utc_datetime(str(candle["timestamp"])) <= end
    ]
    if timeframe == "1Hour":
        execution_candles = aggregate_candles(candles, timeframe="5Min", minutes=5)
        direction_candles = aggregate_candles(candles, timeframe="1Hour", minutes=60)
        result = run_one_hour_filter_backtest(execution_candles, direction_candles)
    elif timeframe == "1Week":
        candles = aggregate_weekly_candles(candles)
        result = run_swing_voting_ensemble_backtest(candles, timeframe=timeframe)
    elif timeframe == "1Day":
        result = run_swing_voting_ensemble_backtest(candles, timeframe=timeframe)
    else:
        result = run_voting_ensemble_backtest(candles, timeframe=timeframe)
    result["sourceManifest"] = manifest.get("manifest") or str(data_path.parent / "manifest.json")
    result["startDate"] = start_date
    result["endDate"] = end_date
    result["timeframe"] = timeframe
    result["rangeLabel"] = f"{start_date} to {end_date}"
    result["cachedAt"] = datetime.now(UTC).isoformat()
    write_json(cache_path, result)
    return result


def cached_ml_comparison(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"ml_comparison_v2_{symbol}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    rows = sorted(
        [
            ml_trade_row(timeframe, trade)
            for timeframe, result in base_results.items()
            for trade in result.get("trades", [])
            if trade.get("entryAt")
        ],
        key=lambda row: row["entryAt"],
    )
    rows = add_walk_forward_probabilities(rows)
    model_map = build_walk_forward_model_map(rows)
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)

    comparison_rows = []
    best_by_timeframe = []
    for timeframe in ML_COMPARISON_TIMEFRAMES:
        timeframe_rows = [row for row in rows if row["timeframe"] == timeframe]
        base_metrics = ml_metric_row(timeframe, "Base", None, timeframe_rows, timeframe_rows)
        comparison_rows.append(base_metrics)
        threshold_metrics = []
        for threshold in ML_COMPARISON_THRESHOLDS:
            replay_result = run_ml_replay_result(
                replay_data=replay_data,
                timeframe=timeframe,
                ml_filter={"threshold": threshold, "modelsByYear": model_map},
            )
            metric = ml_result_metric_row(timeframe, f"ML >= {threshold:.2f}", threshold, replay_result, base_metrics)
            metric["pnlChange"] = round(float(metric["pnl"]) - float(base_metrics["pnl"]), 2)
            metric["drawdownChange"] = round(float(metric["maxDrawdown"]) - float(base_metrics["maxDrawdown"]), 2)
            metric["profitFactorChange"] = (
                round(float(metric["profitFactor"]) - float(base_metrics["profitFactor"]), 2)
                if metric["profitFactor"] is not None and base_metrics["profitFactor"] is not None
                else None
            )
            metric["verdict"] = ml_verdict(base_metrics, metric)
            comparison_rows.append(metric)
            threshold_metrics.append(metric)
        best = (
            sorted(
                threshold_metrics,
                key=lambda row: (
                    1 if row["verdict"] == "Improved" else 0,
                    float(row["expectancy"]),
                    float(row["pnl"]),
                    -float(row["maxDrawdown"]),
                ),
                reverse=True,
            )[0]
            if threshold_metrics
            else None
        )
        best_by_timeframe.append(
            {
                "timeframe": timeframe,
                "basePnl": base_metrics["pnl"],
                "baseProfitFactor": base_metrics["profitFactor"],
                "baseMaxDrawdown": base_metrics["maxDrawdown"],
                "bestVariant": best["variant"] if best else "NA",
                "bestPnl": best["pnl"] if best else 0,
                "bestProfitFactor": best["profitFactor"] if best else None,
                "bestMaxDrawdown": best["maxDrawdown"] if best else 0,
                "verdict": best["verdict"] if best else "Inconclusive",
            }
        )

    result = {
        "model": {
            "name": "Shared walk-forward logistic regression",
            "role": "Trade-quality filter for existing Voting Ensemble candidate trades",
            "trainingPolicy": "For each test year, train only on trades from earlier years.",
            "thresholds": ML_COMPARISON_THRESHOLDS,
            "featureCount": len({feature for row in rows for feature in row.get("features", {})}),
            "rows": len(rows),
            "positiveRows": len([row for row in rows if row["target"] == 1]),
            "note": "ML rows are full in-loop replays: skipped candidates do not affect equity, position sizing, daily loss, or trade limits.",
        },
        "rows": comparison_rows,
        "bestByTimeframe": best_by_timeframe,
    }
    write_json(cache_path, result)
    return result


def cached_candidate_dataset(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    jsonl_path = cache_dir / f"candidate_dataset_v1_{symbol}_{start_date}_{end_date}.jsonl"
    csv_path = cache_dir / f"candidate_dataset_v1_{symbol}_{start_date}_{end_date}.csv"
    manifest_path = cache_dir / f"candidate_dataset_v1_{symbol}_{start_date}_{end_date}_manifest.json"
    if jsonl_path.exists() and csv_path.exists() and manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    ml_comparison = cached_ml_comparison(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)
    best_thresholds = {
        str(row.get("timeframe")): parse_ml_variant_threshold(str(row.get("bestVariant") or ""))
        for row in ml_comparison.get("bestByTimeframe", [])
    }
    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    base_rows = sorted(
        [
            ml_trade_row(timeframe, trade)
            for timeframe, result in base_results.items()
            for trade in result.get("trades", [])
            if trade.get("entryAt")
        ],
        key=lambda row: row["entryAt"],
    )
    model_map = build_walk_forward_model_map(add_walk_forward_probabilities(base_rows))
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)

    rows: list[dict] = []
    for timeframe in ML_COMPARISON_TIMEFRAMES:
        base_result = base_results.get(timeframe, {"trades": []})
        for trade in base_result.get("trades", []):
            rows.append(candidate_export_row(symbol, timeframe, trade, row_type="outcome", source="base", decision="taken_base"))

        threshold = best_thresholds.get(timeframe)
        if threshold is None:
            continue
        candidate_collector: list[dict] = []
        replay_result = run_ml_replay_result(
            replay_data=replay_data,
            timeframe=timeframe,
            ml_filter={
                "threshold": threshold,
                "modelsByYear": model_map,
                "symbol": symbol,
                "candidateCollector": candidate_collector,
            },
        )
        rows.extend(candidate_collector)
        for trade in replay_result.get("trades", []):
            rows.append(candidate_export_row(symbol, timeframe, trade, row_type="outcome", source="ml_replay", decision="taken_ml"))

    for index, row in enumerate(sorted(rows, key=lambda item: (str(item.get("entryAt") or ""), str(item.get("timeframe") or ""), str(item.get("rowType") or ""))), start=1):
        row["rowId"] = index
    rows = sorted(rows, key=lambda item: int(item["rowId"]))

    files = {
        "jsonl": write_jsonl(jsonl_path, rows),
        "csv": write_csv(csv_path, rows),
    }
    result = {
        "version": "candidate_dataset_v1",
        "description": "Voting Ensemble candidate and outcome export for model training. Candidate rows include ML-scored setups, including skipped setups; outcome rows include realized trade labels and expenses.",
        "symbol": symbol,
        "startDate": start_date,
        "endDate": end_date,
        "createdAt": datetime.now(UTC).isoformat(),
        "rows": len(rows),
        "candidateRows": len([row for row in rows if row.get("rowType") == "candidate"]),
        "outcomeRows": len([row for row in rows if row.get("rowType") == "outcome"]),
        "labeledRows": len([row for row in rows if row.get("labelAvailable")]),
        "skippedRows": len([row for row in rows if row.get("decision") == "skipped_ml"]),
        "timeframes": candidate_dataset_timeframe_summary(rows),
        "bestThresholds": {key: value for key, value in best_thresholds.items() if value is not None},
        "files": {**files, "manifest": str(manifest_path)},
        "sample": rows[:8],
        "sourceManifest": manifest.get("manifest") or str(cache_dir / "manifest.json"),
    }
    write_json(manifest_path, result)
    return result


def cached_ml_diagnostics(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"ml_diagnostics_v1_{symbol}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    rows = sorted(
        [
            ml_trade_row(timeframe, trade)
            for timeframe, result in base_results.items()
            for trade in result.get("trades", [])
            if trade.get("entryAt")
        ],
        key=lambda row: row["entryAt"],
    )
    rows = add_walk_forward_probabilities(rows)
    model_map = build_walk_forward_model_map(rows)
    comparison = cached_ml_comparison(manifest=manifest, symbol=symbol, start_date=start_date, end_date=end_date)

    result = {
        "version": "ml_diagnostics_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "model": {
            "name": "Shared walk-forward logistic regression",
            "trainingRows": len(rows),
            "positiveRows": len([row for row in rows if int(row.get("target") or 0) == 1]),
            "featureCount": len({feature for row in rows for feature in row.get("features", {})}),
        },
        "featureWeights": ml_feature_weight_summary(model_map),
        "featureEdges": ml_feature_edge_summary(rows),
        "timeframeGuidance": ml_timeframe_guidance(rows, comparison),
        "recommendations": ml_diagnostic_recommendations(comparison),
        "sourceArtifacts": {
            "mlComparison": str(cache_dir / f"ml_comparison_v2_{symbol}_{start_date}_{end_date}.json"),
            "candidateDatasetManifest": str(cache_dir / f"candidate_dataset_v1_{symbol}_{start_date}_{end_date}_manifest.json"),
        },
    }
    write_json(cache_path, result)
    return result


def cached_daily_refinement(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"daily_refinement_v1_{symbol}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    base_result = base_results.get("1Day", {"trades": []})
    base_metrics = ml_trade_metrics(list(base_result.get("trades", [])))
    training_rows = sorted(
        [
            ml_trade_row(timeframe, trade)
            for timeframe, result in base_results.items()
            for trade in result.get("trades", [])
            if trade.get("entryAt")
        ],
        key=lambda row: row["entryAt"],
    )
    model_map = build_walk_forward_model_map(add_walk_forward_probabilities(training_rows))
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)

    variants = []
    for threshold in DAILY_REFINEMENT_THRESHOLDS:
        replay_result = run_ml_replay_result(
            replay_data=replay_data,
            timeframe="1Day",
            ml_filter={"threshold": threshold, "modelsByYear": model_map},
        )
        metric = ml_result_metric_row("1Day", f"Daily ML >= {threshold:.2f}", threshold, replay_result, base_metrics)
        metric["pnlChange"] = round(float(metric["pnl"]) - float(base_metrics["pnl"]), 2)
        metric["drawdownChange"] = round(float(metric["maxDrawdown"]) - float(base_metrics["maxDrawdown"]), 2)
        metric["profitFactorChange"] = (
            round(float(metric["profitFactor"]) - float(base_metrics["profitFactor"]), 2)
            if metric["profitFactor"] is not None and base_metrics["profitFactor"] is not None
            else None
        )
        metric["verdict"] = ml_verdict(base_metrics, metric)
        variants.append(metric)

    best = sorted(
        variants,
        key=lambda row: (
            1 if row.get("verdict") == "Improved" else 0,
            float(row.get("expectancy") or 0),
            float(row.get("pnl") or 0),
            -float(row.get("maxDrawdown") or 0),
        ),
        reverse=True,
    )[0] if variants else None
    result = {
        "version": "daily_refinement_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "goal": "Focused Daily ML threshold refinement around the observed 0.62-0.70 edge zone.",
        "timeframe": "1Day",
        "base": {"variant": "Base", **base_metrics},
        "thresholds": DAILY_REFINEMENT_THRESHOLDS,
        "variants": variants,
        "best": best,
        "recommendation": daily_refinement_recommendation(base_metrics, best),
        "notes": [
            "This is a threshold-only refinement using the existing risk model.",
            "Risk tuning should be tested after selecting a stable threshold because changing risk first can hide signal quality.",
        ],
    }
    write_json(cache_path, result)
    return result


def daily_refinement_recommendation(base_metrics: dict, best: dict | None) -> str:
    if not best:
        return "No Daily refinement variant produced a usable result."
    if best.get("verdict") == "Improved" and float(best.get("pnl") or 0) > float(base_metrics.get("pnl") or 0):
        return (
            f"Use {best.get('variant')} as the Daily paper-test candidate. "
            f"It improved P/L to {best.get('pnl')} with PF {best.get('profitFactor')} and max drawdown {best.get('maxDrawdown')}."
        )
    return "Keep Daily baseline as control; no tested threshold clearly improved the risk-adjusted result."


def cached_event_refinement(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"event_refinement_v1_{symbol}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    base_result = base_results.get("Event", {"trades": []})
    event_trades = list(base_result.get("trades", []))
    base_metrics = ml_trade_metrics(event_trades)
    event_rows = sorted(
        [ml_trade_row("Event", trade) for trade in event_trades if trade.get("entryAt")],
        key=lambda row: row["entryAt"],
    )
    model_map = build_walk_forward_model_map(add_walk_forward_probabilities(event_rows))
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)

    variants = []
    for threshold in EVENT_REFINEMENT_THRESHOLDS:
        replay_result = run_ml_replay_result(
            replay_data=replay_data,
            timeframe="Event",
            ml_filter={"threshold": threshold, "modelsByYear": model_map},
        )
        metric = ml_result_metric_row("Event", f"Event ML >= {threshold:.2f}", threshold, replay_result, base_metrics)
        metric["pnlChange"] = round(float(metric["pnl"]) - float(base_metrics["pnl"]), 2)
        metric["drawdownChange"] = round(float(metric["maxDrawdown"]) - float(base_metrics["maxDrawdown"]), 2)
        metric["profitFactorChange"] = (
            round(float(metric["profitFactor"]) - float(base_metrics["profitFactor"]), 2)
            if metric["profitFactor"] is not None and base_metrics["profitFactor"] is not None
            else None
        )
        metric["verdict"] = ml_verdict(base_metrics, metric)
        variants.append(metric)

    quality_best = event_quality_best_variant(variants)
    profit_best = event_profit_preserving_variant(variants, base_metrics)
    result = {
        "version": "event_refinement_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "goal": "Event-specific ML model trained only on opening/closing Event trades, tested for quality and profit preservation.",
        "timeframe": "Event",
        "model": {
            "name": "Event-only walk-forward logistic regression",
            "trainingRows": len(event_rows),
            "positiveRows": len([row for row in event_rows if int(row.get("target") or 0) == 1]),
            "featureCount": len({feature for row in event_rows for feature in row.get("features", {})}),
        },
        "base": {"variant": "Raw Event baseline", **base_metrics},
        "thresholds": EVENT_REFINEMENT_THRESHOLDS,
        "variants": variants,
        "qualityBest": quality_best,
        "profitPreservingBest": profit_best,
        "featureWeights": ml_feature_weight_summary(model_map),
        "featureEdges": ml_feature_edge_summary(event_rows),
        "recommendation": event_refinement_recommendation(base_metrics, quality_best, profit_best),
        "notes": [
            "This model is separate from the shared ML filter and only trains on Event outcomes.",
            "Profit-preserving selection prioritizes keeping Event's raw P/L edge while avoiding clearly worse PF/drawdown.",
        ],
    }
    write_json(cache_path, result)
    return result


def event_quality_best_variant(variants: list[dict]) -> dict | None:
    return (
        sorted(
            variants,
            key=lambda row: (
                float(row.get("expectancy") or 0),
                float(row.get("profitFactor") or 0),
                float(row.get("pnl") or 0),
                -float(row.get("maxDrawdown") or 0),
            ),
            reverse=True,
        )[0]
        if variants
        else None
    )


def event_profit_preserving_variant(variants: list[dict], base_metrics: dict) -> dict | None:
    base_pnl = float(base_metrics.get("pnl") or 0)
    base_pf = float(base_metrics.get("profitFactor") or 0)
    base_dd = float(base_metrics.get("maxDrawdown") or 0)
    candidates = [
        row
        for row in variants
        if float(row.get("pnl") or 0) >= base_pnl * 0.85
        and float(row.get("profitFactor") or 0) >= base_pf
        and float(row.get("maxDrawdown") or 0) <= base_dd * 1.15
    ]
    if not candidates:
        candidates = variants
    return (
        sorted(
            candidates,
            key=lambda row: (
                float(row.get("pnl") or 0),
                float(row.get("profitFactor") or 0),
                -float(row.get("maxDrawdown") or 0),
            ),
            reverse=True,
        )[0]
        if candidates
        else None
    )


def event_refinement_recommendation(base_metrics: dict, quality_best: dict | None, profit_best: dict | None) -> str:
    if not quality_best and not profit_best:
        return "No Event-specific ML threshold produced a usable result."
    base_pnl = float(base_metrics.get("pnl") or 0)
    profit_pnl = float((profit_best or {}).get("pnl") or 0)
    quality_pnl = float((quality_best or {}).get("pnl") or 0)
    if profit_best and profit_pnl >= base_pnl:
        return (
            f"Use {profit_best.get('variant')} as the Event paper-test candidate; it preserved or improved raw Event P/L "
            f"with PF {profit_best.get('profitFactor')} and drawdown {profit_best.get('maxDrawdown')}."
        )
    if quality_best and quality_pnl >= base_pnl * 0.75:
        return (
            f"Use {quality_best.get('variant')} only as a quality filter candidate. "
            f"It improves selectivity but still needs paper-test comparison against the raw Event baseline."
        )
    return "Keep raw Event baseline as primary; Event-specific ML did not preserve enough profit in this threshold sweep."


def cached_weekly_risk_tuning(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"weekly_risk_tuning_v1_{symbol}_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    base_results = load_ml_base_results(manifest=manifest, start_date=start_date, end_date=end_date)
    base_result = base_results.get("1Week", {"trades": []})
    base_metrics = weekly_risk_metric_row("Baseline weekly", base_result, weekly_variant_settings(VOTING_ENSEMBLE_RISK_CONFIG, "1Week"))
    replay_data = prepare_ml_replay_data(manifest=manifest, start_date=start_date, end_date=end_date)
    weekly_candles = replay_data.get("1Week", [])

    variants = []
    for risk_percent in WEEKLY_RISK_PERCENTS:
        for atr_multiplier in WEEKLY_ATR_MULTIPLIERS:
            for take_profit_r in WEEKLY_TAKE_PROFIT_R:
                for holding_bars in WEEKLY_HOLDING_BARS:
                    for drawdown_stop in WEEKLY_DRAWDOWN_STOPS:
                        variant_config = weekly_risk_variant_config(
                            risk_percent=risk_percent,
                            atr_multiplier=atr_multiplier,
                            take_profit_r=take_profit_r,
                            max_holding_bars=holding_bars,
                            max_drawdown_stop=drawdown_stop,
                        )
                        label = weekly_variant_label(variant_config)
                        result = run_swing_voting_ensemble_backtest(
                            weekly_candles,
                            timeframe="1Week",
                            risk_config_override=variant_config,
                        )
                        metric = weekly_risk_metric_row(label, result, weekly_variant_settings(variant_config, "1Week"))
                        metric["pnlChange"] = round(float(metric["pnl"]) - float(base_metrics["pnl"]), 2)
                        metric["drawdownChange"] = round(float(metric["maxDrawdown"]) - float(base_metrics["maxDrawdown"]), 2)
                        metric["returnToDrawdown"] = round(float(metric["pnl"]) / float(metric["maxDrawdown"]), 3) if float(metric["maxDrawdown"] or 0) else None
                        metric["capitalEfficiency"] = round(float(metric["returnPercent"]) / max(0.01, float(metric["maxDrawdownPercent"])), 3)
                        variants.append(metric)

    best_profit = sorted(variants, key=lambda row: (float(row["pnl"]), float(row["profitFactor"] or 0), -float(row["maxDrawdown"])), reverse=True)[0] if variants else None
    best_risk_adjusted = weekly_best_risk_adjusted(variants)
    low_drawdown = weekly_best_low_drawdown(variants, base_metrics)
    result = {
        "version": "weekly_risk_tuning_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "goal": "Weekly risk tuning with the same weekly vote signal; only sizing, ATR stop, take-profit, holding period, and drawdown stop change.",
        "timeframe": "1Week",
        "base": base_metrics,
        "testedVariants": len(variants),
        "searchSpace": {
            "riskPercents": WEEKLY_RISK_PERCENTS,
            "atrMultipliers": WEEKLY_ATR_MULTIPLIERS,
            "takeProfitR": WEEKLY_TAKE_PROFIT_R,
            "holdingBars": WEEKLY_HOLDING_BARS,
            "drawdownStops": WEEKLY_DRAWDOWN_STOPS,
        },
        "bestProfit": best_profit,
        "bestRiskAdjusted": best_risk_adjusted,
        "bestLowDrawdown": low_drawdown,
        "topVariants": sorted(
            variants,
            key=lambda row: (float(row.get("capitalEfficiency") or 0), float(row["pnl"]), -float(row["maxDrawdown"])),
            reverse=True,
        )[:20],
        "recommendation": weekly_risk_recommendation(base_metrics, best_risk_adjusted, best_profit, low_drawdown),
        "notes": [
            "Signal logic is unchanged from the weekly Voting Ensemble.",
            "Drawdown stop locks new entries after peak-to-equity drawdown reaches the tested percent.",
            "All results include the existing expense model.",
        ],
    }
    write_json(cache_path, result)
    return result


def weekly_risk_variant_config(
    *,
    risk_percent: float,
    atr_multiplier: float,
    take_profit_r: float,
    max_holding_bars: int,
    max_drawdown_stop: float,
) -> dict:
    config = deepcopy(VOTING_ENSEMBLE_RISK_CONFIG)
    config["riskPerTradePercent"] = risk_percent
    config["swing"]["1Week"] = {
        **config.get("swing", {}).get("1Week", {}),
        "atrMultiplier": atr_multiplier,
        "takeProfitR": take_profit_r,
        "maxHoldingBars": max_holding_bars,
    }
    if max_drawdown_stop:
        config["swing"]["1Week"]["maxDrawdownStopPercent"] = max_drawdown_stop
    else:
        config["swing"]["1Week"].pop("maxDrawdownStopPercent", None)
    return config


def weekly_variant_settings(config: dict, timeframe: str) -> dict:
    swing = dict(config.get("swing", {}).get(timeframe, {}))
    return {
        "riskPercent": float(config.get("riskPerTradePercent") or 0),
        "atrMultiplier": float(swing.get("atrMultiplier") or 0),
        "takeProfitR": float(swing.get("takeProfitR") or 0),
        "maxHoldingBars": int(swing.get("maxHoldingBars") or 0),
        "maxDrawdownStopPercent": float(swing.get("maxDrawdownStopPercent") or 0),
    }


def weekly_variant_label(config: dict) -> str:
    settings = weekly_variant_settings(config, "1Week")
    dd_label = f", DD stop {settings['maxDrawdownStopPercent']:.0f}%" if settings["maxDrawdownStopPercent"] else ""
    return (
        f"Risk {settings['riskPercent']:.2f}%, ATR x{settings['atrMultiplier']:.2f}, "
        f"{settings['takeProfitR']:.1f}R, hold {settings['maxHoldingBars']}w{dd_label}"
    )


def weekly_risk_metric_row(label: str, result: dict, settings: dict) -> dict:
    trades = list(result.get("trades", []))
    metrics = ml_trade_metrics(trades)
    return {
        "variant": label,
        "settings": settings,
        **metrics,
        "bars": result.get("bars"),
        "sessions": result.get("sessions"),
    }


def weekly_best_risk_adjusted(variants: list[dict]) -> dict | None:
    usable = [row for row in variants if int(row.get("trades") or 0) >= 10 and float(row.get("pnl") or 0) > 0]
    return (
        sorted(
            usable or variants,
            key=lambda row: (
                float(row.get("capitalEfficiency") or 0),
                float(row.get("profitFactor") or 0),
                float(row.get("pnl") or 0),
                -float(row.get("maxDrawdown") or 0),
            ),
            reverse=True,
        )[0]
        if variants
        else None
    )


def weekly_best_low_drawdown(variants: list[dict], base_metrics: dict) -> dict | None:
    minimum_pnl = max(0, float(base_metrics.get("pnl") or 0) * 0.50)
    usable = [row for row in variants if float(row.get("pnl") or 0) >= minimum_pnl and int(row.get("trades") or 0) >= 10]
    return (
        sorted(
            usable or variants,
            key=lambda row: (
                -float(row.get("maxDrawdown") or 0),
                float(row.get("pnl") or 0),
                float(row.get("profitFactor") or 0),
            ),
            reverse=True,
        )[0]
        if variants
        else None
    )


def weekly_risk_recommendation(base: dict, risk_adjusted: dict | None, profit_best: dict | None, low_drawdown: dict | None) -> str:
    if not risk_adjusted:
        return "No weekly risk variant produced a usable result."
    base_pnl = float(base.get("pnl") or 0)
    base_dd = float(base.get("maxDrawdown") or 0)
    tuned_pnl = float(risk_adjusted.get("pnl") or 0)
    tuned_dd = float(risk_adjusted.get("maxDrawdown") or 0)
    if tuned_pnl >= base_pnl * 0.80 and tuned_dd < base_dd:
        return (
            f"Use {risk_adjusted.get('variant')} as the weekly risk-adjusted paper-test candidate. "
            f"It keeps most of the weekly profit while lowering drawdown."
        )
    if profit_best and float(profit_best.get("pnl") or 0) > base_pnl:
        return (
            f"Use {profit_best.get('variant')} only if maximizing weekly profit is the priority; "
            f"compare drawdown carefully against the baseline."
        )
    if low_drawdown:
        return f"Use {low_drawdown.get('variant')} for the conservative weekly paper-test candidate."
    return "Keep the current weekly baseline; tested risk variants did not improve enough."


def ml_feature_weight_summary(model_map: dict[int, dict]) -> dict:
    weights_by_feature: dict[str, list[float]] = defaultdict(list)
    for model in model_map.values():
        for feature, weight in dict(model.get("weights") or {}).items():
            weights_by_feature[feature].append(float(weight))
    rows = [
        {
            "feature": feature,
            "avgWeight": round(sum(values) / len(values), 5),
            "avgAbsWeight": round(sum(abs(value) for value in values) / len(values), 5),
            "years": len(values),
        }
        for feature, values in weights_by_feature.items()
        if values
    ]
    return {
        "topPositive": sorted(rows, key=lambda row: row["avgWeight"], reverse=True)[:12],
        "topNegative": sorted(rows, key=lambda row: row["avgWeight"])[:12],
        "topMagnitude": sorted(rows, key=lambda row: row["avgAbsWeight"], reverse=True)[:12],
    }


def ml_feature_edge_summary(rows: list[dict]) -> dict:
    stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "r": 0.0})
    for row in rows:
        trade = row.get("trade", {})
        pnl = float(trade.get("pnl") or 0)
        r_multiple = float(trade.get("rMultiple") or 0)
        target = int(row.get("target") or 0)
        for feature, value in row.get("features", {}).items():
            if not value or feature == "bias":
                continue
            bucket = stats[feature]
            bucket["count"] += 1
            bucket["wins"] += target
            bucket["pnl"] += pnl
            bucket["r"] += r_multiple
    edge_rows = []
    for feature, bucket in stats.items():
        count = int(bucket["count"])
        if count < 10:
            continue
        edge_rows.append(
            {
                "feature": feature,
                "trades": count,
                "winRate": round((float(bucket["wins"]) / count) * 100, 1),
                "pnl": round(float(bucket["pnl"]), 2),
                "expectancy": round(float(bucket["pnl"]) / count, 2),
                "averageR": round(float(bucket["r"]) / count, 3),
            }
        )
    return {
        "bestExpectancy": sorted(edge_rows, key=lambda row: (row["expectancy"], row["trades"]), reverse=True)[:12],
        "worstExpectancy": sorted(edge_rows, key=lambda row: (row["expectancy"], -row["trades"]))[:12],
        "bestWinRate": sorted(edge_rows, key=lambda row: (row["winRate"], row["trades"]), reverse=True)[:12],
    }


def ml_timeframe_guidance(rows: list[dict], comparison: dict) -> list[dict]:
    by_timeframe = {timeframe: [row for row in rows if row.get("timeframe") == timeframe] for timeframe in ML_COMPARISON_TIMEFRAMES}
    best_by_timeframe = {str(row.get("timeframe")): row for row in comparison.get("bestByTimeframe", [])}
    guidance = []
    for timeframe in ML_COMPARISON_TIMEFRAMES:
        timeframe_rows = by_timeframe.get(timeframe, [])
        metrics = ml_trade_metrics([row["trade"] for row in timeframe_rows])
        best = best_by_timeframe.get(timeframe, {})
        verdict = str(best.get("verdict") or "Inconclusive")
        if verdict == "Improved" and int(best.get("bestPnl") or 0) > 0:
            action = "Test ML filter live-sim only"
        elif timeframe in {"1Min", "1Hour"}:
            action = "Keep disabled until more labeled candidates exist"
        else:
            action = "Keep baseline as control"
        guidance.append(
            {
                "timeframe": timeframe,
                "labeledTrades": len(timeframe_rows),
                "basePnl": metrics["pnl"],
                "baseProfitFactor": metrics["profitFactor"],
                "baseExpectancy": metrics["expectancy"],
                "bestVariant": best.get("bestVariant") or "NA",
                "bestPnl": best.get("bestPnl") or 0,
                "bestProfitFactor": best.get("bestProfitFactor"),
                "bestMaxDrawdown": best.get("bestMaxDrawdown") or 0,
                "verdict": verdict,
                "action": action,
            }
        )
    return guidance


def ml_diagnostic_recommendations(comparison: dict) -> list[str]:
    best = {str(row.get("timeframe")): row for row in comparison.get("bestByTimeframe", [])}
    recommendations = [
        "Do not use the ML filter on 1m until the candidate labels improve; the current filter made 1m worse.",
        "Use 5m, daily, weekly, and event ML variants only in paper/live-sim first because they improved quality but changed trade count.",
        "Prioritize more labeled outcomes for skipped candidates, especially event setups, before replacing the rule engine.",
    ]
    if float(best.get("Event", {}).get("bestPnl") or 0) < 9000:
        recommendations.append("Keep the raw Event strategy as the profit benchmark because ML reduced its total P/L even while improving profit factor.")
    if float(best.get("1Day", {}).get("bestPnl") or 0) > 0:
        recommendations.append("Daily is the cleanest ML improvement candidate: positive P/L, improved profit factor, and reduced drawdown versus baseline.")
    return recommendations


def cached_trading_rag_corpus(*, manifest: dict, symbol: str, start_date: str, end_date: str) -> dict:
    cache_dir = backtest_cache_dir(manifest)
    cache_path = cache_dir / f"trading_rag_corpus_v1_{symbol}_{start_date}_{end_date}.json"
    source_files = trading_rag_source_files(cache_dir, symbol, start_date, end_date)
    existing_mtime = cache_path.stat().st_mtime if cache_path.exists() else 0
    source_mtime = max((path.stat().st_mtime for path in source_files if path.exists()), default=0)
    if cache_path.exists() and existing_mtime >= source_mtime:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    documents = []
    for timeframe, filename in [
        ("1Min", f"voting_ensemble_risk_v16_1Min_{start_date}_{end_date}.json"),
        ("5Min", f"voting_ensemble_risk_v16_5Min_{start_date}_{end_date}.json"),
        ("1Hour", f"voting_ensemble_risk_v16_1Hour_{start_date}_{end_date}.json"),
        ("1Day", f"voting_ensemble_risk_v16_1Day_{start_date}_{end_date}.json"),
        ("1Week", f"voting_ensemble_risk_v16_1Week_{start_date}_{end_date}.json"),
        ("Event", f"open_close_events_v5_{start_date}_{end_date}.json"),
    ]:
        path = cache_dir / filename
        if path.exists():
            documents.extend(backtest_result_rag_documents(timeframe, json.loads(path.read_text(encoding="utf-8")), path))

    for label, filename in [
        ("Daily refinement", f"daily_refinement_v1_{symbol}_{start_date}_{end_date}.json"),
        ("Event refinement", f"event_refinement_v1_{symbol}_{start_date}_{end_date}.json"),
        ("Weekly risk tuning", f"weekly_risk_tuning_v1_{symbol}_{start_date}_{end_date}.json"),
        ("ML comparison", f"ml_comparison_v2_{symbol}_{start_date}_{end_date}.json"),
        ("ML diagnostics", f"ml_diagnostics_v1_{symbol}_{start_date}_{end_date}.json"),
    ]:
        path = cache_dir / filename
        if path.exists():
            documents.append(generic_artifact_rag_document(label, json.loads(path.read_text(encoding="utf-8")), path))

    corpus = {
        "version": "trading_rag_corpus_v1",
        "symbol": symbol,
        "createdAt": datetime.now(UTC).isoformat(),
        "range": {"startDate": start_date, "endDate": end_date},
        "sourceManifest": manifest.get("manifest"),
        "documents": documents,
    }
    corpus["path"] = write_json(cache_path, corpus)
    return corpus


def trading_rag_source_files(cache_dir: Path, symbol: str, start_date: str, end_date: str) -> list[Path]:
    return [
        cache_dir / f"voting_ensemble_risk_v16_{timeframe}_{start_date}_{end_date}.json"
        for timeframe in ["1Min", "5Min", "1Hour", "1Day", "1Week"]
    ] + [
        cache_dir / f"open_close_events_v5_{start_date}_{end_date}.json",
        cache_dir / f"daily_refinement_v1_{symbol}_{start_date}_{end_date}.json",
        cache_dir / f"event_refinement_v1_{symbol}_{start_date}_{end_date}.json",
        cache_dir / f"weekly_risk_tuning_v1_{symbol}_{start_date}_{end_date}.json",
        cache_dir / f"ml_comparison_v2_{symbol}_{start_date}_{end_date}.json",
        cache_dir / f"ml_diagnostics_v1_{symbol}_{start_date}_{end_date}.json",
    ]


def backtest_result_rag_documents(timeframe: str, result: dict, path: Path) -> list[dict]:
    metrics = {
        "timeframe": timeframe,
        "strategyDescription": result.get("strategyDescription"),
        "trades": result.get("totalTrades", len(result.get("trades", []))),
        "pnl": round(float(result.get("totalPnl") or 0), 2),
        "returnPercent": result.get("totalReturnPercent"),
        "finalEquity": result.get("finalEquity"),
        "maxDrawdown": result.get("maxDrawdown"),
        "maxDrawdownPercent": result.get("maxDrawdownPercent"),
        "profitFactor": result.get("profitFactor"),
        "winRate": round((float(result.get("winners") or 0) / max(1, int(result.get("totalTrades") or len(result.get("trades", []))))) * 100, 1),
        "expectancy": result.get("expectancy"),
        "averageWin": result.get("averageWin"),
        "averageLoss": result.get("averageLoss"),
    }
    text = (
        f"{timeframe} Voting Ensemble result. Mode {metrics['strategyDescription']}. "
        f"Trades {metrics['trades']}, P/L {metrics['pnl']}, return {metrics['returnPercent']}%, "
        f"drawdown {metrics['maxDrawdown']} ({metrics['maxDrawdownPercent']}%), "
        f"profit factor {metrics['profitFactor']}, win rate {metrics['winRate']}%, expectancy {metrics['expectancy']}."
    )
    docs = [
        {
            "id": f"backtest-{timeframe}",
            "kind": "backtest_result",
            "title": f"{timeframe} backtest result",
            "timeframe": timeframe,
            "metrics": metrics,
            "text": text,
            "sourcePath": str(path),
        }
    ]
    diagnostics = result.get("diagnostics") or {}
    for group_name, rows in diagnostics.items():
        if not isinstance(rows, list):
            continue
        best_rows = sorted(rows, key=lambda row: float(row.get("pnl") or 0), reverse=True)[:4]
        for row in best_rows:
            docs.append(
                {
                    "id": f"diagnostic-{timeframe}-{group_name}-{row.get('label')}",
                    "kind": "diagnostic_slice",
                    "title": f"{timeframe} {group_name} {row.get('label')}",
                    "timeframe": timeframe,
                    "metrics": row,
                    "text": (
                        f"{timeframe} diagnostic {group_name}={row.get('label')}. "
                        f"Trades {row.get('trades')}, P/L {row.get('pnl')}, win rate {row.get('winRate')}%, "
                        f"profit factor {row.get('profitFactor')}, average R {row.get('averageR')}, drawdown {row.get('maxDrawdown')}."
                    ),
                    "sourcePath": str(path),
                }
            )
    return docs


def generic_artifact_rag_document(label: str, artifact: dict, path: Path) -> dict:
    summary_parts = [label]
    for key in ["recommendation", "goal", "version"]:
        if artifact.get(key):
            summary_parts.append(str(artifact[key]))
    for key in ["best", "bestRiskAdjusted", "profitPreservingBest", "qualityBest"]:
        value = artifact.get(key)
        if isinstance(value, dict):
            summary_parts.append(
                f"{key}: {value.get('variant') or value.get('label') or value.get('settings')}; "
                f"pnl {value.get('pnl')}, pf {value.get('profitFactor')}, dd {value.get('maxDrawdown')}"
            )
    text = ". ".join(summary_parts)
    return {
        "id": f"artifact-{label.lower().replace(' ', '-')}",
        "kind": "artifact_summary",
        "title": label,
        "timeframe": artifact.get("timeframe"),
        "metrics": {
            "version": artifact.get("version"),
            "recommendation": artifact.get("recommendation"),
            "testedVariants": artifact.get("testedVariants"),
        },
        "text": text,
        "sourcePath": str(path),
    }


def retrieve_trading_rag_docs(documents: list[dict], *, query: str, current: dict, limit: int) -> list[dict]:
    query_text = trading_rag_query_text(query, current)
    query_terms = term_counts(query_text)
    scored = []
    for document in documents:
        doc_terms = term_counts(f"{document.get('title', '')} {document.get('text', '')}")
        score = cosine_similarity(query_terms, doc_terms)
        timeframe = str(document.get("timeframe") or "")
        selected = str(current.get("selectedTimeframe") or "")
        if selected and selected == timeframe:
            score += 0.15
        winner = str(current.get("winner") or "").lower()
        if winner and winner in str(document.get("text") or "").lower():
            score += 0.05
        if score > 0:
            scored.append((score, document))
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    selected = []
    timeframe_counts: dict[str, int] = defaultdict(int)
    kind_counts: dict[str, int] = defaultdict(int)
    for score, document in ranked:
        timeframe = str(document.get("timeframe") or "Other")
        kind = str(document.get("kind") or "Other")
        if timeframe_counts[timeframe] >= 2:
            continue
        if kind == "diagnostic_slice" and kind_counts[kind] >= 3:
            continue
        selected.append((score, document))
        timeframe_counts[timeframe] += 1
        kind_counts[kind] += 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        seen = {str(document.get("id")) for _, document in selected}
        for score, document in ranked:
            if str(document.get("id")) in seen:
                continue
            selected.append((score, document))
            if len(selected) >= limit:
                break
    return [{**document, "score": round(score, 4)} for score, document in selected]


def trading_rag_query_text(query: str, current: dict) -> str:
    votes = current.get("votes") if isinstance(current.get("votes"), list) else []
    layers = current.get("marketContext") if isinstance(current.get("marketContext"), dict) else {}
    return " ".join(
        [
            query,
            str(current.get("winner") or ""),
            str(current.get("selectedTimeframe") or ""),
            " ".join(f"{vote.get('strategy')} {vote.get('signal')} {vote.get('status')} {vote.get('detail')}" for vote in votes if isinstance(vote, dict)),
            " ".join(
                f"{name} {value.get('label')} {value.get('directionBias')} {value.get('volatility')}"
                for name, value in layers.items()
                if isinstance(value, dict)
            ),
        ]
    )


def term_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for term in re.findall(r"[a-z0-9]+", text.lower()):
        if len(term) < 2 or term in {"the", "and", "for", "with", "that", "this", "from", "into"}:
            continue
        counts[term] += 1
    return counts


def cosine_similarity(left: dict[str, int], right: dict[str, int]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left.get(term, 0) * right.get(term, 0) for term in left)
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


async def ask_local_model_for_trading_rag(*, query: str, current: dict, retrieved: list[dict]) -> dict:
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.15, "num_predict": 480},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You answer a SPY trading RAG query using only the supplied historical result snippets. "
                    "Return valid JSON with keys conclusion, bias, confidence, bestHistoricalMatch, drivers, risks, actionPlan. "
                    "Be concise and avoid claiming certainty."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "current": current,
                        "retrieved": [
                            {
                                "title": doc.get("title"),
                                "timeframe": doc.get("timeframe"),
                                "text": doc.get("text"),
                                "metrics": doc.get("metrics"),
                            }
                            for doc in retrieved
                        ],
                    },
                    separators=(",", ":"),
                ),
            },
        ],
    }
    async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
        last_error: httpx.HTTPStatusError | None = None
        for model in await ollama_model_candidates(client):
            payload["model"] = model
            response = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if response.status_code in {400, 404}:
                    continue
                raise
            parsed = json.loads(response.json().get("message", {}).get("content", ""))
            return normalize_trading_rag_answer(parsed, retrieved)
        if last_error:
            raise last_error
        raise ValueError("No local model available")


def normalize_trading_rag_answer(value: dict, retrieved: list[dict]) -> dict:
    fallback = fallback_trading_rag_answer(query="", current={}, retrieved=retrieved)
    best_match = value.get("bestHistoricalMatch")
    if isinstance(best_match, dict):
        best_match = " ".join(str(best_match.get(key) or "") for key in ["timeframe", "title", "strategy", "variant"]).strip()
    return {
        "conclusion": str(value.get("conclusion") or fallback["conclusion"])[:700],
        "bias": normalize_bias_label(value.get("bias"), fallback["bias"]),
        "confidence": normalize_confidence_label(value.get("confidence"), fallback["confidence"]),
        "bestHistoricalMatch": str(best_match or fallback["bestHistoricalMatch"])[:180],
        "drivers": normalize_summary_list(value.get("drivers"), fallback["drivers"][0])[:5],
        "risks": normalize_summary_list(value.get("risks"), fallback["risks"][0])[:5],
        "actionPlan": normalize_summary_list(value.get("actionPlan"), fallback["actionPlan"][0])[:5],
    }


def normalize_bias_label(value: object, fallback: str) -> str:
    text = str(value or fallback).strip().title()
    return text if text in {"Buy", "Sell", "Hold", "Cautious", "Mixed"} else fallback


def normalize_confidence_label(value: object, fallback: str) -> str:
    text = str(value or fallback).strip().title()
    return text if text in {"High", "Medium", "Low"} else fallback


def fallback_trading_rag_answer(*, query: str, current: dict, retrieved: list[dict]) -> dict:
    best = sorted(
        retrieved,
        key=lambda doc: (
            float((doc.get("metrics") or {}).get("pnl") or 0),
            -float((doc.get("metrics") or {}).get("maxDrawdown") or 0),
            float((doc.get("metrics") or {}).get("profitFactor") or 0),
        ),
        reverse=True,
    )[0] if retrieved else {}
    metrics = best.get("metrics") or {}
    winner = str(current.get("winner") or "Hold")
    best_title = str(best.get("title") or "No historical match available")
    pnl = float(metrics.get("pnl") or 0)
    drawdown = float(metrics.get("maxDrawdown") or 0)
    profit_factor = metrics.get("profitFactor")
    conclusion = (
        f"Current vote winner is {winner}. The closest stored historical result is {best_title}, "
        f"with P/L {pnl:.2f}, max drawdown {drawdown:.2f}, and profit factor {profit_factor or 'NA'}. "
        "Use this as a reference filter, not an automatic order."
        if best
        else "No stored trading result matched the current query yet. Build more artifacts and trade logs before relying on RAG guidance."
    )
    bias = winner if winner in {"Buy", "Sell", "Hold"} else "Hold"
    return {
        "conclusion": conclusion,
        "bias": bias,
        "confidence": "Medium" if best else "Low",
        "bestHistoricalMatch": best_title,
        "drivers": [
            f"Retrieved {len(retrieved)} historical result snippets.",
            f"Current vote winner: {winner}.",
            f"Best match P/L: {pnl:.2f}.",
        ],
        "risks": [
            "RAG is descriptive and depends on the stored backtest artifacts.",
            "Current market conditions can diverge from historical slices.",
            "Missing ML artifacts reduce the available evidence set.",
        ],
        "actionPlan": [
            "Compare current vote winner with the retrieved best historical timeframe.",
            "Prefer paper-trade validation before changing live rules.",
            "Refresh after artifact regeneration to include ML comparison and candidate datasets.",
        ],
    }


def backtest_cache_dir(manifest: dict) -> Path:
    manifest_value = str(manifest.get("manifest") or "")
    manifest_path = Path(manifest_value) if manifest_value else None
    files = manifest.get("files", {})
    fallback_path = Path(str(files.get("continuous1mJsonl") or "."))
    return manifest_path.parent if manifest_path and manifest_path.exists() else fallback_path.parent


def parse_ml_variant_threshold(value: str) -> float | None:
    marker = ">="
    if marker not in value:
        return None
    try:
        return float(value.split(marker, 1)[1].strip())
    except ValueError:
        return None


def candidate_dataset_timeframe_summary(rows: list[dict]) -> list[dict]:
    summary = []
    for timeframe in ML_COMPARISON_TIMEFRAMES:
        subset = [row for row in rows if row.get("timeframe") == timeframe]
        if not subset:
            continue
        outcomes = [row for row in subset if row.get("rowType") == "outcome"]
        pnl = round(sum(float(row.get("pnl") or 0) for row in outcomes), 2)
        summary.append(
            {
                "timeframe": timeframe,
                "rows": len(subset),
                "candidates": len([row for row in subset if row.get("rowType") == "candidate"]),
                "outcomes": len(outcomes),
                "skipped": len([row for row in subset if row.get("decision") == "skipped_ml"]),
                "pnl": pnl,
                "winners": len([row for row in outcomes if float(row.get("pnl") or 0) > 0]),
                "losers": len([row for row in outcomes if float(row.get("pnl") or 0) < 0]),
            }
        )
    return summary


def candidate_export_row(
    symbol: str,
    timeframe: str,
    trade: dict,
    *,
    row_type: str,
    source: str,
    decision: str,
) -> dict:
    entry_at = str(trade.get("entryAt") or "")
    entry_dt = parse_utc_datetime(entry_at) if entry_at else None
    features = ml_trade_row(timeframe, trade).get("features", {})
    pnl = float(trade.get("pnl") or 0)
    r_multiple = float(trade.get("rMultiple") or 0)
    label_available = row_type == "outcome" and bool(trade.get("exitAt"))
    return {
        "rowId": 0,
        "symbol": symbol,
        "timeframe": timeframe,
        "rowType": row_type,
        "source": source,
        "decision": decision,
        "labelAvailable": label_available,
        "targetWin": 1 if label_available and pnl > 0 else 0 if label_available else None,
        "targetPositiveR": 1 if label_available and r_multiple > 0 else 0 if label_available else None,
        "targetRMultiple": round(r_multiple, 4) if label_available else None,
        "entryAt": entry_at,
        "exitAt": trade.get("exitAt"),
        "sessionDate": trade.get("sessionDate") or (entry_at[:10] if entry_at else ""),
        "year": entry_dt.year if entry_dt else None,
        "yearMonth": trade.get("yearMonth") or (entry_at[:7] if entry_at else ""),
        "month": entry_dt.month if entry_dt else None,
        "dayOfWeek": entry_dt.weekday() if entry_dt else None,
        "entryHour": trade.get("entryHour"),
        "side": trade.get("side"),
        "regime": trade.get("regime"),
        "eventType": trade.get("eventType") or "standard",
        "exitReason": trade.get("exitReason"),
        "entryPrice": round(float(trade.get("entryPrice") or 0), 4),
        "exitPrice": round(float(trade.get("exitPrice") or 0), 4) if trade.get("exitPrice") is not None else None,
        "shares": int(trade.get("shares") or 0),
        "positionValue": round(float(trade.get("positionValue") or 0), 2),
        "riskDollars": round(float(trade.get("riskDollars") or 0), 2),
        "plannedRiskPerShare": round(float(trade.get("plannedRiskPerShare") or 0), 4),
        "stopPrice": round(float(trade.get("stopPrice") or 0), 4) if trade.get("stopPrice") is not None else None,
        "targetPrice": round(float(trade.get("targetPrice") or 0), 4) if trade.get("targetPrice") is not None else None,
        "buyVotes": int(trade.get("buyVotes") or 0),
        "sellVotes": int(trade.get("sellVotes") or 0),
        "holdVotes": int(trade.get("holdVotes") or 0),
        "directionalVotes": max(int(trade.get("buyVotes") or 0), int(trade.get("sellVotes") or 0)),
        "voteMargin": abs(int(trade.get("buyVotes") or 0) - int(trade.get("sellVotes") or 0)),
        "mlProbability": trade.get("mlProbability"),
        "mlThreshold": trade.get("mlThreshold"),
        "mlModelStatus": trade.get("mlModelStatus"),
        "mlDecision": trade.get("mlDecision"),
        "pnl": round(pnl, 2) if label_available else None,
        "grossPnl": round(float(trade.get("grossPnl") or 0), 2) if label_available else None,
        "expenses": round(float(trade.get("expenses") or 0), 2) if label_available else None,
        "rMultiple": round(r_multiple, 4) if label_available else None,
        "returnPercent": round(float(trade.get("returnPercent") or 0), 4) if label_available else None,
        "accountReturnPercent": round(float(trade.get("accountReturnPercent") or 0), 4) if label_available else None,
        "featuresJson": json.dumps(features, sort_keys=True, separators=(",", ":")),
    }


def load_ml_base_results(*, manifest: dict, start_date: str, end_date: str) -> dict[str, dict]:
    files = manifest.get("files", {})
    continuous_1m = Path(str(files.get("continuous1mJsonl", "")))
    continuous_5m = Path(str(files.get("continuous5mJsonl", "")))
    daily = Path(str(files.get("dailyJsonl", "")))
    results: dict[str, dict] = {}
    for timeframe in ["1Min", "5Min", "1Hour", "1Day", "1Week"]:
        data_path = daily if timeframe in {"1Day", "1Week"} else continuous_5m if timeframe == "5Min" else continuous_1m
        results[timeframe] = (
            cached_voting_ensemble_backtest(
                data_path=data_path,
                manifest=manifest,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
            )
            if data_path.exists()
            else {"trades": []}
        )
    results["Event"] = (
        cached_open_close_events_backtest(
            data_path=continuous_1m,
            daily_path=daily,
            manifest=manifest,
            start_date=start_date,
            end_date=end_date,
        )
        if continuous_1m.exists() and daily.exists()
        else {"trades": []}
    )
    return results


def prepare_ml_replay_data(*, manifest: dict, start_date: str, end_date: str) -> dict[str, list[dict]]:
    files = manifest.get("files", {})
    continuous_1m = Path(str(files.get("continuous1mJsonl", "")))
    continuous_5m = Path(str(files.get("continuous5mJsonl", "")))
    daily = Path(str(files.get("dailyJsonl", "")))
    start = parse_backtest_start_datetime(start_date)
    end = parse_backtest_end_datetime(end_date)
    one_minute = [candle for candle in read_jsonl(continuous_1m) if start <= parse_utc_datetime(str(candle["timestamp"])) <= end] if continuous_1m.exists() else []
    five_minute = [candle for candle in read_jsonl(continuous_5m) if start <= parse_utc_datetime(str(candle["timestamp"])) <= end] if continuous_5m.exists() else []
    daily_candles = [candle for candle in read_jsonl(daily) if start <= parse_utc_datetime(str(candle["timestamp"])) <= end] if daily.exists() else []
    return {
        "1Min": one_minute,
        "5Min": five_minute,
        "1HourExecution": aggregate_candles(one_minute, timeframe="5Min", minutes=5) if one_minute else [],
        "1HourDirection": aggregate_candles(one_minute, timeframe="1Hour", minutes=60) if one_minute else [],
        "1Day": daily_candles,
        "1Week": aggregate_weekly_candles(daily_candles) if daily_candles else [],
        "EventIntraday": one_minute,
        "EventWeekly": aggregate_weekly_candles(daily_candles) if daily_candles else [],
    }


def run_ml_replay_result(
    *,
    replay_data: dict[str, list[dict]],
    timeframe: str,
    ml_filter: dict,
    risk_config_override: dict | None = None,
) -> dict:
    if timeframe == "Event":
        if not replay_data.get("EventIntraday") or not replay_data.get("EventWeekly"):
            return {"trades": [], "mlSkippedCandidates": 0}
        return run_open_close_events_backtest(
            replay_data["EventIntraday"],
            replay_data["EventWeekly"],
            ml_filter=ml_filter,
            risk_config_override=risk_config_override,
        )
    if timeframe == "1Hour":
        return run_one_hour_filter_backtest(
            replay_data.get("1HourExecution", []),
            replay_data.get("1HourDirection", []),
            ml_filter=ml_filter,
            risk_config_override=risk_config_override,
        )
    if timeframe in {"1Day", "1Week"}:
        return run_swing_voting_ensemble_backtest(
            replay_data.get(timeframe, []),
            timeframe=timeframe,
            ml_filter=ml_filter,
            risk_config_override=risk_config_override,
        )
    candles = replay_data.get(timeframe, [])
    if not candles:
        return {"trades": [], "mlSkippedCandidates": 0}
    return run_voting_ensemble_backtest(candles, timeframe=timeframe, ml_filter=ml_filter, risk_config_override=risk_config_override)


def ml_trade_row(timeframe: str, trade: dict) -> dict:
    entry_at = str(trade.get("entryAt") or "")
    entry_dt = parse_utc_datetime(entry_at) if entry_at else None
    month = entry_dt.month if entry_dt else 0
    day_of_week = entry_dt.weekday() if entry_dt else 0
    year = entry_dt.year if entry_dt else 0
    buy_votes = int(trade.get("buyVotes") or 0)
    sell_votes = int(trade.get("sellVotes") or 0)
    hold_votes = int(trade.get("holdVotes") or 0)
    total_votes = max(1, buy_votes + sell_votes + hold_votes)
    risk_dollars = float(trade.get("riskDollars") or 0)
    position_value = float(trade.get("positionValue") or 0)
    planned_risk = float(trade.get("plannedRiskPerShare") or 0)
    entry_price = float(trade.get("entryPrice") or 0)
    features: dict[str, float] = {
        "bias": 1.0,
        "buyVotes": buy_votes / 10,
        "sellVotes": sell_votes / 10,
        "holdVotes": hold_votes / 10,
        "voteMargin": abs(buy_votes - sell_votes) / 10,
        "directionalVotes": max(buy_votes, sell_votes) / 10,
        "totalVotes": total_votes / 10,
        "riskToPosition": risk_dollars / position_value if position_value else 0,
        "plannedRiskPct": planned_risk / entry_price if entry_price else 0,
    }
    for key, value in {
        "timeframe": timeframe,
        "side": str(trade.get("side") or "NA"),
        "regime": str(trade.get("regime") or "NA"),
        "entryHour": str(trade.get("entryHour") or "NA"),
        "eventType": str(trade.get("eventType") or "standard"),
        "month": str(month),
        "dayOfWeek": str(day_of_week),
    }.items():
        features[f"{key}={value}"] = 1.0
    return {
        "timeframe": timeframe,
        "trade": trade,
        "entryAt": entry_at,
        "year": year,
        "target": 1 if float(trade.get("pnl") or 0) > 0 else 0,
        "features": features,
    }


def add_walk_forward_probabilities(rows: list[dict]) -> list[dict]:
    years = sorted({int(row["year"]) for row in rows if int(row["year"]) > 0})
    for year in years:
        train_rows = [row for row in rows if int(row["year"]) < year]
        test_rows = [row for row in rows if int(row["year"]) == year]
        if len(train_rows) < 30 or len({row["target"] for row in train_rows}) < 2:
            probability = (sum(row["target"] for row in train_rows) / len(train_rows)) if train_rows else 0.5
            for row in test_rows:
                row["mlProbability"] = round(probability, 4)
                row["mlModelStatus"] = "insufficient_history"
            continue
        weights = train_sparse_logistic(train_rows)
        for row in test_rows:
            row["mlProbability"] = round(sigmoid(sparse_dot(weights, row["features"])), 4)
            row["mlModelStatus"] = "walk_forward"
    return rows


def build_walk_forward_model_map(rows: list[dict]) -> dict[int, dict]:
    model_map: dict[int, dict] = {}
    years = sorted({int(row["year"]) for row in rows if int(row["year"]) > 0})
    for year in years:
        train_rows = [row for row in rows if int(row["year"]) < year]
        if len(train_rows) < 30 or len({row["target"] for row in train_rows}) < 2:
            probability = (sum(row["target"] for row in train_rows) / len(train_rows)) if train_rows else 0.5
            model_map[year] = {"status": "insufficient_history", "fallbackProbability": probability, "weights": {}}
        else:
            model_map[year] = {"status": "walk_forward", "fallbackProbability": 0.5, "weights": train_sparse_logistic(train_rows)}
    return model_map


def ml_allows_candidate(timeframe: str, trade: dict, ml_filter: dict | None) -> tuple[bool, dict]:
    if not ml_filter:
        return True, trade
    entry_at = str(trade.get("entryAt") or "")
    if not entry_at:
        return True, trade
    year = parse_utc_datetime(entry_at).year
    model = dict(ml_filter.get("modelsByYear", {}).get(year) or {})
    threshold = float(ml_filter.get("threshold") or 0)
    row = ml_trade_row(timeframe, trade)
    probability = float(model.get("fallbackProbability", 0.5))
    weights = model.get("weights") or {}
    if weights:
        probability = sigmoid(sparse_dot(weights, row["features"]))
    scored_trade = {
        **trade,
        "mlProbability": round(probability, 4),
        "mlThreshold": threshold,
        "mlModelStatus": str(model.get("status") or "unavailable"),
        "mlDecision": "Take" if probability >= threshold else "Skip",
    }
    allowed = probability >= threshold
    collector = ml_filter.get("candidateCollector")
    if isinstance(collector, list):
        collector.append(
            candidate_export_row(
                str(ml_filter.get("symbol") or "SPY"),
                timeframe,
                scored_trade,
                row_type="candidate",
                source="ml_replay",
                decision="taken_ml" if allowed else "skipped_ml",
            )
        )
    return allowed, scored_trade


def train_sparse_logistic(rows: list[dict], *, epochs: int = 90, learning_rate: float = 0.08, l2: float = 0.0005) -> dict[str, float]:
    weights: dict[str, float] = defaultdict(float)
    positives = max(1, sum(row["target"] for row in rows))
    negatives = max(1, len(rows) - positives)
    positive_weight = len(rows) / (2 * positives)
    negative_weight = len(rows) / (2 * negatives)
    for _ in range(epochs):
        for row in rows:
            target = float(row["target"])
            prediction = sigmoid(sparse_dot(weights, row["features"]))
            row_weight = positive_weight if target else negative_weight
            gradient_scale = (prediction - target) * row_weight
            for feature, value in row["features"].items():
                weights[feature] -= learning_rate * ((gradient_scale * value) + (l2 * weights[feature]))
    return dict(weights)


def sparse_dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(float(weights.get(feature, 0)) * float(value) for feature, value in features.items())


def sigmoid(value: float) -> float:
    if value < -35:
        return 0.0
    if value > 35:
        return 1.0
    return 1 / (1 + exp(-value))


def ml_metric_row(
    timeframe: str,
    variant: str,
    threshold: float | None,
    kept_rows: list[dict],
    all_rows: list[dict],
    *,
    risk_config: dict | None = None,
) -> dict:
    kept_ids = {id(row) for row in kept_rows}
    skipped_rows = [row for row in all_rows if id(row) not in kept_ids]
    metrics = ml_trade_metrics(
        [row["trade"] for row in sorted(kept_rows, key=lambda item: item["entryAt"])],
        risk_config=risk_config,
    )
    return {
        "timeframe": timeframe,
        "variant": variant,
        "threshold": threshold,
        **metrics,
        "skippedTrades": len(skipped_rows),
        "skippedWinners": len([row for row in skipped_rows if float(row["trade"].get("pnl") or 0) > 0]),
        "skippedLosers": len([row for row in skipped_rows if float(row["trade"].get("pnl") or 0) < 0]),
        "skippedPnl": round(sum(float(row["trade"].get("pnl") or 0) for row in skipped_rows), 2),
    }


def ml_result_metric_row(
    timeframe: str,
    variant: str,
    threshold: float,
    result: dict,
    base_metrics: dict,
    *,
    risk_config: dict | None = None,
) -> dict:
    trades = list(result.get("trades", []))
    metrics = ml_trade_metrics(trades, risk_config=risk_config)
    skipped = int(result.get("mlSkippedCandidates") or max(0, int(base_metrics.get("trades") or 0) - len(trades)))
    return {
        "timeframe": timeframe,
        "variant": variant,
        "threshold": threshold,
        **metrics,
        "skippedTrades": skipped,
        "skippedWinners": 0,
        "skippedLosers": 0,
        "skippedPnl": 0,
        "replayMode": "in_loop",
    }


def ml_trade_metrics(trades: list[dict], *, risk_config: dict | None = None) -> dict:
    config = risk_config or VOTING_ENSEMBLE_RISK_CONFIG
    starting_capital = float(config["startingCapital"])
    equity = starting_capital
    peak = starting_capital
    max_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    winners = 0
    losers = 0
    monthly: dict[str, float] = defaultdict(float)
    for trade in sorted(trades, key=lambda item: str(item.get("entryAt") or "")):
        pnl = float(trade.get("pnl") or 0)
        equity = round(equity + pnl, 2)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        monthly[str(trade.get("yearMonth") or str(trade.get("entryAt") or "")[:7])] += pnl
        if pnl > 0:
            winners += 1
            gross_profit += pnl
        elif pnl < 0:
            losers += 1
            gross_loss += abs(pnl)
    total_pnl = round(sum(float(trade.get("pnl") or 0) for trade in trades), 2)
    total_expenses = round(sum(float(trade.get("expenses") or 0) for trade in trades), 2)
    return {
        "trades": len(trades),
        "pnl": total_pnl,
        "returnPercent": round((total_pnl / starting_capital) * 100, 2),
        "finalEquity": round(starting_capital + total_pnl, 2),
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2),
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "winRate": round((winners / len(trades)) * 100, 1) if trades else 0,
        "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
        "averageR": round(sum(float(trade.get("rMultiple") or 0) for trade in trades) / len(trades), 2) if trades else 0,
        "winners": winners,
        "losers": losers,
        "totalExpenses": total_expenses,
        "worstMonth": round(min(monthly.values()), 2) if monthly else 0,
        "bestMonth": round(max(monthly.values()), 2) if monthly else 0,
    }


def ml_verdict(base: dict, candidate: dict) -> str:
    if int(candidate["trades"]) < max(3, int(base["trades"]) * 0.1):
        return "Inconclusive"
    base_pf = float(base["profitFactor"] or 0)
    candidate_pf = float(candidate["profitFactor"] or 0)
    if candidate["expectancy"] > base["expectancy"] and candidate_pf >= base_pf and candidate["maxDrawdown"] <= base["maxDrawdown"]:
        return "Improved"
    if candidate["expectancy"] < base["expectancy"] and candidate["maxDrawdown"] >= base["maxDrawdown"]:
        return "Worse"
    return "Mixed"


def cached_open_close_events_backtest(
    *,
    data_path: Path,
    daily_path: Path,
    manifest: dict,
    start_date: str,
    end_date: str,
) -> dict:
    cache_path = data_path.parent / f"open_close_events_v5_{start_date}_{end_date}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    start = parse_backtest_start_datetime(start_date)
    end = parse_backtest_end_datetime(end_date)
    intraday_candles = [
        candle
        for candle in read_jsonl(data_path)
        if start <= parse_utc_datetime(str(candle["timestamp"])) <= end
    ]
    daily_candles = [
        candle
        for candle in read_jsonl(daily_path)
        if start <= parse_utc_datetime(str(candle["timestamp"])) <= end
    ]
    weekly_candles = aggregate_weekly_candles(daily_candles)
    result = run_open_close_events_backtest(intraday_candles, weekly_candles)
    result["sourceManifest"] = manifest.get("manifest") or str(data_path.parent / "manifest.json")
    result["startDate"] = start_date
    result["endDate"] = end_date
    result["timeframe"] = "Event"
    result["rangeLabel"] = f"{start_date} to {end_date}"
    result["cachedAt"] = datetime.now(UTC).isoformat()
    write_json(cache_path, result)
    return result


@app.get("/api/macro-events")
def macro_events(limit: int = Query(8, ge=1, le=20)) -> dict:
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    upcoming = [
        release
        for release in sorted(MACRO_RELEASES_2026, key=lambda item: item["releaseAt"])
        if release["releaseAt"].astimezone(UTC) >= now
    ][:limit]
    return {
        "source": "BLS 2026 release schedule",
        "updatedAt": now.isoformat(),
        "events": [
            {
                **release,
                "releaseAt": release["releaseAt"].isoformat(),
                "daysUntil": max(0, (release["releaseAt"].date() - eastern_now.date()).days),
            }
            for release in upcoming
        ],
    }


@app.get("/api/fed-events")
def fed_events(limit: int = Query(8, ge=1, le=20)) -> dict:
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    upcoming = [
        event
        for event in sorted(FED_EVENTS_2026, key=lambda item: item["releaseAt"])
        if event["releaseAt"].astimezone(UTC) >= now
    ][:limit]
    return {
        "source": "Federal Reserve calendar",
        "updatedAt": now.isoformat(),
        "events": [
            {
                **event,
                "releaseAt": event["releaseAt"].isoformat(),
                "daysUntil": max(0, (event["releaseAt"].date() - eastern_now.date()).days),
            }
            for event in upcoming
        ],
    }


@app.get("/api/trading-alerts")
async def trading_alerts(limit: int = Query(8, ge=1, le=20)) -> dict:
    now = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
            response = await client.get(TRADE_HALTS_RSS_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return {
            "source": "Nasdaq Trader Trade Halt RSS",
            "updatedAt": now.isoformat(),
            "warning": str(exc),
            "events": [],
        }

    return {
        "source": "Nasdaq Trader Trade Halt RSS",
        "updatedAt": now.isoformat(),
        "events": parse_trade_halt_rss(response.text)[:limit],
    }


@app.get("/api/news-feed")
async def news_feed(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    limit: int = Query(10, ge=1, le=30),
) -> dict:
    now = datetime.now(UTC)
    normalized_symbol = symbol.upper()
    source_statuses = news_source_statuses(settings.has_alpaca_credentials)

    if settings.has_alpaca_credentials:
        try:
            items = await fetch_alpaca_news(normalized_symbol, limit)
            if items:
                source_statuses[0]["status"] = "ready"
                source_statuses[0]["note"] = "Loaded from Alpaca news REST endpoint; compatible with the Alpaca news stream source."
                return {
                    "source": "Alpaca News",
                    "updatedAt": now.isoformat(),
                    "symbol": normalized_symbol,
                    "items": items,
                    "sources": source_statuses,
                }
            source_statuses[0]["status"] = "empty"
            source_statuses[0]["note"] = "Alpaca returned no current headlines for this symbol."
        except httpx.HTTPError as exc:
            source_statuses[0]["status"] = "error"
            source_statuses[0]["note"] = str(exc)

    try:
        yahoo_items = await fetch_yahoo_finance_news(normalized_symbol, limit)
        if yahoo_items:
            source_statuses[2]["status"] = "ready"
            source_statuses[2]["note"] = "Loaded from Yahoo Finance RSS fallback."
            return {
                "source": "Yahoo Finance RSS",
                "updatedAt": now.isoformat(),
                "symbol": normalized_symbol,
                "items": yahoo_items,
                "sources": source_statuses,
                "warning": "Using Yahoo Finance RSS fallback; it is not an official streaming API.",
            }
        source_statuses[2]["status"] = "empty"
        source_statuses[2]["note"] = "Yahoo Finance RSS returned no current headlines for this symbol."
    except httpx.HTTPError as exc:
        source_statuses[2]["status"] = "error"
        source_statuses[2]["note"] = str(exc)

    return {
        "source": "Dashboard fallback",
        "updatedAt": now.isoformat(),
        "symbol": normalized_symbol,
        "items": fallback_news_items(normalized_symbol, limit, now),
        "sources": source_statuses,
        "warning": "Live SPY news is unavailable. Configure Alpaca news access for current headlines; other listed providers require paid plans or are RSS/manual fallbacks.",
    }


@app.get("/api/news-summary")
async def news_summary(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    limit: int = Query(10, ge=3, le=20),
) -> dict:
    now = datetime.now(UTC)
    normalized_symbol = symbol.upper()
    snapshot = await build_trade_summary_snapshot(normalized_symbol, limit)

    try:
        selected_model, summary = await ask_ollama_for_trade_summary(snapshot)
        source = f"Ollama {selected_model}"
        warning = ""
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        summary = fallback_trade_summary(snapshot)
        source = "Rule fallback"
        warning = f"Ollama summary unavailable: {exc}"

    return {
        "source": source,
        "updatedAt": now.isoformat(),
        "symbol": normalized_symbol,
        "summary": summary,
        "snapshot": snapshot,
        "warning": warning,
    }


async def build_trade_summary_snapshot(symbol: str, limit: int) -> dict:
    news = await news_feed(symbol=symbol, limit=limit)
    macro = macro_events(limit=4)
    fed = fed_events(limit=4)
    alerts = await trading_alerts(limit=6)
    breakers = circuit_breakers(symbol=symbol)
    moc = moc_imbalance(symbol=symbol)
    vix = await vix_risk()
    es = await es_snapshot()
    return {
        "symbol": symbol,
        "news": {
            "source": news.get("source", ""),
            "warning": news.get("warning", ""),
            "items": [
                {
                    "headline": item.get("headline", ""),
                    "summary": item.get("summary", ""),
                    "source": item.get("source", ""),
                    "publishedAt": item.get("publishedAt"),
                    "symbols": item.get("symbols", []),
                }
                for item in news.get("items", [])[:limit]
            ],
        },
        "macroEvents": macro.get("events", [])[:4],
        "fedEvents": fed.get("events", [])[:4],
        "tradingAlerts": alerts.get("events", [])[:6],
        "circuitBreakers": {
            "referenceSymbol": breakers.get("referenceSymbol"),
            "referenceClose": breakers.get("referenceClose"),
            "rules": breakers.get("rules", []),
        },
        "mocImbalance": {
            "status": moc.get("status"),
            "latest": moc.get("latest"),
            "warning": moc.get("warning", ""),
        },
        "vixRisk": {
            "quote": vix.get("quote"),
            "activeLevel": vix.get("activeLevel"),
            "warning": vix.get("warning", ""),
        },
        "esSnapshot": {
            "session": es.get("session"),
            "quote": es.get("quote"),
            "changePoints": es.get("changePoints"),
            "changePercent": es.get("changePercent"),
            "activeLevel": es.get("activeLevel"),
            "warning": es.get("warning", ""),
        },
    }


async def ask_ollama_for_trade_summary(snapshot: dict) -> tuple[str, dict]:
    prompt = build_trade_summary_prompt(snapshot)
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_predict": 420,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "You summarize market news for an intraday SPY trading dashboard. "
                    "Return only valid JSON. Be concise, risk-aware, and avoid certainty."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=35, trust_env=False) as client:
        last_error: httpx.HTTPStatusError | None = None
        for model in await ollama_model_candidates(client):
            payload["model"] = model
            response = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if response.status_code in {400, 404}:
                    continue
                raise
            content = response.json().get("message", {}).get("content", "")
            parsed = json.loads(content)
            return model, normalize_trade_summary(parsed)
        if last_error:
            raise last_error
        raise ValueError("No Ollama model available")


async def ollama_model_candidates(client: httpx.AsyncClient) -> list[str]:
    candidates = [settings.ollama_model]
    try:
        response = await client.get(f"{settings.ollama_base_url}/api/tags")
        response.raise_for_status()
        models = response.json().get("models", [])
    except httpx.HTTPError:
        return candidates
    for item in models:
        name = str(item.get("name") or item.get("model") or "")
        if name and (name.startswith("llama3") or name.startswith("llama")):
            candidates.append(name)
    return list(dict.fromkeys(candidates))


def build_trade_summary_prompt(snapshot: dict) -> str:
    compact = {
        "symbol": snapshot["symbol"],
        "headlines": snapshot["news"]["items"][:10],
        "macroEvents": [
            {
                "title": item.get("title"),
                "category": item.get("category"),
                "releaseAt": item.get("releaseAt"),
                "daysUntil": item.get("daysUntil"),
            }
            for item in snapshot["macroEvents"]
        ],
        "fedEvents": [
            {
                "title": item.get("title"),
                "category": item.get("category"),
                "detail": item.get("detail"),
                "releaseAt": item.get("releaseAt"),
                "daysUntil": item.get("daysUntil"),
            }
            for item in snapshot["fedEvents"]
        ],
        "tradingAlerts": snapshot["tradingAlerts"][:4],
        "vixRisk": snapshot["vixRisk"],
        "esSnapshot": snapshot["esSnapshot"],
        "mocImbalance": snapshot["mocImbalance"],
    }
    return (
        "Use this JSON market snapshot to summarize the trading implication for SPY.\n"
        "Return JSON with exactly these keys: bias, confidence, conclusion, drivers, risks, actionPlan.\n"
        "bias must be Bullish, Bearish, Neutral, or Cautious. confidence must be Low, Medium, or High.\n"
        "drivers, risks, and actionPlan must each be arrays of 2 to 4 short strings.\n"
        "Focus on what could matter for the next session/intraday trade, not long-term investing.\n"
        f"Snapshot:\n{json.dumps(compact, ensure_ascii=True)[:7000]}"
    )


def normalize_trade_summary(value: dict) -> dict:
    return {
        "bias": str(value.get("bias") or "Cautious")[:24],
        "confidence": str(value.get("confidence") or "Low")[:24],
        "conclusion": str(value.get("conclusion") or "No clear trade conclusion from current feeds.")[:700],
        "drivers": normalize_summary_list(value.get("drivers"), "No strong positive driver identified."),
        "risks": normalize_summary_list(value.get("risks"), "Headline and volatility risk remain uncertain."),
        "actionPlan": normalize_summary_list(value.get("actionPlan"), "Wait for price confirmation before acting."),
    }


def normalize_summary_list(value: object, fallback: str) -> list[str]:
    if isinstance(value, list):
        items = [summary_item_to_text(item)[:220] for item in value if summary_item_to_text(item).strip()]
        if items:
            return items[:4]
    return [fallback]


def summary_item_to_text(item: object) -> str:
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("strategy") or item.get("title") or item.get("label") or "").strip()
        status = str(item.get("status") or "").strip()
        score = item.get("score")
        matches = item.get("matches") if isinstance(item.get("matches"), list) else []
        risks = item.get("risks") if isinstance(item.get("risks"), list) else []
        parts = []
        if name:
            parts.append(name)
        if status or score is not None:
            parts.append(" ".join([status, f"{score}%" if score is not None else ""]).strip())
        if matches:
            parts.append(f"matches {', '.join(str(value) for value in matches[:3])}")
        if risks:
            parts.append(f"risks {', '.join(str(value) for value in risks[:3])}")
        if parts:
            return ": ".join([parts[0], "; ".join(parts[1:])]) if len(parts) > 1 else parts[0]
        return json.dumps(item, separators=(",", ":"))
    return str(item)


def fallback_trade_summary(snapshot: dict) -> dict:
    es_level = (snapshot.get("esSnapshot", {}).get("activeLevel") or {}).get("label", "")
    vix_level = (snapshot.get("vixRisk", {}).get("activeLevel") or {}).get("label", "")
    alerts = snapshot.get("tradingAlerts", [])
    headlines = snapshot.get("news", {}).get("items", [])
    bias = "Neutral"
    if "Bullish" in es_level and vix_level in {"Calm", "Normal"}:
        bias = "Bullish"
    elif "Bearish" in es_level or vix_level in {"Stress", "Shock"} or alerts:
        bias = "Cautious"
    return {
        "bias": bias,
        "confidence": "Low",
        "conclusion": (
            "Local Ollama summary is unavailable, so this is a rule-based read. "
            "Treat SPY bias as headline-sensitive until price confirms direction."
        ),
        "drivers": [
            f"Latest news feed has {len(headlines)} SPY-related headline(s).",
            f"ES futures signal: {es_level or 'unavailable'}.",
            f"VIX regime: {vix_level or 'unavailable'}.",
        ],
        "risks": [
            "No local LLM conclusion was produced.",
            "Macro/Fed events can change intraday volatility quickly.",
            "Use chart confirmation and defined invalidation.",
        ],
        "actionPlan": [
            "Favor waiting for opening range or VWAP confirmation.",
            "Avoid chasing headline spikes without liquidity confirmation.",
            "Reduce size if VIX or ES signals conflict with SPY price action.",
        ],
    }


async def fetch_alpaca_news(symbol: str, limit: int) -> list[dict]:
    base_url = settings.alpaca_data_base_url
    if base_url.endswith("/v2"):
        base_url = base_url[: -len("/v2")]
    url = f"{base_url}/v1beta1/news"
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    params = {
        "symbols": symbol,
        "limit": limit,
        "sort": "desc",
        "include_content": "false",
    }
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
    payload = response.json()
    news_items = payload.get("news") if isinstance(payload, dict) else payload
    if not isinstance(news_items, list):
        return []
    return [normalize_news_item(item, symbol) for item in news_items[:limit] if isinstance(item, dict)]


async def fetch_yahoo_finance_news(symbol: str, limit: int) -> list[dict]:
    headers = {
        "User-Agent": "TradingDashboard/0.1 (+local SPY news fallback)",
    }
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=headers, trust_env=False) as client:
        response = await client.get(YAHOO_FINANCE_RSS_URL.format(symbol=symbol))
        response.raise_for_status()
    return parse_yahoo_finance_rss(response.text, symbol)[:limit]


def parse_yahoo_finance_rss(payload: str, symbol: str) -> list[dict]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        if not title:
            continue
        link = item.findtext("link", default="").strip()
        description = strip_html(item.findtext("description", default="").strip())
        published_at = parse_rss_date(item.findtext("pubDate", default="").strip())
        source = item.findtext("source", default="Yahoo Finance").strip() or "Yahoo Finance"
        items.append(
            {
                "id": link or f"{symbol}-{title}",
                "headline": title,
                "summary": description,
                "url": link,
                "source": source,
                "publishedAt": published_at,
                "symbols": [symbol],
            },
        )
    return items


def normalize_news_item(item: dict, symbol: str) -> dict:
    created_at = item.get("created_at") or item.get("updated_at") or item.get("published_at")
    symbols = item.get("symbols") if isinstance(item.get("symbols"), list) else [symbol]
    return {
        "id": str(item.get("id") or item.get("url") or item.get("headline") or f"{symbol}-news"),
        "headline": str(item.get("headline") or item.get("title") or "SPY market headline"),
        "summary": str(item.get("summary") or ""),
        "url": str(item.get("url") or ""),
        "source": str(item.get("source") or "Alpaca News"),
        "publishedAt": parse_iso_datetime(created_at),
        "symbols": [str(value).upper() for value in symbols],
    }


def parse_iso_datetime(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def latest_session_date(candles: list[dict]) -> str | None:
    if not candles:
        return None
    return candle_session_date(candles[-1])


def candle_session_date(candle: dict) -> str:
    return eastern_datetime_from_iso(candle["timestamp"]).date().isoformat()


def parse_backtest_start_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return datetime(parsed.year, parsed.month, parsed.day, 0, 0, 0, tzinfo=UTC)


def parse_backtest_end_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=UTC)


def eastern_datetime_from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    first_pass = parsed_utc.astimezone(eastern_tz_for_date(parsed_utc.year, parsed_utc.month, parsed_utc.day))
    return parsed_utc.astimezone(eastern_tz_for_date(first_pass.year, first_pass.month, first_pass.day))


def filter_session_date(candles: list[dict], session_date: str | None) -> list[dict]:
    if not session_date:
        return []
    return [candle for candle in candles if candle_session_date(candle) == session_date]


def previous_daily_close(daily: list[dict], session_date: str | None) -> float | None:
    if not session_date:
        return None
    prior = [candle for candle in daily if candle_session_date(candle) < session_date]
    return float(prior[-1]["close"]) if prior else None


def run_voting_ensemble_backtest(
    candles: list[dict],
    *,
    timeframe: str,
    ml_filter: dict | None = None,
    risk_config_override: dict | None = None,
) -> dict:
    config = risk_config_override or VOTING_ENSEMBLE_RISK_CONFIG
    starting_capital = float(config["startingCapital"])
    if len(candles) < 2:
        return {
            "dateLabel": "No candles",
            "trades": [],
            "totalPnl": 0,
            "totalReturnPercent": 0,
            "finalEquity": starting_capital,
            "maxDrawdown": 0,
            "maxDrawdownPercent": 0,
            "profitFactor": None,
            "averageWin": 0,
            "averageLoss": 0,
            "expectancy": 0,
            "winners": 0,
            "losers": 0,
            "bars": len(candles),
            "sessions": 0,
            "riskConfig": config,
        }

    sorted_candles = sorted(candles, key=lambda item: item["timestamp"])
    sessions: dict[str, list[dict]] = {}
    for candle in sorted_candles:
        sessions.setdefault(candle_session_date(candle), []).append(candle)

    trades: list[dict] = []
    equity = starting_capital
    peak_equity = starting_capital
    max_drawdown = 0.0
    prior_close: float | None = None
    ml_skipped_candidates = 0
    warmup = int(config.get("warmupBarsByTimeframe", {}).get(timeframe, 20))
    for _, day_candles in sorted(sessions.items()):
        regular_candles = [candle for candle in day_candles if 570 <= candle_minutes_et(candle) <= 955]
        if len(regular_candles) < max(2, warmup):
            prior_close = float((regular_candles or day_candles)[-1]["close"])
            continue
        day_prior_close = prior_close if prior_close is not None else float(regular_candles[0]["open"])
        open_trade: dict | None = None
        day_start_equity = equity
        day_loss_limit = day_start_equity * (float(config["maxDailyLossPercent"]) / 100)
        daily_pnl = 0.0
        day_trade_count = 0
        trading_locked = False
        first_index = min(warmup, len(regular_candles) - 1)
        for index in range(first_index, len(regular_candles)):
            candle = regular_candles[index]
            minute = candle_minutes_et(candle)
            history = regular_candles[:index]
            if open_trade is not None:
                current_signal = historical_vote_summary(history, day_prior_close, timeframe=timeframe)["signal"] if len(history) >= warmup else "Hold"
                fade_against_position = (open_trade["side"] == "Long" and current_signal != "Buy") or (open_trade["side"] == "Short" and current_signal != "Sell")
                losing_at_open = current_trade_unrealized_pnl(open_trade, float(candle["open"])) < 0
                if config["signalFadeExit"] != "disabled" and fade_against_position and losing_at_open:
                    closed_trade = close_risk_managed_trade(open_trade, candle["timestamp"], float(candle["open"]), "Signal fade")
                else:
                    closed_trade = stop_target_or_time_exit(open_trade, candle)
                if closed_trade is not None:
                    trades.append(closed_trade)
                    equity = round(equity + float(closed_trade["pnl"]), 2)
                    daily_pnl = round(daily_pnl + float(closed_trade["pnl"]), 2)
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    open_trade = None
                    if daily_pnl <= -day_loss_limit:
                        trading_locked = True
                if minute >= time_to_minutes(str(config["forceClose"])):
                    continue
            if (
                trading_locked
                or day_trade_count >= int(config["maxTradesPerDay"])
                or minute < time_to_minutes(str(config["sessionStart"]))
                or minute > time_to_minutes(str(config["newTradesUntil"]))
            ):
                continue

            if len(history) < warmup:
                continue
            vote_summary = historical_vote_summary(history, day_prior_close, timeframe=timeframe)
            signal = vote_summary["signal"]
            if signal == "Hold":
                continue
            confirmation_bars = int(config.get("entryConfirmationBarsByTimeframe", {}).get(timeframe, config["entryConfirmationBars"]))
            if not signal_confirmed(regular_candles, index, day_prior_close, signal, warmup, confirmation_bars, timeframe):
                continue
            entry_hour = eastern_datetime_from_iso(str(candle["timestamp"])).strftime("%H:00")
            allowed_hours = config.get("allowedEntryHoursByTimeframe", {}).get(timeframe, [])
            if allowed_hours and entry_hour not in allowed_hours:
                continue

            side = "Long" if signal == "Buy" else "Short"
            opening_range = opening_range_values(history, 15)
            entry_trade = open_risk_managed_trade(
                side=side,
                candle=candle,
                opening_range=opening_range,
                equity=equity,
                session_date=candle_session_date(candle),
                vote_summary=vote_summary,
                risk_config=config,
            )
            if entry_trade is not None:
                allowed, scored_trade = ml_allows_candidate(timeframe, entry_trade, ml_filter)
                if not allowed:
                    ml_skipped_candidates += 1
                    continue
                entry_trade = scored_trade
                open_trade = entry_trade
                day_trade_count += 1

        final_candle = last_candle_at_or_before(regular_candles, time_to_minutes(str(config["forceClose"]))) or regular_candles[-1]
        if open_trade is not None:
            closed_trade = close_risk_managed_trade(open_trade, final_candle["timestamp"], float(final_candle["close"]), "Timed close")
            trades.append(closed_trade)
            equity = round(equity + float(closed_trade["pnl"]), 2)
            daily_pnl = round(daily_pnl + float(closed_trade["pnl"]), 2)
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)
        prior_close = float(regular_candles[-1]["close"])

    total_pnl = round(sum(float(trade["pnl"]) for trade in trades), 2)
    total_return_percent = round(((equity - starting_capital) / starting_capital) * 100, 2)
    winners = len([trade for trade in trades if float(trade["pnl"]) > 0])
    losers = len([trade for trade in trades if float(trade["pnl"]) < 0])
    gross_profit = round(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0), 2)
    gross_loss = round(abs(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)), 2)
    total_expenses = round(sum(float(trade.get("expenses") or 0) for trade in trades), 2)
    average_win = round(gross_profit / winners, 2) if winners else 0
    average_loss = round(gross_loss / losers, 2) if losers else 0
    expectancy = round(total_pnl / len(trades), 2) if trades else 0
    diagnostics = build_backtest_diagnostics(trades, max_drawdown=max_drawdown, timeframe=timeframe, config=config)
    return {
        "dateLabel": f"{candle_session_date(sorted_candles[0])} to {candle_session_date(sorted_candles[-1])}",
        "trades": trades,
        "totalPnl": total_pnl,
        "totalReturnPercent": total_return_percent,
        "startingCapital": starting_capital,
        "finalEquity": round(equity, 2),
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2),
        "grossProfit": gross_profit,
        "grossLoss": gross_loss,
        "totalExpenses": total_expenses,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "averageWin": average_win,
        "averageLoss": average_loss,
        "expectancy": expectancy,
        "winners": winners,
        "losers": losers,
        "bars": len(sorted_candles),
        "sessions": len(sessions),
        "firstBar": sorted_candles[0]["timestamp"],
        "lastBar": sorted_candles[-1]["timestamp"],
        "riskConfig": config,
        "diagnostics": diagnostics,
        "mlSkippedCandidates": ml_skipped_candidates,
    }


def run_open_close_events_backtest(
    intraday_candles: list[dict],
    weekly_candles: list[dict],
    *,
    ml_filter: dict | None = None,
    risk_config_override: dict | None = None,
) -> dict:
    config = risk_config_override or VOTING_ENSEMBLE_RISK_CONFIG
    event_config = dict(config.get("openCloseEvents", {}))
    starting_capital = float(config["startingCapital"])
    if len(intraday_candles) < 2 or len(weekly_candles) < 20:
        return {
            "dateLabel": "No candles",
            "trades": [],
            "totalPnl": 0,
            "totalReturnPercent": 0,
            "startingCapital": starting_capital,
            "finalEquity": starting_capital,
            "maxDrawdown": 0,
            "maxDrawdownPercent": 0,
            "grossProfit": 0,
            "grossLoss": 0,
            "profitFactor": None,
            "averageWin": 0,
            "averageLoss": 0,
            "expectancy": 0,
            "winners": 0,
            "losers": 0,
            "bars": len(intraday_candles),
            "sessions": 0,
            "riskConfig": config,
            "strategyDescription": event_config.get("label", "Opening/Closing Event Ensemble"),
        }

    sorted_intraday = sorted(intraday_candles, key=lambda item: item["timestamp"])
    sorted_weekly = sorted(weekly_candles, key=lambda item: item["timestamp"])
    sessions = candles_by_session(sorted_intraday)
    trades: list[dict] = []
    equity = starting_capital
    peak_equity = starting_capital
    max_drawdown = 0.0
    ml_skipped_candidates = 0
    max_trades_per_day = int(event_config.get("maxTradesPerDay", 2))
    opening_range_minutes = int(event_config.get("openingRangeMinutes", 15))
    opening_start = 570 + opening_range_minutes
    opening_end = time_to_minutes(str(event_config.get("openingEnd", "10:30")))
    closing_start = time_to_minutes(str(event_config.get("closingStart", "15:30")))
    closing_end = time_to_minutes(str(event_config.get("closingEnd", "15:50")))
    force_close = time_to_minutes(str(event_config.get("forceClose", config["forceClose"])))
    blocked_regimes = set(str(item) for item in event_config.get("blockedRegimes", []))
    enable_closing_events = bool(event_config.get("enableClosingEvents", True))

    for session_date, day_candles in sorted(sessions.items()):
        regular_candles = [candle for candle in day_candles if 570 <= candle_minutes_et(candle) <= 955]
        if len(regular_candles) < 30:
            continue
        weekly_history = [candle for candle in sorted_weekly if candle_session_date(candle) < session_date]
        if len(weekly_history) < int(config.get("warmupBarsByTimeframe", {}).get("1Week", 20)):
            continue
        weekly_prior_close = float(weekly_history[-1]["close"])
        vote_summary = historical_vote_summary(weekly_history, weekly_prior_close, timeframe="1Week")
        signal = str(vote_summary["signal"])
        directional_votes = max(int(vote_summary["buyVotes"]), int(vote_summary["sellVotes"]))
        if signal == "Hold" or str(vote_summary["regime"]) in blocked_regimes:
            continue

        opening_range = opening_range_values(regular_candles, opening_range_minutes)
        day_open = float(regular_candles[0]["open"])
        open_trade: dict | None = None
        day_trade_count = 0

        for index, candle in enumerate(regular_candles):
            minute = candle_minutes_et(candle)
            if open_trade is not None:
                closed_trade = stop_target_or_time_exit(open_trade, candle)
                if closed_trade is None and minute >= force_close:
                    closed_trade = close_risk_managed_trade(open_trade, candle["timestamp"], float(candle["close"]), "Timed close")
                if closed_trade is not None:
                    trades.append(closed_trade)
                    equity = round(equity + float(closed_trade["pnl"]), 2)
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    open_trade = None
                if minute >= force_close:
                    continue

            if open_trade is not None or day_trade_count >= max_trades_per_day:
                continue

            history = regular_candles[: max(1, index)]
            vwap = session_vwap_value(history)
            close = float(candle["close"])
            side = "Long" if signal == "Buy" else "Short"
            event_type = ""
            if opening_start <= minute <= opening_end:
                if directional_votes < int(event_config.get("minOpeningWeeklyDirectionalVotes", 3)):
                    continue
                if signal == "Buy" and close > float(opening_range["high"]) and close > vwap:
                    event_type = "Opening breakout"
                elif signal == "Sell" and close < float(opening_range["low"]) and close < vwap:
                    event_type = "Opening breakdown"
            elif enable_closing_events and closing_start <= minute <= closing_end:
                if directional_votes < int(event_config.get("minClosingWeeklyDirectionalVotes", 4)):
                    continue
                if signal == "Buy" and close > day_open and close > vwap:
                    event_type = "Closing continuation"
                elif signal == "Sell" and close < day_open and close < vwap:
                    event_type = "Closing continuation"
            if not event_type:
                continue

            entry_trade = open_event_risk_managed_trade(
                side=side,
                candle=candle,
                opening_range=opening_range,
                equity=equity,
                session_date=session_date,
                vote_summary=vote_summary,
                event_type=event_type,
                risk_config=config,
            )
            if entry_trade is not None:
                allowed, scored_trade = ml_allows_candidate("Event", entry_trade, ml_filter)
                if not allowed:
                    ml_skipped_candidates += 1
                    continue
                entry_trade = scored_trade
                open_trade = entry_trade
                day_trade_count += 1

        if open_trade is not None:
            final_candle = last_candle_at_or_before(regular_candles, force_close) or regular_candles[-1]
            closed_trade = close_risk_managed_trade(open_trade, final_candle["timestamp"], float(final_candle["close"]), "Timed close")
            trades.append(closed_trade)
            equity = round(equity + float(closed_trade["pnl"]), 2)
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

    total_pnl = round(sum(float(trade["pnl"]) for trade in trades), 2)
    total_return_percent = round(((equity - starting_capital) / starting_capital) * 100, 2)
    winners = len([trade for trade in trades if float(trade["pnl"]) > 0])
    losers = len([trade for trade in trades if float(trade["pnl"]) < 0])
    gross_profit = round(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0), 2)
    gross_loss = round(abs(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)), 2)
    total_expenses = round(sum(float(trade.get("expenses") or 0) for trade in trades), 2)
    diagnostics = build_backtest_diagnostics(trades, max_drawdown=max_drawdown, timeframe="Open/Close", config=config)
    diagnostics["byEventType"] = diagnostic_group(trades, "eventType")
    return {
        "dateLabel": f"{candle_session_date(sorted_intraday[0])} to {candle_session_date(sorted_intraday[-1])}",
        "trades": trades,
        "totalPnl": total_pnl,
        "totalReturnPercent": total_return_percent,
        "startingCapital": starting_capital,
        "finalEquity": round(equity, 2),
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2),
        "grossProfit": gross_profit,
        "grossLoss": gross_loss,
        "totalExpenses": total_expenses,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "averageWin": round(gross_profit / winners, 2) if winners else 0,
        "averageLoss": round(gross_loss / losers, 2) if losers else 0,
        "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
        "winners": winners,
        "losers": losers,
        "bars": len(sorted_intraday),
        "sessions": len(sessions),
        "firstBar": sorted_intraday[0]["timestamp"],
        "lastBar": sorted_intraday[-1]["timestamp"],
        "riskConfig": config,
        "diagnostics": diagnostics,
        "strategyDescription": event_config.get("label", "Opening/Closing Event Ensemble"),
        "mlSkippedCandidates": ml_skipped_candidates,
    }


def open_event_risk_managed_trade(
    *,
    side: str,
    candle: dict,
    opening_range: dict,
    equity: float,
    session_date: str,
    vote_summary: dict,
    event_type: str,
    risk_config: dict | None = None,
) -> dict | None:
    config = risk_config or VOTING_ENSEMBLE_RISK_CONFIG
    event_config = dict(config.get("openCloseEvents", {}))
    slippage = float(config["slippagePerShare"])
    raw_open = float(candle["open"])
    entry_price = raw_open + slippage if side == "Long" else raw_open - slippage
    fixed_stop_distance = configured_stop_distance(config, entry_price, event_config)
    if side == "Long":
        opening_stop = float(opening_range["low"])
        fixed_stop = entry_price - fixed_stop_distance
        stop_price = max(fixed_stop, opening_stop) if opening_stop < entry_price else fixed_stop
        stop_distance = entry_price - stop_price
        target_price = entry_price + (stop_distance * float(event_config.get("takeProfitR", config["takeProfitR"])))
    else:
        opening_stop = float(opening_range["high"])
        fixed_stop = entry_price + fixed_stop_distance
        stop_price = min(fixed_stop, opening_stop) if opening_stop > entry_price else fixed_stop
        stop_distance = stop_price - entry_price
        target_price = entry_price - (stop_distance * float(event_config.get("takeProfitR", config["takeProfitR"])))
    if stop_distance <= 0:
        return None
    shares, planned_risk, sizing_mode = position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)
    if shares < 1:
        return None
    return {
        "side": side,
        "sessionDate": session_date,
        "entryHour": eastern_datetime_from_iso(str(candle["timestamp"])).strftime("%H:00"),
        "yearMonth": session_date[:7],
        "regime": vote_summary["regime"],
        "buyVotes": vote_summary["buyVotes"],
        "sellVotes": vote_summary["sellVotes"],
        "holdVotes": vote_summary["holdVotes"],
        "voteStrength": vote_summary["voteStrength"],
        "eventType": event_type,
        "entryAt": candle["timestamp"],
        "entryPrice": round(entry_price, 4),
        "rawEntryPrice": raw_open,
        "shares": shares,
        "riskDollars": round(planned_risk, 2),
        "plannedRiskPerShare": round(stop_distance, 4),
        "stopPrice": round(stop_price, 4),
        "targetPrice": round(target_price, 4),
        "positionValue": round(entry_price * shares, 2),
        "positionSizingMode": sizing_mode,
        "riskConfig": config,
        "stopModel": "opening-range invalidation or fixed stop",
    }


def run_one_hour_filter_backtest(
    execution_candles: list[dict],
    direction_candles: list[dict],
    *,
    ml_filter: dict | None = None,
    risk_config_override: dict | None = None,
) -> dict:
    config = risk_config_override or VOTING_ENSEMBLE_RISK_CONFIG
    hybrid_config = dict(config.get("hybridOneHour", {}))
    starting_capital = float(config["startingCapital"])
    if len(execution_candles) < 2 or len(direction_candles) < 2:
        return {
            "dateLabel": "No candles",
            "trades": [],
            "totalPnl": 0,
            "totalReturnPercent": 0,
            "startingCapital": starting_capital,
            "finalEquity": starting_capital,
            "maxDrawdown": 0,
            "maxDrawdownPercent": 0,
            "grossProfit": 0,
            "grossLoss": 0,
            "profitFactor": None,
            "averageWin": 0,
            "averageLoss": 0,
            "expectancy": 0,
            "winners": 0,
            "losers": 0,
            "bars": len(execution_candles),
            "sessions": 0,
            "riskConfig": config,
            "strategyDescription": hybrid_config.get("label", "1h filter + 5m execution"),
        }

    sorted_execution = sorted(execution_candles, key=lambda item: item["timestamp"])
    sorted_direction = sorted(direction_candles, key=lambda item: item["timestamp"])
    execution_sessions = candles_by_session(sorted_execution)
    direction_sessions = candles_by_session(sorted_direction)
    session_closes: list[float] = []
    trades: list[dict] = []
    equity = starting_capital
    peak_equity = starting_capital
    max_drawdown = 0.0
    ml_skipped_candidates = 0
    warmup_execution = int(config.get("warmupBarsByTimeframe", {}).get("5Min", 20))
    min_direction_bars = int(config.get("warmupBarsByTimeframe", {}).get("1Hour", 2))
    confirmation_bars = int(config.get("entryConfirmationBarsByTimeframe", {}).get("5Min", 3))
    blocked_hours = set(str(item) for item in hybrid_config.get("blockedDirectionHours", []))
    blocked_regimes = set(str(item) for item in hybrid_config.get("blockedRegimes", []))
    require_daily_alignment = bool(hybrid_config.get("requireDailyTrendAlignment", True))
    allowed_daily_signals = set(str(item) for item in hybrid_config.get("allowedDailySignals", []))

    for session_date, day_candles in sorted(execution_sessions.items()):
        regular_candles = [candle for candle in day_candles if 570 <= candle_minutes_et(candle) <= 955]
        direction_day = [candle for candle in direction_sessions.get(session_date, []) if 570 <= candle_minutes_et(candle) <= 955]
        if len(regular_candles) < max(2, warmup_execution) or len(direction_day) < min_direction_bars:
            if regular_candles:
                session_closes.append(float(regular_candles[-1]["close"]))
            continue

        day_prior_close = session_closes[-1] if session_closes else float(regular_candles[0]["open"])
        daily_signal = daily_trend_signal(session_closes)
        open_trade: dict | None = None
        day_start_equity = equity
        day_loss_limit = day_start_equity * (float(config["maxDailyLossPercent"]) / 100)
        daily_pnl = 0.0
        day_trade_count = 0
        trading_locked = False

        for index in range(warmup_execution, len(regular_candles)):
            candle = regular_candles[index]
            minute = candle_minutes_et(candle)
            execution_history = regular_candles[:index]
            if open_trade is not None:
                closed_trade = stop_target_or_time_exit(open_trade, candle)
                if closed_trade is not None:
                    trades.append(closed_trade)
                    equity = round(equity + float(closed_trade["pnl"]), 2)
                    daily_pnl = round(daily_pnl + float(closed_trade["pnl"]), 2)
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, peak_equity - equity)
                    open_trade = None
                    if daily_pnl <= -day_loss_limit:
                        trading_locked = True
                if minute >= time_to_minutes(str(config["forceClose"])):
                    continue

            if (
                trading_locked
                or day_trade_count >= int(config["maxTradesPerDay"])
                or minute < time_to_minutes(str(config["sessionStart"]))
                or minute > time_to_minutes(str(config["newTradesUntil"]))
            ):
                continue

            direction_history = completed_direction_history(direction_day, minute)
            if len(direction_history) < min_direction_bars:
                continue
            vote_summary = historical_vote_summary(direction_history, day_prior_close, timeframe="1Hour")
            signal = str(vote_summary["signal"])
            if signal == "Hold":
                continue
            direction_hour = eastern_datetime_from_iso(str(direction_history[-1]["timestamp"])).strftime("%H:00")
            if direction_hour in blocked_hours or str(vote_summary["regime"]) in blocked_regimes:
                continue
            if require_daily_alignment and daily_signal and daily_signal != signal:
                continue
            if allowed_daily_signals and signal not in allowed_daily_signals:
                continue
            if not execution_confirmation(execution_history, signal, confirmation_bars):
                continue

            side = "Long" if signal == "Buy" else "Short"
            opening_range = opening_range_values(execution_history, 3)
            atr = average_true_range(execution_history, int(hybrid_config.get("atrPeriod", 14)))
            entry_trade = open_hybrid_risk_managed_trade(
                side=side,
                candle=candle,
                opening_range=opening_range,
                atr=atr,
                equity=equity,
                session_date=session_date,
                vote_summary={**vote_summary, "directionHour": direction_hour, "dailySignal": daily_signal or "NA"},
                risk_config=config,
            )
            if entry_trade is not None:
                allowed, scored_trade = ml_allows_candidate("1Hour", entry_trade, ml_filter)
                if not allowed:
                    ml_skipped_candidates += 1
                    continue
                entry_trade = scored_trade
                open_trade = entry_trade
                day_trade_count += 1

        final_candle = last_candle_at_or_before(regular_candles, time_to_minutes(str(config["forceClose"]))) or regular_candles[-1]
        if open_trade is not None:
            closed_trade = close_risk_managed_trade(open_trade, final_candle["timestamp"], float(final_candle["close"]), "Timed close")
            trades.append(closed_trade)
            equity = round(equity + float(closed_trade["pnl"]), 2)
            daily_pnl = round(daily_pnl + float(closed_trade["pnl"]), 2)
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)
        session_closes.append(float(regular_candles[-1]["close"]))

    total_pnl = round(sum(float(trade["pnl"]) for trade in trades), 2)
    total_return_percent = round(((equity - starting_capital) / starting_capital) * 100, 2)
    winners = len([trade for trade in trades if float(trade["pnl"]) > 0])
    losers = len([trade for trade in trades if float(trade["pnl"]) < 0])
    gross_profit = round(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0), 2)
    gross_loss = round(abs(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)), 2)
    total_expenses = round(sum(float(trade.get("expenses") or 0) for trade in trades), 2)
    diagnostics = build_backtest_diagnostics(trades, max_drawdown=max_drawdown, timeframe="1Hour", config=config)
    diagnostics["byDirectionHour"] = diagnostic_group(trades, "directionHour")
    diagnostics["byDailySignal"] = diagnostic_group(trades, "dailySignal")
    return {
        "dateLabel": f"{candle_session_date(sorted_execution[0])} to {candle_session_date(sorted_execution[-1])}",
        "trades": trades,
        "totalPnl": total_pnl,
        "totalReturnPercent": total_return_percent,
        "startingCapital": starting_capital,
        "finalEquity": round(equity, 2),
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2),
        "grossProfit": gross_profit,
        "grossLoss": gross_loss,
        "totalExpenses": total_expenses,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "averageWin": round(gross_profit / winners, 2) if winners else 0,
        "averageLoss": round(gross_loss / losers, 2) if losers else 0,
        "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
        "winners": winners,
        "losers": losers,
        "bars": len(sorted_execution),
        "sessions": len(execution_sessions),
        "firstBar": sorted_execution[0]["timestamp"],
        "lastBar": sorted_execution[-1]["timestamp"],
        "riskConfig": config,
        "diagnostics": diagnostics,
        "strategyDescription": hybrid_config.get("label", "1h filter + 5m execution"),
        "mlSkippedCandidates": ml_skipped_candidates,
    }


def run_swing_voting_ensemble_backtest(
    candles: list[dict],
    *,
    timeframe: str,
    ml_filter: dict | None = None,
    risk_config_override: dict | None = None,
) -> dict:
    config = risk_config_override or VOTING_ENSEMBLE_RISK_CONFIG
    swing_config = dict(config.get("swing", {}).get(timeframe, {}))
    starting_capital = float(config["startingCapital"])
    if len(candles) < 2:
        return {
            "dateLabel": "No candles",
            "trades": [],
            "totalPnl": 0,
            "totalReturnPercent": 0,
            "startingCapital": starting_capital,
            "finalEquity": starting_capital,
            "maxDrawdown": 0,
            "maxDrawdownPercent": 0,
            "grossProfit": 0,
            "grossLoss": 0,
            "profitFactor": None,
            "averageWin": 0,
            "averageLoss": 0,
            "expectancy": 0,
            "winners": 0,
            "losers": 0,
            "bars": len(candles),
            "sessions": 0,
            "riskConfig": config,
            "strategyDescription": swing_config.get("label", f"{timeframe} swing vote"),
        }

    sorted_candles = sorted(candles, key=lambda item: item["timestamp"])
    trades: list[dict] = []
    equity = starting_capital
    peak_equity = starting_capital
    max_drawdown = 0.0
    open_trade: dict | None = None
    ml_skipped_candidates = 0
    warmup = int(config.get("warmupBarsByTimeframe", {}).get(timeframe, 20))
    max_holding_bars = int(swing_config.get("maxHoldingBars", 5))
    max_drawdown_stop = float(swing_config.get("maxDrawdownStopPercent") or 0)
    drawdown_locked = False
    min_bars = max(warmup, 2)

    for index in range(min_bars, len(sorted_candles)):
        candle = sorted_candles[index]
        history = sorted_candles[:index]
        prior_close = float(history[-1]["close"])
        if open_trade is not None:
            current_signal = historical_vote_summary(history, prior_close, timeframe=timeframe)["signal"]
            bars_held = index - int(open_trade.get("entryIndex", index))
            signal_exit = (
                (open_trade["side"] == "Long" and current_signal == "Sell")
                or (open_trade["side"] == "Short" and current_signal == "Buy")
            )
            closed_trade = swing_stop_target_or_exit(
                open_trade,
                candle,
                bars_held=bars_held,
                max_holding_bars=max_holding_bars,
                signal_exit=signal_exit,
            )
            if closed_trade is not None:
                trades.append(closed_trade)
                equity = round(equity + float(closed_trade["pnl"]), 2)
                peak_equity = max(peak_equity, equity)
                max_drawdown = max(max_drawdown, peak_equity - equity)
                open_trade = None
                if max_drawdown_stop and (peak_equity - equity) >= starting_capital * (max_drawdown_stop / 100):
                    drawdown_locked = True

        if open_trade is not None or drawdown_locked:
            continue

        vote_summary = historical_vote_summary(history, prior_close, timeframe=timeframe)
        signal = str(vote_summary["signal"])
        if signal == "Hold":
            continue
        atr = average_true_range(history, int(swing_config.get("atrPeriod", 14)))
        side = "Long" if signal == "Buy" else "Short"
        entry_trade = open_swing_risk_managed_trade(
            side=side,
            candle=candle,
            atr=atr,
            equity=equity,
            session_date=candle_session_date(candle),
            vote_summary=vote_summary,
            timeframe=timeframe,
            index=index,
            risk_config=config,
        )
        if entry_trade is not None:
            allowed, scored_trade = ml_allows_candidate(timeframe, entry_trade, ml_filter)
            if not allowed:
                ml_skipped_candidates += 1
                continue
            entry_trade = scored_trade
            open_trade = entry_trade

    if open_trade is not None:
        final_candle = sorted_candles[-1]
        closed_trade = close_risk_managed_trade(open_trade, final_candle["timestamp"], float(final_candle["close"]), "Range close")
        trades.append(closed_trade)
        equity = round(equity + float(closed_trade["pnl"]), 2)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)

    total_pnl = round(sum(float(trade["pnl"]) for trade in trades), 2)
    total_return_percent = round(((equity - starting_capital) / starting_capital) * 100, 2)
    winners = len([trade for trade in trades if float(trade["pnl"]) > 0])
    losers = len([trade for trade in trades if float(trade["pnl"]) < 0])
    gross_profit = round(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) > 0), 2)
    gross_loss = round(abs(sum(float(trade["pnl"]) for trade in trades if float(trade["pnl"]) < 0)), 2)
    total_expenses = round(sum(float(trade.get("expenses") or 0) for trade in trades), 2)
    diagnostics = build_backtest_diagnostics(trades, max_drawdown=max_drawdown, timeframe=timeframe, config=config)
    return {
        "dateLabel": f"{candle_session_date(sorted_candles[0])} to {candle_session_date(sorted_candles[-1])}",
        "trades": trades,
        "totalPnl": total_pnl,
        "totalReturnPercent": total_return_percent,
        "startingCapital": starting_capital,
        "finalEquity": round(equity, 2),
        "maxDrawdown": round(max_drawdown, 2),
        "maxDrawdownPercent": round((max_drawdown / starting_capital) * 100, 2),
        "grossProfit": gross_profit,
        "grossLoss": gross_loss,
        "totalExpenses": total_expenses,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "averageWin": round(gross_profit / winners, 2) if winners else 0,
        "averageLoss": round(gross_loss / losers, 2) if losers else 0,
        "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
        "winners": winners,
        "losers": losers,
        "bars": len(sorted_candles),
        "sessions": len(sorted_candles),
        "firstBar": sorted_candles[0]["timestamp"],
        "lastBar": sorted_candles[-1]["timestamp"],
        "riskConfig": config,
        "diagnostics": diagnostics,
        "strategyDescription": swing_config.get("label", f"{timeframe} swing vote"),
        "mlSkippedCandidates": ml_skipped_candidates,
    }


def open_swing_risk_managed_trade(
    *,
    side: str,
    candle: dict,
    atr: float | None,
    equity: float,
    session_date: str,
    vote_summary: dict,
    timeframe: str,
    index: int,
    risk_config: dict | None = None,
) -> dict | None:
    config = risk_config or VOTING_ENSEMBLE_RISK_CONFIG
    swing_config = dict(config.get("swing", {}).get(timeframe, {}))
    slippage = float(config["slippagePerShare"])
    raw_open = float(candle["open"])
    entry_price = raw_open + slippage if side == "Long" else raw_open - slippage
    fixed_stop_distance = configured_stop_distance(config, entry_price, swing_config, percent_key="stopPercent")
    atr_stop_distance = (atr or 0) * float(swing_config.get("atrMultiplier", 1.5))
    stop_distance = max(fixed_stop_distance, atr_stop_distance)
    if side == "Long":
        stop_price = entry_price - stop_distance
        target_price = entry_price + (stop_distance * float(swing_config.get("takeProfitR", config["takeProfitR"])))
    else:
        stop_price = entry_price + stop_distance
        target_price = entry_price - (stop_distance * float(swing_config.get("takeProfitR", config["takeProfitR"])))
    shares, planned_risk, sizing_mode = position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)
    if shares < 1:
        return None
    return {
        "side": side,
        "sessionDate": session_date,
        "entryHour": "Daily" if timeframe == "1Day" else "Weekly",
        "yearMonth": session_date[:7],
        "regime": vote_summary["regime"],
        "buyVotes": vote_summary["buyVotes"],
        "sellVotes": vote_summary["sellVotes"],
        "holdVotes": vote_summary["holdVotes"],
        "voteStrength": vote_summary["voteStrength"],
        "entryAt": candle["timestamp"],
        "entryIndex": index,
        "entryPrice": round(entry_price, 4),
        "rawEntryPrice": raw_open,
        "shares": shares,
        "riskDollars": round(planned_risk, 2),
        "plannedRiskPerShare": round(stop_distance, 4),
        "stopPrice": round(stop_price, 4),
        "targetPrice": round(target_price, 4),
        "positionValue": round(entry_price * shares, 2),
        "positionSizingMode": sizing_mode,
        "riskConfig": config,
        "stopModel": f"max({configured_stop_distance(config, entry_price, swing_config, percent_key='stopPercent'):.2f} fixed dollars/share, ATR)",
    }


def swing_stop_target_or_exit(
    trade: dict,
    candle: dict,
    *,
    bars_held: int,
    max_holding_bars: int,
    signal_exit: bool,
) -> dict | None:
    if trade["side"] == "Long":
        if float(candle["low"]) <= float(trade["stopPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["stopPrice"]), "Stop loss")
        if float(candle["high"]) >= float(trade["targetPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["targetPrice"]), "Take profit")
    else:
        if float(candle["high"]) >= float(trade["stopPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["stopPrice"]), "Stop loss")
        if float(candle["low"]) <= float(trade["targetPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["targetPrice"]), "Take profit")
    if signal_exit:
        return close_risk_managed_trade(trade, candle["timestamp"], float(candle["open"]), "Signal flip")
    if bars_held >= max_holding_bars:
        return close_risk_managed_trade(trade, candle["timestamp"], float(candle["close"]), "Timed close")
    return None


def candles_by_session(candles: list[dict]) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = {}
    for candle in candles:
        sessions.setdefault(candle_session_date(candle), []).append(candle)
    return sessions


def completed_direction_history(direction_day: list[dict], execution_minute: int) -> list[dict]:
    return [candle for candle in direction_day if candle_minutes_et(candle) + 60 <= execution_minute]


def daily_trend_signal(session_closes: list[float]) -> str | None:
    if len(session_closes) < 20:
        return None
    sma20 = sum(session_closes[-20:]) / 20
    latest = session_closes[-1]
    if latest > sma20:
        return "Buy"
    if latest < sma20:
        return "Sell"
    return None


def execution_confirmation(history: list[dict], signal: str, bars: int) -> bool:
    if len(history) < bars:
        return False
    sample = history[-bars:]
    vwap = session_vwap_value(history)
    if signal == "Buy":
        return all(float(candle["close"]) >= vwap for candle in sample) and float(sample[-1]["close"]) > float(sample[0]["open"])
    if signal == "Sell":
        return all(float(candle["close"]) <= vwap for candle in sample) and float(sample[-1]["close"]) < float(sample[0]["open"])
    return False


def average_true_range(candles: list[dict], period: int) -> float | None:
    if len(candles) <= period:
        return None
    sample = candles[-period:]
    prior_close = float(candles[-period - 1]["close"])
    ranges = []
    for candle in sample:
        high = float(candle["high"])
        low = float(candle["low"])
        ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
        prior_close = float(candle["close"])
    return sum(ranges) / len(ranges) if ranges else None


def open_hybrid_risk_managed_trade(
    *,
    side: str,
    candle: dict,
    opening_range: dict,
    atr: float | None,
    equity: float,
    session_date: str,
    vote_summary: dict,
    risk_config: dict | None = None,
) -> dict | None:
    config = risk_config or VOTING_ENSEMBLE_RISK_CONFIG
    hybrid_config = dict(config.get("hybridOneHour", {}))
    slippage = float(config["slippagePerShare"])
    raw_open = float(candle["open"])
    entry_price = raw_open + slippage if side == "Long" else raw_open - slippage
    fixed_stop_distance = configured_stop_distance(config, entry_price)
    atr_stop_distance = (atr or 0) * float(hybrid_config.get("atrMultiplier", 0.75))
    dynamic_stop_distance = max(fixed_stop_distance, atr_stop_distance)
    if side == "Long":
        atr_stop = entry_price - dynamic_stop_distance
        opening_stop = float(opening_range["low"])
        stop_price = min(atr_stop, opening_stop) if opening_stop < entry_price else atr_stop
        stop_distance = entry_price - stop_price
        target_price = entry_price + (stop_distance * float(hybrid_config.get("takeProfitR", config["takeProfitR"])))
    else:
        atr_stop = entry_price + dynamic_stop_distance
        opening_stop = float(opening_range["high"])
        stop_price = max(atr_stop, opening_stop) if opening_stop > entry_price else atr_stop
        stop_distance = stop_price - entry_price
        target_price = entry_price - (stop_distance * float(hybrid_config.get("takeProfitR", config["takeProfitR"])))
    if stop_distance <= 0:
        return None
    shares, planned_risk, sizing_mode = position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)
    if shares < 1:
        return None
    return {
        "side": side,
        "sessionDate": session_date,
        "entryHour": eastern_datetime_from_iso(str(candle["timestamp"])).strftime("%H:00"),
        "directionHour": str(vote_summary.get("directionHour") or "NA"),
        "dailySignal": str(vote_summary.get("dailySignal") or "NA"),
        "yearMonth": session_date[:7],
        "regime": vote_summary["regime"],
        "buyVotes": vote_summary["buyVotes"],
        "sellVotes": vote_summary["sellVotes"],
        "holdVotes": vote_summary["holdVotes"],
        "voteStrength": vote_summary["voteStrength"],
        "entryAt": candle["timestamp"],
        "entryPrice": round(entry_price, 4),
        "rawEntryPrice": raw_open,
        "shares": shares,
        "riskDollars": round(planned_risk, 2),
        "plannedRiskPerShare": round(stop_distance, 4),
        "stopPrice": round(stop_price, 4),
        "targetPrice": round(target_price, 4),
        "positionValue": round(entry_price * shares, 2),
        "positionSizingMode": sizing_mode,
        "riskConfig": config,
        "stopModel": "max(0.35%, 0.75 ATR, opening-range invalidation)",
    }


def historical_winner_signal(history: list[dict], prior_close: float) -> str:
    return str(historical_vote_summary(history, prior_close)["signal"])


def historical_vote_summary(history: list[dict], prior_close: float, *, timeframe: str = "") -> dict:
    votes = historical_strategy_votes(history, prior_close, timeframe=timeframe)
    buy_votes = votes.count("Buy")
    sell_votes = votes.count("Sell")
    hold_votes = votes.count("Hold")
    signal = "Hold"
    min_directional_votes = VOTING_ENSEMBLE_RISK_CONFIG.get("directionalWinnerMinVotesByTimeframe", {}).get(timeframe)
    if min_directional_votes:
        if buy_votes >= int(min_directional_votes) and buy_votes > sell_votes:
            signal = "Buy"
        elif sell_votes >= int(min_directional_votes) and sell_votes > buy_votes:
            signal = "Sell"
    else:
        if buy_votes > sell_votes and buy_votes > hold_votes:
            signal = "Buy"
        elif sell_votes > buy_votes and sell_votes > hold_votes:
            signal = "Sell"
    directional_votes = max(buy_votes, sell_votes)
    return {
        "signal": signal,
        "buyVotes": buy_votes,
        "sellVotes": sell_votes,
        "holdVotes": hold_votes,
        "voteStrength": f"{directional_votes} directional votes",
        "regime": historical_regime_label(history),
    }


def signal_confirmed(candles: list[dict], index: int, prior_close: float, signal: str, warmup: int, bars: int, timeframe: str) -> bool:
    if bars <= 1:
        return True
    if index - bars + 1 < warmup:
        return False
    for offset in range(bars):
        history = candles[: index - offset]
        if len(history) < warmup:
            return False
        if historical_vote_summary(history, prior_close, timeframe=timeframe)["signal"] != signal:
            return False
    return True


def historical_regime_label(history: list[dict]) -> str:
    closes = [float(candle["close"]) for candle in history]
    latest_close = closes[-1]
    sma20 = simple_moving_average(closes, 20)
    sma50 = simple_moving_average(closes, 50)
    vwap = session_vwap_value(history)
    if sma20 is not None and sma50 is not None and sma20 > sma50 and latest_close > vwap:
        return "Trend Up"
    if sma20 is not None and sma50 is not None and sma20 < sma50 and latest_close < vwap:
        return "Trend Down"
    if abs((latest_close - vwap) / vwap) < 0.0015:
        return "VWAP Chop"
    return "Mixed"


def time_to_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return (int(hour) * 60) + int(minute)


def candle_minutes_et(candle: dict) -> int:
    timestamp = eastern_datetime_from_iso(str(candle["timestamp"]))
    return (timestamp.hour * 60) + timestamp.minute


def last_candle_at_or_before(candles: list[dict], minute: int) -> dict | None:
    candidates = [candle for candle in candles if candle_minutes_et(candle) <= minute]
    return candidates[-1] if candidates else None


def position_size_for_config(config: dict, *, equity: float, entry_price: float, stop_distance: float) -> tuple[int, float, str]:
    if stop_distance <= 0 or entry_price <= 0:
        return 0, 0.0, "invalid"
    if str(config.get("positionSizingMode") or "") == "allocation":
        starting_capital = float(config.get("startingCapital") or equity)
        order_limit = starting_capital * (float(config.get("orderAllocationPercent") or 10.0) / 100)
        risk_budget = order_limit * (float(config.get("riskBudgetPercentOfOrder") or 50.0) / 100)
        allocation_shares = int(min(order_limit, equity) // entry_price)
        planned_risk = allocation_shares * stop_distance
        if planned_risk > risk_budget:
            allocation_shares = int(risk_budget // stop_distance)
            planned_risk = allocation_shares * stop_distance
        return max(0, allocation_shares), planned_risk, "allocation"
    risk_dollars = equity * (float(config["riskPerTradePercent"]) / 100)
    risk_shares = int(risk_dollars // stop_distance)
    capital_shares = int(equity // entry_price)
    shares = max(0, min(risk_shares, capital_shares))
    return shares, shares * stop_distance, "risk"


def open_risk_managed_trade(
    *,
    side: str,
    candle: dict,
    opening_range: dict,
    equity: float,
    session_date: str,
    vote_summary: dict,
    risk_config: dict | None = None,
) -> dict | None:
    config = risk_config or VOTING_ENSEMBLE_RISK_CONFIG
    slippage = float(config["slippagePerShare"])
    raw_open = float(candle["open"])
    entry_price = raw_open + slippage if side == "Long" else raw_open - slippage
    fixed_stop_distance = configured_stop_distance(config, entry_price)
    if side == "Long":
        fixed_stop = entry_price - fixed_stop_distance
        opening_stop = float(opening_range["low"])
        stop_price = max(fixed_stop, opening_stop) if opening_stop < entry_price else fixed_stop
        stop_distance = entry_price - stop_price
        target_price = entry_price + (stop_distance * float(config["takeProfitR"]))
    else:
        fixed_stop = entry_price + fixed_stop_distance
        opening_stop = float(opening_range["high"])
        stop_price = min(fixed_stop, opening_stop) if opening_stop > entry_price else fixed_stop
        stop_distance = stop_price - entry_price
        target_price = entry_price - (stop_distance * float(config["takeProfitR"]))
    if stop_distance <= 0:
        return None
    shares, planned_risk, sizing_mode = position_size_for_config(config, equity=equity, entry_price=entry_price, stop_distance=stop_distance)
    if shares < 1:
        return None
    return {
        "side": side,
        "sessionDate": session_date,
        "entryHour": eastern_datetime_from_iso(str(candle["timestamp"])).strftime("%H:00"),
        "yearMonth": session_date[:7],
        "regime": vote_summary["regime"],
        "buyVotes": vote_summary["buyVotes"],
        "sellVotes": vote_summary["sellVotes"],
        "holdVotes": vote_summary["holdVotes"],
        "voteStrength": vote_summary["voteStrength"],
        "entryAt": candle["timestamp"],
        "entryPrice": round(entry_price, 4),
        "rawEntryPrice": raw_open,
        "shares": shares,
        "riskDollars": round(planned_risk, 2),
        "plannedRiskPerShare": round(stop_distance, 4),
        "stopPrice": round(stop_price, 4),
        "targetPrice": round(target_price, 4),
        "positionValue": round(entry_price * shares, 2),
        "positionSizingMode": sizing_mode,
        "riskConfig": config,
    }


def stop_target_or_time_exit(trade: dict, candle: dict) -> dict | None:
    config = dict(trade.get("riskConfig") or VOTING_ENSEMBLE_RISK_CONFIG)
    minute = candle_minutes_et(candle)
    force_close_minute = time_to_minutes(str(config["forceClose"]))
    if trade["side"] == "Long":
        if float(candle["low"]) <= float(trade["stopPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["stopPrice"]), "Stop loss")
        if float(candle["high"]) >= float(trade["targetPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["targetPrice"]), "Take profit")
    else:
        if float(candle["high"]) >= float(trade["stopPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["stopPrice"]), "Stop loss")
        if float(candle["low"]) <= float(trade["targetPrice"]):
            return close_risk_managed_trade(trade, candle["timestamp"], float(trade["targetPrice"]), "Take profit")
    if minute >= force_close_minute:
        return close_risk_managed_trade(trade, candle["timestamp"], float(candle["close"]), "Timed close")
    return None


def close_risk_managed_trade(trade: dict, exit_at: str, raw_exit_price: float, exit_reason: str) -> dict:
    config = dict(trade.get("riskConfig") or VOTING_ENSEMBLE_RISK_CONFIG)
    slippage = float(config["slippagePerShare"])
    exit_price = raw_exit_price - slippage if trade["side"] == "Long" else raw_exit_price + slippage
    direction = 1 if trade["side"] == "Long" else -1
    shares = int(trade["shares"])
    gross_pnl = round((exit_price - float(trade["entryPrice"])) * direction * shares, 2)
    expenses = estimate_trade_expenses(trade, exit_price)
    pnl = round(gross_pnl - expenses["total"], 2)
    account_return_percent = round((pnl / float(config["startingCapital"])) * 100, 4)
    position_return_percent = round((pnl / float(trade["positionValue"])) * 100, 4) if float(trade["positionValue"]) else 0
    return {
        **trade,
        "exitAt": exit_at,
        "exitPrice": round(exit_price, 4),
        "rawExitPrice": raw_exit_price,
        "exitReason": exit_reason,
        "grossPnl": gross_pnl,
        "expenses": expenses["total"],
        "expenseBreakdown": expenses,
        "pnl": pnl,
        "returnPercent": position_return_percent,
        "accountReturnPercent": account_return_percent,
        "rMultiple": round(pnl / float(trade["riskDollars"]), 2) if float(trade["riskDollars"]) else 0,
    }


def estimate_trade_expenses(trade: dict, exit_price: float) -> dict:
    config = dict(trade.get("riskConfig") or VOTING_ENSEMBLE_RISK_CONFIG)
    model = config.get("expenseModel", {})
    shares = int(trade["shares"])
    entry_notional = abs(float(trade["entryPrice"]) * shares)
    exit_notional = abs(exit_price * shares)
    sell_notional = exit_notional if trade["side"] == "Long" else entry_notional
    liquidity = shares * 2 * float(model.get("additionalLiquidityCostPerSharePerSide", 0))
    commission = shares * 2 * float(model.get("commissionPerSharePerSide", 0))
    sec_fee = sell_notional * float(model.get("secFeeRateOnSellNotional", 0))
    taf_fee = min(shares * float(model.get("finraTafPerSellShare", 0)), float(model.get("finraTafMaxPerTrade", 0)))
    total = round(liquidity + commission + sec_fee + taf_fee, 2)
    return {
        "total": total,
        "liquidity": round(liquidity, 2),
        "commission": round(commission, 2),
        "secFee": round(sec_fee, 2),
        "tafFee": round(taf_fee, 2),
    }


def current_trade_unrealized_pnl(trade: dict, raw_price: float) -> float:
    config = dict(trade.get("riskConfig") or VOTING_ENSEMBLE_RISK_CONFIG)
    slippage = float(config["slippagePerShare"])
    exit_price = raw_price - slippage if trade["side"] == "Long" else raw_price + slippage
    direction = 1 if trade["side"] == "Long" else -1
    return (exit_price - float(trade["entryPrice"])) * direction * int(trade["shares"])


def build_backtest_diagnostics(trades: list[dict], *, max_drawdown: float, timeframe: str, config: dict) -> dict:
    return {
        "byTimeframe": [
            diagnostic_row(
                timeframe,
                trades,
                extra={"maxDrawdown": round(max_drawdown, 2), "maxDrawdownPercent": round((max_drawdown / float(config["startingCapital"])) * 100, 2)},
            )
        ],
        "bySide": diagnostic_group(trades, "side"),
        "byHour": diagnostic_group(trades, "entryHour"),
        "byExitReason": diagnostic_group(trades, "exitReason"),
        "byVoteStrength": diagnostic_group(trades, "voteStrength"),
        "byRegime": diagnostic_group(trades, "regime"),
        "byYearMonth": diagnostic_group(trades, "yearMonth", limit=18),
        "byRMultiple": diagnostic_r_multiple_buckets(trades),
        "bySetting": [
            diagnostic_row(
                f"{config['riskPerTradePercent']}% risk / {config['maxDailyLossPercent']}% daily / {config['maxTradesPerDay']} trades",
                trades,
                extra={"maxDrawdown": round(max_drawdown, 2), "maxDrawdownPercent": round((max_drawdown / float(config["startingCapital"])) * 100, 2)},
            )
        ],
    }


def diagnostic_group(trades: list[dict], key: str, *, limit: int = 12) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for trade in trades:
        label = str(trade.get(key) or "NA")
        buckets.setdefault(label, []).append(trade)
    rows = [diagnostic_row(label, bucket) for label, bucket in buckets.items()]
    return sorted(rows, key=lambda row: abs(float(row["pnl"])), reverse=True)[:limit]


def diagnostic_r_multiple_buckets(trades: list[dict]) -> list[dict]:
    buckets = {
        "<= -1R": [],
        "-1R to 0R": [],
        "0R to +1R": [],
        ">= +1R": [],
    }
    for trade in trades:
        r_multiple = float(trade.get("rMultiple") or 0)
        if r_multiple <= -1:
            buckets["<= -1R"].append(trade)
        elif r_multiple < 0:
            buckets["-1R to 0R"].append(trade)
        elif r_multiple < 1:
            buckets["0R to +1R"].append(trade)
        else:
            buckets[">= +1R"].append(trade)
    return [diagnostic_row(label, bucket) for label, bucket in buckets.items()]


def diagnostic_row(label: str, bucket: list[dict], *, extra: dict | None = None) -> dict:
    pnl = round(sum(float(trade["pnl"]) for trade in bucket), 2)
    winners = len([trade for trade in bucket if float(trade["pnl"]) > 0])
    losers = len([trade for trade in bucket if float(trade["pnl"]) < 0])
    gross_profit = round(sum(float(trade["pnl"]) for trade in bucket if float(trade["pnl"]) > 0), 2)
    gross_loss = round(abs(sum(float(trade["pnl"]) for trade in bucket if float(trade["pnl"]) < 0)), 2)
    row = {
        "label": label,
        "trades": len(bucket),
        "pnl": pnl,
        "winRate": round((winners / len(bucket)) * 100, 1) if bucket else 0,
        "winners": winners,
        "losers": losers,
        "profitFactor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "averageR": round(sum(float(trade.get("rMultiple") or 0) for trade in bucket) / len(bucket), 2) if bucket else 0,
    }
    if extra:
        row.update(extra)
    return row


def historical_strategy_votes(history: list[dict], prior_close: float, *, timeframe: str = "") -> list[str]:
    fits = {item["name"]: item for item in historical_strategy_fits(history, prior_close, timeframe=timeframe)}
    raw_votes = [
        historical_strategy_signal("Multi-Timeframe Trend Alignment", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("First Pullback After Open", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Failed Breakout Strategy", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Liquidity Sweep Reversal", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Bollinger Band Reversion", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("ATR Overextension Reversion", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Relative Strength vs QQQ/IWM", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Market Breadth Momentum", history, prior_close, timeframe=timeframe),
        historical_strategy_signal("Economic Event Reaction Strategy", history, prior_close, timeframe=timeframe),
    ]
    allowed_votes = [
        vote
        for name, vote in zip(VOTING_STRATEGY_NAMES[:9], raw_votes, strict=True)
        if fits.get(name, {}).get("status") in {"Allowed", "Strong Fit"}
    ]
    buy_votes = allowed_votes.count("Buy")
    sell_votes = allowed_votes.count("Sell")
    ensemble_vote = "Hold"
    if fits.get("Ensemble Strategy Voting", {}).get("status") in {"Allowed", "Strong Fit"}:
        if buy_votes >= 3 and buy_votes > sell_votes:
            ensemble_vote = "Buy"
        elif sell_votes >= 3 and sell_votes > buy_votes:
            ensemble_vote = "Sell"
        allowed_votes.append(ensemble_vote)
    return allowed_votes


def historical_strategy_fits(history: list[dict], prior_close: float, *, timeframe: str = "") -> list[dict]:
    tags = historical_condition_tags(history, prior_close, timeframe=timeframe)
    scored = []
    for strategy in HISTORICAL_STRATEGY_FIT_CATALOG:
        matched = [tag for tag in strategy["tags"] if tag in tags]
        blocked = [tag for tag in strategy["blocks"] if tag in tags]
        raw = 44 + (len(matched) * 11) - (len(blocked) * 18)
        confidence_bonus = min(18, max(4, len(tags) * 1.8))
        score = max(0, min(99, round(raw + confidence_bonus)))
        if blocked and score > 64:
            score = 64
        status = "Strong Fit" if score >= 78 and not blocked else "Allowed" if score >= 62 and not blocked else "Watch" if score >= 45 else "Avoid"
        classification = STRATEGY_CLASSIFICATION.get(strategy["name"], {"role": "directional", "family": "uncategorized"})
        scored.append(
            {
                "name": strategy["name"],
                "role": classification["role"],
                "family": classification["family"],
                "strategy_family": classification["family"],
                "status": status,
                "score": score,
                "matches": matched[:3],
                "risks": blocked[:3],
            }
        )
    return sorted(scored, key=lambda item: item["score"], reverse=True)


def historical_condition_tags(history: list[dict], prior_close: float, *, timeframe: str = "") -> set[str]:
    closes = [float(candle["close"]) for candle in history]
    latest = history[-1]
    latest_close = float(latest["close"])
    session_open = float(history[0]["open"])
    period_open = float(latest["open"])
    reference_open = period_open if timeframe in {"1Hour", "1Day", "1Week"} else session_open
    sma20 = simple_moving_average(closes, 20)
    sma50 = simple_moving_average(closes, 50)
    vwap = session_vwap_value(history)
    opening_range = opening_range_values(history, 15)
    prior_range = history[-21:-1]
    prior_high = max((float(candle["high"]) for candle in prior_range), default=float(latest["high"]))
    prior_low = min((float(candle["low"]) for candle in prior_range), default=float(latest["low"]))
    volumes = [float(candle["volume"]) for candle in history]
    average_volume = simple_moving_average(volumes, min(20, len(volumes))) or float(latest["volume"])
    gap_percent = ((reference_open - prior_close) / prior_close) * 100 if prior_close else 0
    atr = average_true_range(history, min(14, len(history) - 1))
    bollinger = bollinger_bands(closes, 20, 2.0)
    recent_return = ((latest_close - closes[-min(20, len(closes))]) / closes[-min(20, len(closes))]) * 100 if len(closes) > 1 else 0
    latest_volume = float(latest["volume"])
    tags: set[str] = set()

    if sma20 is not None and sma50 is not None and sma20 > sma50 and latest_close > sma20 and latest_close > vwap:
        tags.update(["trend-up", "above-vwap", "momentum"])
    elif sma20 is not None and sma50 is not None and sma20 < sma50 and latest_close < sma20 and latest_close < vwap:
        tags.update(["trend-down", "below-vwap", "momentum"])
    elif abs((latest_close - vwap) / vwap) < 0.0015:
        tags.update(["vwap-chop", "mean-reversion"])

    if latest_close > opening_range["high"] or latest_close < opening_range["low"]:
        tags.add("opening-range-active")
    if abs(gap_percent) >= 0.15:
        tags.add("event-reaction")
        tags.add("gap-up" if gap_percent > 0 else "gap-down")
    if latest_volume > average_volume * 1.2:
        tags.add("volume-expansion")
    if latest_volume < average_volume * 0.65:
        tags.add("low-volume")
    if latest_close > prior_high or latest_close < prior_low:
        tags.add("volume-breakout")
    if is_failed_breakout(history, prior_high, prior_low):
        tags.update(["failed-breakout", "mean-reversion"])
    if is_liquidity_sweep(history, prior_high, prior_low, average_volume):
        tags.update(["liquidity-sweep", "mean-reversion"])
    if bollinger and (latest_close > bollinger["upper"] or latest_close < bollinger["lower"]):
        tags.update(["bollinger-overextension", "mean-reversion"])
    if atr and sma20 and abs(latest_close - sma20) > atr * 1.25:
        tags.update(["atr-overextension", "mean-reversion"])
    if recent_return > 1.2 and latest_close > vwap:
        tags.update(["relative-strength-up", "breadth-up"])
    elif recent_return < -1.2 and latest_close < vwap:
        tags.update(["relative-strength-down", "breadth-down"])
    if sma20 is not None and atr and atr / latest_close < 0.0035:
        tags.add("range-compression")
    if historical_regime_label(history) == "VWAP Chop":
        tags.add("vwap-chop")
    if historical_regime_label(history) == "Mixed" and latest_volume < average_volume:
        tags.add("cash-filter")
    return tags


def historical_strategy_signal(name: str, history: list[dict], prior_close: float, *, timeframe: str = "") -> str:
    closes = [float(candle["close"]) for candle in history]
    latest = history[-1]
    latest_close = float(latest["close"])
    session_open = float(history[0]["open"])
    period_open = float(latest["open"])
    reference_open = period_open if timeframe in {"1Hour", "1Day", "1Week"} else session_open
    sma20 = simple_moving_average(closes, 20)
    sma50 = simple_moving_average(closes, 50)
    rsi = relative_strength_index(closes, 14)
    vwap = session_vwap_value(history)
    opening_range = opening_range_values(history, 15)
    prior_range = history[-21:-1]
    prior_high = max((float(candle["high"]) for candle in prior_range), default=float(latest["high"]))
    prior_low = min((float(candle["low"]) for candle in prior_range), default=float(latest["low"]))
    volumes = [float(candle["volume"]) for candle in history]
    average_volume = simple_moving_average(volumes, min(20, len(volumes))) or float(latest["volume"])
    gap_percent = ((reference_open - prior_close) / prior_close) * 100 if prior_close else 0
    atr = average_true_range(history, min(14, len(history) - 1))
    bollinger = bollinger_bands(closes, 20, 2.0)
    recent_return = ((latest_close - closes[-min(20, len(closes))]) / closes[-min(20, len(closes))]) * 100 if len(closes) > 1 else 0

    if name == "Multi-Timeframe Trend Alignment":
        if sma20 is not None and sma50 is not None and sma20 > sma50 and latest_close > sma20 and latest_close > vwap:
            return "Buy"
        if sma20 is not None and sma50 is not None and sma20 < sma50 and latest_close < sma20 and latest_close < vwap:
            return "Sell"
    elif name == "First Pullback After Open":
        if sma20 is not None and sma50 is not None and sma20 > sma50 and opening_range["high"] < latest_close <= sma20 * 1.002:
            return "Buy"
        if sma20 is not None and sma50 is not None and sma20 < sma50 and opening_range["low"] > latest_close >= sma20 * 0.998:
            return "Sell"
    elif name == "Failed Breakout Strategy":
        if is_failed_breakout(history, prior_high, prior_low):
            if float(latest["high"]) > prior_high and latest_close < prior_high:
                return "Sell"
            if float(latest["low"]) < prior_low and latest_close > prior_low:
                return "Buy"
    elif name == "Liquidity Sweep Reversal":
        if is_liquidity_sweep(history, prior_high, prior_low, average_volume):
            if float(latest["high"]) > prior_high and latest_close < prior_high:
                return "Sell"
            if float(latest["low"]) < prior_low and latest_close > prior_low:
                return "Buy"
    elif name == "Bollinger Band Reversion":
        if bollinger and rsi is not None:
            if latest_close < bollinger["lower"] and rsi < 45:
                return "Buy"
            if latest_close > bollinger["upper"] and rsi > 55:
                return "Sell"
    elif name == "ATR Overextension Reversion":
        if atr and sma20:
            if latest_close < sma20 - (atr * 1.25):
                return "Buy"
            if latest_close > sma20 + (atr * 1.25):
                return "Sell"
    elif name == "Relative Strength vs QQQ/IWM":
        if recent_return > 1.2 and latest_close > vwap:
            return "Buy"
        if recent_return < -1.2 and latest_close < vwap:
            return "Sell"
    elif name == "Market Breadth Momentum":
        if sma20 is not None and sma50 is not None and recent_return > 0.6 and sma20 > sma50 and latest_close > vwap:
            return "Buy"
        if sma20 is not None and sma50 is not None and recent_return < -0.6 and sma20 < sma50 and latest_close < vwap:
            return "Sell"
    elif name == "Economic Event Reaction Strategy":
        if gap_percent > 0.15 and latest_close > opening_range["high"]:
            return "Buy"
        if gap_percent < -0.15 and latest_close < opening_range["low"]:
            return "Sell"
    return "Hold"


def is_failed_breakout(history: list[dict], prior_high: float, prior_low: float) -> bool:
    latest = history[-1]
    latest_close = float(latest["close"])
    return (float(latest["high"]) > prior_high and latest_close < prior_high) or (float(latest["low"]) < prior_low and latest_close > prior_low)


def is_liquidity_sweep(history: list[dict], prior_high: float, prior_low: float, average_volume: float) -> bool:
    latest = history[-1]
    latest_close = float(latest["close"])
    volume_expanded = float(latest["volume"]) > average_volume * 1.1
    swept_high = float(latest["high"]) > prior_high and latest_close < prior_high
    swept_low = float(latest["low"]) < prior_low and latest_close > prior_low
    return volume_expanded and (swept_high or swept_low)


def bollinger_bands(values: list[float], period: int, deviations: float) -> dict[str, float] | None:
    if len(values) < period:
        return None
    sample = values[-period:]
    average = sum(sample) / period
    variance = sum((value - average) ** 2 for value in sample) / period
    band_width = (variance**0.5) * deviations
    return {"middle": average, "upper": average + band_width, "lower": average - band_width}


def average_true_range(candles: list[dict], period: int) -> float | None:
    if period <= 0 or len(candles) <= period:
        return None
    ranges: list[float] = []
    sample = candles[-(period + 1) :]
    for index in range(1, len(sample)):
        high = float(sample[index]["high"])
        low = float(sample[index]["low"])
        previous_close = float(sample[index - 1]["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges) / len(ranges) if ranges else None


def simple_moving_average(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    sample = values[-period:]
    return sum(sample) / period


def relative_strength_index(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    sample = values[-(period + 1) :]
    gains = 0.0
    losses = 0.0
    for index in range(1, len(sample)):
        change = sample[index] - sample[index - 1]
        if change >= 0:
            gains += change
        else:
            losses += abs(change)
    if losses == 0:
        return 100
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def session_vwap_value(candles: list[dict]) -> float:
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    for candle in candles:
        volume = max(0, float(candle["volume"]))
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        cumulative_price_volume += typical * volume
        cumulative_volume += volume
    return cumulative_price_volume / cumulative_volume if cumulative_volume else float(candles[-1]["close"])


def opening_range_values(candles: list[dict], count: int) -> dict:
    opening = candles[: min(count, len(candles))]
    return {
        "high": max(float(candle["high"]) for candle in opening),
        "low": min(float(candle["low"]) for candle in opening),
    }


def close_backtest_trade(trade: dict, exit_at: str, exit_price: float) -> dict:
    direction = 1 if trade["side"] == "Long" else -1
    pnl = round((exit_price - float(trade["entryPrice"])) * direction, 2)
    return {
        "side": trade["side"],
        "entryAt": trade["entryAt"],
        "exitAt": exit_at,
        "entryPrice": float(trade["entryPrice"]),
        "exitPrice": float(exit_price),
        "pnl": pnl,
        "returnPercent": round((pnl / float(trade["entryPrice"])) * 100, 2),
    }


def aggregate_candles(candles: list[dict], *, timeframe: str, minutes: int) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for candle in candles:
        timestamp = datetime.fromisoformat(candle["timestamp"].replace("Z", "+00:00")).astimezone(UTC)
        bucket_minute = (timestamp.minute // minutes) * minutes
        bucket = timestamp.replace(minute=bucket_minute, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        buckets.setdefault(bucket, []).append(candle)

    aggregated = []
    for timestamp, bucket in sorted(buckets.items()):
        first = bucket[0]
        last = bucket[-1]
        volume = sum(int(candle.get("volume") or 0) for candle in bucket)
        trade_count_values = [candle.get("trade_count") for candle in bucket if candle.get("trade_count") is not None]
        trade_count = sum(int(value) for value in trade_count_values) if trade_count_values else None
        vwap_numerator = sum(float(candle.get("vwap") or candle["close"]) * int(candle.get("volume") or 0) for candle in bucket)
        aggregated.append(
            {
                "provider": "derived",
                "feed": first["feed"],
                "symbol": first["symbol"],
                "timeframe": timeframe,
                "timestamp": timestamp,
                "open": first["open"],
                "high": max(float(candle["high"]) for candle in bucket),
                "low": min(float(candle["low"]) for candle in bucket),
                "close": last["close"],
                "volume": volume,
                "trade_count": trade_count,
                "vwap": (vwap_numerator / volume) if volume else None,
            }
        )
    return aggregated


def aggregate_weekly_candles(candles: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for candle in candles:
        session_date = datetime.fromisoformat(candle_session_date(candle)).date()
        week_start = session_date - timedelta(days=session_date.weekday())
        buckets.setdefault(week_start.isoformat(), []).append(candle)

    aggregated = []
    for _, bucket in sorted(buckets.items()):
        first = bucket[0]
        last = bucket[-1]
        volume = sum(int(candle.get("volume") or 0) for candle in bucket)
        trade_count_values = [candle.get("trade_count") for candle in bucket if candle.get("trade_count") is not None]
        trade_count = sum(int(value) for value in trade_count_values) if trade_count_values else None
        vwap_numerator = sum(float(candle.get("vwap") or candle["close"]) * int(candle.get("volume") or 0) for candle in bucket)
        aggregated.append(
            {
                "provider": "derived",
                "feed": first["feed"],
                "symbol": first["symbol"],
                "timeframe": "1Week",
                "timestamp": first["timestamp"],
                "open": first["open"],
                "high": max(float(candle["high"]) for candle in bucket),
                "low": min(float(candle["low"]) for candle in bucket),
                "close": last["close"],
                "volume": volume,
                "trade_count": trade_count,
                "vwap": (vwap_numerator / volume) if volume else None,
            }
        )
    return aggregated


def enrich_backtest_candles(candles: list[dict], *, previous_close: float | None) -> list[dict]:
    enriched = []
    cumulative_price_volume = 0.0
    cumulative_volume = 0
    day_open = float(candles[0]["open"]) if candles else None
    opening_15_high = None
    opening_15_low = None
    opening_30_high = None
    opening_30_low = None

    for index, candle in enumerate(candles):
        timestamp_et = eastern_datetime_from_iso(candle["timestamp"])
        volume = int(candle.get("volume") or 0)
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        open_price = float(candle["open"])
        typical = (high + low + close) / 3
        cumulative_price_volume += typical * volume
        cumulative_volume += volume
        if index < 15:
            opening_15_high = high if opening_15_high is None else max(opening_15_high, high)
            opening_15_low = low if opening_15_low is None else min(opening_15_low, low)
        if index < 30:
            opening_30_high = high if opening_30_high is None else max(opening_30_high, high)
            opening_30_low = low if opening_30_low is None else min(opening_30_low, low)
        minutes_from_open = int((timestamp_et.replace(tzinfo=None) - timestamp_et.replace(hour=9, minute=30, second=0, microsecond=0, tzinfo=None)).total_seconds() / 60)
        session_vwap = (cumulative_price_volume / cumulative_volume) if cumulative_volume else None
        enriched.append(
            {
                **candle,
                "timestampEt": timestamp_et.isoformat(),
                "sessionDate": timestamp_et.date().isoformat(),
                "sessionTime": timestamp_et.strftime("%H:%M"),
                "isRegularSession": 0 <= minutes_from_open <= 390,
                "minutesFromOpen": minutes_from_open,
                "barIndex": index,
                "previousClose": previous_close,
                "dayOpen": day_open,
                "gapPercent": round(((day_open - previous_close) / previous_close) * 100, 4)
                if previous_close and day_open
                else None,
                "range": round(high - low, 4),
                "body": round(close - open_price, 4),
                "bodyPercentOfRange": round(abs(close - open_price) / max(high - low, 0.0001), 4),
                "direction": "up" if close > open_price else "down" if close < open_price else "flat",
                "typicalPrice": round(typical, 4),
                "dollarVolume": round(close * volume, 2),
                "cumulativeVolume": cumulative_volume,
                "sessionVwap": round(session_vwap, 4) if session_vwap else None,
                "distanceFromSessionVwapPercent": round(((close - session_vwap) / session_vwap) * 100, 4)
                if session_vwap
                else None,
                "opening15High": opening_15_high,
                "opening15Low": opening_15_low,
                "opening30High": opening_30_high,
                "opening30Low": opening_30_low,
            }
        )
    return enriched


def coverage_summary(candles: list[dict]) -> dict:
    if not candles:
        return {"count": 0, "start": None, "end": None}
    return {
        "count": len(candles),
        "start": candles[0]["timestamp"],
        "end": candles[-1]["timestamp"],
    }


def write_json(path: Path, data: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    return str(path)


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def backtest_field_manifest() -> dict:
    return {
        "raw": ["timestamp", "open", "high", "low", "close", "volume", "trade_count", "vwap", "provider", "feed", "symbol", "timeframe"],
        "session": ["timestampEt", "sessionDate", "sessionTime", "isRegularSession", "minutesFromOpen", "barIndex"],
        "context": ["previousClose", "dayOpen", "gapPercent", "opening15High", "opening15Low", "opening30High", "opening30Low"],
        "derived": ["range", "body", "bodyPercentOfRange", "direction", "typicalPrice", "dollarVolume", "cumulativeVolume", "sessionVwap", "distanceFromSessionVwapPercent"],
    }


def fallback_news_items(symbol: str, limit: int, now: datetime) -> list[dict]:
    return [
        {
            **item,
            "id": f"{item['id']}-{symbol}",
            "publishedAt": (now - timedelta(minutes=30 * index)).isoformat(),
            "symbols": sorted(set([symbol, *item["symbols"]])),
        }
        for index, item in enumerate(SPY_NEWS_FALLBACK[:limit])
    ]


def news_source_statuses(has_alpaca_credentials: bool) -> list[dict]:
    return [
        {
            "name": "Alpaca News",
            "kind": "websocket/rest",
            "status": "configured" if has_alpaca_credentials else "unconfigured",
            "note": "Uses Alpaca news access for SPY headlines.",
        },
        {
            "name": "Finnhub WebSocket News",
            "kind": "websocket",
            "status": "not_configured",
            "note": "Stock news streaming generally requires premium access.",
        },
        {
            "name": "Yahoo Finance",
            "kind": "website",
            "status": "manual_fallback",
            "note": "No official reliable streaming API is configured.",
        },
        {
            "name": "Nasdaq Trader RSS",
            "kind": "rss",
            "status": "available_for_alerts",
            "note": "Used for exchange alerts; not a full SPY market news feed.",
        },
        {
            "name": "SEC RSS / filings",
            "kind": "rss/api",
            "status": "available_for_filings",
            "note": "Useful for filings and company events, not broad SPY headlines.",
        },
        {
            "name": "Benzinga API",
            "kind": "api",
            "status": "not_configured",
            "note": "Professional real-time news source; API key/plan required.",
        },
        {
            "name": "Polygon / Massive News API",
            "kind": "api",
            "status": "not_configured",
            "note": "Plan-dependent news API access; key not configured.",
        },
    ]


def parse_trade_halt_rss(payload: str) -> list[dict]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []

    events = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="Trading halt alert").strip()
        description = item.findtext("description", default="").strip()
        published = item.findtext("pubDate", default="").strip()
        published_at = parse_rss_date(published)
        fields = parse_halt_description(description)
        reason_code = fields.get("Reason Code", "")
        category = "luld" if reason_code.upper() == "LUDP" else "halt"
        symbol = fields.get("Issue Symbol", title.split()[0]).upper() if title else "N/A"
        events.append(
            {
                "id": f"{symbol}-{published_at or title}",
                "category": category,
                "symbol": symbol,
                "title": title,
                "detail": halt_detail(fields) or strip_html(description) or "Current trading halt notice",
                "publishedAt": published_at,
                "source": "Nasdaq Trader",
            },
        )

    return events


class TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[str] = []
        self._active_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"th", "td"}:
            self._active_cell = []

    def handle_data(self, data: str) -> None:
        if self._active_cell is not None:
            text = " ".join(data.split())
            if text:
                self._active_cell.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"th", "td"} and self._active_cell is not None:
            self.cells.append(" ".join(self._active_cell).strip())
            self._active_cell = None


def parse_halt_description(value: str) -> dict[str, str]:
    parser = TableTextParser()
    parser.feed(value)
    cells = parser.cells
    if len(cells) < 2:
        return {}
    known_headers = [
        "Halt Date",
        "Halt Time",
        "Issue Symbol",
        "Issue Name",
        "Market",
        "Reason Code",
        "Pause Threshold Price",
        "Resumption Date",
        "Resumption Quote Time",
        "Resumption Trade Time",
    ]
    if cells[0] != "Halt Date" and len(cells) >= len(known_headers):
        return {header: cells[index] for index, header in enumerate(known_headers)}
    midpoint = len(cells) // 2
    headers = cells[:midpoint]
    values = cells[midpoint:]
    return {header: values[index] for index, header in enumerate(headers) if index < len(values)}


def halt_detail(fields: dict[str, str]) -> str:
    if not fields:
        return ""
    parts = [
        fields.get("Issue Name", ""),
        fields.get("Market", ""),
        f"Code {fields['Reason Code']}" if fields.get("Reason Code") else "",
        f"Halt {fields.get('Halt Date', '').strip()} {fields.get('Halt Time', '').strip()} ET".strip(),
    ]
    resumption = fields.get("Resumption Trade Time") or fields.get("Resumption Quote Time")
    if resumption:
        parts.append(f"Resumes {fields.get('Resumption Date', '').strip()} {resumption.strip()} ET".strip())
    return " - ".join(part for part in parts if part and part != "Halt ET")


def strip_html(value: str) -> str:
    parser = TableTextParser()
    parser.feed(value)
    return " ".join(parser.cells)


def parse_rss_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


@app.get("/api/circuit-breakers")
def circuit_breakers(symbol: str = Query("SPY", min_length=1, max_length=12)) -> dict:
    now = datetime.now(UTC)
    normalized_symbol = symbol.upper()
    latest_daily = store.latest(symbol=normalized_symbol, timeframe="1Day", feed="iex", limit=1)
    reference_close = latest_daily[-1]["close"] if latest_daily else None
    reference_date = latest_daily[-1]["timestamp"] if latest_daily else None

    return {
        "source": "NYSE Market-Wide Circuit Breaker rules",
        "updatedAt": now.isoformat(),
        "referenceIndex": "S&P 500 Index",
        "referenceNote": f"{normalized_symbol} proxy levels use latest cached daily close; official MWCB levels use the prior S&P 500 Index close.",
        "referenceSymbol": normalized_symbol,
        "referenceClose": reference_close,
        "referenceDate": reference_date,
        "rules": [
            {
                **rule,
                "referenceValue": round(reference_close * (1 - (rule["percent"] / 100)), 2)
                if reference_close
                else None,
            }
            for rule in CIRCUIT_BREAKER_RULES
        ],
    }


@app.get("/api/moc-imbalance")
def moc_imbalance(symbol: str = Query("SPY", min_length=1, max_length=12)) -> dict:
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    market_open = eastern_now.replace(hour=9, minute=30, second=0, microsecond=0)
    imbalance_start = eastern_now.replace(hour=15, minute=50, second=0, microsecond=0)
    market_close = eastern_now.replace(hour=16, minute=0, second=0, microsecond=0)
    status = (
        "active_window"
        if imbalance_start <= eastern_now <= market_close
        else "pre_window"
        if market_open <= eastern_now < imbalance_start
        else "closed"
    )
    return {
        "source": "Closing auction imbalance feed",
        "updatedAt": now.isoformat(),
        "symbol": symbol.upper(),
        "status": status,
        "auction": "closing",
        "window": {
            "start": "15:50 ET",
            "end": "16:00 ET",
            "updateFrequency": "Every 5 seconds when a live imbalance feed is configured",
        },
        "fields": MOC_IMBALANCE_FIELDS,
        "latest": None,
        "warning": "No live MOC/NOII imbalance feed is configured. Connect an exchange imbalance feed to populate live updates.",
    }


@app.get("/api/vix-risk")
async def vix_risk() -> dict:
    now = datetime.now(UTC)
    quote = None
    warning = ""
    try:
        async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
            response = await client.get(VIX_QUOTE_CSV_URL)
            response.raise_for_status()
        quote = parse_vix_quote(response.text)
    except httpx.HTTPError as exc:
        warning = str(exc)

    current_value = quote["last"] if quote else None
    active_level = vix_level(current_value) if current_value is not None else None
    return {
        "source": "Stooq delayed quote; VIX methodology by Cboe",
        "updatedAt": now.isoformat(),
        "symbol": "VIX",
        "quote": quote,
        "activeLevel": active_level,
        "levels": VIX_RISK_LEVELS,
        "warning": warning if warning else ("" if quote else "VIX quote unavailable"),
    }


@app.get("/api/es-snapshot")
async def es_snapshot() -> dict:
    now = datetime.now(UTC)
    eastern_now = now.astimezone(eastern_tz_for_date(now.year, now.month, now.day))
    quote = None
    warning = ""
    try:
        async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
            response = await client.get(ES_QUOTE_CSV_URL)
            response.raise_for_status()
        quote = parse_es_quote(response.text)
    except httpx.HTTPError as exc:
        warning = str(exc)

    change_points = None
    change_percent = None
    active_level = None
    if quote and quote["last"] is not None and quote["open"] not in {None, 0}:
        change_points = round(quote["last"] - quote["open"], 2)
        change_percent = round((change_points / quote["open"]) * 100, 2)
        active_level = es_direction_level(change_percent)

    return {
        "source": "Stooq delayed ES futures quote; product reference CME Group",
        "updatedAt": now.isoformat(),
        "symbol": "ES",
        "session": es_session_label(eastern_now),
        "quote": quote,
        "changePoints": change_points,
        "changePercent": change_percent,
        "activeLevel": active_level,
        "levels": ES_DIRECTION_LEVELS,
        "warning": warning if warning else ("" if quote else "ES futures quote unavailable"),
    }


def parse_vix_quote(payload: str) -> dict | None:
    rows = [row.strip().split(",") for row in payload.splitlines() if row.strip()]
    if len(rows) < 2:
        return None
    headers = rows[0]
    values = rows[1]
    data = {header: values[index] for index, header in enumerate(headers) if index < len(values)}
    close = parse_float(data.get("Close"))
    open_price = parse_float(data.get("Open"))
    high = parse_float(data.get("High"))
    low = parse_float(data.get("Low"))
    if close is None:
        return None
    return {
        "last": close,
        "open": open_price,
        "high": high,
        "low": low,
        "date": data.get("Date"),
        "time": data.get("Time"),
    }


def parse_es_quote(payload: str) -> dict | None:
    rows = [row.strip().split(",") for row in payload.splitlines() if row.strip()]
    if len(rows) < 2:
        return None
    headers = rows[0]
    values = rows[1]
    data = {header: values[index] for index, header in enumerate(headers) if index < len(values)}
    last = parse_float(data.get("Close"))
    open_price = parse_float(data.get("Open"))
    high = parse_float(data.get("High"))
    low = parse_float(data.get("Low"))
    if last is None:
        return None
    return {
        "last": last,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": parse_float(data.get("Volume")),
        "date": data.get("Date"),
        "time": data.get("Time"),
    }


def parse_float(value: str | None) -> float | None:
    if not value or value == "N/D":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def vix_level(value: float) -> dict:
    for level in VIX_RISK_LEVELS:
        max_value = level["max"]
        if value >= level["min"] and (max_value is None or value < max_value):
            return level
    return VIX_RISK_LEVELS[-1]


def es_direction_level(value: float) -> dict:
    for level in ES_DIRECTION_LEVELS:
        min_value = level["minPercent"]
        max_value = level["maxPercent"]
        if (min_value is None or value >= min_value) and (max_value is None or value < max_value):
            return level
    return ES_DIRECTION_LEVELS[2]


def es_session_label(eastern_now: datetime) -> str:
    market_open = eastern_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = eastern_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if market_open <= eastern_now <= market_close:
        return "regular"
    if eastern_now < market_open:
        return "premarket"
    return "overnight"


@app.get("/api/market-context")
async def market_context(
    symbol: str = Query("SPY", min_length=1, max_length=12),
    feed: Literal["iex", "sip", "otc"] = "iex",
    refresh: bool = True,
    as_of: str | None = None,
) -> dict:
    normalized_symbol = symbol.upper()
    should_refresh = refresh and as_of is None
    daily = await _context_bars(
        symbol=normalized_symbol,
        feed=feed,
        timeframe="1Day",
        limit=300,
        refresh=should_refresh,
        as_of=as_of,
    )
    intraday = await _context_bars(
        symbol=normalized_symbol,
        feed=feed,
        timeframe="1Min",
        limit=1000,
        refresh=should_refresh,
        as_of=as_of,
    )
    return compute_market_context(normalized_symbol, daily, intraday)


async def _context_bars(
    *,
    symbol: str,
    feed: Literal["iex", "sip", "otc"],
    timeframe: Literal["1Min", "1Day"],
    limit: int,
    refresh: bool,
    as_of: str | None,
) -> list[dict]:
    cached = (
        store.latest_until(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit, end=as_of)
        if as_of
        else store.latest(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit)
    )
    if cached and not refresh:
        return cached

    now = datetime.now(UTC)
    lookback = timedelta(days=900) if timeframe == "1Day" else timedelta(days=10)
    try:
        fresh = await alpaca.get_bars(
            symbol=symbol,
            timeframe=timeframe,
            feed=feed,
            limit=limit,
            start=(now - lookback).isoformat().replace("+00:00", "Z"),
            end=now.isoformat().replace("+00:00", "Z"),
            sort="asc",
        )
    except httpx.HTTPError:
        return cached or demo_bars(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit)

    store.upsert_many(fresh)
    return fresh or cached
