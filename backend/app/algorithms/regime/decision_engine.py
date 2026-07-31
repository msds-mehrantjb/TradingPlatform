"""Backend-authoritative Regime decision engine."""

from __future__ import annotations

from hashlib import sha256
from dataclasses import replace
from typing import Any

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import (
    REGIME_ALGORITHM_ID,
    REGIME_ALGORITHM_VERSION,
    REGIME_STRATEGY_CATALOG_VERSION,
    RegimeAxes,
    RegimeClassification,
    RegimeDecision,
    RegimeHysteresisState,
    RegimeMarketSnapshot,
)
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile
from backend.app.algorithms.regime.family_aggregation import aggregate_directional_strategies, apply_confirmation_layer, apply_safety_layer
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.app.algorithms.regime.local_gates import estimate_entry_transaction_cost_bps, evaluate_regime_local_gates
from backend.app.algorithms.regime.router import (
    apply_confirmation_modules,
    apply_context_modules_to_profile,
    blocking_safety_reasons,
    evaluate_directional_strategies,
    evaluate_regime_role,
)


def calculate_regime_decision(
    snapshot: RegimeMarketSnapshot,
    *,
    settings: dict[str, Any] | None = None,
    previous_state: RegimeHysteresisState | None = None,
) -> RegimeDecision:
    validated_settings = validate_regime_settings(settings)
    data_validation_blockers = _validate_decision_input(snapshot)
    pre_safety_outputs = evaluate_regime_role("safety_gate", snapshot, _pre_classification_safety_context(snapshot), settings=validated_settings)
    safety_blockers = (*data_validation_blockers, *blocking_safety_reasons(pre_safety_outputs))
    classification = classify_market_regime(snapshot)
    risk_off_blockers = _immediate_risk_off_blockers(classification)
    state = confirm_regime_transition(classification, previous_state, validated_settings)
    effective_profile = resolve_effective_regime_profile(validated_settings, state.confirmed_regime, classification, snapshot)
    if safety_blockers or risk_off_blockers:
        effective_profile = {**effective_profile, "noNewEntries": True}
    context_outputs = evaluate_regime_role("regime_context", snapshot, classification, settings=validated_settings)
    contextual_profile = apply_context_modules_to_profile(effective_profile, context_outputs)
    routing_classification = replace(classification, raw_regime=state.confirmed_regime)
    directional_outputs, skipped = evaluate_directional_strategies(
        snapshot,
        routing_classification,
        profile=contextual_profile,
        settings=validated_settings,
    )
    confirmation_outputs = evaluate_regime_role("confirmation", snapshot, classification, settings=validated_settings)
    adjusted_directional_outputs = apply_confirmation_modules(directional_outputs, confirmation_outputs, settings=validated_settings)
    outputs = (*pre_safety_outputs, *context_outputs, *adjusted_directional_outputs, *confirmation_outputs)
    directional_aggregation = aggregate_directional_strategies(directional_outputs, contextual_profile, classification=routing_classification)
    confirmation_aggregation = apply_confirmation_layer(
        directional_aggregation,
        confirmation_outputs,
        context_outputs,
        settings=contextual_profile,
    )
    local_blockers = evaluate_regime_local_gates(confirmation_aggregation, classification, state, contextual_profile)
    transaction_cost = estimate_entry_transaction_cost_bps(classification, contextual_profile)
    blockers = tuple(dict.fromkeys((*safety_blockers, *risk_off_blockers, *local_blockers)))
    safety_aggregation = apply_safety_layer(
        confirmation_aggregation,
        safety_outputs=pre_safety_outputs,
        blockers=blockers,
        runtime_context=validated_settings,
    )
    aggregation = safety_aggregation
    signal = str(aggregation["signal"])
    decision_id = _decision_id(snapshot.symbol, snapshot.latest.timestamp, classification.raw_regime)
    return RegimeDecision(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_ALGORITHM_VERSION,
        settings_version=str(validated_settings.get("settingsVersion")),
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        profile_version=str(validated_settings.get("profileVersion")),
        decision_id=decision_id,
        symbol=snapshot.symbol,
        signal=signal,
        aggregate_signal=str(aggregation["aggregateSignal"]),
        trade_allowed=signal != "Hold" and not blockers,
        trade_blockers=blockers,
        raw_classification=classification,
        confirmed_state=state,
        strategy_outputs=outputs,
        family_scores=aggregation["familyScores"],
        effective_settings={
            **contextual_profile,
            "pipelineOrder": (
                "data_validation",
                "safety_gates",
                "classification",
                "hysteresis",
                "dynamic_profile",
                "context_modules",
                "strategy_routing",
                "directional_strategies",
                "confirmation_modules",
                "family_aggregation",
                "local_gates",
                "sizing",
                "order_proposal",
            ),
            "familyAggregation": aggregation,
            "directionalAggregation": directional_aggregation,
            "confirmationLayer": confirmation_aggregation,
            "safetyLayer": safety_aggregation,
            "skippedStrategies": skipped,
            "localGateTransactionCostEstimate": transaction_cost,
        },
        score=float(aggregation["winningScore"]),
        confidence=classification.confidence,
    )


def _decision_id(symbol: str, timestamp: str, regime: str) -> str:
    digest = sha256(f"{symbol}:{timestamp}:{regime}".encode("utf-8")).hexdigest()[:16]
    return f"regime-decision-{digest}"


def _validate_decision_input(snapshot: RegimeMarketSnapshot) -> tuple[str, ...]:
    blockers: list[str] = []
    if snapshot.symbol != "SPY":
        blockers.append("regime.data_validation.symbol_not_supported")
    if not snapshot.candles or not snapshot.one_minute_candles:
        blockers.append("regime.data_validation.missing_primary_candles")
    latest = snapshot.latest
    if latest.close <= 0 or latest.high < latest.low:
        blockers.append("regime.data_validation.invalid_completed_bar")
    return tuple(blockers)


def _pre_classification_safety_context(snapshot: RegimeMarketSnapshot) -> RegimeClassification:
    return RegimeClassification(
        raw_regime="pre_classification",
        axes=RegimeAxes(
            direction="unknown",
            volatility="unknown",
            structure="unknown",
            liquidity="unknown",
            session="unknown",
            event_risk="unknown",
        ),
        confidence=0.0,
        features={},
        evidence={"liquidityEvidence": {}},
        missing_inputs=(),
        no_trade_reasons=(),
        timestamp=snapshot.latest.timestamp,
    )


def _immediate_risk_off_blockers(classification: RegimeClassification) -> tuple[str, ...]:
    blockers: list[str] = []
    if classification.raw_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        blockers.append(f"regime.immediate_risk_off_classification:{classification.raw_regime}")
    if classification.axes.volatility == "extreme":
        blockers.append("regime.immediate_risk_off_axis:volatility_extreme")
    if classification.axes.event_risk == "blackout":
        blockers.append("regime.immediate_risk_off_axis:event_blackout")
    return tuple(blockers)
