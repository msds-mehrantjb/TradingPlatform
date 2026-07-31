"""Backend-owned Regime ML shadow predictor."""

from __future__ import annotations


def evaluate_regime_ml_shadow(decision: dict, artifact: dict | None = None, mode: str = "shadow") -> dict:
    if mode not in {"shadow", "off", "confirm_only"}:
        mode = "shadow"
    applied_effect = "confirm_only" if mode == "confirm_only" else "shadow_only" if mode == "shadow" else "none"
    return {
        "mode": mode,
        "storage": "regime_ml_predictions",
        "defaultMode": "shadow",
        "maximumAutomaticPromotionMode": "confirm_only",
        "appliedEffect": applied_effect,
        "mayChangeDeterministicDecision": False,
        "mayCreateDirection": False,
        "mayReverseSignal": False,
        "mayIncreaseSize": False,
        "mayLoosenGate": False,
        "mayCreateOrder": False,
        "mayBlockTrades": False,
        "orderAuthority": "none",
        "sizingAuthority": "none",
        "artifactTrusted": bool(artifact and artifact.get("trusted")),
        "baselineDecisionId": decision.get("decision_id") or decision.get("decisionId"),
        "reasonCodes": ("regime.ml.shadow_only_no_order_authority",),
    }
