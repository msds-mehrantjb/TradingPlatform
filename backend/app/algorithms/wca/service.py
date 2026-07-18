"""WCA service boundary."""

from __future__ import annotations

from datetime import timezone
from typing import Any

from backend.app.algorithms.wca import WCA_PACKAGE_VERSION
from backend.app.algorithms.wca.configuration import WCA_CONFIGURATION_VERSION, default_baseline_settings
from backend.app.algorithms.wca.backtest.engine import run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    BacktestResult,
    WcaBacktestRequest,
    WcaBacktestSuiteResult,
    WcaBrokerReconciliationResult,
    WcaCandle,
    WcaDecisionSettings,
    WcaEvaluateRequest,
    WcaEvaluateResponse,
    WcaMarketSnapshot,
    WcaOrderStatus,
    WcaPaperExecutionRequest,
    WcaPaperExecutionResult,
    WcaPaperStabilityValidationRequest,
    WcaPaperStabilityValidationResult,
    WcaQuote,
    WcaShadowComparisonEvidence,
    WcaTradingSettings,
)
from backend.app.algorithms.wca.engine import WCA_ENGINE_VERSION, base_weight_map, evaluate_wca_legacy
from backend.app.algorithms.wca.execution_pipeline import WCA_EXECUTION_PIPELINE_VERSION, WcaExecutionPipelineInput, run_wca_execution_pipeline
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition
from backend.app.algorithms.wca.final_acceptance import build_wca_final_acceptance_report
from backend.app.algorithms.wca.broker_reconciliation import WcaPaperBrokerReconciliationClient, reconcile_wca_broker
from backend.app.algorithms.wca.order_validation import WcaOrderValidationContext, apply_wca_final_order_validation, drop_wca_order
from backend.app.algorithms.wca.repository import WcaRepository, WcaSqliteRepository
from backend.app.algorithms.wca.rollout import paper_execution_allowed, wca_rollout_status
from backend.app.algorithms.wca.paper_stability import validate_wca_paper_stability
from backend.app.algorithms.wca.shadow_comparison import WcaShadowComparisonTolerance, run_wca_shadow_comparison
from backend.app.algorithms.wca.sizing import WcaManualSizingOverride
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.app.execution.idempotency import idempotency_key


