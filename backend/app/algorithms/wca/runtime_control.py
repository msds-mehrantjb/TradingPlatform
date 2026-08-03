"""Backend-authoritative WCA paper runtime control."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel


WCA_RUNTIME_CONTROL_SCHEMA_VERSION = "wca_runtime_control_v1"


@dataclass(frozen=True)
class WcaRuntimeControlEvidence:
    paper_execution_env_enabled: bool = False
    rollout_automatic_paper_permitted: bool = False
    global_gate_available: bool = False
    runtime_healthy: bool = False
    paper_account_verified: bool = False
    market_open: bool = False
    inside_entry_window: bool = False
    market_data_fresh: bool = False
    inventory_reconciled: bool = False
    wca_circuit_breaker_closed: bool = False
    global_circuit_breaker_closed: bool = False
    configuration_ready: bool = False
    weight_ready: bool = False
    calibration_ready: bool = False
    rollout_stage: str = "DISABLED"
    rollout_evidence_revision: str = ""
    rollout_evidence_hash: str = ""
    rollout_reason_codes: tuple[str, ...] = ()
    limited_automatic_paper_caps: dict[str, Any] = field(default_factory=dict)
    cancel_unfilled_entry_orders_required: bool = True
    dependency_health: dict[str, dict[str, Any]] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()


class WcaRuntimeControl(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    broker_account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    decision_id: str = Field(default="wca-runtime-control", min_length=1)
    order_intent_id: str = Field(default="wca-runtime-control", min_length=1)
    run_id: str = Field(default="wca-runtime-control", min_length=1)
    configuration_version: str = "wca-runtime-control"
    configuration_hash: str = ""
    weight_version: str = "wca-runtime-control"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    paper_trading_requested: bool = False
    automatic_entries_requested: bool = False
    pause_new_entries: bool = True
    kill_switch_open: bool = False
    effective_paper_trading_enabled: bool = False
    effective_automatic_entries_enabled: bool = False
    paper_account_verified: bool = False
    automatic_paper_permitted: bool = False
    automatic_entry_currently_permitted: bool = False
    rollout_stage: str = "DISABLED"
    rollout_evidence_revision: str = ""
    rollout_evidence_hash: str = ""
    rollout_reason_codes: tuple[str, ...] = ()
    limited_automatic_paper_caps: dict[str, Any] = Field(default_factory=dict)
    cancel_unfilled_entry_orders_required: bool = True
    control_revision: int = Field(default=1, ge=1)
    control_hash: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = "system"
    reason: str = "wca.runtime_control.default_fail_closed"
    reason_codes: tuple[str, ...] = ("wca.runtime_control.default_fail_closed",)
    dependency_health: dict[str, dict[str, Any]] = Field(default_factory=dict)
    schema_version: str = WCA_RUNTIME_CONTROL_SCHEMA_VERSION

    def with_hash(self) -> "WcaRuntimeControl":
        return self.model_copy(update={"control_hash": _control_hash(self)})

    def api_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.update(
            {
                "algorithmId": self.algorithm_id,
                "brokerAccountId": self.broker_account_id,
                "orderIntentId": self.order_intent_id,
                "runId": self.run_id,
                "configurationVersion": self.configuration_version,
                "configurationHash": self.configuration_hash,
                "weightVersion": self.weight_version,
                "paperTradingRequested": self.paper_trading_requested,
                "automaticEntriesRequested": self.automatic_entries_requested,
                "pauseNewEntries": self.pause_new_entries,
                "killSwitchOpen": self.kill_switch_open,
                "effectivePaperTradingEnabled": self.effective_paper_trading_enabled,
                "effectiveAutomaticEntriesEnabled": self.effective_automatic_entries_enabled,
                "paperAccountVerified": self.paper_account_verified,
                "automaticPaperPermitted": self.automatic_paper_permitted,
                "automaticEntryCurrentlyPermitted": self.automatic_entry_currently_permitted,
                "rolloutStage": self.rollout_stage,
                "rolloutEvidenceRevision": self.rollout_evidence_revision,
                "rolloutEvidenceHash": self.rollout_evidence_hash,
                "rolloutReasonCodes": list(self.rollout_reason_codes),
                "limitedAutomaticPaperCaps": self.limited_automatic_paper_caps,
                "cancelUnfilledEntryOrdersRequired": self.cancel_unfilled_entry_orders_required,
                "controlRevision": self.control_revision,
                "controlHash": self.control_hash,
                "updatedAt": self.updated_at.isoformat(),
                "updatedBy": self.updated_by,
                "reasonCodes": list(self.reason_codes),
                "dependencyHealth": self.dependency_health,
                "liveTradingEnabled": False,
                "riskReducingExitsEnabled": True,
                "protectiveOrdersEnabled": True,
                "reconciliationEnabled": True,
            }
        )
        return payload


def default_wca_runtime_control(
    *,
    broker_account_id: str = "paper",
    symbol: str = "SPY",
    updated_by: str = "system",
    reason: str = "wca.runtime_control.default_fail_closed",
    reason_codes: tuple[str, ...] = ("wca.runtime_control.default_fail_closed",),
    control_revision: int = 1,
) -> WcaRuntimeControl:
    now = datetime.now(timezone.utc)
    return WcaRuntimeControl(
        broker_account_id=broker_account_id,
        symbol=symbol.upper(),
        timestamp=now,
        paper_trading_requested=False,
        automatic_entries_requested=False,
        pause_new_entries=True,
        kill_switch_open=False,
        effective_paper_trading_enabled=False,
        effective_automatic_entries_enabled=False,
        automatic_entry_currently_permitted=False,
        control_revision=control_revision,
        updated_at=now,
        updated_by=updated_by,
        reason=reason,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    ).with_hash()


def resolve_wca_effective_runtime_control(
    requested: WcaRuntimeControl | None,
    evidence: WcaRuntimeControlEvidence,
    *,
    now: datetime | None = None,
    updated_by: str | None = None,
    reason: str | None = None,
    reason_codes: tuple[str, ...] = (),
    increment_revision: bool = False,
) -> WcaRuntimeControl:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prior = requested or default_wca_runtime_control()
    blockers: list[str] = []

    def require(condition: bool, code: str) -> None:
        if not condition:
            blockers.append(code)

    require(prior.paper_trading_requested, "wca.runtime_control.paper_trading_not_requested")
    require(prior.automatic_entries_requested, "wca.runtime_control.automatic_entries_not_requested")
    require(not prior.pause_new_entries, "wca.runtime_control.pause_new_entries")
    require(not prior.kill_switch_open, "wca.runtime_control.kill_switch_open")
    require(evidence.paper_execution_env_enabled, "wca.runtime_control.paper_execution_env_disabled")
    require(evidence.rollout_automatic_paper_permitted, "wca.runtime_control.rollout_automatic_paper_blocked")
    require(evidence.global_gate_available, "wca.runtime_control.global_gate_unavailable")
    require(evidence.runtime_healthy, "wca.runtime_control.runtime_unhealthy")
    require(evidence.paper_account_verified, "wca.runtime_control.paper_account_unverified")
    require(evidence.market_open, "wca.runtime_control.market_closed")
    require(evidence.inside_entry_window, "wca.runtime_control.outside_entry_window")
    require(evidence.market_data_fresh, "wca.runtime_control.market_data_stale")
    require(evidence.inventory_reconciled, "wca.runtime_control.inventory_not_reconciled")
    require(evidence.wca_circuit_breaker_closed, "wca.runtime_control.wca_circuit_breaker_open")
    require(evidence.global_circuit_breaker_closed, "wca.runtime_control.global_circuit_breaker_open")
    require(evidence.configuration_ready, "wca.runtime_control.configuration_not_ready")
    require(evidence.weight_ready, "wca.runtime_control.weight_snapshot_not_ready")
    require(evidence.calibration_ready, "wca.runtime_control.calibration_not_ready")

    automatic_permitted = bool(
        evidence.paper_execution_env_enabled
        and evidence.rollout_automatic_paper_permitted
        and evidence.global_gate_available
        and evidence.paper_account_verified
        and evidence.configuration_ready
        and evidence.weight_ready
        and evidence.calibration_ready
    )
    entry_permitted = not blockers
    effective_paper = bool(prior.paper_trading_requested and evidence.paper_account_verified and evidence.paper_execution_env_enabled)
    merged_reasons = tuple(
        dict.fromkeys(
            (
                "wca.runtime_control.resolved",
                *(reason_codes or prior.reason_codes),
                *evidence.reason_codes,
                *blockers,
            )
        )
    )
    control = WcaRuntimeControl(
        broker_account_id=prior.broker_account_id,
        symbol=prior.symbol.upper(),
        decision_id=prior.decision_id,
        order_intent_id=prior.order_intent_id,
        run_id=prior.run_id,
        configuration_version=prior.configuration_version,
        configuration_hash=prior.configuration_hash,
        weight_version=prior.weight_version,
        timestamp=timestamp,
        paper_trading_requested=prior.paper_trading_requested,
        automatic_entries_requested=prior.automatic_entries_requested,
        pause_new_entries=prior.pause_new_entries,
        kill_switch_open=prior.kill_switch_open,
        effective_paper_trading_enabled=effective_paper,
        effective_automatic_entries_enabled=entry_permitted,
        paper_account_verified=evidence.paper_account_verified,
        automatic_paper_permitted=automatic_permitted,
        automatic_entry_currently_permitted=entry_permitted,
        rollout_stage=evidence.rollout_stage,
        rollout_evidence_revision=evidence.rollout_evidence_revision,
        rollout_evidence_hash=evidence.rollout_evidence_hash,
        rollout_reason_codes=evidence.rollout_reason_codes,
        limited_automatic_paper_caps=evidence.limited_automatic_paper_caps,
        cancel_unfilled_entry_orders_required=evidence.cancel_unfilled_entry_orders_required,
        control_revision=prior.control_revision + 1 if increment_revision else prior.control_revision,
        updated_at=timestamp,
        updated_by=updated_by or prior.updated_by,
        reason=reason or prior.reason,
        reason_codes=merged_reasons,
        dependency_health=evidence.dependency_health,
    )
    return control.with_hash()


def _control_hash(control: WcaRuntimeControl) -> str:
    payload = control.model_dump(mode="json", exclude={"control_hash"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "WCA_RUNTIME_CONTROL_SCHEMA_VERSION",
    "WcaRuntimeControl",
    "WcaRuntimeControlEvidence",
    "default_wca_runtime_control",
    "resolve_wca_effective_runtime_control",
]
