def evaluate(snapshot, classification):
    status = snapshot.context_feeds["quoteFreshness"].get("status")
    return "Hold", 1.0 if status == "stale" else 0.5, "regime.safety.stale_data" if status == "stale" else "regime.safety.clear", {"quoteFreshness": status}

