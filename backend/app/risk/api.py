from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from backend.app.risk.manager import GlobalPortfolioRiskManager
from backend.app.risk.types import GlobalGateDecision, GlobalRiskEvaluationRequest


router = APIRouter(prefix="/api/risk", tags=["global-risk"])


class GlobalRiskEvaluateRequest(GlobalRiskEvaluationRequest):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reserve: bool = False


class GlobalRiskEvaluateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpointVersion: str = "global_risk_evaluate_v1"
    decision: GlobalGateDecision
    explanation: str = "Global portfolio risk manager evaluated the order intent server-side without changing algorithm signals or settings."


_manager = GlobalPortfolioRiskManager()


@router.post("/global/evaluate", response_model=GlobalRiskEvaluateResponse)
def evaluate_global_risk(request: GlobalRiskEvaluateRequest) -> GlobalRiskEvaluateResponse:
    decision = _manager.evaluate(
        intent=request.intent,
        account=request.account,
        market=request.market,
        portfolio=request.portfolio,
        reserve=request.reserve,
    )
    return GlobalRiskEvaluateResponse(decision=decision)


__all__ = ["router"]
