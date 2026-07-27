def evaluate(snapshot, classification):
    liquidity = classification.evidence.get("liquidityEvidence", {})
    if classification.raw_regime == "pre_classification":
        quote = snapshot.context_feeds.get("quoteFreshness", {})
        missing_quote = any(quote.get(field) is None for field in ("bid", "ask", "spreadBps"))
        stale_or_unknown = quote.get("status") in {"stale", "unknown", None}
        blocked = bool(stale_or_unknown or missing_quote)
        evidence = {"quoteFreshness": quote.get("status"), "preClassification": True, "missingQuote": missing_quote}
    else:
        blocked = classification.axes.liquidity in {"poor", "unknown"} or bool(liquidity.get("blockNewEntries"))
        evidence = {"liquidity": classification.axes.liquidity, "blockNewEntries": liquidity.get("blockNewEntries")}
    reason = "regime.safety.insufficient_liquidity" if blocked else "regime.safety.clear"
    return "Hold", 1.0 if blocked else 0.5, reason, evidence
