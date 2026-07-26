"""HTTP schema boundary for WCA."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.algorithms.wca.contracts import (
    WcaBacktestRequest,
    WcaBaselineSettings,
    WcaDecision,
    WcaEvaluateRequest,
    WcaPaperExecutionRequest,
    WcaPaperStabilityValidationRequest,
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


@router.get("/inventory", summary="Read authoritative WCA inventory")
def inventory() -> dict[str, Any]:
    return WCA_API_SERVICE.inventory()


@router.get("/configuration", summary="WCA legacy-compatible backend configuration")
def get_configuration() -> dict[str, Any]:
    return WCA_API_SERVICE.configuration()


@router.put("/configuration", summary="Create a WCA candidate configuration")
def put_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    return WCA_API_SERVICE.update_configuration(payload)


@router.post("/configuration/{configuration_version}/activate", status_code=202, summary="Enqueue WCA configuration activation")
def activate_configuration(configuration_version: str) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_configuration_activation(configuration_version)


@router.post("/configuration/{configuration_version}/rollback", status_code=202, summary="Enqueue WCA configuration rollback")
def rollback_configuration(configuration_version: str) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_configuration_rollback(configuration_version)


@router.post("/evaluate", status_code=202, summary="Enqueue WCA shadow comparison for legacy evaluation payloads")
def evaluate(payload: WcaEvaluateRequest) -> dict[str, Any]:
    try:
        return WCA_API_SERVICE.enqueue_evaluation_request(payload).model_dump(mode="json")
    except WcaEngineInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/shadow/compare", status_code=202, summary="Enqueue WCA shadow comparison research evidence generation")
def shadow_compare(payload: WcaEvaluateRequest) -> dict[str, Any]:
    try:
        return WCA_API_SERVICE.enqueue_shadow_comparison(payload).model_dump(mode="json")
    except WcaEngineInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/paper/execute", status_code=202, summary="Enqueue a WCA paper command")
def execute_paper(payload: WcaPaperExecutionRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_paper_command(payload)


@router.post("/paper/manual", status_code=202, summary="Enqueue a manual WCA paper command")
def execute_manual_paper(payload: WcaPaperExecutionRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_paper_command(payload, mode="manual")


@router.post("/paper/automatic", status_code=202, summary="Enqueue an automatic WCA paper command")
def execute_automatic_paper(payload: WcaPaperExecutionRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_paper_command(payload, mode="automatic")


@router.post("/paper/stability/validate", status_code=202, summary="Enqueue WCA paper-stability research report")
def validate_paper_stability(payload: WcaPaperStabilityValidationRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_paper_stability_report(payload).model_dump(mode="json")


@router.post("/schema/decision", response_model=WcaDecision, include_in_schema=True, summary="WCA decision schema echo")
def decision_schema_echo(payload: WcaDecision) -> WcaDecision:
    return payload


@router.post("/backtests", status_code=202, summary="Enqueue a backend-authoritative WCA backtest research job")
def run_backtest(payload: WcaBacktestRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_backtest(payload).model_dump(mode="json")


@router.post("/backtests/modes", status_code=202, summary="Enqueue labeled WCA backtest modes and comparisons")
def run_backtest_modes(payload: WcaBacktestRequest) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_backtest_modes(payload).model_dump(mode="json")


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


@router.get("/research/jobs/{job_id}", summary="Poll a WCA research job")
def research_job_status(job_id: str) -> dict[str, Any]:
    status = WCA_API_SERVICE.research_job_status(job_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="WCA research job not found")
    return status


@router.post("/research/jobs/{job_id}/cancel", summary="Request cancellation for a queued or running WCA research job")
def cancel_research_job(job_id: str) -> dict[str, Any]:
    return WCA_API_SERVICE.cancel_research_job(job_id)


@router.get("/runtime/health", summary="Read WCA runtime process health")
def runtime_health() -> dict[str, Any]:
    return WCA_API_SERVICE.status().get("runtimeHealth", {})


@router.get("/commands/{command_id}", summary="Read WCA runtime command progress")
def command_status(command_id: str) -> dict[str, Any]:
    status = WCA_API_SERVICE.command_status(command_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="WCA command not found")
    return status


@router.post("/runtime/pause", status_code=202, summary="Enqueue WCA pause-new-entries command")
def pause_new_entries(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_pause_new_entries(reason=str((payload or {}).get("reason") or "api_request"))


@router.post("/runtime/resume", status_code=202, summary="Enqueue WCA resume-new-entries command")
def resume_new_entries(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return WCA_API_SERVICE.enqueue_resume_new_entries(reason=str((payload or {}).get("reason") or "api_request"))


@router.post("/reconciliation/request", status_code=202, summary="Enqueue WCA broker reconciliation")
def request_reconciliation(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    return WCA_API_SERVICE.enqueue_reconciliation_request(account_id=str(body.get("accountId") or body.get("account_id") or "paper"), symbol=str(body.get("symbol") or "SPY"))


@router.post("/risk/emergency-reduce", status_code=202, summary="Enqueue WCA emergency risk reduction")
def request_emergency_risk_reduction(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    return WCA_API_SERVICE.enqueue_emergency_risk_reduction(account_id=str(body.get("accountId") or body.get("account_id") or "paper"), symbol=str(body.get("symbol") or "SPY"), reason=str(body.get("reason") or "api_request"))


@router.get("/decisions", summary="Read recent WCA decisions")
def decisions(limit: int = 20) -> dict[str, Any]:
    return {"algorithmId": "wca", "decisions": WCA_API_SERVICE.latest_decisions(limit=limit)}


@router.get("/trades", summary="Read recent WCA trades")
def trades(limit: int = 50) -> dict[str, Any]:
    return {"algorithmId": "wca", "trades": WCA_API_SERVICE.latest_trades(limit=limit)}


@router.get("/inventory/virtual", summary="Read WCA virtual inventory")
def virtual_inventory(account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
    return WCA_API_SERVICE.virtual_inventory(account_id=account_id, symbol=symbol)
