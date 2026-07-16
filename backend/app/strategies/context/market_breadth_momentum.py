from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median, pstdev
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import ContextSignal, Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


BreadthSourceMode = Literal["auto", "feed", "proxy"]
BreadthSourceKind = Literal["breadth_feed", "breadth_proxy"]


class MarketBreadthMomentumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "market_breadth_momentum_v1"
    sourceMode: BreadthSourceMode = "auto"
    proxyBasket: tuple[str, ...] = ("XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC")
    returnHorizonMinutes: int = Field(default=5, ge=1, le=60)
    minComponentCoverage: float = Field(default=0.65, gt=0, le=1)
    maxComponentAgeSeconds: int = Field(default=300, ge=0)
    positiveThreshold: float = Field(default=0.58, ge=0, le=1)
    negativeThreshold: float = Field(default=0.42, ge=0, le=1)
    minAbsoluteMedianReturn: float = Field(default=0.0004, ge=0)

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> MarketBreadthMomentumConfig:
        if self.negativeThreshold >= self.positiveThreshold:
            raise ValueError("negativeThreshold must be less than positiveThreshold")
        if not self.proxyBasket:
            raise ValueError("proxyBasket cannot be empty")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class BreadthMetrics:
    dataReady: bool
    sourceKind: BreadthSourceKind
    sourceLabel: str
    percentagePositiveReturn: float | None
    percentageAboveVwap: float | None
    percentageAboveEma20: float | None
    medianComponentReturn: float | None
    upDownVolumeRatio: float | None
    dispersion: float | None
    dataCoverage: float
    componentCount: int
    freshComponentCount: int
    contextEffect: str
    reasonCodes: list[str]


