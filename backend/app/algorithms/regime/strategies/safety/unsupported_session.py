def evaluate(snapshot, classification):
    blocked = classification.axes.session == "outside_regular"
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.unsupported_session" if blocked else "regime.safety.clear", {"session": classification.axes.session}

