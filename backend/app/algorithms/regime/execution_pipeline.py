"""Backend-authoritative Regime execution pipeline."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.contracts import RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.stateful_core import process_regime_bar


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
    runtime_mode = normalize_regime_runtime_mode(payload.get("runtimeMode") or payload.get("runtime_mode"), default=RegimeRuntimeMode.SHADOW).value
    settings_snapshot = payload.get("__regime_settings_snapshot") if isinstance(payload.get("__regime_settings_snapshot"), dict) else None
    if settings_snapshot is None:
        settings_snapshot = validate_regime_trading_settings_snapshot(
            {
                "identity": {
                    "algorithmInstanceId": payload.get("algorithmInstanceId") or "regime-default",
                    "accountId": payload.get("accountId") or "default",
                    "runtimeMode": runtime_mode,
                    "symbol": snapshot.symbol,
                }
            }
        ).as_dict()
    stateful = process_regime_bar(
        snapshot=snapshot,
        settings_snapshot=settings_snapshot,
        previous_state=payload.get("__regime_previous_state"),
        inventory_snapshot=payload.get("__regime_inventory_snapshot") or payload.get("inventorySnapshot") or {},
        account_snapshot=payload.get("account") or {},
    )
    decision = stateful["decision"]
    return {
        "algorithmId": "regime",
        "runtime": "backend.app.algorithms.regime.execution_pipeline",
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "settingsSource": payload.get("__regime_settings_source") or "caller_supplied_internal_or_test_settings",
        "settingsSnapshot": settings_snapshot,
        "settingsVersion": decision["settings_version"],
        "profileVersion": decision["profile_version"],
        "dataManifestHash": stateful["dataManifestHash"],
        "statefulCoreVersion": stateful["statefulCoreVersion"],
        "decision": decision,
        "nextRuntimeState": stateful["nextRuntimeState"],
        "strategyOutputs": stateful["strategyOutputs"],
        "contextOutputs": stateful["contextOutputs"],
        "confirmationOutputs": stateful["confirmationOutputs"],
        "safetyOutputs": stateful["safetyOutputs"],
        "familyAggregation": stateful["familyAggregation"],
        "effectiveProfile": stateful["effectiveProfile"],
        "localRiskResult": stateful["localRiskResult"],
        "orderProposal": stateful["orderProposal"],
        "persistenceRecords": stateful["persistenceRecords"],
        "sizing": stateful["sizing"],
        "tradeManagement": stateful["tradeManagement"],
        "orderIntent": stateful["orderProposal"],
        "orderValidation": stateful["orderValidation"],
        "globalRiskApproval": stateful["globalRiskApproval"],
        "brokerSubmission": stateful["brokerSubmission"],
    }
