"""Isolated Voting Ensemble strategy engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median, pstdev
from typing import Callable

from backend.app.algorithms.voting_ensemble.models import (
    AlgoSignal,
    FeatureValue,
    VotingContextConfirmation,
    VotingCandle,
    VotingEnsembleEvaluateRequest,
    VotingEnsembleEvaluateResponse,
    VotingStrategyVote,
)
from backend.app.algorithms.voting_ensemble.strategies.registry import VOTING_ENSEMBLE_MODULE_INVENTORY


VOTING_ENSEMBLE_SERVICE_VERSION = "voting_ensemble_backend_v1"
StrategyEvaluator = Callable[[VotingEnsembleEvaluateRequest], VotingStrategyVote]


@dataclass(frozen=True)
class _PullbackResult:
    status: str
    detail: str
    index: int | None = None
    anchor: str = ""


@dataclass(frozen=True)
class _BreakoutLevel:
    name: str
    side: str
    value: float
    opposite: float | None = None


class VotingEnsembleService:
    version = VOTING_ENSEMBLE_SERVICE_VERSION

    def evaluate(self, payload: dict) -> dict:
        request = VotingEnsembleEvaluateRequest.model_validate(payload)
        strategy_results = tuple(_apply_strategy_fit(evaluator(request), request) for evaluator in (*DIRECTIONAL_STRATEGIES, *DYNAMIC_ROLE_STRATEGIES))
        directional_votes = tuple(vote for vote in strategy_results if vote.role == "directional")
        context_signals = tuple(_apply_strategy_fit(evaluator(request), request) for evaluator in CONTEXT_STRATEGIES) + tuple(vote for vote in strategy_results if vote.role == "context")
        eligible_votes = tuple(vote for vote in directional_votes if vote.eligible)
        counts = _counts(directional_votes)
        eligible_counts = _counts(eligible_votes)
        family_decision = _family_aware_decision(eligible_votes)
        context_adjustment = _apply_context_to_candidate(family_decision, context_signals)
        final_signal = context_adjustment["signal"]
        context_confirmation = _context_confirmation(final_signal, context_signals)
        timestamp = request.data_timestamp or request.candles[-1].timestamp
        response = VotingEnsembleEvaluateResponse(
            service_version=self.version,
            symbol=request.symbol.upper(),
            evaluated_at=datetime.now(timezone.utc),
            data_timestamp=timestamp,
            final_signal=final_signal,
            votes=directional_votes,
            context_signals=context_signals,
            context_confirmation=context_confirmation,
            counts=counts,
            eligible_counts=eligible_counts,
            family_scores=family_decision["family_scores"],
            base_score=family_decision["base_score"],
            context_adjusted_score=context_adjustment["context_adjusted_score"],
            context_agreements=context_adjustment["context_agreements"],
            context_conflicts=context_adjustment["context_conflicts"],
            context_adjustment_reason=context_adjustment["context_adjustment_reason"],
            family_support=family_decision["family_support"],
            safety_gate_failed=family_decision["safety_gate_failed"],
            reason_codes=("voting_ensemble.evaluate.completed", context_adjustment["reason_code"]),
        )
        return response.model_dump(mode="json")

    def status(self) -> dict:
        return {
            "algorithmId": "voting_ensemble",
            "serviceVersion": self.version,
            "status": "ready",
            "isolated": True,
            "moduleInventory": VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"),
            "directionalStrategies": [evaluator.__name__.removeprefix("evaluate_") for evaluator in DIRECTIONAL_STRATEGIES],
            "dynamicRoleStrategies": [evaluator.__name__.removeprefix("evaluate_") for evaluator in DYNAMIC_ROLE_STRATEGIES],
            "contextSignals": [evaluator.__name__.removeprefix("evaluate_") for evaluator in CONTEXT_STRATEGIES],
            "removedVoters": ["Ensemble Strategy Voting"],
            "reasonCodes": ["voting_ensemble.api.ready"],
        }


def evaluate_multi_timeframe_trend(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    candles = request.candles
    trend_1m, detail_1m = _timeframe_trend_state(candles[-80:], "1m")
    five_minute = request.spy_5m_candles or tuple(_aggregate(candles, 5))
    fifteen_minute = request.spy_15m_candles or tuple(_aggregate(candles, 15))
    trend_5m, detail_5m = _timeframe_trend_state(five_minute[-48:], "5m")
    trend_15m, detail_15m = _timeframe_trend_state(fifteen_minute[-32:], "15m")
    long_permission = trend_15m == "up" or (trend_15m == "neutral" and not _confirmed_component_direction(fifteen_minute[-32:], "down"))
    short_permission = trend_15m == "down" or (trend_15m == "neutral" and not _confirmed_component_direction(fifteen_minute[-32:], "up"))
    long_confirmation = trend_5m == "up"
    short_confirmation = trend_5m == "down"
    long_trigger = trend_1m == "up" and _explicit_one_minute_trigger(candles[-20:], "up")
    short_trigger = trend_1m == "down" and _explicit_one_minute_trigger(candles[-20:], "down")
    detail = f"{detail_1m}; {detail_5m}; {detail_15m}"
    if long_permission and long_confirmation and long_trigger:
        return _vote(
            "Multi-Timeframe Trend Alignment",
            "trend",
            "Buy",
            82,
            f"1m fresh trigger is bullish, 5m confirms, and 15m permission is {trend_15m}: {detail}.",
            "voting_ensemble.multi_timeframe.buy_trigger_confirmed",
            features={"hierarchy": "1m_trigger_5m_confirmation_15m_permission", "long_permission": True, "long_confirmation": True, "long_trigger": True},
        )
    if short_permission and short_confirmation and short_trigger:
        return _vote(
            "Multi-Timeframe Trend Alignment",
            "trend",
            "Sell",
            82,
            f"1m fresh trigger is bearish, 5m confirms, and 15m permission is {trend_15m}: {detail}.",
            "voting_ensemble.multi_timeframe.sell_trigger_confirmed",
            features={"hierarchy": "1m_trigger_5m_confirmation_15m_permission", "short_permission": True, "short_confirmation": True, "short_trigger": True},
        )
    return _vote(
        "Multi-Timeframe Trend Alignment",
        "trend",
        "Hold",
        54,
        f"No role-complete trigger/confirmation/permission setup: 1m trigger {long_trigger or short_trigger}, 5m confirmation {trend_5m}, 15m permission {trend_15m}. {detail}.",
        "voting_ensemble.multi_timeframe.no_trigger_confirmation",
        features={
            "hierarchy": "1m_trigger_5m_confirmation_15m_permission",
            "long_permission": long_permission,
            "long_confirmation": long_confirmation,
            "long_trigger": long_trigger,
            "short_permission": short_permission,
            "short_confirmation": short_confirmation,
            "short_trigger": short_trigger,
        },
    )


def evaluate_first_pullback_after_open(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    session = request.candles
    if len(session) < 7:
        return _vote("First Pullback After Open", "trend", "Hold", 20, "Need opening impulse, pullback, and confirmation candles.", "voting_ensemble.first_pullback.insufficient_data")
    impulse = _opening_impulse(session)
    if impulse is None:
        return _vote("First Pullback After Open", "trend", "Hold", 48, "No identifiable opening impulse; strategy does not follow session direction.", "voting_ensemble.first_pullback.no_opening_impulse")
    side, impulse_start, impulse_end, origin, extreme, impulse_volume = impulse
    current_vwap = _vwap(session)
    if current_vwap is None:
        return _vote("First Pullback After Open", "trend", "Hold", 20, "VWAP is unavailable for pullback validation.", "voting_ensemble.first_pullback.missing_vwap")
    if not _initial_trend_established(session, side, impulse_start, impulse_end):
        return _vote("First Pullback After Open", "trend", "Hold", 50, "Opening impulse exists, but price did not establish a valid initial trend.", "voting_ensemble.first_pullback.no_initial_trend")

    pullback = _first_valid_pullback(session, side, impulse_end, origin, extreme, impulse_volume)
    if pullback.status == "invalidated":
        return _vote("First Pullback After Open", "trend", "Hold", 35, pullback.detail, "voting_ensemble.first_pullback.impulse_origin_broken")
    if pullback.status == "vwap_lost":
        return _vote("First Pullback After Open", "trend", "Hold", 40, pullback.detail, "voting_ensemble.first_pullback.vwap_lost")
    if pullback.status == "high_volume":
        return _vote("First Pullback After Open", "trend", "Hold", 45, pullback.detail, "voting_ensemble.first_pullback.pullback_volume_too_high")
    if pullback.index is None:
        return _vote("First Pullback After Open", "trend", "Hold", 55, pullback.detail, "voting_ensemble.first_pullback.no_qualified_pullback")
    if len(session) - 1 - pullback.index > 3:
        return _vote("First Pullback After Open", "trend", "Hold", 45, "The first qualified pullback already passed; later pullbacks are not labeled as first pullback.", "voting_ensemble.first_pullback.already_completed")

    latest = session[-1]
    previous = session[-2]
    if side == "Buy" and _bullish_confirmation(latest, previous) and latest.close > current_vwap:
        return _vote(
            "First Pullback After Open",
            "trend",
            "Buy",
            78,
            f"Bullish opening impulse, valid lower-volume pullback to {pullback.anchor}, no origin break, and bullish confirmation candle.",
            "voting_ensemble.first_pullback.completed_buy",
        )
    if side == "Sell" and _bearish_confirmation(latest, previous) and latest.close < current_vwap:
        return _vote(
            "First Pullback After Open",
            "trend",
            "Sell",
            78,
            f"Bearish opening impulse, valid lower-volume pullback to {pullback.anchor}, no origin break, and bearish confirmation candle.",
            "voting_ensemble.first_pullback.completed_sell",
        )
    return _vote("First Pullback After Open", "trend", "Hold", 59, "First pullback is valid, but rejection/continuation confirmation is incomplete.", "voting_ensemble.first_pullback.waiting_for_confirmation")


def evaluate_failed_breakout_strategy(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    candles = request.candles
    if len(candles) < 18:
        return _vote("Failed Breakout Strategy", "reversal", "Hold", 20, "Need opening range, breakout failure, and next-candle confirmation.", "voting_ensemble.failed_breakout.insufficient_data")
    failed = candles[-2]
    confirmation = candles[-1]
    levels = _failed_breakout_levels(request)
    if not levels:
        return _vote("Failed Breakout Strategy", "reversal", "Hold", 20, "No actual opening-range, prior-day, premarket, or recent swing level is available.", "voting_ensemble.failed_breakout.no_levels")
    for level in levels:
        if level.side == "resistance" and _failed_resistance_breakout(level, failed, confirmation):
            return _vote(
                "Failed Breakout Strategy",
                "reversal",
                "Sell",
                80,
                f"Price traded above {level.name} {level.value:.2f}, closed back inside, then the next candle confirmed weakness.",
                "voting_ensemble.failed_breakout.sell",
            )
        if level.side == "support" and _failed_support_breakout(level, failed, confirmation):
            return _vote(
                "Failed Breakout Strategy",
                "reversal",
                "Buy",
                80,
                f"Price traded below {level.name} {level.value:.2f}, closed back inside, then the next candle confirmed strength.",
                "voting_ensemble.failed_breakout.buy",
            )
    level_names = ", ".join(f"{level.name} {level.value:.2f}" for level in levels[:6])
    return _vote("Failed Breakout Strategy", "reversal", "Hold", 56, f"No failed breakout with next-candle confirmation at actual levels: {level_names}.", "voting_ensemble.failed_breakout.none")


def evaluate_liquidity_sweep_reversal(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    candles = request.candles
    if len(candles) < 18:
        return _vote("Liquidity Sweep Reversal", "reversal", "Hold", 20, "Need reference levels plus a completed sweep candle.", "voting_ensemble.liquidity_sweep.insufficient_data")
    latest = candles[-1]
    levels = _liquidity_sweep_levels(request)
    if not levels:
        return _vote("Liquidity Sweep Reversal", "reversal", "Hold", 20, "No reference level is available for liquidity sweep detection.", "voting_ensemble.liquidity_sweep.no_levels")
    activity_confirmed, activity_detail = _rejection_activity_confirmation(candles)
    if not activity_confirmed:
        level_names = ", ".join(f"{level.name} {level.value:.2f}" for level in levels[:6])
        return _vote("Liquidity Sweep Reversal", "reversal", "Hold", 55, f"No activity-confirmed rejection at reference levels: {level_names}. {activity_detail}.", "voting_ensemble.liquidity_sweep.no_activity_confirmation")
    for level in levels:
        if level.side == "support" and _support_liquidity_sweep(level, latest):
            return _vote(
                "Liquidity Sweep Reversal",
                "reversal",
                "Buy",
                79,
                f"Low swept below {level.name} {level.value:.2f}, wick rejected, close returned above the level, and {activity_detail}.",
                "voting_ensemble.liquidity_sweep.buy",
            )
        if level.side == "resistance" and _resistance_liquidity_sweep(level, latest):
            return _vote(
                "Liquidity Sweep Reversal",
                "reversal",
                "Sell",
                79,
                f"High swept above {level.name} {level.value:.2f}, wick rejected, close returned below the level, and {activity_detail}.",
                "voting_ensemble.liquidity_sweep.sell",
            )
    level_names = ", ".join(f"{level.name} {level.value:.2f}" for level in levels[:6])
    return _vote("Liquidity Sweep Reversal", "reversal", "Hold", 55, f"No level penetration plus rejection and close-back-through at reference levels: {level_names}.", "voting_ensemble.liquidity_sweep.none")


def evaluate_bollinger_band_reversion(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    candles = request.candles
    if len(candles) < 40:
        return _vote("Bollinger Band Reversion", "mean_reversion", "Hold", 20, "Need Bollinger warm-up candles.", "voting_ensemble.bollinger.insufficient_data")
    closes = [candle.close for candle in candles]
    bands = _bollinger(closes[-25:-1])
    if bands is None:
        return _vote("Bollinger Band Reversion", "mean_reversion", "Hold", 20, "Bollinger bands unavailable.", "voting_ensemble.bollinger.missing_inputs")
    middle, upper, lower = bands
    latest = candles[-1]
    candidate: AlgoSignal = "Hold"
    if latest.low < lower and latest.close > lower and latest.close > latest.open:
        candidate = "Buy"
    elif latest.high > upper and latest.close < upper and latest.close < latest.open:
        candidate = "Sell"
    if candidate == "Hold":
        return _vote("Bollinger Band Reversion", "mean_reversion", "Hold", 56, "No Bollinger Band extension followed by close back inside the band.", "voting_ensemble.bollinger.no_reentry")

    width_ok, width_detail = _bollinger_width_not_expanding(closes)
    if not width_ok:
        return _vote("Bollinger Band Reversion", "mean_reversion", "Hold", 48, f"Band width regime rejects reversion: {width_detail}.", "voting_ensemble.bollinger.band_expanding")

    trend_ok, trend_detail = _bollinger_trend_regime_ok(candles, candidate)
    if not trend_ok:
        return _vote("Bollinger Band Reversion", "mean_reversion", "Hold", 42, f"Strong trend regime disables Bollinger reversion: {trend_detail}.", "voting_ensemble.bollinger.strong_trend")

    deviation = _normalized_bollinger_deviation(latest.close, middle, upper, lower)
    low_deviation = _normalized_bollinger_deviation(latest.low, middle, upper, lower)
    high_deviation = _normalized_bollinger_deviation(latest.high, middle, upper, lower)
    rsi = _rsi(closes, 14)
    oversold = candidate == "Buy" and ((rsi is not None and rsi <= 35) or low_deviation <= -1.0)
    overbought = candidate == "Sell" and ((rsi is not None and rsi >= 65) or high_deviation >= 1.0)
    extreme_detail = _bollinger_extreme_detail(candidate, rsi, low_deviation, high_deviation)
    if not (oversold or overbought):
        rsi_text = f"RSI {rsi:.1f}" if rsi is not None else "RSI unavailable"
        return _vote(
            "Bollinger Band Reversion",
            "mean_reversion",
            "Hold",
            55,
            f"Band re-entry exists, but oversold/overbought evidence is incomplete ({rsi_text}, normalized close deviation {deviation:.2f}).",
            "voting_ensemble.bollinger.no_extreme_evidence",
        )
    rsi_text = f"RSI {rsi:.1f}" if rsi is not None else "normalized deviation"
    if candidate == "Buy":
        return _vote(
            "Bollinger Band Reversion",
            "mean_reversion",
            "Buy",
            76,
            f"Price extended below lower band {lower:.2f}, closed back inside, {width_detail}, {trend_detail}, and {extreme_detail}.",
            "voting_ensemble.bollinger.buy",
        )
    return _vote(
        "Bollinger Band Reversion",
        "mean_reversion",
        "Sell",
        76,
        f"Price extended above upper band {upper:.2f}, closed back inside, {width_detail}, {trend_detail}, and {extreme_detail}.",
        "voting_ensemble.bollinger.sell",
    )


def evaluate_atr_overextension_reversion(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    candles = request.candles
    atr = _atr(candles)
    current_vwap = _vwap(candles)
    current_ema = _ema([candle.close for candle in candles], 20)
    if atr is None or atr <= 0 or (current_vwap is None and current_ema is None):
        return _vote("ATR Overextension Reversion", "mean_reversion", "Hold", 20, "ATR plus VWAP/EMA anchor unavailable.", "voting_ensemble.atr_overextension.missing_inputs")
    latest = candles[-1]
    distances = _atr_anchor_distances(latest.close, atr, current_vwap, current_ema)
    anchor_name, distance_atr = max(distances, key=lambda item: abs(item[1]))
    threshold = 1.2
    if abs(distance_atr) < threshold:
        detail = ", ".join(f"{name} {distance:.2f} ATR" for name, distance in distances)
        return _vote("ATR Overextension Reversion", "mean_reversion", "Hold", 55, f"ATR distance has not exceeded {threshold:.1f}: {detail}.", "voting_ensemble.atr_overextension.threshold_not_met")

    candidate: AlgoSignal = "Buy" if distance_atr < 0 else "Sell"
    continuation_active, continuation_detail = _atr_continuation_breakout_active(candles, candidate)
    if continuation_active:
        return _vote("ATR Overextension Reversion", "mean_reversion", "Hold", 42, f"ATR extension may be continuation, not reversal: {continuation_detail}.", "voting_ensemble.atr_overextension.continuation_active")

    decelerating, deceleration_detail = _atr_extension_decelerating(candles, anchor_name, current_vwap, current_ema, atr, candidate)
    if not decelerating:
        return _vote("ATR Overextension Reversion", "mean_reversion", "Hold", 50, f"ATR extension exists but momentum has not decelerated: {deceleration_detail}.", "voting_ensemble.atr_overextension.no_deceleration")

    rejection, rejection_detail = _atr_rejection_or_reentry(candles, current_vwap, current_ema, candidate)
    if not rejection:
        return _vote("ATR Overextension Reversion", "mean_reversion", "Hold", 52, f"ATR extension exists but price has not rejected or re-entered toward an anchor: {rejection_detail}.", "voting_ensemble.atr_overextension.no_rejection")

    detail = ", ".join(f"{name} {distance:.2f} ATR" for name, distance in distances)
    if candidate == "Buy":
        return _vote(
            "ATR Overextension Reversion",
            "mean_reversion",
            "Buy",
            77,
            f"Price is overextended below {anchor_name} ({detail}); {deceleration_detail}; {rejection_detail}; {continuation_detail}.",
            "voting_ensemble.atr_overextension.buy",
        )
    return _vote(
        "ATR Overextension Reversion",
        "mean_reversion",
        "Sell",
        77,
        f"Price is overextended above {anchor_name} ({detail}); {deceleration_detail}; {rejection_detail}; {continuation_detail}.",
        "voting_ensemble.atr_overextension.sell",
    )


def evaluate_economic_event_reaction(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    event = (request.market_context or {}).get("event") or {}
    confidence = float(event.get("confidence") or 0)
    direction = str(event.get("directionBias") or "neutral")
    if confidence < 0.6 or direction not in {"long", "short"}:
        return _context("Economic Event Reaction Strategy", "event", "Hold", 45, "No active event reaction with enough confidence; treated as context only.", "voting_ensemble.event.inactive_context")
    candles = request.candles
    if len(candles) < 5:
        return _context("Economic Event Reaction Strategy", "event", "Hold", 20, "Event is active, but post-event candles are insufficient; treated as context until measurable.", "voting_ensemble.event.insufficient_data_context")
    reaction = (candles[-1].close - candles[-4].open) / candles[-4].open
    if direction == "long" and reaction > 0.0005:
        return _vote("Economic Event Reaction Strategy", "event", "Buy", 74, f"Event layer is long and post-event reaction is {reaction:.2%}.", "voting_ensemble.event.buy")
    if direction == "short" and reaction < -0.0005:
        return _vote("Economic Event Reaction Strategy", "event", "Sell", 74, f"Event layer is short and post-event reaction is {reaction:.2%}.", "voting_ensemble.event.sell")
    return _vote("Economic Event Reaction Strategy", "event", "Hold", 55, f"Event layer is {direction}, but measured reaction is only {reaction:.2%}.", "voting_ensemble.event.no_reaction")


def evaluate_relative_strength(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    if len(request.qqq_candles) < 16 or len(request.iwm_candles) < 16 or len(request.candles) < 16:
        return _context("Relative Strength vs QQQ/IWM", "market_regime", "Hold", 0, "Aligned QQQ/IWM candles are unavailable; context cannot confirm direction.", "voting_ensemble.relative_strength.missing_aux")
    aligned = _aligned_relative_strength_closes(request.candles, request.qqq_candles, request.iwm_candles)
    if len(aligned) < 16:
        return _context("Relative Strength vs QQQ/IWM", "market_regime", "Hold", 0, "QQQ/IWM auxiliary candles do not share enough matching timestamps with SPY.", "voting_ensemble.relative_strength.unaligned")
    horizons = (1, 5, 15)
    values = {horizon: _relative_strength_for_horizon(aligned, horizon) for horizon in horizons}
    if any(value is None for value in values.values()):
        return _context("Relative Strength vs QQQ/IWM", "market_regime", "Hold", 0, "Not enough aligned QQQ/IWM history for 1m, 5m, and 15m relative strength.", "voting_ensemble.relative_strength.insufficient_horizons")
    zscore = _relative_strength_zscore(aligned, 5, 60)
    combined = (0.2 * values[1]) + (0.35 * values[5]) + (0.45 * values[15])  # type: ignore[operator]
    zscore_component = zscore if zscore is not None else 0.0
    if combined >= 0.0007 and zscore_component > -2.0:
        signal: AlgoSignal = "Buy"
    elif combined <= -0.0007 and zscore_component < 2.0:
        signal = "Sell"
    elif combined > 0 and zscore_component >= 1.5:
        signal = "Buy"
    elif combined < 0 and zscore_component <= -1.5:
        signal = "Sell"
    else:
        signal = "Hold"
    score = 68 if signal != "Hold" else 50
    zscore_text = f", z-score {zscore:.2f}" if zscore is not None else ", z-score unavailable"
    detail = (
        "Context modifier only: "
        f"RS 1m {values[1]:.2%}, 5m {values[5]:.2%}, 15m {values[15]:.2%}, weighted {combined:.2%}{zscore_text}. "
        "Positive RS can confirm Buy candidates; negative RS can confirm Sell candidates; conflicts should reduce confidence."
    )
    return _context("Relative Strength vs QQQ/IWM", "market_regime", signal, score, detail, "voting_ensemble.relative_strength.ready")


def evaluate_market_breadth(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    feed = _external_breadth_feed(request)
    if feed:
        return _market_breadth_from_feed(feed, request)
    return _market_breadth_from_proxy(request)


DIRECTIONAL_STRATEGIES: tuple[StrategyEvaluator, ...] = (
    evaluate_multi_timeframe_trend,
    evaluate_first_pullback_after_open,
    evaluate_failed_breakout_strategy,
    evaluate_liquidity_sweep_reversal,
    evaluate_bollinger_band_reversion,
)

DYNAMIC_ROLE_STRATEGIES: tuple[StrategyEvaluator, ...] = ()

CONTEXT_STRATEGIES: tuple[StrategyEvaluator, ...] = (
    evaluate_relative_strength,
    evaluate_market_breadth,
)


def _vote(strategy: str, family: str, signal: AlgoSignal, score: int, detail: str, reason_code: str, features: dict[str, FeatureValue] | None = None) -> VotingStrategyVote:
    confidence = _confidence_from_score(score)
    data_ready = _data_ready_from_reason(reason_code, score)
    reliability = 0.5
    regime_fit = _regime_fit_from_score(score, signal)
    eligible = signal != "Hold" and data_ready and confidence >= 0.62 and reliability >= 0.35 and regime_fit >= 0.35
    return VotingStrategyVote(
        strategy=strategy,
        role="directional",
        family=_signal_family(family),
        signal=signal,
        direction=_signal_direction(signal),
        confidence=confidence,
        active=signal != "Hold",
        eligible=eligible,
        dataReady=data_ready,
        regimeFit=regime_fit,
        reliability=reliability,
        reason=detail,
        features={
            "setupScore": score,
            "legacyStatus": _status(score),
            "reasonCode": reason_code,
            **(features or {}),
        },
    )


def _context(strategy: str, family: str, signal: AlgoSignal, score: int, detail: str, reason_code: str) -> VotingStrategyVote:
    confidence = _confidence_from_score(score)
    return VotingStrategyVote(
        strategy=strategy,
        role="context",
        family=_signal_family(family),
        signal=signal,
        direction=_signal_direction(signal),
        confidence=confidence,
        active=signal != "Hold",
        eligible=False,
        dataReady=_data_ready_from_reason(reason_code, score),
        regimeFit=_regime_fit_from_score(score, signal),
        reliability=0.5,
        reason=detail,
        features={
            "setupScore": score,
            "legacyStatus": _status(score),
            "reasonCode": reason_code,
        },
    )


def _status(score: int) -> str:
    if score >= 78:
        return "Strong Fit"
    if score >= 62:
        return "Allowed"
    if score >= 45:
        return "Watch"
    return "Avoid"


def _confidence_from_score(score: int) -> float:
    return round(max(0.0, min(1.0, score / 100)), 4)


def _signal_direction(signal: AlgoSignal) -> int:
    if signal == "Buy":
        return 1
    if signal == "Sell":
        return -1
    return 0


def _signal_family(family: str) -> str:
    if family in {"trend", "breakout", "reversal", "mean_reversion", "event"}:
        return family
    return "trend"


def _regime_fit_from_score(score: int, signal: AlgoSignal) -> float:
    if signal == "Hold":
        return round(max(0.0, min(1.0, score / 100)), 4)
    return round(max(0.35, min(1.0, score / 100)), 4)


def _data_ready_from_reason(reason_code: str, score: int) -> bool:
    unavailable_terms = (
        "insufficient",
        "missing",
        "unavailable",
        "unaligned",
        "no_levels",
        "stale",
        "coverage_low",
        "malformed",
        "proxy_unavailable",
    )
    return score > 0 and not any(term in reason_code for term in unavailable_terms)


def _apply_strategy_fit(vote: VotingStrategyVote, request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    fit = _strategy_fit_record(request, vote.strategy)
    if fit is None:
        return vote
    fit_score = _fit_number(fit.get("score"))
    fit_status = str(fit.get("status") or "")
    reliability = round(max(0.0, min(1.0, fit_score / 100)), 4) if fit_score is not None else vote.reliability
    fit_blocks = fit_status.lower() == "avoid" or reliability < 0.35
    eligible = vote.eligible and not fit_blocks
    features = {
        **vote.features,
        "backendStrategyFitScore": fit_score if fit_score is not None else "",
        "backendStrategyFitStatus": fit_status,
    }
    return vote.model_copy(update={"reliability": reliability, "eligible": eligible, "features": features})


def _strategy_fit_record(request: VotingEnsembleEvaluateRequest, strategy_name: str) -> dict | None:
    context = request.market_context or {}
    strategies = context.get("strategies")
    if not isinstance(strategies, list):
        return None
    for item in strategies:
        if isinstance(item, dict) and item.get("name") == strategy_name:
            return item
    return None


def _fit_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _counts(votes: tuple[VotingStrategyVote, ...]) -> dict[str, int]:
    return {
        "Buy": sum(1 for vote in votes if vote.signal == "Buy"),
        "Sell": sum(1 for vote in votes if vote.signal == "Sell"),
        "Hold": sum(1 for vote in votes if vote.signal == "Hold"),
    }


def _winner(counts: dict[str, int]) -> AlgoSignal:
    max_votes = max(counts.values()) if counts else 0
    winners = [signal for signal, count in counts.items() if count == max_votes]
    return winners[0] if len(winners) == 1 else "Hold"  # type: ignore[return-value]


def _family_aware_decision(votes: tuple[VotingStrategyVote, ...]) -> dict:
    family_scores = _family_scores(votes)
    weights = _family_weights()
    # Family scores are already signed: Buy is positive, Sell is negative.
    # Do not subtract reversal/reversion families here or their Buy setups become bearish pressure.
    weighted_terms = {
        family: weights[family] * family_scores.get(family, 0.0)
        for family in weights
    }
    total_weight = sum(weights.values()) or 1.0
    base_score = round(sum(weighted_terms.values()) / total_weight, 4)
    buy_support = sum(1 for value in family_scores.values() if value >= 0.15)
    sell_support = sum(1 for value in family_scores.values() if value <= -0.15)
    safety_gate_failed = False
    threshold = 0.25
    if not safety_gate_failed and base_score >= threshold and buy_support >= 2:
        signal: AlgoSignal = "Buy"
    elif not safety_gate_failed and base_score <= -threshold and sell_support >= 2:
        signal = "Sell"
    else:
        signal = "Hold"
    return {
        "signal": signal,
        "family_scores": {family: round(family_scores.get(family, 0.0), 4) for family in weights},
        "base_score": base_score,
        "family_support": {"Buy": buy_support, "Sell": sell_support, "Hold": max(0, len(weights) - buy_support - sell_support)},
        "safety_gate_failed": safety_gate_failed,
    }


def _family_scores(votes: tuple[VotingStrategyVote, ...]) -> dict[str, float]:
    by_family: dict[str, list[float]] = {family: [] for family in _family_weights()}
    for vote in votes:
        if vote.family not in by_family:
            continue
        strategy_value = vote.direction * vote.confidence * vote.reliability * vote.regimeFit
        by_family[vote.family].append(strategy_value)
    return {
        family: mean(values) if values else 0.0
        for family, values in by_family.items()
    }


def _family_weights() -> dict[str, float]:
    return {
        "trend": 1.0,
        "breakout": 1.0,
        "reversal": 1.0,
        "mean_reversion": 1.0,
        "event": 1.0,
    }


def _apply_context_to_candidate(family_decision: dict, context_signals: tuple[VotingStrategyVote, ...]) -> dict:
    base_signal: AlgoSignal = family_decision["signal"]
    base_score = float(family_decision["base_score"])
    tracked_context = {
        signal.strategy: signal
        for signal in context_signals
        if signal.strategy in {"Relative Strength vs QQQ/IWM", "Market Breadth Momentum"} and signal.dataReady
    }
    if base_signal == "Hold":
        return {
            "signal": "Hold",
            "context_adjusted_score": base_score,
            "context_agreements": 0,
            "context_conflicts": 0,
            "context_adjustment_reason": "No Buy/Sell candidate exists; context signals are recorded but do not create trades.",
            "reason_code": "voting_ensemble.context.no_candidate",
        }
    candidate_direction = _signal_direction(base_signal)
    agreements = sum(1 for signal in tracked_context.values() if signal.direction == candidate_direction)
    conflicts = sum(1 for signal in tracked_context.values() if signal.direction == -candidate_direction)
    neutral = len(tracked_context) - agreements - conflicts
    if conflicts >= 2:
        return {
            "signal": "Hold",
            "context_adjusted_score": 0.0,
            "context_agreements": agreements,
            "context_conflicts": conflicts,
            "context_adjustment_reason": f"Market context conflicts with {base_signal} candidate.",
            "reason_code": "voting_ensemble.context.double_conflict_hold",
        }
    magnitude = abs(base_score)
    magnitude = max(0.0, magnitude + (0.04 * agreements) - (0.10 * conflicts))
    adjusted_score = round(candidate_direction * magnitude, 4)
    threshold = 0.25
    if conflicts == 1 and magnitude < threshold:
        return {
            "signal": "Hold",
            "context_adjusted_score": adjusted_score,
            "context_agreements": agreements,
            "context_conflicts": conflicts,
            "context_adjustment_reason": f"One market context signal conflicts with {base_signal}; adjusted score fell below {threshold:.2f}.",
            "reason_code": "voting_ensemble.context.single_conflict_hold",
        }
    if agreements:
        reason = f"{agreements} market context signal{'' if agreements == 1 else 's'} {'agrees' if agreements == 1 else 'agree'}; candidate confidence modestly increased."
        code = "voting_ensemble.context.agrees"
    elif conflicts:
        reason = f"One market context signal conflicts; candidate confidence reduced."
        code = "voting_ensemble.context.single_conflict_reduced"
    else:
        reason = f"Market context is neutral ({neutral} neutral signal{'' if neutral == 1 else 's'}); no change."
        code = "voting_ensemble.context.neutral"
    return {
        "signal": base_signal,
        "context_adjusted_score": adjusted_score,
        "context_agreements": agreements,
        "context_conflicts": conflicts,
        "context_adjustment_reason": reason,
        "reason_code": code,
    }


def _context_confirmation(candidate: AlgoSignal, context_signals: tuple[VotingStrategyVote, ...]) -> VotingContextConfirmation:
    evidence = tuple(f"{signal.strategy}: {signal.signal} - {signal.reason}" for signal in context_signals)
    if candidate == "Hold":
        return VotingContextConfirmation(
            outcome="not_applicable",
            detail="No Buy/Sell candidate exists, so context signals are recorded but do not confirm a trade.",
            evidence=evidence,
            confirmations=0,
            conflicts=0,
        )
    opposite: AlgoSignal = "Sell" if candidate == "Buy" else "Buy"
    confirmations = sum(1 for signal in context_signals if signal.signal == candidate)
    conflicts = sum(1 for signal in context_signals if signal.signal == opposite)
    if conflicts:
        return VotingContextConfirmation(
            outcome="weakens",
            detail=f"{conflicts} context signal{'' if conflicts == 1 else 's'} conflict with the {candidate} candidate.",
            evidence=evidence,
            confirmations=confirmations,
            conflicts=conflicts,
        )
    if confirmations:
        return VotingContextConfirmation(
            outcome="confirms",
            detail=f"{confirmations} context signal{'' if confirmations == 1 else 's'} confirm the {candidate} candidate.",
            evidence=evidence,
            confirmations=confirmations,
            conflicts=conflicts,
        )
    return VotingContextConfirmation(
        outcome="mixed",
        detail=f"Context signals are neutral for the {candidate} candidate.",
        evidence=evidence,
        confirmations=confirmations,
        conflicts=conflicts,
    )


def _external_breadth_feed(request: VotingEnsembleEvaluateRequest) -> dict | None:
    if isinstance(request.external_breadth_feed, dict) and request.external_breadth_feed:
        return request.external_breadth_feed
    context = request.market_context or {}
    for key in ("externalBreadthFeed", "breadthFeed", "marketBreadth"):
        value = context.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


def _market_breadth_from_feed(feed: dict, request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    source_timestamp = _feed_timestamp(feed, "sourceTimestamp", "lastUpdated", "updatedAt", "timestamp")
    anchor_timestamp = request.data_timestamp or request.candles[-1].timestamp
    if source_timestamp is not None:
        age_seconds = abs((_timestamp_utc(anchor_timestamp) - _timestamp_utc(source_timestamp)).total_seconds())
        max_age_seconds = _feed_number(feed, "maxAgeSeconds", "maxStalenessSeconds") or 300.0
        if age_seconds > max_age_seconds:
            return _context(
                "Market Breadth Momentum",
                "market_regime",
                "Hold",
                0,
                f"External breadth feed is stale: last update is {age_seconds:.0f}s from the evaluation timestamp.",
                "voting_ensemble.breadth.feed_stale",
            )
    advancing = _feed_number(feed, "advancingIssues", "advancers", "advancing")
    declining = _feed_number(feed, "decliningIssues", "decliners", "declining")
    unchanged = _feed_number(feed, "unchangedIssues", "unchanged")
    total = _feed_number(feed, "totalIssues", "componentCount", "universeSize")
    if total is None and advancing is not None and declining is not None:
        total = advancing + declining + (unchanged or 0)
    positive = _feed_percent(feed, "percentageAdvancing", "advancePercent", "percentagePositiveReturn")
    if positive is None and advancing is not None and declining is not None and advancing + declining > 0:
        positive = advancing / (advancing + declining)
    advance_decline_ratio = _feed_number(feed, "advanceDeclineRatio", "adRatio")
    if advance_decline_ratio is None and advancing is not None and declining is not None:
        advance_decline_ratio = advancing / declining if declining > 0 else 999.0 if advancing > 0 else 0.0
    up_volume = _feed_number(feed, "upVolume", "advancingVolume")
    down_volume = _feed_number(feed, "downVolume", "decliningVolume")
    up_down_volume_ratio = _feed_number(feed, "upDownVolumeRatio", "upDownVolRatio")
    if up_down_volume_ratio is None and up_volume is not None and down_volume is not None:
        up_down_volume_ratio = up_volume / down_volume if down_volume > 0 else 999.0 if up_volume > 0 else 0.0
    above_vwap = _feed_percent(feed, "percentageAboveVwap", "aboveVwapPercent", "percentAboveVwap")
    above_ema20 = _feed_percent(feed, "percentageAboveEma20", "aboveEma20Percent", "percentAboveEma20")
    new_highs = _feed_number(feed, "newIntradayHighs", "newHighs")
    new_lows = _feed_number(feed, "newIntradayLows", "newLows")
    high_low_balance = None
    if new_highs is not None and new_lows is not None and new_highs + new_lows > 0:
        high_low_balance = (new_highs - new_lows) / (new_highs + new_lows)
    median_return = _feed_number(feed, "medianComponentReturn", "medianReturn")
    coverage = _feed_percent(feed, "dataCoverage", "coverage")
    if coverage is not None and coverage < 0.65:
        return _context("Market Breadth Momentum", "market_regime", "Hold", 0, f"External breadth feed coverage is too low at {coverage:.0%}.", "voting_ensemble.breadth.feed_coverage_low")
    sector_average, sector_dispersion = _sector_breadth_metrics(feed)
    ad_momentum_5m = _feed_number(feed, "advanceDeclineRatioChange5m", "adRatioChange5m", "breadthMomentum5m")
    ad_momentum_15m = _feed_number(feed, "advanceDeclineRatioChange15m", "adRatioChange15m", "breadthMomentum15m")
    trin = _feed_number(feed, "trin", "armsIndex", "arms")
    if all(value is None for value in (positive, above_vwap, above_ema20, up_down_volume_ratio, high_low_balance, sector_average, ad_momentum_5m, ad_momentum_15m, trin)):
        return _context("Market Breadth Momentum", "market_regime", "Hold", 0, "External breadth feed is present but missing usable breadth measurements.", "voting_ensemble.breadth.feed_malformed")
    signal = _breadth_signal(positive, median_return, above_vwap, above_ema20, up_down_volume_ratio, high_low_balance, sector_average, ad_momentum_5m, ad_momentum_15m, trin)
    pieces = [
        f"advancing {positive:.0%}" if positive is not None else None,
        f"unchanged {unchanged:.0f}" if unchanged is not None else None,
        f"A/D {advance_decline_ratio:.2f}" if advance_decline_ratio is not None else None,
        f"A/D momentum 5m {ad_momentum_5m:+.2f}" if ad_momentum_5m is not None else None,
        f"A/D momentum 15m {ad_momentum_15m:+.2f}" if ad_momentum_15m is not None else None,
        f"up/down volume {up_down_volume_ratio:.2f}" if up_down_volume_ratio is not None else None,
        f"TRIN {trin:.2f}" if trin is not None else None,
        f"above VWAP {above_vwap:.0%}" if above_vwap is not None else None,
        f"above EMA20 {above_ema20:.0%}" if above_ema20 is not None else None,
        f"new highs/lows {new_highs:.0f}/{new_lows:.0f}" if new_highs is not None and new_lows is not None else None,
        f"sector breadth {sector_average:.0%}" if sector_average is not None else None,
        f"sector dispersion {sector_dispersion:.0%}" if sector_dispersion is not None else None,
        f"coverage {coverage:.0%}" if coverage is not None else None,
        f"universe {total:.0f}" if total is not None else None,
        f"updated {source_timestamp.isoformat()}" if source_timestamp is not None else None,
    ]
    detail = "Proper breadth feed: " + ", ".join(piece for piece in pieces if piece) + ". Context modifier only."
    return _context("Market Breadth Momentum", "market_regime", signal, 72 if signal != "Hold" else 52, detail, "voting_ensemble.breadth.feed")


def _market_breadth_from_proxy(request: VotingEnsembleEvaluateRequest) -> VotingStrategyVote:
    anchor_timestamp = request.data_timestamp or request.candles[-1].timestamp
    components = {symbol: candles for symbol, candles in request.breadth_components.items() if len(candles) >= 21}
    if not components:
        return _context(
            "Market Breadth Momentum",
            "market_regime",
            "Hold",
            0,
            "dataQuality unavailable: no proper breadth feed or populated ETF/basket proxy was supplied.",
            "voting_ensemble.breadth.missing",
        )
    max_age_seconds = 300
    fresh_components = {
        symbol: candles
        for symbol, candles in components.items()
        if abs((_timestamp_utc(anchor_timestamp) - _timestamp_utc(candles[-1].timestamp)).total_seconds()) <= max_age_seconds
    }
    if len(fresh_components) < max(3, int(len(components) * 0.65)):
        return _context(
            "Market Breadth Momentum",
            "market_regime",
            "Hold",
            0,
            f"dataQuality unavailable: breadth proxy stale or incomplete ({len(fresh_components)} fresh of {len(components)} components).",
            "voting_ensemble.breadth.proxy_stale",
        )
    metrics = [_breadth_proxy_component_metrics(symbol, candles) for symbol, candles in fresh_components.items()]
    metrics = [metric for metric in metrics if metric is not None]
    if len(metrics) < max(3, int(len(components) * 0.65)):
        return _context(
            "Market Breadth Momentum",
            "market_regime",
            "Hold",
            0,
            f"dataQuality unavailable: breadth proxy lacks enough valid VWAP/EMA/return measurements ({len(metrics)} valid of {len(components)} components).",
            "voting_ensemble.breadth.proxy_unavailable",
        )
    returns = [metric["return_5m"] for metric in metrics]
    bullish_percent = sum(1 for metric in metrics if metric["bullish"]) / len(metrics)
    bearish_percent = sum(1 for metric in metrics if metric["bearish"]) / len(metrics)
    positive_5m = sum(1 for value in returns if value > 0) / len(returns)
    above_vwap = sum(1 for metric in metrics if metric["above_vwap"]) / len(metrics)
    median_return = median(returns)
    dispersion = pstdev(returns) if len(returns) > 1 else 0.0
    up_volume = sum(metric["volume"] for metric in metrics if metric["return_5m"] > 0)
    down_volume = sum(metric["volume"] for metric in metrics if metric["return_5m"] < 0)
    up_volume_ratio = up_volume / (up_volume + down_volume) if up_volume + down_volume > 0 else 0.0
    breadth_direction = 1 if bullish_percent >= 0.65 else -1 if bearish_percent >= 0.65 else 0
    signal: AlgoSignal = "Buy" if breadth_direction == 1 else "Sell" if breadth_direction == -1 else "Hold"
    detail = (
        f"ETF/basket proxy breadth: breadth_direction {breadth_direction:+d}, bullish {bullish_percent:.0%}, bearish {bearish_percent:.0%}, "
        f"above VWAP {above_vwap:.0%}, positive 5m return {positive_5m:.0%}, median component return {median_return:.2%}, "
        f"up-volume ratio {up_volume_ratio:.0%}, dispersion {dispersion:.2%}, dataQuality available across {len(metrics)} of {len(components)} components. "
        "Thresholds are defaults and should be selected through walk-forward testing."
    )
    return _context("Market Breadth Momentum", "market_regime", signal, 70 if signal != "Hold" else 50, detail, "voting_ensemble.breadth.proxy")


def _breadth_proxy_component_metrics(symbol: str, candles: tuple[VotingCandle, ...]) -> dict[str, float | bool | str] | None:
    return_5m = _component_return(candles, 5)
    component_vwap = _vwap(candles)
    ema20 = _ema([candle.close for candle in candles], 20)
    if return_5m is None or component_vwap is None or ema20 is None:
        return None
    latest = candles[-1]
    above_vwap = latest.close > component_vwap
    above_ema20 = latest.close > ema20
    bullish = return_5m > 0 and above_vwap and above_ema20
    bearish = return_5m < 0 and not above_vwap and not above_ema20
    return {
        "symbol": symbol,
        "return_5m": return_5m,
        "above_vwap": above_vwap,
        "above_ema20": above_ema20,
        "bullish": bullish,
        "bearish": bearish,
        "volume": latest.volume,
    }


def _breadth_signal(
    positive: float | None,
    median_return: float | None,
    above_vwap: float | None,
    above_ema20: float | None,
    up_down_volume_ratio: float | None,
    high_low_balance: float | None = None,
    sector_average: float | None = None,
    ad_momentum_5m: float | None = None,
    ad_momentum_15m: float | None = None,
    trin: float | None = None,
) -> AlgoSignal:
    bullish = 0
    bearish = 0
    if positive is not None:
        bullish += int(positive >= 0.58)
        bearish += int(positive <= 0.42)
    if median_return is not None:
        bullish += int(median_return >= 0.0004)
        bearish += int(median_return <= -0.0004)
    if above_vwap is not None:
        bullish += int(above_vwap >= 0.58)
        bearish += int(above_vwap <= 0.42)
    if above_ema20 is not None:
        bullish += int(above_ema20 >= 0.58)
        bearish += int(above_ema20 <= 0.42)
    if up_down_volume_ratio is not None:
        bullish += int(up_down_volume_ratio >= 1.2)
        bearish += int(up_down_volume_ratio <= 0.8)
    if high_low_balance is not None:
        bullish += int(high_low_balance >= 0.15)
        bearish += int(high_low_balance <= -0.15)
    if sector_average is not None:
        bullish += int(sector_average >= 0.58)
        bearish += int(sector_average <= 0.42)
    for momentum in (ad_momentum_5m, ad_momentum_15m):
        if momentum is not None:
            bullish += int(momentum >= 0.05)
            bearish += int(momentum <= -0.05)
    if trin is not None:
        bullish += int(trin <= 0.8)
        bearish += int(trin >= 1.2)
    if bullish >= 2 and bullish > bearish:
        return "Buy"
    if bearish >= 2 and bearish > bullish:
        return "Sell"
    return "Hold"


def _feed_number(feed: dict, *keys: str) -> float | None:
    for key in keys:
        value = feed.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _feed_percent(feed: dict, *keys: str) -> float | None:
    value = _feed_number(feed, *keys)
    if value is None:
        return None
    return value / 100 if value > 1 else value


def _feed_timestamp(feed: dict, *keys: str) -> datetime | None:
    for key in keys:
        value = feed.get(key)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            normalized = value.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(normalized)
            except ValueError:
                continue
    return None


def _timestamp_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _sector_breadth_metrics(feed: dict) -> tuple[float | None, float | None]:
    raw = feed.get("sectorBreadth") or feed.get("sectorBreadthPercentages") or feed.get("sectors")
    if not isinstance(raw, dict) or not raw:
        return None, None
    values: list[float] = []
    for sector in raw.values():
        if isinstance(sector, (int, float)):
            value = float(sector)
        elif isinstance(sector, dict):
            parsed = _feed_percent(sector, "percentageAdvancing", "advancePercent", "percentageAboveVwap", "breadth")
            if parsed is None:
                continue
            value = parsed
        else:
            continue
        values.append(value / 100 if value > 1 else value)
    if not values:
        return None, None
    return mean(values), pstdev(values) if len(values) > 1 else 0.0


def _failed_breakout_levels(request: VotingEnsembleEvaluateRequest) -> tuple[_BreakoutLevel, ...]:
    candles = request.candles
    context = request.market_context or {}
    levels: list[_BreakoutLevel] = []

    opening_range = context.get("openingRange") if isinstance(context.get("openingRange"), dict) else None
    opening_high = _context_number(opening_range, "high") if opening_range else None
    opening_low = _context_number(opening_range, "low") if opening_range else None
    if (opening_high is None or opening_low is None) and len(candles) >= 17:
        opening = candles[:15]
        opening_high = max(candle.high for candle in opening)
        opening_low = min(candle.low for candle in opening)
    _append_level_pair(levels, "opening-range high", "opening-range low", opening_high, opening_low)

    prior_day = context.get("priorDayOHLC") if isinstance(context.get("priorDayOHLC"), dict) else None
    prior_high = _context_number(prior_day, "high") if prior_day else _context_number(context, "priorDayHigh")
    prior_low = _context_number(prior_day, "low") if prior_day else _context_number(context, "priorDayLow")
    _append_level_pair(levels, "prior-day high", "prior-day low", prior_high, prior_low)

    premarket = context.get("premarket") if isinstance(context.get("premarket"), dict) else None
    premarket_high = _context_number(premarket, "high") if premarket else _context_number(context, "premarketHigh")
    premarket_low = _context_number(premarket, "low") if premarket else _context_number(context, "premarketLow")
    _append_level_pair(levels, "premarket high", "premarket low", premarket_high, premarket_low)

    swing_window = candles[:-2][-24:]
    if len(swing_window) >= 8:
        swing_high = max(candle.high for candle in swing_window)
        swing_low = min(candle.low for candle in swing_window)
        _append_level_pair(levels, "recent swing high", "recent swing low", swing_high, swing_low)

    return _dedupe_levels(levels)


def _append_level_pair(levels: list[_BreakoutLevel], high_name: str, low_name: str, high: float | None, low: float | None) -> None:
    if high is not None and high > 0:
        levels.append(_BreakoutLevel(high_name, "resistance", high, low if low is not None and low > 0 else None))
    if low is not None and low > 0:
        levels.append(_BreakoutLevel(low_name, "support", low, high if high is not None and high > 0 else None))


def _context_number(source: dict | None, key: str) -> float | None:
    if not source:
        return None
    value = source.get(key)
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _dedupe_levels(levels: list[_BreakoutLevel]) -> tuple[_BreakoutLevel, ...]:
    deduped: list[_BreakoutLevel] = []
    for level in levels:
        duplicate = any(existing.side == level.side and abs(existing.value - level.value) / max(level.value, 0.01) < 0.0005 for existing in deduped)
        if not duplicate:
            deduped.append(level)
    return tuple(deduped)


def _failed_resistance_breakout(level: _BreakoutLevel, failed: VotingCandle, confirmation: VotingCandle) -> bool:
    traded_above = failed.high > level.value * 1.0005
    closed_back_inside = failed.close < level.value and (level.opposite is None or failed.close > level.opposite)
    failure_candle = failed.close < failed.open or _upper_wick_ratio(failed) >= 0.35
    next_candle_confirms = confirmation.close < failed.low or (confirmation.close < confirmation.open and confirmation.close < failed.close)
    return traded_above and closed_back_inside and failure_candle and next_candle_confirms


def _failed_support_breakout(level: _BreakoutLevel, failed: VotingCandle, confirmation: VotingCandle) -> bool:
    traded_below = failed.low < level.value * 0.9995
    closed_back_inside = failed.close > level.value and (level.opposite is None or failed.close < level.opposite)
    failure_candle = failed.close > failed.open or _lower_wick_ratio(failed) >= 0.35
    next_candle_confirms = confirmation.close > failed.high or (confirmation.close > confirmation.open and confirmation.close > failed.close)
    return traded_below and closed_back_inside and failure_candle and next_candle_confirms


def _liquidity_sweep_levels(request: VotingEnsembleEvaluateRequest) -> tuple[_BreakoutLevel, ...]:
    candles = request.candles
    context_levels = list(_failed_breakout_levels(request))
    swing_window = candles[:-1][-24:]
    if len(swing_window) >= 8:
        context_levels.append(_BreakoutLevel("prior swing high", "resistance", max(candle.high for candle in swing_window), min(candle.low for candle in swing_window)))
        context_levels.append(_BreakoutLevel("prior swing low", "support", min(candle.low for candle in swing_window), max(candle.high for candle in swing_window)))
    return _dedupe_levels(context_levels)


def _support_liquidity_sweep(level: _BreakoutLevel, latest: VotingCandle) -> bool:
    penetrated = latest.low < level.value * 0.9995
    rejected = _lower_wick_ratio(latest) >= 0.35 and latest.close > latest.open
    closed_back_through = latest.close > level.value
    still_inside_reference_range = level.opposite is None or latest.close < level.opposite
    return penetrated and rejected and closed_back_through and still_inside_reference_range


def _resistance_liquidity_sweep(level: _BreakoutLevel, latest: VotingCandle) -> bool:
    penetrated = latest.high > level.value * 1.0005
    rejected = _upper_wick_ratio(latest) >= 0.35 and latest.close < latest.open
    closed_back_through = latest.close < level.value
    still_inside_reference_range = level.opposite is None or latest.close > level.opposite
    return penetrated and rejected and closed_back_through and still_inside_reference_range


def _rejection_activity_confirmation(candles: tuple[VotingCandle, ...]) -> tuple[bool, str]:
    latest = candles[-1]
    baseline = candles[-21:-1]
    if not baseline:
        return False, "volume baseline unavailable"
    baseline_volume = mean([candle.volume for candle in baseline])
    if baseline_volume <= 0:
        return False, "volume baseline unavailable"
    ratio = latest.volume / baseline_volume
    if ratio >= 1.05:
        return True, f"rejection activity confirms with {ratio:.2f}x baseline volume"
    return False, f"rejection activity is weak at {ratio:.2f}x baseline volume"


def _timeframe_trend_state(candles: tuple[VotingCandle, ...] | list[VotingCandle], label: str) -> tuple[str, str]:
    if len(candles) < 10:
        return "neutral", f"{label} insufficient candles"
    closes = [candle.close for candle in candles]
    ema_direction, ema_detail = _ema_slope_state(closes)
    vwap_direction, vwap_detail = _price_vwap_state(candles)
    structure_direction, structure_detail = _market_structure_state(candles)
    volume_confirmed, volume_detail = _volume_confirmation_state(candles)
    trend_quality_confirmed, trend_quality_detail = _trend_quality_state(candles)
    score = _direction_score(ema_direction) + _direction_score(vwap_direction) + _direction_score(structure_direction)
    trend_confirmed = volume_confirmed and trend_quality_confirmed
    state = "up" if score >= 2 and trend_confirmed else "down" if score <= -2 and trend_confirmed else "neutral"
    return state, f"{label} {state} ({ema_detail}, {vwap_detail}, {structure_detail}, {volume_detail}, {trend_quality_detail})"


def _confirmed_component_direction(candles: tuple[VotingCandle, ...] | list[VotingCandle], direction: str) -> bool:
    if len(candles) < 10:
        return False
    closes = [candle.close for candle in candles]
    ema_direction, _ = _ema_slope_state(closes)
    _, _ = _price_vwap_state(candles)
    structure_direction, _ = _market_structure_state(candles)
    trend_quality_confirmed, _ = _trend_quality_state(candles)
    return trend_quality_confirmed and ema_direction == direction and structure_direction == direction


def _explicit_one_minute_trigger(candles: tuple[VotingCandle, ...] | list[VotingCandle], direction: str, lookback: int = 3) -> bool:
    if len(candles) <= lookback:
        return False
    latest = candles[-1]
    previous = candles[-2]
    prior = candles[-lookback - 1 : -1]
    closes = [candle.close for candle in candles]
    ema9 = _ema(closes, min(9, max(3, len(closes) // 2)))
    ema20 = _ema(closes, min(20, max(4, len(closes) - 1)))
    if ema9 is None or ema20 is None:
        return False
    if direction == "up":
        ema_reclaim = previous.close <= ema9 and latest.close > ema9 and ema9 > ema20 and latest.close > previous.high
        pullback_continuation = min(candle.low for candle in prior) <= ema9 and latest.close > ema9 and latest.close > max(candle.high for candle in prior[-3:])
        higher_low_break = prior[-1].low > prior[-2].low and latest.close > max(candle.high for candle in prior[-3:])
        return ema_reclaim or pullback_continuation or higher_low_break
    if direction == "down":
        ema_reclaim = previous.close >= ema9 and latest.close < ema9 and ema9 < ema20 and latest.close < previous.low
        pullback_continuation = max(candle.high for candle in prior) >= ema9 and latest.close < ema9 and latest.close < min(candle.low for candle in prior[-3:])
        lower_high_break = prior[-1].high < prior[-2].high and latest.close < min(candle.low for candle in prior[-3:])
        return ema_reclaim or pullback_continuation or lower_high_break
    return False


def _ema_slope_state(values: list[float]) -> tuple[str, str]:
    period = _adaptive_ema_period(len(values))
    if period is None:
        return "neutral", "EMA slope unavailable"
    lookback = min(3, max(1, len(values) - period))
    current = _ema(values, period)
    prior = _ema(values[:-lookback], period)
    if current is None or prior is None or prior <= 0:
        return "neutral", "EMA slope unavailable"
    slope = (current - prior) / prior
    if slope > 0.00025:
        return "up", f"EMA{period} rising {slope:.3%}"
    if slope < -0.00025:
        return "down", f"EMA{period} falling {slope:.3%}"
    return "neutral", f"EMA{period} flat {slope:.3%}"


def _adaptive_ema_period(length: int) -> int | None:
    if length >= 24:
        return 20
    if length >= 12:
        return 8
    if length >= 8:
        return 5
    return None


def _price_vwap_state(candles: tuple[VotingCandle, ...] | list[VotingCandle]) -> tuple[str, str]:
    current_vwap = _vwap(candles)
    if current_vwap is None or current_vwap <= 0:
        return "neutral", "VWAP unavailable"
    latest = candles[-1]
    distance = (latest.close - current_vwap) / current_vwap
    if distance > 0.0003:
        return "up", f"price above VWAP by {distance:.2%}"
    if distance < -0.0003:
        return "down", f"price below VWAP by {abs(distance):.2%}"
    return "neutral", f"price near VWAP {distance:.2%}"


def _market_structure_state(candles: tuple[VotingCandle, ...] | list[VotingCandle]) -> tuple[str, str]:
    if len(candles) < 10:
        return "neutral", "structure unavailable"
    window = min(5, len(candles) // 3)
    recent = candles[-window:]
    prior = candles[-window * 2 : -window]
    recent_high = max(candle.high for candle in recent)
    recent_low = min(candle.low for candle in recent)
    prior_high = max(candle.high for candle in prior)
    prior_low = min(candle.low for candle in prior)
    if recent_high > prior_high and recent_low >= prior_low:
        return "up", "higher high / higher low"
    if recent_low < prior_low and recent_high <= prior_high:
        return "down", "lower low / lower high"
    return "neutral", "mixed structure"


def _volume_confirmation_state(candles: tuple[VotingCandle, ...] | list[VotingCandle]) -> tuple[bool, str]:
    if len(candles) < 10:
        return False, "volume confirmation unavailable"
    current_window = min(3, len(candles) // 4)
    baseline_window = min(20, len(candles) - current_window)
    recent = candles[-current_window:]
    baseline = candles[-(baseline_window + current_window) : -current_window]
    if not baseline:
        return False, "volume baseline unavailable"
    recent_volume = mean([candle.volume for candle in recent])
    baseline_volume = mean([candle.volume for candle in baseline])
    if baseline_volume <= 0:
        return False, "volume baseline unavailable"
    ratio = recent_volume / baseline_volume
    if ratio >= 1.05:
        return True, f"volume confirms {ratio:.2f}x baseline"
    if ratio >= 0.85:
        return True, f"volume acceptable {ratio:.2f}x baseline"
    return False, f"volume weak {ratio:.2f}x baseline"


def _trend_quality_state(candles: tuple[VotingCandle, ...] | list[VotingCandle]) -> tuple[bool, str]:
    if len(candles) < 15:
        return False, "ADX/chop filter unavailable"
    adx = _adx(candles, min(14, len(candles) - 1))
    efficiency = _efficiency_ratio([candle.close for candle in candles[-min(20, len(candles)) :]])
    if adx is not None and adx < 16:
        return False, f"ADX/chop rejects rotation: ADX {adx:.1f}, efficiency {efficiency:.2f}"
    if efficiency < 0.25:
        adx_text = f"ADX {adx:.1f}" if adx is not None else "ADX unavailable"
        return False, f"ADX/chop rejects rotation: {adx_text}, efficiency {efficiency:.2f}"
    if adx is not None and adx >= 20:
        return True, f"ADX/chop confirms trend: ADX {adx:.1f}, efficiency {efficiency:.2f}"
    return True, f"ADX/chop acceptable: ADX {adx:.1f}, efficiency {efficiency:.2f}" if adx is not None else f"chop acceptable: efficiency {efficiency:.2f}"


def _adx(candles: tuple[VotingCandle, ...] | list[VotingCandle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    plus_dm = []
    minus_dm = []
    true_ranges = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    if len(true_ranges) < period:
        return None
    dx_values = []
    for end in range(period, len(true_ranges) + 1):
        tr_sum = sum(true_ranges[end - period : end])
        if tr_sum <= 0:
            continue
        plus_di = 100 * sum(plus_dm[end - period : end]) / tr_sum
        minus_di = 100 * sum(minus_dm[end - period : end]) / tr_sum
        di_total = plus_di + minus_di
        if di_total <= 0:
            continue
        dx_values.append(100 * abs(plus_di - minus_di) / di_total)
    if not dx_values:
        return None
    return mean(dx_values[-period:])


def _efficiency_ratio(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    directional_move = abs(values[-1] - values[0])
    path = sum(abs(values[index] - values[index - 1]) for index in range(1, len(values)))
    return directional_move / path if path > 0 else 0.0


def _direction_score(direction: str) -> int:
    if direction == "up":
        return 1
    if direction == "down":
        return -1
    return 0


def _aggregate(candles: tuple[VotingCandle, ...], size: int) -> list[VotingCandle]:
    groups = [candles[index : index + size] for index in range(0, len(candles), size)]
    complete = [group for group in groups if len(group) == size]
    return [
        VotingCandle(
            timestamp=group[-1].timestamp,
            open=group[0].open,
            high=max(candle.high for candle in group),
            low=min(candle.low for candle in group),
            close=group[-1].close,
            volume=sum(candle.volume for candle in group),
        )
        for group in complete
    ]


def _vwap(candles: tuple[VotingCandle, ...] | list[VotingCandle]) -> float | None:
    volume = sum(candle.volume for candle in candles)
    if volume <= 0:
        return None
    return sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in candles) / volume


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = mean(values[:period])
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def _strongest_anchor(latest_close: float, atr: float, anchors: tuple[tuple[str, float | None], ...]) -> tuple[str, float]:
    available = [(name, value) for name, value in anchors if value is not None]
    if not available:
        raise ValueError("at least one anchor is required")
    return max(available, key=lambda item: abs((latest_close - item[1]) / atr))


def _atr_anchor_distances(price: float, atr: float, vwap: float | None, ema20: float | None) -> tuple[tuple[str, float], ...]:
    distances: list[tuple[str, float]] = []
    if vwap is not None:
        distances.append(("VWAP", (price - vwap) / atr))
    if ema20 is not None:
        distances.append(("EMA20", (price - ema20) / atr))
    return tuple(distances)


def _atr_extension_decelerating(
    candles: tuple[VotingCandle, ...],
    anchor_name: str,
    current_vwap: float | None,
    current_ema: float | None,
    atr: float,
    candidate: AlgoSignal,
) -> tuple[bool, str]:
    if len(candles) < 4:
        return False, "need more candles for momentum deceleration"
    previous_anchor = _atr_reference_anchor(candles[:-1], anchor_name)
    current_anchor = current_vwap if anchor_name == "VWAP" else current_ema
    if previous_anchor is None or current_anchor is None:
        return False, "reference anchor unavailable"
    previous_distance = (candles[-2].close - previous_anchor) / atr
    current_distance = (candles[-1].close - current_anchor) / atr
    latest_move = candles[-1].close - candles[-2].close
    prior_move = candles[-2].close - candles[-3].close
    if candidate == "Buy":
        decelerating = current_distance > previous_distance or latest_move > prior_move
        if decelerating:
            return True, f"downside extension decelerating from {previous_distance:.2f} to {current_distance:.2f} ATR"
        return False, f"downside extension still expanding from {previous_distance:.2f} to {current_distance:.2f} ATR"
    decelerating = current_distance < previous_distance or latest_move < prior_move
    if decelerating:
        return True, f"upside extension decelerating from {previous_distance:.2f} to {current_distance:.2f} ATR"
    return False, f"upside extension still expanding from {previous_distance:.2f} to {current_distance:.2f} ATR"


def _atr_reference_anchor(candles: tuple[VotingCandle, ...], anchor_name: str) -> float | None:
    if anchor_name == "VWAP":
        return _vwap(candles)
    return _ema([candle.close for candle in candles], 20)


def _atr_rejection_or_reentry(
    candles: tuple[VotingCandle, ...],
    current_vwap: float | None,
    current_ema: float | None,
    candidate: AlgoSignal,
) -> tuple[bool, str]:
    latest = candles[-1]
    previous = candles[-2]
    anchors = [value for value in (current_vwap, current_ema) if value is not None]
    if not anchors:
        return False, "VWAP/EMA anchors unavailable"
    if candidate == "Buy":
        rejection = latest.close > latest.open and _lower_wick_ratio(latest) >= 0.25
        reentry = any(previous.close < anchor <= latest.close for anchor in anchors)
        return rejection or reentry, "bullish rejection or re-entry toward VWAP/EMA is present" if rejection or reentry else "no bullish rejection candle or anchor re-entry"
    rejection = latest.close < latest.open and _upper_wick_ratio(latest) >= 0.25
    reentry = any(previous.close > anchor >= latest.close for anchor in anchors)
    return rejection or reentry, "bearish rejection or re-entry toward VWAP/EMA is present" if rejection or reentry else "no bearish rejection candle or anchor re-entry"


def _atr_continuation_breakout_active(candles: tuple[VotingCandle, ...], candidate: AlgoSignal) -> tuple[bool, str]:
    if len(candles) < 24:
        return False, "no strong continuation breakout detected"
    latest = candles[-1]
    prior = candles[-22:-1]
    prior_high = max(candle.high for candle in prior)
    prior_low = min(candle.low for candle in prior)
    adx = _adx(candles, min(14, len(candles) - 1))
    recent_volume = mean([candle.volume for candle in candles[-3:]])
    baseline_volume = mean([candle.volume for candle in candles[-23:-3]])
    volume_expansion = baseline_volume > 0 and recent_volume >= baseline_volume * 1.15
    strong_trend = adx is not None and adx >= 25
    if candidate == "Buy" and latest.close < prior_low and latest.close < latest.open and strong_trend and volume_expansion:
        return True, f"downside continuation breakout below {prior_low:.2f} with ADX {adx:.1f} and {recent_volume / baseline_volume:.2f}x volume"
    if candidate == "Sell" and latest.close > prior_high and latest.close > latest.open and strong_trend and volume_expansion:
        return True, f"upside continuation breakout above {prior_high:.2f} with ADX {adx:.1f} and {recent_volume / baseline_volume:.2f}x volume"
    adx_detail = f"ADX {adx:.1f}" if adx is not None else "ADX unavailable"
    volume_detail = f"{recent_volume / baseline_volume:.2f}x volume" if baseline_volume > 0 else "volume baseline unavailable"
    return False, f"no strong continuation breakout detected ({adx_detail}, {volume_detail})"


def _atr(candles: tuple[VotingCandle, ...], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for index in range(len(candles) - period, len(candles)):
        candle = candles[index]
        previous = candles[index - 1]
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous.close), abs(candle.low - previous.close)))
    return mean(ranges)


def _bollinger(closes: list[float], period: int = 20, deviations: float = 2.0) -> tuple[float, float, float] | None:
    if len(closes) < period:
        return None
    sample = closes[-period:]
    middle = mean(sample)
    variance = mean([(value - middle) ** 2 for value in sample])
    width = variance ** 0.5 * deviations
    return middle, middle + width, middle - width


def _bollinger_width_not_expanding(closes: list[float], period: int = 20) -> tuple[bool, str]:
    widths: list[float] = []
    for end in range(period, len(closes)):
        bands = _bollinger(closes[:end], period)
        if bands is None:
            continue
        middle, upper, lower = bands
        if middle <= 0:
            continue
        widths.append((upper - lower) / middle)
    if len(widths) < 6:
        return False, "band width history unavailable"
    recent_width = widths[-1]
    prior_width = widths[-2]
    baseline = mean(widths[-6:-1])
    rapidly_expanding = recent_width > baseline * 1.2 and recent_width > prior_width * 1.08
    if rapidly_expanding:
        return False, f"band width expanded to {recent_width:.2%} versus {baseline:.2%} baseline"
    return True, f"band width stable at {recent_width:.2%} versus {baseline:.2%} baseline"


def _bollinger_trend_regime_ok(candles: tuple[VotingCandle, ...], candidate: AlgoSignal) -> tuple[bool, str]:
    adx = _adx(candles, min(14, len(candles) - 1))
    closes = [candle.close for candle in candles]
    ema = _ema(closes, min(20, len(closes)))
    trend_slope = _slope(closes[-12:])
    latest = candles[-1]
    if adx is None or ema is None:
        return True, "ADX trend regime unavailable but not blocking"
    strong_downtrend = adx >= 25 and trend_slope < -0.0015 and latest.close < ema
    strong_uptrend = adx >= 25 and trend_slope > 0.0015 and latest.close > ema
    if candidate == "Buy" and strong_downtrend:
        return False, f"ADX {adx:.1f} with falling EMA/trend suggests lower-band walk risk"
    if candidate == "Sell" and strong_uptrend:
        return False, f"ADX {adx:.1f} with rising EMA/trend suggests upper-band walk risk"
    if adx >= 25:
        return True, f"ADX {adx:.1f} is elevated but not aligned against the reversal"
    return True, f"ADX {adx:.1f} does not indicate a strong trend"


def _normalized_bollinger_deviation(value: float, middle: float, upper: float, lower: float) -> float:
    half_width = max(upper - middle, middle - lower, 0.000001)
    return (value - middle) / half_width


def _bollinger_extreme_detail(candidate: AlgoSignal, rsi: float | None, low_deviation: float, high_deviation: float) -> str:
    if candidate == "Buy":
        if rsi is not None and rsi <= 35:
            return f"RSI {rsi:.1f} confirms oversold"
        return f"normalized low deviation {low_deviation:.2f} confirms oversold"
    if rsi is not None and rsi >= 65:
        return f"RSI {rsi:.1f} confirms overbought"
    return f"normalized high deviation {high_deviation:.2f} confirms overbought"


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for index in range(len(values) - period, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _slope(values: list[float]) -> float:
    if len(values) < 2 or values[0] == 0:
        return 0.0
    return (values[-1] - values[0]) / values[0]


def _opening_impulse(candles: tuple[VotingCandle, ...]) -> tuple[AlgoSignal, int, int, float, float, float] | None:
    search_end = min(len(candles) - 2, 15)
    candidates: list[tuple[float, AlgoSignal, int, int, float, float, float]] = []
    for end in range(3, search_end + 1):
        start = max(0, end - 2)
        window = candles[start : end + 1]
        if not window or window[0].open <= 0:
            continue
        move = (window[-1].close - window[0].open) / window[0].open
        impulse_volume = mean([candle.volume for candle in window])
        baseline = candles[:start] if start >= 5 else candles[: max(end, 1)]
        baseline_volume = mean([candle.volume for candle in baseline]) if baseline else impulse_volume
        volume_expanded = baseline_volume <= 0 or impulse_volume >= baseline_volume * 1.05
        if move >= 0.003 and volume_expanded and window[-1].close > window[-1].open:
            candidates.append((abs(move), "Buy", start, end, min(candle.low for candle in window), max(candle.high for candle in window), impulse_volume))
        if move <= -0.003 and volume_expanded and window[-1].close < window[-1].open:
            candidates.append((abs(move), "Sell", start, end, max(candle.high for candle in window), min(candle.low for candle in window), impulse_volume))
    if not candidates:
        return None
    _, side, start, end, origin, extreme, impulse_volume = max(candidates, key=lambda item: item[0])
    return side, start, end, origin, extreme, impulse_volume


def _initial_trend_established(candles: tuple[VotingCandle, ...], side: AlgoSignal, impulse_start: int, impulse_end: int) -> bool:
    window = candles[impulse_start : impulse_end + 1]
    current_vwap = _vwap(candles[: impulse_end + 1])
    if current_vwap is None or not window:
        return False
    latest = window[-1]
    if side == "Buy":
        return latest.close > current_vwap and latest.close > window[0].open and max(candle.high for candle in window) > candles[max(0, impulse_start - 1)].high
    if side == "Sell":
        return latest.close < current_vwap and latest.close < window[0].open and min(candle.low for candle in window) < candles[max(0, impulse_start - 1)].low
    return False


def _first_valid_pullback(
    candles: tuple[VotingCandle, ...],
    side: AlgoSignal,
    impulse_end: int,
    origin: float,
    extreme: float,
    impulse_volume: float,
) -> _PullbackResult:
    if impulse_end + 2 >= len(candles):
        return _PullbackResult("missing", "Opening impulse exists, but no pullback candle has completed.")
    pullback_start = impulse_end + 1
    first_ready: _PullbackResult | None = None
    for index in range(pullback_start, len(candles)):
        pullback_window = candles[pullback_start : index + 1]
        candle = candles[index]
        running_vwap = _vwap(candles[: index + 1])
        if side == "Buy" and min(item.low for item in pullback_window) < origin:
            return _PullbackResult("invalidated", f"Pullback broke bullish impulse origin {origin:.2f}.")
        if side == "Sell" and max(item.high for item in pullback_window) > origin:
            return _PullbackResult("invalidated", f"Pullback broke bearish impulse origin {origin:.2f}.")
        if running_vwap is not None and side == "Buy" and candle.close < running_vwap * 0.999:
            return _PullbackResult("vwap_lost", f"Bullish pullback closed below VWAP {running_vwap:.2f}.")
        if running_vwap is not None and side == "Sell" and candle.close > running_vwap * 1.001:
            return _PullbackResult("vwap_lost", f"Bearish pullback closed above VWAP {running_vwap:.2f}.")
        if first_ready is not None:
            continue
        previous = candles[index - 1]
        is_retracing = (candle.close < previous.close or candle.low < previous.low) if side == "Buy" else (candle.close > previous.close or candle.high > previous.high)
        if not is_retracing:
            continue
        anchor = _pullback_anchor_touch(candles[: index + 1], side, candle, origin, extreme)
        if not anchor:
            continue
        pullback_volume = mean([item.volume for item in pullback_window])
        if pullback_volume >= impulse_volume:
            return _PullbackResult("high_volume", f"Pullback volume {pullback_volume:.0f} is not lower than impulse volume {impulse_volume:.0f}.")
        first_ready = _PullbackResult("ready", f"Pullback reached {anchor} with lower volume and no origin break.", index=index, anchor=anchor)
    if first_ready is not None:
        return first_ready
    return _PullbackResult("missing", "Opening impulse exists, but price has not retraced toward VWAP, EMA9, EMA20, or impulse midpoint.")


def _pullback_anchor_touch(candles: tuple[VotingCandle, ...], side: AlgoSignal, candle: VotingCandle, origin: float, extreme: float) -> str | None:
    midpoint = (origin + extreme) / 2
    closes = [item.close for item in candles]
    anchors = [
        ("VWAP", _vwap(candles)),
        ("EMA9", _ema(closes, 9)),
        ("EMA20", _ema(closes, 20)),
        ("impulse midpoint", midpoint),
    ]
    touched: list[tuple[str, float]] = []
    for name, value in anchors:
        if value is None:
            continue
        if side == "Buy" and candle.low <= value * 1.003:
            touched.append((name, abs(candle.low - value)))
        if side == "Sell" and candle.high >= value * 0.997:
            touched.append((name, abs(candle.high - value)))
    if not touched:
        return None
    return min(touched, key=lambda item: item[1])[0]


def _bullish_confirmation(latest: VotingCandle, previous: VotingCandle) -> bool:
    return latest.close > latest.open and latest.close > previous.high


def _bearish_confirmation(latest: VotingCandle, previous: VotingCandle) -> bool:
    return latest.close < latest.open and latest.close < previous.low


def _upper_wick_ratio(candle: VotingCandle) -> float:
    full_range = max(candle.high - candle.low, 0.000001)
    return (candle.high - max(candle.open, candle.close)) / full_range


def _lower_wick_ratio(candle: VotingCandle) -> float:
    full_range = max(candle.high - candle.low, 0.000001)
    return (min(candle.open, candle.close) - candle.low) / full_range


def _return(candles: tuple[VotingCandle, ...]) -> float:
    if len(candles) < 2 or candles[0].open <= 0:
        return 0.0
    return (candles[-1].close - candles[0].open) / candles[0].open


def _component_return(candles: tuple[VotingCandle, ...], horizon: int) -> float | None:
    if len(candles) <= horizon or candles[-(horizon + 1)].close <= 0:
        return None
    return candles[-1].close / candles[-(horizon + 1)].close - 1


def _aligned_relative_strength_closes(
    spy_candles: tuple[VotingCandle, ...],
    qqq_candles: tuple[VotingCandle, ...],
    iwm_candles: tuple[VotingCandle, ...],
) -> tuple[tuple[datetime, float, float, float], ...]:
    qqq_by_timestamp = {_timestamp_key(candle.timestamp): candle.close for candle in qqq_candles}
    iwm_by_timestamp = {_timestamp_key(candle.timestamp): candle.close for candle in iwm_candles}
    aligned = [
        (candle.timestamp, candle.close, qqq_by_timestamp[key], iwm_by_timestamp[key])
        for candle in spy_candles
        if (key := _timestamp_key(candle.timestamp)) in qqq_by_timestamp and key in iwm_by_timestamp
    ]
    return tuple(sorted(aligned, key=lambda item: item[0]))


def _timestamp_key(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(second=0, microsecond=0)
    return timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _relative_strength_for_horizon(aligned: tuple[tuple[datetime, float, float, float], ...], horizon: int) -> float | None:
    if len(aligned) <= horizon:
        return None
    _, spy_now, qqq_now, iwm_now = aligned[-1]
    _, spy_prior, qqq_prior, iwm_prior = aligned[-(horizon + 1)]
    if min(spy_prior, qqq_prior, iwm_prior) <= 0:
        return None
    spy_return = spy_now / spy_prior - 1
    qqq_return = qqq_now / qqq_prior - 1
    iwm_return = iwm_now / iwm_prior - 1
    return spy_return - (0.5 * qqq_return) - (0.5 * iwm_return)


def _relative_strength_zscore(aligned: tuple[tuple[datetime, float, float, float], ...], horizon: int, window: int) -> float | None:
    values = [
        value
        for index in range(horizon, len(aligned))
        if (value := _relative_strength_for_horizon(aligned[: index + 1], horizon)) is not None
    ]
    if len(values) < max(10, horizon * 2):
        return None
    sample = values[-min(window, len(values)) :]
    if len(sample) <= 1:
        return None
    baseline_sample = sample[:-1]
    baseline = mean(baseline_sample)
    variance = mean([(value - baseline) ** 2 for value in baseline_sample])
    standard_deviation = variance ** 0.5
    if standard_deviation <= 0:
        return None
    return (sample[-1] - baseline) / standard_deviation
