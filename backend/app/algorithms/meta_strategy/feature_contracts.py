"""Validated required-input contracts for Meta-Strategy strategies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyMarketSnapshot


@dataclass(frozen=True)
class MetaStrategyInputProducer:
    input_id: str
    section: str
    producer_id: str
    critical: bool = True


META_STRATEGY_INPUT_PRODUCERS: tuple[MetaStrategyInputProducer, ...] = (
    MetaStrategyInputProducer("candles", "market_snapshot.candles", "market_snapshot_builder"),
    MetaStrategyInputProducer("moving_averages", "market_snapshot.moving_averages", "market_snapshot_builder"),
    MetaStrategyInputProducer("moving_average_slope", "market_snapshot.features.movingAverageSlope", "market_snapshot_builder"),
    MetaStrategyInputProducer("market_structure", "market_snapshot.features.marketStructureState", "market_snapshot_builder"),
    MetaStrategyInputProducer("reward_to_risk", "strategy.evidence.rewardToRisk", "directional_strategy"),
    MetaStrategyInputProducer("vwap", "market_snapshot.vwap", "market_snapshot_builder"),
    MetaStrategyInputProducer("vwap_relationship", "market_snapshot.features.vwapRelationship", "market_snapshot_builder"),
    MetaStrategyInputProducer("vwap_slope", "market_snapshot.features.vwapSlope", "market_snapshot_builder"),
    MetaStrategyInputProducer("atr", "market_snapshot.atr", "market_snapshot_builder"),
    MetaStrategyInputProducer("adx", "market_snapshot.adx", "market_snapshot_builder"),
    MetaStrategyInputProducer("rsi", "market_snapshot.rsi", "market_snapshot_builder"),
    MetaStrategyInputProducer("macd", "market_snapshot.macd", "market_snapshot_builder"),
    MetaStrategyInputProducer("bollinger_bands", "market_snapshot.bollinger_bands", "market_snapshot_builder"),
    MetaStrategyInputProducer("bollingerWidthPercentile", "market_snapshot.features.bollingerWidthPercentile", "market_snapshot_builder"),
    MetaStrategyInputProducer("relative_volume", "market_snapshot.relative_volume", "market_snapshot_builder"),
    MetaStrategyInputProducer("volume", "market_snapshot.volume", "market_snapshot_builder"),
    MetaStrategyInputProducer("spread", "market_snapshot.spread", "market_snapshot_builder"),
    MetaStrategyInputProducer("liquidity", "market_snapshot.liquidity", "market_snapshot_builder"),
    MetaStrategyInputProducer("session_phase", "market_snapshot.session_phase", "session_calendar"),
    MetaStrategyInputProducer("gap_state", "market_snapshot.gap_state", "market_snapshot_builder"),
    MetaStrategyInputProducer("qqq_iwm_context", "market_snapshot.qqq_iwm_context", "market_snapshot_builder"),
    MetaStrategyInputProducer("breadth", "market_snapshot.breadth", "market_snapshot_builder"),
    MetaStrategyInputProducer("economic_event_state", "evaluation_context.economic_event_snapshot", "economic_event_reader"),
    MetaStrategyInputProducer("pullbackDepthAtr", "market_snapshot.features.pullbackDepthAtr", "market_snapshot_builder"),
    MetaStrategyInputProducer("failedBreakoutSide", "market_snapshot.features.failedBreakoutSide", "market_snapshot_builder"),
    MetaStrategyInputProducer("reclaimDistanceAtr", "market_snapshot.features.reclaimDistanceAtr", "market_snapshot_builder"),
    MetaStrategyInputProducer("sweepSide", "market_snapshot.features.sweepSide", "market_snapshot_builder"),
    MetaStrategyInputProducer("rejectionWickRatio", "market_snapshot.features.rejectionWickRatio", "market_snapshot_builder"),
    MetaStrategyInputProducer("openingRangeHigh", "market_snapshot.features.openingRangeHigh", "market_snapshot_builder"),
    MetaStrategyInputProducer("openingRangeLow", "market_snapshot.features.openingRangeLow", "market_snapshot_builder"),
    MetaStrategyInputProducer("opening_range", "market_snapshot.features.openingRangeHighLow", "market_snapshot_builder"),
    MetaStrategyInputProducer("previous_day_levels", "market_snapshot.features.previousDayHighLow", "market_snapshot_builder", critical=False),
    MetaStrategyInputProducer("premarket_levels", "market_snapshot.features.premarketHighLow", "market_snapshot_builder", critical=False),
    MetaStrategyInputProducer("recent_swing_levels", "market_snapshot.features.recentSwingLevels", "market_snapshot_builder"),
    MetaStrategyInputProducer("session_levels", "market_snapshot.features.sessionHighLow", "market_snapshot_builder"),
    MetaStrategyInputProducer("microstructure_evidence", "market_snapshot.features.microstructureEvidence", "quote_trade_depth_reader", critical=False),
    MetaStrategyInputProducer("cash_available", "evaluation_context.account_snapshot.cash_available", "account_data_reader"),
    MetaStrategyInputProducer("avoid_trading", "evaluation_context.operational_health_snapshot.trading_allowed", "operational_health_reader"),
    MetaStrategyInputProducer("critical_data", "market_snapshot", "market_snapshot_builder"),
    MetaStrategyInputProducer("source_cutoff_timestamp", "market_snapshot.source_cutoff_timestamp", "market_snapshot_builder"),
    MetaStrategyInputProducer("halt_luld_state", "market_snapshot.features.haltLuldState", "market_snapshot_builder"),
    MetaStrategyInputProducer("operational_health", "evaluation_context.operational_health_snapshot", "operational_health_reader"),
    MetaStrategyInputProducer("daily_loss_state", "algorithm_inventory_snapshot.daily_statistics", "meta_strategy_repository"),
    MetaStrategyInputProducer("trade_count_state", "algorithm_inventory_snapshot.daily_statistics", "meta_strategy_repository"),
    MetaStrategyInputProducer("duplicate_order_state", "algorithm_order_intents", "meta_strategy_repository"),
    MetaStrategyInputProducer("existing_position_state", "algorithm_inventory_snapshot.positions", "meta_strategy_repository"),
    MetaStrategyInputProducer("local_risk_budget", "algorithm_inventory_snapshot.reserved_risk", "meta_strategy_repository"),
)
META_STRATEGY_INPUT_PRODUCER_IDS = frozenset(producer.input_id for producer in META_STRATEGY_INPUT_PRODUCERS)


def validate_required_input_producers(entries: Iterable[Any]) -> dict[str, Any]:
    missing = tuple(
        sorted(
            {
                input_id
                for entry in entries
                if bool(getattr(entry, "enabled", False))
                for input_id in getattr(entry, "required_inputs", ())
                if input_id not in META_STRATEGY_INPUT_PRODUCER_IDS
            }
        )
    )
    return {
        "algorithmId": "meta_strategy",
        "valid": not missing,
        "missingProducers": missing,
        "producerCount": len(META_STRATEGY_INPUT_PRODUCERS),
        "reasonCodes": ("meta_strategy.feature_contracts.valid" if not missing else "meta_strategy.feature_contracts.missing_producer",),
    }


def feature_value(snapshot: MetaStrategyMarketSnapshot, name: str) -> Any:
    if name not in META_STRATEGY_INPUT_PRODUCER_IDS:
        raise KeyError(f"Meta-Strategy feature has no authoritative producer: {name}")
    return snapshot.features.get(name)


def required_input_status(snapshot: MetaStrategyMarketSnapshot, required_inputs: Iterable[str]) -> dict[str, bool]:
    return {name: has_required_input(snapshot, name) for name in required_inputs}


def has_required_input(snapshot: MetaStrategyMarketSnapshot, name: str) -> bool:
    if name == "candles":
        return bool(snapshot.candles.get("1m"))
    if name == "moving_averages":
        return bool(snapshot.moving_averages.get("1m"))
    if name in {"moving_average_slope", "market_structure", "reward_to_risk"}:
        return bool(snapshot.candles.get("1m")) and bool(snapshot.moving_averages.get("1m"))
    if name == "vwap":
        return snapshot.vwap is not None
    if name in {"vwap_relationship", "vwap_slope"}:
        return snapshot.vwap is not None and bool(snapshot.candles.get("1m"))
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
    if name == "bollingerWidthPercentile":
        return snapshot.bollinger_bands.get("1m") is not None
    if name == "relative_volume":
        return snapshot.relative_volume.get("1m") is not None
    if name == "volume":
        return snapshot.volume > 0
    if name == "spread":
        return bool(snapshot.spread) or snapshot.spread_bps is not None
    if name == "liquidity":
        return bool(snapshot.liquidity)
    if name == "session_phase":
        return bool(snapshot.session_phase)
    if name == "gap_state":
        return bool(snapshot.gap_state)
    if name == "qqq_iwm_context":
        return bool(snapshot.qqq_iwm_context)
    if name == "breadth":
        return bool(snapshot.breadth)
    if name == "economic_event_state":
        return bool(snapshot.economic_event_state)
    if name == "source_cutoff_timestamp":
        return snapshot.source_cutoff_timestamp is not None
    if name == "opening_range":
        return _present(snapshot.features, "openingRangeHigh") and _present(snapshot.features, "openingRangeLow")
    if name == "previous_day_levels":
        return _present(snapshot.features, "previousDayHigh") and _present(snapshot.features, "previousDayLow")
    if name == "premarket_levels":
        return _present(snapshot.features, "premarketHigh") and _present(snapshot.features, "premarketLow")
    if name == "recent_swing_levels":
        return bool(snapshot.candles.get("1m")) or (_present(snapshot.features, "recentSwingHigh") and _present(snapshot.features, "recentSwingLow"))
    if name == "session_levels":
        return bool(snapshot.candles.get("1m"))
    if name == "microstructure_evidence":
        evidence = snapshot.features.get("microstructureEvidence")
        return isinstance(evidence, Mapping) and bool(evidence.get("reliable"))
    if name == "critical_data":
        return bool(
            snapshot.point_in_time
            and snapshot.source_cutoff_timestamp is not None
            and snapshot.candles.get("1m")
            and snapshot.vwap is not None
            and snapshot.atr.get("1m") is not None
            and (snapshot.spread or snapshot.spread_bps is not None)
            and snapshot.liquidity
        )
    if name == "cash_available":
        return _present(snapshot.features, "cashAvailable")
    if name == "avoid_trading":
        return _present(snapshot.features, "avoidTrading")
    if name == "operational_health":
        return _present(snapshot.features, "operationalHealth")
    if name == "halt_luld_state":
        return _present(snapshot.features, "haltLuldState")
    if name == "daily_loss_state":
        return _present(snapshot.features, "dailyLossLimit") and (_present(snapshot.features, "marketDailyPnl") or _present(snapshot.features, "dailyPnl"))
    if name == "trade_count_state":
        return _present(snapshot.features, "tradeCount") and _present(snapshot.features, "tradeCountLimit")
    if name == "duplicate_order_state":
        return _present(snapshot.features, "duplicateOrderState")
    if name == "existing_position_state":
        return _present(snapshot.features, "existingPositionState")
    if name == "local_risk_budget":
        return _present(snapshot.features, "localRiskBudget")
    return _present(snapshot.features, name)


def _present(payload: Mapping[str, Any], name: str) -> bool:
    value = payload.get(name)
    return value is not None and value != "" and value != {}


__all__ = [
    "META_STRATEGY_INPUT_PRODUCER_IDS",
    "META_STRATEGY_INPUT_PRODUCERS",
    "MetaStrategyInputProducer",
    "feature_value",
    "has_required_input",
    "required_input_status",
    "validate_required_input_producers",
]
