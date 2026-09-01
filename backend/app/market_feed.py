"""The one place that says which instrument and data source the app is trading.

Every algorithm reads market data from the shared candle store, which is keyed by
``(provider, feed, symbol, timeframe)``. What was missing was a statement of *which* of
those the app is currently on, and what each instrument actually is. This module is that
statement: an instrument registry, a provider interface the store is filled through, and a
single app-wide active selection that the five algorithms and the chart all follow.

Inventory isolation is unaffected. Each algorithm keeps its own capital partition and its
own persisted state; sharing a data source is not sharing an inventory, and nothing here
touches per-algorithm state.

**An instrument the app cannot trade correctly is not tradeable.** Index futures are
contracts with a point value and a nearly 23-hour session, while every sizing path in this
codebase computes ``floor(dollars / price)`` shares and every session window assumes
09:30-16:00. Those are real pieces of work, not switches, so futures instruments are
registered with the capabilities they require, declared unsupported until those land, and
refused for trading until then. Selecting one for data and charting is fine; trading it is
not, and the refusal is explicit rather than a silently wrong contract count.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol


MARKET_FEED_VERSION = "market_feed_v1"

AssetClass = Literal["equity", "future"]

# Capabilities an instrument may require of the platform. An instrument is tradeable only
# when every capability it needs is in SUPPORTED_CAPABILITIES.
Capability = Literal[
    "share_sizing",
    "contract_sizing",
    "regular_session",
    "extended_session",
    "contract_rollover",
]

# What the platform can actually do today. Adding to this set is what makes a futures
# instrument tradeable -- it must follow the work, never lead it.
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({"share_sizing", "regular_session"})

ACTIVE_FEED_ENV_VAR = "ACTIVE_MARKET_FEED"
ACTIVE_FEED_STATE_PATH = Path("data/market_feed/active_feed.json")


@dataclass(frozen=True)
class Instrument:
    """One tradeable thing, and everything the platform needs to know to handle it."""

    instrument_id: str
    symbol: str
    display_name: str
    asset_class: AssetClass
    provider: str
    feed: str
    # Currency change per one point of price movement, per unit. Shares move one dollar per
    # point; an ES contract moves 50. Sizing that ignores this is not off by a little.
    point_value: float
    tick_size: float
    session_calendar: str
    required_capabilities: tuple[str, ...]
    contract_root: str | None = None
    roll_rule: str | None = None

    @property
    def missing_capabilities(self) -> tuple[str, ...]:
        return tuple(name for name in self.required_capabilities if name not in SUPPORTED_CAPABILITIES)

    @property
    def trade_ready(self) -> bool:
        """Whether the platform can size and schedule this instrument correctly."""
        return not self.missing_capabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrumentId": self.instrument_id,
            "symbol": self.symbol,
            "displayName": self.display_name,
            "assetClass": self.asset_class,
            "provider": self.provider,
            "feed": self.feed,
            "pointValue": self.point_value,
            "tickSize": self.tick_size,
            "sessionCalendar": self.session_calendar,
            "contractRoot": self.contract_root,
            "rollRule": self.roll_rule,
            "tradeReady": self.trade_ready,
            "missingCapabilities": list(self.missing_capabilities),
        }


# The equity instrument the platform runs on today, and the index futures to move to. The
# futures entries are deliberately complete -- point values and tick sizes are the real CME
# contract specifications -- so that the work to support them has a target rather than a
# placeholder.
INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        instrument_id="spy_equity",
        symbol="SPY",
        display_name="SPDR S&P 500 ETF",
        asset_class="equity",
        provider="alpaca",
        feed="iex",
        point_value=1.0,
        tick_size=0.01,
        session_calendar="us_equity_regular",
        required_capabilities=("share_sizing", "regular_session"),
    ),
    Instrument(
        instrument_id="es_future",
        symbol="ES",
        display_name="E-mini S&P 500",
        asset_class="future",
        provider="futures",
        feed="cme",
        point_value=50.0,
        tick_size=0.25,
        session_calendar="cme_equity_index",
        required_capabilities=("contract_sizing", "extended_session", "contract_rollover"),
        contract_root="ES",
        roll_rule="quarterly_hmuz_volume_crossover",
    ),
    Instrument(
        instrument_id="mes_future",
        symbol="MES",
        display_name="Micro E-mini S&P 500",
        asset_class="future",
        provider="futures",
        feed="cme",
        point_value=5.0,
        tick_size=0.25,
        session_calendar="cme_equity_index",
        required_capabilities=("contract_sizing", "extended_session", "contract_rollover"),
        contract_root="MES",
        roll_rule="quarterly_hmuz_volume_crossover",
    ),
    Instrument(
        instrument_id="nq_future",
        symbol="NQ",
        display_name="E-mini Nasdaq-100",
        asset_class="future",
        provider="futures",
        feed="cme",
        point_value=20.0,
        tick_size=0.25,
        session_calendar="cme_equity_index",
        required_capabilities=("contract_sizing", "extended_session", "contract_rollover"),
        contract_root="NQ",
        roll_rule="quarterly_hmuz_volume_crossover",
    ),
    Instrument(
        instrument_id="mnq_future",
        symbol="MNQ",
        display_name="Micro E-mini Nasdaq-100",
        asset_class="future",
        provider="futures",
        feed="cme",
        point_value=2.0,
        tick_size=0.25,
        session_calendar="cme_equity_index",
        required_capabilities=("contract_sizing", "extended_session", "contract_rollover"),
        contract_root="MNQ",
        roll_rule="quarterly_hmuz_volume_crossover",
    ),
)

INSTRUMENTS_BY_ID: dict[str, Instrument] = {item.instrument_id: item for item in INSTRUMENTS}
DEFAULT_INSTRUMENT_ID = "spy_equity"


def instrument(instrument_id: str) -> Instrument:
    """The registered instrument, or a refusal naming what was asked for."""
    try:
        return INSTRUMENTS_BY_ID[instrument_id]
    except KeyError:
        raise KeyError(f"{instrument_id} is not a registered instrument") from None


class MarketDataProvider(Protocol):
    """What the platform needs from a source of market data.

    The candle store is keyed by provider, so two providers can hold the same symbol and
    timeframe without their bars merging -- reads pass the provider through.
    """

    provider_id: str

    @property
    def available(self) -> bool:
        """Whether this provider can actually serve data right now."""
        ...

    async def get_bars(
        self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str
    ) -> list[dict]:
        ...


class AlpacaMarketDataProvider:
    """The equity source the platform runs on today."""

    provider_id = "alpaca"

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        return True

    async def get_bars(
        self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str
    ) -> list[dict]:
        return await self._client.get_bars(
            symbol=symbol, timeframe=timeframe, feed=feed, limit=limit, start=start, end=end, sort=sort
        )


class UnavailableMarketDataProvider:
    """A registered source with no implementation behind it yet.

    Alpaca does not carry index futures, so the futures source is a named seat waiting for a
    vendor. It answers honestly rather than returning empty bars, because an empty series is
    indistinguishable from a quiet market and would be read as real data.
    """

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    async def get_bars(
        self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str
    ) -> list[dict]:
        raise RuntimeError(f"market data provider '{self.provider_id}' is not configured: {self.reason}")


FUTURES_PROVIDER_REASON = (
    "index futures require a CME data vendor; Alpaca does not carry them. "
    "Register a provider implementation for 'futures' before selecting a futures instrument."
)


def build_providers(alpaca_client: Any) -> dict[str, MarketDataProvider]:
    """Every source the app knows about, by provider id."""
    return {
        AlpacaMarketDataProvider.provider_id: AlpacaMarketDataProvider(alpaca_client),
        "futures": UnavailableMarketDataProvider("futures", FUTURES_PROVIDER_REASON),
    }


def _state_path() -> Path:
    return ACTIVE_FEED_STATE_PATH


def active_instrument_id() -> str:
    """The instrument the whole app is on: persisted choice, else env, else the default."""
    path = _state_path()
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("instrumentId")
        if isinstance(stored, str) and stored in INSTRUMENTS_BY_ID:
            return stored
    except (OSError, ValueError):
        pass
    from_env = os.getenv(ACTIVE_FEED_ENV_VAR, "").strip()
    if from_env in INSTRUMENTS_BY_ID:
        return from_env
    return DEFAULT_INSTRUMENT_ID


def active_instrument() -> Instrument:
    return instrument(active_instrument_id())


def set_active_instrument(instrument_id: str, *, allow_untradeable: bool = True) -> Instrument:
    """Switch the whole app to another instrument.

    Selecting an instrument the platform cannot trade yet is permitted, because charting and
    data collection are useful before trading support exists, and collecting history is how
    that support gets validated. What it must not do is quietly enable trading: the
    instrument still reports trade_ready False and the trading paths refuse it.
    """
    selected = instrument(instrument_id)
    if not selected.trade_ready and not allow_untradeable:
        raise ValueError(
            f"{instrument_id} is not tradeable yet; missing {', '.join(selected.missing_capabilities)}"
        )
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"instrumentId": selected.instrument_id, "version": MARKET_FEED_VERSION}, indent=2),
        encoding="utf-8",
    )
    return selected


def instrument_for_symbol(symbol: str) -> Instrument | None:
    """The registered instrument trading under this symbol, if the registry knows it.

    Returns None for a symbol the registry does not carry -- a breadth component, say --
    which callers should read as "no instrument-level restriction" rather than as a
    refusal. Only registered instruments can carry capability requirements.
    """
    wanted = str(symbol or "").strip().upper()
    for item in INSTRUMENTS:
        if item.symbol.upper() == wanted:
            return item
    return None


def require_tradeable(selected: Instrument | None = None) -> Instrument:
    """The active instrument, or a refusal explaining what the platform still lacks.

    Trading paths call this instead of reading the symbol directly. Sizing an index future
    through the share-based path would not fail loudly -- it would return a plausible
    quantity that is wrong by the contract's point value -- so this refuses first.
    """
    chosen = selected or active_instrument()
    if not chosen.trade_ready:
        raise RuntimeError(
            f"{chosen.instrument_id} cannot be traded: missing platform capabilities "
            f"{', '.join(chosen.missing_capabilities)}"
        )
    return chosen


def market_feed_status() -> dict[str, Any]:
    """What the app is on, what else it could be on, and why the rest are not tradeable."""
    current = active_instrument()
    return {
        "version": MARKET_FEED_VERSION,
        "scope": "application_wide",
        "activeInstrument": current.as_dict(),
        "supportedCapabilities": sorted(SUPPORTED_CAPABILITIES),
        "instruments": [item.as_dict() for item in INSTRUMENTS],
    }


__all__ = [
    "ACTIVE_FEED_ENV_VAR",
    "AlpacaMarketDataProvider",
    "Capability",
    "DEFAULT_INSTRUMENT_ID",
    "FUTURES_PROVIDER_REASON",
    "INSTRUMENTS",
    "INSTRUMENTS_BY_ID",
    "Instrument",
    "MARKET_FEED_VERSION",
    "MarketDataProvider",
    "SUPPORTED_CAPABILITIES",
    "UnavailableMarketDataProvider",
    "active_instrument",
    "active_instrument_id",
    "build_providers",
    "instrument",
    "instrument_for_symbol",
    "market_feed_status",
    "require_tradeable",
    "set_active_instrument",
]
