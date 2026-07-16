from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import backend.app.algorithms.wca.service as wca_service_module
from backend.app.algorithms.wca.aggregation import aggregate_wca
from backend.app.algorithms.wca.contracts import (
    WcaBaselineSettings,
    WcaCandle,
    WcaDecision,
    WcaDynamicProfile,
    WcaEffectiveSettings,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaOrderStatus,
    WcaPaperExecutionRequest,
    WcaSide,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineResult
from backend.app.algorithms.wca.order_validation import WCA_ORDER_VALIDATION_PASSED, WcaOrderValidationContext, apply_wca_final_order_validation
from backend.app.algorithms.wca.repository import WcaOrderIntentReservation, WcaPersistenceSummary
from backend.app.algorithms.wca.service import WcaService
from backend.app.algorithms.wca.sizing import WcaSizingContext, size_wca_order
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.app.main import app


def test_manual_and_automatic_paper_actions_use_shared_execution_pipeline() -> None:
    repository = MemoryWcaRepository()
    service = WcaService(repository=repository)
    request = WcaPaperExecutionRequest(candles=candles(), runId="paper-parity")

    manual = service.execute_manual_paper(request)
    automatic = service.execute_automatic_paper(request)

    for result in (manual, automatic):
        assert "strategy_registry" in result.called_production_modules
        assert "confidence_calibration" in result.called_production_modules
        assert "weight_engine" in result.called_production_modules
        assert "market_status" in result.called_production_modules
        assert "dynamic_profile" in result.called_production_modules
        assert "aggregation" in result.called_production_modules
        assert "local_gates" in result.called_production_modules
        assert "sizing" in result.called_production_modules
        assert "order_proposal" in result.called_production_modules
        assert "order_validation" in result.called_production_modules
        assert "exits" in result.called_production_modules
        assert result.decision.effective_settings is not None
        assert result.decision.effective_settings.profile_version == "wca_dynamic_profile_v1"
        assert "wca.paper.uses_execution_pipeline" in result.reason_codes

    assert manual.mode == "manual"
    assert automatic.mode == "automatic"
    assert repository.decisions[manual.decision.decision_id].effective_settings == manual.decision.effective_settings
    assert repository.decisions[automatic.decision.decision_id].effective_settings == automatic.decision.effective_settings


def test_paper_execution_api_routes_to_pipeline() -> None:
    response = TestClient(app).post(
        "/api/wca/paper/manual",
        json=WcaPaperExecutionRequest(candles=candles(), runId="paper-api").model_dump(mode="json", by_alias=True),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "manual"
    assert "wca.paper.uses_execution_pipeline" in body["reason_codes"]
    assert "dynamic_profile" in body["called_production_modules"]
    assert body["decision"]["effective_settings"]["profile_version"] == "wca_dynamic_profile_v1"


def test_final_order_validation_drops_invalid_order_after_backend_adjustment() -> None:
    decision = decision_with_order()
    assert decision.proposed_order is not None
    invalid = decision.proposed_order.model_copy(update={"stop_price": decision.sizing.entry_price + 1})

    validated = apply_wca_final_order_validation(
        decision.model_copy(update={"proposed_order": invalid}),
        validation_context(decision),
    )

    assert validated.proposed_order is None
    assert validated.sizing.final_quantity == 0
    assert "wca.order_validation.invalid_price_geometry" in validated.reason_codes


def test_valid_order_is_revalidated_after_status_adjustment() -> None:
    decision = decision_with_order()
    assert decision.proposed_order is not None
    adjusted = decision.model_copy(
        update={"proposed_order": decision.proposed_order.model_copy(update={"status": WcaOrderStatus.ACCEPTED_FOR_PAPER})}
    )

    validated = apply_wca_final_order_validation(adjusted, validation_context(decision))

    assert validated.proposed_order is not None
    assert validated.proposed_order.status == WcaOrderStatus.ACCEPTED_FOR_PAPER.value
    assert WCA_ORDER_VALIDATION_PASSED in validated.proposed_order.reason_codes


def test_paper_service_revalidates_after_pipeline_and_status_adjustment(monkeypatch) -> None:
    repository = MemoryWcaRepository()
    service = WcaService(repository=repository)
    decision = decision_with_order()
    assert decision.proposed_order is not None
    invalid = decision.proposed_order.model_copy(update={"target_price": decision.sizing.entry_price - 1})
    pipeline = pipeline_result(decision.model_copy(update={"proposed_order": invalid}))

    monkeypatch.setattr(wca_service_module, "run_wca_execution_pipeline", lambda *args, **kwargs: pipeline)

    result = service.execute_manual_paper(WcaPaperExecutionRequest(candles=candles(), runId="paper-final-validation"))

    assert result.submitted is False
    assert result.proposed_order is None
    assert result.decision.sizing.final_quantity == 0
    assert "wca.paper.final_order_validation_failed" in result.reason_codes
    assert "wca.order_validation.invalid_price_geometry" in result.decision.reason_codes


def test_paper_order_submission_is_idempotent_by_persisted_intent(monkeypatch) -> None:
    repository = MemoryWcaRepository()
    service = WcaService(repository=repository)
    decision = decision_with_order()
    pipeline = pipeline_result(decision)
    request = WcaPaperExecutionRequest(candles=candles(), runId="paper-idempotent", accountId="paper-account-1")

    monkeypatch.setattr(wca_service_module, "run_wca_execution_pipeline", lambda *args, **kwargs: pipeline)

    first = service.execute_manual_paper(request)
    second = service.execute_manual_paper(request)

    assert first.submitted is True
    assert first.action_status == WcaOrderStatus.ACCEPTED_FOR_PAPER.value
    assert second.submitted is False
    assert second.action_status == "DUPLICATE_INTENT"
    assert first.idempotency_key == second.idempotency_key
    assert first.proposed_order == second.proposed_order
    assert "wca.paper.intent_persisted_before_submission" in first.reason_codes
    assert "wca.paper.duplicate_order_intent" in second.reason_codes
    assert len(repository.intents_by_key) == 1


class MemoryWcaRepository:
    def __init__(self) -> None:
        self.weights = baseline_weight_snapshot()
        self.decisions = {}
        self.backtests = {}
        self.intents_by_key = {}

    def initialize_defaults(self, *, symbol: str, configuration: dict, weight_snapshot: WcaWeightSnapshot, engine_version: str) -> None:
        self.weights = weight_snapshot

    def save_configuration(self, payload: dict, *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        return None

    def read_active_weights(self) -> WcaWeightSnapshot | None:
        return self.weights

    def write_decision_snapshot(self, decision, *, run_id: str | None = None) -> None:
        self.decisions[decision.decision_id] = decision

    def reserve_order_intent(self, decision, *, run_id: str, account_id: str, idempotency_key: str) -> WcaOrderIntentReservation:
        if decision.proposed_order is None:
            raise AssertionError("missing proposed order")
        if idempotency_key in self.intents_by_key:
            return WcaOrderIntentReservation(False, self.intents_by_key[idempotency_key], idempotency_key)
        proposed = decision.proposed_order.model_copy(update={"idempotency_key": idempotency_key, "account_id": account_id})
        self.intents_by_key[idempotency_key] = proposed
        return WcaOrderIntentReservation(True, proposed, idempotency_key)

    def save_backtest_result(self, result) -> None:
        self.backtests[result.run_configuration.run_id] = result

    def load_backtest_result(self, run_id: str):
        return self.backtests.get(run_id)

    def table_counts(self) -> WcaPersistenceSummary:
        return WcaPersistenceSummary(table_counts={})


def candles(count: int = 60) -> tuple[WcaCandle, ...]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        price = 100 + index * 0.05
        rows.append(
            WcaCandle(
                timestamp=start + timedelta(minutes=index),
                open=price,
                high=price + 0.15,
                low=price - 0.10,
                close=price + 0.08,
                volume=300_000,
            )
        )
    return tuple(rows)


def decision_with_order() -> WcaDecision:
    rows = candles()
    timestamp = rows[-1].timestamp
    settings = effective_settings()
    sized = size_wca_order(
        WcaSizingContext(
            decision_id="decision-final-validation",
            order_intent_id="intent-final-validation",
            symbol="SPY",
            side=WcaSide.BUY,
            price=100,
            atr=1,
            bid=99.95,
            ask=100.05,
            account_equity=100_000,
            available_buying_power=100_000,
            average_one_minute_volume=300_000,
            confidence_size_multiplier=1.0,
            global_gate_quantity_cap=None,
            approved_risk_budget=500,
            minimum_reward_risk=1.5,
            estimated_cost_per_share=0.0,
        ),
        settings,
    )
    assert sized.proposed_order is not None
    evaluations = tuple(strategy_evaluation(strategy_id, weight) for strategy_id, weight in (("C1", 0.2), ("C2", 0.2), ("C7", 0.2), ("C9", 0.2), ("C11", 0.2)))
    return WcaDecision(
        decision_id="decision-final-validation",
        configuration_version="test_configuration",
        weight_version="test_weights",
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        market_snapshot=WcaMarketSnapshot(
            symbol="SPY",
            data_timestamp=timestamp,
            decision_timestamp=timestamp,
            candles=rows,
            source="test",
        ),
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE, input_timestamp=timestamp),
        effective_settings=settings,
        aggregation=aggregate_wca(evaluations, effective_settings=settings),
        local_gates=(),
        sizing=sized.sizing,
        proposed_order=sized.proposed_order,
        reason_codes=("test.decision",),
    )


def effective_settings() -> WcaEffectiveSettings:
    baseline = WcaBaselineSettings(
        base_risk_percent=1.0,
        order_allocation_percent=100.0,
        max_position_percent=100.0,
        atr_stop_multiplier=1.0,
        take_profit_r=1.5,
        assumed_slippage_per_share=0.0,
        hard_max_order_allocation_percent=100.0,
        hard_max_position_percent=100.0,
    )
    return WcaEffectiveSettings(
        baseline=baseline,
        baseline_settings_version=baseline.settings_version,
        profile_version="wca_dynamic_profile_v1",
        final_risk_percent=1.0,
        final_order_allocation_percent=100.0,
        final_max_position_percent=100.0,
        final_atr_stop_multiplier=1.0,
        final_take_profit_r=1.5,
        final_assumed_slippage_per_share=0.0,
        reason_codes=("test.settings",),
    )


def strategy_evaluation(strategy_id: str, weight: float) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version="test_strategy",
        name=strategy_id,
        signal=WcaSide.BUY,
        confidence=0.8,
        raw_confidence=0.8,
        calibrated_confidence=0.8,
        direction=WcaSide.BUY,
        evidence_strength=0.8,
        base_weight=weight,
        effective_weight=weight,
        contribution=weight * 0.8,
    )


def validation_context(decision: WcaDecision) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(evaluation_timestamp=decision.decision_timestamp, paper_only_mode=True)


def pipeline_result(decision: WcaDecision) -> WcaExecutionPipelineResult:
    assert decision.effective_settings is not None
    return WcaExecutionPipelineResult(
        decision=decision,
        market_status=decision.market_status,
        dynamic_profile=WcaDynamicProfile(
            profile_id="test_profile",
            profile_version="wca_dynamic_profile_v1",
            baseline_settings_version=decision.effective_settings.baseline_settings_version,
            market_status=decision.market_status,
            active_overlays=(),
            effective_settings=decision.effective_settings,
            calculation_timestamp=decision.decision_timestamp,
            expiration_timestamp=decision.decision_timestamp + timedelta(minutes=1),
        ),
        exit_evaluation=None,
        risk_improvement_confirmations=0,
        called_production_modules=("strategy_registry", "order_validation"),
    )
