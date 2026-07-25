from __future__ import annotations

import unittest
from datetime import timedelta

from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.gap_continuation_fade import GapContinuationFadeStrategy
from backend.app.domain.models import Direction, RegimeState
from backend.tests.test_voting_ensemble_snapshot import START, snapshot_payload


class GapContinuationFadeStrategyTest(unittest.TestCase):
    def test_bullish_gap_continuation_gap_and_go_returns_buy(self) -> None:
        snapshot = _snapshot(_gap_rows("bullish_continuation"), prior_close=100.0, vwap=101.10)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.strategyId, "gap_continuation_fade")
        self.assertEqual(signal.strategyName, "Gap Continuation / Fade")
        self.assertEqual(signal.family, "gap_session")
        self.assertEqual(signal.signal, "Buy")
        self.assertIn("gap_continuation_fade.bullish_gap_continuation", signal.reasonCodes)
        self.assertEqual(signal.features["setupOutcome"], "bullish_gap_continuation")
        self.assertTrue(str(signal.features["eventCorrelationId"]).startswith("gap-session-event-"))
        self.assertEqual(signal.features["shadowOnly"], True)

    def test_bearish_gap_continuation_returns_sell(self) -> None:
        snapshot = _snapshot(_gap_rows("bearish_continuation"), prior_close=100.0, vwap=98.90)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Sell")
        self.assertIn("gap_continuation_fade.bearish_gap_continuation", signal.reasonCodes)
        self.assertEqual(signal.features["setupOutcome"], "bearish_gap_continuation")

    def test_bearish_gap_fade_gap_fill_returns_sell(self) -> None:
        snapshot = _snapshot(_gap_rows("bearish_fade"), prior_close=100.0, vwap=100.70)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Sell")
        self.assertIn("gap_continuation_fade.bearish_gap_fade", signal.reasonCodes)
        self.assertEqual(signal.features["setupOutcome"], "bearish_gap_fade")

    def test_bullish_gap_fade_returns_buy(self) -> None:
        snapshot = _snapshot(_gap_rows("bullish_fade"), prior_close=100.0, vwap=99.30)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Buy")
        self.assertIn("gap_continuation_fade.bullish_gap_fade", signal.reasonCodes)
        self.assertEqual(signal.features["setupOutcome"], "bullish_gap_fade")

    def test_small_or_no_gap_returns_hold(self) -> None:
        snapshot = _snapshot(_gap_rows("small_gap"), prior_close=100.0, vwap=100.10)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("gap_continuation_fade.small_or_no_gap", signal.reasonCodes)

    def test_large_gap_returns_hold(self) -> None:
        snapshot = _snapshot(_gap_rows("large_gap"), prior_close=100.0, vwap=103.80)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("gap_continuation_fade.large_gap", signal.reasonCodes)

    def test_partial_fill_is_hold_without_confirmation(self) -> None:
        snapshot = _snapshot(_gap_rows("partial_fill"), prior_close=100.0, vwap=100.80)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("gap_continuation_fade.no_confirmed_setup", signal.reasonCodes)

    def test_premarket_level_rejection_can_confirm_gap_fade(self) -> None:
        snapshot = _snapshot(_gap_rows("premarket_rejection"), prior_close=100.0, vwap=100.80, premarket_high=101.15)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Sell")
        self.assertEqual(signal.features["setupOutcome"], "bearish_gap_fade")
        self.assertGreaterEqual(signal.features["fillFraction"], 0.35)

    def test_event_days_return_hold(self) -> None:
        payload = _payload(_gap_rows("bullish_continuation"), prior_close=100.0)
        evaluation = payload["data_timestamp"]
        payload["market_context"]["event"] = {
            "name": "CPI",
            "importance": "high",
            "state": "active",
            "providerTimestamp": evaluation,
            "receiptTimestamp": evaluation,
        }
        snapshot = _snapshot_from_payload(payload, vwap=101.10)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("gap_continuation_fade.event_day", signal.reasonCodes)

    def test_stale_prior_close_data_fails_closed(self) -> None:
        payload = _payload(_gap_rows("bullish_continuation"), prior_close=100.0)
        payload["market_context"]["sessionState"]["priorCloseTimestamp"] = (START - timedelta(days=10)).isoformat()
        snapshot = _snapshot_from_payload(payload, vwap=101.10)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertFalse(signal.dataReady)
        self.assertIn("gap_continuation_fade.stale_prior_close", signal.reasonCodes)

    def test_strategy_cannot_fire_outside_session_window(self) -> None:
        rows = _gap_rows("bullish_continuation")
        rows.extend(_row(index, open_=101.60, high=101.80, low=101.40, close=101.70, volume=1000) for index in range(5, 55))
        snapshot = _snapshot(rows, prior_close=100.0, vwap=101.20)
        signal = GapContinuationFadeStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("gap_continuation_fade.outside_session_window", signal.reasonCodes)


def _snapshot(rows: list[dict], *, prior_close: float, vwap: float, premarket_high: float = 101.4):
    return _snapshot_from_payload(_payload(rows, prior_close=prior_close, premarket_high=premarket_high), vwap=vwap)


def _snapshot_from_payload(payload: dict, *, vwap: float):
    snapshot = build_live_paper_snapshot(payload)
    return snapshot.model_copy(update={"features": snapshot.features.model_copy(update={"atr": 1.0, "vwap": vwap})})


