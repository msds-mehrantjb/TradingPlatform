from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


TICK_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "microstructure"


def safe_symbol(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "-", value.upper()).strip("-") or "UNKNOWN"


def tick_day_path(symbol: str, feed: str, day: str, name: str) -> Path:
    return TICK_DATA_DIR / safe_symbol(symbol) / str(feed).lower() / day / name


def append_quote_trade_ticks(*, symbol: str, feed: str, quotes: list[dict[str, Any]] | None = None, trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    written: dict[str, int] = {"quotes": 0, "trades": 0}
    for kind, rows in (("quotes", quotes or []), ("trades", trades or [])):
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            timestamp = str(row.get("timestamp") or "")
            if len(timestamp) < 10:
                continue
            normalized = {**row, "symbol": safe_symbol(str(row.get("symbol") or symbol)), "feed": str(row.get("feed") or feed).lower()}
            by_day.setdefault(timestamp[:10], []).append(normalized)
        for day, day_rows in by_day.items():
            path = tick_day_path(symbol, feed, day, f"{kind}.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = read_jsonl(path) if path.exists() else []
            merged = dedupe_ticks([*existing, *day_rows])
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in merged), encoding="utf-8")
            written[kind] += len(day_rows)
    return {
        "status": "recorded",
        "symbol": safe_symbol(symbol),
        "feed": str(feed).lower(),
        "quotes": written["quotes"],
        "trades": written["trades"],
        "activationPolicy": "tick_data_is_passive_until_paper_or_live_model_promotion",
    }


def load_quote_trade_ticks(
    *,
    symbol: str,
    feed: str,
    start: str | datetime,
    end: str | datetime,
) -> dict[str, list[dict[str, Any]]]:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if start_dt is None or end_dt is None or end_dt < start_dt:
        return {"quotes": [], "trades": [], "events": []}
    quotes: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for day in days_between(start_dt, end_dt):
        quotes.extend(read_jsonl(tick_day_path(symbol, feed, day, "quotes.jsonl")))
        trades.extend(read_jsonl(tick_day_path(symbol, feed, day, "trades.jsonl")))
    quotes = [row for row in quotes if timestamp_between(row.get("timestamp"), start_dt, end_dt)]
    trades = [row for row in trades if timestamp_between(row.get("timestamp"), start_dt, end_dt)]
    return {"quotes": sorted(quotes, key=tick_sort_key), "trades": sorted(trades, key=tick_sort_key), "events": merged_quote_trade_events(quotes, trades)}


def merged_quote_trade_events(quotes: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for trade in trades:
        price = optional_float(trade.get("price"))
        if price is not None:
            events.append({"type": "trade", "timestamp": trade.get("timestamp"), "price": price, "raw": trade})
    for quote in quotes:
        bid = optional_float(quote.get("bid_price") or quote.get("bidPrice") or quote.get("bid"))
        ask = optional_float(quote.get("ask_price") or quote.get("askPrice") or quote.get("ask"))
        if bid is not None and ask is not None and ask >= bid:
            events.append(
                {
                    "type": "quote",
                    "timestamp": quote.get("timestamp"),
                    "price": (bid + ask) / 2.0,
                    "bid": bid,
                    "ask": ask,
                    "spread": ask - bid,
                    "raw": quote,
                }
            )
    return sorted(events, key=tick_sort_key)


def first_barrier_hit_from_ticks(
    ticks: dict[str, list[dict[str, Any]]] | list[dict[str, Any]],
    *,
    start: str | datetime,
    end: str | datetime,
    target: float,
    stop: float,
) -> dict[str, Any]:
    events = ticks.get("events", []) if isinstance(ticks, dict) else ticks
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return {"status": "unavailable", "label": None, "reason": "tick_data.invalid_window"}
    for event in sorted(events, key=tick_sort_key):
        timestamp = parse_timestamp(event.get("timestamp"))
        price = optional_float(event.get("price"))
        if timestamp is None or price is None or timestamp < start_dt or timestamp >= end_dt:
            continue
        if price >= target:
            return {"status": "resolved", "label": 1, "hit": "target", "timestamp": event.get("timestamp"), "price": price, "source": event.get("type")}
        if price <= stop:
            return {"status": "resolved", "label": -1, "hit": "stop", "timestamp": event.get("timestamp"), "price": price, "source": event.get("type")}
    return {"status": "unresolved", "label": None, "reason": "tick_data.no_barrier_hit"}


def quote_snapshot_at_or_before(quotes: list[dict[str, Any]], timestamp: str | datetime) -> dict[str, Any] | None:
    target = parse_timestamp(timestamp)
    if target is None:
        return None
    selected: dict[str, Any] | None = None
    for quote in sorted(quotes, key=tick_sort_key):
        quote_time = parse_timestamp(quote.get("timestamp"))
        if quote_time is None or quote_time > target:
            continue
        selected = quote
    return selected


def quote_mid_and_spread(quotes: list[dict[str, Any]], timestamp: str | datetime) -> dict[str, float | None]:
    quote = quote_snapshot_at_or_before(quotes, timestamp)
    if not quote:
        return {"mid": None, "spread": None}
    bid = optional_float(quote.get("bid_price") or quote.get("bidPrice") or quote.get("bid"))
    ask = optional_float(quote.get("ask_price") or quote.get("askPrice") or quote.get("ask"))
    if bid is None or ask is None or ask < bid:
        return {"mid": None, "spread": None}
    return {"mid": (bid + ask) / 2.0, "spread": ask - bid}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def dedupe_ticks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=tick_sort_key):
        key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def days_between(start: datetime, end: datetime) -> list[str]:
    cursor = start.date()
    final = end.date()
    days: list[str] = []
    while cursor <= final:
        days.append(cursor.isoformat())
        cursor = cursor + timedelta(days=1)
    return days


def timestamp_between(value: Any, start: datetime, end: datetime) -> bool:
    parsed = parse_timestamp(value)
    return parsed is not None and start <= parsed <= end


def tick_sort_key(row: dict[str, Any]) -> float:
    parsed = parse_timestamp(row.get("timestamp"))
    return parsed.timestamp() if parsed else 0.0


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "")
        if not text:
            return None
        text = re.sub(r"(\.\d{6})\d+(Z|[+-]\d\d:\d\d)$", r"\1\2", text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


__all__ = [
    "TICK_DATA_DIR",
    "append_quote_trade_ticks",
    "first_barrier_hit_from_ticks",
    "load_quote_trade_ticks",
    "quote_mid_and_spread",
]
