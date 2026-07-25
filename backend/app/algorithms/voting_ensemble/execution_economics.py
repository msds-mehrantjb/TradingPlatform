from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.trading_settings.models import VotingEnsembleOneMinuteSettings
from backend.app.domain.models import EnsembleDecision, Signal, TradeCandidate


VOTING_ENSEMBLE_EXECUTION_ECONOMICS_VERSION = "voting_ensemble_execution_economics_v1"


class VotingEnsembleLatencyMeasurements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marketDataAgeSeconds: float = Field(ge=0.0)
    quoteAgeSeconds: float = Field(ge=0.0)
    decisionAgeSeconds: float = Field(ge=0.0)
    snapshotBuildDurationMs: float = Field(ge=0.0)
    strategyEvaluationDurationMs: float = Field(ge=0.0)
    aggregationDurationMs: float = Field(ge=0.0)
    gateDurationMs: float = Field(ge=0.0)
    orderPlanningDurationMs: float = Field(ge=0.0)
    queueDelayMs: float = Field(ge=0.0)
    routingDurationMs: float = Field(ge=0.0)
    brokerAcknowledgementDurationMs: float = Field(ge=0.0)
    fillDurationMs: float = Field(ge=0.0)
    clockSkewMs: float = Field(ge=0.0)
    decisionDeadlineExpired: bool


class VotingEnsembleExecutionEconomics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    economicsVersion: str = VOTING_ENSEMBLE_EXECUTION_ECONOMICS_VERSION
    expectedSpreadCostDollars: float = Field(ge=0.0)
    expectedSlippageDollars: float = Field(ge=0.0)
    expectedFeesDollars: float = Field(ge=0.0)
    expectedRegulatorySellSideCostsDollars: float = Field(ge=0.0)
    expectedMarketImpactDollars: float = Field(ge=0.0)
    expectedTotalRoundTripCostDollars: float = Field(ge=0.0)
    predictedGrossEdgeDollars: float
    predictedNetEdgeDollars: float
    edgeToCostRatio: float
    availableFillableQuantity: int = Field(ge=0)
    participationRate: float = Field(ge=0.0)
    adverseSelectionRisk: float = Field(ge=0.0, le=1.0)
    minimumNetEdgeDollars: float = Field(ge=0.0)
    minimumEdgeToCostRatio: float = Field(ge=0.0)
    maximumSpreadBps: float = Field(ge=0.0)
    maximumSpreadDollars: float = Field(ge=0.0)
    maximumSlippageDollars: float = Field(ge=0.0)
    minimumFillableQuantity: int = Field(ge=0)
    latency: VotingEnsembleLatencyMeasurements
    sourceQuote: dict[str, Any]
    assumptions: dict[str, Any]
    reasonCodes: tuple[str, ...]
    configurationHash: str


