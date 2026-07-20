"""Optional booster challengers for Meta-Strategy."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase


@dataclass
class OptionalBoosterChallenger(MetaStrategyModelBase):
    dependency_name: str = ""
    model_id: str = "optional_booster_challenger"
    role: str = "challenger"
    kind: str = "optional_booster"

    def fit(self, rows: list[dict[str, Any]], feature_names: list[str]) -> "OptionalBoosterChallenger":
        try:
            importlib.import_module(self.dependency_name)
        except Exception as exc:
            self.fitted_payload = {"available": False, "reason": f"{self.dependency_name} import failed: {exc}"}
            return self
        self.fitted_payload = {
            "available": False,
            "reason": f"{self.dependency_name} training is optional and remains disabled in the Meta-Strategy split layer",
            "rows": len(rows),
            "featureNames": feature_names,
        }
        return self

    def predict_probabilities(self, features: dict[str, float]) -> dict[str, float]:
        return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}


def XGBoostChallenger() -> OptionalBoosterChallenger:
    return OptionalBoosterChallenger(
        model_id="xgboost_challenger",
        kind="xgboost",
        dependency_name="xgboost",
    )


def LightGBMChallenger() -> OptionalBoosterChallenger:
    return OptionalBoosterChallenger(
        model_id="lightgbm_challenger",
        kind="lightgbm",
        dependency_name="lightgbm",
    )


def train_optional_challenger_models(rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, OptionalBoosterChallenger]:
    challengers = [XGBoostChallenger(), LightGBMChallenger()]
    return {challenger.model_id: challenger.fit(rows, feature_names) for challenger in challengers}


__all__ = [
    "LightGBMChallenger",
    "OptionalBoosterChallenger",
    "XGBoostChallenger",
    "train_optional_challenger_models",
]
