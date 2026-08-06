from __future__ import annotations

from datetime import datetime

from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository


def readiness_report_ready() -> dict[str, object]:
    prerequisites = {
        "durableDatabaseAvailable": True,
        "activeSettingsPromotedForPaper": True,
        "paperBrokerVerified": True,
        "authoritativeMarketDataHealthy": True,
        "marketClockHealthy": True,
        "requiredWorkersHealthy": True,
        "queueLagBelowThreshold": True,
        "deadLetterWithinThreshold": True,
        "restartReconstructionSucceeded": True,
        "inventoryReconciliationCurrent": True,
        "globalRiskSourceCurrent": True,
        "requiredAcceptanceTestsPassed": True,
    }
    return {
        "algorithmId": "meta_strategy",
        "status": "OK",
        "complete": True,
        "paperReady": True,
        **prerequisites,
        "paperEntryReadinessPrerequisites": prerequisites,
        "operationalPrerequisites": prerequisites,
        "runtimeSupervisor": {
            "algorithmId": "meta_strategy",
            "enabled": True,
            "ready": True,
            "status": "ready",
            "mode": "PAPER",
            "paperOrdersBlocked": False,
        },
        "currentShadowPaperStatus": {"paperOrdersBlocked": False, "liveExecutionEnabled": False},
    }


def arm_automatic_paper_trading(repository: MetaStrategyJobRepository, *, now: datetime) -> None:
    repository.write_gateway_snapshot(
        "meta_strategy.runtime.readiness",
        {
            "algorithmId": "meta_strategy",
            "enabled": True,
            "ready": True,
            "status": "ready",
            "mode": "PAPER",
            "paperOrdersBlocked": False,
            "marketWorkersHealthy": True,
            "paperReadinessPrerequisites": {
                "durableDatabaseAvailable": True,
                "activeSettingsPromotedForPaper": True,
                "paperBrokerVerified": True,
                "authoritativeMarketDataHealthy": True,
                "marketClockHealthy": True,
                "requiredWorkersHealthy": True,
                "queueLagBelowThreshold": True,
                "deadLetterWithinThreshold": True,
                "restartReconstructionSucceeded": True,
                "inventoryReconciliationCurrent": True,
                "globalRiskSourceCurrent": True,
                "requiredAcceptanceTestsPassed": True,
            },
            "workers": {
                "finalised_bar_decisions": "healthy",
                "order_submission": "healthy",
                "order_reconciliation": "healthy",
                "stale_order_handling": "healthy",
                "inventory_reconciliation": "healthy",
                "position_management": "healthy",
            },
            "queueLagSeconds": {
                "finalised_bar_decisions": 0,
                "order_submission": 0,
                "order_reconciliation": 0,
                "stale_order_handling": 0,
                "inventory_reconciliation": 0,
                "position_management": 0,
            },
            "deadLetterCount": 0,
            "restartState": {"status": "OK", "reasonCodes": ("meta_strategy.runtime.restart_reconstruction_succeeded",)},
            "reasonCodes": ("meta_strategy.runtime.ready",),
        },
        now=now,
    )
    repository.update_paper_trading_control(
        new_paper_entries_enabled=True,
        updated_by="test",
        reason="meta_strategy.test.enable_automatic_paper_trading",
        now=now,
    )
