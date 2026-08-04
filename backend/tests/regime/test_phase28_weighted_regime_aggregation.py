from __future__ import annotations

from math import inf, nan

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation
from backend.app.algorithms.regime.family_aggregation import aggregate_directional_strategies


def test_phase28_strategy_ordering_does_not_change_weighted_aggregation_result() -> None:
    outputs = (
        _output("trend_a", "trend", "Buy", 0.82, 0.70, edge=24.0),
        _output("breakout_a", "breakout", "Buy", 0.71, 0.55, edge=18.0),
        _output("reversal_a", "reversal", "Sell", 0.46, 0.30, edge=10.0),
        _output("trend_b", "trend", "Buy", 0.88, 0.80, edge=16.0),
    )
    settings = {
        "minimumActiveStrategies": 2,
        "minimumIndependentFamilies": 2,
        "minimumWinningScore": 0.55,
        "minimumSignalEdge": 0.10,
        "minimumNetExpectedEdgeBps": 1.0,
        "maximumContributionPerFamily": 0.35,
        "estimatedFeesBps": 0.2,
        "estimatedSpreadBps": 0.3,
        "estimatedSlippageBps": 0.4,
    }

    forward = aggregate_directional_strategies(outputs, settings)
    reversed_result = aggregate_directional_strategies(tuple(reversed(outputs)), settings)

    for key in (
        "signal",
        "scores",
        "familyScores",
        "selectedStrategyByFamily",
        "winningScore",
        "winningEdge",
        "expectedGrossEdgeBps",
        "expectedNetEdgeBps",
        "strategyContributions",
        "thresholdReasonCodes",
    ):
        assert forward[key] == reversed_result[key]
    assert forward["signal"] == "Buy"
    assert forward["selectedStrategyByFamily"]["trend"] == "trend_b"


def test_phase28_only_eligible_directional_strategies_contribute_votes() -> None:
    outputs = (
        _output("directional_good", "trend", "Buy", 0.9, 0.8, edge=25.0),
        _output("confirmation_bad", "confirmation", "Sell", 1.0, 1.0, edge=100.0, role="confirmation"),
        _output("context_bad", "context", "Sell", 1.0, 1.0, edge=100.0, role="regime_context"),
        _output("safety_bad", "safety", "Sell", 1.0, 1.0, edge=100.0, role="safety_gate"),
        _output("shadow_bad", "shadow", "Sell", 1.0, 1.0, edge=100.0, lifecycle_status="shadow"),
        _output("disabled_bad", "disabled", "Sell", 1.0, 1.0, edge=100.0, lifecycle_status="disabled"),
        _output("quarantined_bad", "quarantined", "Sell", 1.0, 1.0, edge=100.0, lifecycle_status="quarantined"),
        _output("unhealthy_bad", "unhealthy", "Sell", 1.0, 1.0, edge=100.0, evidence={"strategyHealth": "unhealthy"}),
        _output("missing_bad", "missing", "Sell", 1.0, 1.0, edge=100.0, data_ready=False, reason_codes=("regime.strategy.missing_inputs",)),
        _output("nan_bad", "nan", "Sell", nan, inf, edge=100.0),
    )

    result = aggregate_directional_strategies(
        outputs,
        {
            "minimumActiveStrategies": 1,
            "minimumIndependentFamilies": 1,
            "minimumWinningScore": 0.1,
            "minimumSignalEdge": 0.0,
            "minimumNetExpectedEdgeBps": 1.0,
        },
    )

    contributions = {item["strategyId"]: item for item in result["strategyContributions"]}
    assert result["signal"] == "Buy"
    assert contributions["directional_good"]["eligibility"] is True
    assert contributions["directional_good"]["weightedContribution"] > 0
    for strategy_id in set(contributions) - {"directional_good"}:
        assert contributions[strategy_id]["eligibility"] is False
        assert contributions[strategy_id]["weightedContribution"] == 0.0
    assert "regime.family_aggregation.role_not_directional:confirmation" in contributions["confirmation_bad"]["exclusionReasonCodes"]
    assert "regime.directional.strategy_health_restricted" in contributions["unhealthy_bad"]["exclusionReasonCodes"]
    assert "regime.family_aggregation.missing_inputs" in contributions["missing_bad"]["exclusionReasonCodes"]
    assert "regime.family_aggregation.weight_not_finite" in contributions["nan_bad"]["exclusionReasonCodes"]


