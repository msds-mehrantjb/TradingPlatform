from __future__ import annotations

from backend.app.domain.models import Signal, TradeCandidate
from backend.app.trading_policy.models import DynamicTradingPolicyConfig, EntryPlan


TREND_FAMILIES = {"TREND", "MEAN_REVERSION"}


def build_entry_plan(
    candidate: TradeCandidate,
    *,
    entry_offset_bps: float,
    config: DynamicTradingPolicyConfig | None = None,
) -> EntryPlan | None:
    if candidate.signal == Signal.HOLD.value:
        return None
    resolved_config = config or DynamicTradingPolicyConfig()
    family = candidate_family(candidate)
    subtype = candidate_setup_subtype(candidate)
    if family in TREND_FAMILIES:
        return _limit_pullback_entry(candidate, family=family, subtype=subtype, entry_offset_bps=entry_offset_bps, config=resolved_config)
    if family == "BREAKOUT":
        return _breakout_entry(candidate, family=family, subtype=subtype, config=resolved_config)
    if family == "REVERSAL":
        return _reversal_entry(candidate, family=family, subtype=subtype, config=resolved_config)
    if family == "GAP_SESSION":
        return _gap_session_entry(candidate, family=family, subtype=subtype, config=resolved_config)
    return _limit_pullback_entry(candidate, family=family, subtype=subtype, entry_offset_bps=entry_offset_bps, config=resolved_config)


def candidate_family(candidate: TradeCandidate) -> str:
    raw = candidate.features.get("strategyFamily", candidate.features.get("family", "TREND"))
    return _normalize_token(raw)


def candidate_setup_subtype(candidate: TradeCandidate) -> str:
    raw = candidate.features.get("setupSubtype", candidate.features.get("gapSubtype", candidate.features.get("entrySubtype", "default")))
    return _normalize_token(raw).lower()


def _limit_pullback_entry(
    candidate: TradeCandidate,
    *,
    family: str,
    subtype: str,
    entry_offset_bps: float,
    config: DynamicTradingPolicyConfig,
) -> EntryPlan | None:
    if not _supports(config, "LIMIT"):
        return None
    limit_price = _offset_price(candidate, entry_offset_bps)
    return EntryPlan(
        side=candidate.signal,
        strategyFamily=family,
        setupSubtype=subtype,
        entryPrice=candidate.entryPrice,
        entryOffsetBps=entry_offset_bps,
        limitPrice=round(limit_price, 4),
        triggerPrice=None,
        orderType="LIMIT",
        maxChaseDistance=round(_distance_from_bps(candidate.entryPrice, config.maxChaseDistanceBps), 6),
        expirationBars=config.pullbackExpirationBars,
        cancelConditions=["structure_invalidates", "maximum_chase_distance_exceeded", "entry_order_expired"],
        brokerCapabilityAssumptions=["broker_supports_limit_orders"],
        intent="pullback_or_mean_reversion_limit_entry",
        reasonCodes=["entry.family_limit_policy"],
        explanation="Trend/pullback and mean-reversion entries use a limit order, short expiration, structural invalidation, and maximum chase distance.",
    )


def _breakout_entry(
    candidate: TradeCandidate,
    *,
    family: str,
    subtype: str,
    config: DynamicTradingPolicyConfig,
) -> EntryPlan | None:
    trigger_price = _offset_price(candidate, config.breakoutTriggerBufferBps)
    limit_price = _offset_price(candidate, config.breakoutLimitOffsetBps)
    if _supports(config, "STOP_LIMIT"):
        return EntryPlan(
            side=candidate.signal,
            strategyFamily=family,
            setupSubtype=subtype,
            entryPrice=round(trigger_price, 4),
            entryOffsetBps=config.breakoutTriggerBufferBps,
            limitPrice=round(limit_price, 4),
            triggerPrice=round(trigger_price, 4),
            orderType="STOP_LIMIT",
            maxChaseDistance=round(_distance_from_bps(candidate.entryPrice, config.maxChaseDistanceBps), 6),
            expirationBars=config.breakoutExpirationBars,
            cancelConditions=["breakout_rejected", "maximum_chase_distance_exceeded", "entry_order_expired"],
            brokerCapabilityAssumptions=["broker_supports_stop_limit_orders"],
            intent="breakout_stop_limit_with_spread_aware_buffer",
            reasonCodes=["entry.breakout_stop_limit_policy"],
            explanation="Breakout entry uses a stop-limit trigger with spread-aware buffer, bounded limit offset, expiration, and no chasing beyond the configured distance.",
        )
    if _supports(config, "LIMIT"):
        return EntryPlan(
            side=candidate.signal,
            strategyFamily=family,
            setupSubtype=subtype,
            entryPrice=candidate.entryPrice,
            entryOffsetBps=0.0,
            limitPrice=round(candidate.entryPrice, 4),
            triggerPrice=None,
            orderType="LIMIT",
            maxChaseDistance=round(_distance_from_bps(candidate.entryPrice, config.maxChaseDistanceBps), 6),
            expirationBars=config.breakoutExpirationBars,
            cancelConditions=["retest_not_confirmed", "maximum_chase_distance_exceeded", "entry_order_expired"],
            brokerCapabilityAssumptions=["broker_supports_limit_orders", "stop_limit_unavailable_confirmed_retest_fallback"],
            intent="breakout_confirmed_limit_retest_entry",
            reasonCodes=["entry.breakout_confirmed_limit_fallback"],
            explanation="Stop-limit is unavailable, so breakout entry falls back to a confirmed limit/retest intent that matches backtest behavior.",
        )
    return None


