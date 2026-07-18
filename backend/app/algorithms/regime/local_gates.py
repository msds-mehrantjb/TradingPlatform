"""Regime-local decision gates."""

from __future__ import annotations


def evaluate_regime_local_gates(aggregation: dict[str, object], classification, state, settings: dict) -> tuple[str, ...]:
    blockers: list[str] = []
    if classification.raw_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        blockers.append(f"regime.local_gate.no_entry_regime:{classification.raw_regime}")
    if int(aggregation["activeStrategyCount"]) < int(settings["minimumActiveStrategies"]):
        blockers.append("regime.local_gate.minimum_active_strategies")
    if int(aggregation["activeFamilyCount"]) < int(settings["minimumIndependentFamilies"]):
        blockers.append("regime.local_gate.minimum_independent_families")
    if float(aggregation["winningScore"]) < float(settings["minimumWinningScore"]):
        blockers.append("regime.local_gate.minimum_winning_score")
    if float(aggregation["winningEdge"]) < float(settings["minimumSignalEdge"]):
        blockers.append("regime.local_gate.minimum_signal_edge")
    if classification.confidence < float(settings["minimumRegimeConfidence"]):
        blockers.append("regime.local_gate.minimum_regime_confidence")
    if float(aggregation["abstentionRate"]) > float(settings["maximumAbstentionRate"]):
        blockers.append("regime.local_gate.maximum_abstention_rate")
    blockers.extend(classification.no_trade_reasons)
    return tuple(dict.fromkeys(blockers))

