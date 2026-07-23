"""Signal engine boundary for Weighted Voting strategies."""

from __future__ import annotations

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG, WeightedVotingStrategyCatalogEntry
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedMarketCondition, WeightedSide, WeightedVotingSignal, WeightedWeightState
from backend.app.algorithms.weighted_voting.strategies.bollinger_atr_reversion import BollingerAtrReversionStrategy
from backend.app.algorithms.weighted_voting.strategies.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.algorithms.weighted_voting.strategies.first_pullback_after_open import FirstPullbackAfterOpenStrategy
from backend.app.algorithms.weighted_voting.strategies.liquidity_sweep_reversal import LiquiditySweepReversalStrategy


WEIGHTED_VOTING_SIGNAL_ENGINE_VERSION = "weighted_voting_signal_engine_v1"


WEIGHTED_VOTING_STRATEGY_CLASSES = (
    FirstPullbackAfterOpenStrategy,
    FailedBreakoutReversalStrategy,
    LiquiditySweepReversalStrategy,
    BollingerAtrReversionStrategy,
)

WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID = {
    entry.strategy_id: strategy_class
    for entry, strategy_class in zip(WEIGHTED_VOTING_STRATEGY_CATALOG, WEIGHTED_VOTING_STRATEGY_CLASSES)
}


def evaluate_signals(
    snapshot: WeightedVotingMarketSnapshot,
    config: WeightedVotingConfig | None = None,
    active_weight_state: WeightedWeightState | None = None,
    market_condition: WeightedMarketCondition | None = None,
) -> list[WeightedVotingSignal]:
    active_config = config or WeightedVotingConfig()
    signals: list[WeightedVotingSignal] = []
    for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
        strategy_class = WEIGHTED_VOTING_STRATEGY_CLASS_BY_ID[entry.strategy_id]
        raw_signal = strategy_class(active_config).evaluate(snapshot)
        signals.append(_standardize_strategy_signal(raw_signal, entry, snapshot, active_weight_state, market_condition))
    return signals


def waiting_signals(snapshot: WeightedVotingMarketSnapshot) -> list[WeightedVotingSignal]:
    return [
        _standardize_strategy_signal(
            WeightedVotingSignal(
                strategy_id=entry.strategy_id,
                strategy_name=entry.name,
                strategy_version=entry.version,
                family=entry.family,
                signal=WeightedSide.HOLD,
                p_buy=0.0,
                p_sell=0.0,
                p_hold=1.0,
                expected_return=0.0,
                expected_return_after_costs=0.0,
                strength=0.0,
                final_weight=0.0,
                eligible=False,
                data_ready=False,
                data_timestamp=snapshot.data_timestamp,
                reason_codes=("weighted_voting.strategy_not_implemented",),
                explanation=f"{entry.name} is waiting for backend implementation at {snapshot.data_timestamp.isoformat()}.",
            ),
            entry,
            snapshot,
            None,
            None,
        )
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG
    ]


