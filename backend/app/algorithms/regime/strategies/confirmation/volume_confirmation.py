def evaluate(snapshot, classification):
    rel_volume = classification.features.get("relativeVolume") or 0
    return "Hold", min(1.0, max(0.0, rel_volume / 2)), "regime.confirmation.volume", {"relativeVolume": rel_volume}

