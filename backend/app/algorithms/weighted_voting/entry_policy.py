"""Entry policy checks for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backend.app.algorithms.weighted_voting.models import (
    WeightedDecision,
    WeightedEffectiveSettings,
    WeightedMarketSnapshot,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume, vwap


WEIGHTED_VOTING_ENTRY_POLICY_VERSION = "weighted_voting_entry_policy_v2"


class WeightedEntryType(str, Enum):
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"
    CONFIRMATION_LIMIT = "confirmation_limit"
    REJECTED = "rejected"


@dataclass(frozen=True)
class WeightedEntryPolicyResult:
    accepted: bool
    dominant_family: WeightedStrategyFamily | str
    entry_type: WeightedEntryType | str
    side: WeightedSide | str
    trigger_price: float | None
    limit_price: float | None
    maximum_chase_distance: float
    entry_timeout_seconds: int
    entry_buffer: float
    cancellation_condition: str
    reason_codes: tuple[str, ...]
    explanation: str


def evaluate_entry_policy(
    *,
    decision: WeightedDecision,
    signals: tuple[WeightedVotingSignal, ...],
    snapshot: WeightedMarketSnapshot,
    effective_settings: WeightedEffectiveSettings,
    current_time: datetime | None = None,
) -> WeightedEntryPolicyResult:
    now = current_time or snapshot.data_timestamp
    side = decision.proposed_side
    dominant_family = _dominant_family(signals)
    entry_price = _quote_entry_price(snapshot, side)
    if side not in (WeightedSide.BUY.value, WeightedSide.SELL.value) or entry_price is None:
        return _reject(dominant_family, side, "weighted_voting.entry.no_directional_quote", "Directional side and actual quote are required.")
    candles = tuple(candle for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp)
    if len(candles) < 3:
        return _reject(dominant_family, side, "weighted_voting.entry.insufficient_history", "Entry policy requires completed current and historical candles.")
    latest = candles[-1]
    current_vwap = vwap(candles)
    atr = average_true_range(candles, 14) or max(latest.high - latest.low, entry_price * effective_settings.minimum_stop_distance_percent)
    spread = (snapshot.ask or entry_price) - (snapshot.bid or entry_price)
    entry_buffer = max(entry_price * effective_settings.entry_buffer_percent, spread)
    maximum_chase_distance = max(entry_buffer, atr * 0.35)
    timeout_seconds = max(30, min(1800, effective_settings.time_stop_minutes * 60))

    if dominant_family == WeightedStrategyFamily.TREND.value:
        return _trend_policy(side, entry_price, candles, current_vwap, atr, entry_buffer, maximum_chase_distance, timeout_seconds)
    if dominant_family == WeightedStrategyFamily.BREAKOUT.value:
        return _breakout_policy(side, entry_price, candles, snapshot, atr, entry_buffer, maximum_chase_distance, timeout_seconds, effective_settings)
    if dominant_family == WeightedStrategyFamily.MEAN_REVERSION.value:
        return _mean_reversion_policy(side, entry_price, candles, current_vwap, atr, entry_buffer, maximum_chase_distance, timeout_seconds)
    if dominant_family == WeightedStrategyFamily.REVERSAL.value:
        return _reversal_policy(side, entry_price, candles, atr, entry_buffer, maximum_chase_distance, timeout_seconds)
    return _reject(dominant_family, side, "weighted_voting.entry.unknown_family", "Dominant family could not be determined.")


def entry_policy_status() -> dict[str, str]:
    return {
        "version": WEIGHTED_VOTING_ENTRY_POLICY_VERSION,
        "status": "implemented",
        "explanation": "Weighted Voting entry policies are deterministic and family-aware.",
    }


def _trend_policy(side, entry_price, candles, current_vwap, atr, entry_buffer, maximum_chase_distance, timeout_seconds) -> WeightedEntryPolicyResult:
    closes = [candle.close for candle in candles]
    trend_up = closes[-1] > closes[-2] > closes[-3]
    trend_down = closes[-1] < closes[-2] < closes[-3]
    if current_vwap is None:
        return _reject(WeightedStrategyFamily.TREND, side, "weighted_voting.entry.trend.missing_vwap", "Trend entry requires VWAP confirmation.")
    if side == WeightedSide.BUY.value and closes[-1] < current_vwap:
        return _reject(WeightedStrategyFamily.TREND, side, "weighted_voting.entry.trend.vwap_reject", "VWAP rejects Buy trend entry but does not change side.")
    if side == WeightedSide.SELL.value and closes[-1] > current_vwap:
        return _reject(WeightedStrategyFamily.TREND, side, "weighted_voting.entry.trend.vwap_reject", "VWAP rejects Sell trend entry but does not change side.")
    if side == WeightedSide.BUY.value and not trend_up:
        entry_type = WeightedEntryType.LIMIT
        trigger = entry_price
        limit = max(0.01, entry_price - entry_buffer)
    elif side == WeightedSide.SELL.value and not trend_down:
        entry_type = WeightedEntryType.LIMIT
        trigger = entry_price
        limit = entry_price + entry_buffer
    else:
        entry_type = WeightedEntryType.STOP_LIMIT
        trigger = entry_price + entry_buffer if side == WeightedSide.BUY.value else entry_price - entry_buffer
        limit = trigger + entry_buffer if side == WeightedSide.BUY.value else max(0.01, trigger - entry_buffer)
    return _accept(WeightedStrategyFamily.TREND, side, entry_type, trigger, limit, maximum_chase_distance, timeout_seconds, entry_buffer, "Cancel if VWAP or structure alignment fails before fill.", ("weighted_voting.entry.trend.confirmed",))


def _breakout_policy(side, entry_price, candles, snapshot, atr, entry_buffer, maximum_chase_distance, timeout_seconds, settings) -> WeightedEntryPolicyResult:
    previous = candles[:-1]
    level = max(candle.high for candle in previous[-20:]) if side == WeightedSide.BUY.value else min(candle.low for candle in previous[-20:])
    latest = candles[-1]
    volume_base = average_volume(tuple(previous), min(20, len(previous))) or 0.0
    volume_expansion = latest.volume >= volume_base * 1.15 if volume_base > 0 else False
    spread_percent = ((snapshot.ask or entry_price) - (snapshot.bid or entry_price)) / entry_price
    extension = abs(entry_price - level)
    if spread_percent > settings.maximum_spread_percent:
        return _reject(WeightedStrategyFamily.BREAKOUT, side, "weighted_voting.entry.breakout.spread_reject", "Breakout entry spread is above effective threshold.")
    if not volume_expansion:
        return _reject(WeightedStrategyFamily.BREAKOUT, side, "weighted_voting.entry.breakout.volume_reject", "Breakout entry requires volume expansion.")
    if extension > max(maximum_chase_distance, atr * 0.65):
        return _reject(WeightedStrategyFamily.BREAKOUT, side, "weighted_voting.entry.breakout.overextended", "Breakout entry is overextended beyond maximum chase distance.")
    trigger = level + entry_buffer if side == WeightedSide.BUY.value else level - entry_buffer
    limit = trigger + entry_buffer if side == WeightedSide.BUY.value else max(0.01, trigger - entry_buffer)
    return _accept(WeightedStrategyFamily.BREAKOUT, side, WeightedEntryType.STOP_LIMIT, trigger, limit, maximum_chase_distance, timeout_seconds, entry_buffer, "Cancel if price closes back inside the breakout level before fill.", ("weighted_voting.entry.breakout.confirmed_level", "weighted_voting.entry.breakout.volume_expansion"))


def _mean_reversion_policy(side, entry_price, candles, current_vwap, atr, entry_buffer, maximum_chase_distance, timeout_seconds) -> WeightedEntryPolicyResult:
    if current_vwap is None:
        return _reject(WeightedStrategyFamily.MEAN_REVERSION, side, "weighted_voting.entry.mean_reversion.missing_vwap", "Mean reversion entry requires VWAP context.")
    distance_to_vwap = abs(entry_price - current_vwap)
    if distance_to_vwap < atr * 0.25:
        return _reject(WeightedStrategyFamily.MEAN_REVERSION, side, "weighted_voting.entry.mean_reversion.chasing_complete", "Most of the reversion is already complete; chasing is rejected.")
    latest = candles[-1]
    reversal_buy = side == WeightedSide.BUY.value and latest.close > latest.open
    reversal_sell = side == WeightedSide.SELL.value and latest.close < latest.open
    if not (reversal_buy or reversal_sell):
        return _reject(WeightedStrategyFamily.MEAN_REVERSION, side, "weighted_voting.entry.mean_reversion.no_reversal", "Mean reversion entry requires reversal confirmation.")
    trigger = entry_price
    limit = max(0.01, entry_price - entry_buffer) if side == WeightedSide.BUY.value else entry_price + entry_buffer
    return _accept(WeightedStrategyFamily.MEAN_REVERSION, side, WeightedEntryType.LIMIT, trigger, limit, maximum_chase_distance, timeout_seconds, entry_buffer, "Cancel if price reaches VWAP before fill or extension accelerates away.", ("weighted_voting.entry.mean_reversion.range_extension", "weighted_voting.entry.mean_reversion.reversal_confirmed"))


def _reversal_policy(side, entry_price, candles, atr, entry_buffer, maximum_chase_distance, timeout_seconds) -> WeightedEntryPolicyResult:
    latest = candles[-1]
    previous = candles[-2]
    if side == WeightedSide.BUY.value:
        swept = latest.low < previous.low and latest.close > previous.low
        invalidation = latest.low
        trigger = previous.low + entry_buffer
        limit = trigger + entry_buffer
    else:
        swept = latest.high > previous.high and latest.close < previous.high
        invalidation = latest.high
        trigger = previous.high - entry_buffer
        limit = max(0.01, trigger - entry_buffer)
    if not swept:
        return _reject(WeightedStrategyFamily.REVERSAL, side, "weighted_voting.entry.reversal.no_confirmed_failure", "Reversal entry requires confirmed failed level or sweep.")
    if abs(entry_price - trigger) > max(maximum_chase_distance, atr * 0.5):
        return _reject(WeightedStrategyFamily.REVERSAL, side, "weighted_voting.entry.reversal.overextended", "Reversal entry is overextended beyond maximum chase distance.")
    return _accept(WeightedStrategyFamily.REVERSAL, side, WeightedEntryType.CONFIRMATION_LIMIT, trigger, limit, maximum_chase_distance, timeout_seconds, entry_buffer, f"Cancel if price trades beyond structural invalidation {invalidation:.4f}.", ("weighted_voting.entry.reversal.failed_level_confirmed", "weighted_voting.entry.reversal.structural_invalidation"))


def _dominant_family(signals: tuple[WeightedVotingSignal, ...]) -> str:
    totals: dict[str, float] = {}
    for signal in signals:
        if signal.signal not in (WeightedSide.BUY.value, WeightedSide.SELL.value):
            continue
        totals[str(signal.family)] = totals.get(str(signal.family), 0.0) + signal.final_weight * signal.directional_confidence
    if not totals:
        return "unknown"
    return max(sorted(totals), key=lambda family: totals[family])


def _quote_entry_price(snapshot: WeightedMarketSnapshot, side: WeightedSide | str) -> float | None:
    if side == WeightedSide.BUY.value:
        return snapshot.ask
    if side == WeightedSide.SELL.value:
        return snapshot.bid
    return None


def _accept(family, side, entry_type, trigger, limit, chase, timeout, buffer, cancellation, reason_codes) -> WeightedEntryPolicyResult:
    return WeightedEntryPolicyResult(
        accepted=True,
        dominant_family=family.value if isinstance(family, WeightedStrategyFamily) else family,
        entry_type=entry_type.value if isinstance(entry_type, WeightedEntryType) else entry_type,
        side=side,
        trigger_price=round(trigger, 10),
        limit_price=round(limit, 10),
        maximum_chase_distance=round(chase, 10),
        entry_timeout_seconds=timeout,
        entry_buffer=round(buffer, 10),
        cancellation_condition=cancellation,
        reason_codes=tuple(reason_codes),
        explanation="Weighted Voting entry policy accepted the order without changing the weighted side.",
    )


def _reject(family, side, reason_code, explanation) -> WeightedEntryPolicyResult:
    return WeightedEntryPolicyResult(
        accepted=False,
        dominant_family=family.value if isinstance(family, WeightedStrategyFamily) else family,
        entry_type=WeightedEntryType.REJECTED.value,
        side=side,
        trigger_price=None,
        limit_price=None,
        maximum_chase_distance=0.0,
        entry_timeout_seconds=0,
        entry_buffer=0.0,
        cancellation_condition="No entry; rejected by local Weighted Voting entry policy.",
        reason_codes=(reason_code,),
        explanation=explanation,
    )
