def evaluate(snapshot, classification):
    close = snapshot.latest.close
    vwap = classification.features.get("vwap") or close
    distance = (close - vwap) / max(vwap, 0.01)
    if distance <= -0.004:
        return "Buy", 0.64, "regime.strategy.vwap_oversold", {"distanceFromVwap": distance}
    if distance >= 0.004:
        return "Sell", 0.64, "regime.strategy.vwap_overbought", {"distanceFromVwap": distance}
    return "Hold", 0.42, "regime.strategy.near_vwap", {"distanceFromVwap": distance}

