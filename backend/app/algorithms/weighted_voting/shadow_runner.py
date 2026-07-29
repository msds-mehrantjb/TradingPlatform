"""Background shadow evidence runner for Weighted Voting.

This runner is intentionally evidence-only: it uses an isolated in-memory
store, keeps automatic paper submission disabled, and writes a durable JSON
report under Weighted Voting's own data directory.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.app.algorithms.weighted_voting.broker_reconciliation import (
    WeightedVotingBrokerFillObservation,
    WeightedVotingBrokerPositionObservation,
    reconcile_weighted_voting_broker_observations,
)
from backend.app.algorithms.weighted_voting.decision_kernel import WeightedVotingDecisionKernel
from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.global_interface import (
    WeightedVotingStaticCentralGlobalRiskService,
    apply_global_response_to_weighted_voting_proposal,
    build_global_order_proposal_from_weighted_voting_proposal,
)
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.persistence import persist_effective_settings
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation
from backend.app.algorithms.weighted_voting.runtime_context import (
    WeightedVotingExecutionCostEstimate,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingStaticAccountPort,
    WeightedVotingStaticGlobalRiskPort,
    WeightedVotingStaticInventorySnapshotPort,
    WeightedVotingStaticMarketDataPort,
)
from backend.app.algorithms.weighted_voting.runtime_supervisor import (
    WeightedVotingEventBus,
    WeightedVotingFinalisedBarEvent,
    WeightedVotingRuntimeConfig,
    WeightedVotingRuntimeSupervisor,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state
from backend.app.gates import GlobalGateResponse
from backend.app.algorithms.weighted_voting.execution_gateway import enqueue_weighted_voting_execution_order


SHADOW_RUNNER_VERSION = "weighted_voting_shadow_runner_v1"
SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}

    def read_snapshot(self, key: str) -> dict[str, Any]:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        self.snapshots[key] = snapshot


def run_shadow_evidence(*, events: int = 12, delay_seconds: float = 0.0, output_dir: Path | None = None) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    run_id = f"weighted-voting-shadow-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    store = MemoryStore()
    effective = resolve_effective_settings(
        timestamp=SESSION_OPEN,
        expiration_timestamp=SESSION_OPEN + timedelta(days=30),
        source_evidence=("weighted_voting.shadow_runner.stable_versioned_settings",),
    )
    persist_effective_settings(store, effective)
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(
        session_date=SESSION_OPEN.date(),
        allocated_capital=25_000.0,
        cash_available=25_000.0,
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=0,
        event_id=f"{run_id}.session-start",
    )
    supervisor = WeightedVotingRuntimeSupervisor(
        service=WeightedVotingService(store=store, central_risk_service=WeightedVotingStaticCentralGlobalRiskService()),
        store=store,
        inventory_repository=inventory,
        account_port=WeightedVotingStaticAccountPort(account_equity=100_000.0, broker_buying_power=75_000.0, source_id="weighted_voting.shadow_runner.account"),
        global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=1_000.0, global_max_shares=100_000, gate_response=None, source_id="weighted_voting.shadow_runner.global_risk"),
        rollout_flags=WeightedVotingRolloutFlags(v2_enabled=True, shadow_mode=True, auto_submit_enabled=False),
        rollout_validation=WeightedVotingRolloutValidation(),
        config=WeightedVotingRuntimeConfig(queue_maxsize=max(8, events + 4), max_queue_lag_seconds=86_400, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=max(8, events + 4)),
    )

    event_records: list[dict[str, Any]] = []
    duplicate_record: dict[str, Any] | None = None
    for index in range(events):
        payload = _shadow_payload(offset_minutes=index)
        event = _event_from_payload(payload, published_at=datetime.now(timezone.utc))
        before = time.perf_counter()
        record = _run_async(supervisor.process_finalised_bar_event(event))
        latency_ms = round((time.perf_counter() - before) * 1000.0, 3)
        event_records.append(
            {
                "index": index,
                "eventId": event.event_id,
                "timestamp": event.finalised_candle_timestamp.isoformat(),
                "status": record.get("status"),
                "decisionId": record.get("decision_id") or record.get("decisionId"),
                "latencyMs": latency_ms,
            }
        )
        if index == 0:
            duplicate_record = _run_async(supervisor.process_finalised_bar_event(event))
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    accepted_probe = _accepted_probe(store, effective)
    completed_at = datetime.now(timezone.utc)
    evidence = _evidence_report(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        store=store,
        supervisor=supervisor,
        event_records=event_records,
        duplicate_record=duplicate_record,
        accepted_probe=accepted_probe,
        settings_version=effective.settings_version,
    )
    output = _write_report(evidence, output_dir=output_dir)
    evidence["outputPath"] = str(output)
    output.write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    return evidence


def _accepted_probe(store: MemoryStore, effective) -> dict[str, Any]:
    payload = _shadow_payload(offset_minutes=100)
    snapshot = build_weighted_voting_market_snapshot(payload)
    inventory_snapshot = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0).current_snapshot(
        now=snapshot.data_timestamp,
        session_date=snapshot.data_timestamp.date(),
    )
    context = WeightedVotingRuntimeContextBuilder(
        market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
        inventory_repository=WeightedVotingStaticInventorySnapshotPort(inventory_snapshot),
        account_port=WeightedVotingStaticAccountPort(account_equity=100_000.0, broker_buying_power=75_000.0, source_id="weighted_voting.shadow_runner.acceptance_account"),
        global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=1_000.0, global_max_shares=100_000, gate_response=None, source_id="weighted_voting.shadow_runner.acceptance_global_risk"),
        effective_settings=effective,
        active_weight_state=create_unseeded_equal_weight_state(timestamp=snapshot.data_timestamp, data_timestamp=snapshot.data_timestamp),
        observed_at=snapshot.data_timestamp,
        mode="test_fixture",
        cost_estimate=WeightedVotingExecutionCostEstimate(
            slippage_per_share=effective.slippage_allowance_per_share,
            fee_per_share=effective.fee_per_share,
            observed_at=snapshot.data_timestamp,
            source_id="weighted_voting.shadow_runner.stable_cost_model",
            reason_codes=("weighted_voting.shadow_runner.stable_cost_model",),
        ),
    ).build()
    kernel = WeightedVotingDecisionKernel.evaluate(context, signal_evaluator=_shadow_buy_signal_evaluator)
    global_proposal = build_global_order_proposal_from_weighted_voting_proposal(
        proposal=kernel.order_proposal,
        decision=kernel.decision,
        sizing=kernel.sizing_result,
        effective_settings=kernel.effective_settings,
    )
    response = GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=global_proposal.quantity,
        maximumAdditionalRiskDollars=global_proposal.plannedRiskDollars,
        rejectionReasons=(),
        evaluatedAt=snapshot.data_timestamp,
        configurationHash="weighted_voting_shadow_runner_global_allow",
    )
    application = apply_global_response_to_weighted_voting_proposal(global_proposal, response)
    item = enqueue_weighted_voting_execution_order(
        store=store,
        proposal=global_proposal,
        global_application=application,
        local_gate_result=kernel.gate_result,
        enqueued_at=snapshot.data_timestamp,
        idempotency_key=f"{global_proposal.orderIntentId}.shadow",
    )
    if item is None:
        return {
            "accepted": False,
            "reasonCodes": tuple(kernel.reason_codes),
            "decisionSignal": kernel.decision.signal,
            "quantity": 0,
        }

    slippage = effective.slippage_allowance_per_share
    fill_price = round(float(global_proposal.limitPrice or global_proposal.triggerPrice or snapshot.ask) + slippage, 4)
    mark_price = round(fill_price + 0.18, 4)
    fill = WeightedVotingBrokerFillObservation(
        fill_id=f"{item.command.client_order_id}.shadow-fill-1",
        client_order_id=item.command.client_order_id,
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=global_proposal.symbol,
        side="BUY",
        quantity=item.command.quantity,
        average_fill_price=fill_price,
        filled_at=snapshot.data_timestamp + timedelta(seconds=3),
        broker_order_id=f"{item.command.client_order_id}.shadow-broker",
    )
    position = WeightedVotingBrokerPositionObservation(
        client_order_id=item.command.client_order_id,
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=global_proposal.symbol,
        quantity=item.command.quantity,
        average_entry_price=fill_price,
        observed_at=snapshot.data_timestamp + timedelta(seconds=4),
        broker_position_id=f"{item.command.client_order_id}.shadow-position",
    )
    repo = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    reconciliation = reconcile_weighted_voting_broker_observations(
        store=store,
        inventory_repository=repo,
        fills=(fill,),
        positions=(position,),
        reconciled_at=snapshot.data_timestamp + timedelta(seconds=5),
    )
    duplicate_reconciliation = reconcile_weighted_voting_broker_observations(
        store=store,
        inventory_repository=repo,
        fills=(fill,),
        positions=(position,),
        reconciled_at=snapshot.data_timestamp + timedelta(seconds=6),
    )
    current_after_reconciliation = repo.current_snapshot(now=snapshot.data_timestamp + timedelta(seconds=6))
    rebuilt_after_restart = repo.rebuild_snapshot_from_events()
    restart_recovery = {
        "passed": rebuilt_after_restart.snapshot_version == current_after_reconciliation.snapshot_version
        and len(rebuilt_after_restart.open_positions) == len(current_after_reconciliation.open_positions),
        "rebuiltSnapshotVersion": rebuilt_after_restart.snapshot_version,
        "currentSnapshotVersion": current_after_reconciliation.snapshot_version,
        "reasonCodes": ("weighted_voting.shadow_runner.restart_recovery_rebuild_verified",),
    }
    protective_order = _protective_order_check(global_proposal)
    gross_pnl = (mark_price - fill_price) * item.command.quantity
    estimated_fees = effective.fee_per_share * item.command.quantity
    return {
        "accepted": True,
        "decisionId": kernel.decision.decision_id,
        "decisionSignal": kernel.decision.signal,
        "orderIntentId": global_proposal.orderIntentId,
        "clientOrderId": item.command.client_order_id,
        "quantity": item.command.quantity,
        "expectedValueReasonCodes": tuple(kernel.reason_codes),
        "localGatePassed": kernel.gate_result.permission_granted,
        "globalGateAction": application.action,
        "globallyAllowedQuantity": application.globallyAllowedQuantity,
        "simulatedFill": {
            "fillId": fill.fill_id,
            "averageFillPrice": fill.average_fill_price,
            "markPrice": mark_price,
            "expectedSlippagePerShare": slippage,
            "observedSlippagePerShare": round(fill_price - float(global_proposal.limitPrice or global_proposal.triggerPrice or fill_price), 10),
            "estimatedFees": round(estimated_fees, 10),
        },
        "simulatedPnl": {
            "grossUnrealized": round(gross_pnl, 10),
            "netUnrealizedAfterFees": round(gross_pnl - estimated_fees, 10),
        },
        "reconciliation": reconciliation.as_dict(),
        "duplicateFillReconciliation": duplicate_reconciliation.as_dict(),
        "restartRecovery": restart_recovery,
        "protectiveOrderBehavior": protective_order,
    }


def _evidence_report(
    *,
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    store: MemoryStore,
    supervisor: WeightedVotingRuntimeSupervisor,
    event_records: list[dict[str, Any]],
    duplicate_record: dict[str, Any] | None,
    accepted_probe: dict[str, Any],
    settings_version: str,
) -> dict[str, Any]:
    decisions = _snapshots_with_prefix(store, "weighted_voting.decisions.")
    proposals = _snapshots_with_prefix(store, "weighted_voting.order_proposals.")
    global_applications = _snapshots_with_prefix(store, "weighted_voting.global_gate_applications.")
    runtime_contexts = _snapshots_with_prefix(store, "weighted_voting.runtime.contexts.")
    skipped = [decision for decision in decisions if str(decision.get("signal")) == "Hold" or int(decision.get("proposed_quantity") or 0) <= 0]
    accepted = [
        proposal
        for proposal in proposals
        if proposal.get("side") in {WeightedSide.BUY.value, WeightedSide.SELL.value} and int(proposal.get("quantity") or 0) > 0
    ]
    latency_values = [item["latencyMs"] for item in event_records]
    duplicate_prevention = {
        "duplicateEventStatus": (duplicate_record or {}).get("status"),
        "duplicateEventPrevented": (duplicate_record or {}).get("status") == "duplicate_noop",
        "runtimeDuplicateCount": supervisor.health().get("duplicateEvents"),
        "duplicateFillIds": accepted_probe.get("duplicateFillReconciliation", {}).get("duplicateFillIds", ()),
    }
    reconciliation = accepted_probe.get("reconciliation", {})
    health = supervisor.health()
    return {
        "runnerVersion": SHADOW_RUNNER_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "runId": run_id,
        "mode": "background_shadow_evidence",
        "liveOrdersSubmitted": False,
        "automaticPaperSubmissionEnabled": False,
        "settingsVersion": settings_version,
        "startedAt": started_at.isoformat(),
        "completedAt": completed_at.isoformat(),
        "durationSeconds": round((completed_at - started_at).total_seconds(), 3),
        "decisions": {
            "count": len(decisions),
            "items": decisions,
        },
        "skippedTrades": {
            "count": len(skipped),
            "items": skipped,
        },
        "acceptedProposals": {
            "count": len(accepted) + (1 if accepted_probe.get("accepted") else 0),
            "runtimeItems": accepted,
            "controlledAcceptanceProbe": accepted_probe,
        },
        "latency": {
            "eventCount": len(event_records),
            "minMs": round(min(latency_values), 3) if latency_values else None,
            "maxMs": round(max(latency_values), 3) if latency_values else None,
            "avgMs": round(sum(latency_values) / len(latency_values), 3) if latency_values else None,
            "events": event_records,
            "runtimeDecisionLatencyMs": health.get("metrics", {}).get("decisionLatencyMs"),
        },
        "simulatedFills": accepted_probe.get("simulatedFill"),
        "slippage": accepted_probe.get("simulatedFill", {}),
        "pnl": accepted_probe.get("simulatedPnl"),
        "duplicatePrevention": duplicate_prevention,
        "reconciliationHealth": {
            "inventoryReconciled": reconciliation.get("inventoryReconciled"),
            "entriesPaused": reconciliation.get("entriesPaused"),
            "discrepancyCount": len(reconciliation.get("discrepancies", ())),
            "appliedFillIds": reconciliation.get("appliedFillIds", ()),
            "duplicateFillIds": accepted_probe.get("duplicateFillReconciliation", {}).get("duplicateFillIds", ()),
            "runtimeHealth": {
                "inventoryReconciled": health.get("inventoryReconciled"),
                "entryCreationPausedForReconciliation": health.get("entryCreationPausedForReconciliation"),
                "recoveryRequired": health.get("recoveryRequired"),
                "automaticOrderCreationPaused": health.get("automaticOrderCreationPaused"),
            },
        },
        "restartRecovery": accepted_probe.get("restartRecovery"),
        "protectiveOrderBehavior": accepted_probe.get("protectiveOrderBehavior"),
        "runtimeContexts": {
            "count": len(runtime_contexts),
            "items": runtime_contexts,
        },
        "globalGateApplications": {
            "count": len(global_applications),
            "items": global_applications,
        },
        "reasonCodes": (
            "weighted_voting.shadow_runner.completed",
            "weighted_voting.shadow_runner.no_live_orders_submitted",
            "weighted_voting.shadow_runner.isolated_weighted_voting_inventory",
        ),
    }


def _shadow_buy_signal_evaluator(snapshot, _config=None, _weights=None, _condition=None):
    family_by_strategy = {
        "S2": WeightedStrategyFamily.TREND,
        "S7": WeightedStrategyFamily.MEAN_REVERSION,
        "S5": WeightedStrategyFamily.REVERSAL,
        "S6": WeightedStrategyFamily.REVERSAL,
    }
    return tuple(
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} shadow acceptance signal",
            strategy_version="weighted_strategy_shadow_acceptance_v1",
            family=family,
            signal=WeightedSide.BUY,
            p_buy=0.75,
            p_sell=0.05,
            p_hold=0.20,
            directional_confidence=0.75,
            signal_strength=0.75,
            expected_raw_movement=0.002,
            expected_return=0.002,
            expected_return_after_costs=0.0015,
            strength=0.75,
            final_weight=0.25,
            eligible=True,
            data_ready=True,
            required_data_freshness_seconds=300,
            actual_data_freshness_seconds=0,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=snapshot.data_timestamp,
            explanation="Deterministic Weighted Voting shadow acceptance probe signal.",
        )
        for strategy_id, family in family_by_strategy.items()
    )


def _protective_order_check(global_proposal) -> dict[str, Any]:
    entry = float(global_proposal.limitPrice or global_proposal.triggerPrice or 0.0)
    stop = float(global_proposal.stopPrice or 0.0)
    target = float(global_proposal.targetPrice or 0.0)
    quantity = int(global_proposal.quantity or 0)
    side = str(global_proposal.side)
    buy_valid = side == "BUY" and quantity > 0 and stop > 0 and target > 0 and stop < entry < target
    sell_valid = side == "SELL" and quantity > 0 and stop > 0 and target > 0 and target < entry < stop
    return {
        "passed": bool(buy_valid or sell_valid),
        "side": side,
        "quantity": quantity,
        "entry": entry,
        "stop": stop,
        "target": target,
        "bracketComplete": stop > 0 and target > 0,
        "reasonCodes": ("weighted_voting.shadow_runner.protective_order_geometry_verified",),
    }


def _shadow_payload(*, offset_minutes: int) -> dict[str, Any]:
    rows = []
    start = SESSION_OPEN + timedelta(minutes=offset_minutes)
    for index in range(95):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": round(base, 4),
                "high": round(base + 0.45, 4),
                "low": round(base - 0.18, 4),
                "close": round(base + 0.08, 4),
                "volume": 200_000 if index != 5 else 5_000,
                "finalized": True,
            }
        )
    five_minute = [
        {
            "timestamp": rows[index]["timestamp"],
            "open": rows[index - 4]["open"],
            "high": max(row["high"] for row in rows[index - 4 : index + 1]),
            "low": min(row["low"] for row in rows[index - 4 : index + 1]),
            "close": rows[index]["close"],
            "volume": sum(row["volume"] for row in rows[index - 4 : index + 1]),
            "finalized": True,
        }
        for index in range(4, len(rows), 5)
    ]
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "five_minute_candles": five_minute,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "session_phase": "morning",
        "data_freshness_seconds": 0.0,
    }


def _event_from_payload(payload: dict[str, Any], *, published_at: datetime) -> WeightedVotingFinalisedBarEvent:
    snapshot = build_weighted_voting_market_snapshot(payload)
    return WeightedVotingFinalisedBarEvent(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        symbol=snapshot.symbol,
        finalised_candle_timestamp=snapshot.data_timestamp,
        data_manifest_hash=snapshot.data_manifest_hash or "",
        market_payload=payload,
        published_at=published_at,
    )


def _run_async(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _snapshots_with_prefix(store: MemoryStore, prefix: str) -> list[dict[str, Any]]:
    return [value for key, value in sorted(store.snapshots.items()) if key.startswith(prefix)]


def _write_report(evidence: dict[str, Any], *, output_dir: Path | None) -> Path:
    directory = output_dir or Path(__file__).resolve().parents[3] / "data" / "algorithms" / "weighted_voting" / "shadow_evidence"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{evidence['runId']}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Weighted Voting in local background shadow evidence mode.")
    parser.add_argument("--events", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    evidence = run_shadow_evidence(events=max(1, args.events), delay_seconds=max(0.0, args.delay_seconds), output_dir=args.output_dir)
    print(json.dumps({"runId": evidence["runId"], "outputPath": evidence["outputPath"]}, sort_keys=True))


if __name__ == "__main__":
    main()
