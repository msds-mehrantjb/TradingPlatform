"""HTTP schema boundary for WCA."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.algorithms.wca.contracts import (
    BacktestResult,
    WcaBacktestRequest,
    WcaBacktestSuiteResult,
    WcaBaselineSettings,
    WcaDecision,
    WcaEvaluateRequest,
    WcaEvaluateResponse,
    WcaPaperExecutionRequest,
    WcaPaperExecutionResult,
    WcaPaperStabilityValidationRequest,
    WcaPaperStabilityValidationResult,
    WcaShadowComparisonEvidence,
)
from backend.app.algorithms.wca.engine import WcaEngineInputError
from backend.app.algorithms.wca.service import WcaService


router = APIRouter(prefix="/api/wca", tags=["wca"])
WCA_API_SERVICE = WcaService()


@router.get("/status", summary="WCA backend boundary status")
def status() -> dict[str, Any]:
    return WCA_API_SERVICE.status()


@router.get("/config/baseline", response_model=WcaBaselineSettings, summary="WCA baseline settings schema")
def baseline_config() -> WcaBaselineSettings:
    return WcaBaselineSettings.model_validate(WCA_API_SERVICE.baseline_settings())


@router.get("/configuration", summary="WCA legacy-compatible backend configuration")
def get_configuration() -> dict[str, Any]:
    return WCA_API_SERVICE.configuration()


@router.put("/configuration", summary="Update WCA legacy-compatible backend configuration")
def put_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    return WCA_API_SERVICE.update_configuration(payload)


@router.post("/evaluate", response_model=WcaEvaluateResponse, summary="Evaluate WCA with the legacy-compatible backend engine")
def evaluate(payload: WcaEvaluateRequest) -> WcaEvaluateResponse:
    try:
        return WCA_API_SERVICE.evaluate(payload)
    except WcaEngineInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/shadow/compare", response_model=WcaShadowComparisonEvidence, summary="Record legacy/backend WCA shadow comparison evidence without submitting orders")
def shadow_compare(payload: WcaEvaluateRequest) -> WcaShadowComparisonEvidence:
    try:
        return WCA_API_SERVICE.record_shadow_comparison_evidence(payload)
    except WcaEngineInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/paper/execute", response_model=WcaPaperExecutionResult, summary="Execute a WCA paper action through the shared backend pipeline")
def execute_paper(payload: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
    return WCA_API_SERVICE.execute_paper(payload)


@router.post("/paper/manual", response_model=WcaPaperExecutionResult, summary="Execute a manual WCA paper action through the shared backend pipeline")
def execute_manual_paper(payload: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
    return WCA_API_SERVICE.execute_manual_paper(payload)


@router.post("/paper/automatic", response_model=WcaPaperExecutionResult, summary="Execute an automatic WCA paper action through the shared backend pipeline")
def execute_automatic_paper(payload: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
    return WCA_API_SERVICE.execute_automatic_paper(payload)


@router.post("/paper/stability/validate", response_model=WcaPaperStabilityValidationResult, summary="Validate stable WCA paper trading evidence before rollout acceptance")
def validate_paper_stability(payload: WcaPaperStabilityValidationRequest) -> WcaPaperStabilityValidationResult:
    return WCA_API_SERVICE.validate_paper_stability(payload)


@router.post("/schema/decision", response_model=WcaDecision, include_in_schema=True, summary="WCA decision schema echo")
def decision_schema_echo(payload: WcaDecision) -> WcaDecision:
    return payload


@router.post("/backtests", response_model=BacktestResult, summary="Run a backend-authoritative WCA backtest")
def run_backtest(payload: WcaBacktestRequest) -> BacktestResult:
    return WCA_API_SERVICE.run_backtest(payload)


@router.post("/backtests/modes", response_model=WcaBacktestSuiteResult, summary="Run labeled WCA backtest modes and comparisons")
def run_backtest_modes(payload: WcaBacktestRequest) -> WcaBacktestSuiteResult:
    return WCA_API_SERVICE.run_backtest_modes(payload)


@router.get("/backtests/{run_id}/status", summary="Poll WCA backtest status")
def backtest_status(run_id: str) -> dict[str, Any]:
    return WCA_API_SERVICE.backtest_status(run_id)


@router.get("/backtests/{run_id}", summary="Fetch a WCA backtest result")
def backtest_result(run_id: str) -> dict[str, Any]:
    result = WCA_API_SERVICE.backtest_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="WCA backtest run not found")
    return result.model_dump(mode="json")


@router.get("/backtests/{run_id}/report", summary="Download a WCA backtest report payload")
def backtest_report(run_id: str) -> dict[str, Any]:
    report = WCA_API_SERVICE.backtest_report(run_id)
    if report.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="WCA backtest run not found")
    return report
