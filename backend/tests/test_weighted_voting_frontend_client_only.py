from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_TS = ROOT / "frontend" / "src" / "main.ts"


def _source() -> str:
    return MAIN_TS.read_text(encoding="utf-8")


def _function_body(source: str, function_name: str) -> str:
    match = re.search(rf"function {re.escape(function_name)}\([^)]*\) \{{", source)
    assert match, f"{function_name} was not found"
    start = match.end()
    depth = 1
    index = start
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"{function_name} body did not parse"
    return source[start:index - 1]


def test_weighted_voting_panel_uses_backend_client_not_local_calculation() -> None:
    source = _source()
    body = _function_body(source, "updateWeightedVotingPanel")

    assert "/api/weighted-voting" in source
    assert "refreshWeightedVotingBackendClient" in body
    assert "weightedVotingBackendSummary" in body
    assert "calculateWeightedVote(" not in body
    assert "weightedAlphaSignal(" not in body
    assert "weightedTargetOrderRecommendation(" not in body
    assert "maybeAutoSubmitWeightedTargetOrder(" not in body


def test_weighted_voting_config_edits_go_through_api() -> None:
    body = _function_body(_source(), "handleWeightedConfigSettingChange")

    assert 'fetchWeightedVotingJson("/config"' in body
    assert 'method: "PUT"' in body
    assert "localStorage" not in body
    assert "saveWeightedTradingSettings" not in body


def test_weighted_voting_frontend_does_not_submit_or_record_paper_fills() -> None:
    source = _source()

    assert "function maybeAutoSubmitWeightedTargetOrder" not in source
    assert "maybeAutoSubmitWeightedTargetOrder(" not in source


def test_weighted_voting_daily_refresh_calls_backend_scheduler() -> None:
    body = _function_body(_source(), "runWeightedDailyBacktestRefresh")

    assert 'fetchWeightedVotingJson("/daily-update/run"' in body
    assert "weightedInitialBacktestWeights" not in body
    assert "saveWeightedVotingWeightState" not in body
    assert "saveWeightedStrategyPerformance" not in body
    assert "localStorage" not in body
