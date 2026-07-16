from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.app.risk.types import AccountSnapshot


def account_snapshot_from_mapping(payload: dict[str, Any], *, observed_at: datetime | None = None) -> AccountSnapshot:
    now = observed_at or datetime.now(UTC)
    equity = float(payload.get("equity", payload.get("accountEquity", 100_000.0)))
    return AccountSnapshot(
        accountSnapshotId=str(payload.get("accountSnapshotId") or f"acct-{uuid4().hex}"),
        accountId=str(payload.get("accountId") or "paper-account"),
        equity=equity,
        highWaterEquity=float(payload.get("highWaterEquity", payload.get("high_water_equity", equity))),
        availableBuyingPower=float(payload.get("availableBuyingPower", payload.get("buyingPower", equity))),
        settledCash=payload.get("settledCash"),
        realizedDailyPnl=float(payload.get("realizedDailyPnl", 0.0)),
        unrealizedDailyPnl=float(payload.get("unrealizedDailyPnl", 0.0)),
        brokerConnected=bool(payload.get("brokerConnected", True)),
        brokerAccountActive=bool(payload.get("brokerAccountActive", True)),
        tradingPermission=bool(payload.get("tradingPermission", True)),
        clockSynchronized=bool(payload.get("clockSynchronized", True)),
        accountSnapshotFresh=bool(payload.get("accountSnapshotFresh", True)),
        localBrokerOrdersReconciled=bool(payload.get("localBrokerOrdersReconciled", True)),
        localBrokerPositionsReconciled=bool(payload.get("localBrokerPositionsReconciled", True)),
        unresolvedSubmissionFailure=bool(payload.get("unresolvedSubmissionFailure", False)),
        brokerRateLimited=bool(payload.get("brokerRateLimited", False)),
        observedAt=now,
    )


__all__ = ["account_snapshot_from_mapping"]
