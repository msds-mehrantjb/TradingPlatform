def evaluate(snapshot, classification):
    blocked = classification.axes.volatility == "extreme"
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.extreme_volatility" if blocked else "regime.safety.clear", {"volatility": classification.axes.volatility}