def _standardize_strategy_signal(
    signal: WeightedVotingSignal,
    entry: WeightedVotingStrategyCatalogEntry,
    snapshot: WeightedVotingMarketSnapshot,
    active_weight_state: WeightedWeightState | None,
    market_condition: WeightedMarketCondition | None,
) -> WeightedVotingSignal:
    if signal.strategy_id != entry.strategy_id:
        raise ValueError(f"strategy {entry.strategy_id} returned signal for {signal.strategy_id}")
    base_weight = entry.baseline_weight
    active_weight = _active_weight(entry, active_weight_state)
    market_condition_fit = _market_condition_fit(entry, market_condition)
    data_ready = _data_ready(signal, entry, snapshot)
    eligible = bool(entry.enabled and signal.eligible and data_ready and _direction_allowed(signal, entry))
    active = bool(eligible and active_weight > 0 and market_condition_fit > 0)
    final_weight = min(entry.maximum_weight, active_weight * market_condition_fit) if active else 0.0
    reasons = list(signal.reason_codes)
    if not entry.enabled:
        reasons.append("weighted_voting.signal_engine.strategy_disabled")
    if not data_ready:
        reasons.append("weighted_voting.signal_engine.data_not_ready")
    if signal.eligible and not _direction_allowed(signal, entry):
        reasons.append("weighted_voting.signal_engine.direction_not_allowed")
    if market_condition_fit <= 0:
        reasons.append("weighted_voting.signal_engine.market_condition_blocks_strategy")
    if active:
        reasons.append("weighted_voting.signal_engine.strategy_active")
    return signal.model_copy(
        update={
            "strategy_name": entry.name,
            "strategy_version": entry.version,
            "family": entry.family,
            "direction": signal.signal,
            "buy_probability": signal.p_buy,
            "sell_probability": signal.p_sell,
            "hold_probability": signal.p_hold,
            "confidence": signal.directional_confidence,
            "expected_return_before_costs": signal.expected_return,
            "base_weight": base_weight,
            "active_weight": active_weight,
            "final_weight": round(final_weight, 10),
            "eligible": eligible,
            "active": active,
            "data_ready": data_ready,
            "market_condition_fit": market_condition_fit,
            "reason_codes": tuple(dict.fromkeys(reasons)),
            "feature_snapshot": _feature_snapshot(signal, entry, snapshot, market_condition),
        }
    )


def _active_weight(entry: WeightedVotingStrategyCatalogEntry, active_weight_state: WeightedWeightState | None) -> float:
    if active_weight_state is None:
        return entry.baseline_weight
    return max(0.0, min(entry.maximum_weight, active_weight_state.strategy_weights.get(entry.strategy_id, entry.baseline_weight)))


def _market_condition_fit(entry: WeightedVotingStrategyCatalogEntry, market_condition: WeightedMarketCondition | None) -> float:
    if market_condition is None:
        return 1.0
    return round(max(0.0, min(1.5, market_condition.regime_fit_multipliers.get(_enum_value(entry.family), 1.0))), 6)


def _data_ready(signal: WeightedVotingSignal, entry: WeightedVotingStrategyCatalogEntry, snapshot: WeightedVotingMarketSnapshot) -> bool:
    completed = sum(1 for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp)
    return bool(signal.data_ready and completed >= entry.minimum_warmup)


def _direction_allowed(signal: WeightedVotingSignal, entry: WeightedVotingStrategyCatalogEntry) -> bool:
    if _enum_value(signal.signal) == WeightedSide.BUY.value:
        return entry.long_allowed
    if _enum_value(signal.signal) == WeightedSide.SELL.value:
        return entry.short_allowed
    return True


def _feature_snapshot(
    signal: WeightedVotingSignal,
    entry: WeightedVotingStrategyCatalogEntry,
    snapshot: WeightedVotingMarketSnapshot,
    market_condition: WeightedMarketCondition | None,
) -> dict[str, float | str | bool | None]:
    latest = snapshot.one_minute_candles[-1] if snapshot.one_minute_candles else None
    features: dict[str, float | str | bool | None] = {
        "signal_engine_version": WEIGHTED_VOTING_SIGNAL_ENGINE_VERSION,
        "strategy_id": entry.strategy_id,
        "strategy_family": _enum_value(entry.family),
        "completed_one_minute_candles": float(sum(1 for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp)),
        "required_one_minute_candles": float(entry.minimum_warmup),
        "data_freshness_seconds": snapshot.data_freshness_seconds,
        "spread": snapshot.spread,
        "latest_close": latest.close if latest is not None else None,
        "raw_signal": _enum_value(signal.signal),
        "raw_confidence": signal.directional_confidence,
    }
    if market_condition is not None:
        features.update(
            {
                "market_quality": _enum_value(market_condition.market_quality),
                "trend_direction": _enum_value(market_condition.trend_direction),
                "volatility_level": _enum_value(market_condition.volatility_level),
                "range_condition": _enum_value(market_condition.range_condition),
            }
        )
    return features


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))
