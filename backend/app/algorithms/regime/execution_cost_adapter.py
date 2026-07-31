"""Read-only execution-cost adapter for Regime entry gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from backend.app.execution.cost_model import EXECUTION_COST_MODEL_SERVICE, EXECUTION_COST_MODEL_VERSION, ExecutionCostModelService


REGIME_EXECUTION_COST_ADAPTER_VERSION = "regime_execution_cost_adapter_v1"


@dataclass(frozen=True)
class RegimeExecutionCostEstimate:
    adapter_version: str
    model_version: str
    model_status: str
    model_applied: bool
    artifact_id: str | None
    conservative_fallback_approved: bool
    expected_gross_edge_bps: float
    expected_spread_cost_bps: float
    expected_slippage_bps: float
    expected_fees_bps: float
    expected_regulatory_fees_bps: float
    expected_market_impact_bps: float
    adverse_selection_allowance_bps: float
    uncertainty_buffer_bps: float
    expected_net_edge_bps: float
    total_cost_bps: float
    cost_to_edge_ratio: float | None
    maximum_cost_to_edge_ratio: float
    maximum_slippage_bps: float
    minimum_net_edge_bps: float
    stale: bool
    unavailable: bool
    evaluated_at: str
    reason_codes: tuple[str, ...]
    raw_model_estimate: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload["reasonCodes"] = list(self.reason_codes)
        payload["adapterVersion"] = self.adapter_version
        payload["modelVersion"] = self.model_version
        payload["modelStatus"] = self.model_status
        payload["modelApplied"] = self.model_applied
        payload["artifactId"] = self.artifact_id
        payload["conservativeFallbackApproved"] = self.conservative_fallback_approved
        payload["expectedGrossEdgeBps"] = self.expected_gross_edge_bps
        payload["expectedSpreadCostBps"] = self.expected_spread_cost_bps
        payload["expectedSlippageBps"] = self.expected_slippage_bps
        payload["expectedFeesBps"] = self.expected_fees_bps
        payload["expectedRegulatoryFeesBps"] = self.expected_regulatory_fees_bps
        payload["expectedMarketImpactBps"] = self.expected_market_impact_bps
        payload["adverseSelectionAllowanceBps"] = self.adverse_selection_allowance_bps
        payload["uncertaintyBufferBps"] = self.uncertainty_buffer_bps
        payload["expectedNetEdgeBps"] = self.expected_net_edge_bps
        payload["totalCostBps"] = self.total_cost_bps
        payload["costToEdgeRatio"] = self.cost_to_edge_ratio
        return payload


def estimate_regime_execution_cost(
    *,
    symbol: str,
    side: str,
    order_type: str,
    entry_price: float,
    quantity: int,
    expected_gross_edge_bps: float,
    classification,
    settings: dict[str, Any],
    runtime_context: dict[str, Any] | None = None,
    model_service: ExecutionCostModelService = EXECUTION_COST_MODEL_SERVICE,
    evaluated_at: datetime | None = None,
) -> RegimeExecutionCostEstimate:
    context = runtime_context or {}
    liquidity = _record(getattr(classification, "evidence", {}).get("liquidityEvidence"))
    features = _record(getattr(classification, "features", {}))
    quote = _record(context.get("quoteFreshness") or context.get("quote") or liquidity)
    price = max(0.01, float(entry_price or features.get("close") or features.get("lastPrice") or 0.01))
    qty = max(0, int(quantity or 0))
    spread_bps = _nonnegative_number(context.get("spreadBps") or quote.get("spreadBps") or liquidity.get("spreadBps") or features.get("spreadBps"))
    half_spread_bps = _nonnegative_number(context.get("halfSpreadBps") or context.get("entryHalfSpreadBps"))
    if half_spread_bps <= 0 and spread_bps > 0:
        half_spread_bps = spread_bps / 2.0
    slippage_bps = _nonnegative_number(context.get("expectedSlippageBps") or context.get("expectedEntrySlippageBps") or settings.get("maximumSlippageBps"))
    fees_bps = _nonnegative_number(context.get("feesBps") or settings.get("estimatedFeesBps"))
    regulatory_fees_bps = _nonnegative_number(context.get("regulatoryFeesBps") or settings.get("estimatedRegulatoryFeesBps"))
    market_impact_bps = _market_impact_bps(context, settings, quantity=qty)
    adverse_bps = _nonnegative_number(context.get("adverseSelectionAllowanceBps") or context.get("adverseSelectionBufferBps") or settings.get("adverseSelectionBufferBps"))
    uncertainty_bps = _nonnegative_number(context.get("uncertaintyBufferBps") or settings.get("uncertaintyBufferBps"))
    base_cost_bps = half_spread_bps + slippage_bps + fees_bps + regulatory_fees_bps + market_impact_bps + adverse_bps + uncertainty_bps
    fallback = {
        "baseCost": _bps_to_dollars(base_cost_bps, price, qty),
        "totalEstimatedCost": _bps_to_dollars(base_cost_bps, price, qty),
        "expectedSpreadCostBps": half_spread_bps,
        "expectedSlippageBps": slippage_bps,
        "expectedFeesBps": fees_bps,
        "expectedRegulatoryFeesBps": regulatory_fees_bps,
        "expectedMarketImpactBps": market_impact_bps,
        "adverseSelectionAllowanceBps": adverse_bps,
        "uncertaintyBufferBps": uncertainty_bps,
        "fillProbability": 1.0,
        "expectedPartialFillFraction": 1.0,
        "opportunityDecay": 0.0,
        "realizedVsEstimatedCostErrorReserve": uncertainty_bps,
        "featureSnapshot": {
            "spreadBps": spread_bps,
            "quantity": qty,
            "entryPrice": price,
            "quoteAgeMs": quote.get("ageMs") or features.get("quoteAgeMs"),
        },
    }
    raw = model_service.estimate(
        symbol=symbol,
        side=side,
        order_type=order_type,
        feature_snapshot=fallback["featureSnapshot"],
        conservative_fallback=fallback,
    )
    model_total_bps = _dollars_to_bps(_nonnegative_number(raw.get("totalEstimatedCost")), price, qty)
    if raw.get("modelApplied"):
        model_extra_bps = max(0.0, model_total_bps - base_cost_bps)
        uncertainty_bps = max(uncertainty_bps, _nonnegative_number(raw.get("realizedVsEstimatedCostErrorReserve"))) + model_extra_bps
        base_cost_bps = max(base_cost_bps, model_total_bps)
    total_cost_bps = half_spread_bps + slippage_bps + fees_bps + regulatory_fees_bps + market_impact_bps + adverse_bps + uncertainty_bps
    total_cost_bps = max(total_cost_bps, base_cost_bps)
    gross = max(0.0, float(expected_gross_edge_bps or 0.0))
    net = gross - half_spread_bps - slippage_bps - fees_bps - regulatory_fees_bps - market_impact_bps - adverse_bps - uncertainty_bps
    ratio = None if gross <= 0 else total_cost_bps / gross
    model_status = str(raw.get("status") or "UNKNOWN")
    model_applied = bool(raw.get("modelApplied"))
    fallback_approved = bool(settings.get("conservativeCostFallbackApproved") or context.get("conservativeCostFallbackApproved"))
    unavailable = not model_applied and not fallback_approved
    stale = _is_stale(raw, context, features, settings)
    reasons = [str(code) for code in raw.get("reasonCodes") or ()]
    if unavailable:
        reasons.append("regime.execution_cost.model_unavailable_fallback_not_approved")
    if stale:
        reasons.append("regime.execution_cost.model_stale")
    return RegimeExecutionCostEstimate(
        adapter_version=REGIME_EXECUTION_COST_ADAPTER_VERSION,
        model_version=str(raw.get("modelVersion") or EXECUTION_COST_MODEL_VERSION),
        model_status=model_status,
        model_applied=model_applied,
        artifact_id=raw.get("artifactId"),
        conservative_fallback_approved=fallback_approved,
        expected_gross_edge_bps=round(gross, 6),
        expected_spread_cost_bps=round(half_spread_bps, 6),
        expected_slippage_bps=round(slippage_bps, 6),
        expected_fees_bps=round(fees_bps, 6),
        expected_regulatory_fees_bps=round(regulatory_fees_bps, 6),
        expected_market_impact_bps=round(market_impact_bps, 6),
        adverse_selection_allowance_bps=round(adverse_bps, 6),
        uncertainty_buffer_bps=round(uncertainty_bps, 6),
        expected_net_edge_bps=round(net, 6),
        total_cost_bps=round(total_cost_bps, 6),
        cost_to_edge_ratio=None if ratio is None else round(ratio, 6),
        maximum_cost_to_edge_ratio=float(settings.get("maximumCostToEdgeRatio", settings.get("maximumCostEdgeRatio", 0.75))),
        maximum_slippage_bps=float(settings.get("maximumSlippageBps", 0.0)),
        minimum_net_edge_bps=_minimum_net_edge_bps(settings),
        stale=stale,
        unavailable=unavailable,
        evaluated_at=(evaluated_at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        reason_codes=tuple(dict.fromkeys(reasons or ("regime.execution_cost.estimated",))),
        raw_model_estimate=raw,
    )


def evaluate_regime_execution_cost_gate(estimate: RegimeExecutionCostEstimate, settings: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if estimate.unavailable:
        blockers.append("regime.execution_cost.model_unavailable_fallback_not_approved")
    if estimate.stale:
        blockers.append("regime.execution_cost.model_stale")
    if estimate.expected_net_edge_bps <= 0:
        blockers.append("regime.execution_cost.net_edge_nonpositive")
    if estimate.expected_net_edge_bps < estimate.minimum_net_edge_bps:
        blockers.append("regime.execution_cost.net_edge_below_threshold")
    if estimate.cost_to_edge_ratio is None:
        blockers.append("regime.execution_cost.cost_to_edge_ratio_unavailable")
    elif estimate.cost_to_edge_ratio > estimate.maximum_cost_to_edge_ratio:
        blockers.append("regime.execution_cost.cost_to_edge_ratio_exceeded")
    if estimate.expected_slippage_bps > estimate.maximum_slippage_bps:
        blockers.append("regime.execution_cost.slippage_limit_exceeded")
    return {
        "passed": not blockers,
        "reasonCodes": tuple(dict.fromkeys(blockers or ("regime.execution_cost.gate_passed",))),
        "estimate": estimate.as_dict(),
        "gateVersion": "regime_execution_cost_gate_v1",
    }


def _market_impact_bps(context: dict[str, Any], settings: dict[str, Any], *, quantity: int) -> float:
    explicit = _nonnegative_number(context.get("marketImpactBps") or settings.get("marketImpactBps"))
    expected_fill_quantity = _nonnegative_number(context.get("expectedFillQuantity") or _record(context.get("quoteFreshness")).get("expectedFillQuantity"))
    if expected_fill_quantity <= 0:
        return explicit
    participation = quantity / max(expected_fill_quantity, 1.0)
    coefficient = _nonnegative_number(settings.get("marketImpactBpsPerParticipationPct") or context.get("marketImpactBpsPerParticipationPct"))
    return max(explicit, participation * 100.0 * coefficient)


def _minimum_net_edge_bps(settings: dict[str, Any]) -> float:
    explicit = _number(settings.get("minimumNetExpectedEdgeBps"))
    if explicit is not None:
        return max(0.0, explicit)
    return max(0.0, _nonnegative_number(settings.get("minimumNetExpectedEdge")) * 100.0)


def _is_stale(raw: dict[str, Any], context: dict[str, Any], features: dict[str, Any], settings: dict[str, Any]) -> bool:
    if raw.get("modelApplied") is not True:
        return False
    age = _number(raw.get("modelAgeSeconds") or context.get("costModelAgeSeconds") or features.get("costModelAgeSeconds"))
    if age is None:
        return False
    return age > float(settings.get("maximumCostModelAgeSeconds", 900))


def _bps_to_dollars(bps: float, price: float, quantity: int) -> float:
    return max(0.0, bps) / 10_000.0 * max(price, 0.01) * max(quantity, 1)


def _dollars_to_bps(dollars: float, price: float, quantity: int) -> float:
    notional = max(price, 0.01) * max(quantity, 1)
    return max(0.0, dollars) / notional * 10_000.0


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_number(value: Any) -> float:
    number = _number(value)
    return max(0.0, number or 0.0)


__all__ = [
    "REGIME_EXECUTION_COST_ADAPTER_VERSION",
    "RegimeExecutionCostEstimate",
    "estimate_regime_execution_cost",
    "evaluate_regime_execution_cost_gate",
]
