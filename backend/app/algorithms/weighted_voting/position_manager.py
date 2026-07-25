"""Autonomous Weighted Voting position and trade manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Literal, Protocol

from backend.app.algorithms.weighted_voting.exit_policy import (
    WeightedVotingExitDecision,
    WeightedVotingExitInputs,
    WeightedVotingExitLifecycleState,
    evaluate_exit_lifecycle,
    open_exit_lifecycle,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository, WeightedVotingPosition
from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings, WeightedMarketQuality, WeightedSide
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_POSITION_MANAGER_VERSION = "weighted_voting_position_manager_v1"
WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE = "weighted_voting.position_manager"
PROTECTION_PREFIX = f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.protection."
TRADE_PREFIX = f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.trades."
LINKAGE_PREFIX = f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.linkage."
CHECKPOINT_KEY = f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.checkpoint.latest"


class WeightedVotingManagedOrderType(str, Enum):
    STOP = "protective_stop"
    TARGET = "profit_target"
    EXIT = "exit"


class WeightedVotingProtectiveOrderBroker(Protocol):
    def submit_protective_order(self, instruction: "WeightedVotingProtectiveInstruction") -> str:
        ...

    def submit_exit_order(self, instruction: "WeightedVotingExitInstruction") -> str:
        ...


@dataclass(frozen=True)
class WeightedVotingProtectiveInstruction:
    algorithm_id: Literal["weighted_voting"]
    instruction_id: str
    client_order_id: str
    position_id: str
    trade_id: str
    symbol: str
    side: str
    quantity: int
    stop_price: float
    target_price: float
    settings_version: str
    settings_hash: str
    broker_held_preferred: bool
    created_at: datetime
    supporting_strategy_ids: tuple[str, ...]
    broker_stop_order_id: str | None = None
    broker_target_order_id: str | None = None
    reason_codes: tuple[str, ...] = ("weighted_voting.position_manager.protective_instruction_created",)

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingExitInstruction:
    algorithm_id: Literal["weighted_voting"]
    instruction_id: str
    client_order_id: str
    position_id: str
    trade_id: str
    symbol: str
    side: str
    quantity: int
    exit_reason: str
    exit_price: float
    settings_version: str
    created_at: datetime
    emergency: bool = False

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingAuthoritativeTradeRecord:
    algorithm_id: Literal["weighted_voting"]
    trade_id: str
    position_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    gross_pnl: float
    estimated_costs: float
    realised_costs: float
    net_pnl: float
    mae: float
    mfe: float
    holding_seconds: float
    supporting_strategy_ids: tuple[str, ...]
    settings_version: str
    settings_hash: str
    entry_order_id: str
    stop_order_id: str | None
    target_order_id: str | None
    exit_order_id: str | None
    reason_codes: tuple[str, ...]
    manager_version: str = WEIGHTED_VOTING_POSITION_MANAGER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class WeightedVotingPositionManagerService:
    def __init__(
        self,
        *,
        store: WeightedVotingStateStore,
        inventory_repository: WeightedVotingInventoryRepository,
        broker: WeightedVotingProtectiveOrderBroker | None = None,
    ) -> None:
        self.store = store
        self.inventory_repository = inventory_repository
        self.broker = broker

    def protect_position_on_entry_fill(
        self,
        *,
        position: WeightedVotingPosition,
        effective_settings: WeightedEffectiveSettings,
        entry_order_id: str,
        stop_price: float | None = None,
        target_price: float | None = None,
        supporting_strategy_ids: tuple[str, ...] = (),
        protected_at: datetime,
    ) -> WeightedVotingProtectiveInstruction:
        _require_weighted_voting(position.algorithm_id)
        trade_id = _trade_id(position.client_order_id)
        stop = stop_price if stop_price is not None else _fallback_stop(position, effective_settings)
        lifecycle = open_exit_lifecycle(
            trade_id=trade_id,
            symbol=position.symbol,
            side=WeightedSide.BUY.value if position.quantity > 0 else WeightedSide.SELL.value,
            quantity=abs(position.quantity),
            entry_price=position.average_entry_price,
            entry_timestamp=position.opened_at,
            stop_price=stop,
            effective_settings=effective_settings,
            supporting_strategy_ids=supporting_strategy_ids,
        )
        target = target_price if target_price is not None else lifecycle.profit_target
        instruction = WeightedVotingProtectiveInstruction(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            instruction_id=f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.protect.{position.client_order_id}",
            client_order_id=position.client_order_id,
            position_id=position.position_id,
            trade_id=trade_id,
            symbol=position.symbol,
            side="SELL" if position.quantity > 0 else "BUY",
            quantity=abs(position.quantity),
            stop_price=stop,
            target_price=target,
            settings_version=effective_settings.settings_version,
            settings_hash=effective_settings.configuration_hash,
            broker_held_preferred=True,
            created_at=protected_at,
            supporting_strategy_ids=supporting_strategy_ids,
        )
        broker_stop_id = None
        broker_target_id = None
        if self.broker is not None:
            broker_stop_id = self.broker.submit_protective_order(instruction)
            broker_target_id = f"{broker_stop_id}.target"
            instruction = WeightedVotingProtectiveInstruction(**{**asdict(instruction), "broker_stop_order_id": broker_stop_id, "broker_target_order_id": broker_target_id})
        self.store.write_snapshot(_protection_key(position.client_order_id), instruction.as_dict())
        self.store.write_snapshot(_linkage_key(position.client_order_id), {"algorithmId": WEIGHTED_VOTING_ALGORITHM_ID, "entryOrderId": entry_order_id, "stopOrderId": instruction.broker_stop_order_id, "targetOrderId": instruction.broker_target_order_id, "tradeId": trade_id, "positionId": position.position_id, "settingsVersion": effective_settings.settings_version})
        self.store.write_snapshot(_lifecycle_key(position.client_order_id), _json_ready(asdict(lifecycle)))
        self.store.write_snapshot(CHECKPOINT_KEY, {"algorithmId": WEIGHTED_VOTING_ALGORITHM_ID, "lastProtectedClientOrderId": position.client_order_id, "updatedAt": protected_at.isoformat(), "reasonCodes": ("weighted_voting.position_manager.protection_checkpoint",)})
        return instruction

    def restore_protective_management(
        self,
        *,
        effective_settings_by_version: dict[str, WeightedEffectiveSettings],
        restored_at: datetime,
    ) -> tuple[WeightedVotingProtectiveInstruction, ...]:
        restored: list[WeightedVotingProtectiveInstruction] = []
        for position in self.inventory_repository.current_snapshot(now=restored_at).open_positions:
            if _read_optional(self.store, _protection_key(position.client_order_id)):
                continue
            settings = next(iter(effective_settings_by_version.values())) if effective_settings_by_version else None
            if settings is None:
                continue
            restored.append(self.protect_position_on_entry_fill(position=position, effective_settings=settings, entry_order_id=position.client_order_id, protected_at=restored_at))
        return tuple(restored)

    def monitor_position(
        self,
        *,
        position: WeightedVotingPosition,
        current_price: float,
        observed_at: datetime,
        market_quality: WeightedMarketQuality | str = WeightedMarketQuality.CLEAN,
        end_of_day: bool = False,
        global_emergency_exit: bool = False,
        signal_decay_exit: bool = False,
        opposing_weight_exit: bool = False,
        spread_liquidity_emergency: bool = False,
        realised_exit_costs: float = 0.0,
    ) -> WeightedVotingAuthoritativeTradeRecord | None:
        _require_weighted_voting(position.algorithm_id)
        lifecycle_payload = _read_optional(self.store, _lifecycle_key(position.client_order_id))
        protection_payload = _read_optional(self.store, _protection_key(position.client_order_id))
        if not lifecycle_payload or not protection_payload:
            return None
        lifecycle = _lifecycle_from_payload(lifecycle_payload)
        exit_inputs = WeightedVotingExitInputs(
            lifecycle=lifecycle,
            current_price=current_price,
            current_timestamp=observed_at,
            current_condition_quality=market_quality.value if isinstance(market_quality, WeightedMarketQuality) else market_quality,
            global_emergency_exit=global_emergency_exit,
            end_of_session=end_of_day,
            signal_decay_exit=signal_decay_exit,
            opposing_weight_exit=opposing_weight_exit,
            local_risk_exit=spread_liquidity_emergency,
            emergency_exit_reason="spread_liquidity" if spread_liquidity_emergency else None,
        )
        decision = evaluate_exit_lifecycle(exit_inputs)
        self.store.write_snapshot(_lifecycle_key(position.client_order_id), _json_ready(asdict(decision.updated_lifecycle)))
        if decision.action != "exit":
            return None
        instruction = WeightedVotingExitInstruction(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            instruction_id=f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.exit.{position.client_order_id}.{decision.exit_reason}",
            client_order_id=position.client_order_id,
            position_id=position.position_id,
            trade_id=lifecycle.trade_id,
            symbol=position.symbol,
            side="SELL" if position.quantity > 0 else "BUY",
            quantity=abs(position.quantity),
            exit_reason=str(decision.exit_reason),
            exit_price=current_price,
            settings_version=str(_payload_get(protection_payload, "settings_version", "settingsVersion")),
            created_at=observed_at,
            emergency=decision.emergency_exit,
        )
        broker_exit_id = self.broker.submit_exit_order(instruction) if self.broker is not None else None
        trade = self._close_trade(position=position, decision=decision, exit_price=current_price, exit_time=observed_at, protection_payload=protection_payload, exit_order_id=broker_exit_id, realised_exit_costs=realised_exit_costs)
        self.store.write_snapshot(_exit_instruction_key(position.client_order_id), {**instruction.as_dict(), "brokerExitOrderId": broker_exit_id})
        self.store.write_snapshot(_trade_key(trade.trade_id), trade.as_dict())
        self.store.write_snapshot(CHECKPOINT_KEY, {"algorithmId": WEIGHTED_VOTING_ALGORITHM_ID, "lastClosedTradeId": trade.trade_id, "updatedAt": observed_at.isoformat(), "reasonCodes": ("weighted_voting.position_manager.trade_checkpoint",)})
        return trade

    def _close_trade(
        self,
        *,
        position: WeightedVotingPosition,
        decision: WeightedVotingExitDecision,
        exit_price: float,
        exit_time: datetime,
        protection_payload: dict[str, Any],
        exit_order_id: str | None,
        realised_exit_costs: float,
    ) -> WeightedVotingAuthoritativeTradeRecord:
        snapshot = self.inventory_repository.current_snapshot(now=exit_time)
        self.inventory_repository.append_event(
            event_id=f"{position.client_order_id}.close.{decision.exit_reason}",
            event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
            payload={"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "position_id": position.position_id, "exit_price": exit_price, "exit_reason": decision.exit_reason},
            occurred_at=exit_time,
            expected_snapshot_version=snapshot.snapshot_version,
        )
        gross = (exit_price - position.average_entry_price) * position.quantity
        estimated_costs = abs(position.quantity) * 0.02
        net = gross - estimated_costs - realised_exit_costs
        holding_seconds = max(0.0, (exit_time - position.opened_at).total_seconds())
        mae = min(0.0, (position.mark_price or exit_price) - position.average_entry_price) * abs(position.quantity)
        mfe = max(0.0, (position.mark_price or exit_price) - position.average_entry_price) * abs(position.quantity)
        return WeightedVotingAuthoritativeTradeRecord(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            trade_id=str(_payload_get(protection_payload, "trade_id", "tradeId")),
            position_id=position.position_id,
            client_order_id=position.client_order_id,
            symbol=position.symbol,
            side=position.side,
            quantity=abs(position.quantity),
            entry_price=position.average_entry_price,
            exit_price=exit_price,
            entry_time=position.opened_at,
            exit_time=exit_time,
            exit_reason=str(decision.exit_reason),
            gross_pnl=round(gross, 10),
            estimated_costs=round(estimated_costs, 10),
            realised_costs=round(realised_exit_costs, 10),
            net_pnl=round(net, 10),
            mae=round(mae, 10),
            mfe=round(mfe, 10),
            holding_seconds=holding_seconds,
            supporting_strategy_ids=tuple(_payload_get(protection_payload, "supporting_strategy_ids", "supportingStrategyIds") or ()),
            settings_version=str(_payload_get(protection_payload, "settings_version", "settingsVersion")),
            settings_hash=str(_payload_get(protection_payload, "settings_hash", "settingsHash")),
            entry_order_id=position.client_order_id,
            stop_order_id=_payload_get(protection_payload, "broker_stop_order_id", "brokerStopOrderId"),
            target_order_id=_payload_get(protection_payload, "broker_target_order_id", "brokerTargetOrderId"),
            exit_order_id=exit_order_id,
            reason_codes=tuple(dict.fromkeys(("weighted_voting.position_manager.trade_closed", *decision.reason_codes))),
        )


def assert_weighted_voting_position_manager_ownership(position: WeightedVotingPosition | dict[str, Any]) -> None:
    algorithm_id = position.algorithm_id if isinstance(position, WeightedVotingPosition) else position.get("algorithmId") or position.get("algorithm_id")
    _require_weighted_voting(str(algorithm_id))


def position_manager_status() -> dict[str, Any]:
    return {
        "managerVersion": WEIGHTED_VOTING_POSITION_MANAGER_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE,
        "dashboardRequired": False,
        "exitManagementContinuesWhenEntriesPaused": True,
        "ownedExitReasons": ("structural_stop", "atr_fallback_stop", "hard_max_loss", "profit_target", "break_even", "trailing_stop", "signal_deterioration", "opposite_high_confidence", "spread_liquidity_emergency", "strategy_time_stop", "end_of_day", "global_emergency", "partial_fill_protection", "orphaned_protective_recovery"),
        "reasonCodes": ("weighted_voting.position_manager.status.ready",),
    }


def _fallback_stop(position: WeightedVotingPosition, settings: WeightedEffectiveSettings) -> float:
    distance = max(position.average_entry_price * settings.minimum_stop_distance_percent, position.average_entry_price * 0.005)
    return position.average_entry_price - distance if position.quantity > 0 else position.average_entry_price + distance


def _trade_id(client_order_id: str) -> str:
    return f"weighted_voting.trade.{client_order_id}"


def _protection_key(client_order_id: str) -> str:
    return f"{PROTECTION_PREFIX}{client_order_id}"


def _linkage_key(client_order_id: str) -> str:
    return f"{LINKAGE_PREFIX}{client_order_id}"


def _lifecycle_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.lifecycle.{client_order_id}"


def _exit_instruction_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE}.exit_instruction.{client_order_id}"


def _trade_key(trade_id: str) -> str:
    return f"{TRADE_PREFIX}{trade_id}"


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict[str, Any] | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _payload_get(payload: dict[str, Any], snake_key: str, camel_key: str) -> Any:
    return payload.get(snake_key) if snake_key in payload else payload.get(camel_key)


def _lifecycle_from_payload(payload: dict[str, Any]) -> WeightedVotingExitLifecycleState:
    values = dict(payload)
    values["entry_timestamp"] = _datetime_value(values["entry_timestamp"])
    if isinstance(values.get("original_effective_settings"), dict):
        values["original_effective_settings"] = WeightedEffectiveSettings.model_validate(values["original_effective_settings"])
    return WeightedVotingExitLifecycleState(**values)


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _require_weighted_voting(algorithm_id: str) -> None:
    if algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting position manager rejects foreign position mutation")


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


__all__ = [
    "CHECKPOINT_KEY",
    "TRADE_PREFIX",
    "WEIGHTED_VOTING_POSITION_MANAGER_NAMESPACE",
    "WEIGHTED_VOTING_POSITION_MANAGER_VERSION",
    "WeightedVotingAuthoritativeTradeRecord",
    "WeightedVotingExitInstruction",
    "WeightedVotingManagedOrderType",
    "WeightedVotingPositionManagerService",
    "WeightedVotingProtectiveInstruction",
    "assert_weighted_voting_position_manager_ownership",
    "position_manager_status",
]
