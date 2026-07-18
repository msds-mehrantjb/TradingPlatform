def evaluate(snapshot, classification):
    blocked = bool(snapshot.context_feeds["haltLuldCircuitBreaker"].get("newEntriesBlocked"))
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.halt_luld" if blocked else "regime.safety.clear", {"blocked": blocked}

