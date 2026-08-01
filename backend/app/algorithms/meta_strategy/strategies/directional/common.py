"""Shared base for Meta-Strategy directional strategies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.evaluation_context import MetaStrategyEvaluationContext, context_market_snapshot
from backend.app.algorithms.meta_strategy.feature_contracts import feature_value, has_required_input, required_input_status
from backend.app.algorithms.meta_strategy.settings import MetaStrategyStrategySettings, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult, hold_result


class DirectionalSnapshotStrategy:
    strategy_id = "directional_snapshot_strategy"
    family = "UNKNOWN"
    version = "meta_strategy_strategy_v1"
    required_inputs: tuple[str, ...] = ("candles",)
    supported_sell = True
    supported_directions: tuple[str, ...] = ("BUY", "SELL", "HOLD")

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
        evidence = self._complete_evidence(snapshot, self.evidence(snapshot), required_status)
        if not self.strategy_settings.enabled:
            return self._hold(snapshot, "meta_strategy.strategy.disabled_by_settings", evidence, required_status)
        if not snapshot.point_in_time:
            return self._hold(snapshot, "meta_strategy.strategy.snapshot_not_point_in_time", evidence, required_status)
        if not all(required_status.values()):
            return self._hold(snapshot, "meta_strategy.strategy.missing_required_inputs", evidence, required_status)
        if len(snapshot.candles.get("1m", ())) < self.minimum_warmup:
            return self._hold(snapshot, "meta_strategy.strategy.insufficient_warmup", evidence, required_status)
        if not self.regime_allows(snapshot, evidence):
            return self._hold(snapshot, "meta_strategy.strategy.incorrect_regime", evidence, required_status)

        buy_score = float(evidence.get("buyScore") or 0.0)
        sell_score = float(evidence.get("sellScore") or 0.0)
        blocked = tuple(str(code) for code in evidence.get("blockReasonCodes", ()) if code)
        if blocked:
            return self._hold(snapshot, blocked[0], evidence, required_status)
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
            strategy_version=self.version,
            required_inputs=self.required_inputs,
            minimum_warmup=self.minimum_warmup,
            supported_directions=self.supported_directions,
            entry_reference=_optional_float(evidence.get("entryReference")),
            invalidation_reference=_optional_float(evidence.get("invalidationReference")),
            suggested_stop_reference=_optional_float(evidence.get("suggestedStopReference")),
            settings_version=snapshot.settings_version,
            effective_settings_hash=snapshot.effective_settings_hash,
            family=self.family,
            evidence=evidence,
            required_input_status=required_status,
            reason_codes=(f"meta_strategy.directional.{self.strategy_id}.{signal.lower()}",),
        )

    def _hold(
        self,
        snapshot: MetaStrategyMarketSnapshot,
        reason_code: str,
        evidence: dict[str, Any],
        required_status: dict[str, bool],
    ) -> SnapshotEvaluationResult:
        result = hold_result(
            self.strategy_id,
            reason_code,
            family=self.family,
            settings_version=snapshot.settings_version,
            effective_settings_hash=snapshot.effective_settings_hash,
            evidence=evidence,
            required_input_status=required_status,
        )
        return SnapshotEvaluationResult(
            strategy_id=result.strategy_id,
            signal=result.signal,
            confidence=result.confidence,
            eligible=result.eligible,
            strategy_version=self.version,
            required_inputs=self.required_inputs,
            minimum_warmup=self.minimum_warmup,
            supported_directions=self.supported_directions,
            entry_reference=_optional_float(evidence.get("entryReference")),
            invalidation_reference=_optional_float(evidence.get("invalidationReference")),
            suggested_stop_reference=_optional_float(evidence.get("suggestedStopReference")),
            settings_version=result.settings_version,
            effective_settings_hash=result.effective_settings_hash,
            family=result.family,
            evidence=result.evidence,
            required_input_status=result.required_input_status,
            reason_codes=result.reason_codes,
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

    def _complete_evidence(
        self,
        snapshot: MetaStrategyMarketSnapshot,
        evidence: dict[str, Any],
        required_status: dict[str, bool],
    ) -> dict[str, Any]:
        complete = dict(evidence)
        side = "BUY" if float(complete.get("buyScore") or 0.0) >= float(complete.get("sellScore") or 0.0) else "SELL"
        entry = _optional_float(complete.get("entryReference")) or snapshot.last_price
        invalidation = _optional_float(complete.get("invalidationReference")) or structural_invalidation(snapshot, side)
        stop = _optional_float(complete.get("suggestedStopReference")) or invalidation
        complete.update(
            {
                "strategyId": self.strategy_id,
                "strategyVersion": self.version,
                "family": self.family,
                "requiredInputs": self.required_inputs,
                "requiredInputStatus": required_status,
                "minimumWarmup": self.minimum_warmup,
                "supportedDirections": self.supported_directions,
                "signalDomain": ("BUY", "SELL", "HOLD"),
                "entryReference": round(float(entry), 6) if entry is not None else None,
                "invalidationReference": round(float(invalidation), 6) if invalidation is not None else None,
                "suggestedStopReference": round(float(stop), 6) if stop is not None else None,
                "completeEvidencePayload": True,
                "submitsOrdersDirectly": False,
                "snapshotTimestamp": snapshot.timestamp.isoformat(),
            }
        )
        return complete

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


def candles(snapshot: MetaStrategyMarketSnapshot, timeframe: str = "1m") -> tuple[dict[str, Any], ...]:
    return tuple(snapshot.candles.get(timeframe, ()))


def ma(snapshot: MetaStrategyMarketSnapshot, timeframe: str, name: str) -> float | None:
    value = (snapshot.moving_averages.get(timeframe) or {}).get(name)
    return float(value) if value is not None else None


def slope(snapshot: MetaStrategyMarketSnapshot, timeframe: str = "1m", lookback: int = 5) -> float:
    rows = candles(snapshot, timeframe)
    if len(rows) <= lookback:
        return 0.0
    prior = float(rows[-lookback - 1]["close"])
    latest = float(rows[-1]["close"])
    return pct_distance(latest, prior)


def last_candle(snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
    rows = candles(snapshot, "1m")
    return rows[-1] if rows else {"open": snapshot.last_price, "high": snapshot.last_price, "low": snapshot.last_price, "close": snapshot.last_price, "volume": snapshot.volume}


def recent_high(snapshot: MetaStrategyMarketSnapshot, lookback: int = 20) -> float:
    rows = candles(snapshot, "1m")[-lookback:]
    return max(float(row["high"]) for row in rows) if rows else snapshot.last_price


def recent_low(snapshot: MetaStrategyMarketSnapshot, lookback: int = 20) -> float:
    rows = candles(snapshot, "1m")[-lookback:]
    return min(float(row["low"]) for row in rows) if rows else snapshot.last_price


def structural_invalidation(snapshot: MetaStrategyMarketSnapshot, side: str) -> float:
    atr = float(snapshot.atr.get("1m") or 0.0)
    if side == "BUY":
        return min(candle_low(snapshot), recent_low(snapshot, 10)) - max(0.01, atr * 0.1)
    if side == "SELL":
        return max(candle_high(snapshot), recent_high(snapshot, 10)) + max(0.01, atr * 0.1)
    return snapshot.last_price


def reward_to_risk(snapshot: MetaStrategyMarketSnapshot, side: str, *, entry: float | None = None, stop: float | None = None, target: float | None = None) -> float:
    entry_price = entry or snapshot.last_price
    stop_price = stop or structural_invalidation(snapshot, side)
    if target is None:
        atr = float(snapshot.atr.get("1m") or 0.0)
        target = entry_price + (atr * 1.5 if side == "BUY" else -atr * 1.5)
    risk = abs(entry_price - stop_price)
    reward = abs(target - entry_price)
    return reward / risk if risk > 0 else 0.0


def crossed_above(snapshot: MetaStrategyMarketSnapshot, level: float) -> bool:
    rows = candles(snapshot, "1m")
    if len(rows) < 2:
        return snapshot.last_price > level
    return float(rows[-2]["close"]) <= level < float(rows[-1]["close"])


def crossed_below(snapshot: MetaStrategyMarketSnapshot, level: float) -> bool:
    rows = candles(snapshot, "1m")
    if len(rows) < 2:
        return snapshot.last_price < level
    return float(rows[-2]["close"]) >= level > float(rows[-1]["close"])


def close_outside(snapshot: MetaStrategyMarketSnapshot, *, high: float, low: float, buffer: float) -> tuple[bool, bool]:
    close = latest_close(snapshot)
    return close > high + buffer, close < low - buffer


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
