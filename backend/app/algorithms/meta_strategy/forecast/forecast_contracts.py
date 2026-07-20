"""Contracts for Meta-Strategy-owned out-of-sample forecast features."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
)


FORECAST_FEATURE_VERSION = "market_forecast_oos_feature_v1"


class MetaStrategyForecastContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True, allow_inf_nan=False)


class ForecastFallbackFeature(MetaStrategyForecastContract):
    algorithmId: Literal["meta_strategy"] = ALGORITHM_ID
    algorithmVersion: Literal["meta_strategy_algorithm_v1"] = META_STRATEGY_ALGORITHM_VERSION
    configurationVersion: Literal["meta_strategy_config_v1"] = META_STRATEGY_CONFIGURATION_VERSION
    featureSchemaVersion: Literal["meta_strategy_feature_schema_v1"] = META_STRATEGY_FEATURE_SCHEMA_VERSION
    featureVersion: Literal["market_forecast_oos_feature_v1"] = FORECAST_FEATURE_VERSION
    status: Literal["missing_approved_forecast_model"] = "missing_approved_forecast_model"
    probabilityBuySuccess: float | None = None
    probabilitySellSuccess: float | None = None
    probabilityTimeout: float | None = None
    trainingWindowStartUtc: datetime | None = None
    trainingWindowEndUtc: datetime | None = None
    validationWindowStartUtc: datetime | None = None
    validationWindowEndUtc: datetime | None = None
    artifactId: str | None = None
    reasonCodes: tuple[str, ...] = ("forecast_model.missing_approved_artifact",)
    explanation: str = "No approved market-forecast artifact was available before the decision timestamp."


class OutOfSampleForecastFeature(MetaStrategyForecastContract):
    algorithmId: Literal["meta_strategy"] = ALGORITHM_ID
    algorithmVersion: Literal["meta_strategy_algorithm_v1"] = META_STRATEGY_ALGORITHM_VERSION
    configurationVersion: Literal["meta_strategy_config_v1"] = META_STRATEGY_CONFIGURATION_VERSION
    featureSchemaVersion: Literal["meta_strategy_feature_schema_v1"] = META_STRATEGY_FEATURE_SCHEMA_VERSION
    featureVersion: Literal["market_forecast_oos_feature_v1"] = FORECAST_FEATURE_VERSION
    status: Literal["out_of_sample", "live_approved_artifact"]
    rowId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decisionTimestampUtc: datetime
    trainingWindowStartUtc: datetime
    trainingWindowEndUtc: datetime
    validationWindowStartUtc: datetime | None = None
    validationWindowEndUtc: datetime | None = None
    fold: int | None = Field(default=None, ge=1)
    artifactId: str = Field(min_length=1)
    modelKind: str = Field(default="logistic_oos_forecast", min_length=1)
    probabilityBuySuccess: float = Field(ge=0, le=1)
    probabilitySellSuccess: float = Field(ge=0, le=1)
    probabilityTimeout: float = Field(ge=0, le=1)
    modelDisagreement: float | None = Field(default=None, ge=0)
    reasonCodes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @field_validator(
        "decisionTimestampUtc",
        "trainingWindowStartUtc",
        "trainingWindowEndUtc",
        "validationWindowStartUtc",
        "validationWindowEndUtc",
    )
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("forecast timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def training_must_end_before_prediction(self) -> OutOfSampleForecastFeature:
        if self.trainingWindowEndUtc >= self.decisionTimestampUtc:
            raise ValueError("forecast training window must end before prediction time")
        if self.trainingWindowStartUtc > self.trainingWindowEndUtc:
            raise ValueError("forecast training window start cannot be after training end")
        if self.validationWindowStartUtc and self.validationWindowStartUtc < self.trainingWindowEndUtc:
            raise ValueError("forecast validation window cannot overlap training window")
        if self.validationWindowEndUtc and self.validationWindowStartUtc and self.validationWindowEndUtc < self.validationWindowStartUtc:
            raise ValueError("forecast validation window end cannot precede validation start")
        return self


__all__ = [
    "FORECAST_FEATURE_VERSION",
    "ForecastFallbackFeature",
    "MetaStrategyForecastContract",
    "OutOfSampleForecastFeature",
]
