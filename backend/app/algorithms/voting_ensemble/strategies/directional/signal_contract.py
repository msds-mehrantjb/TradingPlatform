"""Snapshot-native directional strategy signal contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.algorithms.voting_ensemble.models import AlgoSignal, SignalFamily


class DirectionalStrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategyId: str
    strategyName: str
    strategyVersion: str
    family: SignalFamily
    signal: AlgoSignal
    confidence: float = Field(ge=0.0, le=1.0)
    eligible: bool
    dataReady: bool
    evidence: tuple[str, ...]
    reasonCodes: tuple[str, ...]
    evaluatedAt: datetime
    correlationId: str
    eventCorrelationId: str
    setupId: str
    evidenceRole: str
    referenceLevelId: str | None = None
    triggerTimestamp: str
    confirmationTimestamp: str
    features: dict[str, int | float | bool | str] = Field(default_factory=dict)


class DirectionalStrategyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minConfidence: float = Field(default=0.05, ge=0.0, le=1.0)


def hold_signal(
    *,
    strategy_id: str,
    strategy_name: str,
    strategy_version: str,
    family: SignalFamily,
    evaluated_at: datetime,
    correlation_id: str,
    reason: str,
    reason_code: str,
    confidence: float = 0.15,
    data_ready: bool = True,
    features: dict[str, Any] | None = None,
) -> DirectionalStrategySignal:
    contract = _correlation_contract(
        strategy_id=strategy_id,
        family=family,
        evaluated_at=evaluated_at,
        correlation_id=correlation_id,
        features=features or {},
    )
    return DirectionalStrategySignal(
        strategyId=strategy_id,
        strategyName=strategy_name,
        strategyVersion=strategy_version,
        family=family,
        signal="Hold",
        confidence=confidence,
        eligible=False,
        dataReady=data_ready,
        evidence=(reason,),
        reasonCodes=(reason_code,),
        evaluatedAt=evaluated_at,
        correlationId=correlation_id,
        eventCorrelationId=contract["eventCorrelationId"],
        setupId=contract["setupId"],
        evidenceRole=contract["evidenceRole"],
        referenceLevelId=contract.get("referenceLevelId") or None,
        triggerTimestamp=contract["triggerTimestamp"],
        confirmationTimestamp=contract["confirmationTimestamp"],
        features={key: value for key, value in {**(features or {}), **contract}.items() if isinstance(value, (int, float, bool, str))},
    )


def directional_signal(
    *,
    strategy_id: str,
    strategy_name: str,
    strategy_version: str,
    family: SignalFamily,
    signal: Literal["Buy", "Sell"],
    confidence: float,
    evaluated_at: datetime,
    correlation_id: str,
    evidence: tuple[str, ...],
    reason_codes: tuple[str, ...],
    features: dict[str, Any] | None = None,
) -> DirectionalStrategySignal:
    contract = _correlation_contract(
        strategy_id=strategy_id,
        family=family,
        evaluated_at=evaluated_at,
        correlation_id=correlation_id,
        features=features or {},
    )
    return DirectionalStrategySignal(
        strategyId=strategy_id,
        strategyName=strategy_name,
        strategyVersion=strategy_version,
        family=family,
        signal=signal,
        confidence=confidence,
        eligible=True,
        dataReady=True,
        evidence=evidence,
        reasonCodes=reason_codes,
        evaluatedAt=evaluated_at,
        correlationId=correlation_id,
        eventCorrelationId=contract["eventCorrelationId"],
        setupId=contract["setupId"],
        evidenceRole=contract["evidenceRole"],
        referenceLevelId=contract.get("referenceLevelId") or None,
        triggerTimestamp=contract["triggerTimestamp"],
        confirmationTimestamp=contract["confirmationTimestamp"],
        features={key: value for key, value in {**(features or {}), **contract}.items() if isinstance(value, (int, float, bool, str))},
    )


def _correlation_contract(
    *,
    strategy_id: str,
    family: str,
    evaluated_at: datetime,
    correlation_id: str,
    features: dict[str, Any],
) -> dict[str, str]:
    evaluated = evaluated_at.isoformat()
    event_id = _first_string(features, ("eventCorrelationId", "trendEventCorrelationId", "correlationId")) or correlation_id
    setup_id = _first_string(features, ("setupId", "eventId")) or f"{strategy_id}:{event_id}"
    role = _first_string(features, ("evidenceRole", "trendEvidenceRole", "strategyEvidenceRole")) or _default_evidence_role(strategy_id, family)
    trigger = _first_string(features, ("triggerTimestamp", "triggerTime", "setupTimestamp")) or evaluated
    confirmation = _first_string(features, ("confirmationTimestamp", "confirmationTime")) or trigger
    reference = _first_string(features, ("referenceLevelId", "levelId", "openingRangeBoundaryId", "gapEventId")) or ""
    return {
        "eventCorrelationId": event_id,
        "setupId": setup_id,
        "evidenceRole": role,
        "referenceLevelId": reference,
        "triggerTimestamp": trigger,
        "confirmationTimestamp": confirmation,
    }


def _first_string(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _default_evidence_role(strategy_id: str, family: str) -> str:
    if strategy_id == "multi_timeframe_trend_alignment":
        return "timeframe_agreement"
    if strategy_id == "first_pullback_after_open":
        return "opening_pullback"
    if strategy_id == "vwap_trend_continuation":
        return "vwap_continuation"
    if strategy_id == "failed_breakout_reversal":
        return "failed_breakout_level_rejection"
    if strategy_id == "liquidity_sweep_reversal":
        return "liquidity_sweep_level_rejection"
    if strategy_id == "bollinger_band_reversion":
        return "bollinger_band_overextension"
    if strategy_id == "atr_overextension_reversion":
        return "atr_overextension"
    if strategy_id == "opening_range_breakout":
        return "opening_range_break"
    if strategy_id == "gap_continuation_fade":
        return "opening_gap_session"
    return f"{family}_evidence"
