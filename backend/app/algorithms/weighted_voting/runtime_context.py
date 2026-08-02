"""Typed runtime context for one Weighted Voting decision."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol

from backend.app.algorithms.weighted_voting.decision_gates import WeightedFiveMinuteAlignment
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import (
    CURRENT_SNAPSHOT_KEY,
    WeightedVotingInventoryRepository,
    WeightedVotingInventorySnapshot,
    WeightedVotingPendingOrder,
    WeightedVotingPosition,
)
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedDataQualityStatus,
    WeightedEffectiveSettings,
    WeightedEventRiskLevel,
    WeightedMarketCondition,
    WeightedMarketSnapshot,
    WeightedPositionState,
    WeightedSessionPhase,
    WeightedWeightState,
)
from backend.app.gates import GlobalGateResponse


WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION = "weighted_voting_runtime_context_v1"
WEIGHTED_VOTING_RUNTIME_CONTEXT_SOURCE = "backend.app.algorithms.weighted_voting.runtime_context"

FORBIDDEN_AUTHORITATIVE_EVALUATION_INPUTS = frozenset(
    {
        "current_position",
        "currentPosition",
        "position",
        "open_position",
        "openPosition",
        "daily_pnl",
        "dailyPnl",
        "daily_loss",
        "dailyLoss",
        "daily_trade_count",
        "dailyTradeCount",
        "account_equity",
        "accountEquity",
        "buying_power",
        "buyingPower",
        "available_buying_power",
        "availableBuyingPower",
        "algorithm_capital_available",
        "algorithmCapitalAvailable",
        "remaining_algorithm_risk",
        "remainingAlgorithmRisk",
        "remaining_daily_risk",
        "remainingDailyRisk",
        "remaining_weighted_daily_risk",
        "remainingWeightedDailyRisk",
        "remaining_weighted_capital_partition",
        "remainingWeightedCapitalPartition",
        "capital_partition",
        "capitalPartition",
        "capital_available",
        "capitalAvailable",
        "global_available_risk",
        "globalAvailableRisk",
        "global_max_shares",
        "globalMaxShares",
        "session_allowed",
        "sessionAllowed",
        "exchange_session_open",
        "exchangeSessionOpen",
        "trading_session_allowed",
        "tradingSessionAllowed",
        "five_minute_alignment",
        "fiveMinuteAlignment",
        "global_gate_response",
        "globalGateResponse",
        "global_risk_approval",
        "globalRiskApproval",
        "global_gate_application",
        "globalGateApplication",
        "paper_trading_enabled",
        "paperTradingEnabled",
        "automatic_entries_enabled",
        "automaticEntriesEnabled",
        "paper_toggle_state",
        "paperToggleState",
    }
)

RUNTIME_CONTEXT_FIELD_NAMES = (
    "finalised_one_minute_market_snapshot",
    "five_minute_candles",
    "five_minute_alignment",
    "exchange_session_state",
    "data_quality_state",
    "current_market_condition",
    "effective_settings",
    "active_weight_state",
    "inventory_snapshot",
    "inventory_available",
    "current_position",
    "pending_orders",
    "algorithm_daily_pnl",
    "algorithm_daily_trade_count",
    "remaining_algorithm_daily_risk",
    "remaining_algorithm_capital_partition",
    "read_only_account_equity",
    "read_only_broker_buying_power",
    "current_spread",
    "quote_timestamp",
    "estimated_slippage",
    "estimated_fees",
    "event_risk_state",
    "global_risk_state",
    "global_risk_service_availability",
    "context_version",
    "manifest_hash",
)


class WeightedVotingMarketDataPort(Protocol):
    def market_snapshot(self) -> WeightedMarketSnapshot:
        """Return finalised read-only market facts for the decision."""


class WeightedVotingAccountObservationPort(Protocol):
    def account_observation(self, *, as_of: datetime) -> "WeightedVotingReadOnlyAccountObservation":
        """Return a read-only broker/account observation."""


class WeightedVotingGlobalRiskPort(Protocol):
    def global_risk_state(self, *, as_of: datetime) -> "WeightedVotingGlobalRiskState":
        """Return central global-risk availability and any external response."""


class WeightedVotingInventorySnapshotPort(Protocol):
    def current_snapshot(self, *, now: datetime | None = None, session_date: date | None = None) -> WeightedVotingInventorySnapshot:
        """Return the immutable Weighted Voting inventory snapshot for the decision."""


@dataclass(frozen=True)
class WeightedVotingRuntimeFieldSource:
    field_name: str
    source_id: str
    source_kind: str
    observed_at: datetime
    data_timestamp: datetime
    authoritative: bool
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.field_name not in RUNTIME_CONTEXT_FIELD_NAMES:
            raise ValueError(f"unknown Weighted Voting runtime context field {self.field_name}")


@dataclass(frozen=True)
class WeightedVotingExchangeSessionState:
    session_date: date
    session_phase: WeightedSessionPhase | str
    session_allowed: bool | None
    is_exchange_open: bool | None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedVotingDataQualityState:
    status: WeightedDataQualityStatus | str
    completed_one_minute_candle: bool
    freshness_seconds: float | None
    malformed: bool = False
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedVotingReadOnlyAccountObservation:
    account_equity: float | None
    broker_buying_power: float | None
    observed_at: datetime
    source_id: str
    available: bool
    stale_after_seconds: int | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedVotingExecutionCostEstimate:
    slippage_per_share: float | None
    fee_per_share: float | None
    observed_at: datetime
    source_id: str
    available: bool = True
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedVotingEventRiskState:
    risk_level: WeightedEventRiskLevel | str
    trading_blocked: bool | None
    observed_at: datetime
    source_id: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedVotingGlobalRiskState:
    service_available: bool
    global_available_risk: float | None
    global_max_shares: int | None
    gate_response: GlobalGateResponse | None
    observed_at: datetime
    source_id: str
    reason_codes: tuple[str, ...] = ()


PaperAccountSnapshot = WeightedVotingReadOnlyAccountObservation
ExchangeSessionState = WeightedVotingExchangeSessionState
ExecutionCostEstimate = WeightedVotingExecutionCostEstimate
GlobalRiskCapacity = WeightedVotingGlobalRiskState


@dataclass(frozen=True)
class WeightedVotingRuntimeContext:
    finalised_one_minute_market_snapshot: WeightedMarketSnapshot
    five_minute_candles: tuple[WeightedCandle, ...]
    five_minute_alignment: WeightedFiveMinuteAlignment | str
    exchange_session_state: WeightedVotingExchangeSessionState
    data_quality_state: WeightedVotingDataQualityState
    current_market_condition: WeightedMarketCondition
    effective_settings: WeightedEffectiveSettings
    active_weight_state: WeightedWeightState
    inventory_snapshot: WeightedVotingInventorySnapshot
    inventory_available: bool
    current_position: WeightedPositionState | None
    pending_orders: tuple[WeightedVotingPendingOrder, ...]
    algorithm_daily_pnl: float
    algorithm_daily_trade_count: int
    remaining_algorithm_daily_risk: float | None
    remaining_algorithm_capital_partition: float | None
    read_only_account_equity: float | None
    read_only_broker_buying_power: float | None
    current_spread: float | None
    quote_timestamp: datetime | None
    estimated_slippage: float | None
    estimated_fees: float | None
    event_risk_state: WeightedVotingEventRiskState
    global_risk_state: WeightedVotingGlobalRiskState
    context_version: str = WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION
    manifest_hash: str = ""
    source_attribution: dict[str, WeightedVotingRuntimeFieldSource] = field(default_factory=dict)
    mode: Literal["production", "research_shadow", "replay_fixture", "test_fixture"] = "production"
    previous_market_condition: WeightedMarketCondition | None = None

    def __post_init__(self) -> None:
        if self.finalised_one_minute_market_snapshot.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting runtime context requires a weighted_voting market snapshot")
        if self.inventory_snapshot.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting runtime context requires weighted_voting inventory")
        if self.current_position is not None and self.current_position.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting runtime context rejects cross-algorithm positions")
        for order in self.pending_orders:
            if order.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
                raise ValueError("Weighted Voting runtime context rejects cross-algorithm pending orders")
        missing_sources = set(RUNTIME_CONTEXT_FIELD_NAMES) - set(self.source_attribution)
        if missing_sources:
            raise ValueError(f"Weighted Voting runtime context missing source attribution: {sorted(missing_sources)}")
        expected_hash = _manifest_hash(self)
        if self.manifest_hash and self.manifest_hash != expected_hash:
            raise ValueError("Weighted Voting runtime context manifest hash does not match contents")
        object.__setattr__(self, "manifest_hash", expected_hash)

    @property
    def global_risk_service_availability(self) -> bool:
        return self.global_risk_state.service_available

    @property
    def market_snapshot(self) -> WeightedMarketSnapshot:
        return self.finalised_one_minute_market_snapshot

    @property
    def paper_account_snapshot(self) -> PaperAccountSnapshot:
        account_source = self.source_attribution.get("read_only_account_equity")
        observed_at = account_source.observed_at if account_source is not None else self.finalised_one_minute_market_snapshot.data_timestamp
        source_id = account_source.source_id if account_source is not None else "weighted_voting.runtime_context.account_source_unknown"
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=self.read_only_account_equity,
            broker_buying_power=self.read_only_broker_buying_power,
            observed_at=observed_at,
            source_id=source_id,
            available=self.read_only_account_equity is not None and self.read_only_broker_buying_power is not None,
            reason_codes=("weighted_voting.runtime_context.paper_account_snapshot_alias",),
        )

    @property
    def session_state(self) -> ExchangeSessionState:
        return self.exchange_session_state

    @property
    def cost_estimate(self) -> ExecutionCostEstimate:
        cost_source = self.source_attribution.get("estimated_slippage")
        observed_at = cost_source.observed_at if cost_source is not None else self.finalised_one_minute_market_snapshot.data_timestamp
        source_id = cost_source.source_id if cost_source is not None else "weighted_voting.runtime_context.cost_source_unknown"
        return WeightedVotingExecutionCostEstimate(
            slippage_per_share=self.estimated_slippage,
            fee_per_share=self.estimated_fees,
            observed_at=observed_at,
            source_id=source_id,
            available=self.estimated_slippage is not None and self.estimated_fees is not None,
            reason_codes=("weighted_voting.runtime_context.cost_estimate_alias",),
        )

    @property
    def settings(self) -> WeightedEffectiveSettings:
        return self.effective_settings

    @property
    def weight_state(self) -> WeightedWeightState:
        return self.active_weight_state

    @property
    def global_risk_capacity(self) -> GlobalRiskCapacity:
        return self.global_risk_state

    def context_failure_reason_codes(self, *, stale_after_seconds: int) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.finalised_one_minute_market_snapshot.one_minute_candles:
            reasons.append("weighted_voting.runtime_context.missing_one_minute_snapshot")
        if self.data_quality_state.malformed:
            reasons.append("weighted_voting.runtime_context.malformed_market_data")
        if not self.data_quality_state.completed_one_minute_candle:
            reasons.extend(("weighted_voting.context.one_minute_unavailable", "weighted_voting.runtime_context.incomplete_one_minute_candle"))
        freshness = self.data_quality_state.freshness_seconds
        if freshness is None:
            reasons.extend(("weighted_voting.context.quote_stale", "weighted_voting.runtime_context.missing_data_freshness"))
        elif freshness > stale_after_seconds:
            reasons.extend(("weighted_voting.context.quote_stale", "weighted_voting.runtime_context.stale_market_data"))
        if _enum_value(self.five_minute_alignment) == WeightedFiveMinuteAlignment.UNAVAILABLE.value:
            reasons.extend(("weighted_voting.context.five_minute_unavailable", "weighted_voting.runtime_context.five_minute_unavailable"))
        if self.exchange_session_state.session_allowed is not True:
            reasons.extend(("weighted_voting.context.session_unavailable", "weighted_voting.runtime_context.session_permission_unavailable"))
        if not self.inventory_available:
            reasons.extend(("weighted_voting.context.inventory_unavailable", "weighted_voting.runtime_context.inventory_unavailable"))
        if self.read_only_account_equity is None:
            reasons.append("weighted_voting.runtime_context.missing_read_only_account_equity")
        if self.read_only_broker_buying_power is None:
            reasons.append("weighted_voting.runtime_context.missing_read_only_broker_buying_power")
        account_source = self.source_attribution.get("read_only_account_equity")
        if account_source is not None and (self.finalised_one_minute_market_snapshot.data_timestamp - account_source.observed_at).total_seconds() > stale_after_seconds:
            reasons.extend(("weighted_voting.context.account_snapshot_stale", "weighted_voting.runtime_context.account_snapshot_stale"))
        if self.remaining_algorithm_daily_risk is None:
            reasons.append("weighted_voting.runtime_context.missing_algorithm_daily_risk")
        if self.remaining_algorithm_capital_partition is None:
            reasons.append("weighted_voting.runtime_context.missing_algorithm_capital_partition")
        if self.global_risk_state.service_available is not True:
            reasons.append("weighted_voting.runtime_context.global_risk_service_unavailable")
        if self.global_risk_state.global_available_risk is None or self.global_risk_state.global_max_shares is None:
            reasons.append("weighted_voting.runtime_context.global_risk_capacity_unavailable")
        if self.event_risk_state.trading_blocked is not False:
            reasons.append("weighted_voting.runtime_context.event_risk_unavailable_or_blocked")
        if self.inventory_snapshot.symbol.upper() != self.finalised_one_minute_market_snapshot.symbol.upper():
            reasons.append("weighted_voting.runtime_context.conflicting_inventory_symbol")
        if self.current_spread is None or self.quote_timestamp is None:
            reasons.extend(("weighted_voting.context.quote_stale", "weighted_voting.runtime_context.missing_quote_state"))
        elif (self.finalised_one_minute_market_snapshot.data_timestamp - self.quote_timestamp).total_seconds() > stale_after_seconds:
            reasons.extend(("weighted_voting.context.quote_stale", "weighted_voting.runtime_context.quote_stale"))
        if self.estimated_slippage is None or self.estimated_fees is None:
            reasons.extend(("weighted_voting.context.cost_model_unavailable", "weighted_voting.runtime_context.missing_execution_cost_estimate"))
        return tuple(dict.fromkeys(reasons))

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingStaticMarketDataPort:
    snapshot: WeightedMarketSnapshot

    def market_snapshot(self) -> WeightedMarketSnapshot:
        return self.snapshot


@dataclass(frozen=True)
class WeightedVotingStaticInventorySnapshotPort:
    snapshot: WeightedVotingInventorySnapshot

    def current_snapshot(self, *, now: datetime | None = None, session_date: date | None = None) -> WeightedVotingInventorySnapshot:
        return self.snapshot


@dataclass(frozen=True)
class WeightedVotingUnavailableAccountPort:
    source_id: str = "read_only_broker_account.unavailable"

    def account_observation(self, *, as_of: datetime) -> WeightedVotingReadOnlyAccountObservation:
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=None,
            broker_buying_power=None,
            observed_at=as_of,
            source_id=self.source_id,
            available=False,
            reason_codes=("weighted_voting.runtime_context.account_port_unavailable",),
        )


@dataclass(frozen=True)
class WeightedVotingStaticAccountPort:
    account_equity: float
    broker_buying_power: float
    source_id: str = "weighted_voting.replay_fixture.account_observation"
    observed_at: datetime | None = None

    def account_observation(self, *, as_of: datetime) -> WeightedVotingReadOnlyAccountObservation:
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=self.account_equity,
            broker_buying_power=self.broker_buying_power,
            observed_at=self.observed_at or as_of,
            source_id=self.source_id,
            available=True,
            reason_codes=("weighted_voting.runtime_context.account_observed",),
        )


@dataclass(frozen=True)
class WeightedVotingUnavailableGlobalRiskPort:
    source_id: str = "central_risk_service.unavailable"

    def global_risk_state(self, *, as_of: datetime) -> WeightedVotingGlobalRiskState:
        return WeightedVotingGlobalRiskState(
            service_available=False,
            global_available_risk=None,
            global_max_shares=None,
            gate_response=None,
            observed_at=as_of,
            source_id=self.source_id,
            reason_codes=("weighted_voting.runtime_context.global_risk_port_unavailable",),
        )


@dataclass(frozen=True)
class WeightedVotingStaticGlobalRiskPort:
    global_available_risk: float
    global_max_shares: int
    gate_response: GlobalGateResponse | None
    source_id: str = "weighted_voting.replay_fixture.central_risk_response"

    def global_risk_state(self, *, as_of: datetime) -> WeightedVotingGlobalRiskState:
        return WeightedVotingGlobalRiskState(
            service_available=True,
            global_available_risk=self.global_available_risk,
            global_max_shares=self.global_max_shares,
            gate_response=self.gate_response,
            observed_at=as_of,
            source_id=self.source_id,
            reason_codes=("weighted_voting.runtime_context.global_risk_observed",),
        )


@dataclass(frozen=True)
class WeightedVotingRuntimeContextBuilder:
    market_data_port: WeightedVotingMarketDataPort
    inventory_repository: WeightedVotingInventorySnapshotPort
    account_port: WeightedVotingAccountObservationPort
    global_risk_port: WeightedVotingGlobalRiskPort
    effective_settings: WeightedEffectiveSettings
    active_weight_state: WeightedWeightState
    observed_at: datetime
    mode: Literal["production", "research_shadow", "replay_fixture", "test_fixture"] = "production"
    cost_estimate: WeightedVotingExecutionCostEstimate | None = None
    event_risk_state: WeightedVotingEventRiskState | None = None
    exchange_session_state: WeightedVotingExchangeSessionState | None = None
    previous_market_condition: WeightedMarketCondition | None = None
    market_condition: WeightedMarketCondition | None = None

    def build(self) -> WeightedVotingRuntimeContext:
        snapshot = self.market_data_port.market_snapshot()
        inventory = self.inventory_repository.current_snapshot(now=self.observed_at, session_date=_session_date(snapshot))
        account = self.account_port.account_observation(as_of=self.observed_at)
        global_risk = self.global_risk_port.global_risk_state(as_of=self.observed_at)
        condition = self.market_condition or classify_market_condition(snapshot, previous_condition=self.previous_market_condition)
        data_quality = _data_quality_state(snapshot)
        completed_five_minute = _completed_explicit_five_minute_candles(snapshot)
        session_state = self.exchange_session_state or WeightedVotingExchangeSessionState(
            session_date=_session_date(snapshot),
            session_phase=snapshot.session_phase,
            session_allowed=_session_allowed(snapshot),
            is_exchange_open=_session_allowed(snapshot),
            reason_codes=("weighted_voting.runtime_context.session_state_from_market_snapshot",),
        )
        costs = self.cost_estimate or WeightedVotingExecutionCostEstimate(
            slippage_per_share=None,
            fee_per_share=None,
            observed_at=self.observed_at,
            source_id="weighted_voting.cost_model.unavailable",
            available=False,
            reason_codes=("weighted_voting.context.cost_model_unavailable",),
        )
        event_risk = self.event_risk_state or WeightedVotingEventRiskState(
            risk_level=condition.event_risk,
            trading_blocked=_enum_value(condition.event_risk) == WeightedEventRiskLevel.BLOCKED.value,
            observed_at=condition.data_timestamp,
            source_id="weighted_voting.market_condition.event_risk",
            reason_codes=("weighted_voting.runtime_context.event_risk_from_market_condition",),
        )
        context_values: dict[str, Any] = {
            "finalised_one_minute_market_snapshot": snapshot,
            "five_minute_candles": completed_five_minute,
            "five_minute_alignment": _five_minute_alignment(snapshot),
            "exchange_session_state": session_state,
            "data_quality_state": data_quality,
            "current_market_condition": condition,
            "effective_settings": self.effective_settings,
            "active_weight_state": self.active_weight_state,
            "inventory_snapshot": inventory,
            "inventory_available": _inventory_available(self.inventory_repository),
            "current_position": _current_position(inventory, snapshot.symbol, snapshot.data_timestamp),
            "pending_orders": tuple(order for order in inventory.pending_orders if order.symbol.upper() == snapshot.symbol.upper()),
            "algorithm_daily_pnl": inventory.daily_realised_pnl + inventory.daily_unrealised_pnl,
            "algorithm_daily_trade_count": inventory.daily_trade_count,
            "remaining_algorithm_daily_risk": inventory.remaining_daily_risk,
            "remaining_algorithm_capital_partition": inventory.remaining_capital_partition,
            "read_only_account_equity": account.account_equity,
            "read_only_broker_buying_power": account.broker_buying_power,
            "current_spread": snapshot.spread,
            "quote_timestamp": snapshot.data_timestamp,
            "estimated_slippage": costs.slippage_per_share,
            "estimated_fees": costs.fee_per_share,
            "event_risk_state": event_risk,
            "global_risk_state": global_risk,
        }
        sources = _source_attribution(context_values, account=account, global_risk=global_risk, costs=costs, event_risk=event_risk, observed_at=self.observed_at)
        context_values.update(
            {
                "context_version": WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION,
                "manifest_hash": "",
                "source_attribution": sources,
                "mode": self.mode,
                "previous_market_condition": self.previous_market_condition,
            }
        )
        return WeightedVotingRuntimeContext(**context_values)


def payload_contains_forbidden_authoritative_evaluation_inputs(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in FORBIDDEN_AUTHORITATIVE_EVALUATION_INPUTS)


def runtime_context_status() -> dict[str, Any]:
    return {
        "version": WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "authoritativeInput": "WeightedVotingRuntimeContext",
        "backendPythonAuthoritative": True,
        "publicHttpMayManufactureInventoryOrSafetyState": False,
        "automaticPaperRuntimeAllowsFixtureOverrides": False,
        "fixtureOverrideModes": ("replay_fixture", "test_fixture"),
        "fieldNames": RUNTIME_CONTEXT_FIELD_NAMES,
        "forbiddenAuthoritativeEvaluationInputs": tuple(sorted(FORBIDDEN_AUTHORITATIVE_EVALUATION_INPUTS)),
        "sourceAttributionRequired": True,
        "missingSafetyInputsFailClosed": True,
        "reasonCodes": ("weighted_voting.runtime_context.contract.ready",),
    }


def _source_attribution(
    values: dict[str, Any],
    *,
    account: WeightedVotingReadOnlyAccountObservation,
    global_risk: WeightedVotingGlobalRiskState,
    costs: WeightedVotingExecutionCostEstimate,
    event_risk: WeightedVotingEventRiskState,
    observed_at: datetime,
) -> dict[str, WeightedVotingRuntimeFieldSource]:
    source_by_field = {
        "read_only_account_equity": (account.source_id, "read_only_account_port", account.observed_at),
        "read_only_broker_buying_power": (account.source_id, "read_only_account_port", account.observed_at),
        "global_risk_state": (global_risk.source_id, "central_risk_port", global_risk.observed_at),
        "global_risk_service_availability": (global_risk.source_id, "central_risk_port", global_risk.observed_at),
        "estimated_slippage": (costs.source_id, "execution_cost_port", costs.observed_at),
        "estimated_fees": (costs.source_id, "execution_cost_port", costs.observed_at),
        "event_risk_state": (event_risk.source_id, "event_risk_port", event_risk.observed_at),
    }
    sources: dict[str, WeightedVotingRuntimeFieldSource] = {}
    data_timestamp = _data_timestamp(values)
    for field_name in RUNTIME_CONTEXT_FIELD_NAMES:
        source_id, source_kind, timestamp = source_by_field.get(field_name, (f"{WEIGHTED_VOTING_RUNTIME_CONTEXT_SOURCE}.{field_name}", "weighted_voting_port_or_repository", observed_at))
        sources[field_name] = WeightedVotingRuntimeFieldSource(
            field_name=field_name,
            source_id=source_id,
            source_kind=source_kind,
            observed_at=timestamp,
            data_timestamp=data_timestamp,
            authoritative=not source_id.startswith("weighted_voting.replay_fixture"),
            reason_codes=(f"weighted_voting.runtime_context.source.{field_name}",),
        )
    return sources


def _data_quality_state(snapshot: WeightedMarketSnapshot) -> WeightedVotingDataQualityState:
    completed = bool(snapshot.one_minute_candles) and snapshot.one_minute_candles[-1].timestamp <= snapshot.data_timestamp
    status = WeightedDataQualityStatus.FULL if completed and snapshot.data_freshness_seconds is not None else WeightedDataQualityStatus.UNAVAILABLE
    reasons = ["weighted_voting.runtime_context.finalised_one_minute_candle" if completed else "weighted_voting.runtime_context.incomplete_one_minute_candle"]
    return WeightedVotingDataQualityState(
        status=status,
        completed_one_minute_candle=completed,
        freshness_seconds=snapshot.data_freshness_seconds,
        malformed=False,
        reason_codes=tuple(reasons),
    )


def _session_allowed(snapshot: WeightedMarketSnapshot) -> bool | None:
    phase = _enum_value(snapshot.session_phase)
    if phase == WeightedSessionPhase.UNKNOWN.value:
        return None
    return phase != WeightedSessionPhase.OUTSIDE_SESSION.value


def _session_date(snapshot: WeightedMarketSnapshot) -> date:
    if snapshot.session_date:
        return date.fromisoformat(snapshot.session_date)
    return snapshot.data_timestamp.date()


def _completed_explicit_five_minute_candles(snapshot: WeightedMarketSnapshot) -> tuple[WeightedCandle, ...]:
    return tuple(candle for candle in snapshot.five_minute_candles if candle.timestamp <= snapshot.data_timestamp)


def _five_minute_alignment(snapshot: WeightedMarketSnapshot) -> WeightedFiveMinuteAlignment:
    if snapshot.five_minute_candles:
        candles = _completed_explicit_five_minute_candles(snapshot)
        if not candles:
            return WeightedFiveMinuteAlignment.UNAVAILABLE
        first = candles[-1].open
        last = candles[-1].close
    else:
        candles = tuple(candle for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp)[-5:]
        if len(candles) < 5:
            return WeightedFiveMinuteAlignment.UNAVAILABLE
        first = candles[0].open
        last = candles[-1].close
    if not candles:
        return WeightedFiveMinuteAlignment.UNAVAILABLE
    if last > first:
        return WeightedFiveMinuteAlignment.POSITIVE
    if last < first:
        return WeightedFiveMinuteAlignment.NEGATIVE
    return WeightedFiveMinuteAlignment.NEUTRAL


def _inventory_available(repository: WeightedVotingInventorySnapshotPort) -> bool:
    if isinstance(repository, WeightedVotingStaticInventorySnapshotPort):
        return True
    if isinstance(repository, WeightedVotingInventoryRepository):
        try:
            repository.store.read_snapshot(CURRENT_SNAPSHOT_KEY)
            return True
        except KeyError:
            return False
    return True


def _current_position(snapshot: WeightedVotingInventorySnapshot, symbol: str, data_timestamp: datetime) -> WeightedPositionState | None:
    positions = [position for position in snapshot.open_positions if position.symbol.upper() == symbol.upper()]
    if not positions:
        return None
    total_quantity = sum(position.quantity for position in positions)
    if total_quantity == 0:
        return None
    weighted_cost = sum(position.quantity * position.average_entry_price for position in positions)
    return WeightedPositionState(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=symbol.upper(),
        quantity=total_quantity,
        average_entry_price=abs(weighted_cost / total_quantity),
        realized_pnl=sum(position.realised_pnl for position in positions),
        unrealized_pnl=sum(position.unrealised_pnl for position in positions),
        data_timestamp=data_timestamp,
        explanation="Weighted Voting current position derived only from the isolated inventory snapshot.",
    )


def _data_timestamp(values: dict[str, Any]) -> datetime:
    snapshot = values.get("finalised_one_minute_market_snapshot")
    if isinstance(snapshot, WeightedMarketSnapshot):
        return snapshot.data_timestamp
    return datetime.now(timezone.utc)


def _manifest_hash(context: WeightedVotingRuntimeContext) -> str:
    payload = {
        "context_version": context.context_version,
        "mode": context.mode,
        "snapshot_hash": context.finalised_one_minute_market_snapshot.data_manifest_hash,
        "settings_version": context.effective_settings.settings_version,
        "weight_version": context.active_weight_state.weight_version,
        "inventory_snapshot_version": context.inventory_snapshot.snapshot_version,
        "inventory_sequence": context.inventory_snapshot.last_event_sequence,
        "field_sources": {key: _json_ready(value) for key, value in sorted(context.source_attribution.items())},
        "global_risk_available": context.global_risk_state.service_available,
        "account_observed": context.read_only_account_equity is not None and context.read_only_broker_buying_power is not None,
    }
    return hashlib.sha256(json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


__all__ = [
    "FORBIDDEN_AUTHORITATIVE_EVALUATION_INPUTS",
    "RUNTIME_CONTEXT_FIELD_NAMES",
    "WEIGHTED_VOTING_RUNTIME_CONTEXT_SOURCE",
    "WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION",
    "WeightedVotingAccountObservationPort",
    "WeightedVotingDataQualityState",
    "WeightedVotingEventRiskState",
    "WeightedVotingExchangeSessionState",
    "WeightedVotingExecutionCostEstimate",
    "WeightedVotingGlobalRiskPort",
    "WeightedVotingGlobalRiskState",
    "WeightedVotingMarketDataPort",
    "WeightedVotingReadOnlyAccountObservation",
    "WeightedVotingRuntimeContext",
    "WeightedVotingRuntimeContextBuilder",
    "WeightedVotingRuntimeFieldSource",
    "WeightedVotingStaticAccountPort",
    "WeightedVotingStaticGlobalRiskPort",
    "WeightedVotingStaticInventorySnapshotPort",
    "WeightedVotingStaticMarketDataPort",
    "WeightedVotingUnavailableAccountPort",
    "WeightedVotingUnavailableGlobalRiskPort",
    "PaperAccountSnapshot",
    "ExchangeSessionState",
    "ExecutionCostEstimate",
    "GlobalRiskCapacity",
    "payload_contains_forbidden_authoritative_evaluation_inputs",
    "runtime_context_status",
]
