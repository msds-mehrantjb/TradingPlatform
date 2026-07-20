"""Meta-Strategy-owned triple-barrier label generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping

from backend.app.algorithms.meta_strategy.contracts import CandidateGeometry, MetaLabel
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.labeling.execution_labels import (
    CandidateSide,
    MetaStrategyLabelingError,
    execution_costs,
    execution_price,
    geometry_valid,
    require_finite_non_negative,
    require_finite_positive,
    require_timezone_aware,
)
from backend.app.algorithms.meta_strategy.labeling.lineage import label_id
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_LABEL_SPECIFICATION_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)


META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION = "candidate_triple_barrier_v1"
META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION = "meta_strategy_label_execution_v1"
BarrierHit = Literal["TARGET", "STOP", "VERTICAL", "NO_CANDIDATE", "NO_ENTRY", "INVALID_GEOMETRY", "AMBIGUOUS"]
SameBarAmbiguityPolicy = Literal["stop_first", "target_first", "exclude_from_training"]


@dataclass(frozen=True)
class MetaStrategyLabelExecutionConfig:
    configVersion: str = META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION
    maxHoldingPeriodMinutes: int = 30
    spreadDollars: float = 0.0
    slippagePerShare: float = 0.0
    feesPerShare: float = 0.0
    flatFeePerOrder: float = 0.0
    latencyMilliseconds: int = 0
    orderFillBehavior: Literal["next_open_after_latency"] = "next_open_after_latency"
    sameBarAmbiguityPolicy: SameBarAmbiguityPolicy = "stop_first"
    configurationHash: str = META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION

    def __post_init__(self) -> None:
        if self.maxHoldingPeriodMinutes <= 0:
            raise MetaStrategyLabelingError("maxHoldingPeriodMinutes must be positive")
        if self.latencyMilliseconds < 0:
            raise MetaStrategyLabelingError("latencyMilliseconds must be non-negative")
        require_finite_non_negative(self.spreadDollars, "spreadDollars")
        require_finite_non_negative(self.slippagePerShare, "slippagePerShare")
        require_finite_non_negative(self.feesPerShare, "feesPerShare")
        require_finite_non_negative(self.flatFeePerOrder, "flatFeePerOrder")
        if not self.configurationHash.strip():
            raise MetaStrategyLabelingError("configurationHash cannot be empty")


@dataclass(frozen=True)
class MetaStrategyLabelCandle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        require_timezone_aware(self.timestamp, "candle.timestamp")
        require_finite_positive(self.open, "candle.open")
        require_finite_positive(self.high, "candle.high")
        require_finite_positive(self.low, "candle.low")
        require_finite_positive(self.close, "candle.close")
        require_finite_non_negative(self.volume, "candle.volume")
        if self.low > self.high:
            raise MetaStrategyLabelingError("candle.low cannot be greater than candle.high")


@dataclass(frozen=True)
class BarrierResult:
    hit: BarrierHit
    timestamp: datetime
    exit_reference_price: float | None
    ambiguous: bool = False
    gap_through_stop: bool = False
    reason_code: str | None = None


@dataclass(frozen=True)
class MetaStrategyExecutionLabel:
    labelId: str
    labelVersion: str
    labelSpecificationVersion: str
    algorithmId: str
    algorithmVersion: str
    configurationVersion: str
    strategyCatalogVersion: str
    decisionId: str
    snapshotId: str
    symbol: str
    candidateId: str | None
    candidateSide: CandidateSide
    decisionTimestampUtc: datetime
    labelEndTimestampUtc: datetime
    entryTimestampUtc: datetime | None
    entryPrice: float | None
    profitTargetPrice: float | None
    protectiveStopPrice: float | None
    upperBarrierPrice: float | None
    lowerBarrierPrice: float | None
    verticalBarrierTimestampUtc: datetime | None
    firstBarrierHit: BarrierHit
    firstBarrierTimestampUtc: datetime | None
    exitPrice: float | None
    strictOutcomeLabel: int | None
    costAdjustedTrainingLabel: int | None
    grossPnlPerShare: float | None
    netPnlAfterCosts: float | None
    quantity: float
    spreadDollars: float
    slippagePerShare: float
    fees: float
    latencyMilliseconds: int
    orderFillBehavior: str
    sameBarAmbiguityPolicy: SameBarAmbiguityPolicy
    ambiguous: bool
    gapThroughStop: bool
    eligibleForTraining: bool
    reasonCodes: tuple[str, ...]
    barrierExplanation: str
    createdAt: datetime
    configurationHash: str
    metaLabel: MetaLabel

    @property
    def label_end_timestamp_utc(self) -> datetime:
        return self.labelEndTimestampUtc

    def legacy_fixture_summary(self) -> dict[str, Any]:
        return {
            "candidateSide": self.candidateSide,
            "costAdjustedTrainingLabel": self.costAdjustedTrainingLabel,
            "eligibleForTraining": self.eligibleForTraining,
            "entryPrice": self.entryPrice,
            "firstBarrierHit": self.firstBarrierHit,
            "labelId": self.labelId,
            "labelVersion": self.labelVersion,
            "profitTargetPrice": self.profitTargetPrice,
            "protectiveStopPrice": self.protectiveStopPrice,
            "reasonCodes": list(self.reasonCodes),
            "strictOutcomeLabel": self.strictOutcomeLabel,
        }


def create_triple_barrier_label(
    *,
    decision_id: str,
    snapshot_id: str,
    symbol: str,
    decision_timestamp_utc: datetime,
    candidate_side: CandidateSide,
    geometry: CandidateGeometry | Mapping[str, Any] | None,
    future_candles: tuple[MetaStrategyLabelCandle, ...] | list[MetaStrategyLabelCandle],
    config: MetaStrategyLabelExecutionConfig | None = None,
) -> MetaStrategyExecutionLabel:
    execution = config or MetaStrategyLabelExecutionConfig()
    require_timezone_aware(decision_timestamp_utc, "decision_timestamp_utc")
    if candidate_side == "HOLD":
        return _diagnostic_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp_utc=decision_timestamp_utc,
            candidate_side="HOLD",
            first_barrier_hit="NO_CANDIDATE",
            reason_codes=("hold_snapshot_diagnostic_only",),
            explanation="Hold snapshots are retained for diagnostics and are not labeled as failed candidate trades.",
            config=execution,
        )

    candidate_id, quantity, stop_price, target_price = _candidate_geometry_values(geometry, candidate_side)
    if candidate_id is None:
        return _diagnostic_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp_utc=decision_timestamp_utc,
            candidate_side=candidate_side,
            first_barrier_hit="INVALID_GEOMETRY",
            reason_codes=("candidate_trade_geometry_missing",),
            explanation="Candidate side exists, but no complete stop/target/order geometry was available for labeling.",
            config=execution,
        )

    entry_after = decision_timestamp_utc + timedelta(milliseconds=execution.latencyMilliseconds)
    ordered_candles = tuple(sorted((candle for candle in future_candles if candle.timestamp > entry_after), key=lambda candle: candle.timestamp))
    if not ordered_candles:
        return _diagnostic_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp_utc=decision_timestamp_utc,
            candidate_side=candidate_side,
            first_barrier_hit="NO_ENTRY",
            reason_codes=("no_executable_price_after_decision",),
            explanation="No post-decision candle was available after latency, so no executable simulated entry exists.",
            config=execution,
            candidate_id=candidate_id,
            quantity=quantity,
            profit_target_price=target_price,
            protective_stop_price=stop_price,
        )

    entry_candle = ordered_candles[0]
    entry_price = execution_price(
        entry_candle.open,
        candidate_side,
        spread_dollars=execution.spreadDollars,
        slippage_per_share=execution.slippagePerShare,
        is_entry=True,
    )
    if not geometry_valid(candidate_side, entry_price, stop_price, target_price):
        return _diagnostic_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp_utc=decision_timestamp_utc,
            candidate_side=candidate_side,
            first_barrier_hit="INVALID_GEOMETRY",
            reason_codes=("entry_geometry_invalid_after_execution",),
            explanation="The next executable entry no longer sat between the proposed stop and target.",
            config=execution,
            candidate_id=candidate_id,
            quantity=quantity,
            entry_timestamp=entry_candle.timestamp,
            entry_price=entry_price,
            profit_target_price=target_price,
            protective_stop_price=stop_price,
        )

    vertical_at = entry_candle.timestamp + timedelta(minutes=execution.maxHoldingPeriodMinutes)
    barrier = first_barrier(
        side=candidate_side,
        candles=ordered_candles,
        target_price=target_price,
        stop_price=stop_price,
        vertical_at=vertical_at,
        same_bar_policy=execution.sameBarAmbiguityPolicy,
    )
    if barrier.hit == "AMBIGUOUS" or barrier.exit_reference_price is None:
        return _diagnostic_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp_utc=decision_timestamp_utc,
            candidate_side=candidate_side,
            first_barrier_hit=barrier.hit,
            reason_codes=(barrier.reason_code or "same_bar_ambiguity_excluded",),
            explanation="Target and stop were reached in the same candle and the configured ambiguity policy excluded the row from training.",
            config=execution,
            candidate_id=candidate_id,
            quantity=quantity,
            entry_timestamp=entry_candle.timestamp,
            entry_price=entry_price,
            profit_target_price=target_price,
            protective_stop_price=stop_price,
            label_end_timestamp=barrier.timestamp,
            first_barrier_timestamp=barrier.timestamp,
            vertical_barrier_timestamp=vertical_at,
            ambiguous=True,
        )

    exit_price = execution_price(
        barrier.exit_reference_price,
        candidate_side,
        spread_dollars=execution.spreadDollars,
        slippage_per_share=execution.slippagePerShare,
        is_entry=False,
    )
    costs = execution_costs(
        side=candidate_side,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        fees_per_share=execution.feesPerShare,
        flat_fee_per_order=execution.flatFeePerOrder,
    )
    strict_label = 1 if barrier.hit == "TARGET" else 0
    cost_adjusted_label = 1 if strict_label == 1 and costs.net_pnl_after_costs > 0 else 0
    stop_distance = abs(entry_price - stop_price)
    return_r = costs.net_pnl_per_share / stop_distance if stop_distance > 0 else 0.0
    outcome = "WIN" if barrier.hit == "TARGET" else "LOSS" if barrier.hit == "STOP" else "TIMEOUT"
    label_end = barrier.timestamp
    return MetaStrategyExecutionLabel(
        labelId=label_id(snapshot_id=snapshot_id, label_version=META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION, configuration_hash=execution.configurationHash),
        labelVersion=META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION,
        labelSpecificationVersion=META_STRATEGY_LABEL_SPECIFICATION_VERSION,
        algorithmId=ALGORITHM_ID,
        algorithmVersion=META_STRATEGY_ALGORITHM_VERSION,
        configurationVersion=META_STRATEGY_CONFIGURATION_VERSION,
        strategyCatalogVersion=META_STRATEGY_STRATEGY_CATALOG_VERSION,
        decisionId=decision_id,
        snapshotId=snapshot_id,
        symbol=symbol,
        candidateId=candidate_id,
        candidateSide=candidate_side,
        decisionTimestampUtc=decision_timestamp_utc,
        labelEndTimestampUtc=label_end,
        entryTimestampUtc=entry_candle.timestamp,
        entryPrice=entry_price,
        profitTargetPrice=target_price,
        protectiveStopPrice=stop_price,
        upperBarrierPrice=target_price,
        lowerBarrierPrice=stop_price,
        verticalBarrierTimestampUtc=vertical_at,
        firstBarrierHit=barrier.hit,
        firstBarrierTimestampUtc=barrier.timestamp,
        exitPrice=exit_price,
        strictOutcomeLabel=strict_label,
        costAdjustedTrainingLabel=cost_adjusted_label,
        grossPnlPerShare=costs.gross_pnl_per_share,
        netPnlAfterCosts=costs.net_pnl_after_costs,
        quantity=quantity,
        spreadDollars=execution.spreadDollars,
        slippagePerShare=execution.slippagePerShare,
        fees=costs.fees,
        latencyMilliseconds=execution.latencyMilliseconds,
        orderFillBehavior=execution.orderFillBehavior,
        sameBarAmbiguityPolicy=execution.sameBarAmbiguityPolicy,
        ambiguous=barrier.ambiguous,
        gapThroughStop=barrier.gap_through_stop,
        eligibleForTraining=True,
        reasonCodes=(f"first_barrier_{barrier.hit.lower()}", "candidate_only_binary_meta_label"),
        barrierExplanation=_barrier_explanation(candidate_side, target_price, stop_price, vertical_at, execution),
        createdAt=decision_timestamp_utc,
        configurationHash=execution.configurationHash,
        metaLabel=_meta_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            timestamp=label_end,
            label=candidate_side,
            outcome=outcome,
            return_r=return_r,
            barrier_minutes=execution.maxHoldingPeriodMinutes,
        ),
    )


def first_barrier(
    *,
    side: CandidateSide,
    candles: tuple[MetaStrategyLabelCandle, ...],
    target_price: float,
    stop_price: float,
    vertical_at: datetime,
    same_bar_policy: SameBarAmbiguityPolicy,
) -> BarrierResult:
    if side == "HOLD":
        raise MetaStrategyLabelingError("hold candidates do not have barriers")
    require_timezone_aware(vertical_at, "vertical_at")
    last_candle = candles[0]
    for candle in candles:
        if candle.timestamp > vertical_at:
            break
        last_candle = candle
        gap_through_stop = candle.open <= stop_price if side == "BUY" else candle.open >= stop_price
        if gap_through_stop:
            return BarrierResult(hit="STOP", timestamp=candle.timestamp, exit_reference_price=candle.open, gap_through_stop=True, reason_code="gap_through_stop")
        target_hit = candle.high >= target_price if side == "BUY" else candle.low <= target_price
        stop_hit = candle.low <= stop_price if side == "BUY" else candle.high >= stop_price
        if target_hit and stop_hit:
            if same_bar_policy == "exclude_from_training":
                return BarrierResult(hit="AMBIGUOUS", timestamp=candle.timestamp, exit_reference_price=None, ambiguous=True, reason_code="same_bar_ambiguity_excluded")
            hit: Literal["TARGET", "STOP"] = "TARGET" if same_bar_policy == "target_first" else "STOP"
            price = target_price if hit == "TARGET" else stop_price
            return BarrierResult(hit=hit, timestamp=candle.timestamp, exit_reference_price=price, ambiguous=True, reason_code=f"same_bar_{same_bar_policy}")
        if target_hit:
            return BarrierResult(hit="TARGET", timestamp=candle.timestamp, exit_reference_price=target_price)
        if stop_hit:
            return BarrierResult(hit="STOP", timestamp=candle.timestamp, exit_reference_price=stop_price)

    vertical_candle = next((candle for candle in candles if candle.timestamp >= vertical_at), last_candle)
    return BarrierResult(hit="VERTICAL", timestamp=vertical_candle.timestamp, exit_reference_price=vertical_candle.close)


def candle_from_mapping(value: Mapping[str, Any]) -> MetaStrategyLabelCandle:
    timestamp = value.get("timestamp") or value.get("timestampUtc")
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if not isinstance(timestamp, datetime):
        raise MetaStrategyLabelingError("candle timestamp is required")
    return MetaStrategyLabelCandle(
        timestamp=timestamp,
        open=float(value["open"]),
        high=float(value["high"]),
        low=float(value["low"]),
        close=float(value["close"]),
        volume=float(value.get("volume", 0.0)),
    )


def _candidate_geometry_values(geometry: CandidateGeometry | Mapping[str, Any] | None, side: CandidateSide) -> tuple[str | None, float, float, float]:
    if geometry is None:
        return None, 0.0, 0.0, 0.0
    if isinstance(geometry, CandidateGeometry):
        if geometry.side not in {side, "HOLD"}:
            return None, 0.0, 0.0, 0.0
        candidate_id = geometry.candidate_id
        quantity = float(geometry.quantity)
        stop_price = geometry.stop_price
        target_price = geometry.target_price
    else:
        candidate_id = geometry.get("candidateId") or geometry.get("candidate_id")
        quantity = float(geometry.get("quantity") or 0.0)
        stop_price = geometry.get("stopPrice", geometry.get("stop_price"))
        target_price = geometry.get("targetPrice", geometry.get("target_price"))
        if geometry.get("eligible") is False:
            return None, 0.0, 0.0, 0.0
    if not candidate_id or quantity <= 0 or stop_price is None or target_price is None:
        return None, 0.0, 0.0, 0.0
    return str(candidate_id), quantity, float(stop_price), float(target_price)


def _diagnostic_label(
    *,
    decision_id: str,
    snapshot_id: str,
    symbol: str,
    decision_timestamp_utc: datetime,
    candidate_side: CandidateSide,
    first_barrier_hit: BarrierHit,
    reason_codes: tuple[str, ...],
    explanation: str,
    config: MetaStrategyLabelExecutionConfig,
    candidate_id: str | None = None,
    quantity: float = 0.0,
    entry_timestamp: datetime | None = None,
    entry_price: float | None = None,
    profit_target_price: float | None = None,
    protective_stop_price: float | None = None,
    label_end_timestamp: datetime | None = None,
    first_barrier_timestamp: datetime | None = None,
    vertical_barrier_timestamp: datetime | None = None,
    ambiguous: bool = False,
) -> MetaStrategyExecutionLabel:
    label_end = label_end_timestamp or first_barrier_timestamp or entry_timestamp or decision_timestamp_utc
    return MetaStrategyExecutionLabel(
        labelId=label_id(snapshot_id=snapshot_id, label_version=META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION, configuration_hash=config.configurationHash),
        labelVersion=META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION,
        labelSpecificationVersion=META_STRATEGY_LABEL_SPECIFICATION_VERSION,
        algorithmId=ALGORITHM_ID,
        algorithmVersion=META_STRATEGY_ALGORITHM_VERSION,
        configurationVersion=META_STRATEGY_CONFIGURATION_VERSION,
        strategyCatalogVersion=META_STRATEGY_STRATEGY_CATALOG_VERSION,
        decisionId=decision_id,
        snapshotId=snapshot_id,
        symbol=symbol,
        candidateId=candidate_id,
        candidateSide=candidate_side,
        decisionTimestampUtc=decision_timestamp_utc,
        labelEndTimestampUtc=label_end,
        entryTimestampUtc=entry_timestamp,
        entryPrice=entry_price,
        profitTargetPrice=profit_target_price,
        protectiveStopPrice=protective_stop_price,
        upperBarrierPrice=profit_target_price,
        lowerBarrierPrice=protective_stop_price,
        verticalBarrierTimestampUtc=vertical_barrier_timestamp,
        firstBarrierHit=first_barrier_hit,
        firstBarrierTimestampUtc=first_barrier_timestamp,
        exitPrice=None,
        strictOutcomeLabel=None,
        costAdjustedTrainingLabel=None,
        grossPnlPerShare=None,
        netPnlAfterCosts=None,
        quantity=quantity,
        spreadDollars=config.spreadDollars,
        slippagePerShare=config.slippagePerShare,
        fees=0.0,
        latencyMilliseconds=config.latencyMilliseconds,
        orderFillBehavior=config.orderFillBehavior,
        sameBarAmbiguityPolicy=config.sameBarAmbiguityPolicy,
        ambiguous=ambiguous,
        gapThroughStop=False,
        eligibleForTraining=False,
        reasonCodes=reason_codes,
        barrierExplanation=explanation,
        createdAt=decision_timestamp_utc,
        configurationHash=config.configurationHash,
        metaLabel=_meta_label(
            decision_id=decision_id,
            snapshot_id=snapshot_id,
            timestamp=label_end,
            label=candidate_side,
            outcome="NO_TRADE" if first_barrier_hit in {"NO_CANDIDATE", "NO_ENTRY", "INVALID_GEOMETRY"} else "TIMEOUT",
            return_r=0.0,
            barrier_minutes=0,
        ),
    )


def _meta_label(
    *,
    decision_id: str,
    snapshot_id: str,
    timestamp: datetime,
    label: CandidateSide,
    outcome: Literal["WIN", "LOSS", "TIMEOUT", "NO_TRADE"],
    return_r: float,
    barrier_minutes: int,
) -> MetaLabel:
    return MetaLabel(
        algorithm_id=ALGORITHM_ID,
        algorithm_version=META_STRATEGY_ALGORITHM_VERSION,
        configuration_version=META_STRATEGY_CONFIGURATION_VERSION,
        strategy_catalog_version=META_STRATEGY_STRATEGY_CATALOG_VERSION,
        decision_id=decision_id,
        snapshot_id=snapshot_id,
        timestamp=timestamp,
        label=label,
        outcome=outcome,
        return_r=round(return_r, 8),
        barrier_minutes=barrier_minutes,
        label_specification_version=META_STRATEGY_LABEL_SPECIFICATION_VERSION,
    )


def _barrier_explanation(
    side: CandidateSide,
    target_price: float,
    stop_price: float,
    vertical_at: datetime,
    config: MetaStrategyLabelExecutionConfig,
) -> str:
    return (
        f"{META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION}: {side} candidate uses the profit target at {target_price:.4f}, "
        f"protective stop at {stop_price:.4f}, vertical barrier at {vertical_at.isoformat()}, "
        f"entry from {config.orderFillBehavior}, spread {config.spreadDollars:.4f}, "
        f"slippage/share {config.slippagePerShare:.4f}, fees/share {config.feesPerShare:.4f}, "
        f"flat fee/order {config.flatFeePerOrder:.4f}, latency {config.latencyMilliseconds} ms."
    )


__all__ = [
    "BarrierHit",
    "BarrierResult",
    "META_STRATEGY_LABEL_EXECUTION_CONFIG_VERSION",
    "META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION",
    "MetaStrategyExecutionLabel",
    "MetaStrategyLabelCandle",
    "MetaStrategyLabelExecutionConfig",
    "SameBarAmbiguityPolicy",
    "candle_from_mapping",
    "create_triple_barrier_label",
    "first_barrier",
]
