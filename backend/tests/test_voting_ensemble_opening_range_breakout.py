from __future__ import annotations

import unittest
from datetime import UTC, timedelta

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.domain.models import Direction, RegimeState
from backend.tests.test_voting_ensemble_snapshot import START, snapshot_payload


class OpeningRangeBreakoutStrategyTest(unittest.TestCase):
    def test_long_breakout_returns_buy_with_event_correlation_id(self) -> None:
        snapshot = _snapshot(_opening_range_rows("long"))
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="ignored-correlation", regime_state=_regime(snapshot))

        self.assertEqual(signal.strategyId, "opening_range_breakout")
        self.assertEqual(signal.strategyName, "Opening Range Breakout")
        self.assertEqual(signal.family, "breakout")
        self.assertEqual(signal.signal, "Buy")
        self.assertTrue(signal.eligible)
        self.assertTrue(signal.dataReady)
        self.assertIn("opening_range_breakout.buy_breakout", signal.reasonCodes)
        self.assertGreaterEqual(signal.features["breakoutDistanceAtr"], 0.08)
        self.assertGreaterEqual(signal.features["relativeVolume"], 1.05)
        self.assertEqual(signal.features["shadowOnly"], True)
        self.assertTrue(signal.correlationId.startswith("opening-range-breakout-"))
        self.assertNotEqual(signal.correlationId, "ignored-correlation")

    def test_short_breakout_returns_sell(self) -> None:
        snapshot = _snapshot(_opening_range_rows("short"))
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Sell")
        self.assertTrue(signal.eligible)
        self.assertIn("opening_range_breakout.sell_breakout", signal.reasonCodes)

    def test_false_break_close_back_inside_returns_hold(self) -> None:
        snapshot = _snapshot(_opening_range_rows("false_long"))
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertFalse(signal.eligible)
        self.assertIn("opening_range_breakout.close_back_inside", signal.reasonCodes)

    def test_exact_range_boundary_returns_hold(self) -> None:
        snapshot = _snapshot(_opening_range_rows("boundary"))
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("opening_range_breakout.no_boundary_break", signal.reasonCodes)

    def test_missing_mandatory_data_returns_data_not_ready_hold(self) -> None:
        payload = snapshot_payload(_opening_range_rows("long"), include_nbbo=False)
        snapshot = build_live_paper_snapshot(payload).model_copy(
            update={"features": build_live_paper_snapshot(payload).features.model_copy(update={"atr": 1.0})}
        )
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertFalse(signal.dataReady)
        self.assertIn("opening_range_breakout.missing_nbbo", signal.reasonCodes)

    def test_event_blackout_returns_hold(self) -> None:
        payload = snapshot_payload(_opening_range_rows("long"))
        evaluation = payload["data_timestamp"]
        payload["market_context"]["event"] = {
            "name": "FOMC",
            "importance": "high",
            "state": "active",
            "providerTimestamp": evaluation,
            "receiptTimestamp": evaluation,
        }
        snapshot = _snapshot_from_payload(payload)
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("opening_range_breakout.event_blackout", signal.reasonCodes)

    def test_future_breakout_candle_is_not_available_point_in_time(self) -> None:
        rows = _opening_range_rows("boundary")
        payload = snapshot_payload(rows)
        payload["candles"].append(_row(30, open_=100.40, high=100.90, low=100.35, close=100.80, volume=3000))
        snapshot = _snapshot_from_payload(payload)
        signal = OpeningRangeBreakoutStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertLess(snapshot.spyOneMinuteCandles[-1].candle.timestamp, START + timedelta(minutes=30))
        self.assertEqual(signal.signal, "Hold")
        self.assertIn("opening_range_breakout.no_boundary_break", signal.reasonCodes)

    def test_shadow_output_is_captured_without_affecting_active_decision(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_regime = service_module.REGIME_CLASSIFIER

        class FakeRegimeClassifier:
            def evaluate_snapshot(self, snapshot):
                return _regime(snapshot)

        service_module.DIRECTIONAL_STRATEGIES = (
            lambda request: _vote(
                "Multi-Timeframe Trend Alignment",
                "trend",
                "Hold",
                10,
                "Active strategy held.",
                "test.active_hold",
            ),
        )
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FakeRegimeClassifier()
        try:
            result = VotingEnsembleService().evaluate(snapshot_payload(_opening_range_rows("long")))
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_regime

        self.assertEqual(result["final_signal"], "Hold")
        self.assertEqual([vote["features"].get("strategyId") for vote in result["votes"]], [None])
        shadow = next(vote for vote in result["shadow_directional_votes"] if vote["features"].get("strategyId") == "opening_range_breakout")
        self.assertEqual(shadow["features"]["strategyId"], "opening_range_breakout")
        self.assertEqual(shadow["signal"], "Buy")
        self.assertFalse(shadow["active"])
        self.assertEqual(result["eligible_strategy_count"], 0)
        self.assertNotIn("breakout", result["family_scores"])


def _snapshot(rows: list[dict]) -> object:
    return _snapshot_from_payload(snapshot_payload(rows))


def _snapshot_from_payload(payload: dict) -> object:
    snapshot = build_live_paper_snapshot(payload)
    return snapshot.model_copy(update={"features": snapshot.features.model_copy(update={"atr": 1.0})})


def _regime(snapshot) -> RegimeState:
    return RegimeState(
        regimeId="test-breakout-regime",
        label="test-breakout-regime",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.75,
        features={
            "trendFit": 0.65,
            "breakoutFit": 0.85,
            "reversalFit": 0.35,
            "meanReversionFit": 0.35,
            "gapSessionFit": 0.35,
            "eventRiskState": "clear",
            "reasonCodes": ("test.regime",),
        },
        evaluatedAt=snapshot.evaluationTimestamp,
        sessionDate=snapshot.evaluationTimestamp.date(),
        configurationHash="test-regime",
    )


def _opening_range_rows(kind: str) -> list[dict]:
    rows = [_row(index, open_=100.00, high=100.20, low=99.80, close=100.00, volume=1000) for index in range(15)]
    rows.extend(_row(index, open_=100.03, high=100.15, low=99.95, close=100.05, volume=950) for index in range(15, 20))
    if kind == "long":
        rows.append(_row(20, open_=100.25, high=100.50, low=100.24, close=100.42, volume=2600))
    elif kind == "short":
        rows.append(_row(20, open_=99.75, high=99.76, low=99.50, close=99.58, volume=2600))
    elif kind == "false_long":
        rows.append(_row(20, open_=100.18, high=100.50, low=100.05, close=100.42, volume=2600))
    elif kind == "boundary":
        rows.append(_row(20, open_=100.10, high=100.25, low=100.00, close=100.20, volume=2600))
    else:
        raise ValueError(f"unknown opening-range row kind: {kind}")
    return rows


def _row(index: int, *, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    timestamp = START + timedelta(minutes=index)
    return {
        "timestamp": timestamp.isoformat(),
        "open": round(open_, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": volume,
        "symbol": "SPY",
        "finalizationTimestamp": timestamp.isoformat(),
    }


if __name__ == "__main__":
    unittest.main()
