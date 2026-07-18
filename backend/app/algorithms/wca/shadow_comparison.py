"""WCA legacy/backend shadow comparison evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Protocol
from uuid import uuid4

from backend.app.algorithms.wca.configuration import default_baseline_settings
from backend.app.algorithms.wca.contracts import (
    WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION,
    WcaBaselineSettings,
    WcaCandle,
    WcaEvaluateRequest,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaQuote,
    WcaShadowComparisonEvidence,
    WcaShadowFieldComparison,
    WcaSide,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.engine import evaluate_wca_legacy, normalized_signal
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.rollout import WCA_SHADOW_COMPARISON_FIELDS


WCA_SHADOW_COMPARISON_EVIDENCE_VERSION = WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION


class WcaShadowEvidenceRepository(Protocol):
    def write_shadow_comparison_evidence(self, evidence: WcaShadowComparisonEvidence) -> None:
        ...


@dataclass(frozen=True)
class WcaShadowComparisonTolerance:
    numeric: float = 1e-4
    quantity: int = 0
    price: float = 1e-4


def run_wca_shadow_comparison(
    request: WcaEvaluateRequest,
    *,
    repository: WcaShadowEvidenceRepository | None = None,
    tolerance: WcaShadowComparisonTolerance = WcaShadowComparisonTolerance(),
) -> WcaShadowComparisonEvidence:
    legacy = evaluate_wca_legacy(request)
    timestamp = request.timestamp or legacy.decision.decision_timestamp if legacy.decision is not None else datetime.now(timezone.utc)
    baseline = _baseline_from_legacy(request)
    pipeline = run_wca_execution_pipeline(
        WcaExecutionPipelineInput(
            run_id=f"{request.snapshot_id or 'adhoc'}-shadow",
            decision_id=f"{request.snapshot_id or 'adhoc'}-backend-shadow",
            order_intent_id=f"{request.snapshot_id or 'adhoc'}-backend-shadow-intent",
            snapshot=_market_snapshot(request, timestamp),
            configuration_version="wca_shadow_backend_comparison",
            baseline=baseline,
            weight_snapshot=_weight_snapshot(request, timestamp),
            account_equity=(request.sizing_inputs.account_equity if request.sizing_inputs else request.trading_settings.starting_capital),
            available_buying_power=_available_buying_power(request),
            global_gate_quantity_cap=request.trading_settings.max_allowed_shares or None,
            approved_risk_budget=None,
            estimated_cost_per_share=request.trading_settings.slippage_per_share,
            estimated_expectancy_after_costs=0.01,
        ),
        voters=tuple(_LegacySnapshotVoter(row) for row in request.strategy_signals),
    )
    legacy_payload = _legacy_payload(request, legacy)
    backend_payload = _backend_payload(pipeline.decision)
    comparisons = tuple(
        _compare_field(field, legacy_payload.get(field), backend_payload.get(field), tolerance)
        for field in WCA_SHADOW_COMPARISON_FIELDS
    )
    mismatches = tuple(row.field for row in comparisons if not row.matched)
    within_tolerance = not mismatches
    evidence = WcaShadowComparisonEvidence(
        evidence_id=f"wca-shadow-evidence-{uuid4().hex}",
        evidence_version=WCA_SHADOW_COMPARISON_EVIDENCE_VERSION,
        snapshot_id=request.snapshot_id or pipeline.decision.decision_id,
        symbol=request.symbol,
        evaluated_at=timestamp.astimezone(timezone.utc),
        compared_fields=WCA_SHADOW_COMPARISON_FIELDS,
        field_comparisons=comparisons,
        mismatched_fields=mismatches,
        within_tolerance=within_tolerance,
        rollout_phase="legacy_parity",
        rollout_phase_passed=within_tolerance,
        submission_allowed=False,
        legacy_result=legacy_payload,
        backend_result=backend_payload,
        reason_codes=(
            "wca.shadow_comparison.evidence_recorded",
            "wca.shadow_comparison.no_submission",
            "wca.shadow_comparison.within_tolerance" if within_tolerance else "wca.shadow_comparison.tolerance_failed",
        ),
        explanation="Legacy WCA and backend WCA were evaluated side by side without submitting orders.",
    )
    if repository is not None:
        repository.write_shadow_comparison_evidence(evidence)
    return evidence


class _LegacySnapshotVoter:
    def __init__(self, row) -> None:
        self.strategy_id = row.key
        self.family = row.family
        self.version = "legacy_shadow_snapshot_v1"
        self.name = row.name
        self.row = row

    def evaluate(self, market: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        signal = _side_from_legacy_signal(self.row.signal)
        return WcaStrategyEvaluation(
            strategy_id=self.row.key,
            strategy_version="legacy_shadow_snapshot_v1",
            name=self.row.name,
            status=WcaEvaluationStatus.ACTIVE if signal != WcaSide.HOLD else WcaEvaluationStatus.NOT_APPLICABLE,
            signal=signal,
            confidence=self.row.confidence,
            raw_confidence=self.row.confidence,
            calibrated_confidence=self.row.confidence,
            direction=signal,
            applicability=WcaEvaluationStatus.ACTIVE if signal != WcaSide.HOLD else WcaEvaluationStatus.NOT_APPLICABLE,
            evidence_strength=self.row.confidence if signal != WcaSide.HOLD else 0,
            data_quality_status=WcaEvaluationStatus.ACTIVE,
            base_weight=self.row.base_weight,
            effective_weight=self.row.effective_weight,
            contribution=round(self.row.direction * self.row.effective_weight * self.row.confidence, 10),
            reason_codes=("wca.shadow_comparison.legacy_strategy_snapshot",),
            explanation=self.row.reason,
        )


def _legacy_payload(request: WcaEvaluateRequest, response) -> dict[str, Any]:
    sizing = response.sizing_result
    price = _request_price(request)
    stop = _stop(response.proposed_order, response.signal, sizing, price)
    return {
        "strategy_outputs": {
            row.key: {
                "signal": _side_from_legacy_signal(row.signal).value,
                "confidence": row.confidence,
                "effective_weight": row.effective_weight,
            }
            for row in response.strategy_evaluations
        },
        "scores": {
            "buy": response.buy_score,
            "sell": response.sell_score,
            "net": response.net_score,
            "normalized": response.normalized_net_score,
        },
        "decision": _canonical_decision(response.signal),
        "quantity": sizing.final_quantity,
        "stop": stop,
        "target": _target(request, response.proposed_order, response.signal, sizing, price),
        "gate_results": {row.label: row.status for row in response.local_gate_result},
    }


def _backend_payload(decision) -> dict[str, Any]:
    order = decision.proposed_order
    return {
        "strategy_outputs": {
            row.strategy_id: {
                "signal": _side_value(row.signal),
                "confidence": row.confidence,
                "effective_weight": row.effective_weight,
            }
            for row in decision.aggregation.strategy_evaluations
        },
        "scores": {
            "buy": decision.aggregation.buy_score,
            "sell": decision.aggregation.sell_score,
            "net": decision.aggregation.net_score,
            "normalized": decision.aggregation.normalized_net_score,
        },
        "decision": _side_value(decision.aggregation.post_local_gate_decision),
        "quantity": decision.sizing.final_quantity,
        "stop": order.stop_price if order is not None else decision.sizing.stop_price,
        "target": order.target_price if order is not None else decision.sizing.target_price,
        "gate_results": {gate.gate_id: gate.status for gate in decision.local_gates},
    }


def _compare_field(field: str, legacy_value: Any, backend_value: Any, tolerance: WcaShadowComparisonTolerance) -> WcaShadowFieldComparison:
    matched = _values_match(legacy_value, backend_value, _field_tolerance(field, tolerance))
    return WcaShadowFieldComparison(
        field=field,
        legacy_value=legacy_value,
        backend_value=backend_value,
        matched=matched,
        tolerance=_field_tolerance(field, tolerance),
        reason_codes=(f"wca.shadow_comparison.{field}.{'matched' if matched else 'mismatch'}",),
    )


def _values_match(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        return all(_values_match(left.get(key), right.get(key), tolerance) for key in keys)
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            left_number = float(left)
            right_number = float(right)
        except (TypeError, ValueError):
            return False
        if not isfinite(left_number) or not isfinite(right_number):
            return False
        return abs(left_number - right_number) <= tolerance
    return left == right


def _field_tolerance(field: str, tolerance: WcaShadowComparisonTolerance) -> float:
    if field == "quantity":
        return float(tolerance.quantity)
    if field in {"stop", "target"}:
        return tolerance.price
    return tolerance.numeric


def _market_snapshot(request: WcaEvaluateRequest, timestamp: datetime) -> WcaMarketSnapshot:
    market = request.market_snapshot or {}
    close = _request_price(request)
    atr = float(market.get("atr") or (request.sizing_inputs.atr if request.sizing_inputs else max(close * 0.01, 0.01)))
    volume = float(market.get("latestVolume") or market.get("latest_volume") or (request.sizing_inputs.latest_volume if request.sizing_inputs else 1))
    candle = WcaCandle(
        timestamp=timestamp.astimezone(timezone.utc),
        open=close,
        high=close + max(atr / 2, 0.01),
        low=max(0.01, close - max(atr / 2, 0.01)),
        close=close,
        volume=volume,
    )
    spread = max(0.01, close * 0.0001)
    return WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=candle.timestamp,
        decision_timestamp=candle.timestamp,
        candles=(candle,),
        quote=WcaQuote(timestamp=candle.timestamp, bid=max(0.01, close - spread / 2), ask=close + spread / 2),
        source="wca_shadow_comparison",
        reason_codes=("wca.shadow_comparison.market_snapshot",),
    )


def _baseline_from_legacy(request: WcaEvaluateRequest) -> WcaBaselineSettings:
    trading = request.trading_settings
    decision = request.decision_settings
    return default_baseline_settings().model_copy(
        update={
            "minimum_score": min(abs(decision.buy_threshold), abs(decision.sell_threshold)),
            "minimum_directional_agreement": decision.minimum_directional_agreement,
            "minimum_average_confidence": decision.minimum_average_confidence,
            "minimum_active_strategies": decision.minimum_active_strategies,
            "base_risk_percent": trading.base_risk_percent,
            "order_allocation_percent": trading.order_allocation_percent,
            "daily_allocation_percent": trading.daily_allocation_percent,
            "max_position_percent": trading.max_position_percent,
            "max_daily_trades": trading.max_daily_trades,
            "atr_stop_multiplier": trading.atr_stop_multiplier,
            "minimum_stop_distance_percent": trading.minimum_stop_distance_percent,
            "take_profit_r": trading.take_profit_r,
            "assumed_slippage_per_share": trading.slippage_per_share,
            "max_participation_percent": trading.max_participation_percent,
            "max_allowed_shares": trading.max_allowed_shares,
            "hard_max_risk_percent": max(trading.base_risk_percent, 1),
            "hard_max_order_allocation_percent": max(trading.order_allocation_percent, 100),
            "hard_max_daily_allocation_percent": max(trading.daily_allocation_percent, 100),
            "hard_max_position_percent": max(trading.max_position_percent, 100),
            "hard_max_allowed_shares": trading.max_allowed_shares,
            "pyramiding_enabled": trading.pyramiding_enabled,
        }
    )


def _weight_snapshot(request: WcaEvaluateRequest, timestamp: datetime) -> WcaWeightSnapshot:
    weights = {row.key: row.effective_weight for row in request.strategy_signals}
    total = sum(weights.values())
    if total <= 0:
        weights = {row.key: 1 / len(request.strategy_signals) for row in request.strategy_signals}
    elif abs(total - 1.0) > 1e-6:
        weights = {key: value / total for key, value in weights.items()}
    return WcaWeightSnapshot(weight_version="wca_shadow_legacy_weights", created_at=timestamp, weights=weights)


def _available_buying_power(request: WcaEvaluateRequest) -> float:
    if request.sizing_inputs is not None:
        return request.sizing_inputs.account_equity
    return request.trading_settings.starting_capital


def _side_from_legacy_signal(signal: str) -> WcaSide:
    normalized = normalized_signal(signal)
    if normalized == "buy":
        return WcaSide.BUY
    if normalized == "sell":
        return WcaSide.SELL
    return WcaSide.HOLD


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _canonical_decision(signal: str) -> str:
    if signal == "Buy":
        return WcaSide.BUY.value
    if signal == "Sell":
        return WcaSide.SELL.value
    return WcaSide.HOLD.value


def _request_price(request: WcaEvaluateRequest) -> float:
    market = request.market_snapshot or {}
    if market.get("close"):
        return float(market["close"])
    if request.sizing_inputs is not None:
        return request.sizing_inputs.price
    return 1.0


def _stop(order, signal: str, sizing, price: float) -> float | None:
    if order is not None and order.stop_price is not None:
        return order.stop_price
    if signal == "Buy":
        return max(0.01, price - sizing.stop_distance)
    if signal == "Sell":
        return price + sizing.stop_distance
    return None


def _target(request: WcaEvaluateRequest, order, signal: str, sizing, price: float) -> float | None:
    if order is not None and order.target_price is not None:
        return order.target_price
    distance = sizing.stop_distance * request.trading_settings.take_profit_r
    if signal == "Buy":
        return price + distance
    if signal == "Sell":
        return max(0.01, price - distance)
    return None


__all__ = [
    "WCA_SHADOW_COMPARISON_EVIDENCE_VERSION",
    "WcaShadowComparisonTolerance",
    "WcaShadowEvidenceRepository",
    "run_wca_shadow_comparison",
]
