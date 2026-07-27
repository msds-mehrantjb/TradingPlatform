"""Pure state-in/state-out Regime completed-bar processor."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from backend.app.algorithms.regime.broker_adapter import build_regime_broker_submission
from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings, regime_settings_identity_from_payload
from backend.app.algorithms.regime.contracts import RegimeMarketSnapshot, to_dict
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.global_risk_adapter import RegimeGlobalRiskRequest, evaluate_regime_global_risk_request
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_risk
from backend.app.algorithms.regime.order_intent import build_regime_order_intent
from backend.app.algorithms.regime.order_validation import validate_regime_order_intent
from backend.app.algorithms.regime.runtime_state import (
    RegimeRuntimeState,
    migrate_regime_runtime_state,
    next_regime_runtime_state,
    runtime_state_to_hysteresis,
)
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


REGIME_STATEFUL_CORE_VERSION = "regime_stateful_completed_bar_v1"


def process_regime_bar(
    *,
    snapshot: RegimeMarketSnapshot,
    settings_snapshot: dict[str, Any],
    previous_state: dict[str, Any] | RegimeRuntimeState | None,
    inventory_snapshot: dict[str, Any] | None = None,
    account_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inventory = inventory_snapshot or {}
    account = account_snapshot or {}
    identity = _identity(settings_snapshot, snapshot, inventory, account)
    state = previous_state if isinstance(previous_state, RegimeRuntimeState) else migrate_regime_runtime_state(previous_state, identity, timestamp=snapshot.latest.timestamp)
    settings = flatten_regime_trading_settings(settings_snapshot)
    data_manifest_hash = deterministic_data_manifest_hash(snapshot, inventory)
    decision_id = deterministic_regime_decision_id(
        algorithm_instance_id=identity["algorithmInstanceId"],
        runtime_mode=identity["runtimeMode"],
        symbol=snapshot.symbol,
        completed_bar_timestamp=snapshot.latest.timestamp,
        data_manifest_hash=data_manifest_hash,
        settings_version=str(settings["settingsVersion"]),
    )
    decision = calculate_regime_decision(
        snapshot,
        settings={
            **settings,
            "accountSnapshot": account,
            "inventorySnapshot": inventory,
            "dailyCounters": state.daily_counters,
            "cooldownState": state.cooldown_state,
            "openPosition": _open_position_summary(inventory, account),
        },
        previous_state=runtime_state_to_hysteresis(state),
    )
    decision = replace(decision, decision_id=decision_id)
    sizing = calculate_regime_position_size(decision, snapshot, account)
    trade_management = evaluate_regime_exit(
        _open_position_summary(inventory, account),
        {
            "timestamp": snapshot.latest.timestamp,
            "open": snapshot.latest.open,
            "high": snapshot.latest.high,
            "low": snapshot.latest.low,
            "close": snapshot.latest.close,
            "volume": snapshot.latest.volume,
        },
        decision.confirmed_state.confirmed_regime,
    )
    intent = build_regime_order_intent(decision, sizing)
    order_proposal = to_dict(intent)
    if order_proposal is not None:
        order_proposal["settingsSnapshot"] = settings_snapshot
        order_proposal["settingsVersion"] = decision.settings_version
        order_proposal["profileVersion"] = decision.profile_version
        order_proposal["dataManifestHash"] = data_manifest_hash
    order_valid, order_reasons = validate_regime_order_intent(intent, decision.effective_settings)
    local_risk_result = None
    if order_proposal is not None:
        local_risk_result = evaluate_regime_local_risk(
            decision_id=decision_id,
            order_intent_id=str(order_proposal.get("order_intent_id") or order_proposal.get("orderIntentId") or ""),
            settings_version=decision.settings_version,
            requested_quantity=int(order_proposal.get("quantity") or 0),
            entry_price=float(order_proposal.get("entry_price") or order_proposal.get("entryPrice") or snapshot.latest.close),
            aggregation=decision.effective_settings.get("familyAggregation") or {},
            classification=decision.raw_classification,
            state=decision.confirmed_state,
            settings=decision.effective_settings,
            runtime_context={
                "accountSnapshot": account,
                "inventorySnapshot": inventory,
                "openPosition": _open_position_summary(inventory, account),
                "quoteFreshness": _record(snapshot.context_feeds.get("quoteFreshness") or snapshot.context_feeds.get("quote")),
                "dailyCounters": state.daily_counters,
                "cooldownState": state.cooldown_state,
                "familyCooldowns": state.family_cooldowns,
                "expectedGrossEdgeBps": max(0.0, decision.score * 100.0),
                "decisionAgeSeconds": 0,
            },
        ).as_dict()
        if local_risk_result.get("passed"):
            order_proposal["quantity"] = int(local_risk_result["approvedQuantity"])
        else:
            order_proposal = None
    risk_approval = None
    broker_submission = None
    if intent is not None and order_proposal is not None and order_valid:
        risk_approval = evaluate_regime_global_risk_request(
            RegimeGlobalRiskRequest(
                decision_id=intent.decision_id,
                order_intent_id=intent.order_intent_id,
                symbol=intent.symbol,
                requested_quantity=int(order_proposal.get("quantity") or 0),
                requested_risk_dollars=intent.risk_dollars,
                algorithm_version=intent.algorithm_version,
                settings_version=intent.settings_version,
                global_quantity_cap=account.get("globalRiskCapacityQuantity"),
            )
        )
        broker_submission = build_regime_broker_submission(
            decision_id=intent.decision_id,
            order_intent_id=intent.order_intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=risk_approval.approved_quantity,
            algorithm_version=intent.algorithm_version,
            settings_version=intent.settings_version,
            approved_by_global_risk=not risk_approval.rejected,
        )
    next_state = next_regime_runtime_state(
        state,
        identity=identity,
        decision_id=decision_id,
        bar_timestamp=snapshot.latest.timestamp,
        confirmed_regime=decision.confirmed_state.confirmed_regime,
        previous_regime=decision.confirmed_state.previous_regime,
        candidate_regime=decision.confirmed_state.candidate_regime,
        candidate_confirmation_count=decision.confirmed_state.candidate_confirmation_count,
        regime_start_timestamp=decision.confirmed_state.regime_start_time,
        missing_inputs=decision.raw_classification.missing_inputs,
        open_position_summary=_open_position_summary(inventory, account),
        order_proposed=order_proposal is not None and bool(order_valid),
    )
    outputs = [to_dict(output) for output in decision.strategy_outputs]
    strategy_outputs = [output for output in outputs if output.get("role") == "directional"]
    context_outputs = [output for output in outputs if output.get("role") == "regime_context"]
    confirmation_outputs = [output for output in outputs if output.get("role") == "confirmation"]
    safety_outputs = [output for output in outputs if output.get("role") == "safety_gate"]
    family_aggregation = dict(decision.effective_settings.get("familyAggregation") or {})
    if not family_aggregation:
        family_aggregation = {
            "aggregateSignal": decision.aggregate_signal,
            "familyScores": dict(decision.family_scores),
            "winningScore": decision.score,
            "confidence": decision.confidence,
        }
    decision_record = to_dict(decision)
    transition = to_dict(decision.confirmed_state)
    classification = to_dict(decision.raw_classification)
    return {
        "algorithmId": "regime",
        "statefulCoreVersion": REGIME_STATEFUL_CORE_VERSION,
        "decisionId": decision_id,
        "dataManifestHash": data_manifest_hash,
        "decision": decision_record,
        "nextRuntimeState": next_state.as_dict(),
        "classification": classification,
        "transition": transition,
        "strategyOutputs": strategy_outputs,
        "contextOutputs": context_outputs,
        "confirmationOutputs": confirmation_outputs,
        "safetyOutputs": safety_outputs,
        "familyScores": dict(decision.family_scores),
        "familyAggregation": family_aggregation,
        "effectiveProfile": dict(decision.effective_settings),
        "localRiskCandidate": {"valid": order_valid, "reasonCodes": order_reasons, "tradeBlockers": decision.trade_blockers},
        "localRiskResult": local_risk_result,
        "orderProposal": order_proposal,
        "orderValidation": {"valid": order_valid, "reasonCodes": order_reasons},
        "globalRiskApproval": to_dict(risk_approval),
        "brokerSubmission": to_dict(broker_submission),
        "sizing": to_dict(sizing),
        "tradeManagement": trade_management,
        "persistenceRecords": {
            "decisionId": decision_id,
            "runtimeStateSchemaVersion": next_state.schema_version,
            "runtimeStateSequenceVersion": next_state.sequence_version,
            "orderIntentId": order_proposal.get("order_intent_id") if isinstance(order_proposal, dict) else None,
            "localRiskResultId": local_risk_result.get("localRiskResultId") if isinstance(local_risk_result, dict) else None,
        },
    }


def process_completed_bar(
    *,
    snapshot: RegimeMarketSnapshot,
    settings_snapshot: dict[str, Any],
    previous_state: dict[str, Any] | RegimeRuntimeState | None,
    inventory_snapshot: dict[str, Any] | None = None,
    account_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return process_regime_bar(
        snapshot=snapshot,
        settings_snapshot=settings_snapshot,
        previous_state=previous_state,
        inventory_snapshot=inventory_snapshot,
        account_snapshot=account_snapshot,
    )


def deterministic_regime_decision_id(
    *,
    algorithm_instance_id: str,
    runtime_mode: str,
    symbol: str,
    completed_bar_timestamp: str,
    data_manifest_hash: str,
    settings_version: str,
) -> str:
    key = ":".join(
        (
            "regime",
            algorithm_instance_id,
            runtime_mode,
            symbol.upper(),
            completed_bar_timestamp,
            data_manifest_hash,
            settings_version,
        )
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"regime-decision-{digest}"


def deterministic_data_manifest_hash(snapshot: RegimeMarketSnapshot, inventory_snapshot: dict[str, Any] | None = None) -> str:
    inventory = inventory_snapshot or {}
    supplied = inventory.get("dataManifestHash") or inventory.get("data_manifest_hash")
    if supplied:
        return str(supplied)
    payload = {
        "symbol": snapshot.symbol,
        "latestTimestamp": snapshot.latest.timestamp,
        "oneMinuteCount": len(snapshot.one_minute_candles),
        "fiveMinuteCount": len(snapshot.five_minute_candles),
        "latestClose": snapshot.latest.close,
        "latestVolume": snapshot.latest.volume,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _identity(settings_snapshot: dict[str, Any], snapshot: RegimeMarketSnapshot, inventory: dict[str, Any], account: dict[str, Any]) -> dict[str, str]:
    raw = {
        **settings_snapshot,
        "algorithmInstanceId": inventory.get("algorithmInstanceId") or account.get("algorithmInstanceId") or settings_snapshot.get("algorithmInstanceId"),
        "accountId": inventory.get("accountId") or account.get("accountId") or settings_snapshot.get("accountId"),
        "runtimeMode": inventory.get("runtimeMode") or account.get("runtimeMode") or settings_snapshot.get("runtimeMode"),
        "symbol": snapshot.symbol,
    }
    if isinstance(settings_snapshot.get("identity"), dict):
        raw["identity"] = {
            **settings_snapshot["identity"],
            "algorithmInstanceId": raw.get("algorithmInstanceId") or settings_snapshot["identity"].get("algorithmInstanceId"),
            "accountId": raw.get("accountId") or settings_snapshot["identity"].get("accountId"),
            "runtimeMode": raw.get("runtimeMode") or settings_snapshot["identity"].get("runtimeMode"),
            "symbol": snapshot.symbol,
        }
    return regime_settings_identity_from_payload(raw)


def _open_position_summary(inventory: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    value = inventory.get("openPosition") or inventory.get("open_position") or account.get("position") or account.get("currentPosition") or {}
    return value if isinstance(value, dict) else {}


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "REGIME_STATEFUL_CORE_VERSION",
    "deterministic_data_manifest_hash",
    "deterministic_regime_decision_id",
    "process_completed_bar",
    "process_regime_bar",
]
