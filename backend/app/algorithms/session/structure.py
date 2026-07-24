"""Point-in-time Session structure evidence."""

from __future__ import annotations

from statistics import mean
from typing import Any, Literal

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig


StructureBehavior = Literal[
    "trend_up",
    "trend_down",
    "balanced_range",
    "mean_reverting",
    "choppy",
    "valid_breakout_up",
    "valid_breakout_down",
    "failed_breakout_up",
    "failed_breakout_down",
    "reversal_up",
    "reversal_down",
    "unknown",
]


def analyze_session_structure(candles: list[dict[str, Any]], context: dict[str, Any] | None = None, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> dict[str, Any]:
    if len(candles) < config.minimum_behavior_bars:
        return {
            "status": "not_ready",
            "behavior": "unknown",
            "reasonCodes": ("session.structure.not_enough_bars",),
            "swing": _empty_swing(),
            "auction": {},
            "breakout": _empty_breakout(),
            "pullback": _empty_pullback(),
            "trendChop": {},
        }
    bars = [_bar(candle) for candle in candles]
    context = context or {}
    swing = _swing_structure(bars, config)
    trend_chop = _trend_chop(bars, context, config)
    auction = _auction_behavior(bars, context, config)
    breakout = _breakout_behavior(bars, context, config)
    pullback = _pullback_behavior(bars, config)
    behavior, reason_codes = _behavior_from_evidence(swing, trend_chop, auction, breakout, pullback, config)
    return {
        "status": "ready",
        "behavior": behavior,
        "reasonCodes": tuple(dict.fromkeys(reason_codes)),
        "swing": swing,
        "auction": auction,
        "breakout": breakout,
        "pullback": pullback,
        "trendChop": trend_chop,
    }


def legacy_pullback_depth_value(structure: dict[str, Any] | None) -> str:
    pullback = (structure or {}).get("pullback") or {}
    fraction = pullback.get("depthFraction")
    atr = pullback.get("depthAtr")
    if fraction is None:
        return "not-ready"
    if atr is None:
        return f"{float(fraction) * 100:.1f}%"
    return f"{float(fraction) * 100:.1f}% / {float(atr):.2f} ATR"


def same_time_volume_value(participation: dict[str, Any] | None) -> str:
    evidence = participation or {}
    expected = evidence.get("expectedCumulativeVolume")
    ratio = evidence.get("volumePaceRatio")
    if expected is None or ratio is None:
        return "not-ready"
    return f"{float(expected):.0f} / {float(ratio):.2f}x"


def _swing_structure(bars: list[dict[str, float]], config: SessionConfig) -> dict[str, Any]:
    swings_high = _swing_points(bars, field="high", lookback=config.structure_swing_lookback_bars, mode="high")
    swings_low = _swing_points(bars, field="low", lookback=config.structure_swing_lookback_bars, mode="low")
    sampled_highs = [point["value"] for point in swings_high[-3:]] or _sampled_values(bars, "high")
    sampled_lows = [point["value"] for point in swings_low[-3:]] or _sampled_values(bars, "low")
    higher_highs = _rising(sampled_highs)
    higher_lows = _rising(sampled_lows)
    lower_highs = _falling(sampled_highs)
    lower_lows = _falling(sampled_lows)
    latest_close = bars[-1]["close"]
    prior_high = max((point["value"] for point in swings_high[:-1]), default=max(bar["high"] for bar in bars[:-1]))
    prior_low = min((point["value"] for point in swings_low[:-1]), default=min(bar["low"] for bar in bars[:-1]))
    bos_up = latest_close > prior_high
    bos_down = latest_close < prior_low
    first_half_net = bars[len(bars) // 2]["close"] - bars[0]["open"]
    second_half_net = bars[-1]["close"] - bars[len(bars) // 2]["close"]
    choch_up = (lower_highs and lower_lows and bos_up) or (first_half_net < 0 and second_half_net > abs(first_half_net) * 0.8 and bos_up)
    choch_down = (higher_highs and higher_lows and bos_down) or (first_half_net > 0 and second_half_net < -abs(first_half_net) * 0.8 and bos_down)
    return {
        "higherHighs": higher_highs,
        "higherLows": higher_lows,
        "lowerHighs": lower_highs,
        "lowerLows": lower_lows,
        "breakOfStructure": "up" if bos_up else "down" if bos_down else "none",
        "changeOfCharacter": "up" if choch_up else "down" if choch_down else "none",
        "swingHighCount": len(swings_high),
        "swingLowCount": len(swings_low),
        "latestSwingHigh": swings_high[-1] if swings_high else None,
        "latestSwingLow": swings_low[-1] if swings_low else None,
        "reasonCodes": tuple(
            reason
            for reason, active in {
                "session.structure.swing.hh_hl": higher_highs and higher_lows,
                "session.structure.swing.lh_ll": lower_highs and lower_lows,
                "session.structure.bos_up": bos_up,
                "session.structure.bos_down": bos_down,
                "session.structure.choch_up": choch_up,
                "session.structure.choch_down": choch_down,
            }.items()
            if active
        ),
    }


def _auction_behavior(bars: list[dict[str, float]], context: dict[str, Any], config: SessionConfig) -> dict[str, Any]:
    opening = context.get("openingRanges") or {}
    breakouts = opening.get("breakouts") or {}
    or5 = breakouts.get("OR5") or {}
    vwap = context.get("vwapFeatures") or {}
    vwap_current = vwap.get("current") or {}
    latest = bars[-1]
    prior_day_high = _number(context.get("priorDayHigh"))
    prior_day_low = _number(context.get("priorDayLow"))
    premarket_high = _number(context.get("premarketHigh"))
    premarket_low = _number(context.get("premarketLow"))
    derived_failed_direction = _prior_failed_breakout_direction(bars, opening)
    failed_acceptance = _failed_acceptance_count(opening) + (1 if derived_failed_direction else 0)
    opening_rejection = bool(or5.get("rejectionBackInside") or derived_failed_direction)
    return {
        "openingRangeAcceptance": bool(or5.get("accepted")),
        "openingRangeRejection": opening_rejection,
        "priorDayHighInteraction": _level_interaction(latest, prior_day_high),
        "priorDayLowInteraction": _level_interaction(latest, prior_day_low),
        "premarketHighInteraction": _level_interaction(latest, premarket_high),
        "premarketLowInteraction": _level_interaction(latest, premarket_low),
        "vwapReclaim": "above" if vwap_current.get("reclaimAbove") else "below" if vwap_current.get("reclaimBelow") else "none",
        "vwapRejection": "above" if vwap_current.get("rejectionAbove") else "below" if vwap_current.get("rejectionBelow") else "none",
        "repeatedFailedAcceptance": failed_acceptance >= config.structure_failed_acceptance_threshold,
        "failedAcceptanceCount": failed_acceptance,
        "reasonCodes": tuple(
            reason
            for reason, active in {
                "session.structure.auction.or_acceptance": bool(or5.get("accepted")),
                "session.structure.auction.or_rejection": opening_rejection,
                "session.structure.auction.vwap_reclaim": bool(vwap_current.get("reclaimAbove") or vwap_current.get("reclaimBelow")),
                "session.structure.auction.vwap_rejection": bool(vwap_current.get("rejectionAbove") or vwap_current.get("rejectionBelow")),
                "session.structure.auction.repeated_failed_acceptance": failed_acceptance >= config.structure_failed_acceptance_threshold,
            }.items()
            if active
        ),
    }


def _breakout_behavior(bars: list[dict[str, float]], context: dict[str, Any], config: SessionConfig) -> dict[str, Any]:
    opening = context.get("openingRanges") or {}
    breakouts = opening.get("breakouts") or {}
    or5 = breakouts.get("OR5") or {}
    direction = or5.get("direction") or "inside"
    close_beyond = bool(or5.get("closeBeyondRange"))
    accepted = bool(or5.get("accepted"))
    failed = bool(or5.get("failedBreakout"))
    retest = bool(or5.get("retest"))
    volume_ratio = _number((context.get("participationEvidence") or {}).get("oneMinuteRelativeVolume")) or _recent_volume_ratio(bars)
    bars_since = _bars_since_breakout(bars, opening)
    valid = close_beyond and accepted and volume_ratio >= config.structure_valid_breakout_volume_ratio and bars_since is not None and bars_since <= config.breakout_max_bars_after_acceptance
    sweep = bool(or5.get("wickBeyondRange") and not close_beyond)
    prior_failed_direction = _prior_failed_breakout_direction(bars, opening)
    if prior_failed_direction:
        failed = True
        direction = prior_failed_direction
        valid = False
    distance = or5.get("distanceFromRangeBps")
    return {
        "direction": direction,
        "validBreakout": valid,
        "failedBreakout": failed,
        "liquiditySweep": sweep,
        "retestSuccess": retest and not failed,
        "retestFailure": retest and failed,
        "barsSinceBreakout": bars_since,
        "breakoutDistanceBps": distance,
        "volumeConfirmation": volume_ratio >= config.structure_valid_breakout_volume_ratio,
        "volumeRatio": volume_ratio,
        "reasonCodes": tuple(
            reason
            for reason, active in {
                "session.structure.breakout.valid": valid,
                "session.structure.breakout.failed": failed,
                "session.structure.breakout.liquidity_sweep": sweep,
                "session.structure.breakout.retest_success": retest and not failed,
                "session.structure.breakout.retest_failure": retest and failed,
                "session.structure.breakout.volume_confirmed": volume_ratio >= config.structure_valid_breakout_volume_ratio,
            }.items()
            if active
        ),
    }


def _pullback_behavior(bars: list[dict[str, float]], config: SessionConfig) -> dict[str, Any]:
    direction = "up" if bars[-1]["close"] >= bars[0]["open"] else "down"
    impulse_start_index, impulse_end_index = _impulse_indices(bars, direction)
    impulse_start = bars[impulse_start_index]
    impulse_end = bars[impulse_end_index]
    impulse = (impulse_end["high"] - impulse_start["low"]) if direction == "up" else (impulse_start["high"] - impulse_end["low"])
    if impulse <= 0 or impulse_end_index >= len(bars) - 2:
        return _empty_pullback()
    pullback_bars = bars[impulse_end_index + 1 :]
    if direction == "up":
        deepest = min(bar["low"] for bar in pullback_bars)
        depth = max(0.0, impulse_end["high"] - deepest)
        origin_protected = deepest > impulse_start["low"]
        continuation = bars[-1]["close"] > max(bar["high"] for bar in pullback_bars[:-1] or pullback_bars)
    else:
        deepest = max(bar["high"] for bar in pullback_bars)
        depth = max(0.0, deepest - impulse_end["low"])
        origin_protected = deepest < impulse_start["high"]
        continuation = bars[-1]["close"] < min(bar["low"] for bar in pullback_bars[:-1] or pullback_bars)
    depth_fraction = depth / impulse if impulse else None
    atr = _atr(bars)
    impulse_volume = mean(bar["volume"] for bar in bars[max(0, impulse_start_index) : impulse_end_index + 1])
    pullback_volume = mean(bar["volume"] for bar in pullback_bars)
    volume_contraction = pullback_volume <= impulse_volume * config.pullback_volume_contraction_ratio if impulse_volume else False
    shallow = depth_fraction is not None and depth_fraction <= config.shallow_pullback_max_fraction
    deep_invalid = depth_fraction is not None and depth_fraction > config.deep_pullback_max_fraction
    return {
        "direction": direction,
        "depthFraction": depth_fraction,
        "depthAtr": None if not atr else depth / atr,
        "retracementDurationBars": len(pullback_bars),
        "volumeContraction": volume_contraction,
        "originProtected": origin_protected,
        "continuationConfirmation": continuation,
        "shallowValid": shallow and origin_protected and volume_contraction,
        "deepInvalid": deep_invalid or not origin_protected,
        "reasonCodes": tuple(
            reason
            for reason, active in {
                "session.structure.pullback.shallow": shallow,
                "session.structure.pullback.deep_invalid": deep_invalid or not origin_protected,
                "session.structure.pullback.volume_contraction": volume_contraction,
                "session.structure.pullback.origin_protected": origin_protected,
                "session.structure.pullback.continuation": continuation,
            }.items()
            if active
        ),
    }


def _trend_chop(bars: list[dict[str, float]], context: dict[str, Any], config: SessionConfig) -> dict[str, Any]:
    start = bars[0]["open"]
    end = bars[-1]["close"]
    session_high = max(bar["high"] for bar in bars)
    session_low = min(bar["low"] for bar in bars)
    net = end - start
    total_path = sum(abs(bars[index]["close"] - bars[index - 1]["close"]) for index in range(1, len(bars)))
    range_amount = max(session_high - session_low, 0.01)
    net_move_bps = abs(net) / start * 10_000 if start else 0.0
    directional_efficiency = abs(net) / range_amount
    path_efficiency = abs(net) / total_path if total_path else 0.0
    vwap_frequency = _number(context.get("vwapCrossingFrequencyPerHour")) or 0.0
    overlap_ratio = _overlap_ratio(bars)
    choppiness = 1.0 - min(1.0, path_efficiency)
    return {
        "directionalEfficiency": directional_efficiency,
        "netDisplacement": net,
        "netMoveBps": net_move_bps,
        "totalPath": total_path,
        "pathEfficiency": path_efficiency,
        "vwapCrossingFrequencyPerHour": vwap_frequency,
        "overlapRatio": overlap_ratio,
        "pathToRangeRatio": total_path / range_amount if range_amount else 0.0,
        "choppiness": choppiness,
        "reasonCodes": tuple(
            reason
            for reason, active in {
                "session.structure.trend.path_efficient": path_efficiency >= config.trend_path_efficiency_threshold and net_move_bps >= config.structure_trend_minimum_move_bps,
                "session.structure.chop.overlap_high": overlap_ratio >= config.choppy_overlap_ratio_threshold,
                "session.structure.chop.vwap_cross_frequency": vwap_frequency >= config.choppy_vwap_crosses,
            }.items()
            if active
        ),
    }


def _behavior_from_evidence(
    swing: dict[str, Any],
    trend_chop: dict[str, Any],
    auction: dict[str, Any],
    breakout: dict[str, Any],
    pullback: dict[str, Any],
    config: SessionConfig,
) -> tuple[StructureBehavior, list[str]]:
    reasons = [*swing.get("reasonCodes", ()), *trend_chop.get("reasonCodes", ()), *auction.get("reasonCodes", ()), *breakout.get("reasonCodes", ()), *pullback.get("reasonCodes", ())]
    if auction.get("repeatedFailedAcceptance"):
        return "mean_reverting", [*reasons, "session.structure.behavior.mean_reverting"]
    if swing["changeOfCharacter"] == "up":
        return "reversal_up", [*reasons, "session.structure.behavior.reversal_up"]
    if swing["changeOfCharacter"] == "down":
        return "reversal_down", [*reasons, "session.structure.behavior.reversal_down"]
    if breakout["failedBreakout"]:
        return ("failed_breakout_up" if breakout["direction"] == "up" else "failed_breakout_down"), [*reasons, "session.structure.behavior.failed_breakout"]
    trend_ready = trend_chop["pathEfficiency"] >= config.trend_path_efficiency_threshold and trend_chop["netMoveBps"] >= config.structure_trend_minimum_move_bps
    if pullback["shallowValid"] and trend_ready:
        if pullback["direction"] == "up":
            return "trend_up", [*reasons, "session.structure.behavior.trend_up", "session.structure.behavior.pullback_continuation"]
        return "trend_down", [*reasons, "session.structure.behavior.trend_down", "session.structure.behavior.pullback_continuation"]
    if breakout["validBreakout"]:
        return ("valid_breakout_up" if breakout["direction"] == "up" else "valid_breakout_down"), [*reasons, "session.structure.behavior.valid_breakout"]
    if trend_chop["overlapRatio"] >= config.choppy_overlap_ratio_threshold and trend_chop["vwapCrossingFrequencyPerHour"] >= config.choppy_vwap_crosses and trend_chop["pathToRangeRatio"] >= config.choppy_path_ratio_threshold:
        return "choppy", [*reasons, "session.structure.behavior.choppy"]
    if swing["higherHighs"] and swing["higherLows"] and trend_ready:
        return "trend_up", [*reasons, "session.structure.behavior.trend_up"]
    if swing["lowerHighs"] and swing["lowerLows"] and trend_ready:
        return "trend_down", [*reasons, "session.structure.behavior.trend_down"]
    return "balanced_range", [*reasons, "session.structure.behavior.balanced_range"]


def _swing_points(bars: list[dict[str, float]], *, field: str, lookback: int, mode: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index in range(lookback, len(bars) - lookback):
        value = bars[index][field]
        window = [bars[item][field] for item in range(index - lookback, index + lookback + 1) if item != index]
        if mode == "high" and all(value >= item for item in window):
            points.append({"index": index, "value": value})
        if mode == "low" and all(value <= item for item in window):
            points.append({"index": index, "value": value})
    return points


def _level_interaction(latest: dict[str, float], level: float | None) -> str:
    if level is None:
        return "not_provided"
    if latest["high"] > level and latest["close"] <= level:
        return "rejection"
    if latest["close"] > level:
        return "accepted_above"
    if latest["low"] < level and latest["close"] >= level:
        return "sweep_reclaim"
    if latest["close"] < level:
        return "below"
    return "touch"


def _failed_acceptance_count(opening: dict[str, Any]) -> int:
    breakouts = opening.get("breakouts") or {}
    return sum(1 for breakout in breakouts.values() if breakout.get("failedBreakout") or breakout.get("rejectionBackInside"))


def _bars_since_breakout(bars: list[dict[str, float]], opening: dict[str, Any]) -> int | None:
    references = opening.get("references") or {}
    or5 = references.get("OR5") or {}
    high = or5.get("high")
    low = or5.get("low")
    if high is None or low is None:
        return None
    for index, bar in enumerate(bars):
        if bar["close"] > float(high) or bar["close"] < float(low):
            return len(bars) - 1 - index
    return None


def _prior_failed_breakout_direction(bars: list[dict[str, float]], opening: dict[str, Any]) -> str | None:
    references = opening.get("references") or {}
    or5 = references.get("OR5") or {}
    high = or5.get("high")
    low = or5.get("low")
    if high is None or low is None:
        return None
    high = float(high)
    low = float(low)
    saw_up = False
    saw_down = False
    for bar in bars:
        close = bar["close"]
        if close > high:
            saw_up = True
        if close < low:
            saw_down = True
        if saw_up and close < high:
            return "up"
        if saw_down and close > low:
            return "down"
    return None


def _recent_volume_ratio(bars: list[dict[str, float]]) -> float:
    if len(bars) < 6:
        return 1.0
    recent = mean(bar["volume"] for bar in bars[-3:])
    base = mean(bar["volume"] for bar in bars[:-3])
    return recent / base if base else 1.0


def _impulse_indices(bars: list[dict[str, float]], direction: str) -> tuple[int, int]:
    if direction == "up":
        low_index = min(range(max(1, len(bars) // 3)), key=lambda index: bars[index]["low"])
        for index in range(low_index + 2, len(bars) - 2):
            if bars[index]["high"] >= max(bar["high"] for bar in bars[low_index:index + 1]) and min(bar["low"] for bar in bars[index + 1 :]) < bars[index]["high"]:
                return low_index, index
        high_index = max(range(low_index + 1, len(bars) - 2) or [low_index], key=lambda index: bars[index]["high"])
        return low_index, high_index
    high_index = max(range(max(1, len(bars) // 3)), key=lambda index: bars[index]["high"])
    for index in range(high_index + 2, len(bars) - 2):
        if bars[index]["low"] <= min(bar["low"] for bar in bars[high_index:index + 1]) and max(bar["high"] for bar in bars[index + 1 :]) > bars[index]["low"]:
            return high_index, index
    low_index = min(range(high_index + 1, len(bars) - 2) or [high_index], key=lambda index: bars[index]["low"])
    return high_index, low_index


def _overlap_ratio(bars: list[dict[str, float]]) -> float:
    if len(bars) < 2:
        return 0.0
    overlaps = 0
    for index in range(1, len(bars)):
        high = min(bars[index]["high"], bars[index - 1]["high"])
        low = max(bars[index]["low"], bars[index - 1]["low"])
        if high > low:
            overlaps += 1
    return overlaps / (len(bars) - 1)


def _atr(bars: list[dict[str, float]], period: int = 14) -> float | None:
    sample = bars[-period:]
    if not sample:
        return None
    return mean(bar["high"] - bar["low"] for bar in sample)


def _bar(candle: dict[str, Any]) -> dict[str, float]:
    return {
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": float(candle.get("volume") or 0.0),
    }


def _rising(values: list[float]) -> bool:
    return len(values) >= 2 and all(values[index] > values[index - 1] for index in range(1, len(values)))


def _falling(values: list[float]) -> bool:
    return len(values) >= 2 and all(values[index] < values[index - 1] for index in range(1, len(values)))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sampled_values(bars: list[dict[str, float]], field: str) -> list[float]:
    if len(bars) < 6:
        return [bar[field] for bar in bars]
    step = max(1, len(bars) // 4)
    return [bars[index][field] for index in range(0, len(bars), step)][-4:]


def _empty_swing() -> dict[str, Any]:
    return {"higherHighs": False, "higherLows": False, "lowerHighs": False, "lowerLows": False, "breakOfStructure": "none", "changeOfCharacter": "none", "reasonCodes": ()}


def _empty_breakout() -> dict[str, Any]:
    return {"validBreakout": False, "failedBreakout": False, "liquiditySweep": False, "retestSuccess": False, "retestFailure": False, "barsSinceBreakout": None, "reasonCodes": ()}


def _empty_pullback() -> dict[str, Any]:
    return {"depthFraction": None, "depthAtr": None, "retracementDurationBars": None, "volumeContraction": False, "originProtected": False, "continuationConfirmation": False, "shallowValid": False, "deepInvalid": False, "reasonCodes": ("session.structure.pullback.not_ready",)}
