"""Backend-authoritative Regime execution pipeline."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.broker_adapter import build_regime_broker_submission
from backend.app.algorithms.regime.contracts import to_dict
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.global_risk_adapter import RegimeGlobalRiskRequest, evaluate_regime_global_risk_request
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.order_intent import build_regime_order_intent
from backend.app.algorithms.regime.order_validation import validate_regime_order_intent
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


REGIME_EXECUTION_PIPELINE_MODULES = (
    "market_snapshot",
    "classifier",
    "hysteresis",
    "router",
    "strategy_registry",
    "family_aggregation",
    "local_gates",
    "dynamic_profile",
    "sizing",
    "trade_management",
    "order_intent",
    "order_validation",
    "global_risk_adapter",
    "broker_adapter",
)


def execute_regime_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_regime_market_snapshot(payload.get("marketData") or payload)
    settings = payload.get("settings") or {}
    account = payload.get("account") or {}
    decision = calculate_regime_decision(snapshot, settings=settings)
    sizing = calculate_regime_position_size(decision, snapshot, account)
    trade_management = evaluate_regime_exit(
        payload.get("position") or account.get("position") or account.get("currentPosition"),
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
    order_valid, order_reasons = validate_regime_order_intent(intent, decision.effective_settings)
    risk_approval = None
    broker_submission = None
    if intent is not None and order_valid:
        risk_approval = evaluate_regime_global_risk_request(
            RegimeGlobalRiskRequest(
                decision_id=intent.decision_id,
                order_intent_id=intent.order_intent_id,
                symbol=intent.symbol,
                requested_quantity=intent.quantity,
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
    return {
        "algorithmId": "regime",
        "runtime": "backend.app.algorithms.regime.execution_pipeline",
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "decision": to_dict(decision),
        "sizing": to_dict(sizing),
        "tradeManagement": trade_management,
        "orderIntent": to_dict(intent),
        "orderValidation": {"valid": order_valid, "reasonCodes": order_reasons},
        "globalRiskApproval": to_dict(risk_approval),
        "brokerSubmission": to_dict(broker_submission),
    }
