"""Base WCA modifier contract."""

from __future__ import annotations

from typing import Protocol

from backend.app.algorithms.wca.contracts import WcaEvaluationStatus, WcaMarketSnapshot, WcaModifierEvaluation
from backend.app.algorithms.wca.strategies.indicators import completed_candles


class WcaModifier(Protocol):
    modifier_id: str
    name: str
    family: str

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaModifierEvaluation:
        ...


def active_modifier(modifier: WcaModifier, multiplier: float, reason_code: str, explanation: str) -> WcaModifierEvaluation:
    return _evaluation(modifier, WcaEvaluationStatus.ACTIVE, multiplier, reason_code, explanation)


def not_applicable_modifier(modifier: WcaModifier, reason_code: str, explanation: str) -> WcaModifierEvaluation:
    return _evaluation(modifier, WcaEvaluationStatus.NOT_APPLICABLE, 1.0, reason_code, explanation)


def invalid_modifier(modifier: WcaModifier, reason_code: str, explanation: str) -> WcaModifierEvaluation:
    return _evaluation(modifier, WcaEvaluationStatus.INVALID, 1.0, reason_code, explanation)


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
) -> WcaModifierEvaluation:
    return WcaModifierEvaluation(
        modifier_id=modifier.modifier_id,
        status=status,
        multiplier=round(max(0.0, min(2.0, multiplier)), 4),
        reason_codes=(reason_code,),
        explanation=explanation,
    )
