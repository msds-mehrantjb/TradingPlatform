from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx

from .alpaca import eastern_timezone_for_date, nyse_holiday_name
from .config import get_settings


MICROSTRUCTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "microstructure"
DEFAULT_SYMBOL = "SPY"
DEFAULT_START_DATE = "2026-01-01"
DEFAULT_FEED = "iex"
PAGE_LIMIT = 10_000


@dataclass
class MinuteBucket:
    quote_count: int = 0
    spread_sum: float = 0.0
    min_spread: float | None = None
    max_spread: float | None = None
    bid_size_sum: float = 0.0
    ask_size_sum: float = 0.0
    mid_sum: float = 0.0
    last_bid: float | None = None
    last_ask: float | None = None
    last_mid: float | None = None
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    unknown_volume: float = 0.0
    trade_count: int = 0
    trade_volume: float = 0.0

    def add_quote(self, bid: float, ask: float, bid_size: float, ask_size: float) -> None:
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        spread = ask - bid
        mid = (bid + ask) / 2
        self.quote_count += 1
        self.spread_sum += spread
        self.min_spread = spread if self.min_spread is None else min(self.min_spread, spread)
        self.max_spread = spread if self.max_spread is None else max(self.max_spread, spread)
        self.bid_size_sum += bid_size
        self.ask_size_sum += ask_size
        self.mid_sum += mid
        self.last_bid = bid
        self.last_ask = ask
        self.last_mid = mid

    def add_trade(self, price: float, size: float, mid: float | None) -> None:
        if price <= 0 or size <= 0:
            return
        self.trade_count += 1
        self.trade_volume += size
        if mid is None:
            self.unknown_volume += size
        elif price >= mid:
            self.buy_volume += size
        else:
            self.sell_volume += size

    def as_row(self, *, symbol: str, feed: str, timestamp: str) -> dict[str, Any]:
        total_classified = self.buy_volume + self.sell_volume
        avg_spread = self.spread_sum / self.quote_count if self.quote_count else None
        avg_mid = self.mid_sum / self.quote_count if self.quote_count else self.last_mid
        return {
            "symbol": symbol,
            "feed": feed,
            "timestamp": timestamp,
            "quote_count": self.quote_count,
            "avg_spread": round(avg_spread, 6) if avg_spread is not None else None,
            "avg_spread_pct": round(avg_spread / avg_mid, 8) if avg_spread is not None and avg_mid else None,
            "min_spread": round(self.min_spread, 6) if self.min_spread is not None else None,
            "max_spread": round(self.max_spread, 6) if self.max_spread is not None else None,
            "avg_bid_size": round(self.bid_size_sum / self.quote_count, 2) if self.quote_count else None,
            "avg_ask_size": round(self.ask_size_sum / self.quote_count, 2) if self.quote_count else None,
            "last_bid": self.last_bid,
            "last_ask": self.last_ask,
            "last_mid": round(self.last_mid, 6) if self.last_mid is not None else None,
            "trade_count": self.trade_count,
            "trade_volume": round(self.trade_volume, 2),
            "buy_volume": round(self.buy_volume, 2),
            "sell_volume": round(self.sell_volume, 2),
            "unknown_volume": round(self.unknown_volume, 2),
            "buy_sell_imbalance": round((self.buy_volume - self.sell_volume) / total_classified, 6) if total_classified else None,
        }


