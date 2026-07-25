from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.algorithms.voting_ensemble.snapshot import build_backtest_snapshot, build_live_paper_snapshot, build_replay_snapshot


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class VotingEnsembleSnapshotTest(unittest.TestCase):
    def test_same_point_in_time_inputs_produce_identical_snapshot_across_modes(self) -> None:
        payload = snapshot_payload(candles(30))

        live = build_live_paper_snapshot(payload)
        replay = build_replay_snapshot(payload)
        backtest = build_backtest_snapshot(payload)

        self.assertEqual(live.model_dump(mode="json"), replay.model_dump(mode="json"))
        self.assertEqual(live.model_dump(mode="json"), backtest.model_dump(mode="json"))
        self.assertEqual(live.feedHealthStatus, "ready")
        self.assertTrue(live.dataReadiness.ready)
        self.assertEqual(live.nbbo.spreadDollars, 0.02)
        self.assertGreater(live.nbbo.bidSize, 0)
        self.assertGreater(live.nbbo.askSize, 0)
        self.assertIsNotNone(live.features.atr)
        self.assertIsNotNone(live.features.adx)
        self.assertIsNotNone(live.features.bollingerMiddle)

    def test_future_candles_and_future_events_are_inaccessible_or_fail_closed(self) -> None:
        rows = candles(30)
        future = candle(START + timedelta(minutes=60), close=500.0)
        payload = snapshot_payload(rows)
        payload["candles"].append(future)
        payload["market_context"]["event"] = {
            "name": "Future event",
            "providerTimestamp": (START + timedelta(minutes=60)).isoformat(),
            "receiptTimestamp": (START + timedelta(minutes=60)).isoformat(),
        }

        snapshot = build_live_paper_snapshot(payload)

        self.assertTrue(all(item.candle.timestamp <= snapshot.evaluationTimestamp for item in snapshot.spyOneMinuteCandles))
        self.assertNotEqual(snapshot.spyOneMinuteCandles[-1].candle.close, 500.0)
        self.assertEqual(snapshot.feedHealthStatus, "fail_closed")
        self.assertIn("future_economic_event_provider_timestamp", snapshot.dataReadiness.staleInputs)

    def test_missing_or_stale_quotes_fail_closed_without_candle_substitution(self) -> None:
        missing = build_live_paper_snapshot(snapshot_payload(candles(30), include_nbbo=False))
        stale_payload = snapshot_payload(candles(30))
        stale_payload["nbbo"]["quoteTimestamp"] = (START - timedelta(minutes=10)).isoformat()
        stale = build_live_paper_snapshot(stale_payload)

        self.assertEqual(missing.feedHealthStatus, "fail_closed")
        self.assertIn("missing_spy_nbbo", missing.dataReadiness.mandatoryFailures)
        self.assertIsNone(missing.nbbo)
        self.assertEqual(stale.feedHealthStatus, "fail_closed")
        self.assertIn("stale_spy_quote", stale.dataReadiness.staleInputs)

    def test_service_fail_closed_data_readiness_does_not_run_strategies(self) -> None:
        original = service_module.DIRECTIONAL_STRATEGIES
        calls: list[str] = []

        def recorder(request: VotingEnsembleEvaluateRequest):
            calls.append("called")
            return _vote("Recorder", "trend", "Hold", 0, "should not run", "test.recorder")

        service_module.DIRECTIONAL_STRATEGIES = (recorder,)
        try:
            result = VotingEnsembleService().evaluate(snapshot_payload(candles(30), include_nbbo=False))
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original

        self.assertEqual(calls, [])
        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        self.assertIn("voting_ensemble.evaluate.fail_closed_data_readiness", result["reason_codes"])

    def test_all_active_strategies_receive_same_immutable_snapshot_request(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        seen_request_ids: list[int] = []
        seen_snapshot_hashes: list[str] = []

        def recorder(request: VotingEnsembleEvaluateRequest):
            seen_request_ids.append(id(request))
            seen_snapshot_hashes.append(request.market_context["pointInTimeSnapshot"]["snapshotHash"])
            with self.assertRaises(ValidationError):
                request.symbol = "QQQ"  # type: ignore[misc]
            return _vote("Multi-Timeframe Trend Alignment", "trend", "Hold", 10, "recorded", "test.recorded")

        service_module.DIRECTIONAL_STRATEGIES = (recorder, recorder)
        service_module.CONTEXT_STRATEGIES = ()
        try:
            result = VotingEnsembleService().evaluate(snapshot_payload(candles(30)))
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context

        self.assertEqual(result["final_signal"], "Hold")
        self.assertEqual(len(set(seen_request_ids)), 1)
        self.assertEqual(len(set(seen_snapshot_hashes)), 1)


def snapshot_payload(rows: list[dict[str, Any]], *, include_nbbo: bool = True) -> dict[str, Any]:
    evaluation = rows[-1]["timestamp"]
    payload = {
        "symbol": "SPY",
        "data_timestamp": evaluation,
        "candles": rows,
        "spy_5m_candles": candles(6, minutes=5),
        "spy_15m_candles": candles(2, minutes=15),
        "qqq_candles": candles(len(rows), symbol="QQQ"),
        "iwm_candles": candles(len(rows), symbol="IWM"),
        "breadth_components": {
            "XLK": candles(len(rows), symbol="XLK"),
            "XLF": candles(len(rows), symbol="XLF"),
            "XLV": candles(len(rows), symbol="XLV"),
        },
        "external_breadth_feed": {
            "providerTimestamp": evaluation,
            "receiptTimestamp": evaluation,
            "percentageAdvancing": 0.55,
            "dataCoverage": 0.90,
        },
        "market_context": {
            "priorDayOHLC": {"high": 101.0, "low": 99.0, "open": 100.0, "close": 100.5},
            "premarket": {"high": 100.8, "low": 99.4, "open": 99.8, "close": 100.2},
            "openingRange": {"high": 101.2, "low": 99.6, "open": 100.0, "close": 100.7},
            "event": {
                "name": "None",
                "providerTimestamp": evaluation,
                "receiptTimestamp": evaluation,
            },
            "sessionState": {"phase": "regular"},
            "accountRiskSnapshot": {"equity": 25000.0, "realizedPnlToday": 0.0},
            "operationalHealthSnapshot": {"status": "nominal"},
        },
        "settings": {},
    }
    if include_nbbo:
        payload["nbbo"] = {
            "bid": 100.49,
            "ask": 100.51,
            "bidSize": 1200,
            "askSize": 1100,
            "quoteTimestamp": evaluation,
            "lastTradeTimestamp": evaluation,
            "marketDataReceiptTimestamp": evaluation,
        }
    return payload


def candles(count: int, *, minutes: int = 1, symbol: str = "SPY") -> list[dict[str, Any]]:
    return [candle(START + timedelta(minutes=index * minutes), close=100.0 + index * 0.05, symbol=symbol) for index in range(count)]


def candle(timestamp: datetime, *, close: float, symbol: str = "SPY") -> dict[str, Any]:
    return {
        "timestamp": timestamp.isoformat(),
        "open": round(close - 0.03, 4),
        "high": round(close + 0.10, 4),
        "low": round(close - 0.10, 4),
        "close": round(close, 4),
        "volume": 1000,
        "symbol": symbol,
        "finalizationTimestamp": timestamp.isoformat(),
    }


if __name__ == "__main__":
    unittest.main()
