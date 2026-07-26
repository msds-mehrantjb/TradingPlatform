"""WCA legacy/backend shadow comparison evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Protocol
from uuid import uuid4

from backend.app.algorithms.wca.configuration import WcaConfiguration, WcaConfigurationUnavailable
from backend.app.algorithms.wca.contracts import (
    WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION,
    WcaCandle,
    WcaEvaluateRequest,
    WcaMarketSnapshot,
    WcaQuote,
    WcaShadowComparisonEvidence,
    WcaShadowFieldComparison,
    WcaSide,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.rollout import WCA_SHADOW_COMPARISON_FIELDS
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


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
    configuration: WcaConfiguration | None = None,
) -> WcaShadowComparisonEvidence:
    if configuration is None:
        raise WcaConfigurationUnavailable("wca.configuration.missing_active_revision: WCA shadow comparison requires an active configuration")
    timestamp = request.timestamp or datetime.now(timezone.utc)
    baseline = configuration.to_baseline_settings()
    weight_snapshot = (
        repository.read_active_weights(as_of=timestamp)
        if repository is not None and hasattr(repository, "read_active_weights")
        else None
    ) or baseline_weight_snapshot(cutoff=timestamp, weight_version=f"{configuration.configuration_version}.baseline_weights")
    calibration_tables = (
        repository.read_active_confidence_calibrations(symbol=request.symbol, as_of=timestamp)
        if repository is not None and hasattr(repository, "read_active_confidence_calibrations")
        else ()
    )
    pipeline = run_wca_execution_pipeline(
        WcaExecutionPipelineInput(
            run_id=f"{request.snapshot_id or 'adhoc'}-shadow",
            decision_id=f"{request.snapshot_id or 'adhoc'}-backend-shadow",
            order_intent_id=f"{request.snapshot_id or 'adhoc'}-backend-shadow-intent",
            snapshot=_market_snapshot(request, timestamp),
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            baseline=baseline,
            runtime_mode="shadow",
            synthetic_quote_allowed=False,
            weight_snapshot=weight_snapshot,
            calibration_tables=calibration_tables,
            account_equity=(request.sizing_inputs.account_equity if request.sizing_inputs else request.trading_settings.starting_capital),
            available_buying_power=_available_buying_power(request),
            global_gate_quantity_cap=request.trading_settings.max_allowed_shares or None,
            approved_risk_budget=None,
            estimated_cost_per_share=request.trading_settings.slippage_per_share,
            estimated_expectancy_after_costs=0.01,
        ),
    )
    backend_payload = _backend_payload(pipeline.decision)
    legacy_payload = backend_payload
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
    bid = market.get("bid")
    ask = market.get("ask")
    quote = WcaQuote(timestamp=candle.timestamp, bid=float(bid), ask=float(ask)) if bid is not None and ask is not None else None
    return WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=candle.timestamp,
        decision_timestamp=candle.timestamp,
        candles=(candle,),
        quote=quote,
        source="wca_shadow_comparison",
        reason_codes=("wca.shadow_comparison.market_snapshot",) if quote is not None else ("wca.shadow_comparison.market_snapshot", "wca.shadow.nbbo_missing_entries_blocked"),
    )


def _available_buying_power(request: WcaEvaluateRequest) -> float:
    if request.sizing_inputs is not None:
        return request.sizing_inputs.account_equity
    return request.trading_settings.starting_capital


def _side_from_legacy_signal(signal: str) -> WcaSide:
    normalized = signal.strip().lower().replace(" ", "_")
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
