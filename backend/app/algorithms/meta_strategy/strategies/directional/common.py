"""Shared base for Meta-Strategy directional strategies."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.feature_contracts import feature_value, has_required_input, required_input_status
from backend.app.algorithms.meta_strategy.settings import MetaStrategyStrategySettings, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult, hold_result


class DirectionalSnapshotStrategy:
    strategy_id = "directional_snapshot_strategy"
    family = "UNKNOWN"
    required_inputs: tuple[str, ...] = ("candles",)
    supported_sell = True

    def __init__(
        self,
        settings: MetaStrategyStrategySettings | None = None,
        *,
        settings_version: str = "meta_strategy_settings_v1",
        effective_settings_hash: str = "meta_strategy_settings_unresolved",
    ) -> None:
        injected = settings or build_meta_strategy_settings().directional_strategies.get(self.strategy_id, MetaStrategyStrategySettings())
        self.strategy_settings = injected
        self.settings_version = settings_version
        self.effective_settings_hash = effective_settings_hash
        self.minimum_warmup = injected.minimum_warmup
        self.buy_threshold = injected.buy_threshold
        self.sell_threshold = injected.sell_threshold

    def evaluate(self, value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> SnapshotEvaluationResult:
        snapshot = context_market_snapshot(value)
        required_status = self.required_input_status(snapshot)
        evidence = self.evidence(snapshot)
        if not self.strategy_settings.enabled:
            return hold_result(self.strategy_id, "meta_strategy.strategy.disabled_by_settings", family=self.family, settings_version=snapshot.settings_version, effective_settings_hash=snapshot.effective_settings_hash, evidence=evidence, required_input_status=required_status)
        if not snapshot.point_in_time:
            return hold_result(self.strategy_id, "meta_strategy.strategy.snapshot_not_point_in_time", family=self.family, settings_version=snapshot.settings_version, effective_settings_hash=snapshot.effective_settings_hash, evidence=evidence, required_input_status=required_status)
        if not all(required_status.values()):
            return hold_result(self.strategy_id, "meta_strategy.strategy.missing_required_inputs", family=self.family, settings_version=snapshot.settings_version, effective_settings_hash=snapshot.effective_settings_hash, evidence=evidence, required_input_status=required_status)
        if len(snapshot.candles.get("1m", ())) < self.minimum_warmup:
            return hold_result(self.strategy_id, "meta_strategy.strategy.insufficient_warmup", family=self.family, settings_version=snapshot.settings_version, effective_settings_hash=snapshot.effective_settings_hash, evidence=evidence, required_input_status=required_status)
        if not self.regime_allows(snapshot, evidence):
            return hold_result(self.strategy_id, "meta_strategy.strategy.incorrect_regime", family=self.family, settings_version=snapshot.settings_version, effective_settings_hash=snapshot.effective_settings_hash, evidence=evidence, required_input_status=required_status)

        buy_score = float(evidence.get("buyScore") or 0.0)
        sell_score = float(evidence.get("sellScore") or 0.0)
        if buy_score >= self.buy_threshold - 1e-9 and buy_score >= sell_score:
            signal = "BUY"
            confidence = min(1.0, buy_score)
        elif self.supported_sell and sell_score >= self.sell_threshold - 1e-9:
            signal = "SELL"
            confidence = min(1.0, sell_score)
        else:
            signal = "HOLD"
            confidence = 0.0

        return SnapshotEvaluationResult(
            strategy_id=self.strategy_id,
            signal=signal,
            confidence=round(confidence, 6),
            eligible=signal in {"BUY", "SELL"},
            settings_version=snapshot.settings_version,
            effective_settings_hash=snapshot.effective_settings_hash,
            family=self.family,
            evidence=evidence,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.directional.{self.strategy_id}.{signal.lower()}",),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return required_input_status(snapshot, self.required_inputs)

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        return has_required_input(snapshot, name)

    def evidence(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
        return {
            "buyScore": 0.0,
            "sellScore": 0.0,
            "thresholds": {"buy": self.buy_threshold, "sell": self.sell_threshold},
        }

    def regime_allows(self, snapshot: MetaStrategyMarketSnapshot, evidence: dict[str, Any]) -> bool:
        if str(snapshot.economic_event_state.get("state", "")).lower() in {"blocked", "halt"}:
            return False
        if str(snapshot.liquidity.get("level", "")).lower() == "poor":
            return False
        return True


def pct_distance(left: float | None, right: float | None) -> float:
    if left is None or right is None or right == 0:
        return 0.0
    return (left - right) / right


def latest_close(snapshot: MetaStrategyMarketSnapshot) -> float:
    candles = snapshot.candles.get("1m", ())
    if not candles:
        return snapshot.last_price
    return float(candles[-1]["close"])


def previous_close(snapshot: MetaStrategyMarketSnapshot) -> float:
    candles = snapshot.candles.get("1m", ())
    if len(candles) < 2:
        return snapshot.last_price
    return float(candles[-2]["close"])


def candle_high(snapshot: MetaStrategyMarketSnapshot, offset: int = -1) -> float:
    candles = snapshot.candles.get("1m", ())
    return float(candles[offset]["high"]) if candles else snapshot.last_price


def candle_low(snapshot: MetaStrategyMarketSnapshot, offset: int = -1) -> float:
    candles = snapshot.candles.get("1m", ())
    return float(candles[offset]["low"]) if candles else snapshot.last_price


def typed_feature(snapshot: MetaStrategyMarketSnapshot, name: str, default: Any = None) -> Any:
    value = feature_value(snapshot, name)
    return default if value is None else value
