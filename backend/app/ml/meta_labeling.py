from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import CandidateMetaLabel, DecisionSnapshotV2, DomainModel, OrderPlan, Signal, TradeCandidate


META_LABEL_VERSION = "candidate_triple_barrier_v1"


class MetaLabelExecutionConfig(DomainModel):
    configVersion: str = Field(default="candidate_meta_label_execution_v1", min_length=1)
    maxHoldingPeriodMinutes: int = Field(default=30, gt=0)
    spreadDollars: float = Field(default=0.0, ge=0)
    slippagePerShare: float = Field(default=0.0, ge=0)
    feesPerShare: float = Field(default=0.0, ge=0)
    flatFeePerOrder: float = Field(default=0.0, ge=0)
    latencyMilliseconds: int = Field(default=0, ge=0)
    orderFillBehavior: Literal["next_open_after_latency"] = "next_open_after_latency"
    sameCandleTieBreak: Literal["stop_first", "target_first"] = "stop_first"
    configurationHash: str = Field(default="candidate_meta_label_execution_v1", min_length=1)


def create_candidate_meta_label(
    snapshot: DecisionSnapshotV2,
    future_candles: list[MarketCandle],
    config: MetaLabelExecutionConfig | None = None,
) -> CandidateMetaLabel:
    """Create a candidate-only binary meta-label from post-decision candles."""

    execution = config or MetaLabelExecutionConfig()
    candidate_side = _signal_value(snapshot.ensembleDecision.signal)
    decision_at = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp

    if candidate_side == Signal.HOLD.value:
        return _diagnostic_label(
            snapshot,
            execution,
            candidate_side=Signal.HOLD,
            first_barrier_hit="NO_CANDIDATE",
            reason_codes=["hold_snapshot_diagnostic_only"],
            explanation="Hold snapshots are retained for diagnostics and are not labeled as failed candidate trades.",
        )

    geometry = _candidate_geometry(snapshot)
    if geometry is None:
        return _diagnostic_label(
            snapshot,
            execution,
            candidate_side=Signal(candidate_side),
            first_barrier_hit="INVALID_GEOMETRY",
            reason_codes=["candidate_trade_geometry_missing"],
            explanation="Candidate side exists, but no complete stop/target/order geometry was available for labeling.",
        )

    candidate_id, quantity, stop_price, target_price = geometry
    entry_after = decision_at + timedelta(milliseconds=execution.latencyMilliseconds)
    ordered_candles = sorted((candle for candle in future_candles if candle.timestamp > entry_after), key=lambda candle: candle.timestamp)
    if not ordered_candles:
        return _diagnostic_label(
            snapshot,
            execution,
            candidate_side=Signal(candidate_side),
            candidate_id=candidate_id,
            quantity=quantity,
            profit_target_price=target_price,
            protective_stop_price=stop_price,
            first_barrier_hit="NO_ENTRY",
            reason_codes=["no_executable_price_after_decision"],
            explanation="No post-decision candle was available after latency, so no executable simulated entry exists.",
        )

    entry_candle = ordered_candles[0]
    entry_price = _execution_price(entry_candle.open, Signal(candidate_side), execution, is_entry=True)
    if not _geometry_valid(candidate_side, entry_price, stop_price, target_price):
        return _diagnostic_label(
            snapshot,
            execution,
            candidate_side=Signal(candidate_side),
            candidate_id=candidate_id,
            quantity=quantity,
            entry_timestamp=entry_candle.timestamp,
            entry_price=entry_price,
            profit_target_price=target_price,
            protective_stop_price=stop_price,
            first_barrier_hit="INVALID_GEOMETRY",
            reason_codes=["entry_geometry_invalid_after_execution"],
            explanation="The next executable entry no longer sat between the proposed stop and target.",
        )

    vertical_at = entry_candle.timestamp + timedelta(minutes=execution.maxHoldingPeriodMinutes)
    barrier = _first_barrier(
        side=Signal(candidate_side),
        candles=ordered_candles,
        target_price=target_price,
        stop_price=stop_price,
        vertical_at=vertical_at,
        config=execution,
    )
    exit_price = _execution_price(barrier.exit_reference_price, Signal(candidate_side), execution, is_entry=False)
    gross_per_share = _side_multiplier(candidate_side) * (exit_price - entry_price)
    fees = (execution.flatFeePerOrder * 2.0) + (execution.feesPerShare * quantity * 2.0)
    net_pnl = (gross_per_share * quantity) - fees
    strict_label: Literal[0, 1] = 1 if barrier.hit == "TARGET" else 0
    cost_adjusted_label: Literal[0, 1] = 1 if strict_label == 1 and net_pnl > 0 else 0

    return CandidateMetaLabel(
        labelId=_label_id(snapshot, execution),
        labelVersion=META_LABEL_VERSION,
        snapshotId=snapshot.snapshotId,
        symbol=snapshot.symbol,
        candidateId=candidate_id,
        candidateSide=Signal(candidate_side),
        decisionTimestampUtc=decision_at,
        sessionDateNewYork=snapshot.sessionDateNewYork or snapshot.sessionDate,
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
        grossPnlPerShare=gross_per_share,
        netPnlAfterCosts=net_pnl,
        quantity=quantity,
        spreadDollars=execution.spreadDollars,
        slippagePerShare=execution.slippagePerShare,
        fees=fees,
        latencyMilliseconds=execution.latencyMilliseconds,
        orderFillBehavior=execution.orderFillBehavior,
        barrierExplanation=_barrier_explanation(Signal(candidate_side), target_price, stop_price, vertical_at, execution),
        eligibleForTraining=True,
        reasonCodes=[f"first_barrier_{barrier.hit.lower()}", "candidate_only_binary_meta_label"],
        createdAt=decision_at,
        configurationHash=execution.configurationHash,
    )


