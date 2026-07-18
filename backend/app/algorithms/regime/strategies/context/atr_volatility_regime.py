def evaluate(snapshot, classification):
    return "Hold", 0.6, "regime.context.atr_volatility", {"atrPercent": classification.features.get("atrPercent")}

