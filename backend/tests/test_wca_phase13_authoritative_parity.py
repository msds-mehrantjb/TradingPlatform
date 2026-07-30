from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from backend.app.algorithms.wca.backtest.engine import run_wca_backtest_modes
from backend.app.algorithms.wca.backtest.execution import WCA_BACKTEST_EXECUTION_SIMULATION_VERSION
from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import WcaDecision, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.execution_pipeline import (
    WCA_EXECUTION_PIPELINE_MODULES,
    WcaExecutionPipelineInput,
    run_wca_backtest_pipeline_adapter,
    run_wca_execution_pipeline,
    run_wca_paper_pipeline_adapter,
    run_wca_replay_pipeline_adapter,
)
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_step14_15_backend_backtest import backtest_request, multi_session_candles
from backend.tests.test_wca_step5_production_pipeline import fake_voters, market_snapshot


EXPECTED_GOLDEN_COMPONENTS = {
    "strategy_outputs": (
        {"strategy_id": "C1", "signal": "BUY", "calibrated_confidence": 0.6, "effective_weight": 1.0},
        {"strategy_id": "C7", "signal": "BUY", "calibrated_confidence": 0.6, "effective_weight": 1.0},
        {"strategy_id": "C8", "signal": "BUY", "calibrated_confidence": 0.6, "effective_weight": 1.0},
    ),
    "aggregation": {
        "post_local_gate_decision": "HOLD",
        "buy_score": 1.8,
        "sell_score": 0.0,
        "active_strategy_count": 3,
        "normalized_net_score": 0.6,
    },
    "sizing": {
        "final_quantity": 0,
        "limiting_factor": "non_directional",
        "entry_price": 102.07,
        "stop_distance": 0.0,
    },
    "final_decision": {"side": "HOLD", "order_quantity": 0, "has_order": False},
}

