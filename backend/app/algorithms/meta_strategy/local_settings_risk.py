"""Meta-Strategy local paper account and risk authority from backend settings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings, MetaStrategySettingsStore
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal


META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION = "meta_strategy_local_settings_risk_source_v1"
_AUTO_QUANTITY_CAP = 1_000_000_000


class MetaStrategyLocalSettingsRiskSource:
    """Uses Meta-Strategy settings plus its own inventory ledger as paper risk state."""

    source_kind = "meta_strategy_local_settings_risk"
    configured = True

    def __init__(
        self,
        *,
        settings_store: MetaStrategySettingsStore,
        inventory_repository: MetaStrategySqliteRepository,
    ) -> None:
        self.settings_store = settings_store
        self.inventory_repository = inventory_repository

    def read_account_snapshot(self, *, at: datetime) -> Mapping[str, Any]:
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot()
        equity = _account_equity(snapshot.allocated_capital, snapshot.realised_pnl, snapshot.unrealised_pnl, snapshot.fees_and_slippage)
        buying_power = _buying_power(equity, snapshot.reserved_risk_dollars, snapshot.symbol_exposure)
        return {
            "source": self.source_kind,
            "sourceVersion": META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION,
            "authoritativeReadOnly": True,
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": snapshot.capital_partition_id,
            "settingsVersion": settings.settings_version,
            "capturedAt": min(_ensure_utc(at), datetime.now(UTC)).isoformat(),
            "accountId": f"{ALGORITHM_ID}:{snapshot.capital_partition_id}",
            "accountType": "paper",
            "liveTradingEnabled": False,
            "accountEquity": equity,
            "buyingPower": buying_power,
            "cashAvailable": buying_power,
            "reservedRiskDollars": snapshot.reserved_risk_dollars,
            "realisedPnl": snapshot.realised_pnl,
            "unrealisedPnl": snapshot.unrealised_pnl,
            "paperAccountVerified": True,
            "reasonCodes": ("meta_strategy.local_settings.account_snapshot_loaded",),
        }

    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str) -> Mapping[str, Any]:
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot()
        if capital_partition_id != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
            return _risk_snapshot(
                settings=settings,
                snapshot=snapshot,
                at=at,
                available_risk=0.0,
                max_quantity=0,
                reject=True,
                reasons=("meta_strategy.local_settings_risk.wrong_capital_partition",),
            )
        available_risk = _remaining_risk(settings, snapshot)
        max_quantity = _max_quantity(settings)
        return _risk_snapshot(
            settings=settings,
            snapshot=snapshot,
            at=at,
            available_risk=available_risk,
            max_quantity=max_quantity,
            reject=available_risk <= 0.0,
            reasons=(
                "meta_strategy.local_settings_risk.zero_available_risk"
                if available_risk <= 0.0
                else "meta_strategy.local_settings_risk.snapshot_loaded"
            ),
        )

    def approve_order(self, proposal: GlobalOrderProposal) -> GlobalGateResponse:
        evaluated_at = datetime.now(UTC)
        settings = self.settings_store.get_active_settings()
        snapshot = self.inventory_repository.current_inventory_snapshot()
        reasons: list[str] = []
        if proposal.algorithmId != ALGORITHM_ID:
            reasons.append("meta_strategy.local_settings_risk.foreign_algorithm_rejected")
        if proposal.capitalPartitionId != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
            reasons.append("meta_strategy.local_settings_risk.wrong_capital_partition")
        account = self.read_account_snapshot(at=evaluated_at)
        available_risk = _remaining_risk(settings, snapshot)
        max_quantity = min(int(proposal.quantity), _max_quantity(settings))
        if float(account["accountEquity"]) <= 0.0:
            reasons.append("meta_strategy.sizing.zero_account_equity")
        if float(account["buyingPower"]) <= 0.0:
            reasons.append("meta_strategy.sizing.zero_buying_power")
        if available_risk <= 0.0:
            reasons.append("meta_strategy.sizing.zero_algorithm_risk")
        if proposal.quantity <= 0 or max_quantity <= 0:
            reasons.append("meta_strategy.sizing.approved_quantity_zero")
        if proposal.plannedRiskDollars > available_risk and proposal.quantity > 0 and available_risk > 0.0:
            max_quantity = min(max_quantity, int(proposal.quantity * (available_risk / proposal.plannedRiskDollars)))
            reasons.append("meta_strategy.local_settings_risk.quantity_reduced_to_available_risk")
        action = "ALLOW" if not reasons and max_quantity > 0 else "REJECT_NEW_ENTRY"
        if "meta_strategy.local_settings_risk.quantity_reduced_to_available_risk" in reasons and max_quantity > 0:
            action = "REDUCE_QUANTITY"
        return GlobalGateResponse(
            action=action,  # type: ignore[arg-type]
            maximumAllowedQuantity=max(0, max_quantity if action != "REJECT_NEW_ENTRY" else 0),
            maximumAdditionalRiskDollars=max(0.0, min(float(proposal.plannedRiskDollars), available_risk)),
            rejectionReasons=tuple(reasons),
            evaluatedAt=evaluated_at,
            configurationHash=f"{settings.settings_hash}:{snapshot.snapshot_id}",
        )


def _risk_snapshot(
    *,
    settings: MetaStrategySettings,
    snapshot: Any,
    at: datetime,
    available_risk: float,
    max_quantity: int,
    reject: bool,
    reasons: tuple[str, ...],
) -> Mapping[str, Any]:
        return {
            "source": "meta_strategy_local_settings_risk",
            "sourceVersion": META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION,
            "authoritativeReadOnly": True,
            "current": True,
            "status": "OK",
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": snapshot.capital_partition_id,
        "settingsVersion": settings.settings_version,
        "capturedAt": min(_ensure_utc(at), datetime.now(UTC)).isoformat(),
        "availableRiskDollars": max(0.0, float(available_risk)),
        "maxQuantity": max(0, int(max_quantity)),
        "reservedRiskDollars": snapshot.reserved_risk_dollars,
        "reject": reject,
        "tradingHalt": False,
        "reasonCodes": reasons,
    }


def _remaining_risk(settings: MetaStrategySettings, snapshot: Any) -> float:
    equity = _account_equity(snapshot.allocated_capital, snapshot.realised_pnl, snapshot.unrealised_pnl, snapshot.fees_and_slippage)
    configured_trade_risk = equity * settings.local_risk.risk_percentage
    daily_loss_remaining = max(0.0, settings.local_risk.maximum_daily_loss + snapshot.realised_pnl)
    open_risk_remaining = max(0.0, settings.local_risk.maximum_open_risk - snapshot.reserved_risk_dollars)
    return round(max(0.0, min(configured_trade_risk, daily_loss_remaining, open_risk_remaining)), 10)


def _account_equity(allocated_capital: float, realised_pnl: float, unrealised_pnl: float, fees_and_slippage: float) -> float:
    return round(max(0.0, float(allocated_capital) + float(realised_pnl) + float(unrealised_pnl) - float(fees_and_slippage)), 10)


def _buying_power(equity: float, reserved_risk: float, symbol_exposure: Mapping[str, float]) -> float:
    gross_exposure = sum(abs(float(value)) for value in symbol_exposure.values())
    return round(max(0.0, float(equity) - float(reserved_risk) - gross_exposure), 10)


def _max_quantity(settings: MetaStrategySettings) -> int:
    configured = int(settings.position_sizing.maximum_share_quantity)
    return configured if configured > 0 else _AUTO_QUANTITY_CAP


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["META_STRATEGY_LOCAL_SETTINGS_RISK_SOURCE_VERSION", "MetaStrategyLocalSettingsRiskSource"]
