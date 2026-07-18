def evaluate(snapshot, classification):
    blocked = classification.raw_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}
    return "Hold", 1.0 if blocked else 0.5, "regime.safety.cash_avoid" if blocked else "regime.safety.clear", {"blocked": blocked}

