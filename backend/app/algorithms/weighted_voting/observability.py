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
WEIGHTED_VOTING_OBSERVABILITY_NAMESPACE = "data/algorithms/weighted_voting/observability/"
WEIGHTED_VOTING_OBSERVABILITY_REQUIRED_FIELDS = (
    "decision_id",
    "market_snapshot_hash",
    "strategy_outputs",
    "active_weights",
    "aggregated_scores",
    "market_condition",
    "local_gate_outcomes",
    "sizing_result",
    "global_gate_result",
    "final_proposal",
    "stage_timings",
    "exceptions",
    "data_quality_warnings",
    "configuration_versions",
    "explanation",
    "reason_codes",
)
WEIGHTED_VOTING_OBSERVABILITY_STAGES = (
    "market_snapshot",
    "active_weight_load",
    "market_condition",
    "strategy_evaluation",
    "weighted_aggregation",
    "dynamic_settings",
    "local_gates",
    "position_sizing",
    "order_proposal",
    "global_gate_interface",
    "observability_persistence",
)


class WeightedVotingObservabilityStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


def observability_status() -> dict[str, Any]:
    return {
        "observabilityVersion": WEIGHTED_VOTING_OBSERVABILITY_VERSION,
        "algorithmId": "weighted_voting",
        "authoritativeSource": WEIGHTED_VOTING_OBSERVABILITY_NAMESPACE,
        "dashboardVisibility": "shared_dashboard_read_only",
        "requiredFields": WEIGHTED_VOTING_OBSERVABILITY_REQUIRED_FIELDS,
        "stageTimingContract": WEIGHTED_VOTING_OBSERVABILITY_STAGES,
        "isolation": {
            "ownsNamespace": True,
            "recordsOnlyWeightedVotingState": True,
            "sharedDashboardsMayDisplay": True,
            "sharedDashboardsMayMutate": False,
        },
        "reasonCodes": ("weighted_voting.observability.ready",),
    }


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
    stage_timings: dict[str, Any] | None = None,
    exceptions: tuple[Any, ...] = (),
    data_quality_warnings: tuple[str, ...] = (),
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
        stage_timings=stage_timings,
        exceptions=exceptions,
        data_quality_warnings=data_quality_warnings,
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
    stage_timings: dict[str, Any] | None,
    exceptions: tuple[Any, ...],
    data_quality_warnings: tuple[str, ...],
    recorded_at: datetime,
) -> dict[str, Any]:
    proposal = _json_ready(global_order_proposal)
    gate_application = _json_ready(global_gate_application)
    sizing = _json_ready(sizing_result)
    decision_payload = decision.model_dump(mode="json")
    signal_payloads = [signal.model_dump(mode="json") for signal in signals]
    data_freshness = _data_freshness(market_snapshot, signals)
    quality_warnings = _data_quality_warnings(market_snapshot, signals, data_quality_warnings)
    rejection_reasons = _rejection_reasons(decision_payload, _json_ready(local_gate_result), sizing, _json_ready(global_gate_response), gate_application)
    reason_codes = _reason_codes(decision_payload, signal_payloads, _json_ready(local_gate_result), sizing, _json_ready(global_gate_response), gate_application, rejection_reasons)
    return {
        "observabilityVersion": WEIGHTED_VOTING_OBSERVABILITY_VERSION,
        "immutable": True,
        "algorithmId": "weighted_voting",
        "authoritativeSource": WEIGHTED_VOTING_OBSERVABILITY_NAMESPACE,
        "dashboardVisibility": "shared_dashboard_read_only",
        "decisionId": decision.decision_id,
        "recordedAt": recorded_at.isoformat(),
        "dataTimestamp": market_snapshot.data_timestamp.isoformat(),
        "marketSnapshotHash": market_snapshot.data_manifest_hash,
        "dataFreshness": data_freshness,
        "strategyOutputs": signal_payloads,
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
        "activeWeights": active_weight_state.model_dump(mode="json"),
        "weightStages": {
            "activeWeightState": active_weight_state.model_dump(mode="json"),
            "adjustments": [adjustment.model_dump(mode="json") for adjustment in decision.weight_adjustments],
        },
        "familyContributions": decision.vote_scores.family_contributions,
        "aggregatedScores": decision.vote_scores.model_dump(mode="json"),
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
        "configurationVersions": _configuration_versions(decision, signals, active_weight_state, effective_settings, proposal),
        "localGateOutcomes": _json_ready(local_gate_result),
        "localGateResults": _json_ready(local_gate_result),
        "sizingResult": sizing,
        "proposedQuantity": sizing.get("quantity", decision.proposed_quantity),
        "globalGateResult": _json_ready(global_gate_response),
        "executableQuantity": gate_application.get("globallyAllowedQuantity", 0),
        "finalProposal": {
            "proposal": proposal,
            "globalGateApplication": gate_application,
            "acceptedQuantity": gate_application.get("globallyAllowedQuantity", 0),
            "action": gate_application.get("action"),
        },
        "orderLevels": {
            "entry": proposal.get("triggerPrice"),
            "limit": proposal.get("limitPrice"),
            "stop": proposal.get("stopPrice"),
            "target": proposal.get("targetPrice"),
        },
        "stageTimings": _stage_timings(stage_timings, recorded_at),
        "exceptions": [_json_ready(exception) for exception in exceptions],
        "dataQualityWarnings": quality_warnings,
        "reasonCodes": reason_codes,
        "explanation": decision.explanation,
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
            "configurationVersion": decision.configuration_version,
            "strategyCatalogVersion": decision.strategy_catalog_version,
            "settingsVersion": decision.settings_version,
            "weightVersion": decision.weight_version,
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


def _configuration_versions(
    decision: WeightedDecision,
    signals: tuple[WeightedVotingSignal, ...],
    active_weight_state: WeightedWeightState,
    effective_settings: WeightedEffectiveSettings,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "decisionVersion": decision.decision_version,
        "configurationVersion": decision.configuration_version,
        "configurationHash": decision.configuration_hash,
        "strategyCatalogVersion": decision.strategy_catalog_version,
        "strategyVersions": {signal.strategy_id: signal.strategy_version for signal in signals},
        "weightVersion": active_weight_state.weight_version,
        "decisionWeightVersion": decision.weight_version,
        "settingsVersion": effective_settings.settings_version,
        "decisionSettingsVersion": decision.settings_version,
        "effectiveConfigurationVersion": effective_settings.configuration_version,
        "baselineConfigurationVersion": effective_settings.baseline_configuration_version,
        "dynamicProfileVersion": effective_settings.dynamic_profile_version,
        "proposalSettingsVersion": proposal.get("settingsVersion") or proposal.get("settings_version"),
        "proposalWeightVersion": proposal.get("weightVersion") or proposal.get("weight_version"),
        "dataManifestHash": decision.data_manifest_hash,
    }


def _stage_timings(stage_timings: dict[str, Any] | None, recorded_at: datetime) -> dict[str, Any]:
    provided = _json_ready(stage_timings or {})
    stages: dict[str, Any] = {}
    for stage in WEIGHTED_VOTING_OBSERVABILITY_STAGES:
        value = provided.get(stage, {}) if isinstance(provided, dict) else {}
        if isinstance(value, dict):
            stages[stage] = {
                "status": value.get("status", "recorded"),
                "startedAt": value.get("startedAt"),
                "completedAt": value.get("completedAt", recorded_at.isoformat()),
                "elapsedMs": float(value.get("elapsedMs", 0.0)),
            }
        else:
            stages[stage] = {
                "status": "recorded",
                "startedAt": None,
                "completedAt": recorded_at.isoformat(),
                "elapsedMs": 0.0,
            }
    if isinstance(provided, dict):
        for stage, value in provided.items():
            if stage not in stages:
                stages[str(stage)] = value
    return stages


def _data_quality_warnings(
    market_snapshot: WeightedMarketSnapshot,
    signals: tuple[WeightedVotingSignal, ...],
    explicit_warnings: tuple[str, ...],
) -> list[str]:
    warnings = [str(warning) for warning in explicit_warnings if warning]
    if not market_snapshot.one_minute_candles:
        warnings.append("weighted_voting.data_quality.no_one_minute_candles")
    if market_snapshot.data_manifest_hash is None:
        warnings.append("weighted_voting.data_quality.missing_market_snapshot_hash")
    for signal in signals:
        if str(signal.data_quality_status) not in ("ready", "WeightedDataQualityStatus.READY"):
            warnings.append(f"weighted_voting.data_quality.{signal.strategy_id}.{signal.data_quality_status}")
        if signal.actual_data_freshness_seconds is not None and signal.actual_data_freshness_seconds > signal.required_data_freshness_seconds:
            warnings.append(f"weighted_voting.data_quality.{signal.strategy_id}.stale_signal_data")
    return sorted(dict.fromkeys(warnings))


def _reason_codes(
    decision: dict[str, Any],
    signals: list[dict[str, Any]],
    local_gate: dict[str, Any],
    sizing: dict[str, Any],
    global_response: dict[str, Any],
    global_application: dict[str, Any],
    rejection_reasons: list[str],
) -> list[str]:
    codes: list[str] = ["weighted_voting.observability.decision_recorded"]
    codes.extend(str(code) for code in decision.get("reason_codes", ()))
    for signal in signals:
        codes.extend(str(code) for code in signal.get("reason_codes", ()))
    codes.extend(str(code) for code in local_gate.get("reason_codes", ()))
    for gate in local_gate.get("gate_results", ()):
        codes.extend(str(code) for code in gate.get("reason_codes", ()))
    codes.extend(str(code) for code in sizing.get("reason_codes", ()))
    codes.extend(str(code) for code in global_response.get("reasonCodes", ()))
    codes.extend(str(code) for code in global_response.get("rejectionReasons", ()))
    codes.extend(str(code) for code in global_application.get("reasonCodes", ()))
    codes.extend(str(code) for code in global_application.get("rejectionReasons", ()))
    codes.extend(rejection_reasons)
    return sorted(dict.fromkeys(code for code in codes if code))


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
