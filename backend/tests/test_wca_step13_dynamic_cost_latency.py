import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from backend.app.algorithms.wca.configuration import WcaConfiguration, default_wca_configuration
from backend.app.algorithms.wca.contracts import WcaEvaluationStatus, WcaMarketStatus, WcaOrderStatus
from backend.app.algorithms.wca.cost_model import WCA_COST_MODEL_ADAPTER_VERSION
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_backtest_pipeline_adapter, run_wca_paper_pipeline_adapter, run_wca_replay_pipeline_adapter
from backend.app.algorithms.wca.latency import WCA_LATENCY_OBSERVABILITY_VERSION
from backend.app.algorithms.wca.paper_broker import WcaDeterministicPaperBroker, WcaPaperBrokerFill, WcaPaperBrokerOutboxAdapter
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_step10_paper_broker_outbox import repository_for_step10, reserve
from backend.tests.test_wca_step5_production_pipeline import fake_voters, market_snapshot
from backend.app.algorithms.wca.contracts import WcaSide


def test_dynamic_profile_starts_from_baseline_applies_caps_and_rejects_stale_previous_profile() -> None:
    configuration = _configuration(
        dynamic_profile={
            "maximum_defensive_risk_multiplier": 0.4,
            "maximum_defensive_quantity_multiplier": 0.5,
            "minimum_profile_hold_seconds": 300,
            "overlay_ttl_seconds": 120,
        }
    )
    baseline = configuration.to_baseline_settings()
    start = market_snapshot().decision_timestamp
    defensive = resolve_dynamic_profile(
        baseline=baseline,
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE, volatility="high"),
        calculation_timestamp=start,
        config=WcaDynamicProfileConfig(
            maximum_defensive_risk_multiplier=configuration.dynamic_profile.maximum_defensive_risk_multiplier,
            maximum_defensive_quantity_multiplier=configuration.dynamic_profile.maximum_defensive_quantity_multiplier,
            minimum_profile_hold_seconds=configuration.dynamic_profile.minimum_profile_hold_seconds,
            profile_ttl_seconds=configuration.dynamic_profile.overlay_ttl_seconds,
        ),
    )

    assert defensive.effective_settings.baseline == baseline
    assert defensive.effective_settings.risk_multiplier <= 0.4
    assert defensive.effective_settings.quantity_multiplier <= 0.5
    assert defensive.effective_settings.final_risk_percent <= baseline.base_risk_percent

    released = resolve_dynamic_profile(
        baseline=baseline,
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
        calculation_timestamp=start + timedelta(seconds=121),
        previous_profile=defensive,
        config=WcaDynamicProfileConfig(minimum_profile_hold_seconds=300, profile_ttl_seconds=120),
    )

    assert released.profile_id == "baseline"
    assert "wca.dynamic_profile.previous_expired" in released.reason_codes


def test_paper_replay_and_backtest_use_the_same_dynamic_and_cost_resolver() -> None:
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    command = WcaExecutionPipelineInput(
        run_id="step13-parity",
        decision_id="step13-decision",
        order_intent_id="step13-intent",
        snapshot=snapshot,
        configuration_version=configuration.configuration_version,
        configuration=configuration,
        weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="step13.weights"),
        global_gate_quantity_cap=1000,
        approved_risk_budget=1000,
    )

    paper = run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision
    replay = run_wca_replay_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision
    backtest = run_wca_backtest_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision

    assert paper.effective_settings == replay.effective_settings == backtest.effective_settings
    assert paper.cost_estimate == replay.cost_estimate == backtest.cost_estimate
    assert paper.decision_hash == replay.decision_hash == backtest.decision_hash
    assert paper.cost_estimate is not None
    assert WCA_COST_MODEL_ADAPTER_VERSION in paper.cost_estimate.reason_codes
    assert paper.latency is not None
    assert WCA_LATENCY_OBSERVABILITY_VERSION in paper.latency.metrics.reason_codes


def test_conservative_cost_model_blocks_entries_below_minimum_net_edge() -> None:
    configuration = _configuration(execution={"minimum_net_edge_per_share": 100.0, "uncertainty_buffer_per_share": 1.0})
    snapshot = market_snapshot()
    decision = run_wca_paper_pipeline_adapter(
        WcaExecutionPipelineInput(
            run_id="step13-cost-block",
            decision_id="step13-cost-block-decision",
            order_intent_id="step13-cost-block-intent",
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
            global_gate_quantity_cap=1000,
            approved_risk_budget=1000,
        ),
        voters=fake_voters(WcaSide.BUY),
    ).decision

    assert decision.cost_estimate is not None
    assert decision.cost_estimate.entry_allowed is False
    assert decision.proposed_order is None
    assert decision.sizing.final_quantity == 0
    assert "wca.cost_model.entry_edge_not_met" in decision.reason_codes
    assert "wca.cost_model.entry_edge_not_met" in decision.cost_estimate.reason_codes


def test_paper_broker_persists_broker_latency_and_fill_quality() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="step13-latency")
    fill = WcaPaperBrokerFill(
        fill_id="step13-latency-fill",
        client_order_id=request.client_order_id,
        broker_order_id=f"paper-{request.client_order_id}",
        filled_quantity=request.quantity,
        remaining_quantity=0,
        average_fill_price=request.limit_price,
    )

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, WcaDeterministicPaperBroker(fill=fill), owner_id="step10")

    assert result.state == WcaOrderStatus.FILLED.value
    with sqlite3.connect(repository.path) as conn:
        outbox_payload = conn.execute("SELECT response_payload_json FROM wca_execution_outbox WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()[0]
        broker_payload = conn.execute("SELECT payload_json FROM wca_broker_orders WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()[0]
    outbox = json.loads(outbox_payload)
    broker = json.loads(broker_payload)

    assert outbox["latency"]["timestamps"]["broker_request"] is not None
    assert outbox["latency"]["timestamps"]["broker_acknowledgement"] is not None
    assert outbox["latency"]["timestamps"]["first_fill"] is not None
    assert outbox["latency"]["metrics"]["fill_quality"] == "at_limit"
    assert "latency" in broker


def test_no_sibling_algorithm_imports_or_mutates_wca_dynamic_overlays() -> None:
    algorithms_root = Path("backend/app/algorithms")
    offenders: list[str] = []
    for path in algorithms_root.rglob("*.py"):
        normalized = path.as_posix()
        if "/wca/" in normalized:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in ("WcaDynamicProfileSettings", "resolve_dynamic_profile", "wca.dynamic_profile")):
            offenders.append(normalized)

    assert offenders == []


def _configuration(*, execution: dict | None = None, dynamic_profile: dict | None = None) -> WcaConfiguration:
    configuration = default_wca_configuration()
    payload = configuration.model_dump(mode="python")
    if execution:
        payload["execution"] = configuration.execution.model_copy(update=execution).model_dump(mode="python")
    if dynamic_profile:
        payload["dynamic_profile"] = configuration.dynamic_profile.model_copy(update=dynamic_profile).model_dump(mode="python")
    payload["content_hash"] = ""
    return WcaConfiguration.model_validate(payload)
