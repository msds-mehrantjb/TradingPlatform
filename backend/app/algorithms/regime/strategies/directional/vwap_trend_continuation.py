def evaluate(snapshot, classification):
    close = snapshot.latest.close
    vwap = classification.features.get("vwap") or close
    if close > vwap:
        return "Buy", 0.62, "regime.strategy.above_vwap", {"close": close, "vwap": vwap}
    if close < vwap:
        return "Sell", 0.62, "regime.strategy.below_vwap", {"close": close, "vwap": vwap}
    return "Hold", 0.4, "regime.strategy.at_vwap", {"close": close, "vwap": vwap}

