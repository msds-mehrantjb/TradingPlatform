def evaluate(snapshot, classification):
    spread = snapshot.context_feeds["quoteFreshness"].get("spreadPercent") or 0
    blocked = spread > 0.03
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.excessive_spread" if blocked else "regime.safety.clear", {"spreadPercent": spread}

