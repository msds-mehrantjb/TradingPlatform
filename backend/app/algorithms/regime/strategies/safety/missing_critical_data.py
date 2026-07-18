def evaluate(snapshot, classification):
    blocked = bool(classification.missing_inputs)
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.missing_data" if blocked else "regime.safety.clear", {"missingInputs": classification.missing_inputs}

