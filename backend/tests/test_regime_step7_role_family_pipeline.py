from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.family_aggregation import aggregate_family_scores
from backend.app.algorithms.regime.router import apply_confirmation_modules, evaluate_regime_role
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.fixtures.market_snapshots import snapshot


def test_safety_outputs_block_new_entry_before_directional_routing() -> None:
    market = snapshot(
        "up",
        context={
            "quoteFreshness": {
                "status": "stale",
                "ageMs": 60_000,
                "bid": 100.0,
                "ask": 100.02,
                "spreadBps": 2.0,
            }
        },
    )
    decision = calculate_regime_decision(
        market,
        settings={
            "minimumWinningScore": 0,
            "minimumSignalEdge": 0,
            "minimumActiveStrategies": 0,
            "minimumIndependentFamilies": 1,
            "minimumRegimeConfidence": 0,
        },
    )

    assert decision.signal == "Hold"
    assert decision.trade_allowed is False
    assert "regime.safety.stale_data" in decision.trade_blockers
    assert all(output.role != "directional" for output in decision.strategy_outputs)


def test_context_and_confirmation_roles_never_emit_direction() -> None:
    market = snapshot("up")
    raw = classification()
    context_outputs = evaluate_regime_role("regime_context", market, raw)
    confirmation_outputs = evaluate_regime_role("confirmation", market, raw)

    assert context_outputs
    assert confirmation_outputs
    assert {output.signal for output in context_outputs} == {"Hold"}
    assert {output.signal for output in confirmation_outputs} == {"Hold"}


def test_confirmation_modules_adjust_confidence_without_creating_or_reversing_direction() -> None:
    sell = _directional("trend_sell", "trend", "Sell", confidence=0.60, weight=0.40)
    hold = _directional("trend_hold", "trend", "Hold", confidence=0.95, weight=0.40)
    confirmations = (
        RegimeStrategyEvaluation("volume_confirmation", "Volume", "confirmation", "confirmation", "Hold", 1.0, 0.0, True, "ok"),
        RegimeStrategyEvaluation("adx_trend_strength", "ADX", "confirmation", "confirmation", "Hold", 1.0, 0.0, True, "ok"),
    )

    adjusted_sell, adjusted_hold = apply_confirmation_modules((sell, hold), confirmations, settings={"maximumConfirmationAdjustment": 0.05})

    assert adjusted_sell.signal == "Sell"
    assert adjusted_sell.confidence == 0.65
    assert adjusted_sell.evidence["confirmationAdjustment"] == 0.05
    assert adjusted_hold.signal == "Hold"
    assert adjusted_hold.confidence == hold.confidence


def test_family_aggregation_caps_correlated_strategy_influence() -> None:
    trend_a = _directional("moving_average_trend", "trend", "Buy", confidence=0.90, weight=0.60)
    trend_b = _directional("trend_pullback", "trend", "Buy", confidence=0.80, weight=0.60)
    momentum = _directional("macd_momentum", "momentum", "Buy", confidence=0.70, weight=0.60)
    baseline = aggregate_family_scores((trend_a, momentum), {"maximumContributionPerFamily": 0.25})
    duplicated = aggregate_family_scores((trend_a, trend_b, momentum), {"maximumContributionPerFamily": 0.25})

    assert duplicated["activeFamilyCount"] == baseline["activeFamilyCount"] == 2
    assert duplicated["familyScores"]["trend"] == baseline["familyScores"]["trend"] == 0.25
    assert duplicated["scores"] == baseline["scores"]
    assert "regime.family_aggregation.correlated_family_collapsed:trend" in duplicated["correlationCollisionReasonCodes"]
    assert duplicated["selectedStrategyByFamily"]["trend"] == "moving_average_trend"


def test_hold_confidence_does_not_create_artificial_winning_score() -> None:
    high_confidence_hold = _directional("vwap_mean_reversion", "vwap", "Hold", confidence=0.99, weight=0.80)
    aggregation = aggregate_family_scores((high_confidence_hold,), {"maximumContributionPerFamily": 0.25})

    assert aggregation["signal"] == "Hold"
    assert aggregation["winningScore"] == 0.0
    assert aggregation["winningEdge"] == 0.0
    assert aggregation["activeFamilyCount"] == 0


def test_buy_sell_decisions_require_minimum_independent_families() -> None:
    market = snapshot("up")
    one_family_settings = {
        "minimumWinningScore": 0,
        "minimumSignalEdge": 0,
        "minimumActiveStrategies": 1,
        "minimumIndependentFamilies": 99,
        "minimumRegimeConfidence": 0,
        "maximumAbstentionRate": 1,
    }
    decision = calculate_regime_decision(market, settings=one_family_settings)

    assert decision.signal == "Hold"
    assert "regime.local_gate.minimum_independent_families" in decision.trade_blockers


def _directional(strategy_id: str, family: str, signal: str, *, confidence: float, weight: float) -> RegimeStrategyEvaluation:
    return RegimeStrategyEvaluation(
        strategy_id=strategy_id,
        name=strategy_id,
        family=family,
        role="directional",
        signal=signal,
        confidence=confidence,
        weight=weight,
        eligible=True,
        reason=f"regime.strategy.{strategy_id}",
        evidence={"fixture": True},
    )
