"""Random Forest challenger for Meta-Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase
from backend.app.algorithms.meta_strategy.training import training_core


@dataclass
class RandomForestChallenger(MetaStrategyModelBase):
    model_id: str = "random_forest_challenger"
    role: str = "challenger"
    kind: str = "random_forest"
    tree_count: int = 40
    max_depth: int = 4
    random_seed: int = 17

    def fit(self, rows: list[dict[str, Any]], feature_names: list[str]) -> "RandomForestChallenger":
        self.fitted_payload = _legacy_call(
            "train_random_forest",
            rows,
            feature_names,
            tree_count=self.tree_count,
            max_depth=self.max_depth,
            random_seed=self.random_seed,
        )
        self.fitted_payload["available"] = True
        self.fitted_payload["hyperparameters"] = {"treeCount": self.tree_count, "maxDepth": self.max_depth}
        return self

    def predict_probabilities(self, features: dict[str, float]) -> dict[str, float]:
        return _legacy_call("predict_random_forest_probabilities", self.fitted_payload, features)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = ["RandomForestChallenger"]
