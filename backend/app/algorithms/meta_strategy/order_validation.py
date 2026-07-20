"""Final Meta-Strategy order validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent, MetaStrategyMarketSnapshot


@dataclass(frozen=True)
class MetaStrategyOrderValidationFailure:
    field: str
    reason_code: str
    observed: Any = None
    threshold: Any = None


@dataclass(frozen=True)
class MetaStrategyOrderValidationContext:
    order_intent: MetaOrderIntent | None
    snapshot: MetaStrategyMarketSnapshot
    model_action: str
    deterministic_direction: str
    final_direction: str
    sizing_quantity: int
    global_approved_quantity: int
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    reward_risk: float | None
    available_buying_power: float
    reserved_risk_dollars: float
    maximum_reserved_risk_dollars: float
    session_allowed: bool
    max_quote_age_seconds: int
    max_spread_bps: float
    minimum_liquidity: float
    duplicate_intent_ids: tuple[str, ...] = ()
    existing_position_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetaStrategyOrderValidationResult:
    valid: bool
    failures: tuple[MetaStrategyOrderValidationFailure, ...]
    reason_codes: tuple[str, ...]
    persisted_payload: dict[str, Any]


def validate_meta_strategy_order(context: MetaStrategyOrderValidationContext) -> MetaStrategyOrderValidationResult:
    failures: list[MetaStrategyOrderValidationFailure] = []
    intent = context.order_intent
    if intent is None:
        return _result(
            failures=(),
            extra_reason_codes=("meta_strategy.order_validation.no_order_to_validate",),
            context=context,
        )

    _check(intent.algorithm_id == "meta_strategy", failures, "algorithm_attribution", "meta_strategy.order_validation.invalid_algorithm", intent.algorithm_id, "meta_strategy")
    _check(intent.side in {"BUY", "SELL"}, failures, "direction", "meta_strategy.order_validation.invalid_direction", intent.side, ("BUY", "SELL"))
    _check(
        intent.side == context.final_direction == context.deterministic_direction,
        failures,
        "direction",
        "meta_strategy.order_validation.direction_mismatch",
        {"intent": intent.side, "final": context.final_direction, "deterministic": context.deterministic_direction},
        "all_equal",
    )
    _check(
        context.model_action in {"ACCEPT", "REDUCE_RISK", "FALLBACK"},
        failures,
        "model_action",
        "meta_strategy.order_validation.model_action_not_tradeable",
        context.model_action,
        ("ACCEPT", "REDUCE_RISK", "FALLBACK"),
    )
    _check(float(intent.quantity) > 0, failures, "quantity", "meta_strategy.order_validation.invalid_quantity", intent.quantity, ">0")
    _check(
        int(intent.quantity) <= int(context.sizing_quantity) and int(intent.quantity) <= int(context.global_approved_quantity),
        failures,
        "quantity",
        "meta_strategy.order_validation.quantity_exceeds_adjusted_cap",
        {"quantity": intent.quantity, "sizing": context.sizing_quantity, "global": context.global_approved_quantity},
        "quantity <= min(sizing, global)",
    )
    _check(_positive(context.entry_price), failures, "entry", "meta_strategy.order_validation.invalid_entry", context.entry_price, ">0")
    _check(_valid_stop(intent.side, context.entry_price, context.stop_price), failures, "stop", "meta_strategy.order_validation.invalid_stop", context.stop_price, "protective")
    _check(_valid_target(intent.side, context.entry_price, context.target_price), failures, "target", "meta_strategy.order_validation.invalid_target", context.target_price, "profitable")
    _check(
        context.reward_risk is not None and float(context.reward_risk) > 0,
        failures,
        "reward_risk",
        "meta_strategy.order_validation.invalid_reward_risk",
        context.reward_risk,
        ">0",
    )
    notional = float(intent.quantity) * abs(float(context.entry_price or 0.0))
    _check(
        notional <= float(context.available_buying_power),
        failures,
        "buying_power",
        "meta_strategy.order_validation.buying_power_insufficient",
        notional,
        context.available_buying_power,
    )
    _check(
        float(context.reserved_risk_dollars) <= float(context.maximum_reserved_risk_dollars),
        failures,
        "risk_reservation",
        "meta_strategy.order_validation.risk_reservation_exceeded",
        context.reserved_risk_dollars,
        context.maximum_reserved_risk_dollars,
    )
    _check(context.session_allowed, failures, "session", "meta_strategy.order_validation.session_blocked", False, True)
    quote_age = _quote_age_seconds(context.snapshot)
    _check(
        quote_age is not None and quote_age <= context.max_quote_age_seconds,
        failures,
        "quote_freshness",
        "meta_strategy.order_validation.quote_stale",
        quote_age,
        context.max_quote_age_seconds,
    )
    spread = context.snapshot.spread_bps
    _check(
        spread is not None and float(spread) <= float(context.max_spread_bps),
        failures,
        "spread",
        "meta_strategy.order_validation.spread_too_wide",
        spread,
        context.max_spread_bps,
    )
    liquidity = float((context.snapshot.liquidity or {}).get("shareVolume") or context.snapshot.volume or 0.0)
    _check(
        liquidity >= float(context.minimum_liquidity),
        failures,
        "liquidity",
        "meta_strategy.order_validation.liquidity_too_low",
        liquidity,
        context.minimum_liquidity,
    )
    _check(
        intent.order_intent_id not in context.duplicate_intent_ids,
        failures,
        "duplicate_intent",
        "meta_strategy.order_validation.duplicate_intent",
        intent.order_intent_id,
        "unique",
    )
    _check(
        intent.symbol not in context.existing_position_symbols,
        failures,
        "existing_position",
        "meta_strategy.order_validation.existing_position_conflict",
        intent.symbol,
        "no_existing_position",
    )
    return _result(failures=tuple(failures), extra_reason_codes=(), context=context)


def _result(
    *,
    failures: tuple[MetaStrategyOrderValidationFailure, ...],
    extra_reason_codes: tuple[str, ...],
    context: MetaStrategyOrderValidationContext,
) -> MetaStrategyOrderValidationResult:
    reason_codes = tuple(dict.fromkeys((*extra_reason_codes, *(failure.reason_code for failure in failures))))
    payload = {
        "valid": not failures,
        "failureCount": len(failures),
        "failures": tuple(failure.__dict__ for failure in failures),
        "reasonCodes": reason_codes,
        "orderIntentId": getattr(context.order_intent, "order_intent_id", None),
    }
    return MetaStrategyOrderValidationResult(
        valid=not failures,
        failures=failures,
        reason_codes=reason_codes,
        persisted_payload=payload,
    )


def _check(
    condition: bool,
    failures: list[MetaStrategyOrderValidationFailure],
    field: str,
    reason_code: str,
    observed: Any,
    threshold: Any,
) -> None:
    if not condition:
        failures.append(MetaStrategyOrderValidationFailure(field=field, reason_code=reason_code, observed=observed, threshold=threshold))


def _positive(value: float | None) -> bool:
    return value is not None and float(value) > 0


def _valid_stop(side: str, entry: float | None, stop: float | None) -> bool:
    if not _positive(entry) or not _positive(stop):
        return False
    return float(stop) < float(entry) if side == "BUY" else float(stop) > float(entry)


def _valid_target(side: str, entry: float | None, target: float | None) -> bool:
    if not _positive(entry) or not _positive(target):
        return False
    return float(target) > float(entry) if side == "BUY" else float(target) < float(entry)


def _quote_age_seconds(snapshot: MetaStrategyMarketSnapshot) -> float | None:
    quote = snapshot.quote or {}
    timestamp = quote.get("timestamp") if isinstance(quote, dict) else None
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return max(0.0, (snapshot.timestamp - parsed).total_seconds())


__all__ = [
    "MetaStrategyOrderValidationContext",
    "MetaStrategyOrderValidationFailure",
    "MetaStrategyOrderValidationResult",
    "validate_meta_strategy_order",
]
