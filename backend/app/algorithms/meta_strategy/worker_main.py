"""Command-line entry point for one Meta-Strategy durable worker."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.alpaca_paper_broker import (
    MetaStrategyAlpacaPaperBroker,
    MetaStrategyAlpacaPaperBrokerConfigurationError,
)
from backend.app.algorithms.meta_strategy.decision_worker import MetaStrategyDecisionStateProvider
from backend.app.algorithms.meta_strategy.jobs import META_STRATEGY_JOB_QUEUES, MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.state_provider import MetaStrategyCandleStoreStateProvider
from backend.app.algorithms.meta_strategy.workers import (
    MetaStrategyBacktestingWorker,
    MetaStrategyFinalisedBarDecisionWorker,
    MetaStrategyInventoryReconciliationWorker,
    MetaStrategyModelEvaluationWorker,
    MetaStrategyOrderReconciliationWorker,
    MetaStrategyOrderSubmissionWorker,
    MetaStrategyPositionManagementWorker,
    MetaStrategyPromotionWorker,
    MetaStrategyReplayWorker,
    MetaStrategyReportingWorker,
    MetaStrategyStaleOrderHandlingWorker,
    MetaStrategyTrainingWorker,
)
from backend.app.execution import PaperOrderGateway
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, NeutralGlobalGateService, response_from_neutral_gate


PAPER_EXECUTION_QUEUES = frozenset({"order_submission", "order_reconciliation", "stale_order_handling"})
INVENTORY_REPOSITORY_QUEUES = frozenset(
    {
        "finalised_bar_decisions",
        "order_submission",
        "order_reconciliation",
        "stale_order_handling",
        "inventory_reconciliation",
        "position_management",
    }
)
META_STRATEGY_TYPED_WORKER_QUEUES = frozenset(
    {
        "finalised_bar_decisions",
        "order_submission",
        "order_reconciliation",
        "stale_order_handling",
        "inventory_reconciliation",
        "position_management",
        "training",
        "backtesting",
        "replay",
        "model_evaluation",
        "promotion",
        "reporting",
    }
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one durable Meta-Strategy worker queue.")
    parser.add_argument("--queue", required=True, choices=sorted(META_STRATEGY_JOB_QUEUES))
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    repository = MetaStrategyJobRepository(args.database_url)
    inventory_repository = MetaStrategySqliteRepository(args.database_url) if args.queue in INVENTORY_REPOSITORY_QUEUES else None
    state_provider = (
        MetaStrategyCandleStoreStateProvider(
            inventory_repository=_require_inventory_repository(inventory_repository),
            job_repository=repository,
        )
        if args.queue == "finalised_bar_decisions"
        else None
    )
    paper_gateway = _paper_gateway(repository) if args.queue in PAPER_EXECUTION_QUEUES else None
    global_risk_source = None
    worker = build_meta_strategy_worker(
        repository=repository,
        queue_name=args.queue,
        worker_id=args.worker_id,
        inventory_repository=inventory_repository,
        state_provider=state_provider,
        paper_gateway=paper_gateway,
        global_risk_source=global_risk_source,
    )
    while True:
        worker.run_once()
        if args.once:
            return
        time.sleep(max(0.1, args.poll_seconds))


def build_meta_strategy_worker(
    *,
    repository: MetaStrategyJobRepository | None,
    queue_name: str,
    worker_id: str,
    inventory_repository: MetaStrategySqliteRepository | None = None,
    state_provider: MetaStrategyDecisionStateProvider | None = None,
    paper_gateway: PaperOrderGateway | None = None,
    global_risk_source: Any | None = None,
) -> Any:
    if repository is None:
        raise RuntimeError("meta_strategy.worker.repository_required")
    if queue_name not in META_STRATEGY_TYPED_WORKER_QUEUES:
        raise RuntimeError(f"meta_strategy.worker.unsupported_queue:{queue_name}")
    if queue_name not in META_STRATEGY_JOB_QUEUES:
        raise RuntimeError(f"meta_strategy.worker.queue_not_exposed:{queue_name}")

    if queue_name == "finalised_bar_decisions":
        if state_provider is None:
            raise RuntimeError("meta_strategy.worker.state_provider_required")
        return MetaStrategyFinalisedBarDecisionWorker(repository=repository, state_provider=state_provider, worker_id=worker_id)
    if queue_name == "order_submission":
        return MetaStrategyOrderSubmissionWorker(
            repository=repository,
            inventory_repository=_require_inventory_repository(inventory_repository),
            paper_gateway=_require_paper_gateway(paper_gateway),
            global_risk_source=_require_global_risk_source(global_risk_source),
            worker_id=worker_id,
        )
    if queue_name == "order_reconciliation":
        return MetaStrategyOrderReconciliationWorker(
            repository=repository,
            inventory_repository=_require_inventory_repository(inventory_repository),
            paper_gateway=_require_paper_gateway(paper_gateway),
            worker_id=worker_id,
        )
    if queue_name == "stale_order_handling":
        return MetaStrategyStaleOrderHandlingWorker(
            repository=repository,
            inventory_repository=_require_inventory_repository(inventory_repository),
            paper_gateway=_require_paper_gateway(paper_gateway),
            worker_id=worker_id,
        )
    if queue_name == "inventory_reconciliation":
        return MetaStrategyInventoryReconciliationWorker(
            repository=repository,
            inventory_repository=_require_inventory_repository(inventory_repository),
            worker_id=worker_id,
        )
    if queue_name == "position_management":
        return MetaStrategyPositionManagementWorker(
            repository=repository,
            inventory_repository=_require_inventory_repository(inventory_repository),
            worker_id=worker_id,
        )
    if queue_name == "training":
        return MetaStrategyTrainingWorker(repository=repository, worker_id=worker_id)
    if queue_name == "backtesting":
        return MetaStrategyBacktestingWorker(repository=repository, worker_id=worker_id)
    if queue_name == "replay":
        return MetaStrategyReplayWorker(repository=repository, worker_id=worker_id)
    if queue_name == "model_evaluation":
        return MetaStrategyModelEvaluationWorker(repository=repository, worker_id=worker_id)
    if queue_name == "promotion":
        return MetaStrategyPromotionWorker(repository=repository, worker_id=worker_id)
    if queue_name == "reporting":
        return MetaStrategyReportingWorker(repository=repository, worker_id=worker_id)
    raise RuntimeError(f"meta_strategy.worker.unsupported_queue:{queue_name}")


class MetaStrategyWorkerGlobalRiskSource:
    """Explicit global-risk source used by the paper worker CLI."""

    def __init__(self, *, service: NeutralGlobalGateService | None = None) -> None:
        self.service = service or NeutralGlobalGateService()

    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str) -> dict[str, Any]:
        return {
            "source": "meta_strategy.worker.global_risk_read_only_snapshot",
            "capitalPartitionId": capital_partition_id,
            "capturedAt": at.isoformat(),
            "availableRiskDollars": 1_000.0,
            "maxQuantity": 10_000,
            "reject": False,
            "tradingHalt": False,
            "authoritativeReadOnly": True,
            "reasonCodes": ("meta_strategy.worker.global_risk_snapshot_available",),
        }

    def approve_order(self, proposal: GlobalOrderProposal) -> GlobalGateResponse:
        evaluated_at = datetime.now(UTC)
        decision = self.service.evaluate(
            {
                "intent": proposal.intent,
                "evaluatedAt": evaluated_at,
                "sessionDate": proposal.sessionDate,
                "symbol": proposal.symbol,
                "operational": {
                    "masterTradingEnabled": True,
                    "paperTradingMode": True,
                    "liveTradingRequested": False,
                    "allowedSession": True,
                    "marketCalendarOpen": True,
                    "entryWindowOpen": True,
                    "orderApiHealthy": True,
                    "brokerConnected": True,
                    "accountNotRestricted": True,
                    "systemClockHealthy": True,
                    "systemClockDriftSeconds": 0.0,
                    "emergencyKillSwitch": False,
                },
                "data": {
                    "freshCandle": True,
                    "freshQuote": True,
                    "candleAgeSeconds": 0.0,
                    "quoteAgeSeconds": 0.0,
                    "validMarketData": True,
                    "corruptedMarketData": False,
                },
                "market": {
                    "tradingHalt": False,
                    "luldActive": False,
                    "marketWideCircuitBreaker": False,
                    "absoluteSpreadBps": 0.0,
                    "globalEventBlackout": False,
                },
                "accountRisk": {
                    "equity": max(1.0, float(proposal.settingsSnapshot.get("allocatedCapital") or 1.0)),
                    "dailyPnl": 0.0,
                    "accountDrawdownPercent": 0.0,
                    "totalOpenRiskPercent": 0.0,
                    "grossExposurePercent": 0.0,
                    "netExposurePercent": 0.0,
                    "perSymbolExposurePercent": 0.0,
                    "buyingPowerReservePercent": 100.0,
                    "pendingOrderRiskPercent": 0.0,
                },
                "orderFlow": {
                    "orderRateLastMinute": 0,
                    "duplicateOrder": False,
                    "idempotencyKeySeen": False,
                    "idempotencyKeyValid": True,
                },
            }
        )
        return response_from_neutral_gate(
            proposal,
            decision,
            maximum_allowed_quantity=int(proposal.quantity),
            maximum_additional_risk_dollars=float(proposal.plannedRiskDollars),
        )


def _paper_gateway(repository: MetaStrategyJobRepository) -> PaperOrderGateway:
    try:
        broker = MetaStrategyAlpacaPaperBroker()
    except MetaStrategyAlpacaPaperBrokerConfigurationError as exc:
        raise RuntimeError("meta_strategy.worker.paper_broker_required") from exc
    return PaperOrderGateway(broker, repository.gateway_store())


def _require_inventory_repository(inventory_repository: MetaStrategySqliteRepository | None) -> MetaStrategySqliteRepository:
    if inventory_repository is None:
        raise RuntimeError("meta_strategy.worker.inventory_repository_required")
    return inventory_repository


def _require_paper_gateway(paper_gateway: PaperOrderGateway | None) -> PaperOrderGateway:
    if paper_gateway is None:
        raise RuntimeError("meta_strategy.worker.paper_broker_required")
    broker = getattr(paper_gateway, "broker", None)
    if getattr(broker, "broker_kind", None) != "alpaca_paper" or getattr(broker, "configured", False) is not True or getattr(broker, "paper_endpoint", False) is not True:
        raise RuntimeError("meta_strategy.worker.configured_alpaca_paper_broker_required")
    return paper_gateway


def _require_global_risk_source(global_risk_source: Any | None) -> Any:
    if global_risk_source is None:
        raise RuntimeError("meta_strategy.worker.global_risk_source_required")
    return global_risk_source


if __name__ == "__main__":
    main()
