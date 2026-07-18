"""Backend-owned Regime diagnostics and traces."""

from __future__ import annotations

from backend.app.algorithms.regime.contracts import RegimeDecision, to_dict


def build_regime_decision_trace(decision: RegimeDecision) -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "decisionId": decision.decision_id,
        "classificationTrace": to_dict(decision.raw_classification),
        "transitionTrace": to_dict(decision.confirmed_state),
        "strategyAttribution": [to_dict(output) for output in decision.strategy_outputs],
        "profileAttribution": decision.effective_settings,
        "familyScores": decision.family_scores,
        "tradeBlockers": decision.trade_blockers,
    }

