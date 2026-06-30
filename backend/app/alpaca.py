from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from random import Random

import httpx

from .config import Settings


TIMEFRAME_MINUTES = {
    "1Min": 1,
    "3Min": 3,
    "5Min": 5,
    "15Min": 15,
    "1Hour": 60,
    "1Day": 390,
}


class AlpacaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
    ) -> list[dict]:
        if not self.settings.has_alpaca_credentials:
            return demo_bars(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit)

        params: dict[str, str | int] = {
            "timeframe": timeframe,
            "feed": feed,
            "limit": limit,
            "adjustment": "raw",
            "sort": sort,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        url = f"{self.settings.alpaca_data_base_url}/stocks/{symbol}/bars"
        headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }

        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        bars = payload.get("bars") or []
        normalized = [
            normalize_bar(
                provider="alpaca",
                feed=feed,
                symbol=symbol,
                timeframe=timeframe,
                bar=bar,
            )
            for bar in bars
        ]
        return sorted(normalized, key=lambda bar: bar["timestamp"])

    async def get_bars_window(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        start: str,
        end: str,
        limit: int = 10000,
        max_pages: int = 25,
    ) -> list[dict]:
        if not self.settings.has_alpaca_credentials:
            return demo_bars(symbol=symbol, timeframe=timeframe, feed=feed, limit=min(limit, 1000))

        url = f"{self.settings.alpaca_data_base_url}/stocks/{symbol}/bars"
        headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }
        params: dict[str, str | int] = {
            "timeframe": timeframe,
            "feed": feed,
            "limit": limit,
            "adjustment": "raw",
            "sort": "asc",
            "start": start,
            "end": end,
        }
        bars: list[dict] = []
        next_page_token: str | None = None

        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            for _ in range(max_pages):
                if next_page_token:
                    params["page_token"] = next_page_token
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
                page_bars = payload.get("bars") or []
                bars.extend(page_bars)
                next_page_token = payload.get("next_page_token")
                if not next_page_token:
                    break

        normalized = [
            normalize_bar(
                provider="alpaca",
                feed=feed,
                symbol=symbol,
                timeframe=timeframe,
                bar=bar,
            )
            for bar in bars
        ]
        return sorted(normalized, key=lambda bar: bar["timestamp"])

    async def get_market_status(self) -> dict:
        if not self.settings.has_alpaca_credentials:
            return inferred_market_status()

        headers = {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=3.0), trust_env=False) as client:
            clock_response = await client.get(
                f"{self.settings.alpaca_trading_base_url}/clock",
                headers=headers,
            )
            clock_response.raise_for_status()
            clock = clock_response.json()

            today = datetime.now(UTC).date().isoformat()
            calendar_warning = None
            try:
                calendar_response = await client.get(
                    f"{self.settings.alpaca_trading_base_url}/calendar",
                    headers=headers,
                    params={"start": today, "end": today},
                )
                calendar_response.raise_for_status()
                calendar = calendar_response.json()
            except httpx.HTTPError as exc:
                calendar = None
                calendar_warning = str(exc)

        session = calendar[0] if calendar else None
        if clock.get("is_open"):
            status = "open"
        elif calendar is None:
            status = "closed"
        elif session:
            status = "closed"
        else:
            status = "holiday"

        result = {
            "status": status,
            "isOpen": bool(clock.get("is_open")),
            "timestamp": clock.get("timestamp"),
            "nextOpen": clock.get("next_open"),
            "nextClose": clock.get("next_close"),
            "session": session,
        }
        if calendar_warning:
            result["warning"] = calendar_warning
        return result


def normalize_bar(
    *,
    provider: str,
    feed: str,
    symbol: str,
    timeframe: str,
    bar: dict,
) -> dict:
    return {
        "provider": provider,
        "feed": feed,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": bar["t"],
        "open": float(bar["o"]),
        "high": float(bar["h"]),
        "low": float(bar["l"]),
        "close": float(bar["c"]),
        "volume": int(bar.get("v", 0)),
        "trade_count": bar.get("n"),
        "vwap": bar.get("vw"),
    }


