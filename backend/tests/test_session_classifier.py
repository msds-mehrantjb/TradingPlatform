from __future__ import annotations

import pytest

from backend.app.algorithms.session import SessionBehavior, classify_session_axes, resolve_session_clock
from session_test_fixtures import axis_inputs, golden_axis_cases


@pytest.mark.parametrize("case", golden_axis_cases(), ids=[case.name for case in golden_axis_cases()])
def test_session_classifier_golden_market_patterns_have_exact_semantic_axes(case) -> None:
    result = classify_session_axes(**axis_inputs(case))

    assert result.phase == case.expected_phase
    assert result.behavior == case.expected_behavior
    assert result.direction_bias == case.expected_direction
    assert result.volatility_state == case.expected_volatility
    assert result.liquidity_state == case.expected_liquidity
    assert result.data_quality_state == case.expected_data_quality
    assert result.event_risk_state == case.expected_event_risk
    assert result.block_new_entries is case.block_new_entries
    assert any(case.reason_fragment in code for code in result.reason_codes)


def test_session_classifier_every_declared_behavior_is_reachable_or_safety_reserved() -> None:
    observed = {classify_session_axes(**axis_inputs(case)).behavior for case in golden_axis_cases()}
    observed.update(classify_session_axes(**inputs).behavior for inputs in _all_behavior_inputs())

    assert set(SessionBehavior) <= observed


def test_session_classifier_reason_codes_support_every_golden_behavior() -> None:
    for case in golden_axis_cases():
        result = classify_session_axes(**axis_inputs(case))
        assert result.reason_codes
        assert all(isinstance(code, str) and code for code in result.reason_codes)


def _all_behavior_inputs():
    from session_test_fixtures import _axis_inputs

    opening = resolve_session_clock("2026-07-23T13:33:00Z")
    opening_range = resolve_session_clock("2026-07-23T13:45:00Z")
    morning = resolve_session_clock("2026-07-23T14:20:00Z")
    midday = resolve_session_clock("2026-07-23T16:00:00Z")
    return (
        {**_axis_inputs(data_quality_state="warming_up"), "clock": opening},
        {**_axis_inputs(opening_drive="up", or5_status="building"), "clock": opening},
        {**_axis_inputs(structure_behavior="trend_up"), "clock": morning},
        {**_axis_inputs(structure_behavior="trend_down"), "clock": morning},
        {**_axis_inputs(), "clock": morning},
        {**_axis_inputs(structure_behavior="mean_reverting"), "clock": midday},
        {**_axis_inputs(structure_behavior="choppy", crossing_frequency=8), "clock": morning},
        {**_axis_inputs(structure_behavior="valid_breakout_up"), "clock": opening_range},
        {**_axis_inputs(structure_behavior="valid_breakout_down"), "clock": opening_range},
        {**_axis_inputs(structure_behavior="failed_breakout_up"), "clock": opening_range},
        {**_axis_inputs(structure_behavior="failed_breakout_down"), "clock": opening_range},
        {**_axis_inputs(structure_behavior="reversal_up"), "clock": morning},
        {**_axis_inputs(structure_behavior="reversal_down"), "clock": morning},
        {**_axis_inputs(range_percentile=0.8, rv_percentile=0.7), "clock": morning},
        {**_axis_inputs(range_percentile=0.2, rv_percentile=0.2), "clock": midday},
        {**_axis_inputs(event_blackout=True), "clock": morning},
        {**_axis_inputs(liquidity_state="stressed", liquidity_block=True), "clock": morning},
        {**_axis_inputs(data_quality_state="incomplete", data_block=True), "clock": morning},
    )
