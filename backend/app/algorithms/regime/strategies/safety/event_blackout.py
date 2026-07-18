def evaluate(snapshot, classification):
    blocked = classification.axes.event_risk == "blackout"
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.event_blackout" if blocked else "regime.safety.clear", {"eventRisk": classification.axes.event_risk}

