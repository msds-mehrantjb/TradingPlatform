from __future__ import annotations

import pytest

from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.execution_pipeline import (
    WcaExecutionPipelineInput,
    run_wca_execution_pipeline,
    run_wca_paper_pipeline_adapter,
    run_wca_replay_pipeline_adapter,
)
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_step5_production_pipeline import fake_voters, market_snapshot


PARITY_MODES = (
    WcaRuntimeMode.HISTORICAL_REPLAY,
    WcaRuntimeMode.SHADOW,
    WcaRuntimeMode.PAPER_RECOMMENDATION,
    WcaRuntimeMode.MANUAL_PAPER,
    WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER,
    WcaRuntimeMode.AUTOMATIC_PAPER,
)


def test_wca_runtime_mode_enum_is_strict_and_contains_no_live_mode() -> None:
    assert tuple(mode.value for mode in WcaRuntimeMode) == (
        "DISABLED",
        "HISTORICAL_REPLAY",
        "SHADOW",
        "PAPER_RECOMMENDATION",
        "MANUAL_PAPER",
        "LIMITED_AUTOMATIC_PAPER",
        "AUTOMATIC_PAPER",
    )
    assert "LIVE" not in {mode.value for mode in WcaRuntimeMode}


def test_paper_adapter_preserves_selected_paper_mode_without_downgrading_automatic() -> None:
    command = parity_command()

    for mode in (
        WcaRuntimeMode.PAPER_RECOMMENDATION,
        WcaRuntimeMode.MANUAL_PAPER,
        WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER,
        WcaRuntimeMode.AUTOMATIC_PAPER,
    ):
        decision = run_wca_paper_pipeline_adapter(command_for_mode(command, mode), voters=fake_voters(WcaSide.BUY)).decision
        assert decision.runtime_mode == mode.value


def test_paper_adapter_rejects_non_paper_modes_instead_of_silently_changing_them() -> None:
    command = parity_command()

    with pytest.raises(ValueError, match="cannot execute runtime mode SHADOW"):
        run_wca_paper_pipeline_adapter(command_for_mode(command, WcaRuntimeMode.SHADOW), voters=fake_voters(WcaSide.BUY))


def test_identical_inputs_produce_identical_authoritative_decisions_across_runtime_modes() -> None:
    command = parity_command()
    decisions = {mode: decision_for_mode(command, mode) for mode in PARITY_MODES}
    baseline = parity_payload(decisions[WcaRuntimeMode.HISTORICAL_REPLAY])

    for mode, decision in decisions.items():
        assert decision.runtime_mode == mode.value
        assert parity_payload(decision) == baseline

    execution_permission = {mode: mode in {WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, WcaRuntimeMode.AUTOMATIC_PAPER} for mode in PARITY_MODES}
    assert execution_permission == {
        WcaRuntimeMode.HISTORICAL_REPLAY: False,
        WcaRuntimeMode.SHADOW: False,
        WcaRuntimeMode.PAPER_RECOMMENDATION: False,
        WcaRuntimeMode.MANUAL_PAPER: False,
        WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER: True,
        WcaRuntimeMode.AUTOMATIC_PAPER: True,
    }


def parity_command() -> WcaExecutionPipelineInput:
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    return WcaExecutionPipelineInput(
        run_id="phase4-parity-run",
        decision_id="phase4-parity-decision",
        order_intent_id="phase4-parity-intent",
        snapshot=snapshot,
        configuration_version=configuration.configuration_version,
        configuration=configuration,
        runtime_mode=WcaRuntimeMode.MANUAL_PAPER,
        weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="phase4.weights.v1"),
        global_gate_quantity_cap=1000,
        approved_risk_budget=1000,
        account_equity=100_000,
        available_buying_power=100_000,
        trades_today=0,
        realized_daily_loss=0,
    )


def command_for_mode(command: WcaExecutionPipelineInput, mode: WcaRuntimeMode) -> WcaExecutionPipelineInput:
    return WcaExecutionPipelineInput(**{**command.__dict__, "runtime_mode": mode})


def decision_for_mode(command: WcaExecutionPipelineInput, mode: WcaRuntimeMode):
    command = command_for_mode(command, mode)
    if mode == WcaRuntimeMode.HISTORICAL_REPLAY:
        return run_wca_replay_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision
    if mode == WcaRuntimeMode.SHADOW:
        return run_wca_execution_pipeline(command, voters=fake_voters(WcaSide.BUY)).decision
    return run_wca_paper_pipeline_adapter(command, voters=fake_voters(WcaSide.BUY)).decision


def parity_payload(decision) -> dict:
    payload = decision.model_dump(mode="json")
    payload.pop("runtime_mode", None)
    return payload