class _BarrierResult(DomainModel):
    hit: Literal["TARGET", "STOP", "VERTICAL"]
    timestamp: datetime
    exit_reference_price: float


def _first_barrier(
    *,
    side: Signal,
    candles: list[MarketCandle],
    target_price: float,
    stop_price: float,
    vertical_at: datetime,
    config: MetaLabelExecutionConfig,
) -> _BarrierResult:
    last_candle = candles[0]
    for candle in candles:
        if candle.timestamp > vertical_at:
            break
        last_candle = candle
        target_hit = candle.high >= target_price if side == Signal.BUY else candle.low <= target_price
        stop_hit = candle.low <= stop_price if side == Signal.BUY else candle.high >= stop_price
        if target_hit and stop_hit:
            hit = "TARGET" if config.sameCandleTieBreak == "target_first" else "STOP"
            price = target_price if hit == "TARGET" else stop_price
            return _BarrierResult(hit=hit, timestamp=candle.timestamp, exit_reference_price=price)
        if target_hit:
            return _BarrierResult(hit="TARGET", timestamp=candle.timestamp, exit_reference_price=target_price)
        if stop_hit:
            return _BarrierResult(hit="STOP", timestamp=candle.timestamp, exit_reference_price=stop_price)

    vertical_candle = next((candle for candle in candles if candle.timestamp >= vertical_at), last_candle)
    return _BarrierResult(hit="VERTICAL", timestamp=vertical_candle.timestamp, exit_reference_price=vertical_candle.close)


def _candidate_geometry(snapshot: DecisionSnapshotV2) -> tuple[str, int, float, float] | None:
    if snapshot.orderPlan and snapshot.orderPlan.eligible and snapshot.orderPlan.orderType != "NO_ORDER":
        return _geometry_from_order(snapshot.orderPlan)
    if snapshot.tradeCandidate:
        return _geometry_from_candidate(snapshot.tradeCandidate)
    return None


def _geometry_from_order(order: OrderPlan) -> tuple[str, int, float, float] | None:
    if order.stopPrice is None or order.targetPrice is None or order.quantity <= 0:
        return None
    return order.candidateId, order.quantity, order.stopPrice, order.targetPrice


