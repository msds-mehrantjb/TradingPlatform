"""Base WCA modifier contract."""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.algorithms.wca.contracts import WcaEvaluationStatus, WcaMarketSnapshot, WcaModifierEvaluation
from backend.app.algorithms.wca.strategies.indicators import completed_candles


class WcaModifier(Protocol):
    modifier_id: str
    name: str
    family: str

    def evaluate(self, snapshot: WcaMarketSnapshot, settings: Any | None = None) -> WcaModifierEvaluation:
        ...


def active_modifier(
    modifier: WcaModifier,
    multiplier: float,
    reason_code: str,
    explanation: str,
    *,
    settings: Any | None = None,
    confidence_multiplier: float | None = None,
    weight_multiplier: float | None = None,
    risk_multiplier: float = 1.0,
    position_size_multiplier: float = 1.0,
    entry_requirement_multiplier: float = 1.0,
    market_status_contributions: dict[str, float | str] | None = None,
) -> WcaModifierEvaluation:
    return _evaluation(
        modifier,
        WcaEvaluationStatus.ACTIVE,
        multiplier,
        reason_code,
        explanation,
        settings=settings,
        confidence_multiplier=confidence_multiplier,
        weight_multiplier=weight_multiplier,
        risk_multiplier=risk_multiplier,
        position_size_multiplier=position_size_multiplier,
        entry_requirement_multiplier=entry_requirement_multiplier,
        market_status_contributions=market_status_contributions,
    )


def not_applicable_modifier(modifier: WcaModifier, reason_code: str, explanation: str, *, settings: Any | None = None) -> WcaModifierEvaluation:
    return _evaluation(modifier, WcaEvaluationStatus.NOT_APPLICABLE, 1.0, reason_code, explanation, settings=settings)


def invalid_modifier(modifier: WcaModifier, reason_code: str, explanation: str, *, settings: Any | None = None) -> WcaModifierEvaluation:
    return _evaluation(modifier, WcaEvaluationStatus.INVALID, 1.0, reason_code, explanation, settings=settings)


def invalid_snapshot_result(snapshot: WcaMarketSnapshot, modifier: WcaModifier) -> WcaModifierEvaluation | None:
    if not snapshot.data_ready:
        return invalid_modifier(modifier, "wca.modifier.data_not_ready", "Market snapshot is not data-ready.")
    candles = completed_candles(snapshot)
    if not candles:
        return invalid_modifier(modifier, "wca.modifier.missing_candles", "No completed candles are available.")
    if any(candle.close <= 0 or candle.high < candle.low or candle.volume < 0 for candle in candles):
        return invalid_modifier(modifier, "wca.modifier.invalid_candle", "Snapshot contains invalid candle data.")
    return None


def _evaluation(
    modifier: WcaModifier,
    status: WcaEvaluationStatus,
    multiplier: float,
    reason_code: str,
    explanation: str,
    *,
    settings: Any | None = None,
    confidence_multiplier: float | None = None,
    weight_multiplier: float | None = None,
    risk_multiplier: float = 1.0,
    position_size_multiplier: float = 1.0,
    entry_requirement_multiplier: float = 1.0,
    market_status_contributions: dict[str, float | str] | None = None,
) -> WcaModifierEvaluation:
    confidence = _bounded(settings, "confidence", multiplier if confidence_multiplier is None else confidence_multiplier)
    weight = _bounded(settings, "weight", multiplier if weight_multiplier is None else weight_multiplier)
    risk = _bounded(settings, "risk", risk_multiplier)
    size = _bounded(settings, "position_size", position_size_multiplier)
    entry = _bounded(settings, "entry_requirement", entry_requirement_multiplier)
    return WcaModifierEvaluation(
        modifier_id=modifier.modifier_id,
        status=status,
        multiplier=confidence,
        confidence_multiplier=confidence,
        weight_multiplier=weight,
        risk_multiplier=risk,
        position_size_multiplier=size,
        entry_requirement_multiplier=entry,
        market_status_contributions=market_status_contributions or {},
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def _bounded(settings: Any | None, label: str, value: float) -> float:
    lower = getattr(settings, f"minimum_{label}_multiplier", 0.0)
    upper = getattr(settings, f"maximum_{label}_multiplier", 2.0)
    return round(max(lower, min(upper, value)), 4)