def test_phase28_thresholds_return_hold_for_ties_low_coverage_margin_or_net_edge() -> None:
    tied = aggregate_directional_strategies(
        (
            _output("trend_buy", "trend", "Buy", 0.8, 1.0, edge=20.0),
            _output("breakout_sell", "breakout", "Sell", 0.8, 1.0, edge=20.0),
        ),
        {"minimumActiveStrategies": 2, "minimumIndependentFamilies": 2, "minimumSignalEdge": 0.01},
    )
    low_coverage = aggregate_directional_strategies(
        (_output("trend_buy", "trend", "Buy", 0.8, 1.0, edge=20.0),),
        {"minimumActiveStrategies": 2, "minimumIndependentFamilies": 2},
    )
    negative_net = aggregate_directional_strategies(
        (_output("trend_buy", "trend", "Buy", 0.8, 1.0, edge=1.0),),
        {"minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "estimatedFeesBps": 2.0},
    )

    assert tied["signal"] == "Hold"
    assert low_coverage["signal"] == "Hold"
    assert negative_net["signal"] == "Hold"
    assert "regime.family_aggregation.minimum_active_strategies_not_met" in low_coverage["thresholdReasonCodes"]
    assert "regime.family_aggregation.minimum_independent_strategies_not_met" in low_coverage["thresholdReasonCodes"]
    assert "regime.family_aggregation.positive_net_expected_edge_required" in negative_net["thresholdReasonCodes"]


def test_phase28_duplicate_strategy_and_family_correlation_do_not_multiply_votes() -> None:
    result = aggregate_directional_strategies(
        (
            _output("trend_dup", "trend", "Buy", 0.7, 1.0, edge=20.0),
            _output("trend_dup", "trend", "Buy", 0.9, 1.0, edge=18.0),
            _output("trend_other", "trend", "Buy", 1.0, 1.0, edge=30.0),
            _output("breakout", "breakout", "Buy", 0.8, 1.0, edge=16.0),
        ),
        {"minimumActiveStrategies": 2, "minimumIndependentFamilies": 2, "maximumContributionPerFamily": 0.25},
    )

    contributions = {item["strategyId"]: item for item in result["strategyContributions"]}
    assert result["activeFamilyCount"] == 2
    assert result["familyScores"]["trend"] == 0.25
    assert contributions["trend_other"]["weightedContribution"] == 0.25
    assert "regime.family_aggregation.duplicate_strategy_suppressed" in [
        reason
        for item in result["strategyContributions"]
        for reason in item["exclusionReasonCodes"]
    ]
    assert "regime.family_aggregation.correlated_family_collapsed:trend" in result["correlationCollisionReasonCodes"]


def _output(
    strategy_id: str,
    family: str,
    signal: str,
    confidence: float,
    weight: float,
    *,
    edge: float,
    role: str = "directional",
    lifecycle_status: str = "active",
    evidence: dict | None = None,
    data_ready: bool = True,
    reason_codes: tuple[str, ...] = (),
) -> RegimeStrategyEvaluation:
    return RegimeStrategyEvaluation(
        strategy_id=strategy_id,
        name=strategy_id,
        family=family,
        role=role,
        signal=signal,
        confidence=confidence,
        weight=weight,
        eligible=True,
        reason="regime.phase28.test",
        evidence=evidence or {"strategyHealth": "healthy"},
        lifecycle_status=lifecycle_status,
        expected_gross_edge_bps=edge,
        data_ready=data_ready,
        reason_codes=reason_codes,
    )