def _geometry_from_candidate(candidate: TradeCandidate) -> tuple[str, int, float, float] | None:
    if candidate.stopPrice is None or candidate.targetPrice is None or candidate.quantity <= 0:
        return None
    return candidate.candidateId, candidate.quantity, candidate.stopPrice, candidate.targetPrice


def _diagnostic_label(
    snapshot: DecisionSnapshotV2,
    execution: MetaLabelExecutionConfig,
    *,
    candidate_side: Signal,
    first_barrier_hit: Literal["NO_CANDIDATE", "NO_ENTRY", "INVALID_GEOMETRY"],
    reason_codes: list[str],
    explanation: str,
    candidate_id: str | None = None,
    quantity: int = 0,
    entry_timestamp=None,
    entry_price: float | None = None,
    profit_target_price: float | None = None,
    protective_stop_price: float | None = None,
) -> CandidateMetaLabel:
    decision_at = snapshot.decisionTimestampUtc or snapshot.decisionTimestamp
    return CandidateMetaLabel(
        labelId=_label_id(snapshot, execution),
        labelVersion=META_LABEL_VERSION,
        snapshotId=snapshot.snapshotId,
        symbol=snapshot.symbol,
        candidateId=candidate_id,
        candidateSide=candidate_side,
        decisionTimestampUtc=decision_at,
        sessionDateNewYork=snapshot.sessionDateNewYork or snapshot.sessionDate,
        entryTimestampUtc=entry_timestamp,
        entryPrice=entry_price,
        profitTargetPrice=profit_target_price,
        protectiveStopPrice=protective_stop_price,
        upperBarrierPrice=profit_target_price,
        lowerBarrierPrice=protective_stop_price,
        firstBarrierHit=first_barrier_hit,
        quantity=quantity,
        spreadDollars=execution.spreadDollars,
        slippagePerShare=execution.slippagePerShare,
        fees=0.0,
        latencyMilliseconds=execution.latencyMilliseconds,
        orderFillBehavior=execution.orderFillBehavior,
        barrierExplanation=explanation,
        eligibleForTraining=False,
        reasonCodes=reason_codes,
        createdAt=decision_at,
        configurationHash=execution.configurationHash,
    )


def _execution_price(raw_price: float, side: Signal, config: MetaLabelExecutionConfig, *, is_entry: bool) -> float:
    half_spread = config.spreadDollars / 2.0
    adverse_cost = half_spread + config.slippagePerShare
    if is_entry:
        return raw_price + adverse_cost if side == Signal.BUY else raw_price - adverse_cost
    return raw_price - adverse_cost if side == Signal.BUY else raw_price + adverse_cost


def _geometry_valid(side: str, entry: float, stop: float, target: float) -> bool:
    if side == Signal.BUY.value:
        return stop < entry < target
    return target < entry < stop


def _side_multiplier(side: str) -> int:
    return 1 if side == Signal.BUY.value else -1


def _signal_value(signal: Signal | str) -> str:
    return signal.value if isinstance(signal, Signal) else str(signal)


def _label_id(snapshot: DecisionSnapshotV2, config: MetaLabelExecutionConfig) -> str:
    payload = f"{snapshot.snapshotId}|{META_LABEL_VERSION}|{config.configurationHash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _barrier_explanation(side: Signal, target_price: float, stop_price: float, vertical_at, config: MetaLabelExecutionConfig) -> str:
    return (
        f"{META_LABEL_VERSION}: {side.value} candidate uses the profit target at {target_price:.4f}, "
        f"protective stop at {stop_price:.4f}, vertical barrier at {vertical_at.isoformat()}, "
        f"entry from {config.orderFillBehavior}, spread {config.spreadDollars:.4f}, "
        f"slippage/share {config.slippagePerShare:.4f}, fees/share {config.feesPerShare:.4f}, "
        f"flat fee/order {config.flatFeePerOrder:.4f}, latency {config.latencyMilliseconds} ms."
    )