@dataclass
class QuoteState:
    latest_mid: float | None = None
    by_minute: dict[str, float] = field(default_factory=dict)

    def update(self, minute: str, bid: float, ask: float) -> None:
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        self.latest_mid = (bid + ask) / 2
        self.by_minute[minute] = self.latest_mid

    def mid_for_minute(self, minute: str) -> float | None:
        return self.by_minute.get(minute, self.latest_mid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect SPY quote/trade microstructure and aggregate to 1-minute rows.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--feed", default=DEFAULT_FEED, choices=["iex", "sip", "otc", "boats"])
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true", help="Overwrite existing day aggregates.")
    parser.add_argument("--save-raw", action="store_true", help="Also save normalized raw quote/trade JSONL files for each day.")
    parser.add_argument("--max-days", type=int, default=0, help="Optional cap for this run; 0 means all dates.")
    args = parser.parse_args()

    summary = asyncio.run(
        collect_microstructure_history(
            symbol=args.symbol.upper(),
            feed=args.feed,
            start_date=date.fromisoformat(args.start_date),
            end_date=date.fromisoformat(args.end_date),
            force=args.force,
            save_raw=args.save_raw,
            max_days=args.max_days,
        )
    )
    print(json.dumps(summary, indent=2))


async def collect_microstructure_history(
    *,
    symbol: str,
    feed: str,
    start_date: date,
    end_date: date,
    force: bool,
    save_raw: bool,
    max_days: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_alpaca_credentials:
        raise RuntimeError("Alpaca credentials are not configured. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in backend/.env.")

    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    days = [day for day in trading_days(start_date, end_date)]
    if max_days > 0:
        days = days[:max_days]

    completed = 0
    skipped = 0
    failed: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), trust_env=False) as client:
        for day in days:
            output_path = day_output_path(symbol, feed, day)
            raw_paths = raw_output_paths(symbol, feed, day)
            raw_ready = not save_raw or (raw_paths["quotes"].exists() and raw_paths["trades"].exists())
            if output_path.exists() and raw_ready and not force:
                skipped += 1
                continue
            try:
                rows, raw = await collect_microstructure_day(client, settings.alpaca_data_base_url, headers, symbol, feed, day, save_raw=save_raw)
                write_jsonl(output_path, rows)
                if save_raw:
                    write_jsonl(raw_paths["quotes"], raw["quotes"])
                    write_jsonl(raw_paths["trades"], raw["trades"])
                completed += 1
                print(
                    json.dumps(
                        {
                            "day": day.isoformat(),
                            "rows": len(rows),
                            "quotes": len(raw["quotes"]),
                            "trades": len(raw["trades"]),
                            "path": str(output_path),
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                failed.append({"day": day.isoformat(), "error": str(exc)})
                print(json.dumps({"day": day.isoformat(), "error": str(exc)}), flush=True)

    return {
        "symbol": symbol,
        "feed": feed,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "daysRequested": len(days),
        "completed": completed,
        "skipped": skipped,
        "failed": failed,
        "rawSaved": save_raw,
        "outputRoot": str(MICROSTRUCTURE_DIR / symbol / feed),
    }


async def collect_microstructure_day(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    symbol: str,
    feed: str,
    day: date,
    save_raw: bool,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    start, end = session_window_utc(day)
    buckets: dict[str, MinuteBucket] = defaultdict(MinuteBucket)
    quote_state = QuoteState()
    raw_quotes: list[dict[str, Any]] = []
    raw_trades: list[dict[str, Any]] = []

    quotes = await fetch_pages(client, f"{base_url}/stocks/{symbol}/quotes", headers, feed, start, end, "quotes")
    for quote in quotes:
        timestamp = str(quote.get("t") or "")
        minute = minute_key(timestamp)
        if not minute:
            continue
        bid = float(quote.get("bp") or quote.get("bid_price") or 0)
        ask = float(quote.get("ap") or quote.get("ask_price") or 0)
        bid_size = float(quote.get("bs") or quote.get("bid_size") or 0)
        ask_size = float(quote.get("as") or quote.get("ask_size") or 0)
        if save_raw:
            raw_quotes.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "feed": feed,
                    "bid_price": bid,
                    "bid_size": bid_size,
                    "ask_price": ask,
                    "ask_size": ask_size,
                }
            )
        buckets[minute].add_quote(bid, ask, bid_size, ask_size)
        quote_state.update(minute, bid, ask)

    trades = await fetch_pages(client, f"{base_url}/stocks/{symbol}/trades", headers, feed, start, end, "trades")
    for trade in trades:
        timestamp = str(trade.get("t") or "")
        minute = minute_key(timestamp)
        if not minute:
            continue
        price = float(trade.get("p") or trade.get("price") or 0)
        size = float(trade.get("s") or trade.get("size") or 0)
        exchange = trade.get("x") or trade.get("exchange")
        conditions = trade.get("c") or trade.get("conditions") or []
        if save_raw:
            raw_trades.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "feed": feed,
                    "price": price,
                    "size": size,
                    "exchange": exchange,
                    "conditions": conditions,
                }
            )
        buckets[minute].add_trade(price, size, quote_state.mid_for_minute(minute))

    rows = [buckets[minute].as_row(symbol=symbol, feed=feed, timestamp=minute) for minute in sorted(buckets)]
    return rows, {"quotes": raw_quotes, "trades": raw_trades}


async def fetch_pages(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    feed: str,
    start: str,
    end: str,
    key: str,
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "feed": feed,
        "start": start,
        "end": end,
        "limit": PAGE_LIMIT,
        "sort": "asc",
    }
    rows: list[dict[str, Any]] = []
    next_page_token: str | None = None
    while True:
        if next_page_token:
            params["page_token"] = next_page_token
        response = await client.get(url, params=params, headers=headers)
        if response.status_code == 429:
            await asyncio.sleep(10)
            continue
        response.raise_for_status()
        payload = response.json()
        page_rows = payload.get(key) or []
        if isinstance(page_rows, dict):
            flattened: list[dict[str, Any]] = []
            for value in page_rows.values():
                flattened.extend(value if isinstance(value, list) else [])
            page_rows = flattened
        rows.extend(page_rows)
        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break
    return rows


def trading_days(start_date: date, end_date: date):
    day = start_date
    while day <= end_date:
        if day.weekday() < 5 and nyse_holiday_name(day) is None:
            yield day
        day += timedelta(days=1)


def session_window_utc(day: date) -> tuple[str, str]:
    eastern = eastern_timezone_for_date(day)
    session_start = datetime.combine(day, time(9, 30), eastern).astimezone(UTC)
    session_end = datetime.combine(day, time(16, 0), eastern).astimezone(UTC)
    return session_start.isoformat().replace("+00:00", "Z"), session_end.isoformat().replace("+00:00", "Z")


def minute_key(timestamp: str) -> str | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
    return parsed.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def day_output_path(symbol: str, feed: str, day: date) -> Path:
    return MICROSTRUCTURE_DIR / symbol / feed / day.isoformat() / "one_minute_microstructure.jsonl"


def raw_output_paths(symbol: str, feed: str, day: date) -> dict[str, Path]:
    root = MICROSTRUCTURE_DIR / symbol / feed / day.isoformat()
    return {
        "quotes": root / "quotes.jsonl",
        "trades": root / "trades.jsonl",
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
