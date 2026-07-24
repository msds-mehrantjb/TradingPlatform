from __future__ import annotations

from backend.app.algorithms.session import compare_session_runtime_parity, run_session_backtest, run_session_event_stream
from session_test_fixtures import event_stream


def test_session_runtime_parity_matches_replay_backtest_and_paper_shadow() -> None:
    events = event_stream(15)
    replay = run_session_event_stream(events, mode="direct_replay")
    backtest = run_session_backtest(events)
    paper = run_session_event_stream(events, mode="paper_shadow")

    parity = compare_session_runtime_parity(replay, backtest, paper)

    assert parity.identical is True
    assert parity.mismatchCount == 0
    assert [item.classification for item in replay] == [item.classification for item in backtest] == [item.classification for item in paper]


def test_session_runtime_duplicate_event_is_idempotent() -> None:
    regular = run_session_event_stream(event_stream(12), mode="direct_replay")
    duplicated = run_session_event_stream(event_stream(12, duplicate_index=5), mode="direct_replay")

    assert regular[-1].classification == duplicated[-1].classification


def test_session_runtime_missing_bar_blocks_entries() -> None:
    snapshots = run_session_event_stream(event_stream(12, missing_index=5), mode="direct_replay")

    assert snapshots[-1].blockNewEntries is True
    assert any("missing" in code.lower() for code in snapshots[-1].classification["reason_codes"])