class WcaService:
    version = WCA_PACKAGE_VERSION
    configuration_version = WCA_CONFIGURATION_VERSION

    def __init__(self, repository: WcaRepository | None = None) -> None:
        self._decision_settings = WcaDecisionSettings()
        self._trading_settings = WcaTradingSettings(
            baseRiskPercent=1,
            maxPositionPercent=10,
            maxDailyTrades=5,
            maxSpreadPercent=0.1,
            maxParticipationPercent=1,
        )
        self._backtest_results: dict[str, BacktestResult] = {}
        self._backtest_suites: dict[str, WcaBacktestSuiteResult] = {}
        self._repository = repository or WcaSqliteRepository()
        self._repository.initialize_defaults(
            symbol="SPY",
            configuration=self.configuration(),
            weight_snapshot=baseline_weight_snapshot(),
            engine_version=WCA_ENGINE_VERSION,
        )

    def status(self) -> dict[str, Any]:
        persistence = self._repository.table_counts()
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "serviceVersion": self.version,
            "engineVersion": WCA_ENGINE_VERSION,
            "executionPipelineVersion": WCA_EXECUTION_PIPELINE_VERSION,
            "configurationVersion": self.configuration_version,
            "status": "ready",
            "mode": "backend_v2_active_paper_recommendation_only",
            "strategyCount": len(WCA_STRATEGY_REGISTRY),
            "paperOnly": True,
            "rollout": wca_rollout_status(),
            "finalAcceptance": build_wca_final_acceptance_report(),
            "persistence": {
                "backendAuthoritative": True,
                "migrationVersion": persistence.migration_version,
                "tableCounts": persistence.table_counts,
            },
            "reasonCodes": ("wca.backend_v2.active", "wca.paper_execution.disabled"),
        }

    def baseline_settings(self) -> dict[str, Any]:
        return default_baseline_settings().model_dump(mode="json")

    def configuration(self) -> dict[str, Any]:
        return {
            "algorithmId": WCA_ALGORITHM_ID,
            "configurationVersion": self.configuration_version,
            "engineVersion": WCA_ENGINE_VERSION,
            "decisionSettings": self._decision_settings.model_dump(mode="json", by_alias=True),
            "tradingSettings": self._trading_settings.model_dump(mode="json", by_alias=True),
            "baseWeights": base_weight_map(),
            "strategyCount": len(WCA_STRATEGY_REGISTRY),
            "paperOnly": True,
            "rollout": wca_rollout_status(),
        }

    def update_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "decisionSettings" in payload or "decision_settings" in payload:
            self._decision_settings = WcaDecisionSettings.model_validate(payload.get("decisionSettings") or payload.get("decision_settings"))
        if "tradingSettings" in payload or "trading_settings" in payload:
            self._trading_settings = WcaTradingSettings.model_validate(payload.get("tradingSettings") or payload.get("trading_settings"))
        configuration = self.configuration()
        self._repository.save_configuration(configuration, symbol="SPY", engine_version=WCA_ENGINE_VERSION)
        return configuration

    def evaluate(self, request: WcaEvaluateRequest) -> WcaEvaluateResponse:
        return evaluate_wca_legacy(request)

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
        )

    def execute_paper(self, request: WcaPaperExecutionRequest) -> WcaPaperExecutionResult:
        snapshot = _paper_snapshot(request)
        open_position = _paper_open_position(request)
        identity = _paper_identity_part(request.account_id)
        pipeline = run_wca_execution_pipeline(
            WcaExecutionPipelineInput(
                run_id=request.run_id,
                decision_id=f"{request.run_id}-{identity}-{request.mode}-decision-{snapshot.decision_timestamp.isoformat()}",
                order_intent_id=f"{request.run_id}-{identity}-{request.mode}-intent-{snapshot.decision_timestamp.isoformat()}",
                snapshot=snapshot,
                configuration_version=request.configuration_version,
                baseline=default_baseline_settings(),
                weight_snapshot=self._repository.read_active_weights() or baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
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
                estimated_cost_per_share=request.estimated_cost_per_share,
                estimated_expectancy_after_costs=request.estimated_expectancy_after_costs,
                manual_sizing_override=_manual_override(request),
                emergency_exit=request.emergency_exit,
            )
        )
        proposed = pipeline.decision.proposed_order
        automatic_blocked = request.mode == "automatic" and not paper_execution_allowed()
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
            status = WcaOrderStatus.ACCEPTED_FOR_PAPER.value
            submitted = False
            reasons = ("wca.paper.execution_path_completed", f"wca.paper.mode.{request.mode}")
            proposed = proposed.model_copy(update={"status": WcaOrderStatus.ACCEPTED_FOR_PAPER})
            decision = pipeline.decision.model_copy(update={"proposed_order": proposed, "reason_codes": (*pipeline.decision.reason_codes, *reasons)})
            decision = apply_wca_final_order_validation(decision, _paper_order_validation_context(request, snapshot))
            if decision.proposed_order is None:
                status = "NO_ACTION"
                submitted = False
                reasons = (*reasons, "wca.paper.final_order_validation_failed")
                decision = decision.model_copy(update={"reason_codes": (*decision.reason_codes, "wca.paper.final_order_validation_failed")})
            else:
                key = _paper_order_idempotency_key(request.account_id, decision)
                proposed = decision.proposed_order.model_copy(
                    update={
                        "idempotency_key": key,
                        "account_id": request.account_id,
                        "reason_codes": (*decision.proposed_order.reason_codes, "wca.paper.idempotency_key_generated"),
                    }
                )
                decision = decision.model_copy(update={"proposed_order": proposed})
                reservation = self._repository.reserve_order_intent(
                    decision,
                    run_id=request.run_id,
                    account_id=request.account_id,
                    idempotency_key=key,
                )
                proposed = reservation.proposed_order
                if reservation.created:
                    submitted = True
                    reasons = (*reasons, "wca.paper.intent_persisted_before_submission")
                    decision = decision.model_copy(
                        update={
                            "proposed_order": proposed,
                            "reason_codes": (*decision.reason_codes, "wca.paper.intent_persisted_before_submission"),
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
        result = run_wca_backtest(request)
        self._backtest_results[result.run_configuration.run_id] = result
        self._repository.save_backtest_result(result)
        return result

    def run_backtest_modes(self, request: WcaBacktestRequest) -> WcaBacktestSuiteResult:
        result = run_wca_backtest_modes(request)
        self._backtest_suites[result.suite_id] = result
        for mode_result in (result.smoke, *result.rolling, result.full_history, result.walk_forward, result.holdout):
            self._backtest_results[mode_result.result.run_configuration.run_id] = mode_result.result
            self._repository.save_backtest_result(mode_result.result)
        return result

    def backtest_status(self, run_id: str) -> dict[str, Any]:
        if run_id in self._backtest_results or self._repository.load_backtest_result(run_id) is not None:
            return {"runId": run_id, "status": "complete", "backendAuthoritative": True}
        if run_id in self._backtest_suites:
            return {"runId": run_id, "status": "complete", "backendAuthoritative": True, "suite": True}
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


def _paper_snapshot(request: WcaPaperExecutionRequest) -> WcaMarketSnapshot:
    candles = tuple(sorted(request.candles, key=lambda candle: candle.timestamp))
    latest = candles[-1]
    quote_by_time = {quote.timestamp: quote for quote in request.quotes}
    quote = quote_by_time.get(latest.timestamp) or _synthetic_quote(latest)
    return WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=candles,
        quote=quote,
        data_ready=True,
        source="wca_paper_execution",
        reason_codes=("wca.paper.completed_bar",),
    )


def _synthetic_quote(candle: WcaCandle) -> WcaQuote:
    spread = max(0.01, candle.close * 0.0002)
    return WcaQuote(timestamp=candle.timestamp, bid=max(0.01, candle.close - spread / 2), ask=candle.close + spread / 2)


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


def _paper_order_validation_context(request: WcaPaperExecutionRequest, snapshot: WcaMarketSnapshot) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=snapshot.decision_timestamp,
        paper_only_mode=True,
        current_position_quantity=request.current_position_quantity,
        current_position_side=request.current_position_side,
        allow_position_increase=request.allow_position_increase,
        position_owned_by_wca=True,
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
