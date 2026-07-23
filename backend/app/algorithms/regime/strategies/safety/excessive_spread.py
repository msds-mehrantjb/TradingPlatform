def evaluate(snapshot, classification):
    liquidity = classification.evidence.get("liquidityEvidence", {})
    spread_bps = liquidity.get("spreadBps")
    if spread_bps is None:
        spread_percent = snapshot.context_feeds["quoteFreshness"].get("spreadPercent")
        spread_bps = spread_percent * 10000 if spread_percent is not None else None
    blocked = bool(liquidity.get("blockNewEntries")) if spread_bps is None else float(spread_bps) > 30
    reason = "regime.safety.excessive_spread" if blocked else "regime.safety.clear"
    return "Hold", 1.0 if blocked else 0.5, reason, {"spreadBps": spread_bps, "liquidity": liquidity.get("axis")}
