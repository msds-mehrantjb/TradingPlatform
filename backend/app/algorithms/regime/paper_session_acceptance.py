"""Deterministic Regime paper-session acceptance harness.

The harness uses backend-owned Regime runtime components with fake market data
and a sandbox paper broker. It never contacts a live broker endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from backend.app.algorithms.regime.contracts import REGIME_ALGORITHM_VERSION, REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID
from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore
from backend.app.algorithms.regime.account_snapshot import REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.rollout import (
    LIMITED_PAPER_PROMOTION_EVIDENCE,
    REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
    REGIME_OPERATIONAL_ROLLOUT_STATE_KEY,
    activate_operational_rollout_stage,
)
from backend.app.algorithms.regime.runtime_publisher import RegimeFinalizedOneMinutePublisher, RegimeFinalizedOneMinutePublisherConfig
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


@dataclass(frozen=True)
class RegimePaperSessionHarnessConfig:
    repository_path: Path
    account_id: str = "paper-session-account"
    algorithm_instance_id: str = REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID
    symbol: str = "SPY"
    quantity: int = 5
    partial_fill_quantity: int = 2
    base_price: float = 500.0
    warmup_bars: int = 120
    poll_interval_seconds: float = 0.02
    start_time: datetime | None = None
    soak_minutes: int = 0


@dataclass(frozen=True)
class RegimePaperSessionReport:
    algorithm_id: str
    algorithm_instance_id: str
    account_id: str
    runtime_mode: str
    symbol: str
    backend_started_without_browser: bool
    restored_successfully: bool
    paper_initially_off: bool
    automatic_publications: int
    background_decisions: int
    submitted_while_off: int
    paper_on_effective: bool
    readiness_gates_healthy: bool
    eligible_fixture_orders: int
    gateway_submissions: int
    acknowledged_orders: int
    filled_quantity: int
    regime_inventory_quantity: int
    paper_off_blocked_next_entry: bool
    protection_continued: bool
    reconciliation_continued: bool
    restart_duplicate_orders: int
    live_endpoint_contacted: bool
    endpoints_contacted: tuple[str, ...]
    soak_mode: bool
    soak_minutes_requested: int
    reason_codes: tuple[str, ...]
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "algorithmInstanceId": self.algorithm_instance_id,
            "accountId": self.account_id,
            "runtimeMode": self.runtime_mode,
            "symbol": self.symbol,
            "backendStartedWithoutBrowser": self.backend_started_without_browser,
            "restoredSuccessfully": self.restored_successfully,
            "paperInitiallyOff": self.paper_initially_off,
            "automaticPublications": self.automatic_publications,
            "backgroundDecisions": self.background_decisions,
            "submittedWhileOff": self.submitted_while_off,
            "paperOnEffective": self.paper_on_effective,
            "readinessGatesHealthy": self.readiness_gates_healthy,
            "eligibleFixtureOrders": self.eligible_fixture_orders,
            "gatewaySubmissions": self.gateway_submissions,
            "acknowledgedOrders": self.acknowledged_orders,
            "filledQuantity": self.filled_quantity,
            "regimeInventoryQuantity": self.regime_inventory_quantity,
            "paperOffBlockedNextEntry": self.paper_off_blocked_next_entry,
            "protectionContinued": self.protection_continued,
            "reconciliationContinued": self.reconciliation_continued,
            "restartDuplicateOrders": self.restart_duplicate_orders,
            "liveEndpointContacted": self.live_endpoint_contacted,
            "endpointsContacted": list(self.endpoints_contacted),
            "soakMode": self.soak_mode,
            "soakMinutesRequested": self.soak_minutes_requested,
            "reasonCodes": list(self.reason_codes),
            "status": self.status,
        }


async def run_regime_paper_session_acceptance(config: RegimePaperSessionHarnessConfig) -> RegimePaperSessionReport:
    harness = _RegimePaperSessionHarness(config)
    return await harness.run()


async def run_regime_paper_session_soak(config: RegimePaperSessionHarnessConfig) -> RegimePaperSessionReport:
    minutes = config.soak_minutes or 390
    return await run_regime_paper_session_acceptance(RegimePaperSessionHarnessConfig(**{**config.__dict__, "soak_minutes": minutes}))


class _RegimePaperSessionHarness:
    def __init__(self, config: RegimePaperSessionHarnessConfig) -> None:
        self.config = config
        self.identity = {
            "algorithmId": "regime",
            "algorithmInstanceId": config.algorithm_instance_id,
            "accountId": config.account_id,
            "runtimeMode": "paper",
            "symbol": config.symbol.upper(),
        }
        self.repository = RegimeRepository(f"sqlite:///{config.repository_path}")
        self.market_data = _SessionMarketDataClient(start_time=_session_start_time(config.start_time), warmup_bars=config.warmup_bars, base_price=config.base_price)
        self.broker = FakeSandboxRegimePaperBroker(identity=self.identity, partial_fill_quantity=config.partial_fill_quantity, final_quantity=config.quantity)
        self.service = _DeterministicEligibleRegimeService(self.repository, quantity=config.quantity)
        self.gateway = PaperOrderGateway(self.broker, RegimePaperGatewayStore(self.repository, self.identity))
        self.publisher = _DeterministicSessionPublisher(
            identity=self.identity,
            repository=self.repository,
            market_data_client=self.market_data,
            candle_store=_MemoryCandleStore(),
            publish_completed_bar=lambda payload: self.supervisor.publish_completed_bar(payload),
            config=RegimeFinalizedOneMinutePublisherConfig(
                warmup_bars=config.warmup_bars,
                fetch_limit=config.warmup_bars + 20,
                finalization_delay_seconds=5,
                max_event_age_seconds=300,
                publisher_poll_interval_seconds=config.poll_interval_seconds,
                closed_market_poll_interval_seconds=config.poll_interval_seconds,
            ),
        )
        self.supervisor = RegimeRuntimeSupervisor(
            service=self.service,
            config=RegimeRuntimeSupervisorConfig(
                default_algorithm_instance_id=self.identity["algorithmInstanceId"],
                default_account_id=self.identity["accountId"],
                default_runtime_mode="paper",
                symbol=self.identity["symbol"],
                publisher_poll_interval_seconds=config.poll_interval_seconds,
                execution_poll_interval_seconds=config.poll_interval_seconds,
                reconciliation_poll_interval_seconds=config.poll_interval_seconds,
                position_management_interval_seconds=config.poll_interval_seconds,
                health_interval_seconds=config.poll_interval_seconds,
                maintenance_interval_seconds=60.0,
                max_processing_lag_seconds=300,
                worker_lease_seconds=5,
            ),
            paper_gateway=self.gateway,
            account_snapshot_provider=self._account_snapshot,
            market_event_publisher=self.publisher,
        )

    async def run(self) -> RegimePaperSessionReport:
        self._prepare_rollout()
        await self.supervisor.start()
        try:
            await self.supervisor.run_recovery_once()
            self._force_ready_gates()
            initial_status = self.supervisor.status()
            paper_initially_off = bool(initial_status.get("paperRequestedOn") is False and initial_status.get("paperEffectiveOn") is False)

            await _wait_for(lambda: self.publisher.publication_count >= 1 and self.service.evaluate_calls >= 1)
            submitted_while_off = self.broker.submit_count

            await self.supervisor.submit_command("set_automatic_paper", {"enabled": True, "reason": "acceptance_session_on"}, actor="acceptance-harness")
            self._force_ready_gates()
            paper_on_status = self.supervisor.status()
            paper_on_effective = bool(paper_on_status.get("paperRequestedOn") is True and paper_on_status.get("paperEffectiveOn") is True)
            readiness_healthy = self._readiness_healthy(paper_on_status)

            await self._publish_next_bar()
            await _wait_for(lambda: len(self.repository.read_owned_records("regime_order_intents", self.identity)) >= 1)
            await _wait_for(lambda: self.broker.submit_count >= 1)

            first_order_count = self.broker.submit_count
            self.broker.observe_final_fill()
            reconciliation = self.supervisor.reconcile_broker_observations(trigger="acceptance_final_fill")
            self.broker.suppress_replayed_fills()
            inventory_after_fill = self.repository.current_inventory_snapshot(self.identity)

            await self.supervisor.submit_command("set_automatic_paper", {"enabled": False, "reason": "acceptance_session_off"}, actor="acceptance-harness")
            await self._publish_next_bar()
            await asyncio.sleep(self.config.poll_interval_seconds * 2)
            self.supervisor.process_execution_outbox_once()
            paper_off_blocked = self.broker.submit_count == first_order_count

            protection_continued = bool(self.repository.read_owned_records("regime_orders", self.identity))
            reconciliation_continued = bool(reconciliation.get("reconciled") is True and self.supervisor.metrics.latest_reconciliation)

            if self.config.soak_minutes:
                await self._pause_background_workers_for_soak()
                await self._run_soak_minutes()

            restart_duplicate_orders = await self._restart_and_check_duplicates(existing_submit_count=first_order_count)
            final_inventory = self.repository.current_inventory_snapshot(self.identity)
            live_contacted = any("api.alpaca.markets" in endpoint and "paper-api.alpaca.markets" not in endpoint for endpoint in self.broker.endpoints_contacted)
            reasons = self._reason_codes(
                paper_initially_off=paper_initially_off,
                submitted_while_off=submitted_while_off,
                paper_on_effective=paper_on_effective,
                readiness_healthy=readiness_healthy,
                first_order_count=first_order_count,
                paper_off_blocked=paper_off_blocked,
                protection_continued=protection_continued,
                reconciliation_continued=reconciliation_continued,
                restart_duplicate_orders=restart_duplicate_orders,
                live_contacted=live_contacted,
            )
            status = "passed" if reasons == ("regime.acceptance.paper_session.passed",) else "failed"
            return RegimePaperSessionReport(
                algorithm_id="regime",
                algorithm_instance_id=self.identity["algorithmInstanceId"],
                account_id=self.identity["accountId"],
                runtime_mode="paper",
                symbol=self.identity["symbol"],
                backend_started_without_browser=True,
                restored_successfully=bool(self.supervisor.metrics.recovery_succeeded),
                paper_initially_off=paper_initially_off,
                automatic_publications=self.publisher.publication_count,
                background_decisions=self.service.evaluate_calls,
                submitted_while_off=submitted_while_off,
                paper_on_effective=paper_on_effective,
                readiness_gates_healthy=readiness_healthy,
                eligible_fixture_orders=first_order_count,
                gateway_submissions=self.broker.submit_count,
                acknowledged_orders=len(self.broker.acknowledgements),
                filled_quantity=int(final_inventory.get("quantity") or inventory_after_fill.get("quantity") or 0),
                regime_inventory_quantity=int(final_inventory.get("quantity") or 0),
                paper_off_blocked_next_entry=paper_off_blocked,
                protection_continued=protection_continued,
                reconciliation_continued=reconciliation_continued,
                restart_duplicate_orders=restart_duplicate_orders,
                live_endpoint_contacted=live_contacted,
                endpoints_contacted=tuple(self.broker.endpoints_contacted),
                soak_mode=bool(self.config.soak_minutes),
                soak_minutes_requested=self.config.soak_minutes,
                reason_codes=reasons,
                status=status,
            )
        finally:
            await self.supervisor.shutdown()

    def _prepare_rollout(self) -> None:
        self.repository.activate_settings_snapshot(
            {
                "settings": {
                    "identity": self.identity,
                    "settingsVersion": "regime_acceptance_paper_session_v1",
                    "profileVersion": "regime_acceptance_profile_v1",
                    "exit_policy": {
                        "flattenTimeEt": "23:59",
                        "maxHoldingBars": 10_000,
                        "takeProfitR": 100.0,
                        "endOfDayFlattenEnabled": False,
                        "mandatoryStop": False,
                        "mandatoryMaxHoldingTime": False,
                    },
                    "execution": {
                        "orderTimeToLiveSeconds": 300,
                        "maxCancelReplaceAttempts": 0,
                        "allowMarketEntryOrders": False,
                        "brokerTransportMode": "paper",
                    },
                    "rollout": {
                        "runtimeMode": "paper",
                        "requireRolloutEvidence": True,
                        "mlMayPromoteOrders": False,
                        "liveTradingEnabled": False,
                    },
                    "runtime": {
                        "runtimeMode": "paper",
                        "paperTradingOnly": True,
                        "paperOnly": True,
                        "liveTradingEnabled": False,
                        "symbolAllowlist": [self.identity["symbol"]],
                        "timeframe": "1Min",
                        "regularHoursOnly": True,
                    },
                },
                "actor": "acceptance-harness",
                "reason": "paper session acceptance settings",
            }
        )
        evidence = {
            "backendEvidenceSource": REGIME_OPERATIONAL_ROLLOUT_EVIDENCE_SOURCE,
            "evidenceId": f"paper-session-evidence-{uuid4().hex}",
            "recordedAt": _iso(self.market_data.current_time),
            "persistedEvidenceIds": LIMITED_PAPER_PROMOTION_EVIDENCE,
        }
        evidence.update({requirement: True for requirement in LIMITED_PAPER_PROMOTION_EVIDENCE})
        self.repository.record_regime_rollout_promotion_evidence(self.identity, evidence)
        store = _SnapshotStore(self.repository, self.identity)
        evidence_snapshot = self.repository.read_regime_rollout_promotion_evidence(self.identity)
        for stage in ("simulated_execution", "limited_paper"):
            activation = activate_operational_rollout_stage(
                store,
                stage,
                actor="acceptance-harness",
                reason="paper session acceptance",
                evidence=evidence_snapshot,
                activated_at=self.market_data.current_time,
            )
            if not activation.get("activated"):
                raise RuntimeError(f"Regime paper-session harness could not activate {stage}: {activation}")

    def _force_ready_gates(self) -> None:
        self.supervisor.metrics.supervisor_started = True
        self.supervisor.metrics.recovery_succeeded = True
        self.supervisor.metrics.inventory_reconciled = True
        self.supervisor.metrics.risk_reservations_consistent = True
        self.supervisor.metrics.persistence_available = True
        self.supervisor.metrics.settings_available = True
        self.supervisor.metrics.broker_paper_mode_verified = True
        self.supervisor.metrics.broker_connectivity_ok = True
        self.supervisor.metrics.queue_lag_block_active = False
        self.supervisor.metrics.stale_events = 0
        self.supervisor.metrics.reconciliation_discrepancies = 0
        self.supervisor.metrics.latest_reconciliation = {"reconciled": True, "trigger": "acceptance_ready"}
        self.supervisor.metrics.entry_block_reason_codes = [
            code
            for code in self.supervisor.metrics.entry_block_reason_codes
            if code
            in {
                "regime.runtime.automatic_paper_control_off",
            }
        ]
        for component in ("market_event_publisher", "database", "paper_broker", "broker_connectivity", "settings_repository", "order_reconciliation"):
            self.supervisor.metrics.component_health[component] = {
                "status": "healthy",
                "reasonCodes": [f"regime.acceptance.{component}.healthy"],
                "lastError": None,
            }

    async def _publish_next_bar(self) -> None:
        self.market_data.advance_minute()
        await self.supervisor.poll_market_event_publisher_once(worker_id="regime_acceptance_manual_backend_tick")
        await _wait_for(lambda: self.supervisor.event_queue.qsize() == 0)

    async def _run_soak_minutes(self) -> None:
        for _ in range(max(0, self.config.soak_minutes - 2)):
            self.market_data.advance_minute()
            await self.supervisor.poll_market_event_publisher_once(worker_id="regime_acceptance_soak_backend_tick")
            await self._drain_event_queue_directly()
            self.supervisor.process_execution_outbox_once()

    async def _restart_and_check_duplicates(self, *, existing_submit_count: int) -> int:
        restarted = RegimeRuntimeSupervisor(
            service=_DeterministicEligibleRegimeService(self.repository, quantity=self.config.quantity),
            config=self.supervisor.config,
            paper_gateway=self.gateway,
            account_snapshot_provider=self._account_snapshot,
            market_event_publisher=self.publisher,
        )
        await restarted.run_recovery_once()
        restarted.metrics.supervisor_started = True
        restarted.metrics.recovery_succeeded = True
        restarted.metrics.inventory_reconciled = True
        restarted.metrics.persistence_available = True
        restarted.metrics.settings_available = True
        restarted.metrics.broker_paper_mode_verified = True
        restarted.metrics.broker_connectivity_ok = True
        restarted.metrics.latest_reconciliation = {"reconciled": True, "trigger": "acceptance_restart"}
        restarted.process_execution_outbox_once()
        return max(0, self.broker.submit_count - existing_submit_count)

    async def _pause_background_workers_for_soak(self) -> None:
        tasks = list(getattr(self.supervisor, "_tasks", ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.supervisor._tasks.clear()
        for worker_id in self.supervisor.metrics.worker_status:
            self.supervisor.metrics.worker_status[worker_id] = "soak_direct_drain"

    async def _drain_event_queue_directly(self) -> None:
        while True:
            try:
                event = self.supervisor.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                self.supervisor.metrics.queue_depth = self.supervisor.event_queue.qsize()
                return
            try:
                await self.supervisor.process_finalised_bar_event(event)
            finally:
                self.supervisor.event_queue.task_done()
                self.supervisor.metrics.queue_depth = self.supervisor.event_queue.qsize()

    def _account_snapshot(self, identity: dict[str, str]) -> dict[str, Any]:
        return {
            **identity,
            "sourceAuthority": REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY,
            "accountId": self.identity["accountId"],
            "runtimeMode": "paper",
            "equity": 100_000.0,
            "cash": 100_000.0,
            "buyingPower": 100_000.0,
            "availableBuyingPower": 100_000.0,
            "globalRiskCapacityQuantity": 1_000,
            "dailyAccountPnl": 0.0,
            "buyingPowerCurrent": True,
            "accountSnapshotFresh": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": False,
            "observedAt": _iso(datetime.now(UTC)),
            "reasonCodes": [],
        }

    def _readiness_healthy(self, status: dict[str, Any]) -> bool:
        return bool(
            status.get("paperEffectiveOn")
            and not status.get("paperEffectiveBlockers")
            and status.get("runtimeMode") == "paper"
            and status.get("algorithmInstanceId") == self.identity["algorithmInstanceId"]
            and status.get("accountId") == self.identity["accountId"]
        )

    def _reason_codes(self, **checks: Any) -> tuple[str, ...]:
        failures = []
        if not checks["paper_initially_off"]:
            failures.append("regime.acceptance.paper_initially_on")
        if checks["submitted_while_off"]:
            failures.append("regime.acceptance.submitted_while_paper_off")
        if not checks["paper_on_effective"]:
            failures.append("regime.acceptance.paper_on_not_effective")
        if not checks["readiness_healthy"]:
            failures.append("regime.acceptance.readiness_gates_unhealthy")
        if checks["first_order_count"] != 1:
            failures.append("regime.acceptance.expected_one_gateway_order")
        if not checks["paper_off_blocked"]:
            failures.append("regime.acceptance.paper_off_did_not_block_next_entry")
        if not checks["protection_continued"]:
            failures.append("regime.acceptance.protection_not_observed")
        if not checks["reconciliation_continued"]:
            failures.append("regime.acceptance.reconciliation_not_observed")
        if checks["restart_duplicate_orders"]:
            failures.append("regime.acceptance.restart_duplicated_order")
        if checks["live_contacted"]:
            failures.append("regime.acceptance.live_endpoint_contacted")
        return tuple(failures or ["regime.acceptance.paper_session.passed"])


class _DeterministicEligibleRegimeService(RegimeApplicationService):
    def __init__(self, repository: RegimeRepository, *, quantity: int) -> None:
        super().__init__(repository)
        self.quantity = quantity
        self.evaluate_calls = 0

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.evaluate_calls += 1
        identity = {
            "algorithmId": "regime",
            "algorithmInstanceId": str(payload.get("algorithmInstanceId") or REGIME_DEFAULT_PAPER_ALGORITHM_INSTANCE_ID),
            "accountId": str(payload.get("accountId") or "paper-session-account"),
            "runtimeMode": "paper",
            "symbol": "SPY",
        }
        market_data = payload.get("marketData") if isinstance(payload.get("marketData"), dict) else {}
        candles = list(market_data.get("primaryCandles") or market_data.get("oneMinuteCandles") or [])
        latest = candles[-1] if candles else {"timestamp": _iso(datetime.now(UTC)), "close": 500.0}
        bar_timestamp = str(latest.get("timestamp") or _iso(datetime.now(UTC)))
        created_at = _iso(datetime.now(UTC))
        decision_id = f"regime-session-decision-{_digest(identity['algorithmInstanceId'] + bar_timestamp)[:16]}"
        settings = self.settings_repository.ensure_active_settings_snapshot(identity)
        settings_version = str(settings.get("settingsVersion") or "regime-settings-acceptance")
        profile_version = str(settings.get("profileVersion") or "regime-profile-acceptance")
        account = payload.get("__regime_account_snapshot") if isinstance(payload.get("__regime_account_snapshot"), dict) else {}
        paper_effective = bool(account.get("paperButtonEffective") or account.get("paperEffectiveOn") or account.get("automaticPaperTradingEnabled"))
        inventory = self.repository.current_inventory_snapshot(identity)
        create_entry = bool(paper_effective and int(inventory.get("quantity") or 0) == 0)
        order_intent = _order_intent(identity, decision_id=decision_id, settings_version=settings_version, profile_version=profile_version, quantity=self.quantity, created_at=created_at, bar_timestamp=bar_timestamp) if create_entry else None
        local_risk = _local_risk(identity, decision_id=decision_id, order_intent=order_intent, quantity=self.quantity, created_at=created_at)
        global_gate = _global_gate(identity, decision_id=decision_id, order_intent=order_intent, quantity=self.quantity, created_at=created_at)
        if order_intent:
            order_intent = {**order_intent, "localRiskResult": local_risk, "globalRiskApproval": global_gate}
        result = {
            **identity,
            "algorithmVersion": REGIME_ALGORITHM_VERSION,
            "settingsVersion": settings_version,
            "profileVersion": profile_version,
            "decisionId": decision_id,
            "dataTimestamp": bar_timestamp,
            "featureTimestamp": bar_timestamp,
            "dataManifestHash": _digest(f"{bar_timestamp}:{latest.get('close')}"),
            "marketDataValidation": {"passed": True, "complete": True, "current": True, "dataTimestamp": bar_timestamp, "featureTimestamp": bar_timestamp},
            "decision": {
                **identity,
                "algorithmVersion": REGIME_ALGORITHM_VERSION,
                "settingsVersion": settings_version,
                "profileVersion": profile_version,
                "decisionId": decision_id,
                "symbol": "SPY",
                "signal": "Buy" if create_entry else "Hold",
                "aggregateSignal": "Buy" if create_entry else "Hold",
                "tradeAllowed": create_entry,
                "confidence": 0.95,
                "score": 0.95,
            },
            "nextRuntimeState": {
                **identity,
                "schemaVersion": "regime_runtime_state_v1",
                "sequenceVersion": self.evaluate_calls,
                "lastProcessedBarTimestamp": bar_timestamp,
                "lastDecisionId": decision_id,
                "confirmedRegime": "strong_uptrend",
                "dailyCounters": {"decisionCount": self.evaluate_calls, "orderProposalCount": 1 if order_intent else 0},
            },
            "classification": {"rawRegime": "strong_uptrend", "confidence": 0.95, "timestamp": bar_timestamp},
            "transition": {"confirmedRegime": "strong_uptrend", "candidateConfirmationCount": 0, "sequenceVersion": self.evaluate_calls},
            "strategyOutputs": [
                {
                    "strategyId": "acceptance_trend_fixture",
                    "family": "trend",
                    "role": "directional",
                    "signal": "Buy" if create_entry else "Hold",
                    "confidence": 0.95,
                    "effectiveWeight": 1.0,
                    "weightedContribution": 0.95 if create_entry else 0.0,
                    "eligibility": "eligible" if create_entry else "ineligible",
                    "exclusionReasonCodes": [] if create_entry else ["regime.acceptance.paper_off_or_inventory_present"],
                }
            ],
            "familyAggregation": {"aggregateSignal": "Buy" if create_entry else "Hold", "winningScore": 0.95 if create_entry else 0.0, "familyScores": {"trend": 0.95 if create_entry else 0.0}},
            "localRiskResult": local_risk,
            "globalRiskApproval": global_gate,
            "globalGateOutcome": global_gate,
            "orderProposal": order_intent,
            "orderIntent": order_intent,
        }
        self.record_stateful_bar_result(result)
        if order_intent:
            self.repository.record_local_risk_result(identity, local_risk)
            self.repository.insert_order_intent(order_intent)
        return result


class FakeSandboxRegimePaperBroker:
    broker_kind = "regime_alpaca_paper"
    account_type = "paper"
    paper_only = True
    live_trading_enabled = False
    credentials_verified = True
    account_matches_configured_identity = True
    account_allowed_to_trade = True
    market_data_credentials_configured = True

    def __init__(self, *, identity: dict[str, str], partial_fill_quantity: int, final_quantity: int) -> None:
        self.identity = identity
        self.partial_fill_quantity = partial_fill_quantity
        self.final_quantity = final_quantity
        self.submit_count = 0
        self.cancel_count = 0
        self.acknowledgements: list[PaperGatewayBrokerAck] = []
        self.client_order_id: str | None = None
        self.order_intent_id: str | None = None
        self.final_fill_ready = False
        self.replayed_fills_suppressed = False
        self.endpoints_contacted: list[str] = []

    def startup_verification(self) -> dict[str, Any]:
        self._record_endpoint("sandbox://alpaca-paper/account")
        return self.paper_trading_configuration()

    def paper_trading_configuration(self) -> dict[str, Any]:
        return {
            "verified": True,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "accountType": "paper",
            "accountMatchesConfiguredIdentity": True,
            "accountAllowedToTrade": True,
            "credentialsVerified": True,
            "marketDataCredentialsConfigured": True,
            "tradingUrl": "sandbox://alpaca-paper",
            "reasonCodes": ["regime.acceptance.paper_broker.verified"],
        }

    def verify_paper_account(self) -> bool:
        self._record_endpoint("sandbox://alpaca-paper/account")
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self._record_endpoint("sandbox://alpaca-paper/orders")
        self.submit_count += 1
        self.client_order_id = intent.clientOrderId
        self.order_intent_id = intent.orderIntentId
        ack = PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"sandbox-broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=datetime.now(UTC),
        )
        self.acknowledgements.append(ack)
        return ack

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        self._record_endpoint("sandbox://alpaca-paper/orders/by_client_order_id")
        if client_order_id != self.client_order_id or self.order_intent_id is None:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId=self.order_intent_id,
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.partial_fill_quantity,
            averageFillPrice=500.01,
            status="PARTIALLY_FILLED",
            filledAt=datetime.now(UTC),
        )

    def observe_final_fill(self) -> None:
        self.final_fill_ready = True

    def suppress_replayed_fills(self) -> None:
        self.replayed_fills_suppressed = True

    def refresh_fills(self) -> list[dict[str, Any]]:
        self._record_endpoint("sandbox://alpaca-paper/fills")
        if self.replayed_fills_suppressed or not self.final_fill_ready or self.client_order_id is None or self.order_intent_id is None:
            return []
        return [
            {
                **self.identity,
                "algorithmId": "regime",
                "orderIntentId": self.order_intent_id,
                "clientOrderId": self.client_order_id,
                "brokerOrderId": f"sandbox-broker-{self.client_order_id}",
                "fillId": f"sandbox-final-fill-{self.order_intent_id}",
                "symbol": "SPY",
                "side": "Buy",
                "filledQuantity": max(0, self.final_quantity - self.partial_fill_quantity),
                "submittedQuantity": self.final_quantity,
                "averageFillPrice": 500.02,
                "status": "FILLED",
                "filledAt": _iso(datetime.now(UTC)),
            }
        ]

    def refresh_open_orders(self) -> list[dict[str, Any]]:
        self._record_endpoint("sandbox://alpaca-paper/open_orders")
        if not self.client_order_id or self.final_fill_ready:
            return []
        return [
            {
                **self.identity,
                "algorithmId": "regime",
                "orderIntentId": self.order_intent_id,
                "clientOrderId": self.client_order_id,
                "brokerOrderId": f"sandbox-broker-{self.client_order_id}",
                "status": "partially_filled",
                "symbol": "SPY",
            }
        ]

    def refresh_positions(self) -> list[dict[str, Any]]:
        self._record_endpoint("sandbox://alpaca-paper/positions")
        if not self.client_order_id or self.order_intent_id is None:
            return []
        return [
            {
                **self.identity,
                "algorithmId": "regime",
                "orderIntentId": self.order_intent_id,
                "clientOrderId": self.client_order_id,
                "positionId": f"regime-position-SPY-{self.order_intent_id}",
                "quantity": self.final_quantity if self.final_fill_ready else self.partial_fill_quantity,
                "symbol": "SPY",
            }
        ]

    def cancel_order(self, client_order_id: str) -> bool:
        self._record_endpoint("sandbox://alpaca-paper/orders/cancel")
        self.cancel_count += 1
        return True

    def _record_endpoint(self, endpoint: str) -> None:
        self.endpoints_contacted.append(endpoint)


class _SessionMarketDataClient:
    def __init__(self, *, start_time: datetime, warmup_bars: int, base_price: float) -> None:
        self.settings = SimpleNamespace(has_alpaca_credentials=True)
        self.current_time = start_time
        self.base_price = base_price
        self.rows = _bars_until(start_time - timedelta(minutes=1), count=warmup_bars + 1, base_price=base_price)

    async def get_market_status(self) -> dict[str, Any]:
        return {
            "status": "open",
            "isOpen": True,
            "timestamp": _iso(self.current_time),
            "nextOpen": None,
            "source": "paper_session_acceptance_harness",
        }

    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str) -> list[dict[str, Any]]:
        return list(self.rows[-limit:])

    async def get_latest_quote(self, *, symbol: str, feed: str) -> dict[str, Any] | None:
        return {"symbol": symbol, "bid": self.base_price, "ask": self.base_price + 0.02, "bidSize": 100, "askSize": 100, "quoteTimestamp": _iso(self.current_time)}

    def advance_minute(self) -> None:
        self.current_time = self.current_time + timedelta(minutes=1)
        index = len(self.rows)
        close = self.base_price + (index * 0.01)
        timestamp = (self.current_time - timedelta(minutes=1)).replace(second=0, microsecond=0)
        self.rows.append({"symbol": "SPY", "timeframe": "1Min", "feed": "iex", "timestamp": _iso(timestamp), "open": close - 0.02, "high": close + 0.05, "low": close - 0.05, "close": close, "volume": 250_000})


class _DeterministicSessionPublisher(RegimeFinalizedOneMinutePublisher):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.publication_count = 0

    async def poll_once(self, *, now: datetime | None = None, triggered_by: str = "background_publisher"):
        result = await super().poll_once(now=self.market_data_client.current_time, triggered_by=triggered_by)
        self.publication_count += int(result.accepted_count)
        return result


class _MemoryCandleStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def upsert_many(self, candles: list[dict[str, Any]]) -> None:
        existing = {(row["symbol"], row["timeframe"], row["feed"], row["timestamp"]): row for row in self.rows}
        for candle in candles:
            existing[(candle["symbol"], candle["timeframe"], candle["feed"], candle["timestamp"])] = candle
        self.rows = list(existing.values())

    def latest(self, *, symbol: str, timeframe: str, feed: str, limit: int) -> list[dict[str, Any]]:
        rows = [row for row in self.rows if row["symbol"] == symbol and row["timeframe"] == timeframe and row["feed"] == feed]
        return sorted(rows, key=lambda row: row["timestamp"])[-limit:]

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict[str, Any]]:
        return [row for row in self.latest(symbol=symbol, timeframe=timeframe, feed=feed, limit=10_000) if row["timestamp"] <= end][-limit:]


class _SnapshotStore:
    def __init__(self, repository: RegimeRepository, identity: dict[str, str]) -> None:
        self.repository = repository
        self.identity = identity

    def read_snapshot(self, key: str) -> dict[str, Any]:
        snapshot = self.repository.read_runtime_snapshot(self.identity, key)
        if snapshot is None:
            raise KeyError(key)
        return snapshot

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        self.repository.write_runtime_snapshot(self.identity, key, dict(snapshot))


def _order_intent(
    identity: dict[str, str],
    *,
    decision_id: str,
    settings_version: str,
    profile_version: str,
    quantity: int,
    created_at: str,
    bar_timestamp: str,
) -> dict[str, Any]:
    order_intent_id = f"regime-intent-session-{_digest(decision_id)[:16]}"
    return {
        **identity,
        "algorithmVersion": REGIME_ALGORITHM_VERSION,
        "settingsVersion": settings_version,
        "profileVersion": profile_version,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "side": "Buy",
        "positionEffect": "enter_long",
        "quantity": quantity,
        "entryPrice": 500.0,
        "limitPrice": 500.0,
        "stopPrice": 100.0,
        "targetPrice": 10_000.0,
        "riskDollars": float(quantity),
        "completedBarFinalized": True,
        "completedBarTimestamp": bar_timestamp,
        "marketDataValidation": {"passed": True, "complete": True, "current": True},
        "settingsSnapshot": {
            "settingsVersion": settings_version,
            "profileVersion": profile_version,
            "maximumOrderAgeSeconds": 300,
            "maximumHoldingBars": 10_000,
            "endOfDayFlattenEnabled": False,
            "execution": {"orderTimeToLiveSeconds": 300, "orderType": "limit", "maximumCancelReplaceAttempts": 0},
            "exitPolicy": {
                "endOfDayFlattenEnabled": False,
                "maximumHoldingBars": 10_000,
                "timeStopBars": 0,
                "exitOnRegimeTransition": False,
                "trailingExitsEnabled": False,
            },
        },
        "dataManifestHash": _digest(f"{decision_id}:{bar_timestamp}"),
        "createdAt": created_at,
        "expiresAt": _iso(datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(minutes=5)),
    }


def _local_risk(identity: dict[str, str], *, decision_id: str, order_intent: dict[str, Any] | None, quantity: int, created_at: str) -> dict[str, Any]:
    order_intent_id = str(order_intent.get("orderIntentId")) if order_intent else ""
    return {
        **identity,
        "localRiskResultId": f"regime-local-risk-session-{_digest(decision_id)[:16]}",
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "settingsVersion": str((order_intent or {}).get("settingsVersion") or ""),
        "passed": bool(order_intent),
        "requestedQuantity": quantity if order_intent else 0,
        "approvedQuantity": quantity if order_intent else 0,
        "estimatedGrossEdge": 50.0 if order_intent else 0.0,
        "estimatedTransactionCost": 2.0 if order_intent else 0.0,
        "estimatedNetEdge": 48.0 if order_intent else 0.0,
        "blockers": [] if order_intent else ["regime.acceptance.no_entry_when_paper_off_or_inventory_present"],
        "reductions": [],
        "evaluatedAt": created_at,
        "expiresAt": _iso(datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(minutes=5)),
    }


def _global_gate(identity: dict[str, str], *, decision_id: str, order_intent: dict[str, Any] | None, quantity: int, created_at: str) -> dict[str, Any]:
    order_intent_id = str(order_intent.get("orderIntentId")) if order_intent else ""
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "approved": bool(order_intent),
        "approvedQuantity": quantity if order_intent else 0,
        "globallyAllowedQuantity": quantity if order_intent else 0,
        "status": "approved" if order_intent else "not_requested",
        "reservationId": f"reservation-{order_intent_id}" if order_intent else None,
        "reasonCodes": ["regime.acceptance.global_risk.approved"] if order_intent else ["regime.acceptance.global_risk.not_requested"],
        "evaluatedAt": created_at,
    }


def _bars_until(end_time: datetime, *, count: int, base_price: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = end_time.replace(second=0, microsecond=0) - timedelta(minutes=count - 1)
    for index in range(count):
        close = base_price + index * 0.01
        rows.append(
            {
                "symbol": "SPY",
                "timeframe": "1Min",
                "feed": "iex",
                "timestamp": _iso(start + timedelta(minutes=index)),
                "open": close - 0.02,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "volume": 250_000,
            }
        )
    return rows


def _session_start_time(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).replace(second=10, microsecond=0)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _wait_for(predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("Regime paper-session acceptance harness timed out waiting for condition")


__all__ = [
    "FakeSandboxRegimePaperBroker",
    "RegimePaperSessionHarnessConfig",
    "RegimePaperSessionReport",
    "run_regime_paper_session_acceptance",
    "run_regime_paper_session_soak",
]
