"""HTTP boundary for Weighted Voting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from backend.app.algorithms.weighted_voting.identity import (
    WEIGHTED_VOTING_ALGORITHM_ID,
    WEIGHTED_VOTING_API_NAMESPACE,
    WEIGHTED_VOTING_API_TAG,
    WEIGHTED_VOTING_API_VERSION,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService

router = APIRouter(prefix=WEIGHTED_VOTING_API_NAMESPACE, tags=[WEIGHTED_VOTING_API_TAG])
WEIGHTED_VOTING_API_SERVICE = WeightedVotingService()
WEIGHTED_VOTING_API_INVENTORY = (
    ("GET", "/status"),
    ("GET", "/config"),
    ("PUT", "/config"),
    ("POST", "/evaluate"),
    ("GET", "/decisions/{decision_id}"),
    ("GET", "/signals/{decision_id}"),
    ("GET", "/weights/active"),
    ("GET", "/weights/history"),
    ("POST", "/weights/recalculate"),
    ("POST", "/weights/rollback"),
    ("GET", "/performance"),
    ("GET", "/performance/strategies"),
    ("GET", "/performance/market-conditions"),
    ("POST", "/backtests"),
    ("GET", "/backtests/{run_id}"),
    ("GET", "/backtests/{run_id}/trades"),
    ("GET", "/backtests/{run_id}/decisions"),
    ("GET", "/backtests/{run_id}/equity"),
    ("GET", "/backtests/{run_id}/strategy-performance"),
    ("GET", "/daily-update/status"),
    ("POST", "/daily-update/run"),
    ("GET", "/positions"),
    ("GET", "/trades"),
    ("GET", "/observability/{decision_id}"),
)


class WeightedVotingErrorResponse(BaseModel):
    algorithm_id: str = WEIGHTED_VOTING_ALGORITHM_ID
    error_code: str
    message: str
    reason_codes: tuple[str, ...] = ()


class WeightedVotingCandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class WeightedVotingEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, examples=["SPY"])
    data_timestamp: datetime
    candles: tuple[WeightedVotingCandleRequest, ...] = Field(min_length=1)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    account_equity: float = Field(default=100_000.0, gt=0)
    available_buying_power: float = Field(default=100_000.0, ge=0)
    capital_available: float = Field(default=100_000.0, ge=0)


class WeightedVotingConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_risk_per_trade_percent: float | None = Field(default=None, ge=0, le=100)
    order_allocation_percent: float | None = Field(default=None, ge=0, le=100)
    daily_allocation_percent: float | None = Field(default=None, ge=0, le=100)
    maximum_position_percent: float | None = Field(default=None, ge=0, le=100)
    maximum_shares: int | None = Field(default=None, ge=0)
    maximum_trades: int | None = Field(default=None, ge=0)
    maximum_daily_loss_percent: float | None = Field(default=None, ge=0, le=100)
    maximum_participation_rate: float | None = Field(default=None, ge=0, le=1)
    minimum_score: float | None = Field(default=None, ge=0, le=1)
    minimum_edge: float | None = Field(default=None, ge=0, le=1)
    minimum_active_strategies: int | None = Field(default=None, ge=1, le=8)
    minimum_directional_strategies: int | None = Field(default=None, ge=1, le=8)
    maximum_spread_percent: float | None = Field(default=None, ge=0, le=1)
    minimum_liquidity_volume: float | None = Field(default=None, ge=0)
    atr_stop_multiplier: float | None = Field(default=None, ge=0)
    minimum_stop_distance_percent: float | None = Field(default=None, ge=0, le=1)
    target_r: float | None = Field(default=None, ge=0)
    entry_buffer_percent: float | None = Field(default=None, ge=0, le=1)
    break_even_trigger_r: float | None = Field(default=None, ge=0)
    trailing_stop_atr_multiplier: float | None = Field(default=None, ge=0)
    time_stop_minutes: int | None = Field(default=None, ge=0)
    session_cutoff_minutes: int | None = Field(default=None, ge=0)
    pyramiding_enabled: bool | None = None


class WeightedVotingBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = Field(default=None, min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    candles: tuple[WeightedVotingCandleRequest, ...] = Field(min_length=1)


class WeightedVotingDailyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    symbol: str = Field(default="SPY", min_length=1)
    completed_at: datetime
    candles: tuple[WeightedVotingCandleRequest, ...] = Field(min_length=1)


class WeightedVotingWeightRecalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: str | None = Field(default=None)
    update_timestamp: datetime | None = None
    regime_label: str | None = None
    outcomes: tuple[dict[str, Any], ...] = ()


class WeightedVotingWeightRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_weight_version: str = Field(min_length=1)
    rollback_timestamp: datetime | None = None


def api_inventory() -> dict[str, Any]:
    return {
        "apiVersion": WEIGHTED_VOTING_API_VERSION,
        "apiNamespace": WEIGHTED_VOTING_API_NAMESPACE,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "endpoints": [
            {
                "method": method,
                "path": f"{WEIGHTED_VOTING_API_NAMESPACE}{path}",
                "relativePath": path,
            }
            for method, path in WEIGHTED_VOTING_API_INVENTORY
        ],
        "isolated": True,
        "reasonCodes": ("weighted_voting.api.inventory.ready",),
    }


def router_status() -> dict[str, Any]:
    return {
        "apiVersion": WEIGHTED_VOTING_API_VERSION,
        "apiNamespace": WEIGHTED_VOTING_API_NAMESPACE,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "apiInventory": api_inventory(),
        "status": "registered",
        "explanation": "Dedicated Weighted Voting API routes are registered and isolated from other algorithm APIs.",
    }


@router.post(
    "/evaluate",
    responses={400: {"model": WeightedVotingErrorResponse}},
    summary="Evaluate Weighted Voting",
    description="Evaluate the backend-authoritative Weighted Voting algorithm from neutral market candles only.",
)
def evaluate(payload: WeightedVotingEvaluateRequest) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.evaluate(payload.model_dump(mode="json")))


@router.get("/status", summary="Weighted Voting status", description="Return isolated Weighted Voting API and service status.")
def status() -> dict[str, Any]:
    payload = WEIGHTED_VOTING_API_SERVICE.status()
    payload["apiInventory"] = api_inventory()
    return payload


@router.get("/config", summary="Get Weighted Voting config", description="Return backend-authoritative Weighted Voting settings/configuration.")
def get_config() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_config())


@router.put(
    "/config",
    responses={400: {"model": WeightedVotingErrorResponse}},
    summary="Update Weighted Voting config",
    description="Validate and persist backend-authoritative Weighted Voting settings. Browser storage is not authoritative.",
)
def put_config(payload: WeightedVotingConfigUpdateRequest) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True, mode="json")
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.put_config(values))


@router.get("/weights/active", summary="Get active weights", description="Return the active Weighted Voting weight state.")
def weights_active() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.weights_active())


@router.get("/weights/history", summary="Get weight history", description="Return historical Weighted Voting weight states recorded by the backend.")
def weights_history() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.weights_history())


@router.post("/weights/recalculate", responses={400: {"model": WeightedVotingErrorResponse}}, summary="Recalculate active weights")
def weights_recalculate(payload: WeightedVotingWeightRecalculateRequest) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.weights_recalculate(payload.model_dump(exclude_none=True, mode="json")))


@router.post("/weights/rollback", responses={400: {"model": WeightedVotingErrorResponse}}, summary="Rollback active weights")
def weights_rollback(payload: WeightedVotingWeightRollbackRequest) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.weights_rollback(payload.model_dump(exclude_none=True, mode="json")))


@router.get("/decisions/{decision_id}", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get decision")
def get_decision(decision_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_decision(decision_id), not_found="weighted_voting.decision.not_found")


@router.get("/signals/{decision_id}", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get decision signals")
def get_signals(decision_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_signals(decision_id), not_found="weighted_voting.signals.not_found")


@router.get("/performance", summary="Get algorithm performance")
def performance() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.performance())


@router.get("/performance/strategies", summary="Get strategy performance")
def performance_strategies() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.performance_strategies())


@router.get("/performance/market-conditions", summary="Get market-condition performance")
def performance_market_conditions() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.performance_market_conditions())


@router.post(
    "/backtests",
    responses={400: {"model": WeightedVotingErrorResponse}},
    summary="Create Weighted Voting backtest",
    description="Run a production-parity Weighted Voting backtest using the isolated backend engine.",
)
def create_backtest(payload: WeightedVotingBacktestRequest) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.create_backtest(payload.model_dump(mode="json")))


@router.get(
    "/backtests/{run_id}",
    responses={404: {"model": WeightedVotingErrorResponse}},
    summary="Get Weighted Voting backtest",
    description="Fetch a stored Weighted Voting backtest run by run ID.",
)
def get_backtest(run_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_backtest(run_id), not_found="weighted_voting.backtest.not_found")


@router.get("/backtests/{run_id}/trades", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get backtest trades")
def get_backtest_trades(run_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_backtest_collection(run_id, "trades"), not_found="weighted_voting.backtest.not_found")


@router.get("/backtests/{run_id}/decisions", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get backtest decisions")
def get_backtest_decisions(run_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_backtest_collection(run_id, "decisions"), not_found="weighted_voting.backtest.not_found")


@router.get("/backtests/{run_id}/equity", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get backtest equity curve")
def get_backtest_equity(run_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_backtest_collection(run_id, "equity"), not_found="weighted_voting.backtest.not_found")


@router.get("/backtests/{run_id}/strategy-performance", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get strategy performance")
def get_backtest_strategy_performance(run_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.get_backtest_collection(run_id, "strategyPerformance"), not_found="weighted_voting.backtest.not_found")


@router.get("/daily-update/status", summary="Get daily update status", description="Return the latest isolated Weighted Voting daily update status.")
def daily_update_status() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.daily_update_status())


@router.post(
    "/daily-update/run",
    responses={400: {"model": WeightedVotingErrorResponse}},
    summary="Run daily weight update",
    description="Run the idempotent after-market Weighted Voting daily update using neutral refreshed data.",
)
def daily_update_run(payload: WeightedVotingDailyUpdateRequest) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.run_daily_update(payload.model_dump(mode="json")))


@router.get("/positions", summary="Get Weighted Voting positions")
def positions() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.positions())


@router.get("/trades", summary="Get Weighted Voting trades")
def trades() -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.trades())


@router.get("/observability/{decision_id}", responses={404: {"model": WeightedVotingErrorResponse}}, summary="Get decision observability")
def observability(decision_id: str = Path(..., min_length=1)) -> dict[str, Any]:
    return _call(lambda: WEIGHTED_VOTING_API_SERVICE.observability(decision_id), not_found="weighted_voting.observability.not_found")


def _call(handler, *, not_found: str | None = None):
    try:
        return handler()
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=WeightedVotingErrorResponse(
                error_code=not_found or "weighted_voting.not_found",
                message=f"Weighted Voting resource not found: {exc}",
                reason_codes=(not_found or "weighted_voting.not_found",),
            ).model_dump(),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=WeightedVotingErrorResponse(
                error_code="weighted_voting.invalid_request",
                message=str(exc),
                reason_codes=("weighted_voting.invalid_request",),
            ).model_dump(),
        ) from exc
