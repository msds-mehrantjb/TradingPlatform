"""HTTP boundary for the backend-authoritative Regime runtime."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from backend.app.algorithms.regime.backtest.engine import REGIME_BACKTEST_ENGINE_VERSION, run_regime_backtest
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES
from backend.app.algorithms.regime.final_acceptance import build_regime_final_acceptance_report
from backend.app.algorithms.regime.rollout import regime_rollout_status
from backend.app.algorithms.regime.service import RegimeApplicationService

REGIME_API_VERSION = "regime_api_v1"
REGIME_RUNTIME_LOCATION = "backend/app/algorithms/regime"
REGIME_AUTHORITATIVE_RUNTIME = "backend.app.algorithms.regime.execution_pipeline"
REGIME_BACKTEST_ARTIFACT_ROOT = "backend/data/regime-backtests"
REGIME_BACKTEST_AUTHORITATIVE_ENGINE = "backend.app.algorithms.regime.backtest.engine"
REGIME_BACKTEST_FILE_INVENTORY = (
    "__init__.py",
    "engine.py",
    "execution.py",
    "ledger.py",
    "metrics.py",
    "walk_forward.py",
)
REGIME_BACKTEST_OWNED_CAPABILITIES = (
    "Regime replay",
    "Warm-up handling",
    "Point-in-time classification",
    "Hysteresis replay",
    "Strategy routing",
    "Dynamic-profile reconstruction",
    "Family aggregation",
    "Entry and exit simulation",
    "Costs and slippage",
    "Position ledger",
    "Trade ledger",
    "Regime-segmented performance",
    "Strategy-family attribution",
    "Walk-forward validation",
    "Untouched holdout testing",
    "Daily independent backtests",
)

router = APIRouter(prefix="/api/regime", tags=["regime"])
REGIME_SERVICE = RegimeApplicationService()
REGIME_REPOSITORY = REGIME_SERVICE.repository


@router.get("/backtests/status", summary="Poll Regime backtest status")
def regime_backtest_status() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "apiVersion": REGIME_API_VERSION,
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "status": "backend_runtime_available",
        "artifactRoot": REGIME_BACKTEST_ARTIFACT_ROOT,
        "storageKeyPrefix": "regime-backtest:",
        "cacheKeySource": "symbol:first_timestamp:last_timestamp:candle_count",
        "authoritativeRuntime": REGIME_AUTHORITATIVE_RUNTIME,
        "authoritativeEngine": REGIME_BACKTEST_AUTHORITATIVE_ENGINE,
        "runtimeLocation": REGIME_RUNTIME_LOCATION,
        "frontendRole": "API client and presentation only",
        "fileInventory": REGIME_BACKTEST_FILE_INVENTORY,
        "ownedCapabilities": REGIME_BACKTEST_OWNED_CAPABILITIES,
        "isolatedFromWca": True,
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "message": "Regime decisions and backtests execute through the backend Python runtime.",
    }


@router.post("/evaluate", summary="Evaluate a Regime decision through the backend runtime")
def evaluate_regime(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return REGIME_SERVICE.evaluate(payload)


@router.post("/backtests/run", summary="Run a Regime backtest through the backend runtime")
def regime_backtest_run(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    result = run_regime_backtest(payload)
    REGIME_SERVICE.record_backtest_result(result)
    return result


@router.get("/backtests/routes", summary="Describe Regime backtest API routes")
def regime_backtest_routes() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "routes": [
            {
                "method": "GET",
                "path": "/api/regime/backtests/status",
                "purpose": "Regime backtest status and artifact metadata.",
            },
            {
                "method": "POST",
                "path": "/api/regime/evaluate",
                "purpose": "Execute the backend Regime decision pipeline.",
            },
            {
                "method": "POST",
                "path": "/api/regime/backtests/run",
                "purpose": "Execute the backend Regime backtest engine.",
            },
            {
                "method": "GET",
                "path": "/api/regime/backtests/routes",
                "purpose": "Regime backtest API route discovery.",
            },
        ],
    }


@router.get("/rollout/status", summary="Poll Regime staged paper rollout status")
def regime_rollout_status_route() -> dict[str, Any]:
    status = regime_rollout_status()
    status["finalAcceptance"] = build_regime_final_acceptance_report()
    return status


@router.get("/persistence/schema", summary="Describe Regime persistence schema")
def regime_persistence_schema() -> dict[str, Any]:
    return REGIME_SERVICE.persistence_schema()


@router.get("/backend/inventory", summary="Describe Regime backend ownership boundaries")
def regime_backend_inventory_route() -> dict[str, Any]:
    return REGIME_SERVICE.backend_inventory()


@router.post("/decisions/record", summary="Record a Regime decision snapshot")
def record_regime_decision(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    return REGIME_SERVICE.record_decision_snapshot(snapshot)


@router.post("/backtests/record", summary="Record a Regime backtest result")
def record_regime_backtest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    return REGIME_SERVICE.record_backtest_result(result)
