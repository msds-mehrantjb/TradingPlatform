"""Shared base for Meta-Strategy directional strategies."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult, hold_result


class DirectionalSnapshotStrategy:
    strategy_id = "directional_snapshot_strategy"
    family = "UNKNOWN"
    minimum_warmup = 30
    required_inputs: tuple[str, ...] = ("candles",)
    buy_threshold = 0.0
    sell_threshold = 0.0
    supported_sell = True

    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> SnapshotEvaluationResult:
        required_status = self.required_input_status(snapshot)
        evidence = self.evidence(snapshot)
        if not snapshot.point_in_time:
            return hold_result(self.strategy_id, "meta_strategy.strategy.snapshot_not_point_in_time", family=self.family, evidence=evidence, required_input_status=required_status)
        if not all(required_status.values()):
            return hold_result(self.strategy_id, "meta_strategy.strategy.missing_required_inputs", family=self.family, evidence=evidence, required_input_status=required_status)
        if len(snapshot.candles.get("1m", ())) < self.minimum_warmup:
            return hold_result(self.strategy_id, "meta_strategy.strategy.insufficient_warmup", family=self.family, evidence=evidence, required_input_status=required_status)
        if not self.regime_allows(snapshot, evidence):
            return hold_result(self.strategy_id, "meta_strategy.strategy.incorrect_regime", family=self.family, evidence=evidence, required_input_status=required_status)

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
            family=self.family,
            evidence=evidence,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.directional.{self.strategy_id}.{signal.lower()}",),
        )

    def required_input_status(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, bool]:
        return {name: self.has_input(snapshot, name) for name in self.required_inputs}

    def has_input(self, snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
        if name == "candles":
            return bool(snapshot.candles.get("1m"))
        if name == "moving_averages":
            return bool(snapshot.moving_averages.get("1m"))
        if name == "vwap":
            return snapshot.vwap is not None
        if name == "atr":
            return snapshot.atr.get("1m") is not None
        if name == "adx":
            return snapshot.adx.get("1m") is not None
        if name == "rsi":
            return snapshot.rsi.get("1m") is not None
        if name == "macd":
            return snapshot.macd.get("1m") is not None
        if name == "bollinger_bands":
            return snapshot.bollinger_bands.get("1m") is not None
        if name == "relative_volume":
            return snapshot.relative_volume.get("1m") is not None
        if name == "volume":
            return snapshot.volume > 0
        if name == "spread":
            return bool(snapshot.spread)
        if name == "liquidity":
            return bool(snapshot.liquidity)
        if name == "session_phase":
            return bool(snapshot.session_phase)
        if name == "gap_state":
            return bool(snapshot.gap_state)
        if name == "qqq_iwm_context":
            return bool(snapshot.qqq_iwm_context)
        if name == "economic_event_state":
            return bool(snapshot.economic_event_state)
        return snapshot.features.get(name) is not None

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
