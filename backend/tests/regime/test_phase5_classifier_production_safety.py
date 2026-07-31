from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.contracts import RegimeAxes, RegimeClassification, RegimeHysteresisState
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.runtime_state import initial_regime_runtime_state, next_regime_runtime_state


FRESH_CONTEXT = {
    "quoteFreshness": {"status": "fresh", "ageMs": 500, "bid": 100.0, "ask": 100.02, "spreadBps": 2.0, "expectedFillQuantity": 100},
    "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
    "intradayVolatilityBaseline": {
        "calibrationStatus": "ready",
        "atrPercentile": 0.50,
        "realizedVolatilityPercentile": 0.50,
        "currentRangeVsExpected": 1.0,
        "sampleSize": 80,
    },
}


def test_phase5_classifier_has_separate_axes_and_warmup_returns_unknown() -> None:
    snapshot = _snapshot(count=20)

    classification = classify_market_regime(snapshot)

    assert classification.raw_regime == "unknown"
    assert classification.axes.trend_strength in {"strong", "weak", "neutral", "unknown"}
    assert classification.axes.data_quality == "insufficient_warmup"
    assert "regime.safety.insufficient_classifier_warmup" in classification.no_trade_reasons
    assert "regime.classifier.insufficient_warmup.primary_candles" in classification.evidence["warmupEvidence"]["reasonCodes"]


def test_phase5_pipeline_blocks_entries_until_classifier_warmup_is_complete() -> None:
    result = execute_regime_pipeline({"marketData": _payload(count=20)})

    assert result["decision"]["signal"] == "Hold"
    assert result["decision"]["trade_allowed"] is False
    assert result["decision"]["raw_classification"]["raw_regime"] == "unknown"
    assert result["effectiveProfile"]["noNewEntries"] is True


def test_phase5_one_abnormal_candle_cannot_confirm_non_risk_regime_change() -> None:
    previous = RegimeHysteresisState(
        confirmed_regime="strong_uptrend",
        previous_regime=None,
        candidate_regime=None,
        candidate_confirmation_count=0,
        regime_start_time="2026-07-23T15:00:00Z",
        transition_confidence=0.8,
        transition_reason="restored_runtime_state",
    )

    state = confirm_regime_transition(_classification("strong_downtrend", "2026-07-23T15:01:00Z"), previous, {"confirmationBars": 3})

    assert state.confirmed_regime == "strong_uptrend"
    assert state.candidate_regime == "strong_downtrend"
    assert state.candidate_confirmation_count == 1
    assert state.transition_reason == "candidate_waiting"
    assert state.transition_evidence["enterThresholdBars"] == 3
    assert state.transition_evidence["requiredConfirmationBars"] == 3


def test_phase5_hysteresis_candidate_count_resets_at_session_boundary() -> None:
    previous = RegimeHysteresisState(
        confirmed_regime="strong_uptrend",
        previous_regime=None,
        candidate_regime="strong_downtrend",
        candidate_confirmation_count=2,
        regime_start_time="2026-07-23T15:00:00Z",
        transition_confidence=0.8,
        transition_reason="candidate_waiting",
    )

    state = confirm_regime_transition(_classification("strong_downtrend", "2026-07-24T15:00:00Z"), previous, {"confirmationBars": 3})

    assert state.confirmed_regime == "strong_uptrend"
    assert state.candidate_confirmation_count == 1
    assert state.transition_evidence["sessionBoundaryReset"] is True


def test_phase5_runtime_state_resets_session_scoped_counters() -> None:
    identity = {"algorithmInstanceId": "phase5", "accountId": "paper", "runtimeMode": "paper", "symbol": "SPY"}
    previous = initial_regime_runtime_state(identity, timestamp="2026-07-23T15:00:00Z")
    previous = previous.__class__(
        **{
            **previous.__dict__,
            "confirmed_regime": "strong_uptrend",
            "regime_dwell_bars": 12,
            "daily_counters": {"decisionCount": 9, "orderProposalCount": 4, "tradeCount": 2, "lossCount": 1},
            "strategy_cooldowns": {"trend_pullback": 3},
            "family_cooldowns": {"trend": 3},
            "last_processed_bar_timestamp": "2026-07-23T19:59:00Z",
        }
    )

    state = next_regime_runtime_state(
        previous,
        identity=identity,
        decision_id="decision-next-session",
        bar_timestamp="2026-07-24T13:30:00Z",
        confirmed_regime="strong_uptrend",
        previous_regime=None,
        candidate_regime=None,
        candidate_start_timestamp=None,
        candidate_confirmation_count=0,
        regime_confidence=0.8,
        regime_start_timestamp="2026-07-24T13:30:00Z",
        last_transition_timestamp="2026-07-24T13:30:00Z",
        transition_reason="confirmed_regime_held",
        missing_inputs=(),
        open_position_summary={},
        order_proposed=False,
    )

    assert state.regime_dwell_bars == 1
    assert state.daily_counters["decisionCount"] == 1
    assert state.daily_counters["orderProposalCount"] == 0
    assert state.strategy_cooldowns == {}
    assert state.family_cooldowns == {}


def _classification(raw_regime: str, timestamp: str) -> RegimeClassification:
    return RegimeClassification(
        raw_regime=raw_regime,
        axes=RegimeAxes("strong_down", "normal", "trend", "good", "midday", "none", "strong", "valid"),
        confidence=0.99,
        features={"sessionDate": "2026-07-23"},
        evidence={"directionEvidence": {"reasonCodes": ("regime.test.direction",)}},
        missing_inputs=(),
        no_trade_reasons=(),
        timestamp=timestamp,
    )


def _snapshot(*, count: int):
    return build_regime_market_snapshot(_payload(count=count))


def _payload(*, count: int) -> dict:
    start = datetime(2026, 7, 23, 14, 30, tzinfo=UTC)
    candles = []
    price = 100.0
    for index in range(count):
        price += 0.04
        candles.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 150_000 + index,
                "finalized": True,
            }
        )
    return {
        "symbol": "SPY",
        "timeframe": "1Min",
        "primaryCandles": list(candles),
        "oneMinuteCandles": list(candles),
        "contextFeeds": FRESH_CONTEXT,
    }
