"""Authoritative Weighted Voting strategy catalog."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.weighted_voting.models import WeightedVotingStrategyFamily


WEIGHTED_VOTING_CATALOG_VERSION = "weighted_voting_catalog_v2"


@dataclass(frozen=True)
class WeightedVotingStrategyCatalogEntry:
    strategy_id: str
    name: str
    family: WeightedVotingStrategyFamily
    module_name: str
    purpose: str
    required_data: tuple[str, ...]
    optional_data: tuple[str, ...]
    valid_session_window: str
    minimum_warmup: int
    invalid_market_conditions: tuple[str, ...]
    buy_rule: str
    sell_rule: str
    hold_rule: str
    confidence_components: tuple[str, ...]
    invalidation_condition: str
    data_quality_classification: str
    version: str


WEIGHTED_VOTING_STRATEGY_CATALOG: tuple[WeightedVotingStrategyCatalogEntry, ...] = (
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S1",
        name="Opening Range Breakout",
        family=WeightedVotingStrategyFamily.BREAKOUT,
        module_name="opening_range_breakout",
        purpose="Trade a confirmed break beyond the initial regular-session opening range.",
        required_data=("1m OHLCV candles", "regular-session clock", "opening-range high/low", "current close", "current volume"),
        optional_data=("5m confirmation candles", "spread quote"),
        valid_session_window="09:45-11:00 America/New_York",
        minimum_warmup=15,
        invalid_market_conditions=("opening range unavailable", "volume below configured minimum", "wide spread", "halted or stale candles"),
        buy_rule="Buy when price closes above the opening-range high with expanding volume and positive short-term momentum.",
        sell_rule="Sell when price closes below the opening-range low with expanding volume and negative short-term momentum.",
        hold_rule="Hold while price remains inside the opening range or breakout confirmation is incomplete.",
        confidence_components=("opening-range distance", "volume expansion", "breakout candle body", "5m alignment", "spread quality"),
        invalidation_condition="Invalidate if price re-enters the opening range before entry confirmation or data becomes stale.",
        data_quality_classification="requires clean 1m OHLCV and regular-session timestamps",
        version="weighted_strategy_S1_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S2",
        name="First Pullback After Open",
        family=WeightedVotingStrategyFamily.TREND,
        module_name="first_pullback_after_open",
        purpose="Join the first controlled pullback after an early directional impulse.",
        required_data=("1m OHLCV candles", "regular-session clock", "opening impulse", "VWAP", "recent swing high/low"),
        optional_data=("5m confirmation candles", "ATR"),
        valid_session_window="09:45-11:30 America/New_York",
        minimum_warmup=20,
        invalid_market_conditions=("no early impulse", "choppy VWAP rotation", "volume below configured minimum", "stale candles"),
        buy_rule="Buy when an uptrend impulse pulls back toward VWAP or prior support and resumes upward.",
        sell_rule="Sell when a downtrend impulse pulls back toward VWAP or prior resistance and resumes downward.",
        hold_rule="Hold when the first pullback is absent, too deep, or not followed by continuation.",
        confidence_components=("impulse strength", "pullback depth", "VWAP respect", "resumption candle", "5m alignment"),
        invalidation_condition="Invalidate if the pullback breaks the impulse origin or flips through VWAP against the setup.",
        data_quality_classification="requires clean 1m OHLCV, VWAP, and session sequencing",
        version="weighted_strategy_S2_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S3",
        name="VWAP Trend Continuation",
        family=WeightedVotingStrategyFamily.TREND,
        module_name="vwap_trend_continuation",
        purpose="Follow intraday trend continuation while price holds the correct side of VWAP.",
        required_data=("1m OHLCV candles", "VWAP", "short moving average", "long moving average", "current volume"),
        optional_data=("5m confirmation candles", "relative strength comparison candles"),
        valid_session_window="10:00-15:30 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("flat moving averages", "VWAP chop", "low volume", "stale candles"),
        buy_rule="Buy when price is above VWAP, fast trend is above slow trend, and pullbacks hold above VWAP.",
        sell_rule="Sell when price is below VWAP, fast trend is below slow trend, and bounces fail below VWAP.",
        hold_rule="Hold when VWAP is being crossed repeatedly or trend slope is neutral.",
        confidence_components=("VWAP distance", "moving-average slope", "higher-timeframe alignment", "relative strength", "volume confirmation"),
        invalidation_condition="Invalidate if price closes through VWAP against the signal or trend slope turns neutral.",
        data_quality_classification="requires clean 1m OHLCV and deterministic VWAP calculation",
        version="weighted_strategy_S3_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S4",
        name="VWAP Mean Reversion",
        family=WeightedVotingStrategyFamily.MEAN_REVERSION,
        module_name="vwap_mean_reversion",
        purpose="Fade controlled intraday extensions back toward VWAP in range-bound conditions.",
        required_data=("1m OHLCV candles", "VWAP", "ATR", "recent high/low", "current volume"),
        optional_data=("spread quote", "5m confirmation candles"),
        valid_session_window="10:00-15:15 America/New_York",
        minimum_warmup=30,
        invalid_market_conditions=("strong directional trend", "news shock candle", "extreme volatility", "stale candles"),
        buy_rule="Buy when price is extended below VWAP in a range and shows a reversal candle back toward VWAP.",
        sell_rule="Sell when price is extended above VWAP in a range and shows a reversal candle back toward VWAP.",
        hold_rule="Hold when extension is too small, trend is directional, or reversal evidence is absent.",
        confidence_components=("VWAP extension", "ATR-normalized distance", "range condition", "reversal candle quality", "spread quality"),
        invalidation_condition="Invalidate if extension accelerates away from VWAP or range condition becomes directional.",
        data_quality_classification="requires clean 1m OHLCV, VWAP, and ATR warm-up",
        version="weighted_strategy_S4_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S5",
        name="Failed Breakout Reversal",
        family=WeightedVotingStrategyFamily.REVERSAL,
        module_name="failed_breakout_reversal",
        purpose="Reverse after a breakout attempt fails and price returns through the breakout level.",
        required_data=("1m OHLCV candles", "prior range high/low", "breakout attempt", "re-entry close", "current volume"),
        optional_data=("opening-range levels", "5m confirmation candles"),
        valid_session_window="10:00-15:30 America/New_York",
        minimum_warmup=30,
        invalid_market_conditions=("no defined range", "confirmed trend continuation", "thin volume", "stale candles"),
        buy_rule="Buy when a downside break fails and price closes back above the broken range low with reversal momentum.",
        sell_rule="Sell when an upside break fails and price closes back below the broken range high with reversal momentum.",
        hold_rule="Hold when breakout failure is unconfirmed or price remains outside the prior range.",
        confidence_components=("breakout excess", "failed-break re-entry", "volume fade", "range clarity", "5m non-confirmation of breakout"),
        invalidation_condition="Invalidate if price resumes in the breakout direction beyond the failed-break extreme.",
        data_quality_classification="requires clean 1m OHLCV and deterministic prior range levels",
        version="weighted_strategy_S5_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S6",
        name="Liquidity Sweep Reversal",
        family=WeightedVotingStrategyFamily.REVERSAL,
        module_name="liquidity_sweep_reversal",
        purpose="Reverse after a stop-run sweep of a recent swing level rejects back inside the range.",
        required_data=("1m OHLCV candles", "recent swing high/low", "sweep wick", "close back inside level", "current volume"),
        optional_data=("spread quote", "5m confirmation candles"),
        valid_session_window="09:45-15:30 America/New_York",
        minimum_warmup=25,
        invalid_market_conditions=("no recent swing level", "wide spread", "sweep candle is stale", "halted or malformed candles"),
        buy_rule="Buy when price sweeps below a recent swing low and closes back above it with rejection evidence.",
        sell_rule="Sell when price sweeps above a recent swing high and closes back below it with rejection evidence.",
        hold_rule="Hold when the sweep does not reclaim the level or rejection quality is insufficient.",
        confidence_components=("wick rejection", "level significance", "volume burst", "reclaim close", "spread quality"),
        invalidation_condition="Invalidate if price closes beyond the sweep extreme after the reclaim attempt.",
        data_quality_classification="requires clean 1m OHLCV and reliable swing-level construction",
        version="weighted_strategy_S6_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S7",
        name="Bollinger/ATR Reversion",
        family=WeightedVotingStrategyFamily.MEAN_REVERSION,
        module_name="bollinger_atr_reversion",
        purpose="Fade statistically large Bollinger/ATR extensions when volatility is not expanding directionally.",
        required_data=("1m OHLCV candles", "Bollinger bands", "ATR", "current close", "recent candle body"),
        optional_data=("VWAP", "spread quote"),
        valid_session_window="10:00-15:15 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("volatility breakout", "band walk trend", "extreme spread", "stale candles"),
        buy_rule="Buy when price extends below the lower band by an ATR-normalized amount and starts reverting upward.",
        sell_rule="Sell when price extends above the upper band by an ATR-normalized amount and starts reverting downward.",
        hold_rule="Hold when bands are walking directionally or extension is not statistically meaningful.",
        confidence_components=("band z-score", "ATR extension", "reversal candle", "VWAP distance", "volatility stability"),
        invalidation_condition="Invalidate if price continues to close outside the band with expanding ATR.",
        data_quality_classification="requires clean 1m OHLCV plus Bollinger and ATR warm-up",
        version="weighted_strategy_S7_v1",
    ),
    WeightedVotingStrategyCatalogEntry(
        strategy_id="S8",
        name="Volatility Breakout",
        family=WeightedVotingStrategyFamily.BREAKOUT,
        module_name="volatility_breakout",
        purpose="Trade expansion from compressed volatility into a confirmed directional breakout.",
        required_data=("1m OHLCV candles", "ATR", "recent compression range", "current volume", "breakout close"),
        optional_data=("Bollinger band width", "5m confirmation candles", "spread quote"),
        valid_session_window="09:45-15:30 America/New_York",
        minimum_warmup=50,
        invalid_market_conditions=("already extended trend", "low volume", "wide spread", "stale candles"),
        buy_rule="Buy when compressed volatility expands above the recent range with volume confirmation.",
        sell_rule="Sell when compressed volatility expands below the recent range with volume confirmation.",
        hold_rule="Hold when volatility is not compressed first or breakout lacks expansion confirmation.",
        confidence_components=("compression score", "ATR expansion", "range break distance", "volume expansion", "5m alignment"),
        invalidation_condition="Invalidate if expansion fails back inside the compression range before entry confirmation.",
        data_quality_classification="requires clean 1m OHLCV, ATR warm-up, and deterministic compression range",
        version="weighted_strategy_S8_v1",
    ),
)