def _payload(rows: list[dict], *, prior_close: float, premarket_high: float = 101.4) -> dict:
    payload = snapshot_payload(rows)
    payload["market_context"]["priorDayOHLC"] = {"high": prior_close + 1.0, "low": prior_close - 1.0, "open": prior_close - 0.2, "close": prior_close}
    payload["market_context"]["premarket"] = {"high": premarket_high, "low": 98.6, "open": 100.2, "close": rows[0]["open"]}
    payload["market_context"]["sessionState"] = {"phase": "regular"}
    return payload


def _regime(snapshot) -> RegimeState:
    return RegimeState(
        regimeId="test-gap-session-regime",
        label="test-gap-session-regime",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.75,
        features={
            "trendFit": 0.45,
            "breakoutFit": 0.45,
            "reversalFit": 0.45,
            "meanReversionFit": 0.45,
            "gapSessionFit": 0.85,
            "eventRiskState": "clear",
            "reasonCodes": ("test.regime",),
        },
        evaluatedAt=snapshot.evaluationTimestamp,
        sessionDate=snapshot.evaluationTimestamp.date(),
        configurationHash="test-regime",
    )


def _gap_rows(kind: str) -> list[dict]:
    if kind == "bearish_continuation":
        return [
            _row(0, open_=99.00, high=99.18, low=98.90, close=99.04, volume=1000),
            _row(1, open_=99.04, high=99.12, low=98.76, close=98.86, volume=1000),
            _row(2, open_=98.86, high=98.96, low=98.62, close=98.74, volume=1000),
            _row(3, open_=98.74, high=98.80, low=98.48, close=98.58, volume=1000),
            _row(4, open_=98.56, high=98.58, low=98.20, close=98.28, volume=1600),
        ]
    if kind == "bearish_fade":
        return [
            _row(0, open_=101.00, high=101.18, low=100.84, close=101.05, volume=1000),
            _row(1, open_=101.05, high=101.20, low=100.78, close=100.88, volume=1000),
            _row(2, open_=100.88, high=101.02, low=100.68, close=100.78, volume=1000),
            _row(3, open_=100.78, high=100.90, low=100.52, close=100.60, volume=1000),
            _row(4, open_=100.58, high=100.60, low=100.34, close=100.42, volume=1600),
        ]
    if kind == "bullish_fade":
        return [
            _row(0, open_=99.00, high=99.18, low=98.82, close=98.96, volume=1000),
            _row(1, open_=98.96, high=99.22, low=98.80, close=99.08, volume=1000),
            _row(2, open_=99.08, high=99.30, low=98.92, close=99.18, volume=1000),
            _row(3, open_=99.18, high=99.36, low=99.04, close=99.26, volume=1000),
            _row(4, open_=99.30, high=99.66, low=99.26, close=99.58, volume=1600),
        ]
    if kind == "small_gap":
        return [
            _row(0, open_=100.05, high=100.20, low=99.94, close=100.08, volume=1000),
            _row(1, open_=100.08, high=100.18, low=99.98, close=100.10, volume=1000),
            _row(2, open_=100.10, high=100.20, low=100.00, close=100.12, volume=1000),
            _row(3, open_=100.12, high=100.24, low=100.04, close=100.16, volume=1000),
            _row(4, open_=100.16, high=100.34, low=100.12, close=100.28, volume=1600),
        ]
    if kind == "large_gap":
        return [
            _row(0, open_=104.00, high=104.20, low=103.80, close=104.02, volume=1000),
            _row(1, open_=104.02, high=104.16, low=103.70, close=103.86, volume=1000),
            _row(2, open_=103.86, high=104.08, low=103.60, close=103.94, volume=1000),
            _row(3, open_=103.94, high=104.12, low=103.72, close=103.98, volume=1000),
            _row(4, open_=104.00, high=104.26, low=103.90, close=104.18, volume=1600),
        ]
    if kind == "partial_fill":
        return [
            _row(0, open_=101.00, high=101.20, low=100.84, close=101.04, volume=1000),
            _row(1, open_=101.04, high=101.18, low=100.80, close=100.90, volume=1000),
            _row(2, open_=100.90, high=101.02, low=100.76, close=100.86, volume=1000),
            _row(3, open_=100.86, high=100.98, low=100.72, close=100.84, volume=1000),
            _row(4, open_=100.84, high=100.92, low=100.78, close=100.86, volume=1600),
        ]
    if kind == "premarket_rejection":
        return [
            _row(0, open_=101.00, high=101.18, low=100.86, close=101.06, volume=1000),
            _row(1, open_=101.06, high=101.22, low=100.80, close=100.92, volume=1000),
            _row(2, open_=100.92, high=101.10, low=100.74, close=100.82, volume=1000),
            _row(3, open_=100.82, high=100.94, low=100.58, close=100.66, volume=1000),
            _row(4, open_=100.64, high=100.66, low=100.38, close=100.48, volume=1600),
        ]
    return [
        _row(0, open_=101.00, high=101.18, low=100.84, close=101.05, volume=1000),
        _row(1, open_=101.05, high=101.24, low=100.98, close=101.16, volume=1000),
        _row(2, open_=101.16, high=101.34, low=101.08, close=101.24, volume=1000),
        _row(3, open_=101.24, high=101.44, low=101.18, close=101.36, volume=1000),
        _row(4, open_=101.38, high=101.72, low=101.34, close=101.66, volume=1600),
    ]


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