def _reversal_entry(
    candidate: TradeCandidate,
    *,
    family: str,
    subtype: str,
    config: DynamicTradingPolicyConfig,
) -> EntryPlan | None:
    if not _supports(config, "LIMIT"):
        return None
    confirmed = bool(candidate.features.get("reclaimConfirmed") or candidate.features.get("rejectionConfirmed"))
    reclaimed_level = _feature_float(candidate, "reclaimedLevel", "rejectionLevel", default=candidate.entryPrice)
    if not confirmed:
        return None
    limit_price = _offset_price(candidate, 0.0, base_price=reclaimed_level)
    return EntryPlan(
        side=candidate.signal,
        strategyFamily=family,
        setupSubtype=subtype,
        entryPrice=round(reclaimed_level, 4),
        entryOffsetBps=0.0,
        limitPrice=round(limit_price, 4),
        triggerPrice=None,
        orderType="LIMIT",
        maxChaseDistance=round(_distance_from_bps(candidate.entryPrice, config.maxChaseDistanceBps), 6),
        expirationBars=config.reversalExpirationBars,
        cancelConditions=["reclaim_or_rejection_fails", "sweep_or_failed_breakout_extreme_breached", "entry_order_expired"],
        brokerCapabilityAssumptions=["broker_supports_limit_orders"],
        intent="reversal_reclaim_rejection_limit_entry",
        reasonCodes=["entry.reversal_confirmation_policy"],
        explanation="Reversal entry requires reclaim/rejection confirmation, anchors entry near the reclaimed level, and invalidates beyond the sweep or failed-breakout extreme.",
    )


def _gap_session_entry(
    candidate: TradeCandidate,
    *,
    family: str,
    subtype: str,
    config: DynamicTradingPolicyConfig,
) -> EntryPlan | None:
    if "continuation" in subtype and _supports(config, "STOP_LIMIT"):
        return _breakout_entry(candidate, family=family, subtype=subtype, config=config)
    if _supports(config, "LIMIT"):
        plan = _limit_pullback_entry(candidate, family=family, subtype=subtype, entry_offset_bps=0.0, config=config)
        if plan is None:
            return None
        return plan.model_copy(
            update={
                "expirationBars": config.gapSessionExpirationBars,
                "intent": "gap_session_continuation_or_fade_policy",
                "reasonCodes": ["entry.gap_session_policy"],
                "explanation": "Gap/session entry derives order intent from continuation/fade subtype while retaining bounded chase and expiration assumptions.",
            }
        )
    return None


def _supports(config: DynamicTradingPolicyConfig, order_type: str) -> bool:
    return order_type in {str(item).upper() for item in config.supportedOrderTypes}


def _offset_price(candidate: TradeCandidate, bps: float, *, base_price: float | None = None) -> float:
    price = candidate.entryPrice if base_price is None else base_price
    offset = price * (bps / 10_000.0)
    if candidate.signal == Signal.BUY.value:
        return price + offset
    return max(0.01, price - offset)


def _distance_from_bps(price: float, bps: float) -> float:
    return max(0.0, price * (bps / 10_000.0))


def _normalize_token(value: object) -> str:
    text = str(value or "").replace("-", "_").replace("/", "_").replace(" ", "_").upper()
    if text in {"MEAN_REVERSION", "MEANREVERSION"}:
        return "MEAN_REVERSION"
    if text in {"GAP", "GAP_SESSION", "GAPSESSION", "EVENT"}:
        return "GAP_SESSION"
    return text


def _feature_float(candidate: TradeCandidate, *names: str, default: float) -> float:
    for name in names:
        value = candidate.features.get(name)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default
