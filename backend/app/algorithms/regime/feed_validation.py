"""Passive real quote/trade feed validation for Regime liquidity evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping

from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


REGIME_REAL_FEED_VALIDATION_VERSION = "regime_real_quote_trade_feed_validation_v1"
REGIME_REAL_FEED_VALIDATION_TYPE = "real_quote_trade_liquidity_execution_quality"
LIVE_PAPER_SOURCE_MODES = frozenset({"paper", "paper_trading", "live_paper", "live_paper_shadow", "live"})


@dataclass(frozen=True)
class RealFeedValidationPolicy:
    minimum_quote_count: int = 20
    minimum_trade_count: int = 10
    minimum_fill_lifecycle_count: int = 1
    maximum_quote_gap_ms: float = 5_000.0
    maximum_latest_quote_age_ms: float = 2_000.0
    maximum_spread_bps: float = 8.0
    maximum_locked_or_crossed_quote_rate: float = 0.01
    maximum_trade_outside_nbbo_rate: float = 0.05
    maximum_fill_without_quote_rate: float = 0.0
    minimum_fill_rate: float = 0.70
    maximum_partial_fill_rate: float = 0.30
    maximum_non_fill_rate: float = 0.30
    maximum_average_fill_latency_ms: float = 1_500.0
    maximum_average_slippage_bps: float = 4.0


def validate_real_quote_trade_feeds(
    *,
    quotes: Iterable[Mapping[str, Any]],
    trades: Iterable[Mapping[str, Any]],
    fills: Iterable[Mapping[str, Any]] = (),
    source_mode: str = "offline_diagnostic",
    observed_at: str | datetime | None = None,
    policy: RealFeedValidationPolicy = RealFeedValidationPolicy(),
    allow_inactive: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Validate live/paper quote, trade, and fill evidence.

    The report is diagnostic-only unless ``allow_inactive=True`` is passed from
    an explicit live-paper validation workflow.
    """

    quote_rows = [_quote(row) for row in quotes]
    trade_rows = [_trade(row) for row in trades]
    fill_rows = [_fill(row) for row in fills]
    source_mode_normalized = str(source_mode or "").lower()
    observed_time = _parse_time(observed_at) if observed_at else None

    quote_report = _validate_quotes(quote_rows, observed_time=observed_time, policy=policy)
    trade_report = _validate_trades(trade_rows, quote_rows, policy=policy)
    execution_report = _validate_execution_quality(fill_rows, quote_rows, trade_rows, policy=policy)
    source_ready = source_mode_normalized in LIVE_PAPER_SOURCE_MODES
    diagnostic_passed = quote_report["passed"] and trade_report["passed"] and execution_report["passed"] and source_ready

    reason_codes = [
        *quote_report["reasonCodes"],
        *trade_report["reasonCodes"],
        *execution_report["reasonCodes"],
    ]
    if not source_ready:
        reason_codes.append("regime.feed_validation.live_paper_source_required")

    validation_status = "pass" if diagnostic_passed else "fail"
    if not allow_inactive:
        validation_status = INACTIVE_UNTIL_LIVE_PAPER_TRADING

    return {
        "algorithmId": "regime",
        "validationType": REGIME_REAL_FEED_VALIDATION_TYPE,
        "validationVersion": REGIME_REAL_FEED_VALIDATION_VERSION,
        "activationStatus": INACTIVE_UNTIL_LIVE_PAPER_TRADING,
        "validationStatus": validation_status,
        "diagnosticPassed": diagnostic_passed,
        "validationAppliedToLivePaperTrading": bool(allow_inactive and diagnostic_passed),
        "sourceMode": source_mode_normalized,
        "sourceReady": source_ready,
        "liquidityFeed": quote_report,
        "tradeFeed": trade_report,
        "executionQuality": execution_report,
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
    }