class MarketBreadthMomentumContext:
    registryEntry = resolve_strategy("market_breadth_momentum")

    def __init__(self, config: MarketBreadthMomentumConfig | None = None) -> None:
        self.config = config or MarketBreadthMomentumConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Market Breadth Momentum must be registered as context")

        metrics = self._metrics(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=self._confidence(metrics),
            dataReady=metrics.dataReady,
            explanation=self._explanation(metrics),
            features={
                "breadthSourceKind": metrics.sourceKind,
                "breadthSourceLabel": metrics.sourceLabel,
                "percentagePositiveReturn": metrics.percentagePositiveReturn,
                "percentageAboveVwap": metrics.percentageAboveVwap,
                "percentageAboveEma20": metrics.percentageAboveEma20,
                "medianComponentReturn": metrics.medianComponentReturn,
                "upDownVolumeRatio": metrics.upDownVolumeRatio,
                "dispersion": metrics.dispersion,
                "dataCoverage": metrics.dataCoverage,
                "componentCount": metrics.componentCount,
                "freshComponentCount": metrics.freshComponentCount,
                "proxyBasket": list(self.config.proxyBasket),
                "contextEffect": metrics.contextEffect,
                "reasonCodes": metrics.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _metrics(self, context: StrategyEvaluationContext) -> BreadthMetrics:
        raw_inputs = context.featureSnapshot.rawInputs
        feed = raw_inputs.get("externalBreadthFeed") or {}
        if self.config.sourceMode in {"auto", "feed"} and feed:
            return self._feed_metrics(context, feed)
        if self.config.sourceMode == "feed":
            return _empty_metrics(
                "breadth_feed",
                "Configured external breadth feed",
                ["market_breadth.external_feed_unavailable"],
            )
        return self._proxy_metrics(context)

    def _feed_metrics(self, context: StrategyEvaluationContext, feed: dict[str, Any]) -> BreadthMetrics:
        timestamp = _optional_timestamp(feed.get("sourceTimestamp"))
        anchor = context.featureSnapshot.anchorTimestamp or context.evaluatedAt
        if timestamp is None or abs((anchor - timestamp).total_seconds()) > self.config.maxComponentAgeSeconds:
            return _empty_metrics(
                "breadth_feed",
                "External market breadth feed",
                ["market_breadth.external_feed_stale"],
            )
        metrics = _feed_value_metrics(feed)
        if any(value is None for value in metrics.values()):
            return _empty_metrics(
                "breadth_feed",
                "External market breadth feed",
                ["market_breadth.external_feed_malformed"],
            )
        coverage = _number(feed.get("dataCoverage")) or _number(feed.get("coverage")) or 0.0
        component_count = int(_number(feed.get("componentCount")) or 0)
        if coverage < self.config.minComponentCoverage:
            return _empty_metrics(
                "breadth_feed",
                "External market breadth feed",
                ["market_breadth.external_feed_coverage_insufficient"],
                coverage=coverage,
                component_count=component_count,
            )
        effect = self._context_effect(metrics["percentagePositiveReturn"], metrics["medianComponentReturn"])
        return BreadthMetrics(
            dataReady=True,
            sourceKind="breadth_feed",
            sourceLabel="External market breadth feed",
            percentagePositiveReturn=metrics["percentagePositiveReturn"],
            percentageAboveVwap=metrics["percentageAboveVwap"],
            percentageAboveEma20=metrics["percentageAboveEma20"],
            medianComponentReturn=metrics["medianComponentReturn"],
            upDownVolumeRatio=metrics["upDownVolumeRatio"],
            dispersion=metrics["dispersion"],
            dataCoverage=coverage,
            componentCount=component_count,
            freshComponentCount=component_count,
            contextEffect=effect,
            reasonCodes=[f"market_breadth.{effect}", "market_breadth.source:breadth_feed"],
        )

    def _proxy_metrics(self, context: StrategyEvaluationContext) -> BreadthMetrics:
        feature = context.featureSnapshot.features.get("breadthProxyAverageReturn")
        if not feature or feature.quality != FeatureQuality.READY.value:
            return _empty_metrics(
                "breadth_proxy",
                "ETF/constituent proxy basket, not true market breadth",
                ["market_breadth.proxy_unavailable_or_unready"],
            )
        raw_components = context.featureSnapshot.rawInputs.get("breadthComponentCandles") or {}
        if not raw_components:
            return _empty_metrics(
                "breadth_proxy",
                "ETF/constituent proxy basket, not true market breadth",
                ["market_breadth.proxy_basket_empty"],
            )

        anchor = context.featureSnapshot.anchorTimestamp or context.evaluatedAt
        components: list[ComponentMetrics] = []
        for symbol in self.config.proxyBasket:
            candles = _component_candles(raw_components.get(symbol) or [])
            component = _component_metrics(
                candles,
                anchor=anchor,
                horizon_minutes=self.config.returnHorizonMinutes,
                max_age_seconds=self.config.maxComponentAgeSeconds,
            )
            if component is not None:
                components.append(component)

        coverage = len(components) / len(self.config.proxyBasket)
        if coverage < self.config.minComponentCoverage:
            return _empty_metrics(
                "breadth_proxy",
                "ETF/constituent proxy basket, not true market breadth",
                ["market_breadth.proxy_coverage_insufficient"],
                coverage=coverage,
                component_count=len(self.config.proxyBasket),
                fresh_count=len(components),
            )

        returns = [component.returnValue for component in components]
        up_volume = sum(component.volume for component in components if component.returnValue > 0)
        down_volume = sum(component.volume for component in components if component.returnValue < 0)
        positive = sum(1 for value in returns if value > 0) / len(returns)
        above_vwap = sum(1 for component in components if component.aboveVwap) / len(components)
        above_ema20 = sum(1 for component in components if component.aboveEma20) / len(components)
        median_return = median(returns)
        dispersion = pstdev(returns) if len(returns) > 1 else 0.0
        up_down_ratio = up_volume / down_volume if down_volume > 0 else float("inf") if up_volume > 0 else 0.0
        effect = self._context_effect(positive, median_return)
        return BreadthMetrics(
            dataReady=True,
            sourceKind="breadth_proxy",
            sourceLabel="ETF/constituent proxy basket, not true market breadth",
            percentagePositiveReturn=round(positive, 4),
            percentageAboveVwap=round(above_vwap, 4),
            percentageAboveEma20=round(above_ema20, 4),
            medianComponentReturn=round(median_return, 6),
            upDownVolumeRatio=round(up_down_ratio, 4) if up_down_ratio != float("inf") else 999.0,
            dispersion=round(dispersion, 6),
            dataCoverage=round(coverage, 4),
            componentCount=len(self.config.proxyBasket),
            freshComponentCount=len(components),
            contextEffect=effect,
            reasonCodes=[f"market_breadth.{effect}", "market_breadth.source:breadth_proxy"],
        )

    def _context_effect(self, positive_percent: float | None, median_return: float | None) -> str:
        if positive_percent is None or median_return is None:
            return "neutral"
        if positive_percent >= self.config.positiveThreshold and median_return >= self.config.minAbsoluteMedianReturn:
            return "confirm_or_strengthen_long_candidates"
        if positive_percent <= self.config.negativeThreshold and median_return <= -self.config.minAbsoluteMedianReturn:
            return "confirm_or_strengthen_short_candidates"
        return "neutral"

    def _confidence(self, metrics: BreadthMetrics) -> float:
        if not metrics.dataReady or metrics.percentagePositiveReturn is None:
            return 0.0
        breadth_bias = abs(metrics.percentagePositiveReturn - 0.5) * 2
        coverage_score = metrics.dataCoverage
        dispersion_score = 0.5 if metrics.dispersion is None else max(0.0, 1.0 - min(1.0, metrics.dispersion / 0.02))
        return round(max(0.05, min(1.0, (0.55 * breadth_bias) + (0.3 * coverage_score) + (0.15 * dispersion_score))), 4)

    def _explanation(self, metrics: BreadthMetrics) -> str:
        if not metrics.dataReady:
            return f"HOLD context because market breadth inputs are unavailable: {', '.join(metrics.reasonCodes)}."
        return (
            "HOLD context only: Market Breadth Momentum "
            f"uses {metrics.sourceLabel}; positive {metrics.percentagePositiveReturn:.2%}, "
            f"above VWAP {metrics.percentageAboveVwap:.2%}, effect {metrics.contextEffect}."
        )


@dataclass(frozen=True)
class ComponentMetrics:
    returnValue: float
    aboveVwap: bool
    aboveEma20: bool
    volume: float


def _empty_metrics(
    source_kind: BreadthSourceKind,
    source_label: str,
    reason_codes: list[str],
    *,
    coverage: float = 0.0,
    component_count: int = 0,
    fresh_count: int = 0,
) -> BreadthMetrics:
    return BreadthMetrics(
        dataReady=False,
        sourceKind=source_kind,
        sourceLabel=source_label,
        percentagePositiveReturn=None,
        percentageAboveVwap=None,
        percentageAboveEma20=None,
        medianComponentReturn=None,
        upDownVolumeRatio=None,
        dispersion=None,
        dataCoverage=coverage,
        componentCount=component_count,
        freshComponentCount=fresh_count,
        contextEffect="neutral",
        reasonCodes=reason_codes,
    )


def _feed_value_metrics(feed: dict[str, Any]) -> dict[str, float | None]:
    return {
        "percentagePositiveReturn": _number(feed.get("percentagePositiveReturn")),
        "percentageAboveVwap": _number(feed.get("percentageAboveVwap")),
        "percentageAboveEma20": _number(feed.get("percentageAboveEma20")),
        "medianComponentReturn": _number(feed.get("medianComponentReturn")),
        "upDownVolumeRatio": _number(feed.get("upDownVolumeRatio")),
        "dispersion": _number(feed.get("dispersion")),
    }


def _component_metrics(candles: list[dict[str, Any]], *, anchor: datetime, horizon_minutes: int, max_age_seconds: int) -> ComponentMetrics | None:
    completed = [_row for _row in candles if _timestamp(_row["timestamp"]) <= anchor]
    if len(completed) < max(21, horizon_minutes + 1):
        return None
    latest = max(completed, key=lambda candle: _timestamp(candle["timestamp"]))
    if abs((anchor - _timestamp(latest["timestamp"])).total_seconds()) > max_age_seconds:
        return None
    start_time = anchor.replace(second=0, microsecond=0) - timedelta(minutes=horizon_minutes)
    start = _latest_at_or_before(completed, start_time)
    if start is None or float(start["close"]) <= 0:
        return None
    latest_close = float(latest["close"])
    return_value = (latest_close - float(start["close"])) / float(start["close"])
    vwap = _vwap(completed)
    ema20 = _ema20([float(candle["close"]) for candle in completed])
    return ComponentMetrics(
        returnValue=return_value,
        aboveVwap=vwap is not None and latest_close > vwap,
        aboveEma20=ema20 is not None and latest_close > ema20,
        volume=float(latest["volume"]),
    )


def _latest_at_or_before(candles: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    candidates = [candle for candle in candles if _timestamp(candle["timestamp"]) <= timestamp]
    return max(candidates, key=lambda candle: _timestamp(candle["timestamp"])) if candidates else None


def _component_candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda candle: _timestamp(candle["timestamp"]))


def _vwap(candles: list[dict[str, Any]]) -> float | None:
    volume = sum(float(candle["volume"]) for candle in candles)
    if volume <= 0:
        return None
    return sum(((float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3) * float(candle["volume"]) for candle in candles) / volume


def _ema20(closes: list[float]) -> float | None:
    if len(closes) < 20:
        return None
    alpha = 2 / 21
    ema = sum(closes[:20]) / 20
    for close in closes[20:]:
        ema = (close * alpha) + (ema * (1 - alpha))
    return ema


def _optional_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    return _timestamp(value)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None

