"""Backend-owned Regime ML shadow predictor."""

from __future__ import annotations


def evaluate_regime_ml_shadow(decision: dict, artifact: dict | None = None, mode: str = "shadow") -> dict:
    if mode not in {"shadow", "off"}:
        mode = "shadow"
    return {
        "mode": mode,
        "appliedEffect": "shadow_only" if mode == "shadow" else "none",
        "mayChangeDeterministicDecision": False,
        "mayIncreaseSize": False,
        "mayBlockTrades": False,
        "artifactTrusted": bool(artifact and artifact.get("trusted")),
        "baselineDecisionId": decision.get("decision_id") or decision.get("decisionId"),
    }

