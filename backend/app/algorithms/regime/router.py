"""Backend-owned Regime strategy routing."""

from __future__ import annotations

from dataclasses import replace

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation, StrategyRole
from backend.app.algorithms.regime.family_aggregation import aggregate_directional_strategies, apply_confirmation_layer, apply_safety_layer
from backend.app.algorithms.regime.strategy_registry import (
    REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES,
    REGIME_NO_TRADE_REGIMES,
    REGIME_STRATEGY_DEFINITIONS,
    evaluate_strategy,
    regime_strategy_can_route,
)


def route_regime_strategies(
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    profile: dict | None = None,
    settings: dict | None = None,
) -> dict[str, object]:
    context_outputs = evaluate_regime_role("regime_context", snapshot, classification, settings=settings)
    contextual_profile = apply_context_modules_to_profile(profile or {}, context_outputs)
    directional_outputs, skipped = evaluate_directional_strategies(
        snapshot,
        classification,
        profile=contextual_profile,
        settings=settings,
    )
    confirmation_outputs = evaluate_regime_role("confirmation", snapshot, classification, settings=settings)
    adjusted_directional = apply_confirmation_modules(directional_outputs, confirmation_outputs, settings=settings)
    safety_outputs = evaluate_regime_role("safety_gate", snapshot, classification, settings=settings)
    outputs = (*safety_outputs, *context_outputs, *adjusted_directional, *confirmation_outputs)
    authoritative = _order_authoritative_directionals(adjusted_directional)
    represented_families = tuple(sorted({output.family for output in authoritative}))
    minimum_families = _minimum_independent_families(contextual_profile, settings)
    directional_aggregation = aggregate_directional_strategies(directional_outputs, contextual_profile, classification=classification)
    confirmation_layer = apply_confirmation_layer(directional_aggregation, confirmation_outputs, context_outputs, settings=contextual_profile)
    route_blockers: list[str] = []
    if classification.raw_regime in REGIME_NO_TRADE_REGIMES:
        route_blockers.append("regime.router.no_trade_regime")
    if len(represented_families) < minimum_families:
        route_blockers.append("regime.router.minimum_independent_strategies_not_met")
    safety_layer = apply_safety_layer(confirmation_layer, safety_outputs=safety_outputs, blockers=tuple(route_blockers))
    return {
        "outputs": tuple(outputs),
        "safetyOutputs": safety_outputs,
        "contextOutputs": context_outputs,
        "directionalOutputs": adjusted_directional,
        "confirmationOutputs": confirmation_outputs,
        "skippedStrategies": tuple(skipped),
        "selectedStrategyIds": tuple(output.strategy_id for output in authoritative),
        "representedFamilies": represented_families,
        "eligibleIndependentStrategyCount": len(represented_families),
        "minimumIndependentStrategiesRequired": minimum_families,
        "routeSignal": "Hold" if route_blockers else "Routed",
        "routeBlockers": tuple(route_blockers),
        "directionalAggregation": directional_aggregation,
        "confirmationLayer": confirmation_layer,
        "safetyLayer": safety_layer,
        "profileRouting": _profile_routing(contextual_profile),
    }


def evaluate_regime_role(
    role: StrategyRole,
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    *,
    settings: dict | None = None,
) -> tuple[RegimeStrategyEvaluation, ...]:
    strategy_settings = (settings or {}).get("strategy_settings") or (settings or {}).get("strategySettings") or {}
    return tuple(
        evaluate_strategy(definition.strategy_id, snapshot, classification, strategy_settings.get(definition.strategy_id))
        for definition in REGIME_STRATEGY_DEFINITIONS
        if definition.role == role
    )


def evaluate_directional_strategies(
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    *,
    profile: dict | None = None,
    settings: dict | None = None,
) -> tuple[tuple[RegimeStrategyEvaluation, ...], tuple[dict[str, str], ...]]:
    outputs: list[RegimeStrategyEvaluation] = []
    skipped: list[dict[str, str]] = []
    profile = profile or {}
    strategy_settings = (settings or {}).get("strategy_settings") or (settings or {}).get("strategySettings") or {}
    for definition in REGIME_STRATEGY_DEFINITIONS:
        if definition.role != "directional":
            continue
        if definition.lifecycle_status in {"disabled", "unavailable"}:
            skipped.append({"strategyId": definition.strategy_id, "reason": f"regime.router.lifecycle_{definition.lifecycle_status}"})
            continue
        compatible = regime_strategy_can_route(definition, classification.raw_regime, profile)
        if not compatible:
            skipped.append({"strategyId": definition.strategy_id, "reason": _profile_skip_reason(definition.family, classification.raw_regime, profile)})
            continue
        outputs.append(evaluate_strategy(definition.strategy_id, snapshot, classification, strategy_settings.get(definition.strategy_id)))
    return tuple(outputs), tuple(skipped)