def demo_bars(*, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict]:
    minutes = TIMEFRAME_MINUTES.get(timeframe, 1)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=minutes * limit)
    rng = Random(f"{symbol}:{timeframe}:{feed}")
    price = 547.5
    bars: list[dict] = []

    for index in range(limit):
        ts = start + timedelta(minutes=minutes * index)
        drift = (rng.random() - 0.47) * 0.38
        open_price = price
        close = max(1, open_price + drift)
        high = max(open_price, close) + rng.random() * 0.18
        low = min(open_price, close) - rng.random() * 0.18
        volume = int(900 + rng.random() * 4600 + (index % 27 == 0) * 9000)
        price = close
        bars.append(
            {
                "provider": "demo",
                "feed": feed,
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
                "trade_count": None,
                "vwap": None,
            }
        )
    return bars


def inferred_market_status() -> dict:
    return local_market_status()


def local_market_status(*, warning: str | None = None) -> dict:
    now_utc = datetime.now(UTC)
    eastern = eastern_timezone(now_utc)
    now = now_utc.astimezone(eastern)
    today = now.date()
    holiday_name = nyse_holiday_name(today)
    session_open = datetime.combine(today, time(9, 30), eastern_timezone_for_date(today))
    session_close = datetime.combine(today, time(16, 0), eastern_timezone_for_date(today))
    has_session = now.weekday() < 5 and holiday_name is None

    if has_session and session_open <= now < session_close:
        status = "open"
        next_open = session_open
        next_close = session_close
    else:
        status = "holiday" if holiday_name else "closed"
        next_session = today if has_session and now < session_open else next_trading_day(today + timedelta(days=1))
        next_open = datetime.combine(next_session, time(9, 30), eastern_timezone_for_date(next_session))
        next_close = datetime.combine(next_session, time(16, 0), eastern_timezone_for_date(next_session))

    result = {
        "status": status,
        "isOpen": status == "open",
        "timestamp": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "nextOpen": next_open.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "nextClose": next_close.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "session": {
            "date": today.isoformat(),
            "open": session_open.time().isoformat(timespec="minutes") if has_session else None,
            "close": session_close.time().isoformat(timespec="minutes") if has_session else None,
            "holiday": holiday_name,
            "source": "local-calendar",
        },
    }
    if warning:
        result["warning"] = warning
    return result


def next_trading_day(start: date) -> date:
    candidate = start
    while candidate.weekday() >= 5 or nyse_holiday_name(candidate):
        candidate += timedelta(days=1)
    return candidate


def eastern_timezone(moment_utc: datetime) -> timezone:
    offset_hours = -4 if is_us_dst(moment_utc) else -5
    return timezone(timedelta(hours=offset_hours))


def eastern_timezone_for_date(day: date) -> timezone:
    return eastern_timezone(datetime.combine(day, time(12, 0), UTC))


def is_us_dst(moment_utc: datetime) -> bool:
    year = moment_utc.year
    dst_start = datetime.combine(nth_weekday(year, 3, 6, 2), time(7, 0), UTC)
    dst_end = datetime.combine(nth_weekday(year, 11, 6, 1), time(6, 0), UTC)
    return dst_start <= moment_utc < dst_end


def nyse_holiday_name(day: date) -> str | None:
    holidays = {
        observed_fixed(day.year, 1, 1): "New Year's Day",
        nth_weekday(day.year, 1, 0, 3): "Martin Luther King Jr. Day",
        nth_weekday(day.year, 2, 0, 3): "Washington's Birthday",
        easter_date(day.year) - timedelta(days=2): "Good Friday",
        last_weekday(day.year, 5, 0): "Memorial Day",
        observed_fixed(day.year, 6, 19): "Juneteenth",
        observed_fixed(day.year, 7, 4): "Independence Day",
        nth_weekday(day.year, 9, 0, 1): "Labor Day",
        nth_weekday(day.year, 11, 3, 4): "Thanksgiving Day",
        observed_fixed(day.year, 12, 25): "Christmas Day",
    }
    return holidays.get(day)


def observed_fixed(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    candidate = date(year, month, 1)
    days_until = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=days_until + 7 * (occurrence - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    candidate = date(year + int(month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    days_since = (candidate.weekday() - weekday) % 7
    return candidate - timedelta(days=days_since)


def easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