PHASE13_SURFACES: tuple[tuple[str, WcaRuntimeMode, Callable[[WcaExecutionPipelineInput], WcaDecision]], ...] = (
    ("historical_backtest", WcaRuntimeMode.HISTORICAL_REPLAY, lambda command: run_wca_backtest_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("walk_forward_test", WcaRuntimeMode.HISTORICAL_REPLAY, lambda command: run_wca_backtest_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("holdout_test", WcaRuntimeMode.HISTORICAL_REPLAY, lambda command: run_wca_backtest_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("historical_replay", WcaRuntimeMode.HISTORICAL_REPLAY, lambda command: run_wca_replay_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("shadow_runtime", WcaRuntimeMode.SHADOW, lambda command: run_wca_execution_pipeline(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("paper_recommendation", WcaRuntimeMode.PAPER_RECOMMENDATION, lambda command: run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("manual_paper", WcaRuntimeMode.MANUAL_PAPER, lambda command: run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("limited_automatic_paper", WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, lambda command: run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
    ("automatic_paper", WcaRuntimeMode.AUTOMATIC_PAPER, lambda command: run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision),
)


def test_golden_fixture_has_zero_unexplained_decision_mismatches_across_all_phase13_surfaces() -> None:
    fixture = phase13_golden_fixture()
    command = fixture["command"]
    decisions = {
        surface: runner(replace(command, runtime_mode=mode))
        for surface, mode, runner in PHASE13_SURFACES
    }
    baseline_payload = _parity_payload(decisions["historical_backtest"])
    mismatches = {
        surface: _payload_diff(baseline_payload, _parity_payload(decision))
        for surface, decision in decisions.items()
        if _parity_payload(decision) != baseline_payload
    }

    assert mismatches == {}
    assert fixture["market_snapshot"].symbol == "SPY"
    assert fixture["wca_inventory_snapshot"] == {
        "algorithm_id": "wca",
        "broker_account_id": "phase13-paper-account",
        "position": "FLAT",
        "quantity": 0,
        "trades_today": 0,
        "realized_daily_loss": 0.0,
        "reserved_risk": 0.0,
    }
    assert fixture["configuration"].configuration_version == command.configuration_version
    assert fixture["dynamic_profile"] == decisions["historical_backtest"].effective_settings.dynamic_profile_name
    assert fixture["weights"].weight_version == "phase13.weights.v1"
    assert fixture["calibration"] == ()
    assert _golden_components(decisions["historical_backtest"]) == EXPECTED_GOLDEN_COMPONENTS


def test_research_backtest_modes_preserve_integrity_and_production_engine_contracts() -> None:
    suite = run_wca_backtest_modes(
        backtest_request(candles=multi_session_candles(30)),
        configuration=default_wca_configuration(),
    )
    mode_results = (suite.full_history, suite.walk_forward, suite.holdout)

    for mode_result in mode_results:
        metrics = mode_result.result.metrics
        assert metrics["usesBackendEngine"] is True
        assert metrics["calledProductionModules"] == WCA_EXECUTION_PIPELINE_MODULES
        assert metrics["fillRule"] == "signal on bar t fills no earlier than bar t+1 open"
        assert "fill_no_earlier_than_bar_t_plus_1_open" in metrics["eventOrder"]
        assert "evaluate_after_bar_t_close" in metrics["eventOrder"]
        assert metrics["openPositionDrawdownIncluded"] is True
        assert metrics["executionSimulationVersion"] == WCA_BACKTEST_EXECUTION_SIMULATION_VERSION
        assert metrics["pinnedVersions"]["configurationVersion"] == mode_result.result.run_configuration.configuration_version
        assert metrics["pinnedVersions"]["executionSimulationVersion"] == WCA_BACKTEST_EXECUTION_SIMULATION_VERSION

    assert suite.walk_forward.result.metrics["trainingCalibrationSeparatedFromEvaluation"] is True
    assert suite.walk_forward.result.metrics["usesOnlyPriorWindowInformation"] is True
    assert suite.holdout.result.metrics["trainingCalibrationSeparatedFromEvaluation"] is True
    assert all(comparison.metrics["identicalDataset"] for comparison in suite.comparisons)
    assert all(comparison.metrics["identicalExecutionAssumptions"] for comparison in suite.comparisons)


def test_paper_modules_do_not_host_a_second_strategy_engine() -> None:
    root = Path(__file__).resolve().parents[2] / "backend" / "app" / "algorithms" / "wca"
    paper_sources = (
        root / "paper_broker.py",
        root / "paper_account.py",
        root / "alpaca_paper_broker.py",
        root / "execution_pipeline.py",
    )
    forbidden = (
        "def evaluate_strategy",
        "class PaperStrategy",
        "WCA_PRIMARY_VOTERS =",
        "from backend.app.algorithms.wca.strategies.primary_voters import",
    )

    for source_path in paper_sources:
        source = source_path.read_text(encoding="utf-8")
        if source_path.name == "execution_pipeline.py":
            assert "from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS" in source
            continue
        assert all(token not in source for token in forbidden)


def phase13_golden_fixture() -> dict[str, object]:
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    weights = baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="phase13.weights.v1")
    inventory_snapshot = {
        "algorithm_id": "wca",
        "broker_account_id": "phase13-paper-account",
        "position": "FLAT",
        "quantity": 0,
        "trades_today": 0,
        "realized_daily_loss": 0.0,
        "reserved_risk": 0.0,
    }
    command = WcaExecutionPipelineInput(
        run_id="phase13-golden-run",
        decision_id="phase13-golden-decision",
        order_intent_id="phase13-golden-intent",
        snapshot=snapshot,
        configuration_version=configuration.configuration_version,
        configuration=configuration,
        runtime_mode=WcaRuntimeMode.PAPER_RECOMMENDATION,
        account_id=inventory_snapshot["broker_account_id"],
        weight_snapshot=weights,
        calibration_tables=(),
        global_gate_quantity_cap=1000,
        approved_risk_budget=1000,
        account_equity=100_000,
        available_buying_power=100_000,
        trades_today=inventory_snapshot["trades_today"],
        realized_daily_loss=inventory_snapshot["realized_daily_loss"],
        remaining_allocated_risk_budget=1000,
    )
    decision = run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision
    return {
        "market_snapshot": snapshot,
        "wca_inventory_snapshot": inventory_snapshot,
        "configuration": configuration,
        "dynamic_profile": decision.effective_settings.dynamic_profile_name if decision.effective_settings else "",
        "weights": weights,
        "calibration": command.calibration_tables,
        "expected": EXPECTED_GOLDEN_COMPONENTS,
        "command": command,
    }


def _golden_components(decision: WcaDecision) -> dict[str, object]:
    return {
        "strategy_outputs": tuple(
            {
                "strategy_id": row.strategy_id,
                "signal": row.signal.value if hasattr(row.signal, "value") else row.signal,
                "calibrated_confidence": row.calibrated_confidence,
                "effective_weight": row.effective_weight,
            }
            for row in decision.aggregation.strategy_evaluations
        ),
        "aggregation": {
            "post_local_gate_decision": decision.aggregation.post_local_gate_decision.value
            if hasattr(decision.aggregation.post_local_gate_decision, "value")
            else decision.aggregation.post_local_gate_decision,
            "buy_score": decision.aggregation.buy_score,
            "sell_score": decision.aggregation.sell_score,
            "active_strategy_count": decision.aggregation.active_strategy_count,
            "normalized_net_score": decision.aggregation.normalized_net_score,
        },
        "sizing": {
            "final_quantity": decision.sizing.final_quantity,
            "limiting_factor": decision.sizing.limiting_factor,
            "entry_price": decision.sizing.entry_price,
            "stop_distance": decision.sizing.stop_distance,
        },
        "final_decision": {
            "side": decision.aggregation.post_local_gate_decision.value
            if hasattr(decision.aggregation.post_local_gate_decision, "value")
            else decision.aggregation.post_local_gate_decision,
            "order_quantity": decision.proposed_order.quantity if decision.proposed_order is not None else 0,
            "has_order": decision.proposed_order is not None,
        },
    }


def _parity_payload(decision: WcaDecision) -> dict:
    payload = decision.model_dump(mode="json")
    payload.pop("runtime_mode", None)
    return payload


def _payload_diff(expected: dict, actual: dict) -> dict[str, tuple[object, object]]:
    keys = set(expected) | set(actual)
    return {key: (expected.get(key), actual.get(key)) for key in sorted(keys) if expected.get(key) != actual.get(key)}