def apply_context_modules_to_profile(profile: dict | None, context_outputs: tuple[RegimeStrategyEvaluation, ...]) -> dict:
    effective = dict(profile or {})
    effective["contextModuleEvidence"] = tuple(
        {
            "strategyId": output.strategy_id,
            "reason": output.reason,
            "confidence": output.confidence,
            "evidence": output.evidence,
        }
        for output in context_outputs
    )
    disabled = set(effective.get("disabledStrategyFamilies", ()))
    for output in context_outputs:
        if output.strategy_id == "atr_volatility_regime" and output.evidence.get("atrPercent") is not None:
            atr_percent = float(output.evidence["atrPercent"])
            if atr_percent >= 0.02:
                disabled.add("mean_reversion")
    effective["disabledStrategyFamilies"] = tuple(sorted(disabled))
    return effective


def apply_confirmation_modules(
    directional_outputs: tuple[RegimeStrategyEvaluation, ...],
    confirmation_outputs: tuple[RegimeStrategyEvaluation, ...],
    *,
    settings: dict | None = None,
) -> tuple[RegimeStrategyEvaluation, ...]:
    if not confirmation_outputs:
        return directional_outputs
    max_adjustment = max(0.0, min(0.20, float((settings or {}).get("maximumConfirmationAdjustment", 0.08))))
    average_confirmation = sum(output.confidence for output in confirmation_outputs) / len(confirmation_outputs)
    adjustment = max(-max_adjustment, min(max_adjustment, (average_confirmation - 0.5) * 0.20))
    adjusted: list[RegimeStrategyEvaluation] = []
    for output in directional_outputs:
        if output.signal == "Hold":
            adjusted.append(output)
            continue
        confidence = max(0.0, min(1.0, output.confidence + adjustment))
        adjusted.append(
            replace(
                output,
                confidence=confidence,
                evidence={
                    **output.evidence,
                    "confirmationAdjustment": round(adjustment, 6),
                    "confirmationOutputs": tuple(
                        {"strategyId": item.strategy_id, "confidence": item.confidence, "reason": item.reason}
                        for item in confirmation_outputs
                    ),
                },
            )
        )
    return tuple(adjusted)


def blocking_safety_reasons(safety_outputs: tuple[RegimeStrategyEvaluation, ...]) -> tuple[str, ...]:
    return tuple(
        output.reason
        for output in safety_outputs
        if output.reason != "regime.safety.clear" and output.confidence >= 0.5
    )


def _profile_routing(profile: dict) -> dict[str, object]:
    return {
        "noNewEntries": bool(profile.get("noNewEntries", False)),
        "preferredStrategyFamilies": tuple(profile.get("preferredStrategyFamilies", ())),
        "allowedStrategyFamilies": tuple(profile.get("allowedStrategyFamilies", ())),
        "disabledStrategyFamilies": tuple(profile.get("disabledStrategyFamilies", ())),
        "contextModuleEvidence": tuple(profile.get("contextModuleEvidence", ())),
    }


def _order_authoritative_directionals(outputs: tuple[RegimeStrategyEvaluation, ...]) -> tuple[RegimeStrategyEvaluation, ...]:
    return tuple(
        output
        for output in outputs
        if output.role == "directional"
        and output.eligible
        and output.lifecycle_status == "active"
        and output.signal in {"Buy", "Sell"}
    )


def _minimum_independent_families(profile: dict | None, settings: dict | None) -> int:
    raw = (profile or {}).get("minimumIndependentFamilies")
    if raw is None:
        raw = (settings or {}).get("minimumIndependentFamilies")
    if raw is None:
        raw = REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES
    return max(1, int(raw))


def _profile_skip_reason(family: str, regime: str, profile: dict) -> str:
    if profile.get("noNewEntries"):
        return "regime.router.profile_no_new_entries"
    if family in set(profile.get("disabledStrategyFamilies", ())):
        return "regime.router.profile_family_disabled"
    if profile.get("allowedStrategyFamilies"):
        return "regime.router.profile_family_not_allowed"
    if regime in REGIME_NO_TRADE_REGIMES:
        return "regime.router.no_trade_regime"
    return "regime.router.incompatible_with_confirmed_regime"
