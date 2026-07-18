def evaluate(snapshot, classification):
    edge = abs((classification.features.get("bullScore") or 0) - (classification.features.get("bearScore") or 0))
    return "Hold", min(0.85, 0.45 + edge * 0.08), "regime.confirmation.trend_strength", {"scoreEdge": edge}

