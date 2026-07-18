def evaluate(snapshot, classification):
    macd = classification.features.get("macdHistogram")
    if macd is None:
        return "Hold", 0.4, "regime.strategy.macd_unavailable", {"macdHistogram": None}
    if macd > 0:
        return "Buy", min(0.8, 0.55 + abs(macd)), "regime.strategy.macd_positive", {"macdHistogram": macd}
    if macd < 0:
        return "Sell", min(0.8, 0.55 + abs(macd)), "regime.strategy.macd_negative", {"macdHistogram": macd}
    return "Hold", 0.4, "regime.strategy.macd_flat", {"macdHistogram": macd}

