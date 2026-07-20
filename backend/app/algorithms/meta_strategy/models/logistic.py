"""Logistic-regression champion baseline for Meta-Strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase
from backend.app.algorithms.meta_strategy.training import training_core


@dataclass
class LogisticRegressionChampion(MetaStrategyModelBase):
    model_id: str = "logistic_regression_champion"
    role: str = "champion"
    kind: str = "softmax_logistic_regression"
    hyperparameters: dict[str, Any] | None = None

    def fit(self, rows: list[dict[str, Any]], feature_names: list[str]) -> "LogisticRegressionChampion":
        params = {"epochs": 70, "learningRate": 0.035, "l2": 0.0005, **(self.hyperparameters or {})}
        scaler = _legacy_call("feature_scaler", rows, feature_names)
        self.fitted_payload = _legacy_call("train_softmax_logistic", rows, feature_names, scaler, **params)
        self.fitted_payload["available"] = True
        self.fitted_payload["hyperparameters"] = params
        return self

    def predict_probabilities(self, features: dict[str, float]) -> dict[str, float]:
        return _legacy_call("predict_softmax_logistic_probabilities", self.fitted_payload, features)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = ["LogisticRegressionChampion"]
