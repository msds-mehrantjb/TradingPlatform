def evaluate(snapshot, classification):
    state = snapshot.context_feeds["haltLuldCircuitBreaker"].get("circuitBreakerState")
    blocked = state == "active"
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.circuit_breaker" if blocked else "regime.safety.clear", {"circuitBreakerState": state}