def _validate_quotes(
    quotes: list[dict[str, Any]],
    *,
    observed_time: datetime | None,
    policy: RealFeedValidationPolicy,
) -> dict[str, Any]:
    ordered = sorted((row for row in quotes if row["timestamp"] is not None), key=lambda row: row["timestamp"])
    chronological = _strictly_increasing(row["timestamp"] for row in quotes if row["timestamp"] is not None)
    valid_quotes = [row for row in ordered if row["bid"] is not None and row["ask"] is not None and row["bid"] > 0 and row["ask"] > 0]
    locked_or_crossed = [row for row in valid_quotes if row["ask"] <= row["bid"]]
    clean_quotes = [row for row in valid_quotes if row["ask"] > row["bid"]]
    spreads = [((row["ask"] - row["bid"]) / ((row["ask"] + row["bid"]) / 2)) * 10_000 for row in clean_quotes]
    gaps = [
        (right["timestamp"] - left["timestamp"]).total_seconds() * 1000
        for left, right in zip(ordered, ordered[1:])
    ]
    latest_age_ms = None
    if observed_time and ordered:
        latest_age_ms = max(0.0, (observed_time - ordered[-1]["timestamp"]).total_seconds() * 1000)
    locked_or_crossed_rate = len(locked_or_crossed) / len(valid_quotes) if valid_quotes else 1.0
    reason_codes: list[str] = []
    if len(valid_quotes) < policy.minimum_quote_count:
        reason_codes.append("regime.feed_validation.insufficient_real_quotes")
    if not chronological:
        reason_codes.append("regime.feed_validation.quotes_not_chronological")
    if locked_or_crossed_rate > policy.maximum_locked_or_crossed_quote_rate:
        reason_codes.append("regime.feed_validation.locked_or_crossed_quotes")
    if gaps and max(gaps) > policy.maximum_quote_gap_ms:
        reason_codes.append("regime.feed_validation.quote_gap_too_large")
    if latest_age_ms is not None and latest_age_ms > policy.maximum_latest_quote_age_ms:
        reason_codes.append("regime.feed_validation.latest_quote_stale")
    if spreads and mean(spreads) > policy.maximum_spread_bps:
        reason_codes.append("regime.feed_validation.average_spread_too_wide")
    if not spreads:
        reason_codes.append("regime.feed_validation.no_clean_nbbo_spread")
    return {
        "passed": not reason_codes,
        "quoteCount": len(quotes),
        "validQuoteCount": len(valid_quotes),
        "cleanQuoteCount": len(clean_quotes),
        "chronological": chronological,
        "lockedOrCrossedQuoteRate": locked_or_crossed_rate,
        "averageSpreadBps": mean(spreads) if spreads else None,
        "maximumSpreadBps": max(spreads) if spreads else None,
        "maximumQuoteGapMs": max(gaps) if gaps else 0.0,
        "latestQuoteAgeMs": latest_age_ms,
        "reasonCodes": tuple(reason_codes),
        "unitConvention": {"spreadBps": "basis_points", "quoteGapMs": "milliseconds"},
    }


def _validate_trades(
    trades: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    *,
    policy: RealFeedValidationPolicy,
) -> dict[str, Any]:
    ordered_trades = sorted((row for row in trades if row["timestamp"] is not None), key=lambda row: row["timestamp"])
    ordered_quotes = sorted((row for row in quotes if row["timestamp"] is not None), key=lambda row: row["timestamp"])
    chronological = _strictly_increasing(row["timestamp"] for row in trades if row["timestamp"] is not None)
    valid_trades = [row for row in ordered_trades if row["price"] is not None and row["price"] > 0 and row["size"] is not None and row["size"] > 0]
    matched = [_trade_nbbo_match(row, ordered_quotes) for row in valid_trades]
    covered = [item for item in matched if item["quoteCovered"]]
    outside = [item for item in covered if item["outsideNbbo"]]
    outside_rate = len(outside) / len(covered) if covered else 1.0
    reason_codes: list[str] = []
    if len(valid_trades) < policy.minimum_trade_count:
        reason_codes.append("regime.feed_validation.insufficient_real_trades")
    if not chronological:
        reason_codes.append("regime.feed_validation.trades_not_chronological")
    if outside_rate > policy.maximum_trade_outside_nbbo_rate:
        reason_codes.append("regime.feed_validation.trade_prices_outside_nbbo")
    if not covered:
        reason_codes.append("regime.feed_validation.trades_lack_quote_coverage")
    return {
        "passed": not reason_codes,
        "tradeCount": len(trades),
        "validTradeCount": len(valid_trades),
        "chronological": chronological,
        "quoteCoveredTradeCount": len(covered),
        "tradeOutsideNbboCount": len(outside),
        "tradeOutsideNbboRate": outside_rate,
        "reasonCodes": tuple(reason_codes),
    }


