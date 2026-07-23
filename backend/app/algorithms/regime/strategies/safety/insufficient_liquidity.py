def evaluate(snapshot, classification):
    liquidity = classification.evidence.get("liquidityEvidence", {})
    blocked = classification.axes.liquidity in {"poor", "unknown"} or bool(liquidity.get("blockNewEntries"))
    reason = "regime.safety.insufficient_liquidity" if blocked else "regime.safety.clear"
    return "Hold", 1.0 if blocked else 0.5, reason, {"liquidity": classification.axes.liquidity, "blockNewEntries": liquidity.get("blockNewEntries")}
