"""Authoritative Session liquidity and tradability evidence."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from backend.app.algorithms.session.calendar import parse_session_timestamp_utc
from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import DataQualityState, LiquidityState


def analyze_session_liquidity(
    snapshot: dict[str, Any] | None,
    *,
    decision_time: datetime | str,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> dict[str, Any]:
    decision_at = parse_session_timestamp_utc(decision_time)
    if not snapshot:
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INCOMPLETE,
            block_new_entries=True,
            reason_codes=("session.liquidity.quote_missing",),
        )

    bid = _number(_first_present(snapshot, "bestBid", "best_bid", "bid"))
    ask = _number(_first_present(snapshot, "bestAsk", "best_ask", "ask"))
    bid_size = _number(_first_present(snapshot, "bidSize", "bid_size"))
    ask_size = _number(_first_present(snapshot, "askSize", "ask_size"))
    intended_quantity = _number(_first_present(snapshot, "intendedOrderQuantity", "intended_order_quantity", "intendedQuantity"))
    bar_volume = _number(_first_present(snapshot, "barVolume", "bar_volume", "volume"))
    bar_dollar_volume = _number(_first_present(snapshot, "barDollarVolume", "bar_dollar_volume", "dollarVolume"))
    trade_count = _number(_first_present(snapshot, "tradeCount", "trade_count"))
    recent_estimated_slippage_bps = _number(_first_present(snapshot, "recentEstimatedSlippageBps", "recent_estimated_slippage_bps", "estimatedSlippageBps"))
    recent_realized_slippage_bps = _number(_first_present(snapshot, "recentRealizedSlippageBps", "recent_realized_slippage_bps", "realizedSlippageBps"))
    partial_fill_rate = _number(_first_present(snapshot, "partialFillRate", "partial_fill_rate"))
    quote_timestamp_value = _first_present(snapshot, "quoteTimestamp", "quote_timestamp", "timestamp")
    quote_age_seconds = _quote_age_seconds(snapshot, decision_at, quote_timestamp_value)

    if any(value is not None and value < 0 for value in (bid_size, ask_size, intended_quantity, bar_volume, bar_dollar_volume, trade_count)):
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INVALID,
            block_new_entries=True,
            reason_codes=("session.liquidity.invalid_units",),
            quote_age_seconds=quote_age_seconds,
        )
    if partial_fill_rate is not None and not 0 <= partial_fill_rate <= 1:
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INVALID,
            block_new_entries=True,
            reason_codes=("session.liquidity.invalid_units",),
            quote_age_seconds=quote_age_seconds,
        )
    if bid is None or ask is None:
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INCOMPLETE,
            block_new_entries=True,
            reason_codes=("session.liquidity.quote_missing",),
            quote_age_seconds=quote_age_seconds,
        )
    if bid <= 0 or ask <= 0 or ask <= bid:
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INVALID,
            block_new_entries=True,
            reason_codes=("session.liquidity.invalid_or_crossed_market",),
            bid=bid,
            ask=ask,
            quote_age_seconds=quote_age_seconds,
        )
    if quote_age_seconds is None:
        return _result(
            status=LiquidityState.UNKNOWN,
            data_quality_state=DataQualityState.INCOMPLETE,
            block_new_entries=True,
            reason_codes=("session.liquidity.quote_timestamp_missing",),
            bid=bid,
            ask=ask,
        )

    midpoint = (bid + ask) / 2
    spread_dollars = ask - bid
    spread_bps = (spread_dollars / midpoint) * 10_000 if midpoint else None
    if bar_dollar_volume is None and bar_volume is not None:
        bar_dollar_volume = bar_volume * midpoint
    trade_rate = None if trade_count is None else trade_count / 60.0
    top_size = min(value for value in (bid_size, ask_size) if value is not None) if bid_size is not None and ask_size is not None else None
    imbalance = None if bid_size is None or ask_size is None or bid_size + ask_size <= 0 else (bid_size - ask_size) / (bid_size + ask_size)
    estimated_participation = None if intended_quantity is None or bar_volume in {None, 0} else intended_quantity / bar_volume
    estimated_impact_bps = _estimated_market_impact(spread_bps, estimated_participation, intended_quantity, top_size)
    fill_probability = _estimated_fill_probability(spread_bps, estimated_participation, intended_quantity, top_size, config)
    slippage_error_bps = None
    if recent_realized_slippage_bps is not None and recent_estimated_slippage_bps is not None:
        slippage_error_bps = recent_realized_slippage_bps - recent_estimated_slippage_bps

    reasons: list[str] = []
    status = LiquidityState.HEALTHY
    block = False
    data_quality_state = DataQualityState.READY

    if quote_age_seconds > config.maximum_stale_quote_age_seconds:
        return _result(
            status=LiquidityState.STALE,
            data_quality_state=DataQualityState.STALE,
            block_new_entries=True,
            reason_codes=("session.liquidity.quote_stale",),
            bid=bid,
            ask=ask,
            midpoint=midpoint,
            spread_dollars=spread_dollars,
            spread_bps=spread_bps,
            quote_age_seconds=quote_age_seconds,
            top_of_book_imbalance=imbalance,
            one_minute_dollar_volume=bar_dollar_volume,
            trade_rate=trade_rate,
            estimated_participation_rate=estimated_participation,
            estimated_market_impact_bps=estimated_impact_bps,
            estimated_fill_probability=fill_probability,
            realized_vs_estimated_slippage_bps=slippage_error_bps,
            partial_fill_rate=partial_fill_rate,
        )
    if quote_age_seconds > config.maximum_quote_age_seconds:
        status = LiquidityState.STALE
        data_quality_state = DataQualityState.STALE
        block = True
        reasons.append("session.liquidity.quote_stale")
    if spread_bps is not None and spread_bps > config.maximum_constrained_spread_bps:
        status = LiquidityState.STRESSED
        block = True
        reasons.append("session.liquidity.spread_stressed")
    elif spread_bps is not None and spread_bps > config.maximum_healthy_spread_bps and status == LiquidityState.HEALTHY:
        status = LiquidityState.CONSTRAINED
        reasons.append("session.liquidity.spread_constrained")
    if top_size is not None and top_size < config.minimum_top_of_book_size_shares:
        status = LiquidityState.CONSTRAINED if status == LiquidityState.HEALTHY else status
        reasons.append("session.liquidity.thin_top_of_book")
    if estimated_participation is not None and estimated_participation > config.maximum_intended_participation_ratio:
        status = LiquidityState.STRESSED
        block = True
        reasons.append("session.liquidity.participation_too_high")
    elif estimated_participation is not None and estimated_participation > config.constrained_intended_participation_ratio and status == LiquidityState.HEALTHY:
        status = LiquidityState.CONSTRAINED
        reasons.append("session.liquidity.participation_constrained")
    if trade_rate is not None and trade_rate < config.minimum_trade_rate_per_second:
        status = LiquidityState.CONSTRAINED if status == LiquidityState.HEALTHY else status
        reasons.append("session.liquidity.low_trade_rate")
    if slippage_error_bps is not None and abs(slippage_error_bps) > config.stressed_recent_slippage_error_bps:
        status = LiquidityState.STRESSED
        block = True
        reasons.append("session.liquidity.slippage_error_stressed")
    elif slippage_error_bps is not None and abs(slippage_error_bps) > config.maximum_recent_slippage_error_bps and status == LiquidityState.HEALTHY:
        status = LiquidityState.CONSTRAINED
        reasons.append("session.liquidity.slippage_error_constrained")

    if not reasons:
        reasons.append("session.liquidity.healthy")

    return _result(
        status=status,
        data_quality_state=data_quality_state,
        block_new_entries=block,
        reason_codes=tuple(dict.fromkeys(reasons)),
        bid=bid,
        ask=ask,
        midpoint=midpoint,
        spread_dollars=spread_dollars,
        spread_bps=spread_bps,
        quote_age_seconds=quote_age_seconds,
        top_of_book_imbalance=imbalance,
        one_minute_dollar_volume=bar_dollar_volume,
        trade_rate=trade_rate,
        estimated_participation_rate=estimated_participation,
        estimated_market_impact_bps=estimated_impact_bps,
        estimated_fill_probability=fill_probability,
        realized_vs_estimated_slippage_bps=slippage_error_bps,
        partial_fill_rate=partial_fill_rate,
    )


def _result(
    *,
    status: LiquidityState,
    data_quality_state: DataQualityState,
    block_new_entries: bool,
    reason_codes: tuple[str, ...],
    bid: float | None = None,
    ask: float | None = None,
    midpoint: float | None = None,
    spread_dollars: float | None = None,
    spread_bps: float | None = None,
    quote_age_seconds: float | None = None,
    top_of_book_imbalance: float | None = None,
    one_minute_dollar_volume: float | None = None,
    trade_rate: float | None = None,
    estimated_participation_rate: float | None = None,
    estimated_market_impact_bps: float | None = None,
    estimated_fill_probability: float | None = None,
    realized_vs_estimated_slippage_bps: float | None = None,
    partial_fill_rate: float | None = None,
) -> dict[str, Any]:
    return {
        "status": status.value,
        "liquidityState": status.value,
        "dataQualityState": data_quality_state.value,
        "blockNewEntries": block_new_entries,
        "bestBid": bid,
        "bestAsk": ask,
        "midpoint": midpoint,
        "spreadDollars": spread_dollars,
        "spreadBasisPoints": spread_bps,
        "quoteAgeSeconds": quote_age_seconds,
        "topOfBookImbalance": top_of_book_imbalance,
        "oneMinuteDollarVolume": one_minute_dollar_volume,
        "tradeRate": trade_rate,
        "estimatedParticipationRate": estimated_participation_rate,
        "estimatedMarketImpactBps": estimated_market_impact_bps,
        "estimatedFillProbability": estimated_fill_probability,
        "realizedVsEstimatedSlippageBps": realized_vs_estimated_slippage_bps,
        "partialFillRate": partial_fill_rate,
        "reasonCodes": reason_codes,
    }


def _quote_age_seconds(snapshot: dict[str, Any], decision_at: datetime, quote_timestamp_value: Any) -> float | None:
    age_seconds = _number(_first_present(snapshot, "quoteAgeSeconds", "quote_age_seconds"))
    if age_seconds is not None:
        return age_seconds
    age_ms = _number(_first_present(snapshot, "quoteAgeMs", "quote_age_ms", "ageMs", "age_ms"))
    if age_ms is not None:
        return age_ms / 1000.0
    if quote_timestamp_value is None:
        return None
    return max(0.0, (decision_at - parse_session_timestamp_utc(quote_timestamp_value)).total_seconds())


def _estimated_market_impact(spread_bps: float | None, participation: float | None, intended_quantity: float | None, top_size: float | None) -> float | None:
    if spread_bps is None:
        return None
    participation_component = (participation or 0.0) * spread_bps
    depth_component = 0.0
    if intended_quantity is not None and top_size not in {None, 0}:
        depth_component = max(0.0, (intended_quantity / top_size) - 1.0) * (spread_bps / 2.0)
    return max(0.0, (spread_bps / 2.0) + participation_component + depth_component)


def _estimated_fill_probability(
    spread_bps: float | None,
    participation: float | None,
    intended_quantity: float | None,
    top_size: float | None,
    config: SessionConfig,
) -> float | None:
    if spread_bps is None:
        return None
    spread_penalty = min(0.6, spread_bps / max(config.maximum_constrained_spread_bps * 2.0, 1.0))
    participation_penalty = 0.0 if participation is None else min(0.5, participation / max(config.maximum_intended_participation_ratio * 2.0, 0.0001))
    depth_penalty = 0.0
    if intended_quantity is not None and top_size not in {None, 0}:
        depth_penalty = min(0.4, max(0.0, intended_quantity - top_size) / max(intended_quantity, 1.0))
    return max(0.0, min(1.0, 1.0 - spread_penalty - participation_penalty - depth_penalty))


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None
