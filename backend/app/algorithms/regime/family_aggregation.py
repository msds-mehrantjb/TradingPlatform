"""Backend-owned family aggregation."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation


def aggregate_family_scores(outputs: tuple[RegimeStrategyEvaluation, ...]) -> dict[str, object]:
    buy = 0.0
    sell = 0.0
    hold = 0.0
    family_scores: dict[str, float] = {}
    active = 0
    for output in outputs:
        if output.role != "directional" or not output.eligible:
            continue
        active += 1
        contribution = output.weight * output.confidence
        if output.signal == "Buy":
            buy += contribution
        elif output.signal == "Sell":
            sell += contribution
        else:
            hold += max(0.01, output.weight * (1 - output.confidence))
        family_scores[output.family] = family_scores.get(output.family, 0.0) + contribution
    total = max(0.0001, buy + sell + hold)
    scores = {"buy": buy / total, "sell": sell / total, "hold": hold / total}
    if scores["buy"] > scores["sell"] and scores["buy"] > scores["hold"]:
        signal = "Buy"
        edge = scores["buy"] - max(scores["sell"], scores["hold"])
        score = scores["buy"]
    elif scores["sell"] > scores["buy"] and scores["sell"] > scores["hold"]:
        signal = "Sell"
        edge = scores["sell"] - max(scores["buy"], scores["hold"])
        score = scores["sell"]
    else:
        signal = "Hold"
        edge = 0.0
        score = scores["hold"]
    return {
        "scores": scores,
        "familyScores": family_scores,
        "aggregateSignal": signal.lower(),
        "signal": signal,
        "winningScore": round(score, 4),
        "winningEdge": round(edge, 4),
        "activeStrategyCount": active,
        "activeFamilyCount": len(family_scores),
        "abstentionRate": 1 - (active / max(1, sum(1 for output in outputs if output.role == "directional"))),
    }

