"""Regime-local risk evaluation and entry cost estimates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, time
from typing import Any

from backend.app.algorithms.regime.exchange_calendar import exchange_session, parse_exchange_timestamp


REGIME_LOCAL_RISK_VERSION = "regime_local_risk_v1"


@dataclass(frozen=True)
class RegimeLocalRiskResult:
    algorithmId: str
    localRiskResultId: str
    decisionId: str
    orderIntentId: str
    settingsVersion: str
    passed: bool
    requestedQuantity: int
    approvedQuantity: int
    estimatedGrossEdge: float
    estimatedTransactionCost: float
    estimatedNetEdge: float
    blockers: tuple[str, ...]
    reductions: tuple[dict[str, Any], ...]
    evaluatedAt: str
    expiresAt: str
    reasonCodes: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        payload["reductions"] = [dict(item) for item in self.reductions]
        payload["reasonCodes"] = list(self.reasonCodes)
        return payload


def evaluate_regime_local_risk(
    *,
    decision_id: str,
    order_intent_id: str,
    settings_version: str,
    requested_quantity: int,
    entry_price: float,
    aggregation: dict[str, object],
    classification,
    state,
    settings: dict[str, Any],
    runtime_context: dict[str, Any] | None = None,
    evaluated_at: datetime | None = None,
) -> RegimeLocalRiskResult:
    evaluated = _as_utc(evaluated_at or datetime.now(UTC))
    context = _merged_context(settings, runtime_context)
    blockers: list[str] = []
    reductions: list[dict[str, Any]] = []
    liquidity = _record(getattr(classification, "evidence", {}).get("liquidityEvidence"))
    quote = _record(context.get("quoteFreshness") or context.get("quote") or liquidity)
    daily = _record(context.get("dailyCounters") or context.get("daily_counters"))
    account = _record(context.get("accountSnapshot") or context.get("account"))
    inventory = _record(context.get("inventorySnapshot") or context.get("inventory"))
    open_position = _record(context.get("openPosition") or inventory.get("openPosition") or account.get("position"))
    strategy_id = str(context.get("strategyId") or context.get("strategy_id") or _selected_strategy_id(aggregation))
    family_id = str(context.get("familyId") or context.get("family_id") or _selected_family_id(aggregation))
    quantity = max(0, int(requested_quantity or 0))

    if not context.get("completedPrimaryCandle", context.get("completedBar", True)):
        blockers.append("regime.local_risk.completed_bar_required")
    if settings.get("noNewEntries"):
        blockers.append("regime.local_gate.profile_no_new_entries")
    raw_regime = str(getattr(classification, "raw_regime", "") or "")
    if raw_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        blockers.append(f"regime.local_gate.no_entry_regime:{raw_regime}")
    bar_age = _number(context.get("staleCandleSeconds") or context.get("barAgeSeconds") or getattr(classification, "features", {}).get("barAgeSeconds"))
    if bar_age is not None and bar_age > float(settings.get("staleBarToleranceSeconds", 90)):
        blockers.append("regime.local_risk.completed_bar_stale")
    require_quote = bool(context.get("requireQuote", True))
    if (quote or require_quote) and (quote.get("status") == "stale" or (_number(quote.get("ageMs") or getattr(classification, "features", {}).get("quoteAgeMs")) or 0) > float(settings.get("quoteAgeToleranceSeconds", 5)) * 1000):
        blockers.append("regime.local_risk.quote_stale")
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    if require_quote and (bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid):
        blockers.append("regime.local_risk.bid_ask_required")
    spread_percent = _spread_percent(liquidity, quote)
    if spread_percent is not None and spread_percent > float(settings.get("maxSpreadPercent", 0.03)):
        blockers.append("regime.local_risk.spread_too_wide")
    if getattr(getattr(classification, "axes", None), "liquidity", None) in {"poor", "unknown"} or liquidity.get("blockNewEntries"):
        blockers.append("regime.local_risk.liquidity_blocked")
    session = exchange_session(getattr(classification, "timestamp", ""))
    if session.status not in {"opening", "midday", "afternoon", "closing"}:
        blockers.append("regime.local_risk.session_permission")
    if getattr(getattr(classification, "axes", None), "event_risk", None) == "blackout":
        blockers.append("regime.local_risk.event_blackout")
    if _past_entry_cutoff(getattr(classification, "timestamp", ""), settings):
        blockers.append("regime.local_risk.entry_cutoff")
    if context.get("runtimePaused") or context.get("paused"):
        blockers.append("regime.local_risk.runtime_paused")
    if context.get("recoverySucceeded") is False or context.get("entryCreationPausedForReconciliation"):
        blockers.append("regime.local_risk.recovery_incomplete")
    if context.get("inventoryReconciled") is False or context.get("reconciliationRequired"):
        blockers.append("regime.local_risk.reconciliation_incomplete")
    if open_position and not bool(settings.get("pyramidingEnabled", False)):
        blockers.append("regime.local_risk.existing_position")
        blockers.append("regime.local_risk.pyramiding_disabled")
    if context.get("duplicateProposal") or context.get("duplicateOrderIntent"):
        blockers.append("regime.local_risk.duplicate_proposal")
    if int(_number(_record(context.get("cooldownState")).get("remainingBars")) or 0) > 0:
        blockers.append("regime.local_risk.cooldown")
    if _family_cooldown_active(context, aggregation):
        blockers.append("regime.local_risk.family_cooldown")
    if _daily_count(daily, "strategyTradeCounts", strategy_id) >= _strategy_limit(settings, strategy_id):
        blockers.append("regime.local_risk.per_strategy_daily_limit")
    if _daily_count(daily, "familyTradeCounts", family_id) >= _family_limit(settings, family_id):
        blockers.append("regime.local_risk.per_family_daily_limit")
    if int(_number(daily.get("tradeCount") or daily.get("totalTrades")) or 0) >= int(settings.get("maxTradesPerDay", 0)):
        blockers.append("regime.local_risk.total_daily_trade_limit")
    if int(_number(daily.get("consecutiveLosses")) or 0) >= int(settings.get("maxConsecutiveLosses", 0)):
        blockers.append("regime.local_risk.consecutive_loss_breaker")
    if _number(daily.get("dailyLossPercent")) is not None and _number(daily.get("dailyLossPercent")) >= float(settings.get("maxDailyLossPercent", 0.0)):
        blockers.append("regime.local_risk.daily_loss_limit")
    if int(_number(aggregation.get("activeStrategyCount")) or 0) < int(settings.get("minimumActiveStrategies", 0)):
        blockers.append("regime.local_gate.minimum_active_strategies")
    if int(_number(aggregation.get("activeFamilyCount")) or 0) < int(settings.get("minimumIndependentFamilies", 0)):
        blockers.append("regime.local_gate.minimum_independent_families")
    if float(_number(aggregation.get("winningScore")) or 0.0) < float(settings.get("minimumWinningScore", 0.0)):
        blockers.append("regime.local_gate.minimum_winning_score")
    if float(_number(aggregation.get("winningEdge")) or 0.0) < float(settings.get("minimumSignalEdge", 0.0)):
        blockers.append("regime.local_gate.minimum_signal_edge")
    if float(_number(aggregation.get("winningEdge")) or 0.0) < float(settings.get("minimumNetExpectedEdge", settings.get("minimumSignalEdge", 0.0))):
        blockers.append("regime.local_gate.minimum_net_expected_edge")
    if float(_number(aggregation.get("abstentionRate")) or 0.0) > float(settings.get("maximumAbstentionRate", 1.0)):
        blockers.append("regime.local_gate.maximum_abstention_rate")

    quantity = _reduce_quantity(quantity, int(settings.get("maxAllowedShares", 0)), "regime.local_risk.reduce.maximum_shares", reductions)
    quantity = _reduce_notional(quantity, entry_price, float(settings.get("maxOrderNotionalDollars", settings.get("maxNotionalDollars", 0.0))), "regime.local_risk.reduce.maximum_order_notional", reductions)
    current_position_notional = _number(open_position.get("notional") or open_position.get("marketValue")) or 0.0
    max_position_notional = float(settings.get("maxPositionNotionalDollars", settings.get("maxNotionalDollars", 0.0)))
    if max_position_notional > 0:
        remaining_position_quantity = int(max(0.0, (max_position_notional - current_position_notional) / max(entry_price, 0.01)))
        quantity = _reduce_quantity(quantity, remaining_position_quantity, "regime.local_risk.reduce.maximum_position_notional", reductions)
    expected_fill_quantity = int(_number(quote.get("expectedFillQuantity") or liquidity.get("expectedFillQuantity") or liquidity.get("expectedVolume")) or 0)
    if expected_fill_quantity > 0:
        participation = float(settings.get("maxParticipationPercent", 0.0))
        max_participation_quantity = 0 if participation <= 0 else max(1, int(expected_fill_quantity * participation))
        quantity = _reduce_quantity(quantity, max_participation_quantity, "regime.local_risk.reduce.maximum_participation", reductions)
    buying_power = _number(account.get("availableBuyingPower") or account.get("buyingPower"))
    if buying_power is not None:
        quantity = _reduce_quantity(quantity, int(max(0.0, buying_power / max(entry_price, 0.01))), "regime.local_risk.reduce.buying_power", reductions)
    elif context.get("requireBuyingPower", True):
        blockers.append("regime.local_risk.buying_power_unavailable")

    decision_age = _number(context.get("decisionAgeSeconds"))
    if decision_age is not None and decision_age > float(settings.get("staleBarToleranceSeconds", 90)):
        blockers.append("regime.local_risk.decision_age")
    order_ttl = int(settings.get("orderTimeToLiveSeconds", 60))
    if decision_age is not None and decision_age > order_ttl:
        blockers.append("regime.local_risk.order_ttl")
    if quantity <= 0 and requested_quantity > 0:
        blockers.append("regime.local_risk.quantity_reduced_to_zero")
    if requested_quantity <= 0:
        blockers.append("regime.local_risk.requested_quantity_required")

    cost = estimate_round_trip_transaction_cost_bps(classification, settings, context)
    max_cost_bps = _number(settings.get("maximumTransactionCostBps") or settings.get("maximumAcceptableTransactionCostBps"))
    if max_cost_bps is not None and cost["totalCostBps"] > max_cost_bps:
        blockers.append("regime.local_risk.transaction_cost_too_high")
    gross_edge = _gross_edge_bps(context, aggregation, classification)
    safety_margin = max(0.0, _number(context.get("safetyMarginBps") or settings.get("safetyMarginBps")) or 0.0)
    net_edge = gross_edge - cost["totalCostBps"] - safety_margin
    minimum_edge = _minimum_net_edge_bps(settings)
    if net_edge < minimum_edge:
        blockers.append("regime.local_risk.minimum_expected_net_edge")

    blockers.extend(str(reason) for reason in getattr(classification, "no_trade_reasons", ()) or ())
    unique_blockers = tuple(dict.fromkeys(blockers))
    unique_reductions = tuple(reductions)
    passed = not unique_blockers and quantity > 0
    payload_for_id = {
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "settingsVersion": settings_version,
        "requestedQuantity": int(requested_quantity or 0),
        "approvedQuantity": int(quantity if passed else 0),
        "blockers": unique_blockers,
        "reductions": unique_reductions,
    }
    result_id = f"regime-local-risk-{hashlib.sha256(_json(payload_for_id).encode('utf-8')).hexdigest()[:24]}"
    return RegimeLocalRiskResult(
        algorithmId="regime",
        localRiskResultId=result_id,
        decisionId=decision_id,
        orderIntentId=order_intent_id,
        settingsVersion=settings_version,
        passed=passed,
        requestedQuantity=int(requested_quantity or 0),
        approvedQuantity=int(quantity if passed else 0),
        estimatedGrossEdge=round(gross_edge, 6),
        estimatedTransactionCost=round(cost["totalCostBps"] + safety_margin, 6),
        estimatedNetEdge=round(net_edge, 6),
        blockers=unique_blockers,
        reductions=unique_reductions,
        evaluatedAt=evaluated.isoformat().replace("+00:00", "Z"),
        expiresAt=(evaluated + timedelta(seconds=order_ttl)).isoformat().replace("+00:00", "Z"),
        reasonCodes=tuple(unique_blockers or ("regime.local_risk.passed",)),
        details={
            "localRiskVersion": REGIME_LOCAL_RISK_VERSION,
            "costComponentsBps": cost,
            "minimumNetExpectedEdgeBps": minimum_edge,
            "safetyMarginBps": safety_margin,
            "strategyId": strategy_id,
            "familyId": family_id,
        },
    )


def evaluate_regime_local_gates(
    aggregation: dict[str, object],
    classification,
    state,
    settings: dict,
    runtime_context: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    context = runtime_context or {}
    result = evaluate_regime_local_risk(
        decision_id=str(context.get("decisionId") or "regime-local-gate-preview"),
        order_intent_id=str(context.get("orderIntentId") or "regime-local-gate-preview"),
        settings_version=str(settings.get("settingsVersion") or "regime-settings-preview"),
        requested_quantity=max(1, int(_number(context.get("proposedShares") or context.get("requestedQuantity")) or 1)),
        entry_price=max(0.01, _number(context.get("entryPrice") or context.get("limitPrice")) or _classification_price(classification)),
        aggregation=aggregation,
        classification=classification,
        state=state,
        settings=settings,
        runtime_context={**context, "requireBuyingPower": False, "requireQuote": bool(context.get("quoteFreshness") or context.get("quote"))},
    )
    return result.blockers


def estimate_entry_transaction_cost_bps(classification, settings: dict, runtime_context: dict[str, Any] | None = None) -> dict[str, float]:
    return estimate_round_trip_transaction_cost_bps(classification, settings, runtime_context)


def estimate_round_trip_transaction_cost_bps(classification, settings: dict, runtime_context: dict[str, Any] | None = None) -> dict[str, float]:
    context = runtime_context or {}
    liquidity = _record(getattr(classification, "evidence", {}).get("liquidityEvidence"))
    spread_bps = _number(context.get("spreadBps") or liquidity.get("spreadBps") or getattr(classification, "features", {}).get("spreadBps")) or 0.0
    entry_half_spread_bps = max(0.0, _number(context.get("entryHalfSpreadBps")) or spread_bps / 2)
    exit_half_spread_bps = max(0.0, _number(context.get("exitHalfSpreadBps")) or spread_bps / 2)
    entry_slippage_bps = max(0.0, _number(context.get("expectedEntrySlippageBps") or context.get("expectedSlippageBps") or settings.get("maximumSlippageBps")) or 0.0)
    exit_slippage_bps = max(0.0, _number(context.get("expectedExitSlippageBps") or context.get("expectedSlippageBps") or settings.get("maximumSlippageBps")) or 0.0)
    fees_bps = max(0.0, _number(context.get("feesBps") or settings.get("estimatedFeesBps")) or 0.0)
    adverse_selection_bps = max(0.0, _number(context.get("adverseSelectionBufferBps") or settings.get("adverseSelectionBufferBps")) or 0.0)
    return {
        "entryHalfSpreadBps": entry_half_spread_bps,
        "exitHalfSpreadBps": exit_half_spread_bps,
        "expectedEntrySlippageBps": entry_slippage_bps,
        "expectedExitSlippageBps": exit_slippage_bps,
        "feesBps": fees_bps,
        "adverseSelectionBufferBps": adverse_selection_bps,
        "totalCostBps": entry_half_spread_bps + exit_half_spread_bps + entry_slippage_bps + exit_slippage_bps + fees_bps + adverse_selection_bps,
    }


def _merged_context(settings: dict, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    merged = {}
    for key in (
        "runtimeContext",
        "accountSnapshot",
        "inventorySnapshot",
        "dailyCounters",
        "cooldownState",
        "familyCooldowns",
        "quoteFreshness",
        "haltLuldCircuitBreaker",
    ):
        if key in settings:
            merged[key] = settings[key]
    if runtime_context:
        merged.update(runtime_context)
    return merged


def _reduce_quantity(quantity: int, cap: int, reason_code: str, reductions: list[dict[str, Any]]) -> int:
    if cap < 0:
        return quantity
    if quantity > cap:
        reductions.append({"reasonCode": reason_code, "requestedQuantity": quantity, "approvedQuantity": cap})
        return cap
    return quantity


def _reduce_notional(quantity: int, price: float, max_notional: float, reason_code: str, reductions: list[dict[str, Any]]) -> int:
    if max_notional <= 0 or price <= 0:
        return quantity
    cap = int(max_notional / price)
    return _reduce_quantity(quantity, cap, reason_code, reductions)


def _family_cooldown_active(context: dict[str, Any], aggregation: dict[str, object]) -> bool:
    cooldowns = _record(context.get("familyCooldowns"))
    if not cooldowns:
        return False
    selected = _record(aggregation.get("selectedStrategyByFamily"))
    families = set(selected) or set(_record(aggregation.get("familyScores")))
    return any(int(_number(_record(cooldowns.get(family)).get("remainingBars")) or 0) > 0 for family in families)


def _past_entry_cutoff(timestamp: str, settings: dict) -> bool:
    parsed = parse_exchange_timestamp(timestamp)
    if parsed is None:
        return True
    cutoff = _time(settings.get("entryCutoffTimeEt") or "15:30")
    return parsed.time() > cutoff


def _time(value: Any) -> time:
    hour, minute = str(value).split(":", 1)
    return time(int(hour), int(minute[:2]))


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_percent(liquidity: dict[str, Any], quote: dict[str, Any]) -> float | None:
    value = _number(quote.get("spreadPercent") or liquidity.get("spreadPercent"))
    if value is not None:
        return value
    bps = _number(quote.get("spreadBps") or liquidity.get("spreadBps"))
    return None if bps is None else bps / 10_000


def _gross_edge_bps(context: dict[str, Any], aggregation: dict[str, object], classification) -> float:
    explicit = _number(context.get("estimatedGrossEdgeBps") or context.get("expectedGrossEdgeBps") or getattr(classification, "features", {}).get("expectedGrossEdgeBps"))
    if explicit is not None:
        return explicit
    edge = _number(aggregation.get("winningEdge"))
    return max(0.0, edge or 0.0) * 100.0


def _minimum_net_edge_bps(settings: dict[str, Any]) -> float:
    explicit = _number(settings.get("minimumNetExpectedEdgeBps"))
    if explicit is not None:
        return explicit
    value = _number(settings.get("minimumNetExpectedEdge")) or 0.0
    return value * 100.0


def _daily_count(daily: dict[str, Any], key: str, item_id: str) -> int:
    counts = _record(daily.get(key))
    return int(_number(counts.get(item_id)) or 0)


def _strategy_limit(settings: dict[str, Any], strategy_id: str) -> int:
    limits = _record(settings.get("perStrategyTradeLimits"))
    return int(_number(limits.get(strategy_id) or settings.get("perStrategyMaxTradesPerDay")) or 1)


def _family_limit(settings: dict[str, Any], family_id: str) -> int:
    limits = _record(settings.get("perFamilyTradeLimits") or settings.get("familyTradeLimits"))
    return int(_number(limits.get(family_id)) or _number(settings.get("perFamilyMaxTradesPerDay")) or 999_999)


def _selected_family_id(aggregation: dict[str, object]) -> str:
    selected = _record(aggregation.get("selectedStrategyByFamily"))
    if selected:
        return str(next(iter(selected)))
    scores = _record(aggregation.get("familyScores"))
    if scores:
        return str(max(scores, key=lambda item: float(scores.get(item) or 0.0)))
    return "unknown"


def _selected_strategy_id(aggregation: dict[str, object]) -> str:
    selected = _record(aggregation.get("selectedStrategyByFamily"))
    if selected:
        value = next(iter(selected.values()))
        if isinstance(value, dict):
            return str(value.get("strategyId") or value.get("strategy_id") or "unknown")
        return str(value)
    return "unknown"


def _classification_price(classification) -> float:
    return max(0.01, _number(getattr(classification, "features", {}).get("close") or getattr(classification, "features", {}).get("lastPrice")) or 100.0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


__all__ = [
    "REGIME_LOCAL_RISK_VERSION",
    "RegimeLocalRiskResult",
    "estimate_entry_transaction_cost_bps",
    "estimate_round_trip_transaction_cost_bps",
    "evaluate_regime_local_gates",
    "evaluate_regime_local_risk",
]