def _validate_execution_quality(
    fills: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    policy: RealFeedValidationPolicy,
) -> dict[str, Any]:
    ordered_quotes = sorted((row for row in quotes if row["timestamp"] is not None), key=lambda row: row["timestamp"])
    ordered_trades = sorted((row for row in trades if row["timestamp"] is not None), key=lambda row: row["timestamp"])
    lifecycle_rows = [row for row in fills if row["submittedQuantity"] and row["submittedQuantity"] > 0]
    filled_rows = [row for row in lifecycle_rows if row["filledQuantity"] and row["filledQuantity"] > 0]
    partial_rows = [row for row in lifecycle_rows if 0 < (row["filledQuantity"] or 0) < row["submittedQuantity"]]
    non_fill_rows = [row for row in lifecycle_rows if (row["filledQuantity"] or 0) <= 0]
    fill_rate = len(filled_rows) / len(lifecycle_rows) if lifecycle_rows else 0.0
    partial_rate = len(partial_rows) / len(lifecycle_rows) if lifecycle_rows else 0.0
    non_fill_rate = len(non_fill_rows) / len(lifecycle_rows) if lifecycle_rows else 1.0
    fill_without_quote_count = 0
    latencies: list[float] = []
    slippages: list[float] = []
    trade_covered_fills = 0
    for row in filled_rows:
        submit_quote = _latest_quote_at_or_before(ordered_quotes, row["submittedAt"])
        if submit_quote is None:
            fill_without_quote_count += 1
        else:
            mid = (submit_quote["bid"] + submit_quote["ask"]) / 2
            if row["averageFillPrice"] is not None and mid > 0:
                slippages.append(_signed_slippage_bps(row["side"], row["averageFillPrice"], mid))
        if row["submittedAt"] is not None and row["filledAt"] is not None:
            latencies.append(max(0.0, (row["filledAt"] - row["submittedAt"]).total_seconds() * 1000))
        nearest_trade = _nearest_trade(ordered_trades, row["filledAt"])
        if nearest_trade is not None:
            trade_covered_fills += 1
    fill_without_quote_rate = fill_without_quote_count / len(filled_rows) if filled_rows else 0.0
    average_latency = mean(latencies) if latencies else None
    average_slippage = mean(slippages) if slippages else None
    reason_codes: list[str] = []
    if len(lifecycle_rows) < policy.minimum_fill_lifecycle_count:
        reason_codes.append("regime.feed_validation.insufficient_broker_fill_lifecycle")
    if fill_without_quote_rate > policy.maximum_fill_without_quote_rate:
        reason_codes.append("regime.feed_validation.fill_lacks_arrival_quote")
    if fill_rate < policy.minimum_fill_rate:
        reason_codes.append("regime.feed_validation.fill_rate_too_low")
    if partial_rate > policy.maximum_partial_fill_rate:
        reason_codes.append("regime.feed_validation.partial_fill_rate_too_high")
    if non_fill_rate > policy.maximum_non_fill_rate:
        reason_codes.append("regime.feed_validation.non_fill_rate_too_high")
    if average_latency is not None and average_latency > policy.maximum_average_fill_latency_ms:
        reason_codes.append("regime.feed_validation.fill_latency_too_high")
    if average_slippage is not None and average_slippage > policy.maximum_average_slippage_bps:
        reason_codes.append("regime.feed_validation.slippage_too_high")
    return {
        "passed": not reason_codes,
        "fillLifecycleCount": len(lifecycle_rows),
        "filledCount": len(filled_rows),
        "fillRate": fill_rate,
        "partialFillRate": partial_rate,
        "nonFillRate": non_fill_rate,
        "fillWithoutArrivalQuoteRate": fill_without_quote_rate,
        "averageFillLatencyMs": average_latency,
        "averageSignedSlippageBps": average_slippage,
        "tradeCoveredFillCount": trade_covered_fills,
        "reasonCodes": tuple(reason_codes),
        "unitConvention": {"latency": "milliseconds", "slippage": "basis_points_vs_arrival_mid"},
    }


