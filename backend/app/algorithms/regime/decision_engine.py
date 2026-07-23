"""Backend-authoritative Regime decision engine."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import (
    REGIME_ALGORITHM_ID,
    REGIME_ALGORITHM_VERSION,
    REGIME_PROFILE_VERSION,
    REGIME_SETTINGS_VERSION,
    REGIME_STRATEGY_CATALOG_VERSION,
    RegimeDecision,
    RegimeHysteresisState,
    RegimeMarketSnapshot,
)
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile
from backend.app.algorithms.regime.family_aggregation import aggregate_family_scores
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_gates
from backend.app.algorithms.regime.router import route_regime_strategies


def calculate_regime_decision(
    snapshot: RegimeMarketSnapshot,
    *,
    settings: dict[str, Any] | None = None,
    previous_state: RegimeHysteresisState | None = None,
) -> RegimeDecision:
    validated_settings = validate_regime_settings(settings)
    classification = classify_market_regime(snapshot)
    state = confirm_regime_transition(classification, previous_state, validated_settings)
    effective_profile = resolve_effective_regime_profile(validated_settings, state.confirmed_regime)
    routing = route_regime_strategies(snapshot, classification, effective_profile)
    outputs = routing["outputs"]
    aggregation = aggregate_family_scores(outputs)
    blockers = evaluate_regime_local_gates(aggregation, classification, state, effective_profile)
    signal = aggregation["signal"] if not blockers else "Hold"
    decision_id = _decision_id(snapshot.symbol, snapshot.latest.timestamp, classification.raw_regime)
    return RegimeDecision(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_ALGORITHM_VERSION,
        settings_version=REGIME_SETTINGS_VERSION,
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        profile_version=REGIME_PROFILE_VERSION,
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
        effective_settings=effective_profile,
        score=float(aggregation["winningScore"]),
        confidence=classification.confidence,
    )


def _decision_id(symbol: str, timestamp: str, regime: str) -> str:
    digest = sha256(f"{symbol}:{timestamp}:{regime}".encode("utf-8")).hexdigest()[:16]
    return f"regime-decision-{digest}"
