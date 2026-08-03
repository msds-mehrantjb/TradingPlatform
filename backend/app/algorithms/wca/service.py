"""WCA service boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from backend.app.algorithms.wca import WCA_PACKAGE_VERSION
from backend.app.algorithms.wca.configuration import (
    WCA_CONFIGURATION_VERSION,
    WcaConfiguration,
    WcaConfigurationLifecycle,
    WcaConfigurationUnavailable,
    canonical_configuration_from_legacy,
    default_wca_configuration,
)
from backend.app.algorithms.wca.backtest.engine import run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    BacktestResult,
    WcaBacktestRequest,
    WcaBacktestSuiteResult,
    WcaBrokerReconciliationResult,
    WcaCandle,
    WcaEvaluateRequest,
    WcaEvaluateResponse,
    WcaLegacyHardFilter,
    WcaLegacySizingResult,
    WcaLegacyStrategySignal,
    WcaMarketSnapshot,
    WcaOrderStatus,
    WcaPaperExecutionRequest,
    WcaPaperExecutionResult,
    WcaPaperStabilityValidationRequest,
    WcaPaperStabilityValidationResult,
    WcaQuote,
    WcaRuntimeMode,
    WcaShadowComparisonEvidence,
)
from backend.app.algorithms.wca.engine import WCA_ENGINE_VERSION, WcaEngineInputError, base_weight_map
from backend.app.algorithms.wca.execution_pipeline import WCA_EXECUTION_PIPELINE_VERSION, WCA_PRODUCTION_PIPELINE_VERSION, WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition
from backend.app.algorithms.wca.final_acceptance import build_wca_final_acceptance_report
from backend.app.algorithms.wca.broker_reconciliation import WcaPaperBrokerReconciliationClient, reconcile_wca_broker
from backend.app.algorithms.wca.order_validation import WcaOrderValidationContext, apply_wca_final_order_validation, drop_wca_order
from backend.app.algorithms.wca.repository import WcaRepository, WcaSqliteRepository
from backend.app.algorithms.wca.research_jobs import WcaResearchJobReceipt, WcaResearchJobType, research_job
from backend.app.algorithms.wca.research_repository import WcaResearchRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import _load_persisted_rollout_evidence, get_wca_runtime_supervisor
from backend.app.algorithms.wca.session_validation import validate_wca_entry_session
from backend.app.algorithms.wca.rollout import wca_rollout_status
from backend.app.algorithms.wca.paper_account import validate_wca_automatic_paper_account
from backend.app.algorithms.wca.paper_stability import validate_wca_paper_stability
from backend.app.algorithms.wca.paper_broker import build_wca_paper_broker_request
from backend.app.algorithms.wca.shadow_comparison import WcaShadowComparisonTolerance, run_wca_shadow_comparison
from backend.app.algorithms.wca.sizing import WcaManualSizingOverride
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.strategy_registry import WCA_HARD_FILTER_REGISTRY, WCA_MODIFIER_REGISTRY
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.app.execution.idempotency import idempotency_key


class WcaService:
    version = WCA_PACKAGE_VERSION
    configuration_version = WCA_CONFIGURATION_VERSION

    def __init__(self, repository: WcaRepository | None = None, research_repository: WcaResearchRepository | None = None) -> None:
        self._backtest_results: dict[str, BacktestResult] = {}
        self._backtest_suites: dict[str, WcaBacktestSuiteResult] = {}
        self._repository = repository or WcaSqliteRepository()
        self._research_repository = research_repository or WcaResearchRepository(self._repository if isinstance(self._repository, WcaSqliteRepository) else None)
        self._runtime_repository = WcaRuntimeRepository(self._repository if isinstance(self._repository, WcaSqliteRepository) else None)
        self._configuration_error: str | None = None
        seed = default_wca_configuration()
        self._repository.initialize_defaults(
            symbol="SPY",
            configuration=seed.model_dump(mode="json"),
            weight_snapshot=baseline_weight_snapshot(weight_version=f"{seed.configuration_version}.baseline_weights"),
            engine_version=WCA_ENGINE_VERSION,
        )
        self._load_active_configuration()

    def _load_active_configuration(self) -> WcaConfiguration | None:
        if not hasattr(self._repository, "read_active_configuration"):
            return None
        active = self._repository.read_active_configuration()
        if active is None:
            self._configuration_error = "wca.configuration.missing_active_revision"
            return None
        self._configuration_error = None
        return active

    def _require_active_configuration(self) -> WcaConfiguration:
        active = self._load_active_configuration()
        if active is None:
            raise WcaConfigurationUnavailable("wca.configuration.missing_active_revision: new WCA entries are blocked")
        return active

    def _read_active_weights_as_of(self, as_of: datetime):
        if not hasattr(self._repository, "read_active_weights"):
            return None
        try:
            return self._repository.read_active_weights(as_of=as_of)
        except TypeError:
            return self._repository.read_active_weights()

    def _read_active_calibrations_as_of(self, *, symbol: str, as_of: datetime):
        if not hasattr(self._repository, "read_active_confidence_calibrations"):
            return ()
        try:
            return self._repository.read_active_confidence_calibrations(symbol=symbol, as_of=as_of)
        except TypeError:
            return self._repository.read_active_confidence_calibrations()

    def status(self) -> dict[str, Any]:
        persistence = self._repository.table_counts()
        active = self._load_active_configuration()
        now = datetime.now(timezone.utc)
        runtime_health = self._runtime_repository.read_latest_runtime_health()
        queue_depths = self._runtime_repository.queue_depths()
        queue_ages = self._runtime_repository.queue_ages(now=now)
        weights = self._read_active_weights_as_of(now)
        calibrations = self._read_active_calibrations_as_of(symbol="SPY", as_of=now)
        latest_decision = self.latest_decisions(limit=1)
        latest_payload = latest_decision[0] if latest_decision else {}
        runtime_supervisor = get_wca_runtime_supervisor()
        runtime_control = runtime_supervisor.runtime_control()
        account_id = str(runtime_control.get("brokerAccountId") or runtime_control.get("broker_account_id") or "paper")
        symbol = str(runtime_control.get("symbol") or "SPY").upper()
        position = self.current_wca_position(account_id=account_id, symbol=symbol)
        latest_order_intent = self.latest_order_intent(account_id=account_id)
        latest_outbox = self.latest_order()
        latest_broker_order = self.latest_broker_order(account_id=account_id, symbol=symbol)
        latest_fill = self.latest_fill()
        latest_reconciliation = self.reconciliation_status(account_id=account_id, symbol=symbol)
        latest_end_of_session = self.latest_end_of_session_result(account_id=account_id, symbol=symbol)
        latest_global_risk = self.latest_global_risk_status(account_id=account_id, symbol=symbol)
        broker_validation = validate_wca_automatic_paper_account(account_id=account_id)
        session = validate_wca_entry_session(
            timestamp=now,
            entry_cutoff_minutes=active.execution.entry_cutoff_minutes if active is not None else 15 * 60 + 30,
            require_broker_clock=False,
        )
        rollout = wca_rollout_status(evidence=_load_persisted_rollout_evidence(self._repository) if isinstance(self._repository, WcaSqliteRepository) else None)
        rollout_stage = str(runtime_control.get("rolloutStage") or rollout.get("current_stage") or "DISABLED")
        rollout_blockers = _rollout_blockers(rollout, rollout_stage)
        active_circuit_breakers = _active_circuit_breakers(
            runtime_control=runtime_control,
            runtime_health=runtime_health.model_dump(mode="json") if runtime_health else {},
            position=position,
            daily_state=self._daily_state(account_id=account_id, symbol=symbol, now=now),
        )
        authoritative_state_hash = _authoritative_status_hash(
            {
                "runtime_control_revision": runtime_control.get("controlRevision"),
                "runtime_control_hash": runtime_control.get("controlHash"),
                "position": position,
                "latest_reconciliation": latest_reconciliation,
                "queue_depths": queue_depths,
                "queue_ages": queue_ages,
                "latest_decision_id": self._runtime_repository.last_decision_id(),
            }
        )
        paper_ready = bool(runtime_control.get("effectiveAutomaticEntriesEnabled"))
        paper_requested = bool(runtime_control.get("paperTradingRequested") or runtime_control.get("automaticEntriesRequested"))
        heartbeat_at = runtime_health.heartbeat_at if runtime_health else None
        heartbeat_age_seconds = (now - heartbeat_at).total_seconds() if heartbeat_at is not None else None
        heartbeat_fresh = heartbeat_age_seconds is not None and heartbeat_age_seconds <= 90
        runtime_process_status = "running" if heartbeat_fresh else "stale" if runtime_health is not None else "not_started"
        active_entry_block_reasons = _active_entry_block_reasons(
            runtime_control=runtime_control,
            runtime_health=runtime_health.model_dump(mode="json") if runtime_health else {},
            session_reason_codes=session.reason_codes,
            broker_reason_codes=broker_validation.reason_codes,
            rollout_blockers=rollout_blockers,
            reconciliation=latest_reconciliation,
            circuit_breakers=active_circuit_breakers,
            paper_requested=paper_requested,
            paper_ready=paper_ready,
            runtime_process_status=runtime_process_status,
        )
        readiness_state = _wca_readiness_state(
            paper_requested=paper_requested,
            paper_ready=paper_ready,
            rollout_stage=rollout_stage,
            runtime_process_status=runtime_process_status,
            runtime_health=runtime_health.model_dump(mode="json") if runtime_health else {},
            active_block_reasons=active_entry_block_reasons,
            active_circuit_breakers=active_circuit_breakers,
            open_quantity=int(position.get("openQuantity") or 0),
        )
        mode = (
            "automatic_paper_ready"
            if paper_ready
            else "paper_requested_blocked"
            if paper_requested
            else "paper_off"
        )
        status_reason_codes = tuple(
            dict.fromkeys(
                (
                    "wca.backend_v2.active",
                    "wca.api.transport_only",
                    "wca.paper_ready" if paper_ready else "wca.paper_blocked",
                    f"wca.readiness.{readiness_state.lower()}",
                    *(runtime_control.get("reasonCodes") or ()),
                    *active_entry_block_reasons,
                )
            )
        )
        payload = {
            "algorithmId": WCA_ALGORITHM_ID,
            "serviceVersion": self.version,
            "engineVersion": WCA_ENGINE_VERSION,
            "executionPipelineVersion": WCA_EXECUTION_PIPELINE_VERSION,
            "apiProcessRole": "transport_and_presentation_only",
            "runtimeProcessRequired": True,
            "runtimeProcessStatus": runtime_process_status,
            "runtimeReadinessState": readiness_state,
            "configurationVersion": active.configuration_version if active else self.configuration_version,
            "configurationHash": active.content_hash if active else "",
            "configurationStatus": active.lifecycle if active else "unavailable",
            "configurationError": self._configuration_error,
            "status": readiness_state,
            "mode": mode,
            "strategyCount": len(WCA_STRATEGY_REGISTRY),
            "paperOnly": True,
            "requestedPaperState": "ON" if bool(runtime_control.get("paperTradingRequested")) else "OFF",
            "requestedPaperTrading": bool(runtime_control.get("paperTradingRequested")),
            "effectivePaperState": "ON" if bool(runtime_control.get("effectivePaperTradingEnabled")) else "OFF",
            "effectivePaperTrading": bool(runtime_control.get("effectivePaperTradingEnabled")),
            "automaticEntryPermission": {
                "permitted": paper_ready,
                "state": "PERMITTED" if paper_ready else "BLOCKED",
                "requested": bool(runtime_control.get("automaticEntriesRequested")),
                "reasonCodes": [] if paper_ready else list(active_entry_block_reasons),
            },
            "paperReady": paper_ready,
            "paperReadyBlockingReasonCodes": [] if paper_ready else list(active_entry_block_reasons),
            "rolloutStage": rollout_stage,
            "rolloutBlockers": list(rollout_blockers),
            "marketOpen": bool(session.market_is_open),
            "entryWindowOpen": bool(session.allowed_session_window),
            "marketSession": _status_dataclass_payload(session),
            "paperBrokerVerified": bool(broker_validation.verified),
            "paperBroker": {
                "verified": bool(broker_validation.verified),
                "brokerAccountId": broker_validation.account_id,
                "baseUrl": broker_validation.base_url,
                "automaticPaperEnvEnabled": bool(broker_validation.automatic_paper_enabled),
                "reasonCodes": list(broker_validation.reason_codes),
            },
            "brokerAccountId": account_id,
            "weightVersion": weights.weight_version if weights else "",
            "calibrationVersion": ",".join(sorted({table.calibration_version for table in calibrations})),
            "runtimeControlRevision": runtime_control.get("controlRevision"),
            "runtimeControlHash": runtime_control.get("controlHash"),
            "authoritativeStateHash": authoritative_state_hash,
            "inventoryReconciliationState": {
                "blocksNewEntries": self._reconciliation_blocks_new_entries(account_id=account_id, symbol=symbol),
                "lastReconciliation": latest_reconciliation,
                "reasonCodes": latest_reconciliation.get("reason_codes") or latest_reconciliation.get("reasonCodes") or [],
            },
            "wcaPosition": position,
            "reservedRisk": float(position.get("reservedRisk") or 0),
            "globalRiskApprovalStatus": latest_global_risk,
            "queueDepths": queue_depths,
            "queueAges": queue_ages,
            "workerHeartbeats": runtime_health.model_dump(mode="json").get("worker_heartbeats", {}) if runtime_health else {},
            "lastFinalizedBar": {
                "symbol": symbol,
                "timestamp": runtime_health.last_processed_bar.isoformat(),
            } if runtime_health and runtime_health.last_processed_bar else None,
            "lastDecision": _redact_status_payload(latest_payload or None),
            "lastOrderIntent": _redact_status_payload(latest_order_intent),
            "lastOrder": _redact_status_payload(latest_outbox),
            "lastBrokerOrder": _redact_status_payload(latest_broker_order),
            "lastFill": _redact_status_payload(latest_fill),
            "lastReconciliation": _redact_status_payload(latest_reconciliation),
            "lastEndOfSessionResult": _redact_status_payload(latest_end_of_session),
            "activeCircuitBreakers": active_circuit_breakers,
            "activeEntryBlockReasonCodes": list(active_entry_block_reasons),
            "runtimeHealth": runtime_health.model_dump(mode="json") if runtime_health else {"status": "unknown", "apiHealthSeparate": True},
            "runtimeSupervisor": runtime_supervisor.status(),
            "runtimeControl": runtime_control,
            "apiHealth": {"status": "ready", "doesNotRunRuntime": True},
            "activeVersions": {
                "configuration": active.configuration_version if active else "",
                "configurationHash": active.content_hash if active else "",
                "weight": weights.weight_version if weights else "",
                "calibrations": sorted({table.calibration_version for table in calibrations}),
            },
            "observability": {
                "eventLagSeconds": runtime_health.lag_seconds if runtime_health else None,
                "lastProcessedBar": runtime_health.last_processed_bar.isoformat() if runtime_health and runtime_health.last_processed_bar else None,
                "lastFinalizedBar": {
                    "symbol": symbol,
                    "timestamp": runtime_health.last_processed_bar.isoformat(),
                } if runtime_health and runtime_health.last_processed_bar else None,
                "lastDecisionId": runtime_health.last_decision_id if runtime_health else self._runtime_repository.last_decision_id(),
                "lastDecision": latest_payload or None,
                "lastOrder": latest_outbox,
                "lastFill": latest_fill,
                "decisionLatencySeconds": (((latest_payload.get("latency") or {}).get("metrics") or {}).get("decision_latency_seconds") if latest_payload else None),
                "brokerStatus": latest_broker_order or {"status": "no_broker_orders"},
                "reconciliationStatus": latest_reconciliation,
                "currentWcaPosition": position,
            },
            "virtualInventory": self.virtual_inventory(account_id=account_id, symbol=symbol),
            "rollout": rollout,
            "finalAcceptance": build_wca_final_acceptance_report(),
            "persistence": {
                "backendAuthoritative": True,
                "migrationVersion": persistence.migration_version,
                "tableCounts": persistence.table_counts,
            },
            "reasonCodes": status_reason_codes,
        }
        return _redact_status_payload(payload)

    def runtime_control(self) -> dict[str, Any]:
        return get_wca_runtime_supervisor().runtime_control()

    def baseline_settings(self) -> dict[str, Any]:
        return self._require_active_configuration().to_baseline_settings().model_dump(mode="json")

    def inventory(self) -> dict[str, Any]:
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "primary": [_catalog_row(row) for row in WCA_STRATEGY_REGISTRY],
            "modifiers": [_catalog_row(row) for row in WCA_MODIFIER_REGISTRY],
            "hardFilters": [_catalog_row(row) for row in WCA_HARD_FILTER_REGISTRY],
            "authoritativeSource": "backend.app.algorithms.wca.strategy_registry",
            "reasonCodes": ("wca.inventory.read_only",),
        }

    def configuration(self) -> dict[str, Any]:
        configuration = self._require_active_configuration()
        baseline = configuration.to_baseline_settings()
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "configurationVersion": configuration.configuration_version,
            "configurationHash": configuration.content_hash,
            "schemaVersion": configuration.schema_version,
            "creator": configuration.creator,
            "source": configuration.source,
            "lifecycle": configuration.lifecycle,
            "createdAt": configuration.created_at.isoformat(),
            "activationTimestamp": configuration.activation_timestamp.isoformat() if configuration.activation_timestamp else None,
            "engineVersion": WCA_ENGINE_VERSION,
            "canonicalConfiguration": configuration.model_dump(mode="json"),
            "decisionSettings": {
                "strongBuyThreshold": baseline.strong_buy_threshold,
                "buyThreshold": baseline.buy_threshold,
                "sellThreshold": baseline.sell_threshold,
                "strongSellThreshold": baseline.strong_sell_threshold,
                "minimumActiveStrategies": baseline.minimum_active_strategies,
                "minimumDirectionalAgreement": baseline.minimum_directional_agreement,
                "minimumAverageConfidence": baseline.minimum_average_confidence,
            },
            "tradingSettings": {
                "baseRiskPercent": baseline.base_risk_percent,
                "orderAllocationPercent": baseline.order_allocation_percent,
                "dailyAllocationPercent": baseline.daily_allocation_percent,
                "maxPositionPercent": baseline.max_position_percent,
                "maxDailyLossPercent": baseline.max_daily_loss_percent,
                "maxDailyTrades": baseline.max_daily_trades,
                "atrStopMultiplier": baseline.atr_stop_multiplier,
                "minimumStopDistancePercent": baseline.minimum_stop_distance_percent,
                "takeProfitR": baseline.take_profit_r,
                "slippagePerShare": baseline.assumed_slippage_per_share,
                "maxSpreadPercent": baseline.max_spread_percent,
                "maxParticipationPercent": baseline.max_participation_percent,
                "maxAllowedShares": baseline.max_allowed_shares,
                "pyramidingEnabled": baseline.pyramiding_enabled,
            },
            "baseWeights": base_weight_map(),
            "strategyCount": len(WCA_STRATEGY_REGISTRY),
            "paperOnly": True,
            "rollout": wca_rollout_status(),
        }

    def update_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "canonicalConfiguration" in payload or "canonical_configuration" in payload:
            candidate = WcaConfiguration.model_validate(payload.get("canonicalConfiguration") or payload.get("canonical_configuration"))
        else:
            current = self._require_active_configuration()
            candidate = canonical_configuration_from_legacy(
                payload.get("decisionSettings") or payload.get("decision_settings") or current.to_baseline_settings().model_dump(mode="json"),
                payload.get("tradingSettings") or payload.get("trading_settings") or current.to_baseline_settings().model_dump(mode="json"),
                configuration_id=current.configuration_id,
                configuration_version=f"{current.configuration_version}.rev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
                creator=str(payload.get("creator") or "api"),
                source="api_compatibility_boundary",
                lifecycle=WcaConfigurationLifecycle.CANDIDATE,
            )
        candidate = self._repository.validate_configuration_revision(candidate)
        saved = self._repository.save_candidate_configuration(candidate, symbol="SPY", engine_version=WCA_ENGINE_VERSION)
        baseline = saved.to_baseline_settings()
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "status": "CANDIDATE_SAVED",
            "configurationVersion": saved.configuration_version,
            "configurationHash": saved.content_hash,
            "lifecycle": saved.lifecycle,
            "decisionSettings": {
                "strongBuyThreshold": baseline.strong_buy_threshold,
                "buyThreshold": baseline.buy_threshold,
                "sellThreshold": baseline.sell_threshold,
                "strongSellThreshold": baseline.strong_sell_threshold,
                "minimumActiveStrategies": baseline.minimum_active_strategies,
                "minimumDirectionalAgreement": baseline.minimum_directional_agreement,
                "minimumAverageConfidence": baseline.minimum_average_confidence,
            },
            "tradingSettings": {
                "baseRiskPercent": baseline.base_risk_percent,
                "orderAllocationPercent": baseline.order_allocation_percent,
                "dailyAllocationPercent": baseline.daily_allocation_percent,
                "maxPositionPercent": baseline.max_position_percent,
                "maxDailyLossPercent": baseline.max_daily_loss_percent,
                "maxDailyTrades": baseline.max_daily_trades,
                "atrStopMultiplier": baseline.atr_stop_multiplier,
                "minimumStopDistancePercent": baseline.minimum_stop_distance_percent,
                "takeProfitR": baseline.take_profit_r,
                "slippagePerShare": baseline.assumed_slippage_per_share,
                "maxSpreadPercent": baseline.max_spread_percent,
                "maxParticipationPercent": baseline.max_participation_percent,
                "maxAllowedShares": baseline.max_allowed_shares,
                "pyramidingEnabled": baseline.pyramiding_enabled,
            },
            "activationRequired": True,
            "reasonCodes": ("wca.configuration.candidate_saved", "wca.api.configuration_does_not_activate_inline"),
        }

    def enqueue_configuration_activation(self, configuration_version: str) -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.CONFIGURATION_ACTIVATION,
            payload={"configuration_version": configuration_version},
            reason_codes=("wca.api.configuration_activation.enqueued",),
            priority=15,
        )

    def enqueue_configuration_rollback(self, configuration_version: str) -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.CONFIGURATION_ROLLBACK,
            payload={"configuration_version": configuration_version},
            reason_codes=("wca.api.configuration_rollback.enqueued",),
            priority=15,
        )

    def enqueue_evaluation_request(self, request: WcaEvaluateRequest) -> WcaResearchJobReceipt:
        if not request.strategy_signals:
            raise WcaEngineInputError("strategySignals are required for WCA legacy evaluation enqueue")
        return self.enqueue_shadow_comparison(request)

    def enqueue_paper_command(self, request: WcaPaperExecutionRequest, *, mode: str | None = None) -> dict[str, Any]:
        command_request = request.model_copy(update={"mode": mode or request.mode})
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.MANUAL_PAPER_COMMAND,
            account_id=command_request.account_id,
            payload={"request": command_request.model_dump(mode="json"), "paper_only": True},
            reason_codes=("wca.api.paper_command.enqueued", "wca.api.no_inline_broker_submission"),
            priority=25,
        )

    def enqueue_pause_new_entries(self, *, reason: str = "api_request") -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.PAUSE_NEW_ENTRIES,
            payload={"reason": reason},
            reason_codes=("wca.api.pause_new_entries.enqueued",),
            priority=5,
        )

    def enqueue_resume_new_entries(self, *, reason: str = "api_request") -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.RESUME_NEW_ENTRIES,
            payload={"reason": reason},
            reason_codes=("wca.api.resume_new_entries.enqueued",),
            priority=20,
        )

    def enqueue_automatic_paper_control(self, *, enabled: bool, actor: str, reason: str, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.SET_AUTOMATIC_PAPER,
            account_id=account_id,
            symbol=symbol,
            payload={"enabled": enabled, "actor": actor, "reason": reason, "global_paper_control": True},
            reason_codes=("wca.api.automatic_paper_control.enqueued", "wca.api.no_inline_trading_logic"),
            priority=3,
        )

    def enqueue_runtime_control_update(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        actor = str(body.get("actor") or body.get("updatedBy") or body.get("updated_by") or "api")
        reason = str(body.get("reason") or "api_runtime_control_update")
        account_id = str(body.get("accountId") or body.get("account_id") or body.get("brokerAccountId") or body.get("broker_account_id") or "paper")
        symbol = str(body.get("symbol") or "SPY")
        paper_requested = _optional_bool(body, "paperTradingRequested", "paper_trading_requested", "requestedPaperTradingEnabled")
        automatic_requested = _optional_bool(body, "automaticEntriesRequested", "automatic_entries_requested", "automaticPaperTradingEnabled")
        pause_requested = _optional_bool(body, "pauseNewEntries", "pause_new_entries")
        if paper_requested is not None or automatic_requested is not None:
            enabled = bool(paper_requested if paper_requested is not None else automatic_requested)
            return self.enqueue_automatic_paper_control(
                enabled=enabled,
                actor=actor,
                reason=reason,
                account_id=account_id,
                symbol=symbol,
            )
        if pause_requested is True:
            return self._enqueue_runtime_control(
                WcaRuntimeCommandType.PAUSE_NEW_ENTRIES,
                account_id=account_id,
                symbol=symbol,
                payload={"reason": reason, "actor": actor},
                reason_codes=("wca.api.runtime_control.pause_update.enqueued",),
                priority=5,
            )
        if pause_requested is False:
            return self._enqueue_runtime_control(
                WcaRuntimeCommandType.RESUME_NEW_ENTRIES,
                account_id=account_id,
                symbol=symbol,
                payload={"reason": reason, "actor": actor},
                reason_codes=("wca.api.runtime_control.resume_update.enqueued",),
                priority=20,
            )
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.HEARTBEAT,
            account_id=account_id,
            symbol=symbol,
            payload={"reason": reason, "actor": actor, "readiness_refresh": True},
            reason_codes=("wca.api.runtime_control.refresh.enqueued",),
            priority=30,
        )

    def enqueue_reconciliation_request(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.BROKER_RECONCILIATION,
            account_id=account_id,
            symbol=symbol,
            payload={"requested_by": "api"},
            reason_codes=("wca.api.reconciliation.enqueued",),
            priority=10,
        )

    def enqueue_emergency_risk_reduction(self, *, account_id: str = "paper", symbol: str = "SPY", reason: str = "api_request") -> dict[str, Any]:
        return self._enqueue_runtime_control(
            WcaRuntimeCommandType.EMERGENCY_RISK_REDUCTION,
            account_id=account_id,
            symbol=symbol,
            payload={"reason": reason, "risk_reducing_only": True},
            reason_codes=("wca.api.emergency_risk_reduction.enqueued",),
            priority=1,
        )

    def _enqueue_runtime_control(
        self,
        command_type: WcaRuntimeCommandType,
        *,
        account_id: str = "paper",
        symbol: str = "SPY",
        payload: dict[str, Any] | None = None,
        reason_codes: tuple[str, ...] = (),
        priority: int = 50,
    ) -> dict[str, Any]:
        command = runtime_command(
            command_type,
            account_id=account_id,
            symbol=symbol,
            payload={**(payload or {}), "source": "api_transport_only"},
            priority=priority,
            reason_codes=reason_codes,
        )
        queued = self._runtime_repository.enqueue_command(command)
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "commandId": command.command_id,
            "commandType": command.command_type.value if hasattr(command.command_type, "value") else str(command.command_type),
            "status": queued.status,
            "accepted": queued.accepted,
            "queued": queued.accepted,
            "paperOnly": True,
            "reasonCodes": (*queued.reason_codes, *reason_codes, "wca.api.background_control_surface"),
        }

    def latest_decisions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._read_payload_rows("wca_decisions", "created_at", limit=limit)

    def latest_trades(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._read_payload_rows("wca_trade_ledger", "created_at", limit=limit)

    def latest_order(self) -> dict[str, Any] | None:
        return self._read_latest_payload_row("wca_execution_outbox", "updated_at")

    def latest_fill(self) -> dict[str, Any] | None:
        return self._read_latest_payload_row("wca_attributed_fills", "created_at")

    def current_wca_position(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        if not hasattr(self._repository, "read_inventory_projection"):
            return {"accountId": account_id, "symbol": symbol, "openQuantity": 0, "source": "repository_unavailable"}
        projection = self._repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=symbol)
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "accountId": account_id,
            "symbol": symbol,
            "openQuantity": projection.open_quantity,
            "averageEntryPrice": projection.average_entry_price,
            "realizedPnl": projection.realized_pnl,
            "unrealizedPnl": projection.unrealized_pnl,
            "reservedRisk": projection.reserved_risk,
            "configurationVersion": projection.configuration_version,
            "decisionId": projection.decision_id,
            "runId": projection.run_id,
            "lastEventTimestamp": _status_timestamp(projection.last_event_timestamp),
            "reconciliationWatermark": projection.reconciliation_watermark,
        }

    def virtual_inventory(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"accountId": account_id, "symbol": symbol, "openQuantity": 0, "source": "repository_unavailable"}
        with self._repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wca_virtual_positions
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY updated_at DESC
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "accountId": account_id,
            "symbol": symbol,
            "separateFromOtherAlgorithms": True,
            "positions": [_row_payload(row) for row in rows],
        }

    def broker_status(self) -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"status": "unknown"}
        with self._repository.connect() as conn:
            row = conn.execute("SELECT * FROM wca_broker_orders WHERE algorithm_id = ? ORDER BY created_at DESC LIMIT 1", (WCA_ALGORITHM_ID,)).fetchone()
        return _redact_status_payload(_row_payload(row)) if row else {"status": "no_broker_orders"}

    def reconciliation_status(self, *, account_id: str | None = None, symbol: str | None = None) -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"status": "unknown"}
        sql = "SELECT payload_json FROM wca_broker_reconciliations WHERE algorithm_id = ?"
        params: list[Any] = [WCA_ALGORITHM_ID]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._repository.connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
        return _json_payload(row["payload_json"]) if row else {"status": "not_run"}

    def latest_order_intent(self, *, account_id: str = "paper") -> dict[str, Any] | None:
        if not hasattr(self._repository, "connect"):
            return None
        with self._repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_order_intents
                WHERE algorithm_id = ? AND account_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id),
            ).fetchone()
        return _row_payload(row) if row else None

    def latest_broker_order(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any] | None:
        if not hasattr(self._repository, "connect"):
            return None
        with self._repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
        return _row_payload(row) if row else None

    def latest_global_risk_status(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"status": "unknown"}
        with self._repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_global_risk_responses
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
        return _row_payload(row) if row else {"status": "not_requested"}

    def latest_end_of_session_result(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"status": "unknown"}
        with self._repository.connect() as conn:
            command = conn.execute(
                """
                SELECT command_id, command_type, status, reason_codes_json, payload_json, updated_at, created_at
                FROM wca_runtime_command_queue
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND command_type = 'end_of_session'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
            event = conn.execute(
                """
                SELECT *
                FROM wca_inventory_ledger
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND event_type = 'END_OF_SESSION_FLATTEN'
                ORDER BY event_timestamp DESC, created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
        return {
            "status": command["status"] if command else "not_run",
            "command": _row_payload(command) if command else None,
            "flattenEvent": _row_payload(event) if event else None,
        }

    def _daily_state(self, *, account_id: str, symbol: str, now: datetime) -> dict[str, Any]:
        if not hasattr(self._repository, "read_daily_state_projection"):
            return {"status": "unknown"}
        state = self._repository.read_daily_state_projection(
            algorithm_id=WCA_ALGORITHM_ID,
            broker_account_id=account_id,
            symbol=symbol,
            session_date=now.date().isoformat(),
        )
        return _status_dataclass_payload(state)

    def _reconciliation_blocks_new_entries(self, *, account_id: str, symbol: str) -> bool:
        if not hasattr(self._repository, "reconciliation_blocks_new_entries"):
            return True
        return bool(self._repository.reconciliation_blocks_new_entries(account_id=account_id, symbol=symbol))

    def command_status(self, command_id: str) -> dict[str, Any]:
        if not hasattr(self._repository, "connect"):
            return {"status": "not_found"}
        with self._repository.connect() as conn:
            row = conn.execute("SELECT command_id, command_type, status, reason_codes_json, payload_json, updated_at FROM wca_runtime_command_queue WHERE command_id = ?", (command_id,)).fetchone()
        if row is None:
            return {"status": "not_found", "commandId": command_id}
        return {**dict(row), "reasonCodes": _json_payload(row["reason_codes_json"])}

    def _read_payload_rows(self, table: str, order_column: str, *, limit: int) -> list[dict[str, Any]]:
        if not hasattr(self._repository, "connect"):
            return []
        safe_limit = max(1, min(int(limit), 200))
        with self._repository.connect() as conn:
            rows = conn.execute(
                f"SELECT payload_json FROM {table} WHERE algorithm_id = ? ORDER BY {order_column} DESC LIMIT ?",
                (WCA_ALGORITHM_ID, safe_limit),
            ).fetchall()
        return [_json_payload(row["payload_json"]) for row in rows]

    def _read_latest_payload_row(self, table: str, order_column: str) -> dict[str, Any] | None:
        if not hasattr(self._repository, "connect"):
            return None
        with self._repository.connect() as conn:
            row = conn.execute(
                f"SELECT payload_json FROM {table} WHERE algorithm_id = ? ORDER BY {order_column} DESC, created_at DESC LIMIT 1",
                (WCA_ALGORITHM_ID,),
            ).fetchone()
        return _json_payload(row["payload_json"]) if row else None

    def evaluate(self, request: WcaEvaluateRequest) -> WcaEvaluateResponse:
        configuration = self._require_active_configuration()
        snapshot = _legacy_request_snapshot(request)
        sizing_inputs = request.sizing_inputs
        pipeline = run_wca_execution_pipeline(
            WcaExecutionPipelineInput(
                run_id=request.snapshot_id or "legacy-api",
                decision_id=f"wca-{request.snapshot_id or 'legacy-api'}",
                order_intent_id=f"wca-intent-{request.snapshot_id or 'legacy-api'}",
                snapshot=snapshot,
                configuration_version=configuration.configuration_version,
                configuration=configuration,
                weight_snapshot=self._read_active_weights_as_of(snapshot.decision_timestamp) or baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
                calibration_tables=self._read_active_calibrations_as_of(symbol=snapshot.symbol, as_of=snapshot.decision_timestamp),
                runtime_mode=WcaRuntimeMode.SHADOW,
                synthetic_quote_allowed=False,
                account_equity=sizing_inputs.account_equity if sizing_inputs else request.trading_settings.starting_capital,
                available_buying_power=sizing_inputs.account_equity if sizing_inputs else request.trading_settings.starting_capital,
            )
        )
        decision = pipeline.decision.model_copy(
            update={
                "reason_codes": (*pipeline.decision.reason_codes, "wca.legacy_request_translated", "wca.legacy_external_strategy_signals_ignored"),
            }
        )
        return _legacy_response_from_pipeline(decision, request)

    def record_shadow_comparison_evidence(
        self,
        request: WcaEvaluateRequest,
        *,
        numeric_tolerance: float = 1e-4,
        quantity_tolerance: int = 0,
        price_tolerance: float = 1e-4,
    ) -> WcaShadowComparisonEvidence:
        return run_wca_shadow_comparison(
            request,
            repository=self._repository,
            tolerance=WcaShadowComparisonTolerance(
                numeric=numeric_tolerance,
                quantity=quantity_tolerance,
                price=price_tolerance,
            ),
            configuration=self._require_active_configuration(),
        )

    def execute_paper(self, request: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
        configuration = self._require_active_configuration()
        snapshot = _paper_snapshot(request)
        open_position = _paper_open_position(request)
        identity = _paper_identity_part(request.account_id)
        pipeline = run_wca_execution_pipeline(
            WcaExecutionPipelineInput(
                run_id=request.run_id,
                decision_id=f"{request.run_id}-{identity}-{request.mode}-decision-{snapshot.decision_timestamp.isoformat()}",
                order_intent_id=f"{request.run_id}-{identity}-{request.mode}-intent-{snapshot.decision_timestamp.isoformat()}",
                snapshot=snapshot,
                configuration_version=configuration.configuration_version,
                configuration=configuration,
                runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER if request.mode == "automatic" else WcaRuntimeMode.MANUAL_PAPER,
                synthetic_quote_allowed=False,
                account_id=request.account_id,
                weight_snapshot=self._read_active_weights_as_of(snapshot.decision_timestamp) or baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
                calibration_tables=self._read_active_calibrations_as_of(symbol=snapshot.symbol, as_of=snapshot.decision_timestamp),
                trades_today=request.trades_today,
                open_position=open_position,
                realized_daily_loss=request.realized_daily_loss,
                account_equity=request.account_equity,
                available_buying_power=request.available_buying_power,
                allocated_daily_loss_budget=request.allocated_daily_loss_budget,
                remaining_allocated_risk_budget=request.remaining_allocated_risk_budget,
                global_gate_quantity_cap=request.global_gate_quantity_cap,
                approved_risk_budget=request.approved_risk_budget,
                allow_position_increase=request.allow_position_increase,
                manual_sizing_override=_manual_override(request),
                emergency_exit=request.emergency_exit,
            )
        )
        proposed = pipeline.decision.proposed_order
        runtime_control = get_wca_runtime_supervisor().runtime_control()
        automatic_paper_enabled = bool(runtime_control.get("effectiveAutomaticEntriesEnabled"))
        automatic_blocked = request.mode == "automatic" and not automatic_paper_enabled
        if proposed is None:
            status = "NO_ACTION"
            submitted = False
            reasons = ("wca.paper.no_order_proposed",)
            decision = pipeline.decision.model_copy(update={"reason_codes": (*pipeline.decision.reason_codes, *reasons)})
        elif automatic_blocked:
            status = "ROLLOUT_BLOCKED"
            submitted = False
            reasons = ("wca.paper.automatic_rollout_blocked",)
            decision = drop_wca_order(
                pipeline.decision.model_copy(update={"reason_codes": (*pipeline.decision.reason_codes, *reasons)}),
                reasons,
            )
            proposed = None
        else:
            status = WcaOrderStatus.VALIDATED.value
            submitted = False
            reasons = ("wca.paper.execution_path_completed", f"wca.paper.mode.{request.mode}")
            key = proposed.idempotency_key or _paper_order_idempotency_key(request.account_id, pipeline.decision)
            proposed = proposed.model_copy(
                update={
                    "status": WcaOrderStatus.VALIDATED,
                    "idempotency_key": key,
                    "account_id": request.account_id,
                    "rollout_stage": str(runtime_control.get("rolloutStage") or ""),
                    "rollout_evidence_revision": str(runtime_control.get("rolloutEvidenceRevision") or ""),
                    "rollout_evidence_hash": str(runtime_control.get("rolloutEvidenceHash") or ""),
                    "reason_codes": (*proposed.reason_codes, "wca.paper.idempotency_key_generated"),
                }
            )
            decision = pipeline.decision.model_copy(
                update={
                    "proposed_order": proposed,
                    "rollout_stage": str(runtime_control.get("rolloutStage") or ""),
                    "rollout_evidence_revision": str(runtime_control.get("rolloutEvidenceRevision") or ""),
                    "rollout_evidence_hash": str(runtime_control.get("rolloutEvidenceHash") or ""),
                    "reason_codes": (*pipeline.decision.reason_codes, *reasons),
                }
            )
            decision = apply_wca_final_order_validation(decision, _paper_order_validation_context(request, snapshot, runtime_control=runtime_control, automatic_paper_enabled=automatic_paper_enabled))
            if decision.proposed_order is None:
                status = "NO_ACTION"
                submitted = False
                reasons = (*reasons, "wca.paper.final_order_validation_failed")
                decision = decision.model_copy(update={"reason_codes": (*decision.reason_codes, "wca.paper.final_order_validation_failed")})
            else:
                proposed = decision.proposed_order
                decision = decision.model_copy(update={"proposed_order": proposed})
                broker_request = build_wca_paper_broker_request(proposed)
                reservation = self._repository.reserve_decision_order_and_outbox(
                    decision,
                    run_id=request.run_id,
                    account_id=request.account_id,
                    idempotency_key=key,
                    client_order_id=broker_request.client_order_id,
                    request_payload=broker_request.model_dump(mode="json"),
                    final_validation_context=_paper_order_validation_context(
                        request,
                        snapshot,
                        order_type=broker_request.order_type,
                        time_in_force=broker_request.time_in_force,
                        runtime_control=runtime_control,
                        automatic_paper_enabled=automatic_paper_enabled,
                    ),
                )
                proposed = reservation.proposed_order
                if reservation.created:
                    status = WcaOrderStatus.OUTBOX_RESERVED.value
                    submitted = False
                    reasons = (*reasons, "wca.paper.outbox_reserved_before_submission")
                    decision = decision.model_copy(
                        update={
                            "proposed_order": proposed,
                            "reason_codes": (*decision.reason_codes, "wca.paper.outbox_reserved_before_submission"),
                        }
                    )
                else:
                    status = "DUPLICATE_INTENT"
                    submitted = False
                    reasons = (*reasons, "wca.paper.duplicate_order_intent")
                    decision = decision.model_copy(
                        update={
                            "proposed_order": proposed,
                            "reason_codes": (*decision.reason_codes, "wca.paper.duplicate_order_intent"),
                        }
                    )
            proposed = decision.proposed_order
        if proposed is None or status in {"NO_ACTION", "ROLLOUT_BLOCKED", "DUPLICATE_INTENT"}:
            self._repository.write_decision_snapshot(decision, run_id=request.run_id)
        return WcaPaperExecutionResult(
            mode=request.mode,
            action_status=status,
            submitted=submitted,
            idempotency_key=proposed.idempotency_key if proposed is not None else None,
            decision=decision,
            proposed_order=proposed,
            called_production_modules=pipeline.called_production_modules,
            reason_codes=(*reasons, "wca.paper.uses_execution_pipeline"),
            explanation="Manual and automatic WCA paper actions route through the shared execution pipeline used by backtesting.",
        )

    def execute_manual_paper(self, request: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
        return self.execute_paper(request.model_copy(update={"mode": "manual"}))

    def execute_automatic_paper(self, request: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
        return self.execute_paper(request.model_copy(update={"mode": "automatic"}))

    def validate_paper_stability(self, request: WcaPaperStabilityValidationRequest) -> WcaPaperStabilityValidationResult:
        return validate_wca_paper_stability(request, repository=self._repository)

    def enqueue_paper_stability_report(self, request: WcaPaperStabilityValidationRequest) -> WcaResearchJobReceipt:
        return self.enqueue_research_job(
            WcaResearchJobType.PAPER_STABILITY_REPORT,
            payload={"request": request.model_dump(mode="json")},
            run_id=request.validation_id,
            configuration_version="wca_paper_stability_report",
            priority=40,
            reason_codes=("wca.api.paper_stability.enqueued_research_job",),
        )

    def reconcile_paper_broker(
        self,
        broker: WcaPaperBrokerReconciliationClient,
        *,
        account_id: str | None = None,
        stale_after_seconds: int = 300,
    ) -> WcaBrokerReconciliationResult:
        return reconcile_wca_broker(
            repository=self._repository,
            broker=broker,
            account_id=account_id,
            stale_after_seconds=stale_after_seconds,
        )

    def run_backtest(self, request: WcaBacktestRequest) -> BacktestResult:
        result = run_wca_backtest(request, configuration=self._require_active_configuration())
        self._backtest_results[result.run_configuration.run_id] = result
        self._repository.save_backtest_result(result)
        return result

    def enqueue_backtest(self, request: WcaBacktestRequest) -> WcaResearchJobReceipt:
        return self.enqueue_research_job(
            WcaResearchJobType.BACKTEST,
            payload={"request": request.model_dump(mode="json")},
            run_id=request.configuration.run_id,
            configuration_version=request.configuration.configuration_version,
            priority=30,
            reason_codes=("wca.api.backtest.enqueued_research_job",),
        )

    def run_backtest_modes(self, request: WcaBacktestRequest) -> WcaBacktestSuiteResult:
        result = run_wca_backtest_modes(request, configuration=self._require_active_configuration())
        self._backtest_suites[result.suite_id] = result
        for mode_result in (result.smoke, *result.rolling, result.full_history, result.walk_forward, result.holdout):
            self._backtest_results[mode_result.result.run_configuration.run_id] = mode_result.result
            self._repository.save_backtest_result(mode_result.result)
        return result

    def enqueue_backtest_modes(self, request: WcaBacktestRequest) -> WcaResearchJobReceipt:
        return self.enqueue_research_job(
            WcaResearchJobType.BACKTEST_MODES,
            payload={"request": request.model_dump(mode="json")},
            run_id=request.configuration.run_id,
            configuration_version=request.configuration.configuration_version,
            priority=35,
            reason_codes=("wca.api.backtest_modes.enqueued_research_job",),
        )

    def enqueue_shadow_comparison(self, request: WcaEvaluateRequest) -> WcaResearchJobReceipt:
        return self.enqueue_research_job(
            WcaResearchJobType.SHADOW_COMPARISON,
            payload={"request": request.model_dump(mode="json")},
            run_id=request.snapshot_id or "wca-shadow-comparison",
            configuration_version=request.decision_settings.model_dump().get("configuration_version", "wca_shadow_comparison"),
            priority=45,
            reason_codes=("wca.api.shadow_comparison.enqueued_research_job",),
        )

    def enqueue_research_job(
        self,
        job_type: WcaResearchJobType,
        *,
        payload: dict[str, Any],
        run_id: str,
        configuration_version: str,
        priority: int = 50,
        reason_codes: tuple[str, ...] = (),
    ) -> WcaResearchJobReceipt:
        job = research_job(
            job_type,
            payload=payload,
            run_id=run_id,
            configuration_version=configuration_version,
            priority=priority,
            reason_codes=reason_codes,
        )
        return self._research_repository.enqueue_job(job)

    def backtest_status(self, run_id: str) -> dict[str, Any]:
        if run_id in self._backtest_results or self._repository.load_backtest_result(run_id) is not None:
            return {"runId": run_id, "status": "complete", "backendAuthoritative": True}
        if run_id in self._backtest_suites:
            return {"runId": run_id, "status": "complete", "backendAuthoritative": True, "suite": True}
        job = self._research_repository.read_job(run_id) or self._research_repository.read_latest_job_for_run(run_id)
        if job is not None:
            return {"runId": run_id, "jobId": job.job_id, "status": str(job.status).lower(), "backendAuthoritative": True, "researchJob": job.model_dump(mode="json")}
        return {"runId": run_id, "status": "not_found", "backendAuthoritative": True}

    def backtest_result(self, run_id: str) -> BacktestResult | WcaBacktestSuiteResult | None:
        return self._backtest_results.get(run_id) or self._backtest_suites.get(run_id) or self._repository.load_backtest_result(run_id)

    def backtest_report(self, run_id: str) -> dict[str, Any]:
        result = self.backtest_result(run_id)
        if result is None:
            return {"runId": run_id, "status": "not_found"}
        return {
            "runId": run_id,
            "status": "complete",
            "backendAuthoritative": True,
            "report": result.model_dump(mode="json"),
        }

    def research_job_status(self, job_id: str) -> dict[str, Any]:
        snapshot = self._research_repository.read_job(job_id)
        if snapshot is None:
            return {"jobId": job_id, "status": "not_found", "backendAuthoritative": True}
        return {"jobId": job_id, "status": snapshot.status.lower(), "backendAuthoritative": True, "researchJob": snapshot.model_dump(mode="json")}

    def cancel_research_job(self, job_id: str) -> dict[str, Any]:
        cancelled = self._research_repository.request_cancellation(job_id)
        return {
            "jobId": job_id,
            "cancelRequested": cancelled,
            "status": "cancel_requested" if cancelled else "not_cancelled",
            "backendAuthoritative": True,
            "reasonCodes": ["wca.research.job.cancel_requested"] if cancelled else ["wca.research.job.cancel_not_applied"],
        }


def _paper_snapshot(request: WcaPaperExecutionRequest) -> WcaMarketSnapshot:
    candles = tuple(sorted(request.candles, key=lambda candle: candle.timestamp))
    latest = candles[-1]
    quote_by_time = {quote.timestamp: quote for quote in request.quotes}
    quote = quote_by_time.get(latest.timestamp)
    return WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=candles,
        quote=quote,
        data_ready=True,
        source="wca_paper_execution",
        reason_codes=("wca.paper.completed_bar",) if quote is not None else ("wca.paper.completed_bar", "wca.paper.nbbo_missing_entries_blocked"),
    )


def _paper_open_position(request: WcaPaperExecutionRequest) -> WcaBacktestOpenPosition | None:
    if request.current_position_quantity <= 0:
        return None
    if request.current_position_side is None or request.current_position_entry_price is None:
        return None
    candles = tuple(sorted(request.candles, key=lambda candle: candle.timestamp))
    latest = candles[-1]
    side = request.current_position_side
    entry = request.current_position_entry_price
    stop = request.current_position_stop_price
    target = request.current_position_target_price
    if stop is None:
        stop = max(0.01, entry * 0.99) if side == "BUY" else entry * 1.01
    if target is None:
        target = entry * 1.02 if side == "BUY" else max(0.01, entry * 0.98)
    return WcaBacktestOpenPosition(
        trade_id=f"{request.run_id}-paper-open",
        decision_id=f"{request.run_id}-paper-open",
        symbol=request.symbol,
        side=side,
        quantity=request.current_position_quantity,
        entry_at=request.current_position_entry_at or latest.timestamp.astimezone(timezone.utc),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
    )


def _manual_override(request: WcaPaperExecutionRequest) -> WcaManualSizingOverride | None:
    if request.manual_override is None:
        return None
    return WcaManualSizingOverride(
        quantity=request.manual_override.quantity,
        limit_price=request.manual_override.limit_price,
        stop_price=request.manual_override.stop_price,
        target_price=request.manual_override.target_price,
    )


def _paper_order_validation_context(
    request: WcaPaperExecutionRequest,
    snapshot: WcaMarketSnapshot,
    *,
    order_type: str = "LIMIT",
    time_in_force: str = "DAY",
    runtime_control: dict[str, Any] | None = None,
    automatic_paper_enabled: bool = True,
) -> WcaOrderValidationContext:
    runtime_control = runtime_control or {}
    rollout_stage = str(runtime_control.get("rolloutStage") or runtime_control.get("rollout_stage") or "")
    automatic_mode = request.mode == "automatic"
    evaluation_timestamp = datetime.now(timezone.utc) if automatic_mode else snapshot.decision_timestamp
    session = (
        validate_wca_entry_session(
            timestamp=evaluation_timestamp,
            entry_cutoff_minutes=15 * 60 + 30,
            require_broker_clock=False,
        )
        if automatic_mode
        else None
    )
    return WcaOrderValidationContext(
        evaluation_timestamp=evaluation_timestamp,
        paper_only_mode=True,
        account_id=request.account_id,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER if request.mode == "automatic" else WcaRuntimeMode.MANUAL_PAPER,
        rollout_stage=rollout_stage,
        rollout_evidence_revision=str(runtime_control.get("rolloutEvidenceRevision") or runtime_control.get("rollout_evidence_revision") or ""),
        rollout_evidence_hash=str(runtime_control.get("rolloutEvidenceHash") or runtime_control.get("rollout_evidence_hash") or ""),
        rollout_allowed_symbols=(request.symbol.upper(),) if rollout_stage == "LIMITED_AUTOMATIC_PAPER" else (),
        rollout_allowed_strategy_ids=tuple(
            str(strategy_id)
            for strategy_id in ((runtime_control.get("limitedAutomaticPaperCaps") or {}).get("allowed_strategies") or ())
        ),
        rollout_allowed_entry_windows=tuple(
            str(window)
            for window in ((runtime_control.get("limitedAutomaticPaperCaps") or {}).get("session_windows") or ())
        ),
        rollout_max_quantity=(runtime_control.get("limitedAutomaticPaperCaps") or {}).get("max_quantity"),
        rollout_max_daily_trades=(runtime_control.get("limitedAutomaticPaperCaps") or {}).get("max_daily_trades"),
        rollout_max_daily_loss=(runtime_control.get("limitedAutomaticPaperCaps") or {}).get("max_daily_loss_dollars"),
        rollout_policy_required=request.mode == "automatic",
        requires_executable_paper_stage=True,
        automatic_paper_enabled=automatic_paper_enabled,
        market_is_open=session.market_is_open if session is not None else True,
        allowed_session_window=session.allowed_session_window if session is not None else True,
        market_session_reason_codes=session.reason_codes if session is not None else (),
        candle_freshness_seconds=120,
        data_ready=snapshot.data_ready,
        inventory_consistent=True,
        order_type=order_type,
        time_in_force=time_in_force,
        protective_exit_plan_present=True,
        current_position_quantity=request.current_position_quantity,
        current_position_side=request.current_position_side,
        allow_position_increase=request.allow_position_increase,
        position_owned_by_wca=True,
        quote_freshness_seconds=None if request.emergency_exit else 15,
        decision_expiration_seconds=120 if request.mode == "automatic" else None,
        available_buying_power=request.available_buying_power,
        account_equity=request.account_equity,
        max_position_value=request.account_equity,
        realized_daily_loss=request.realized_daily_loss,
        max_daily_loss=request.allocated_daily_loss_budget,
        trades_today=request.trades_today,
        max_daily_trades=None,
        max_approved_quantity=request.global_gate_quantity_cap,
        expected_net_edge=request.estimated_expectancy_after_costs,
        minimum_net_edge=0,
        idempotency_required=True,
        new_entry_permitted=True,
        risk_reducing_exit_permitted=True,
        is_risk_reducing_exit=request.emergency_exit,
    )


def _paper_order_idempotency_key(account_id: str, decision) -> str:
    order = decision.proposed_order
    if order is None:
        raise ValueError("cannot generate an idempotency key without a WCA proposed order")
    side = order.side.value if hasattr(order.side, "value") else str(order.side)
    return idempotency_key(
        account_id,
        order.algorithm_id,
        order.symbol.upper(),
        side,
        decision.decision_id,
        decision.decision_timestamp.astimezone(timezone.utc).isoformat(),
        decision.configuration_version,
    )


def _paper_identity_part(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value).strip("-")
    return cleaned or "paper"


def _legacy_request_snapshot(request: WcaEvaluateRequest) -> WcaMarketSnapshot:
    market = request.market_snapshot or {}
    timestamp = request.timestamp or datetime.now(timezone.utc)
    candles_payload = market.get("candles") if isinstance(market.get("candles"), list) else None
    if candles_payload:
        candles = tuple(WcaCandle.model_validate(row) for row in candles_payload)
    else:
        sizing = request.sizing_inputs
        close = float(market.get("close") or market.get("price") or (sizing.price if sizing else 0) or 0)
        if close <= 0:
            close = 100.0
        atr = float(market.get("atr") or (sizing.atr if sizing else 0.1) or 0.1)
        volume = float(market.get("latestVolume") or market.get("latest_volume") or (sizing.latest_volume if sizing else 100000) or 100000)
        candles = (
            WcaCandle(
                timestamp=timestamp,
                open=max(0.01, close - atr / 4),
                high=close + atr / 2,
                low=max(0.01, close - atr / 2),
                close=close,
                volume=volume,
                vwap=close,
            ),
        )
    latest = candles[-1]
    quote = None
    bid = market.get("bid")
    ask = market.get("ask")
    if bid is not None and ask is not None:
        quote = WcaQuote(timestamp=latest.timestamp, bid=float(bid), ask=float(ask))
    return WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=candles,
        quote=quote,
        data_ready=True,
        source="wca_legacy_request_translation",
        reason_codes=("wca.legacy_request_translated",) if quote is not None else ("wca.legacy_request_translated", "wca.paper.nbbo_missing_entries_blocked"),
    )


def _legacy_response_from_pipeline(decision, request: WcaEvaluateRequest) -> WcaEvaluateResponse:
    evaluations = tuple(_legacy_signal_from_evaluation(row) for row in decision.aggregation.strategy_evaluations)
    buy_weight = sum(row.effective_weight for row in decision.aggregation.strategy_evaluations if row.signal == "BUY")
    sell_weight = sum(row.effective_weight for row in decision.aggregation.strategy_evaluations if row.signal == "SELL")
    signal = _legacy_signal(decision.aggregation.post_local_gate_decision)
    label = _legacy_label(signal, decision.aggregation.normalized_net_score)
    sizing = _legacy_sizing_from_decision(decision, request)
    return WcaEvaluateResponse(
        configurationVersion=decision.configuration_version,
        engineVersion=WCA_PRODUCTION_PIPELINE_VERSION,
        baseWeights={row.strategy: row.base_weight for row in evaluations},
        effectiveWeights={row.strategy: row.effective_weight for row in evaluations},
        strategyEvaluations=evaluations,
        buyScore=round(decision.aggregation.buy_score, 4),
        sellScore=round(decision.aggregation.sell_score, 4),
        netScore=round(decision.aggregation.net_score, 4),
        activeWeight=round(decision.aggregation.active_weight, 4),
        normalizedNetScore=round(decision.aggregation.normalized_net_score, 4),
        activeStrategyCount=decision.aggregation.active_strategy_count,
        buyWeight=round(buy_weight, 4),
        sellWeight=round(sell_weight, 4),
        buyAgreement=round(decision.aggregation.buy_agreement, 4),
        sellAgreement=round(decision.aggregation.sell_agreement, 4),
        buyAverageConfidence=round(decision.aggregation.buy_average_confidence, 4),
        sellAverageConfidence=round(decision.aggregation.sell_average_confidence, 4),
        rawDecision=label,
        rawSignal=signal,
        localGateResult=tuple(_legacy_filter_from_gate(gate) for gate in decision.local_gates),
        effectiveDecision=label,
        signal=signal,
        sizingResult=sizing,
        proposedOrder=decision.proposed_order,
        reasonCodes=decision.reason_codes,
        decision=decision,
    )


def _legacy_signal_from_evaluation(evaluation) -> WcaLegacyStrategySignal:
    signal = "Buy" if evaluation.signal == "BUY" else "Sell" if evaluation.signal == "SELL" else "Hold"
    direction = 1 if signal == "Buy" else -1 if signal == "Sell" else 0
    return WcaLegacyStrategySignal(
        key=evaluation.strategy_id,
        strategy=evaluation.strategy_id,
        name=evaluation.name,
        family="wca",
        signal=signal,
        confidence=evaluation.confidence,
        baseWeight=evaluation.base_weight,
        weightMultiplier=evaluation.effective_weight / evaluation.base_weight if evaluation.base_weight else 1,
        effectiveWeight=evaluation.effective_weight,
        direction=direction,
        reason=";".join(evaluation.reason_codes),
    )


def _legacy_filter_from_gate(gate) -> WcaLegacyHardFilter:
    status = "fail" if gate.blocks_entry else "pass" if gate.status == "PASS" else str(gate.status).lower()
    return WcaLegacyHardFilter(label=gate.gate_id, status=status, detail=gate.explanation or gate.detail)


def _legacy_sizing_from_decision(decision, request: WcaEvaluateRequest) -> WcaLegacySizingResult:
    sizing = decision.sizing
    account_equity = request.sizing_inputs.account_equity if request.sizing_inputs else request.trading_settings.starting_capital
    return WcaLegacySizingResult(
        signalStrength=abs(decision.aggregation.normalized_net_score),
        sizeMultiplier=max(0.0, min(1.0, abs(decision.aggregation.normalized_net_score))),
        riskDollars=sizing.risk_dollars,
        stopDistance=sizing.stop_distance,
        sharesByRisk=sizing.shares_by_risk,
        sharesByOrder=sizing.shares_by_order,
        sharesByCapital=sizing.shares_by_capital,
        sharesByBuyingPower=sizing.shares_by_buying_power,
        sharesByLiquidity=sizing.shares_by_liquidity,
        finalQuantity=sizing.final_quantity,
        availableBuyingPower=account_equity,
        accountEquity=account_equity,
        maxPositionDollars=account_equity * (request.trading_settings.max_position_percent / 100.0),
        currentPositionValue=request.sizing_inputs.current_position_value if request.sizing_inputs else 0,
        limitingFactor=sizing.limiting_factor,
        blockedReason=sizing.blocked_reason,
    )


def _legacy_signal(side) -> str:
    value = side.value if hasattr(side, "value") else str(side)
    return "Buy" if value == "BUY" else "Sell" if value == "SELL" else "Hold"


def _legacy_label(signal: str, score: float) -> str:
    if signal == "Buy" and score >= 0.65:
        return "Strong Buy"
    if signal == "Sell" and score <= -0.65:
        return "Strong Sell"
    return signal


def _json_payload(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        import json

        return json.loads(str(value))
    except Exception:
        return {}


def _status_dataclass_payload(value: Any) -> dict[str, Any]:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value or {})
    return _redact_status_payload(payload)


def _status_timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _authoritative_status_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _rollout_blockers(rollout: dict[str, Any], rollout_stage: str) -> tuple[str, ...]:
    phases = rollout.get("stages") or rollout.get("phases") or ()
    for phase in phases:
        if str(phase.get("phase") or "") != rollout_stage:
            continue
        if str(phase.get("permission") or "").lower() == "enabled":
            return ()
        return tuple(str(code) for code in (phase.get("reason_codes") or phase.get("reasonCodes") or ()))
    return tuple(str(code) for code in (rollout.get("reason_codes") or rollout.get("reasonCodes") or ()))


def _active_circuit_breakers(
    *,
    runtime_control: dict[str, Any],
    runtime_health: dict[str, Any],
    position: dict[str, Any],
    daily_state: dict[str, Any],
) -> dict[str, bool]:
    return {
        "runtimeKillSwitch": bool(runtime_control.get("killSwitchOpen")),
        "wcaRuntimeHealthCircuitBreaker": bool(runtime_health.get("circuit_breaker_open") or runtime_health.get("circuitBreakerOpen")),
        "wcaPositionCircuitBreaker": str(daily_state.get("circuit_breaker_state") or "").lower() in {"open", "tripped", "critical"},
        "unprotectedPosition": bool(runtime_health.get("unprotected_position") or runtime_health.get("unprotectedPosition")),
    }


def _active_entry_block_reasons(
    *,
    runtime_control: dict[str, Any],
    runtime_health: dict[str, Any],
    session_reason_codes: tuple[str, ...],
    broker_reason_codes: tuple[str, ...],
    rollout_blockers: tuple[str, ...],
    reconciliation: dict[str, Any],
    circuit_breakers: dict[str, bool],
    paper_requested: bool,
    paper_ready: bool,
    runtime_process_status: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if paper_ready:
        return ()
    if not paper_requested:
        reasons.append("wca.status.paper_not_requested")
    if runtime_process_status != "running":
        reasons.append(f"wca.status.runtime_process_{runtime_process_status}")
    reasons.extend(str(code) for code in runtime_control.get("reasonCodes") or runtime_control.get("reason_codes") or ())
    reasons.extend(str(code) for code in runtime_health.get("reason_codes") or runtime_health.get("reasonCodes") or ())
    reasons.extend(code for code in session_reason_codes if code != "wca.session.entry_window_open")
    reasons.extend(code for code in broker_reason_codes if code != "wca.paper_account.verified")
    reasons.extend(rollout_blockers)
    if reconciliation.get("status") == "not_run":
        reasons.append("wca.status.reconciliation_not_run")
    for breaker_name, active in circuit_breakers.items():
        if active:
            reasons.append(f"wca.status.circuit_breaker.{breaker_name}")
    return tuple(dict.fromkeys(reasons))


def _wca_readiness_state(
    *,
    paper_requested: bool,
    paper_ready: bool,
    rollout_stage: str,
    runtime_process_status: str,
    runtime_health: dict[str, Any],
    active_block_reasons: tuple[str, ...],
    active_circuit_breakers: dict[str, bool],
    open_quantity: int,
) -> str:
    critical_reasons = tuple(str(code) for code in runtime_health.get("reason_codes") or runtime_health.get("reasonCodes") or ())
    if any(active_circuit_breakers.get(name) for name in ("runtimeKillSwitch", "wcaRuntimeHealthCircuitBreaker", "wcaPositionCircuitBreaker", "unprotectedPosition")):
        return "CRITICAL"
    if str(runtime_health.get("status") or "").lower() == "critical" or any("critical" in reason for reason in critical_reasons):
        return "CRITICAL"
    if not paper_requested:
        return "OFF"
    if runtime_process_status == "not_started":
        return "STARTING"
    if open_quantity or str(runtime_health.get("status") or "").lower() == "protective_only":
        if active_block_reasons:
            return "PROTECTIVE_ONLY"
    if paper_ready and rollout_stage == "LIMITED_AUTOMATIC_PAPER":
        return "LIMITED_AUTOMATIC_PAPER_READY"
    if paper_ready and rollout_stage == "AUTOMATIC_PAPER":
        return "AUTOMATIC_PAPER_READY"
    return "BLOCKED"


_SENSITIVE_STATUS_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "authorization",
    "password",
    "credential",
    "auth_header",
    "key_id",
)


def _redact_status_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_STATUS_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_status_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_status_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_status_payload(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _optional_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return bool(value)
    return None


def _row_payload(row: Any) -> dict[str, Any]:
    payload = _json_payload(row["payload_json"] if "payload_json" in row.keys() else None)
    base = {key: row[key] for key in row.keys() if key != "payload_json"}
    return {**base, "payload": payload}


def _catalog_row(row: Any) -> dict[str, Any]:
    return {key: (value.value if hasattr(value, "value") else value) for key, value in row.__dict__.items()}