def build_execution_economics(
    *,
    snapshot: VotingEnsembleEvaluationSnapshot,
    decision: EnsembleDecision,
    candidate: TradeCandidate | None,
    settings: VotingEnsembleOneMinuteSettings,
    latency_measurements: dict[str, float | bool],
) -> VotingEnsembleExecutionEconomics | None:
    if candidate is None or snapshot.nbbo is None:
        return None
    side = Signal(candidate.signal)
    quantity = max(int(candidate.quantity or 0), 0)
    nbbo = snapshot.nbbo
    entry_price = nbbo.ask if side == Signal.BUY else nbbo.bid
    fillable_quantity = int(max(0.0, nbbo.askSize if side == Signal.BUY else nbbo.bidSize))
    current_volume = _number(snapshot.features.model_dump(mode="json"), "volumeCurrent") or _number(snapshot.operationalHealthSnapshot, "currentOneMinuteVolume") or 100_000.0
    participation_rate = quantity / max(current_volume, 1.0)
    slippage_per_share = _number(snapshot.operationalHealthSnapshot, "expectedSlippageDollars")
    if slippage_per_share is None:
        slippage_per_share = (nbbo.spreadDollars / 2.0) + float(settings.slippageLimits.slippagePerShare)
    impact_per_share = max(0.0, entry_price * min(0.0025, participation_rate * 0.10))
    expense = settings.expenseModel
    fees_per_share = (expense.commissionPerSharePerSide * 2.0) + (expense.additionalLiquidityCostPerSharePerSide * 2.0)
    regulatory_sell = (entry_price * expense.secFeeRateOnSellNotional) + min(expense.finraTafMaxPerTrade, expense.finraTafPerSellShare * quantity) / max(quantity, 1)
    spread_cost = nbbo.spreadDollars
    total_cost = spread_cost + (float(slippage_per_share) * 2.0) + fees_per_share + regulatory_sell + impact_per_share
    gross_edge = _number(snapshot.operationalHealthSnapshot, "predictedGrossEdgeDollars")
    if gross_edge is None:
        gross_edge = abs(float(decision.finalScore)) * entry_price * 0.005
    net_edge = float(gross_edge) - total_cost
    edge_to_cost = float(gross_edge) / total_cost if total_cost > 0 else 999.0
    minimum_net_edge = _number(snapshot.operationalHealthSnapshot, "minimumNetEdgeDollars")
    if minimum_net_edge is None:
        minimum_net_edge = max(0.01, float(settings.netEdgeRequirements.minimumNetEdgeR) * max(abs(candidate.entryPrice - (candidate.stopPrice or candidate.entryPrice)), 0.01))
    maximum_slippage = min(float(settings.slippageLimits.maxSlippagePerShare), float(settings.resolvedTradingProfile.maximumSlippagePerShare))
    decision_age_seconds = _number(snapshot.operationalHealthSnapshot, "decisionAgeSeconds")
    latency = VotingEnsembleLatencyMeasurements(
        marketDataAgeSeconds=nbbo.marketDataAgeSeconds,
        quoteAgeSeconds=nbbo.quoteAgeSeconds,
        decisionAgeSeconds=max(0.0, float(decision_age_seconds or 0.0)),
        snapshotBuildDurationMs=float(latency_measurements.get("snapshotBuildDurationMs") or 0.0),
        strategyEvaluationDurationMs=float(latency_measurements.get("strategyEvaluationDurationMs") or 0.0),
        aggregationDurationMs=float(latency_measurements.get("aggregationDurationMs") or 0.0),
        gateDurationMs=float(latency_measurements.get("gateDurationMs") or 0.0),
        orderPlanningDurationMs=float(latency_measurements.get("orderPlanningDurationMs") or _number(snapshot.operationalHealthSnapshot, "orderPlanningDurationMs") or 0.0),
        queueDelayMs=float(_number(snapshot.operationalHealthSnapshot, "queueDelayMs") or 0.0),
        routingDurationMs=float(_number(snapshot.operationalHealthSnapshot, "routingDurationMs") or 0.0),
        brokerAcknowledgementDurationMs=float(_number(snapshot.operationalHealthSnapshot, "brokerAcknowledgementDurationMs") or 0.0),
        fillDurationMs=float(_number(snapshot.operationalHealthSnapshot, "fillDurationMs") or 0.0),
        clockSkewMs=float(_number(snapshot.operationalHealthSnapshot, "clockSkewMs") or 0.0),
        decisionDeadlineExpired=bool(latency_measurements.get("decisionDeadlineExpired", False)),
    )
    payload = {
        "settingsHash": settings.configurationHash,
        "snapshotHash": snapshot.snapshotHash,
        "decisionId": decision.decisionId,
        "quantity": quantity,
        "entryPrice": round(entry_price, 4),
        "totalCost": round(total_cost, 6),
        "grossEdge": round(float(gross_edge), 6),
        "minimumNetEdge": round(float(minimum_net_edge), 6),
    }
    return VotingEnsembleExecutionEconomics(
        expectedSpreadCostDollars=round(spread_cost, 6),
        expectedSlippageDollars=round(float(slippage_per_share) * 2.0, 6),
        expectedFeesDollars=round(fees_per_share, 6),
        expectedRegulatorySellSideCostsDollars=round(regulatory_sell, 6),
        expectedMarketImpactDollars=round(impact_per_share, 6),
        expectedTotalRoundTripCostDollars=round(total_cost, 6),
        predictedGrossEdgeDollars=round(float(gross_edge), 6),
        predictedNetEdgeDollars=round(net_edge, 6),
        edgeToCostRatio=round(edge_to_cost, 6),
        availableFillableQuantity=fillable_quantity,
        participationRate=round(participation_rate, 8),
        adverseSelectionRisk=round(_adverse_selection_risk(nbbo.spreadBasisPoints, nbbo.quoteAgeSeconds, participation_rate), 6),
        minimumNetEdgeDollars=round(float(minimum_net_edge), 6),
        minimumEdgeToCostRatio=float(settings.resolvedTradingProfile.minimumEdgeToCostRatio),
        maximumSpreadBps=float(settings.resolvedTradingProfile.maximumSpreadBps),
        maximumSpreadDollars=float(settings.resolvedTradingProfile.maximumSpreadDollars),
        maximumSlippageDollars=round(maximum_slippage, 6),
        minimumFillableQuantity=max(1, int(_number(snapshot.operationalHealthSnapshot, "minimumFillableQuantity") or quantity or 1)),
        latency=latency,
        sourceQuote=nbbo.model_dump(mode="json"),
        assumptions={
            "grossEdgeSource": "operationalHealthSnapshot.predictedGrossEdgeDollars or abs(finalScore)*entry*0.005",
            "spreadCost": "one round-trip half-spread entry plus exit approximated as current NBBO spread",
            "slippage": "per-side expected slippage doubled for round trip",
            "marketImpact": "entry_price * min(0.0025, participation_rate * 0.10)",
        },
        reasonCodes=("voting_ensemble.execution_economics.point_in_time_quote",),
        configurationHash=hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16],
    )


def _adverse_selection_risk(spread_bps: float, quote_age_seconds: float, participation_rate: float) -> float:
    return max(0.0, min(1.0, (spread_bps / 50.0) + (quote_age_seconds / 120.0) + min(participation_rate * 10.0, 0.25)))


def _number(payload: dict[str, Any], key: str) -> float | None:
    try:
        value = payload.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _utc(timestamp: datetime) -> datetime:
    return timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
