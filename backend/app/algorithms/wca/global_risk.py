"""WCA adapter for neutral shared global account-risk approval."""

from __future__ import annotations

from enum import Enum
from math import floor, isfinite
from threading import Lock
from typing import Any, Protocol

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel, WcaSide


WCA_GLOBAL_RISK_ADAPTER_VERSION = "wca_global_risk_adapter_v1"


class WcaGlobalRiskDecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_REDUCED_QUANTITY = "APPROVED_REDUCED_QUANTITY"
    APPROVED_REDUCED_RISK = "APPROVED_REDUCED_RISK"
    REJECTED_ENTRY = "REJECTED_ENTRY"
    BLOCK_NEW_ENTRIES = "BLOCK_NEW_ENTRIES"


class WcaGlobalRiskProposal(WcaContractModel):
    algorithm_id: str = Field(default=WCA_ALGORITHM_ID, min_length=1)
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide | str
    requested_quantity: int = Field(ge=0)
    requested_risk: float = Field(ge=0)
    stop_distance: float = Field(ge=0)
    expected_holding_period_seconds: int = Field(default=3600, ge=0)
    current_wca_attributed_exposure: float = Field(default=0.0, ge=0)
    total_account_exposure_snapshot: dict[str, Any] = Field(default_factory=dict)
    configuration_version: str = Field(min_length=1)
    configuration_hash: str = ""
    decision_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    risk_reducing_exit: bool = False

    @model_validator(mode="after")
    def validate_neutral_proposal(self) -> "WcaGlobalRiskProposal":
        side = _side_value(self.side)
        if side not in (WcaSide.BUY.value, WcaSide.SELL.value):
            raise ValueError("WCA global-risk proposal side must be BUY or SELL")
        if self.requested_quantity > 0 and self.requested_risk > 0 and self.stop_distance <= 0:
            raise ValueError("WCA global-risk proposal requires a positive stop distance for entry risk")
        return self


class WcaGlobalRiskDecision(WcaContractModel):
    status: WcaGlobalRiskDecisionStatus
    algorithm_id: str = Field(default=WCA_ALGORITHM_ID, min_length=1)
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide | str
    requested_quantity: int = Field(ge=0)
    approved_quantity: int = Field(ge=0)
    requested_risk: float = Field(ge=0)
    approved_risk: float = Field(ge=0)
    entry_permitted: bool
    risk_reducing_exit_permitted: bool = True
    idempotency_key: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""

    @model_validator(mode="after")
    def cannot_increase_wca_proposal(self) -> "WcaGlobalRiskDecision":
        if self.approved_quantity > self.requested_quantity:
            raise ValueError("global risk cannot increase WCA quantity")
        if self.approved_risk > self.requested_risk + 1e-9:
            raise ValueError("global risk cannot increase WCA requested risk")
        if _side_value(self.side) not in (WcaSide.BUY.value, WcaSide.SELL.value):
            raise ValueError("global risk cannot rewrite WCA side")
        return self


class WcaGlobalRiskClient(Protocol):
    def evaluate_wca_proposal(self, proposal: WcaGlobalRiskProposal) -> WcaGlobalRiskDecision:
        """Return neutral approval, reduction, or rejection for a WCA proposal."""