def _quote(row: Mapping[str, Any]) -> dict[str, Any]:
    bid = _number(_first(row, "bid", "bidPrice", "bid_price", "bestBid", "best_bid"))
    ask = _number(_first(row, "ask", "askPrice", "ask_price", "bestAsk", "best_ask"))
    return {
        "timestamp": _parse_time(_first(row, "timestamp", "t", "quoteTimestamp", "quote_timestamp")),
        "bid": bid,
        "ask": ask,
        "bidSize": _number(_first(row, "bidSize", "bid_size")),
        "askSize": _number(_first(row, "askSize", "ask_size")),
    }


def _trade(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _parse_time(_first(row, "timestamp", "t", "tradeTimestamp", "trade_timestamp")),
        "price": _number(_first(row, "price", "p", "tradePrice", "trade_price")),
        "size": _number(_first(row, "size", "s", "quantity", "qty", "volume")),
    }


def _fill(row: Mapping[str, Any]) -> dict[str, Any]:
    fill = row.get("fill") if isinstance(row.get("fill"), Mapping) else {}
    return {
        "side": str(_first(row, "side") or _first(fill, "side") or "").lower(),
        "submittedAt": _parse_time(_first(row, "orderSubmissionTimestamp", "submittedAt", "submitted_at")),
        "filledAt": _parse_time(_first(row, "fillTimestamp", "filledAt", "filled_at") or _first(fill, "filledAt", "filled_at")),
        "submittedQuantity": _number(_first(row, "submittedQuantity", "submitted_quantity", "quantity", "qty")),
        "filledQuantity": _number(_first(row, "filledQuantity", "filled_quantity") or _first(fill, "filledQuantity", "filled_quantity")),
        "averageFillPrice": _number(_first(row, "averageFillPrice", "average_fill_price", "avgFillPrice") or _first(fill, "averageFillPrice", "average_fill_price")),
        "status": str(_first(row, "status") or _first(fill, "status") or "").upper(),
    }


def _trade_nbbo_match(trade: Mapping[str, Any], quotes: list[dict[str, Any]]) -> dict[str, Any]:
    quote = _latest_quote_at_or_before(quotes, trade["timestamp"])
    if quote is None:
        return {"quoteCovered": False, "outsideNbbo": True}
    price = trade["price"]
    tolerance = max(0.01, (quote["ask"] - quote["bid"]) * 0.25)
    return {
        "quoteCovered": True,
        "outsideNbbo": price < quote["bid"] - tolerance or price > quote["ask"] + tolerance,
    }


def _latest_quote_at_or_before(quotes: list[dict[str, Any]], timestamp: datetime | None) -> dict[str, Any] | None:
    if timestamp is None:
        return None
    candidate = None
    for quote in quotes:
        if quote["timestamp"] is not None and quote["bid"] is not None and quote["ask"] is not None and quote["ask"] > quote["bid"] and quote["timestamp"] <= timestamp:
            candidate = quote
        if quote["timestamp"] is not None and quote["timestamp"] > timestamp:
            break
    return candidate


def _nearest_trade(trades: list[dict[str, Any]], timestamp: datetime | None, *, max_distance_ms: float = 1_000.0) -> dict[str, Any] | None:
    if timestamp is None:
        return None
    nearest = None
    nearest_distance = max_distance_ms
    for trade in trades:
        if trade["timestamp"] is None:
            continue
        distance = abs((trade["timestamp"] - timestamp).total_seconds() * 1000)
        if distance <= nearest_distance:
            nearest = trade
            nearest_distance = distance
    return nearest


def _signed_slippage_bps(side: str, fill_price: float, arrival_mid: float) -> float:
    if side in {"sell", "short"}:
        return ((arrival_mid - fill_price) / arrival_mid) * 10_000
    return ((fill_price - arrival_mid) / arrival_mid) * 10_000


def _strictly_increasing(values: Iterable[datetime]) -> bool:
    items = list(values)
    return all(left < right for left, right in zip(items, items[1:]))


def _first(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
