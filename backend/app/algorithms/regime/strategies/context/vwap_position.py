def evaluate(snapshot, classification):
    close = snapshot.latest.close
    vwap = classification.features.get("vwap") or close
    return "Hold", 0.6, "regime.context.vwap_position", {"aboveVwap": close > vwap, "distance": (close - vwap) / max(vwap, 0.01)}

