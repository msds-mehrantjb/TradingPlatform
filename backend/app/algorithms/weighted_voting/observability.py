"""Immutable decision observability for Weighted Voting.

This module records only Weighted Voting state plus neutral global/order-gateway
results. It does not read or write any other algorithm's artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Protocol

from backend.app.algorithms.weighted_voting.models import (
    WeightedDecision,
    WeightedEffectiveSettings,
    WeightedMarketCondition,
    WeightedMarketSnapshot,
    WeightedVotingSignal,
    WeightedWeightState,
)


WEIGHTED_VOTING_OBSERVABILITY_VERSION = "weighted_voting_observability_v1"
DECISION_OBSERVABILITY_PREFIX = "weighted_voting.observability.decisions."
EXECUTION_OBSERVABILITY_PREFIX = "weighted_voting.observability.executions."
METRICS_KEY = "weighted_voting.observability.metrics"


class WeightedVotingObservabilityStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


def record_decision_observability(
    *,
    store: WeightedVotingObservabilityStore,
    market_snapshot: WeightedMarketSnapshot,
    signals: tuple[WeightedVotingSignal, ...],
    active_weight_state: WeightedWeightState,
    decision: WeightedDecision,
    market_condition: WeightedMarketCondition,
    effective_settings: WeightedEffectiveSettings,
    local_gate_result: Any,
    sizing_result: Any,
    global_order_proposal: Any,
    global_gate_response: Any,
    global_gate_application: Any,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    recorded_at = recorded_at or _now()
    snapshot = _decision_snapshot(
        market_snapshot=market_snapshot,
        signals=signals,
        active_weight_state=active_weight_state,
        decision=decision,
        market_condition=market_condition,
        effective_settings=effective_settings,
        local_gate_result=local_gate_result,
        sizing_result=sizing_result,
        global_order_proposal=global_order_proposal,
        global_gate_response=global_gate_response,
        global_gate_application=global_gate_application,
        recorded_at=recorded_at,
    )
    snapshot["snapshotHash"] = _hash_payload(snapshot)
    key = f"{DECISION_OBSERVABILITY_PREFIX}{decision.decision_id}"
    store.write_snapshot(key, snapshot)
    _update_decision_metrics(store, snapshot)
    return snapshot


def record_order_execution_observability(
    *,
    store: WeightedVotingObservabilityStore,
    decision_id: str,
    order_intent_id: str,
    execution_result: Any,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    recorded_at = recorded_at or _now()
    result_payload = _json_ready(execution_result)
    outcome = {
        "observabilityVersion": WEIGHTED_VOTING_OBSERVABILITY_VERSION,
        "algorithmId": "weighted_voting",
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "recordedAt": recorded_at.isoformat(),
        "eventualOutcome": {
            "status": result_payload.get("status", "UNKNOWN"),
            "submitted": bool(result_payload.get("submitted", False)),
            "duplicate": bool(result_payload.get("duplicate", False)),
            "clientOrderId": result_payload.get("clientOrderId"),
            "reasonCodes": result_payload.get("reasonCodes", ()),
            "brokerAck": result_payload.get("brokerAck"),
            "fill": result_payload.get("fill"),
            "protectiveOrder": result_payload.get("protectiveOrder"),
        },
        "traceability": {
            "decisionId": decision_id,
            "orderIntentId": order_intent_id,
            "clientOrderId": result_payload.get("clientOrderId"),
        },
    }
    outcome["snapshotHash"] = _hash_payload(outcome)
    store.write_snapshot(f"{EXECUTION_OBSERVABILITY_PREFIX}{order_intent_id}", outcome)
    _update_execution_metrics(store, outcome)
    return outcome


def _decision_snapshot(
    *,
    market_snapshot: WeightedMarketSnapshot,
    signals: tuple[WeightedVotingSignal, ...],
    active_weight_state: WeightedWeightState,
    decision: WeightedDecision,
    market_condition: WeightedMarketCondition,
    effective_settings: WeightedEffectiveSettings,
    local_gate_result: Any,
    sizing_result: Any,
    global_order_proposal: Any,
    global_gate_response: Any,
    global_gate_application: Any,
    recorded_at: datetime,
) -> dict[str, Any]:
    proposal = _json_ready(global_order_proposal)
    gate_application = _json_ready(global_gate_application)
    sizing = _json_ready(sizing_result)
    decision_payload = decision.model_dump(mode="json")
    signal_payloads = [signal.model_dump(mode="json") for signal in signals]
    data_freshness = _data_freshness(market_snapshot, signals)
    rejection_reasons = _rejection_reasons(decision_payload, _json_ready(local_gate_result), sizing, _json_ready(global_gate_response), gate_application)
    return {
        "observabilityVersion": WEIGHTED_VOTING_OBSERVABILITY_VERSION,
        "immutable": True,
        "algorithmId": "weighted_voting",
        "decisionId": decision.decision_id,
        "recordedAt": recorded_at.isoformat(),
        "dataTimestamp": market_snapshot.data_timestamp.isoformat(),
        "dataFreshness": data_freshness,
        "strategySignals": signal_payloads,
        "strategyProbabilities": {
            signal.strategy_id: {
                "Buy": signal.p_buy,
                "Sell": signal.p_sell,
                "Hold": signal.p_hold,
                "signal": signal.signal,
            }
            for signal in signals
        },
        "weightStages": {
            "activeWeightState": active_weight_state.model_dump(mode="json"),
            "adjustments": [adjustment.model_dump(mode="json") for adjustment in decision.weight_adjustments],
        },
        "familyContributions": decision.vote_scores.family_contributions,
        "scoreTotals": decision.vote_scores.model_dump(mode="json"),
        "winner": decision.signal,
        "rawWinner": decision.raw_winner,
        "edge": decision.vote_scores.winner_edge,
        "marketCondition": market_condition.model_dump(mode="json"),
        "settings": {
            "defaultSettings": effective_settings.default_settings.model_dump(mode="json"),
            "dynamicMultipliers": _dynamic_multipliers(effective_settings),
            "effectiveSettings": effective_settings.model_dump(mode="json"),
        },
        "localGateResults": _json_ready(local_gate_result),
        "sizingResult": sizing,
        "proposedQuantity": sizing.get("quantity", decision.proposed_quantity),
        "globalGateResult": _json_ready(global_gate_response),
        "executableQuantity": gate_application.get("globallyAllowedQuantity", 0),
        "orderLevels": {
            "entry": proposal.get("triggerPrice"),
            "limit": proposal.get("limitPrice"),
            "stop": proposal.get("stopPrice"),
            "target": proposal.get("targetPrice"),
        },
        "rejectionReason": rejection_reasons,
        "eventualOutcome": {
            "status": "pending",
            "orderIntentId": proposal.get("orderIntentId"),
            "clientOrderId": None,
            "fill": None,
            "pnl": None,
        },
        "traceability": {
            "decisionId": decision.decision_id,
            "orderIntentId": proposal.get("orderIntentId"),
            "capitalPartitionId": proposal.get("capitalPartitionId"),
        },
    }


def _data_freshness(market_snapshot: WeightedMarketSnapshot, signals: tuple[WeightedVotingSignal, ...]) -> dict[str, Any]:
    latest_candle = market_snapshot.one_minute_candles[-1] if market_snapshot.one_minute_candles else None
    candle_age = None
    if latest_candle is not None:
        candle_age = max(0.0, (market_snapshot.data_timestamp - latest_candle.timestamp).total_seconds())
    actual_signal_freshness = [
        signal.actual_data_freshness_seconds
        for signal in signals
        if signal.actual_data_freshness_seconds is not None
    ]
    return {
        "dataTimestamp": market_snapshot.data_timestamp.isoformat(),
        "latestCandleTimestamp": latest_candle.timestamp.isoformat() if latest_candle else None,
        "candleAgeSeconds": candle_age,
        "requiredFreshnessSeconds": max((signal.required_data_freshness_seconds for signal in signals), default=None),
        "maxActualSignalFreshnessSeconds": max(actual_signal_freshness) if actual_signal_freshness else None,
        "dataManifestHash": market_snapshot.data_manifest_hash,
    }


def _dynamic_multipliers(effective_settings: WeightedEffectiveSettings) -> dict[str, float]:
    return {
        adjustment.setting_name: adjustment.condition_multiplier
        for adjustment in effective_settings.dynamic_adjustments
    }


def _rejection_reasons(decision: dict[str, Any], local_gate: dict[str, Any], sizing: dict[str, Any], global_response: dict[str, Any], global_application: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    reasons.extend(str(code) for code in decision.get("reason_codes", ()))
    for gate in local_gate.get("gate_results", ()):
        if gate.get("status") == "fail" or gate.get("blocks_order"):
            reasons.extend(str(code) for code in gate.get("reason_codes", ()))
    reasons.extend(str(code) for code in sizing.get("reason_codes", ()) if "failed" in str(code) or "not_met" in str(code) or "missing" in str(code))
    reasons.extend(str(reason) for reason in global_response.get("rejectionReasons", ()))
    reasons.extend(str(reason) for reason in global_application.get("rejectionReasons", ()))
    if global_application.get("globallyAllowedQuantity", 0) < global_application.get("proposedQuantity", 0):
        reasons.append("weighted_voting.global_quantity_reduced")
    if decision.get("signal") == "Hold" and not reasons:
        reasons.append("weighted_voting.hold_without_directional_permission")
    return sorted(dict.fromkeys(reason for reason in reasons if reason))


def _update_decision_metrics(store: WeightedVotingObservabilityStore, snapshot: dict[str, Any]) -> None:
    metrics = _read_metrics(store)
    metrics["decisionCount"] = int(metrics.get("decisionCount", 0)) + 1
    side = str(snapshot["winner"])
    _increment_nested(metrics, "decisionsBySide", side)
    if side == "Hold":
        for reason in snapshot["rejectionReason"] or ("weighted_voting.hold",):
            _increment_nested(metrics, "holdReasons", reason)
    for gate in snapshot["localGateResults"].get("gate_results", ()):
        if gate.get("status") == "fail" or gate.get("blocks_order"):
            _increment_nested(metrics, "gateRejectionFrequency", str(gate.get("gate_id") or gate.get("gate_name") or "unknown_gate"))
    for strategy_id, weight in snapshot["weightStages"]["activeWeightState"].get("strategy_weights", {}).items():
        _average_nested(metrics, "averageWeight", strategy_id, float(weight))
    family_contributions = snapshot.get("familyContributions", {})
    if family_contributions:
        max_family_weight = max(float(row.get("weight", 0.0)) for row in family_contributions.values())
        _average_nested(metrics, "familyConcentration", "maximumFamilyWeight", max_family_weight)
    sizing = snapshot.get("sizingResult", {})
    if snapshot.get("proposedQuantity") == 0:
        _increment_nested(metrics, "sizingLimitingFactors", "zero_quantity")
    limiting = sizing.get("limiting_cap")
    if limiting:
        _increment_nested(metrics, "sizingLimitingFactors", str(limiting))
    metrics["schedulerStatus"] = _read_optional(store, "weighted_voting.daily_update.latest") or metrics.get("schedulerStatus", {"status": "unknown"})
    metrics["lastUpdatedAt"] = _now().isoformat()
    store.write_snapshot(METRICS_KEY, metrics)


def _update_execution_metrics(store: WeightedVotingObservabilityStore, outcome: dict[str, Any]) -> None:
    metrics = _read_metrics(store)
    eventual = outcome["eventualOutcome"]
    fill = eventual.get("fill") or {}
    status = str(eventual.get("status", "UNKNOWN"))
    _increment_nested(metrics, "executionStatus", status)
    if fill:
        average_fill = fill.get("averageFillPrice")
        if average_fill is not None:
            _average_nested(metrics, "fillQuality", "averageFillPrice", float(average_fill))
    metrics.setdefault("slippage", {"count": 0, "average": 0.0})
    metrics.setdefault("pnl", {"realized": 0.0, "unrealized": 0.0})
    metrics.setdefault("drawdown", {"current": 0.0, "maximum": 0.0})
    metrics["lastUpdatedAt"] = _now().isoformat()
    store.write_snapshot(METRICS_KEY, metrics)


def _read_metrics(store: WeightedVotingObservabilityStore) -> dict[str, Any]:
    return _read_optional(store, METRICS_KEY) or {
        "observabilityVersion": WEIGHTED_VOTING_OBSERVABILITY_VERSION,
        "algorithmId": "weighted_voting",
        "decisionCount": 0,
        "decisionsBySide": {},
        "holdReasons": {},
        "gateRejectionFrequency": {},
        "averageWeight": {},
        "familyConcentration": {},
        "sizingLimitingFactors": {},
        "slippage": {"count": 0, "average": 0.0},
        "fillQuality": {},
        "pnl": {"realized": 0.0, "unrealized": 0.0},
        "drawdown": {"current": 0.0, "maximum": 0.0},
        "schedulerStatus": {"status": "unknown"},
    }


def _increment_nested(metrics: dict[str, Any], group: str, key: str) -> None:
    bucket = metrics.setdefault(group, {})
    bucket[key] = int(bucket.get(key, 0)) + 1


def _average_nested(metrics: dict[str, Any], group: str, key: str, value: float) -> None:
    bucket = metrics.setdefault(group, {})
    row = bucket.get(key, {"count": 0, "average": 0.0})
    count = int(row.get("count", 0)) + 1
    previous_average = float(row.get("average", 0.0))
    bucket[key] = {"count": count, "average": previous_average + (value - previous_average) / count}


def _read_optional(store: WeightedVotingObservabilityStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)
