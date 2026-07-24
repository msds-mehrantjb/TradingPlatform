from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import (
    SessionBacktestEngine,
    SessionBacktestExecutionConfig,
    compare_session_runtime_parity,
    resolve_session_profile,
)
from backend.app.algorithms.session.backtest.engine import run_session_backtest, run_session_event_stream
from backend.app.algorithms.session.execution import build_session_candidate_decision
from backend.app.algorithms.session.runtime import EventDrivenSessionRuntime
from backend.app.algorithms.session.state import FINALIZED_ONE_MINUTE_BAR, QUOTE_NBBO_UPDATE
from backend.app.domain.models import Signal


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


def test_session_step15_direct_replay_backtest_and_paper_shadow_have_identical_authoritative_decisions() -> None:
    events = _event_stream(18)

    direct = run_session_event_stream(events, mode="direct_replay")
    backtest = run_session_backtest(events)
    paper_shadow = run_session_event_stream(events, mode="paper_shadow")
    parity = compare_session_runtime_parity(direct, backtest, paper_shadow)

    assert parity.identical is True
    assert parity.mismatchCount == 0
    assert len(parity.comparedTimestamps) == len(direct) == len(backtest) == len(paper_shadow)
    assert all(snapshot.classification == direct[index].classification for index, snapshot in enumerate(backtest))
    assert all(snapshot.transitionState == direct[index].transitionState for index, snapshot in enumerate(paper_shadow))
    assert all(snapshot.profile == direct[index].profile for index, snapshot in enumerate(backtest))
    assert all(snapshot.blockNewEntries == direct[index].blockNewEntries for index, snapshot in enumerate(paper_shadow))


def test_session_step15_paper_affecting_mode_uses_same_runtime_but_different_output_mode() -> None:
    events = _event_stream(12)

    shadow = run_session_event_stream(events, mode="paper_shadow")
    paper = run_session_event_stream(events, mode="paper_affecting")
    parity = compare_session_runtime_parity(shadow, paper)

    assert parity.identical is True
    assert {snapshot.outputMode for snapshot in shadow} == {"shadow"}
    assert {snapshot.outputMode for snapshot in paper} == {"paper_affecting"}
    assert shadow[-1].routePermissions["cannotBypassGlobalGates"] is True


def test_session_step15_engine_uses_event_runtime_not_vectorized_shortcut(monkeypatch) -> None:
    calls = {"count": 0}
    original = EventDrivenSessionRuntime.process_event

    def counted(self, raw_event):
        calls["count"] += 1
        return original(self, raw_event)

    monkeypatch.setattr(EventDrivenSessionRuntime, "process_event", counted)
    events = _event_stream(6)

    results = SessionBacktestEngine().run(events, mode="backtest")

    assert calls["count"] == len(events)
    assert len(results) == 6
    assert all("runtime" in snapshot.classification["evidence"] for snapshot in results)


def test_session_step15_cost_gate_and_neutral_order_validation_are_in_runtime_snapshots() -> None:
    results = run_session_event_stream(_event_stream(15), mode="paper_shadow")

    gated = [snapshot for snapshot in results if snapshot.orderGate is not None]

    assert gated
    assert gated[-1].orderGate["submitted"] is False
    assert gated[-1].orderGate["globalOrderProposal"]["algorithmId"] == "session"
    assert gated[-1].orderGate["validatedOrderIntent"]["status"] == "VALIDATED"
    assert gated[-1].orderGate["expectedNetEdge"] > 0


def test_session_step15_backtest_execution_models_long_short_and_ambiguous_outcomes() -> None:
    engine = SessionBacktestEngine(execution_config=SessionBacktestExecutionConfig(slippage=0.0))
    long_candidate = _candidate(side=Signal.BUY, entry=100.0, stop=99.5, target=100.75)
    short_candidate = _candidate(side=Signal.SELL, entry=100.0, stop=100.5, target=99.25)

    long_stop = engine.simulate_execution(long_candidate, [_future_bar(0, high=100.2, low=99.4, close=99.6)])
    short_stop = engine.simulate_execution(short_candidate, [_future_bar(0, high=100.6, low=99.9, close=100.4)])
    ambiguous = engine.simulate_execution(long_candidate, [_future_bar(0, high=100.8, low=99.4, close=100.1)])

    assert long_stop.status == "STOP"
    assert long_stop.reasonCodes == ("session.backtest.long_stop_hit",)
    assert short_stop.status == "STOP"
    assert short_stop.reasonCodes == ("session.backtest.short_stop_hit",)
    assert ambiguous.status == "AMBIGUOUS_STOP"
    assert ambiguous.exitPrice == long_candidate.stopPrice


def test_session_step15_backtest_execution_models_missed_partial_and_eod_flatten() -> None:
    miss_engine = SessionBacktestEngine(execution_config=SessionBacktestExecutionConfig(missedLimitFillRate=1.0))
    partial_engine = SessionBacktestEngine(execution_config=SessionBacktestExecutionConfig(partialFillRatio=0.5, slippage=0.0))
    candidate = _candidate(side=Signal.BUY, entry=100.0, stop=99.5, target=101.0)

    missed = miss_engine.simulate_execution(candidate, [_future_bar(0, high=100.5, low=99.9, close=100.2)])
    partial = partial_engine.simulate_execution(candidate, [_future_bar(0, high=100.5, low=99.9, close=100.2)])

    assert missed.status == "NO_FILL"
    assert missed.filledQuantity == 0
    assert partial.status == "EOD_FLATTEN"
    assert partial.filledQuantity == 5
    assert partial.exitPrice == 100.2


def _event_stream(length: int) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index in range(length):
        close = 100 + index * 0.08
        timestamp = SESSION_START + timedelta(minutes=index)
        events.append(
            {
                "type": QUOTE_NBBO_UPDATE,
                "event_id": f"quote-{index}",
                "symbol": "SPY",
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "bid": round(close - 0.005, 4),
                "ask": round(close + 0.005, 4),
            }
        )
        events.append(
            {
                "type": FINALIZED_ONE_MINUTE_BAR,
                "event_id": f"bar-{index}",
                "symbol": "SPY",
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": round(close - 0.03, 4),
                "high": round(close + 0.08, 4),
                "low": round(close - 0.07, 4),
                "close": round(close, 4),
                "volume": 100_000 + index * 1_000,
                "finalized": True,
            }
        )
    return events


def _candidate(*, side: Signal, entry: float, stop: float, target: float):
    runtime = SessionBacktestEngine()
    classification = runtime.run(_event_stream(12), mode="backtest")[-1].classification
    from backend.app.algorithms.session.models import SessionClassification

    model = SessionClassification.model_validate(classification)
    profile = resolve_session_profile(model)
    return build_session_candidate_decision(
        classification=model,
        profile=profile,
        originating_strategy_candidate_id=f"test-{side.value.lower()}",
        side=side,
        order_type="limit",
        desired_quantity=10,
        entry_price=entry,
        permitted_entry_price_range=(entry - 0.05, entry + 0.05),
        expected_gross_edge=0.08,
        spread_estimate=0.005,
        slippage_estimate=0.005,
        fees=0.001,
        market_impact_estimate=0.001,
        adverse_selection_buffer=0.002,
        fill_probability=0.8,
        quantity_cap=10,
        stop_price=stop,
        target_price=target,
        planned_risk_dollars=5.0,
    )


def _future_bar(index: int, *, high: float, low: float, close: float) -> dict[str, object]:
    timestamp = SESSION_START + timedelta(minutes=30 + index)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100_000,
    }
