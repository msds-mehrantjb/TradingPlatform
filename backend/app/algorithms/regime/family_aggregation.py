"""Backend-owned family aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation


DEFAULT_MAX_FAMILY_CONTRIBUTION = 0.40


def aggregate_family_scores(outputs: tuple[RegimeStrategyEvaluation, ...], settings: dict[str, Any] | None = None) -> dict[str, object]:
    max_family_contribution = max(
        0.01,
        min(1.0, float((settings or {}).get("maximumContributionPerFamily", DEFAULT_MAX_FAMILY_CONTRIBUTION))),
    )
    directional = tuple(output for output in outputs if output.role == "directional")
    eligible = tuple(output for output in directional if output.eligible)
    directional_votes = tuple(output for output in eligible if output.signal in {"Buy", "Sell"})
    by_family: dict[str, list[RegimeStrategyEvaluation]] = defaultdict(list)
    for output in directional_votes:
        by_family[output.family].append(output)

    family_scores: dict[str, float] = {}
    family_confidence: dict[str, float] = {}
    selected_by_family: dict[str, str] = {}
    opposing_evidence: list[dict[str, object]] = []
    collision_reasons: list[str] = []
    buy = 0.0
    sell = 0.0
    for family, family_outputs in sorted(by_family.items()):
        selected = max(family_outputs, key=lambda item: (item.weight * item.confidence, item.confidence, item.strategy_id))
        if len(family_outputs) > 1:
            collision_reasons.append(f"regime.family_aggregation.correlated_family_collapsed:{family}")
        raw_contribution = selected.weight * selected.confidence
        contribution = min(max_family_contribution, raw_contribution)
        signed = contribution if selected.signal == "Buy" else -contribution
        family_scores[family] = round(signed, 6)
        family_confidence[family] = round(selected.confidence, 4)
        selected_by_family[family] = selected.strategy_id
        if selected.signal == "Buy":
            buy += contribution
        else:
            sell += contribution
        for item in family_outputs:
            if item.strategy_id != selected.strategy_id or item.signal != selected.signal:
                opposing_evidence.append(
                    {
                        "family": family,
                        "strategyId": item.strategy_id,
                        "signal": item.signal,
                        "confidence": item.confidence,
                        "reason": item.reason,
                        "collision": item.strategy_id != selected.strategy_id,
                    }
                )

    signal_total = buy + sell
    if signal_total <= 0:
        scores = {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    else:
        scores = {"buy": buy / signal_total, "sell": sell / signal_total, "hold": 0.0}
    if scores["buy"] > scores["sell"]:
        signal = "Buy"
        edge = scores["buy"] - scores["sell"]
        score = scores["buy"]
    elif scores["sell"] > scores["buy"]:
        signal = "Sell"
        edge = scores["sell"] - scores["buy"]
        score = scores["sell"]
    else:
        signal = "Hold"
        edge = 0.0
        score = 0.0
    active_family_count = len(by_family)
    abstention_rate = 1 - (len(directional_votes) / max(1, len(directional)))
    return {
        "scores": scores,
        "familyScores": family_scores,
        "familyConfidence": family_confidence,
        "selectedStrategyByFamily": selected_by_family,
        "aggregateSignal": signal.lower(),
        "signal": signal,
        "winningScore": round(score, 4),
        "winningEdge": round(edge, 4),
        "eligibleStrategyCount": len(eligible),
        "activeStrategyCount": len(directional_votes),
        "activeFamilyCount": active_family_count,
        "abstentionRate": round(abstention_rate, 4),
        "opposingEvidence": tuple(opposing_evidence),
        "correlationCollisionReasonCodes": tuple(collision_reasons),
        "maximumContributionPerFamily": max_family_contribution,
        "aggregationPolicy": "strongest_eligible_strategy_per_family",
    }
