from __future__ import annotations

from dataclasses import replace

from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES
from backend.app.algorithms.regime.dynamic_profile import PROFILE_VERSION, resolve_effective_regime_profile
from backend.app.algorithms.regime.local_gates import estimate_entry_transaction_cost_bps, evaluate_regime_local_gates
from backend.tests.regime.fixtures.classification_cases import classification


CLEAR_AGGREGATION = {
    "activeStrategyCount": 4,
    "eligibleStrategyCount": 4,
    "activeFamilyCount": 3,
    "winningScore": 0.9,
    "winningEdge": 0.5,
    "abstentionRate": 0.0,
    "selectedStrategyByFamily": {"trend": "moving_average_trend"},
    "familyScores": {"trend": 0.3, "momentum": 0.2, "structure": 0.2},
}


def test_every_canonical_profile_is_bounded_and_explainable() -> None:
    baseline = validate_regime_settings()
    for regime in CANONICAL_MARKET_REGIMES:
        profile = resolve_effective_regime_profile(baseline, regime)
        assert profile["baselineSettingsVersion"] == baseline["settingsVersion"]
        assert profile["baselineProfileVersion"] == baseline["profileVersion"]
        assert profile["profileVersion"] == PROFILE_VERSION
        assert profile["overlayReasons"]
        assert profile["finalValues"]["baseRiskPercent"] == profile["baseRiskPercent"]
        assert profile["baseRiskPercent"] <= baseline["baseRiskPercent"]
        assert profile["maxPositionPercent"] <= baseline["maxPositionPercent"]
        assert profile["maxParticipationPercent"] <= baseline["maxParticipationPercent"]
        assert profile["maximumSlippageBps"] <= baseline["maximumSlippageBps"]
        assert profile["riskMultiplier"] <= 1.0
        assert profile["maximumPositionMultiplier"] <= 1.0


def test_risk_off_profiles_block_entries_but_allow_position_management() -> None:
    baseline = validate_regime_settings()
    for regime in ("event_risk", "liquidity_stress", "extreme_volatility_no_trade", "choppy_mixed"):
        profile = resolve_effective_regime_profile(baseline, regime)
        assert profile["noNewEntries"] is True
        assert profile["baseRiskPercent"] == 0.0
        assert profile["maxPositionPercent"] == 0.0
        assert profile["riskOffPositionManagementAllowed"] is True


def test_local_gates_emit_stable_blocker_codes_for_runtime_risk() -> None:
    settings = validate_regime_settings(
        {
            "minimumWinningScore": 0,
            "minimumSignalEdge": 0,
            "minimumActiveStrategies": 1,
            "minimumIndependentFamilies": 1,
            "minimumRegimeConfidence": 0,
        }
    )
    raw = classification(
        liquidity="poor",
        event_risk="blackout",
        missing_inputs=("bid",),
        features={"quoteAgeMs": 10_000, "barAgeSeconds": 120, "expectedGrossEdgeBps": 2.0},
    )
    raw = replace(raw, timestamp="2026-07-23T20:45:00Z")
    context = {
        "quoteFreshness": {"status": "stale", "ageMs": 10_000, "spreadBps": 12.0},
        "haltLuldCircuitBreaker": {"haltState": "halted", "circuitBreakerState": "active", "newEntriesBlocked": True},
        "dailyCounters": {"dailyLossPercent": settings["maxDailyLossPercent"], "consecutiveLosses": settings["maxConsecutiveLosses"], "tradeCount": settings["maxTradesPerDay"]},
        "cooldownState": {"remainingBars": 2},
        "familyCooldowns": {"trend": {"remainingBars": 1}},
        "openPosition": {"positionId": "regime-position-1", "quantity": 10},
        "proposedNotional": settings["maxNotionalDollars"] + 1,
        "proposedShares": settings["maxAllowedShares"] + 1,
        "proposedParticipationRate": settings["maxParticipationPercent"] + 0.01,
        "decisionAgeSeconds": settings["staleBarToleranceSeconds"] + 1,
        "duplicateProposal": True,
        "expectedSlippageBps": 4.0,
        "feesBps": 1.0,
        "adverseSelectionBufferBps": 2.0,
    }
    blockers = evaluate_regime_local_gates(CLEAR_AGGREGATION, raw, None, settings, context)

    expected = {
        "regime.local_gate.data_completeness",
        "regime.local_gate.stale_candle",
        "regime.local_gate.stale_quote",
        "regime.local_gate.liquidity_permission",
        "regime.local_gate.session_permission",
        "regime.local_gate.event_blackout",
        "regime.local_gate.halt_luld_circuit_breaker",
        "regime.local_gate.daily_loss_limit",
        "regime.local_gate.consecutive_loss_limit",
        "regime.local_gate.maximum_trades",
        "regime.local_gate.strategy_cooldown",
        "regime.local_gate.family_cooldown",
        "regime.local_gate.existing_position",
        "regime.local_gate.pyramiding_disabled",
        "regime.local_gate.maximum_notional",
        "regime.local_gate.maximum_shares",
        "regime.local_gate.maximum_participation",
        "regime.local_gate.minimum_expected_net_edge",
        "regime.local_gate.decision_age",
        "regime.local_gate.order_ttl",
        "regime.local_gate.entry_cutoff",
        "regime.local_gate.duplicate_proposal",
    }
    assert expected.issubset(set(blockers))


def test_transaction_cost_estimate_uses_half_spread_slippage_fees_and_adverse_selection() -> None:
    raw = classification(features={"spreadBps": 10.0})
    cost = estimate_entry_transaction_cost_bps(
        raw,
        {"maximumSlippageBps": 3.0, "estimatedFeesBps": 0.5, "adverseSelectionBufferBps": 1.25},
    )

    assert cost == {
        "halfSpreadBps": 5.0,
        "expectedSlippageBps": 3.0,
        "feesBps": 0.5,
        "adverseSelectionBps": 1.25,
        "marketImpactBps": 0.0,
        "uncertaintyBufferBps": 0.0,
        "totalCostBps": 9.75,
    }