class SharedGlobalRiskReservationEngine:
    """Small neutral, account-scoped reservation engine used by WCA tests and adapters.

    Production deployments can replace this with the shared account-risk service as long as the
    same neutral proposal/decision contract is preserved.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._decisions_by_idempotency_key: dict[str, WcaGlobalRiskDecision] = {}
        self._reserved_risk_by_account: dict[tuple[str, str], float] = {}

    def evaluate(self, proposal: WcaGlobalRiskProposal) -> WcaGlobalRiskDecision:
        with self._lock:
            existing = self._decisions_by_idempotency_key.get(proposal.idempotency_key)
            if existing is not None:
                return existing
            decision = self._evaluate_locked(proposal)
            self._decisions_by_idempotency_key[proposal.idempotency_key] = decision
            if decision.entry_permitted and not proposal.risk_reducing_exit and decision.approved_risk > 0:
                key = (proposal.account_id, proposal.symbol.upper())
                self._reserved_risk_by_account[key] = self._reserved_risk_by_account.get(key, 0.0) + decision.approved_risk
            return decision

    def reserved_risk(self, *, account_id: str, symbol: str) -> float:
        with self._lock:
            return self._reserved_risk_by_account.get((account_id, symbol.upper()), 0.0)

    def _evaluate_locked(self, proposal: WcaGlobalRiskProposal) -> WcaGlobalRiskDecision:
        snapshot = proposal.total_account_exposure_snapshot
        if bool(snapshot.get("block_new_entries")) and not proposal.risk_reducing_exit:
            return _decision(
                proposal,
                status=WcaGlobalRiskDecisionStatus.BLOCK_NEW_ENTRIES,
                approved_quantity=0,
                approved_risk=0.0,
                entry_permitted=False,
                reason_codes=("wca.global_risk.block_new_entries",),
                explanation="Shared global risk blocked new entries while preserving protective exit permission.",
            )

        quantity_cap = _optional_int(snapshot.get("global_gate_quantity_cap"))
        risk_cap = _optional_float(snapshot.get("approved_risk_budget"))
        maximum_open_risk = _optional_float(snapshot.get("maximum_open_risk_dollars"))
        current_open_risk = _optional_float(snapshot.get("current_open_risk_dollars")) or 0.0
        externally_reserved_risk = _optional_float(snapshot.get("reserved_open_risk_dollars")) or 0.0
        internally_reserved_risk = self._reserved_risk_by_account.get((proposal.account_id, proposal.symbol.upper()), 0.0)

        approved_risk = proposal.requested_risk
        if risk_cap is not None:
            approved_risk = min(approved_risk, max(0.0, risk_cap))
        if maximum_open_risk is not None and not proposal.risk_reducing_exit:
            remaining_global_risk = max(0.0, maximum_open_risk - current_open_risk - externally_reserved_risk - internally_reserved_risk)
            approved_risk = min(approved_risk, remaining_global_risk)

        approved_quantity = proposal.requested_quantity
        if quantity_cap is not None:
            approved_quantity = min(approved_quantity, max(0, quantity_cap))
        if proposal.stop_distance > 0:
            approved_quantity = min(approved_quantity, floor(approved_risk / proposal.stop_distance))
        approved_quantity = max(0, approved_quantity)
        approved_risk = min(approved_risk, approved_quantity * proposal.stop_distance if proposal.stop_distance > 0 else approved_risk)

        if approved_quantity <= 0 and not proposal.risk_reducing_exit:
            return _decision(
                proposal,
                status=WcaGlobalRiskDecisionStatus.REJECTED_ENTRY,
                approved_quantity=0,
                approved_risk=0.0,
                entry_permitted=False,
                reason_codes=("wca.global_risk.rejected_entry",),
                explanation="Shared global risk rejected the WCA entry without rewriting WCA decision inputs.",
            )

        reason_codes = ["wca.global_risk.approved"]
        status = WcaGlobalRiskDecisionStatus.APPROVED
        if approved_quantity < proposal.requested_quantity:
            status = WcaGlobalRiskDecisionStatus.APPROVED_REDUCED_QUANTITY
            reason_codes.append("wca.global_risk.reduced_quantity")
        if approved_risk < proposal.requested_risk - 1e-9:
            status = WcaGlobalRiskDecisionStatus.APPROVED_REDUCED_RISK
            reason_codes.append("wca.global_risk.reduced_risk")
        return _decision(
            proposal,
            status=status,
            approved_quantity=approved_quantity,
            approved_risk=approved_risk,
            entry_permitted=True,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            explanation="Shared global risk approved WCA risk constraints without changing WCA strategy outputs.",
        )


class WcaGlobalRiskAdapter:
    def __init__(self, engine: SharedGlobalRiskReservationEngine | None = None) -> None:
        self._engine = engine or SharedGlobalRiskReservationEngine()

    def evaluate_wca_proposal(self, proposal: WcaGlobalRiskProposal) -> WcaGlobalRiskDecision:
        if proposal.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA adapter accepts only algorithm_id='wca'")
        return self._engine.evaluate(proposal)


def build_wca_global_risk_proposal(
    *,
    account_id: str,
    symbol: str,
    side: WcaSide | str,
    requested_quantity: int,
    requested_risk: float,
    stop_distance: float,
    expected_holding_period_seconds: int,
    current_wca_attributed_exposure: float,
    total_account_exposure_snapshot: dict[str, Any],
    configuration_version: str,
    configuration_hash: str,
    decision_id: str,
    idempotency_key: str,
    risk_reducing_exit: bool = False,
) -> WcaGlobalRiskProposal:
    return WcaGlobalRiskProposal(
        account_id=account_id,
        symbol=symbol,
        side=side,
        requested_quantity=max(0, int(requested_quantity)),
        requested_risk=max(0.0, float(requested_risk)),
        stop_distance=max(0.0, float(stop_distance)),
        expected_holding_period_seconds=max(0, int(expected_holding_period_seconds)),
        current_wca_attributed_exposure=max(0.0, float(current_wca_attributed_exposure)),
        total_account_exposure_snapshot=dict(total_account_exposure_snapshot),
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        risk_reducing_exit=risk_reducing_exit,
    )


def _decision(
    proposal: WcaGlobalRiskProposal,
    *,
    status: WcaGlobalRiskDecisionStatus,
    approved_quantity: int,
    approved_risk: float,
    entry_permitted: bool,
    reason_codes: tuple[str, ...],
    explanation: str,
) -> WcaGlobalRiskDecision:
    return WcaGlobalRiskDecision(
        status=status,
        algorithm_id=proposal.algorithm_id,
        account_id=proposal.account_id,
        symbol=proposal.symbol,
        side=proposal.side,
        requested_quantity=proposal.requested_quantity,
        approved_quantity=max(0, approved_quantity),
        requested_risk=proposal.requested_risk,
        approved_risk=max(0.0, approved_risk),
        entry_permitted=entry_permitted,
        risk_reducing_exit_permitted=True,
        idempotency_key=proposal.idempotency_key,
        reason_codes=(WCA_GLOBAL_RISK_ADAPTER_VERSION, *reason_codes),
        explanation=explanation,
    )


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(max(0, floor(number)))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


__all__ = [
    "SharedGlobalRiskReservationEngine",
    "WCA_GLOBAL_RISK_ADAPTER_VERSION",
    "WcaGlobalRiskAdapter",
    "WcaGlobalRiskClient",
    "WcaGlobalRiskDecision",
    "WcaGlobalRiskDecisionStatus",
    "WcaGlobalRiskProposal",
    "build_wca_global_risk_proposal",
]
