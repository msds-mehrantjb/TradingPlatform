"""Pure state-in/state-out Regime completed-bar processor."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from dataclasses import replace
from typing import Any

from backend.app.algorithms.regime.account_snapshot import normalize_regime_account_snapshot
from backend.app.algorithms.regime.broker_adapter import build_regime_broker_submission
from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings, regime_settings_identity_from_payload
from backend.app.algorithms.regime.contracts import RegimeMarketSnapshot, to_dict
from backend.app.algorithms.regime.contracts import (
    REGIME_ALGORITHM_ID,
    REGIME_STRATEGY_CATALOG_VERSION,
    RegimeAxes,
    RegimeClassification,
    RegimeDecision,
    RegimeHysteresisState,
)
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.global_risk_adapter import RegimeGlobalRiskRequest, evaluate_regime_global_risk_request
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_risk
from backend.app.algorithms.regime.market_data_validation import validate_regime_market_data
from backend.app.algorithms.regime.order_intent import build_regime_order_intent
from backend.app.algorithms.regime.order_validation import validate_regime_order_intent
from backend.app.algorithms.regime.runtime_state import (
    RegimeRuntimeState,
    migrate_regime_runtime_state,
    next_regime_runtime_state,
    runtime_state_to_hysteresis,
)
from backend.app.algorithms.regime.runtime_idempotency import regime_bar_idempotency_key
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
    if identity["runtimeMode"] == "paper":
        account = normalize_regime_account_snapshot(account, identity=identity, max_age_seconds=None)
    state = previous_state if isinstance(previous_state, RegimeRuntimeState) else migrate_regime_runtime_state(previous_state, identity, timestamp=snapshot.latest.timestamp)
    settings = flatten_regime_trading_settings(settings_snapshot)
    data_manifest_hash = deterministic_data_manifest_hash(snapshot, inventory)
    data_validation = validate_regime_market_data(snapshot, settings=settings, observed_at=account.get("marketDataObservedAt"))
    decision_id = deterministic_regime_decision_id(
        algorithm_instance_id=identity["algorithmInstanceId"],
        runtime_mode=identity["runtimeMode"],
        symbol=snapshot.symbol,
        completed_bar_timestamp=snapshot.latest.timestamp,
        data_manifest_hash=data_manifest_hash,
        settings_version=str(settings["settingsVersion"]),
    )
    if data_validation.passed:
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
    else:
        decision = _fail_closed_market_data_decision(
            snapshot=snapshot,
            settings=settings,
            decision_id=decision_id,
            validation=data_validation.as_dict(),
            previous_state=runtime_state_to_hysteresis(state),
        )
    sizing = calculate_regime_position_size(decision, snapshot, account, inventory, state.daily_counters)
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
        settings,
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
                "runtimeMode": identity["runtimeMode"],
                "expectedAccountId": identity["accountId"],
                "expectedRuntimeMode": identity["runtimeMode"],
                "expectedAlgorithmInstanceId": identity["algorithmInstanceId"],
                "supervisorStarted": account.get("supervisorStarted"),
                "paperButtonRequested": account.get("paperButtonRequested"),
                "paperButtonEffective": account.get("paperButtonEffective"),
                "automaticPaperTradingEnabled": account.get("automaticPaperTradingEnabled"),
                "requireAutomaticPaperControlForEntry": account.get("requireAutomaticPaperControlForEntry"),
                "rolloutStageAllowsRealPaperExecution": account.get("rolloutStageAllowsRealPaperExecution"),
                "requireRealPaperExecutionStage": account.get("requireRealPaperExecutionStage"),
                "marketRegularSessionOpen": account.get("marketRegularSessionOpen"),
                "finalizedBarCurrent": account.get("finalizedBarCurrent"),
                "publisherHealthy": account.get("publisherHealthy"),
                "accountSnapshotCurrent": account.get("accountSnapshotCurrent"),
                "brokerHealthy": account.get("brokerHealthy"),
                "databaseHealthy": account.get("databaseHealthy"),
                "marketDataCurrentAndComplete": data_validation.passed and account.get("marketDataCurrentAndComplete", True) is not False,
                "brokerReconciliationHealthy": account.get("brokerReconciliationHealthy"),
                "operationalBlockers": account.get("operationalBlockers") or (),
                "killSwitchActive": account.get("killSwitchActive"),
                "runtimePaused": bool(account.get("runtimePaused") or account.get("paused")),
                "entryCreationPausedForReconciliation": bool(account.get("entryCreationPausedForReconciliation")),
                "recoverySucceeded": account.get("recoverySucceeded"),
                "inventoryReconciled": account.get("inventoryReconciled"),
                "ordersReconciled": account.get("ordersReconciled"),
                "reconciliationRequired": account.get("reconciliationRequired"),
                "quoteFreshness": _record(snapshot.context_feeds.get("quoteFreshness") or snapshot.context_feeds.get("quote")),
                "dailyCounters": state.daily_counters,
                "cooldownState": state.cooldown_state,
                "familyCooldowns": state.family_cooldowns,
                "expectedGrossEdgeBps": max(0.0, float((decision.effective_settings.get("familyAggregation") or {}).get("expectedGrossEdgeBps") or 0.0)),
                "decisionAgeSeconds": 0,
            },
            evaluated_at=_parse_timestamp(snapshot.latest.timestamp),
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
                algorithm_id="regime",
                decision_id=intent.decision_id,
                order_intent_id=intent.order_intent_id,
                symbol=intent.symbol,
                side=intent.side,
                requested_quantity=int(order_proposal.get("quantity") or 0),
                requested_risk_dollars=intent.risk_dollars,
                stop_price=intent.stop_price,
                target_price=intent.target_price,
                estimated_notional=float(order_proposal.get("quantity") or 0) * float(intent.entry_price or snapshot.latest.close),
                existing_regime_exposure=_regime_exposure_for_global_risk(inventory, snapshot),
                existing_account_exposure=_account_exposure_for_global_risk(account),
                algorithm_version=intent.algorithm_version,
                settings_version=intent.settings_version,
                expiration_timestamp=str(local_risk_result.get("expiresAt") or local_risk_result.get("expires_at")),
                idempotency_key=regime_bar_idempotency_key(
                    runtime_mode=identity["runtimeMode"],
                    symbol=intent.symbol,
                    finalised_bar_timestamp=snapshot.latest.timestamp,
                    algorithm_version=intent.algorithm_version,
                    settings_version=intent.settings_version,
                ),
                entry_price=float(intent.entry_price or snapshot.latest.close),
                position_effect=intent.position_effect,
                account_snapshot=account,
                market_snapshot=_market_snapshot_for_global_risk(snapshot, account),
                portfolio_snapshot=_portfolio_snapshot_for_global_risk(account),
                profile_version=decision.profile_version,
                generated_at=str(local_risk_result.get("evaluatedAt") or snapshot.latest.timestamp.isoformat()),
                market_data_timestamp=snapshot.latest.timestamp,
            )
        )
        local_approved_quantity = int(order_proposal.get("quantity") or 0)
        final_approved_quantity = int(risk_approval.approved_quantity)
        final_risk_dollars = _scaled_risk(float(order_proposal.get("risk_dollars") or order_proposal.get("riskDollars") or intent.risk_dollars), local_approved_quantity, final_approved_quantity)
        if risk_approval.rejected or final_approved_quantity <= 0:
            order_proposal = None
        else:
            order_proposal["quantity"] = final_approved_quantity
            order_proposal["risk_dollars"] = final_risk_dollars
            order_proposal["riskDollars"] = final_risk_dollars
            broker_submission = build_regime_broker_submission(
                decision_id=intent.decision_id,
                order_intent_id=intent.order_intent_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=final_approved_quantity,
                algorithm_version=intent.algorithm_version,
                settings_version=intent.settings_version,
                approved_by_global_risk=True,
            )
            order_proposal["localApprovedQuantity"] = local_approved_quantity
            order_proposal["globalApprovedQuantity"] = final_approved_quantity
            order_proposal["finalApprovedQuantity"] = final_approved_quantity
            order_proposal["globalRiskReasonCodes"] = list(risk_approval.reason_codes)
            order_proposal["globalRiskReservationId"] = risk_approval.reservation_id
            order_proposal["globalRiskAccountSnapshotVersion"] = risk_approval.account_risk_snapshot_version
            order_proposal["marketDataValidation"] = data_validation.as_dict()
            order_proposal["completedBarFinalized"] = True
            order_proposal["runtimeMode"] = identity["runtimeMode"]
            order_proposal["algorithmInstanceId"] = identity["algorithmInstanceId"]
            order_proposal["accountId"] = identity["accountId"]
    complete_effective_settings = _complete_effective_settings_snapshot(
        decision.effective_settings,
        local_risk_result=local_risk_result,
        risk_approval=to_dict(risk_approval) or {},
    )
    next_state = next_regime_runtime_state(
        state,
        identity=identity,
        decision_id=decision_id,
        bar_timestamp=snapshot.latest.timestamp,
        confirmed_regime=decision.confirmed_state.confirmed_regime,
        previous_regime=decision.confirmed_state.previous_regime,
        candidate_regime=decision.confirmed_state.candidate_regime,
        candidate_start_timestamp=decision.confirmed_state.candidate_start_time,
        candidate_confirmation_count=decision.confirmed_state.candidate_confirmation_count,
        regime_confidence=decision.confirmed_state.regime_confidence or decision.confirmed_state.transition_confidence,
        regime_start_timestamp=decision.confirmed_state.regime_start_time,
        last_transition_timestamp=decision.confirmed_state.last_transition_time or decision.confirmed_state.regime_start_time,
        transition_reason=decision.confirmed_state.transition_reason,
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
    decision_record["dataTimestamp"] = data_validation.data_timestamp
    decision_record["featureTimestamp"] = data_validation.feature_timestamp
    decision_record["marketDataValidation"] = data_validation.as_dict()
    transition = to_dict(decision.confirmed_state)
    classification = to_dict(decision.raw_classification)
    return {
        "algorithmId": "regime",
        "statefulCoreVersion": REGIME_STATEFUL_CORE_VERSION,
        "decisionId": decision_id,
        "dataTimestamp": data_validation.data_timestamp,
        "featureTimestamp": data_validation.feature_timestamp,
        "dataManifestHash": data_manifest_hash,
        "marketDataValidation": data_validation.as_dict(),
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
        "effectiveProfile": complete_effective_settings,
        "localRiskCandidate": {"valid": order_valid, "reasonCodes": order_reasons, "tradeBlockers": decision.trade_blockers},
        "localRiskResult": local_risk_result,
        "orderProposal": order_proposal,
        "orderValidation": {"valid": order_valid, "reasonCodes": order_reasons},
        "globalRiskApproval": to_dict(risk_approval),
        "brokerSubmission": to_dict(broker_submission),
        "sizing": _sizing_with_global_approval(sizing, risk_approval),
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


def _fail_closed_market_data_decision(
    *,
    snapshot: RegimeMarketSnapshot,
    settings: dict[str, Any],
    decision_id: str,
    validation: dict[str, Any],
    previous_state: RegimeHysteresisState | None,
) -> RegimeDecision:
    reason_codes = tuple(str(code) for code in validation.get("reasonCodes") or ("regime.market_data.validation_failed",))
    classification = RegimeClassification(
        raw_regime="unknown",
        axes=RegimeAxes(
            direction="unknown",
            volatility="unknown",
            structure="unknown",
            liquidity="unknown",
            session="unknown",
            event_risk="unknown",
            trend_strength="unknown",
            data_quality="invalid",
        ),
        confidence=0.0,
        features={
            "dataTimestamp": validation.get("dataTimestamp"),
            "featureTimestamp": validation.get("featureTimestamp"),
            "missingBarCount": validation.get("missingBarCount"),
            "duplicateTimestampCount": validation.get("duplicateTimestampCount"),
        },
        evidence={"marketDataValidation": validation},
        missing_inputs=reason_codes,
        no_trade_reasons=reason_codes,
        timestamp=str(validation.get("dataTimestamp") or snapshot.latest.timestamp),
    )
    state = RegimeHysteresisState(
        confirmed_regime="unknown",
        previous_regime=previous_state.confirmed_regime if previous_state is not None else None,
        candidate_regime="unknown",
        candidate_confirmation_count=0,
        regime_start_time=str(validation.get("dataTimestamp") or snapshot.latest.timestamp),
        transition_confidence=0.0,
        transition_reason="regime.market_data.fail_closed_before_classification",
        transition_evidence={"marketDataValidation": validation},
        candidate_start_time=str(validation.get("dataTimestamp") or snapshot.latest.timestamp),
        regime_confidence=0.0,
        last_transition_time=str(validation.get("dataTimestamp") or snapshot.latest.timestamp),
        bars_in_current_regime=1,
        state_version=max(1, int(getattr(previous_state, "state_version", 0) or 0) + 1) if previous_state is not None else 1,
    )
    return RegimeDecision(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_STATEFUL_CORE_VERSION,
        settings_version=str(settings.get("settingsVersion")),
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        profile_version=str(settings.get("profileVersion")),
        decision_id=decision_id,
        symbol=snapshot.symbol,
        signal="Hold",
        aggregate_signal="Hold",
        trade_allowed=False,
        trade_blockers=tuple(dict.fromkeys(("regime.market_data.validation_failed", *reason_codes))),
        raw_classification=classification,
        confirmed_state=state,
        strategy_outputs=(),
        family_scores={},
        effective_settings={
            **settings,
            "noNewEntries": True,
            "pipelineOrder": (
                "data_validation",
                "hold_fail_closed",
                "sizing",
                "no_order_proposal",
            ),
            "familyAggregation": {"aggregateSignal": "Hold", "familyScores": {}, "winningScore": 0.0, "confidence": 0.0},
            "marketDataValidation": validation,
            "effectiveSettingsReasonCodes": tuple(dict.fromkeys(reason_codes)),
        },
        score=0.0,
        confidence=0.0,
    )


def _complete_effective_settings_snapshot(
    effective_settings: dict[str, Any],
    *,
    local_risk_result: dict[str, Any] | None,
    risk_approval: dict[str, Any],
) -> dict[str, Any]:
    local_reason_codes = tuple(str(code) for code in (local_risk_result or {}).get("reasonCodes") or (local_risk_result or {}).get("blockers") or ())
    global_reason_codes = tuple(str(code) for code in risk_approval.get("reasonCodes") or ())
    return {
        **effective_settings,
        "localRiskReduction": {
            "applied": bool(local_risk_result),
            "passed": (local_risk_result or {}).get("passed"),
            "approvedQuantity": (local_risk_result or {}).get("approvedQuantity"),
            "reasonCodes": local_reason_codes,
        },
        "sharedGlobalRiskReductionOrRejection": {
            "applied": bool(risk_approval),
            "approvedQuantity": risk_approval.get("approved_quantity") or risk_approval.get("approvedQuantity"),
            "rejected": risk_approval.get("rejected"),
            "reasonCodes": global_reason_codes,
        },
        "effectiveSettingsReasonCodes": tuple(
            dict.fromkeys(
                tuple(effective_settings.get("overlayReasons") or ())
                + local_reason_codes
                + global_reason_codes
            )
        ),
    }


def deterministic_regime_decision_id(
    *,
    algorithm_instance_id: str,
    runtime_mode: str,
    symbol: str,
    completed_bar_timestamp: str,
    data_manifest_hash: str,
    settings_version: str,
) -> str:
    del algorithm_instance_id, data_manifest_hash
    key = regime_bar_idempotency_key(
        runtime_mode=runtime_mode,
        symbol=symbol,
        finalised_bar_timestamp=completed_bar_timestamp,
        algorithm_version=REGIME_STATEFUL_CORE_VERSION,
        settings_version=settings_version,
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
    del account
    value = inventory.get("openPosition") or inventory.get("open_position") or {}
    if isinstance(value, dict) and value:
        return value
    quantity = int(_number(inventory.get("quantity")) or 0)
    if quantity == 0:
        return {}
    price = float(_number(inventory.get("averageEntryPrice")) or 0.0)
    return {
        "positionId": inventory.get("positionId"),
        "tradeId": inventory.get("tradeId"),
        "symbol": inventory.get("symbol"),
        "quantity": abs(quantity),
        "signedQuantity": quantity,
        "side": "Long" if quantity > 0 else "Short",
        "averageEntryPrice": price,
        "notional": abs(quantity) * price,
        "marketValue": abs(quantity) * price,
    }


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _regime_exposure_for_global_risk(inventory: dict[str, Any], snapshot: RegimeMarketSnapshot) -> dict[str, Any]:
    quantity = int(_number(inventory.get("quantity") or inventory.get("signedQuantity")) or 0)
    average_price = float(_number(inventory.get("averageEntryPrice") or inventory.get("avgEntryPrice") or snapshot.latest.close) or snapshot.latest.close)
    market_value = abs(quantity) * float(snapshot.latest.close or average_price)
    return {
        "algorithmId": "regime",
        "symbol": inventory.get("symbol") or snapshot.symbol,
        "quantity": quantity,
        "marketValue": float(_number(inventory.get("marketValue")) or market_value),
        "openRiskDollars": float(_number(inventory.get("reservedRisk") or inventory.get("openRiskDollars")) or 0.0),
        "side": "long" if quantity >= 0 else "short",
        "positionId": inventory.get("positionId"),
        "tradeId": inventory.get("tradeId"),
    }


def _account_exposure_for_global_risk(account: dict[str, Any]) -> dict[str, Any]:
    value = (
        account.get("existingAccountExposure")
        or account.get("accountExposure")
        or account.get("globalAccountExposure")
        or account.get("portfolioSnapshot")
        or {}
    )
    return _record(value)


def _portfolio_snapshot_for_global_risk(account: dict[str, Any]) -> dict[str, Any]:
    value = account.get("portfolioSnapshot") or account.get("globalPortfolioSnapshot") or {}
    if isinstance(value, dict):
        return value
    return {}


def _market_snapshot_for_global_risk(snapshot: RegimeMarketSnapshot, account: dict[str, Any]) -> dict[str, Any]:
    quote = _record(snapshot.context_feeds.get("quoteFreshness") or snapshot.context_feeds.get("quote") or account.get("quoteSnapshot"))
    timestamp = _parse_timestamp(snapshot.latest.timestamp)
    timestamp_key = str(int(timestamp.timestamp())) if timestamp is not None else hashlib.sha256(str(snapshot.latest.timestamp).encode("utf-8")).hexdigest()[:16]
    return {
        "marketSnapshotId": account.get("marketSnapshotId") or f"regime-market-{snapshot.symbol}-{timestamp_key}",
        "session": account.get("marketSession") or account.get("session") or "regular",
        "regularSessionAllowed": account.get("regularSessionAllowed", True),
        "extendedHoursAllowed": account.get("extendedHoursAllowed", False),
        "marketHoliday": account.get("marketHoliday", False),
        "earlyClose": account.get("earlyClose", False),
        "entryCutoffReached": account.get("entryCutoffReached", False),
        "tradingHalt": account.get("tradingHalt", False),
        "luld": account.get("luld", False),
        "marketWideCircuitBreaker": account.get("marketWideCircuitBreaker", False),
        "candleTimestamp": snapshot.latest.timestamp,
        "quoteTimestamp": quote.get("quoteTimestamp") or quote.get("timestamp") or account.get("quoteTimestamp") or snapshot.latest.timestamp,
        "spreadPercent": account.get("spreadPercent") or quote.get("spreadPercent"),
        "oneMinuteVolume": snapshot.latest.volume,
        "estimatedSlippagePercent": account.get("estimatedSlippagePercent"),
        "eventBlackout": account.get("eventBlackout", False),
        "unsupportedOrderType": account.get("unsupportedOrderType", False),
    }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sizing_with_global_approval(sizing, risk_approval) -> dict[str, Any]:
    payload = to_dict(sizing)
    if risk_approval is None:
        payload["finalApprovedQuantity"] = payload.get("quantity", 0)
        payload["globalRiskReductionApplied"] = False
        return payload
    approved = int(risk_approval.approved_quantity)
    requested = int(payload.get("quantity") or 0)
    payload["finalApprovedQuantity"] = approved
    payload["globalRiskReductionApplied"] = approved < requested
    payload["globalRiskRejected"] = bool(risk_approval.rejected)
    payload["globalRiskReasonCodes"] = list(risk_approval.reason_codes)
    payload["quantityCaps"] = [
        *list(payload.get("quantity_caps") or payload.get("quantityCaps") or []),
        {
            "label": "shared_global_account_risk_reduction_or_rejection",
            "quantity": approved,
            "basis": requested,
            "reasonCode": "regime.sizing.sequence.shared_global_risk_reduction_or_rejection",
        },
    ]
    return payload


def _scaled_risk(local_risk_dollars: float, local_quantity: int, approved_quantity: int) -> float:
    if local_quantity <= 0:
        return 0.0
    return round(max(0.0, local_risk_dollars) * max(0, approved_quantity) / local_quantity, 6)


__all__ = [
    "REGIME_STATEFUL_CORE_VERSION",
    "deterministic_data_manifest_hash",
    "deterministic_regime_decision_id",
    "process_completed_bar",
    "process_regime_bar",
]
