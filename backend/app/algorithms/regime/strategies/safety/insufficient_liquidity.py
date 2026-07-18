def evaluate(snapshot, classification):
    blocked = classification.axes.liquidity == "poor"
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.insufficient_liquidity" if blocked else "regime.safety.clear", {"liquidity": classification.axes.liquidity}

