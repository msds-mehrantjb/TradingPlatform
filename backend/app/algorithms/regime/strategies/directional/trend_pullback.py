from backend.app.algorithms.regime.strategies.base import directional_by_scores

evaluate = lambda snapshot, classification: directional_by_scores(snapshot, classification, trend=True)

