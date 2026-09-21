"""Market data for the Voting Ensemble live pipeline, read at the data clock.

On a delayed data clock "the latest quote" means the latest quote at the data clock, not
the latest one Alpaca has: a quote from the real present would be minutes in the future
of the bar being decided. This client reads quotes and trades from Alpaca's historical
endpoints, bounded by the data clock, and stamps their receipt with the data clock, so
every freshness gate downstream measures them the way it would measure a live quote.

With no delay it defers to the live endpoints unchanged.

It also fetches every symbol a poll needs in one multi-symbol request. The free plan
allows 200 requests a minute across the whole app, and fetching fourteen symbols one by
one left too little headroom for the quotes a decision cannot do without.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from backend.app.alpaca import AlpacaClient, normalize_bar, normalize_quote, normalize_trade
from backend.app.algorithms.voting_ensemble.data_clock import data_now, delayed

# How far back an as-of quote or trade may be found. The freshness gates decide whether
# what is found is usable; this only bounds the query.
AS_OF_LOOKBACK = timedelta(seconds=60)
MULTI_SYMBOL_PAGE_LIMIT = 10000
MULTI_SYMBOL_MAX_PAGES = 10


class VotingEnsembleDelayedMarketDataClient:
    def __init__(self, alpaca: AlpacaClient) -> None:
        self.alpaca = alpaca
        self.settings = alpaca.settings

    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str) -> list[dict]:
        return await self.alpaca.get_bars(symbol=symbol, timeframe=timeframe, feed=feed, limit=limit, start=start, end=end, sort=sort)

    async def get_bars_multi(self, *, symbols: list[str], timeframe: str, feed: str, start: datetime, end: datetime) -> dict[str, list[dict]]:
        """Bars for several symbols in one request (paged), keyed by upper-case symbol."""
        if not self.settings.has_alpaca_credentials:
            return {}
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "feed": feed,
            "start": _iso(start),
            "end": _iso(end),
            "limit": MULTI_SYMBOL_PAGE_LIMIT,
            "adjustment": "raw",
            "sort": "asc",
        }
        result: dict[str, list[dict]] = {symbol.upper(): [] for symbol in symbols}
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            for _ in range(MULTI_SYMBOL_MAX_PAGES):
                response = await client.get(f"{self.settings.alpaca_data_base_url}/stocks/bars", params=params, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                for symbol, bars in (payload.get("bars") or {}).items():
                    result.setdefault(symbol.upper(), []).extend(
                        normalize_bar(provider="alpaca", feed=feed, symbol=symbol.upper(), timeframe=timeframe, bar=bar) for bar in bars or []
                    )
                token = payload.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        for rows in result.values():
            rows.sort(key=lambda row: row["timestamp"])
        return result

    def get_latest_quote_sync(self, *, symbol: str, feed: str) -> dict | None:
        if not delayed():
            return self.alpaca.get_latest_quote_sync(symbol=symbol, feed=feed)
        observed = data_now()
        record = self._latest_as_of(f"stocks/{symbol}/quotes", "quotes", feed=feed, at=observed)
        if record is None:
            return None
        return {
            **normalize_quote(provider="alpaca", feed=feed, symbol=symbol, quote=record, received_at=observed),
            "source": "alpaca_quote_as_of_data_clock",
        }

    def get_latest_trade_sync(self, *, symbol: str, feed: str) -> dict | None:
        if not delayed():
            return self.alpaca.get_latest_trade_sync(symbol=symbol, feed=feed)
        observed = data_now()
        record = self._latest_as_of(f"stocks/{symbol}/trades", "trades", feed=feed, at=observed)
        if record is None:
            return None
        return {
            **normalize_trade(provider="alpaca", feed=feed, symbol=symbol, trade=record, received_at=observed),
            "source": "alpaca_trade_as_of_data_clock",
        }

    def _latest_as_of(self, path: str, key: str, *, feed: str, at: datetime) -> dict | None:
        if not self.settings.has_alpaca_credentials:
            return None
        params = {
            "feed": feed,
            "start": _iso(at - AS_OF_LOOKBACK),
            "end": _iso(at),
            "limit": 1,
            "sort": "desc",
        }
        with httpx.Client(timeout=httpx.Timeout(4.0, connect=3.0), trust_env=False) as client:
            response = client.get(f"{self.settings.alpaca_data_base_url}/{path}", params=params, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        records = payload.get(key) if isinstance(payload, dict) else None
        return records[0] if isinstance(records, list) and records and isinstance(records[0], dict) else None

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")
