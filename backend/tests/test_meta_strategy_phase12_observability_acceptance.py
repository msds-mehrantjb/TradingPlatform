from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    ALGORITHM_ID,
    META_STRATEGY_FINALIZED_CANDLE_TERMINAL_OUTCOMES,
    META_STRATEGY_FINAL_DOD_IDS,
    META_STRATEGY_OPERATIONAL_CONTROLS,
    META_STRATEGY_RECOVERY_TEST_IDS,
    MetaStrategyApplicationService,
    MetaStrategyDurableFinalisedBarDecisionWorker,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.observability import (
    apply_meta_strategy_operational_control,
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
    record_meta_strategy_test_evidence,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings


NOW = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)


class MetaStrategyPhase12ObservabilityAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path("data/test_tmp").resolve() / f"meta-strategy-phase12-{uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        self.jobs = MetaStrategyJobRepository(f"sqlite:///{root / 'jobs.db'}")
        self.inventory = MetaStrategySqliteRepository(f"sqlite:///{root / 'inventory.db'}")
        self.settings = MetaStrategySettingsStore(root / "settings.db")
        self._phase12_bar_offset = 0

    def _claimed_finalized_bar(self, *, symbol: str = "SPY", bar_end: datetime | None = None, settings_version: str = "settings-v1"):
        if bar_end is None:
            self._phase12_bar_offset += 1
            bar_end = NOW - timedelta(minutes=1, seconds=self._phase12_bar_offset)
        job = self.jobs.enqueue_finalised_bar_decision(
            mode="PAPER",
            symbol=symbol,
            timeframe="1m",
            bar_end=bar_end,
            settings_version=settings_version,
            now=NOW - timedelta(seconds=2),
        )
        claimed = self.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="phase12-test-helper", now=NOW)
        assert claimed is not None
        job_payload = self.jobs.read_payload(job.payload_reference)
        event_payload = job_payload.get("payload") if isinstance(job_payload.get("payload"), dict) else job_payload
        return claimed, self.jobs.event_by_id(str(event_payload.get("eventId") or event_payload.get("event_id")))

    def _persist_phase12_decision(
        self,
        *,
        decision_id: str,
        symbol: str = "SPY",
        final_valid: bool = True,
        decision_status: str = "ORDER_PROPOSED",
        reason_codes: tuple[str, ...] = (),
        order_intent_id: str | None = None,
    ) -> dict[str, str | None]:
        claimed, event = self._claimed_finalized_bar(symbol=symbol)
        event_payload = self.jobs.read_payload(event.payload_reference).get("payload") or {}
        bar_end = str(event_payload["barEnd"])
        payload = {
            "eventId": event.event_id,
            "jobId": claimed.job_id,
            "decisionId": decision_id,
            "symbol": symbol,
            "barEnd": bar_end,
            "settingsVersion": "settings-v1",
            "modelVersion": "model-v1",
            "decisionStatus": decision_status,
            "finalValid": final_valid,
            "dataAgeSeconds": 7,
            "latencyMs": 88,
            "quoteTimestamp": (NOW - timedelta(seconds=11)).isoformat(),
            "reasonCodes": reason_codes,
            "authoritativeState": {
                "globalRiskSnapshot": {"availableRiskDollars": 250.0},
                "inventorySnapshot": {"positionQuantity": 0, "reservedRisk": 0.0},
            },
            "latencyMeasurements": {
                "marketSnapshotMs": 3,
                "strategyEvaluationMs": 8,
                "safetyMs": 2,
                "modelInferenceMs": 4,
                "decisionPersistenceTimeMs": 1,
            },
            "stages": {
                "strategyEvidence": {
                    "directionalOutputs": {
                        "relative_strength": {
                            "strategyId": "relative_strength",
                            "strategyVersion": "rs-v1",
                            "familyId": "trend",
                            "signal": "BUY",
                            "confidence": 0.72,
                            "eligible": True,
                            "dataQuality": "OK",
                            "evidence": {"qqqIwmRatio": 1.03},
                            "vetoes": (),
                            "reasonCodes": (),
                            "evaluatedAt": NOW.isoformat(),
                        }
                    }
                },
                "regime": {"sessionPhase": "REGULAR", "trendRegime": "UP", "reasonCodes": ()},
                "safetyResult": {"status": "OK", "eligible": final_valid, "reasonCodes": reason_codes},
                "aggregateCandidate": {"winningScore": 0.61, "edge": 0.14, "supportingFamilies": ("trend",), "opposingFamilies": ()},
                "orderProposal": {"geometry": {"stopDistance": 0.55}, "costEstimate": {"spreadBps": 1.2}},
                "modelPrediction": {"status": "OK", "decision": "CONFIRM"},
                "decisionPolicy": {"finalSignal": "BUY" if order_intent_id else "HOLD"},
                "localRisk": {"status": "OK", "passed": final_valid},
                "sizing": {"approvedQuantity": 3, "maxByRisk": 3, "maxByBuyingPower": 3},
            },
        }
        order_intent = None
        if order_intent_id is not None:
            order_intent = {
                "algorithmId": ALGORITHM_ID,
                "capitalPartitionId": "meta_strategy.paper.default",
                "mode": "PAPER",
                "settingsVersion": "settings-v1",
                "modelVersion": "model-v1",
                "decisionId": decision_id,
                "jobId": claimed.job_id,
                "eventId": event.event_id,
                "orderIntentId": order_intent_id,
                "symbol": symbol,
                "side": "BUY",
                "quantity": 3,
                "limitPrice": 100.0,
                "stopPrice": 99.45,
                "targetPrice": 101.1,
                "reservedRiskDollars": 1.65,
                "localGatesPassed": True,
                "decisionTimestamp": bar_end,
                "quoteTimestamp": (NOW - timedelta(seconds=11)).isoformat(),
                "buyingPower": 10_000.0,
                "createdAt": NOW.isoformat(),
                "timestamp": NOW.isoformat(),
            }
        persisted = self.jobs.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id=decision_id,
            payload=payload,
            order_intent=order_intent,
            now=NOW,
        )
        self.jobs.complete_job(claimed.job_id, worker_id="phase12-test-helper", result=persisted, now=NOW)
        return {"eventId": event.event_id, "jobId": claimed.job_id, "outboxId": persisted.get("outboxId")}

    def test_finalized_candle_outcome_ledger_records_terminal_outcomes_and_redacts_sensitive_payloads(self) -> None:
        self.assertEqual(
            META_STRATEGY_FINALIZED_CANDLE_TERMINAL_OUTCOMES,
            frozenset({"NO_DECISION", "HOLD", "BLOCKED", "ORDER_PROPOSED", "ORDER_SUBMITTED", "ORDER_REJECTED", "RECONCILIATION_REQUIRED"}),
        )
        proposed = self._persist_phase12_decision(decision_id="decision-proposed", order_intent_id="intent-proposed")
        assert proposed["outboxId"] is not None

        outcome = self.jobs.finalized_candle_outcome(str(proposed["eventId"]))
        assert outcome is not None
        self.assertEqual(outcome["outcome"], "ORDER_PROPOSED")
        self.assertEqual(outcome["decisionId"], "decision-proposed")
        self.assertEqual(outcome["orderIntentId"], "intent-proposed")
        for key in (
            "strategyResults",
            "regimeContext",
            "safetyResult",
            "familyAggregation",
            "candidateScore",
            "geometry",
            "costEstimate",
            "modelResult",
            "localGates",
            "sizingCaps",
            "globalRisk",
            "latencyPerStage",
            "reasonCodes",
        ):
            self.assertIn(key, outcome["payload"])

        self.jobs.update_execution_outbox(
            str(proposed["outboxId"]),
            status="ACKNOWLEDGED",
            payload={
                "executionGuard": {"eligible": True, "reasonCodes": ()},
                "gatewayResult": {
                    "status": "ACKNOWLEDGED",
                    "brokerOrderId": "paper-order-1",
                    "Authorization": "Bearer must-not-persist",
                    "apiKey": "must-not-persist",
                },
            },
            client_order_id="meta-strategy-client-proposed",
            broker_order_id="paper-order-1",
            now=NOW + timedelta(seconds=1),
        )
        submitted = self.jobs.finalized_candle_outcome(str(proposed["eventId"]))
        assert submitted is not None
        self.assertEqual(submitted["outcome"], "ORDER_SUBMITTED")
        self.assertEqual(submitted["payload"]["brokerResult"]["Authorization"], "[REDACTED]")
        self.assertEqual(submitted["payload"]["brokerResult"]["apiKey"], "[REDACTED]")
        self.assertEqual(sum(1 for row in self.jobs.finalized_candle_outcomes(limit=10) if row["eventId"] == proposed["eventId"]), 1)

        rejected = self._persist_phase12_decision(decision_id="decision-rejected", order_intent_id="intent-rejected")
        self.jobs.update_execution_outbox(
            str(rejected["outboxId"]),
            status="REJECTED",
            payload={"reasonCodes": ("meta_strategy.execution_guard.market_closed",), "gatewayResult": {"status": "REJECTED"}},
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(self.jobs.finalized_candle_outcome(str(rejected["eventId"]))["outcome"], "ORDER_REJECTED")

        unknown = self._persist_phase12_decision(decision_id="decision-unknown", order_intent_id="intent-unknown")
        self.jobs.update_execution_outbox(
            str(unknown["outboxId"]),
            status="RECONCILIATION_REQUIRED",
            payload={"reasonCodes": ("meta_strategy.paper_broker.unknown_outcome",), "gatewayResult": {"status": "UNKNOWN"}},
            now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(self.jobs.finalized_candle_outcome(str(unknown["eventId"]))["outcome"], "RECONCILIATION_REQUIRED")

    def test_hold_blocked_and_no_decision_outcomes_are_recorded_for_finalized_candles(self) -> None:
        hold = self._persist_phase12_decision(decision_id="decision-hold", decision_status="HOLD_OR_BLOCKED", order_intent_id=None)
        blocked = self._persist_phase12_decision(
            decision_id="decision-blocked",
            final_valid=False,
            decision_status="BLOCKED",
            reason_codes=("meta_strategy.safety.hard_gate_blocked",),
            order_intent_id=None,
        )
        self.assertEqual(self.jobs.finalized_candle_outcome(str(hold["eventId"]))["outcome"], "HOLD")
        blocked_outcome = self.jobs.finalized_candle_outcome(str(blocked["eventId"]))
        self.assertEqual(blocked_outcome["outcome"], "BLOCKED")
        self.assertIn("meta_strategy.safety.hard_gate_blocked", blocked_outcome["reasonCodes"])

        class FailingStateProvider:
            def load_context(self, event):  # noqa: ANN001
                raise RuntimeError("authorization=must-not-persist")

        job = self.jobs.enqueue_finalised_bar_decision(
            mode="PAPER",
            symbol="QQQ",
            timeframe="1m",
            bar_end=NOW - timedelta(minutes=1),
            settings_version="settings-v1",
            now=NOW,
        )
        event_payload = self.jobs.read_payload(job.payload_reference).get("payload")
        worker = MetaStrategyDurableFinalisedBarDecisionWorker(repository=self.jobs, state_provider=FailingStateProvider(), worker_id="phase12-failing-worker")
        worker.run_once(now=NOW + timedelta(seconds=1))

        no_decision = self.jobs.finalized_candle_outcome(str(event_payload["eventId"]))
        assert no_decision is not None
        self.assertEqual(no_decision["outcome"], "NO_DECISION")
        self.assertIn(self.jobs.read_job(job.job_id).status.value, {"RETRY", "FAILED", "DEAD_LETTER"})

    def test_observability_reports_required_metrics_and_versions(self) -> None:
        self.jobs.enqueue_job(job_type="training", idempotency_key="training-old", payload={}, now=NOW - timedelta(seconds=45))
        self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW - timedelta(minutes=1), settings_version="settings-v1", now=NOW - timedelta(seconds=30))
        self.jobs.record_worker_heartbeat(worker_id="decision-worker", queue_name="finalised_bar_decisions", now=NOW)
        self.jobs.write_gateway_snapshot("paper_broker_connectivity", {"status": "OK", "configured": True}, now=NOW)

        snapshot = build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        metrics = snapshot["metrics"]

        self.assertEqual(snapshot["algorithmId"], ALGORITHM_ID)
        for key in (
            "finalisedBarEventLagSeconds",
            "queueDepth",
            "oldestQueuedJob",
            "workerHeartbeat",
            "leaseRecoveryCount",
            "jobRetryCount",
            "jobDeadLetterCount",
            "snapshotDataAgeSeconds",
            "decisionLatencyMs",
            "blockedDecisionReasonCounts",
            "strategyActivationCounts",
            "orderOutboxOldestAgeSeconds",
            "brokerSubmissionLatencySeconds",
            "reconciliationLagSeconds",
            "openOrderAgeSeconds",
            "inventoryMismatchCount",
            "dailyPnl",
            "reservedRisk",
            "paperBrokerConnectivity",
            "settingsVersion",
            "modelVersion",
        ):
            self.assertIn(key, metrics)
        self.assertEqual(metrics["paperBrokerConnectivity"]["status"], "OK")
        self.assertGreaterEqual(metrics["strategyActivationCounts"]["active"], 8)

    def test_operational_controls_are_audited_without_deleting_job_evidence(self) -> None:
        queued = self.jobs.enqueue_job(job_type="training", idempotency_key="pending-control", payload={}, now=NOW)

        pause = apply_meta_strategy_operational_control(job_repository=self.jobs, control="pause_new_entries", actor="ops", reason="test.pause", now=NOW)
        disabled = apply_meta_strategy_operational_control(job_repository=self.jobs, control="disable_strategy", actor="ops", reason="test.disable", payload={"strategyId": "opening_range_breakout"}, now=NOW)
        cancelled = apply_meta_strategy_operational_control(job_repository=self.jobs, control="cancel_pending_jobs", actor="ops", reason="test.cancel", payload={"queueName": "training"}, now=NOW)

        self.assertEqual(pause.status, "RECORDED")
        self.assertEqual(disabled.payload["state"]["mode"], "DISABLED")
        self.assertEqual(cancelled.payload["cancelledJobs"], 1)
        self.assertEqual(self.jobs.read_job(queued.job_id).status.value, "CANCELLED")
        self.assertTrue(self.jobs.operational_events(event_type="control.pause_new_entries"))
        self.assertTrue(self.jobs.operational_events(event_type="cancel_pending_jobs"))

    def test_required_phase15_controls_are_supported_and_audited(self) -> None:
        for control in META_STRATEGY_OPERATIONAL_CONTROLS:
            with self.subTest(control=control):
                result = apply_meta_strategy_operational_control(
                    job_repository=self.jobs,
                    control=control,
                    actor="ops-user",
                    reason=f"test.{control.lower()}",
                    payload={"correlationId": f"corr-{control}"},
                    now=NOW,
                )

                self.assertEqual(result.status, "RECORDED")
                self.assertEqual(result.control, control)
                self.assertEqual(result.payload["actor"], "ops-user")
                self.assertEqual(result.payload["reason"], f"test.{control.lower()}")
                self.assertEqual(result.payload["correlationId"], f"corr-{control}")
                self.assertEqual(result.payload["requestedAt"], NOW.isoformat())
                self.assertTrue(self.jobs.operational_events(event_type=f"control.{control}"))

        snapshot = build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        self.assertEqual(tuple(snapshot["supportedControls"]), META_STRATEGY_OPERATIONAL_CONTROLS)
        self.assertTrue(snapshot["controls"]["EXIT_ONLY"]["state"]["exitOnly"])
        self.assertTrue(snapshot["controls"]["DISABLE_ML_INFLUENCE"]["state"]["mlInfluenceDisabled"])
        self.assertTrue(snapshot["controls"]["DISABLE_DYNAMIC_OVERLAYS"]["state"]["dynamicOverlaysDisabled"])
        self.assertTrue(snapshot["controls"]["STOP_META_RUNTIME"]["state"]["paperOrdersBlocked"])

    def test_unknown_operational_control_is_rejected_fail_closed(self) -> None:
        service = MetaStrategyApplicationService(settings_store=self.settings, job_repository=self.jobs, repository=self.inventory)

        result = service.apply_control("ENABLE_LIVE_TRADING", {"actor": "ops", "reason": "test.unsupported"})

        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("meta_strategy.controls.unsupported_control_rejected", result["reasonCodes"])
        self.assertFalse(result["payload"]["payload"]["state"]["supported"])

    def test_phase15_metrics_surface_required_operational_signals(self) -> None:
        job = self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW - timedelta(minutes=1), settings_version="settings-v1", now=NOW)
        claimed = self.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker", now=NOW)
        assert claimed is not None
        job_payload = self.jobs.read_payload(job.payload_reference)
        event_payload = job_payload.get("payload") if isinstance(job_payload.get("payload"), dict) else job_payload
        event = self.jobs.event_by_id(str(event_payload.get("eventId") or event_payload.get("event_id")))
        self.jobs.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id="decision-phase15",
            payload={
                "decisionId": "decision-phase15",
                "symbol": "SPY",
                "barEnd": (NOW - timedelta(minutes=1)).isoformat(),
                "settingsVersion": "settings-v1",
                "modelVersion": "model-v1",
                "decisionStatus": "HOLD_OR_BLOCKED",
                "finalValid": False,
                "latencyMs": 123,
                "reasonCodes": (
                    "meta_strategy.local_gate.minimum_independent_families_below_minimum",
                    "meta_strategy.inference.model_unavailable",
                    "meta_strategy.inference.ood_detected",
                ),
                "stages": {
                    "aggregateCandidate": {
                        "direction": "HOLD",
                        "directionalOutputs": {
                            "trend": {"signal": "BUY", "eligible": True},
                            "reversion": {"signal": "HOLD", "eligible": False},
                        },
                        "familyConflicts": ("trend_alignment",),
                    },
                    "decisionPolicy": {"finalSignal": "HOLD"},
                    "modelPrediction": {"status": "UNAVAILABLE"},
                },
            },
            order_intent=None,
            now=NOW,
        )
        self.jobs.complete_job(claimed.job_id, worker_id="decision-worker", now=NOW)
        self.jobs.record_worker_heartbeat(worker_id="decision-worker", queue_name="finalised_bar_decisions", now=NOW)
        self.jobs.record_operational_event("finalised_candle_enqueued", {"duplicate": False}, now=NOW)
        self.jobs.record_operational_event("finalised_candle_enqueued", {"duplicate": True}, now=NOW)
        self.jobs.record_operational_event("finalised_candle_data_quality", {"status": "MISSING_GAP"}, status="MISSING_GAP", now=NOW)
        self.jobs.write_gateway_snapshot("authoritative_market_clock", {"status": "OK", "capturedAt": (NOW - timedelta(seconds=4)).isoformat()}, now=NOW)
        self.jobs.update_paper_trading_control(new_paper_entries_enabled=True, updated_by="ops", reason="phase12.enable", now=NOW)
        self.jobs.update_paper_trading_control(new_paper_entries_enabled=False, updated_by="ops", reason="phase12.disable", expected_version=1, now=NOW + timedelta(seconds=1))
        rejected_order = self._persist_phase12_decision(decision_id="decision-phase12-rejected-metrics", order_intent_id="intent-phase12-rejected-metrics")
        self.jobs.update_execution_outbox(
            str(rejected_order["outboxId"]),
            status="REJECTED",
            payload={"reasonCodes": ("meta_strategy.execution_guard.duplicate_client_order_id",), "gatewayResult": {"status": "REJECTED"}},
            now=NOW + timedelta(seconds=2),
        )
        unknown_order = self._persist_phase12_decision(decision_id="decision-phase12-unknown-metrics", order_intent_id="intent-phase12-unknown-metrics")
        self.jobs.update_execution_outbox(
            str(unknown_order["outboxId"]),
            status="RECONCILIATION_REQUIRED",
            payload={"reasonCodes": ("meta_strategy.paper_broker.unknown_outcome",), "gatewayResult": {"status": "UNKNOWN"}},
            now=NOW + timedelta(seconds=3),
        )

        snapshot = build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        metrics = snapshot["metrics"]

        for key in (
            "finalizedBarCount",
            "duplicateBarCount",
            "missingBarCount",
            "queueDepth",
            "queueLagSeconds",
            "workerHeartbeat",
            "decisionLatencyMs",
            "decisionCountsBySide",
            "noTradeReasons",
            "strategySignalCounts",
            "strategyAbstentionCounts",
            "familyConflicts",
            "mlInferenceFailures",
            "oodRate",
            "orderSubmissionLatency",
            "brokerRejectionRate",
            "partialFillRate",
            "slippage",
            "inventoryMismatch",
            "openRisk",
            "reservedRisk",
            "realizedPnl",
            "unrealizedPnl",
            "dailyDrawdown",
            "restartFailures",
            "reconciliationFailures",
            "quoteAgeSeconds",
            "brokerClockAgeSeconds",
            "finalizedCandleOutcomeCounts",
            "rejectedEntriesByReason",
            "inventoryDivergence",
            "duplicateOrderAttempts",
            "riskReservations",
            "unknownBrokerOutcomes",
            "paperToggleStateTransitions",
        ):
            self.assertIn(key, metrics)
        self.assertEqual(metrics["finalizedBarCount"], 1)
        self.assertEqual(metrics["duplicateBarCount"], 1)
        self.assertEqual(metrics["missingBarCount"], 1)
        self.assertEqual(metrics["decisionCountsBySide"]["BLOCKED"], 1)
        self.assertEqual(metrics["strategySignalCounts"]["trend"]["BUY"], 1)
        self.assertEqual(metrics["strategyAbstentionCounts"]["reversion"], 1)
        self.assertEqual(metrics["familyConflicts"]["trend_alignment"], 1)
        self.assertEqual(metrics["mlInferenceFailures"], 1)
        self.assertGreater(metrics["oodRate"], 0.0)
        self.assertGreaterEqual(metrics["quoteAgeSeconds"]["count"], 1)
        self.assertGreaterEqual(metrics["brokerClockAgeSeconds"]["count"], 1)
        self.assertEqual(metrics["finalizedCandleOutcomeCounts"]["BLOCKED"], 1)
        self.assertEqual(metrics["finalizedCandleOutcomeCounts"]["ORDER_REJECTED"], 1)
        self.assertEqual(metrics["finalizedCandleOutcomeCounts"]["RECONCILIATION_REQUIRED"], 1)
        self.assertEqual(metrics["rejectedEntriesByReason"]["meta_strategy.execution_guard.duplicate_client_order_id"], 1)
        self.assertEqual(metrics["duplicateOrderAttempts"], 1)
        self.assertGreaterEqual(metrics["riskReservations"]["count"], 2)
        self.assertEqual(metrics["unknownBrokerOutcomes"], 1)
        self.assertEqual(len(metrics["paperToggleStateTransitions"]), 2)

    def test_recovery_evidence_paths_are_exercised_and_recorded(self) -> None:
        job = self.jobs.enqueue_job(job_type="training", idempotency_key="lease", payload={}, now=NOW)
        self.jobs.claim_next_job(queue_name="training", worker_id="worker-before-restart", lease_seconds=1, now=NOW)
        recovered = self.jobs.claim_next_job(queue_name="training", worker_id="worker-after-restart", lease_seconds=10, now=NOW + timedelta(seconds=2))
        first_event = self.jobs.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="dup-event", payload={}, now=NOW)
        duplicate_event = self.jobs.record_event(event_type="finalised_bar", queue_name="finalised_bar_decisions", idempotency_key="dup-event", payload={}, now=NOW)
        first_broker = self.jobs.record_broker_event({"algorithmId": ALGORITHM_ID, "brokerEventId": "broker-event-1", "clientOrderId": "client-1"}, now=NOW)
        duplicate_broker = self.jobs.record_broker_event({"algorithmId": ALGORITHM_ID, "brokerEventId": "broker-event-1", "clientOrderId": "client-1"}, now=NOW)
        baseline = build_meta_strategy_settings(settings_version="baseline-phase12", created_at=NOW)
        self.settings.create_baseline(baseline, actor="ops")
        self.settings.activate_settings("baseline-phase12", actor="ops")
        rolled_back = self.settings.rollback_to("baseline-phase12", actor="ops", reason="test.rollback")
        model_rollback = self.jobs.rollback_active_model(actor="ops", reason="test.model.rollback", now=NOW)
        rebuild = self.inventory.rebuild_inventory_from_ledger()

        self.assertEqual(recovered.job_id, job.job_id)
        self.assertFalse(first_event.duplicate)
        self.assertTrue(duplicate_event.duplicate)
        self.assertFalse(first_broker["duplicate"])
        self.assertTrue(duplicate_broker["duplicate"])
        self.assertEqual(rolled_back.restored_settings_version, "baseline-phase12")
        self.assertIn("meta_strategy.model.rollback_applied", model_rollback["reasonCodes"])
        self.assertTrue(rebuild.rebuilt_from_ledger)
        self.assertGreaterEqual(self.jobs.operational_metrics(now=NOW + timedelta(seconds=2))["leaseRecoveryCount"], 1)

    def test_final_acceptance_is_computed_from_evidence_not_static_flags(self) -> None:
        self.jobs.record_worker_heartbeat(worker_id="decision-worker", queue_name="finalised_bar_decisions", now=NOW)
        missing = build_meta_strategy_evidence_acceptance_report(
            build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        )
        self.assertFalse(missing["complete"])
        self.assertGreater(missing["counts"]["FAILED"], 0)

        for test_id in (*META_STRATEGY_RECOVERY_TEST_IDS, *META_STRATEGY_FINAL_DOD_IDS):
            record_meta_strategy_test_evidence(job_repository=self.jobs, test_id=test_id, passed=True, command="pytest evidence", evidence=f"record:{test_id}", now=NOW)

        ready = build_meta_strategy_evidence_acceptance_report(
            build_meta_strategy_observability_snapshot(job_repository=self.jobs, inventory_repository=self.inventory, settings_store=self.settings, now=NOW)
        )
        self.assertTrue(ready["complete"])
        self.assertTrue(ready["paperReady"])
        self.assertEqual(ready["paperStatus"], "READY")
        self.assertFalse(ready["liveExecutionEnabled"])

    def test_service_exposes_readiness_controls_and_evidence_endpoints(self) -> None:
        service = MetaStrategyApplicationService(settings_store=self.settings, job_repository=self.jobs, repository=self.inventory)

        control = service.apply_control("stop_execution_continue_decisions", {"actor": "ops", "reason": "test.stop_execution"})
        evidence = service.record_test_evidence({"testId": "api_restart", "passed": True, "command": "pytest", "evidence": "phase12"})
        readiness = service.readiness_report()

        self.assertEqual(control["status"], "OK")
        self.assertEqual(evidence["status"], "OK")
        self.assertEqual(readiness["algorithmId"], ALGORITHM_ID)
        self.assertEqual(readiness["payload"]["currentShadowPaperStatus"]["liveExecutionEnabled"], False)
        self.assertTrue(readiness["payload"]["apiProcessHealthyDoesNotImplyMetaStrategyReadiness"])
        self.assertEqual(readiness["payload"]["algorithmSpecificReadiness"]["algorithmId"], ALGORITHM_ID)


if __name__ == "__main__":
    unittest.main()
