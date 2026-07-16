from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.weighted_voting.service import WeightedVotingService


REPO_ROOT = Path(__file__).parents[2]
FRONTEND_MAIN = REPO_ROOT / "frontend" / "src" / "main.ts"
SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


class WeightedVotingAlgorithmIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frontend_source = FRONTEND_MAIN.read_text(encoding="utf-8")

    def test_other_algorithm_outputs_do_not_change_backend_weighted_result(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        baseline_payload = evaluate_payload()
        changed_other_algorithms_payload = copy.deepcopy(baseline_payload)
        changed_other_algorithms_payload.update(
            {
                "votingEnsemble": {
                    "winner": "Sell",
                    "buyVotes": 0,
                    "sellVotes": 99,
                    "settings": {"minimumVotes": 99},
                    "tradeLedger": [{"side": "Sell", "quantity": 999}],
                    "backtestCompletion": {"status": "failed"},
                },
                "regimeSelection": {
                    "marketState": "panic_downtrend",
                    "decision": "Sell",
                    "settings": {"cashAvoidFilter": True},
                    "tradeLedger": [{"side": "Sell", "quantity": 999}],
                    "backtestCompletion": {"status": "failed"},
                },
                "confidenceAggregation": {
                    "signal": "Sell",
                    "normalizedNetScore": -1,
                    "settings": {"buyThreshold": 1},
                    "tradeLedger": [{"side": "Sell", "quantity": 999}],
                    "backtestCompletion": {"status": "failed"},
                },
                "metaStrategy": {
                    "decision": "Sell",
                    "familyAggregation": {"trend": -1},
                    "settings": {"enabled": False},
                    "tradeLedger": [{"side": "Sell", "quantity": 999}],
                    "backtestCompletion": {"status": "failed"},
                },
                "dynamicTradingArtifact": {
                    "ownerAlgorithm": "voting_ensemble",
                    "recommendation": "Sell",
                    "thresholdOverrides": {"S1": 0},
                },
            }
        )

        baseline = service.evaluate(baseline_payload)
        changed = service.evaluate(changed_other_algorithms_payload)

        self.assertEqual(stable_json(changed), stable_json(baseline))

    def test_weighted_backend_result_does_not_mutate_other_algorithm_payloads(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        payload = evaluate_payload()
        other_algorithm_state = {
            "votingEnsemble": {"winner": "Buy", "settings": {"minimumVotes": 2}},
            "regimeSelection": {"marketState": "range", "settings": {"enabled": True}},
            "confidenceAggregation": {"signal": "Hold", "settings": {"buyThreshold": 0.6}},
            "metaStrategy": {"decision": "Hold", "settings": {"enabled": True}},
        }
        payload.update(copy.deepcopy(other_algorithm_state))

        before = stable_json({key: payload[key] for key in other_algorithm_state})
        service.evaluate(payload)
        after = stable_json({key: payload[key] for key in other_algorithm_state})

        self.assertEqual(after, before)

    def test_frontend_weighted_calculation_does_not_read_other_algorithm_outputs(self) -> None:
        self.assertNotIn("function calculateWeightedVote", self.frontend_source)
        weighted_slices = "\n".join(
            [
                source_between(self.frontend_source, "function weightedVotingBackendSummary", "function latestWeightedCalculationCandles()"),
                source_between(self.frontend_source, "async function runWeightedDailyBacktestRefresh", "function runConfidenceDailyBacktestFromPreparedCandles"),
            ]
        )
        forbidden_terms = (
            "strategyEnsembleSignals",
            "votingEnsembleScoreSummary",
            "calculateConfidenceAggregation",
            "calculateRegimeSelection",
            "calculateMetaStrategy",
            "state.marketContext",
            "state.dynamicArtifact",
            "state.tradeHistory",
            "state.confidenceTradeHistory",
            "state.regimeTradeHistory",
            "state.metaTradeHistory",
            "state.algoBacktestResult",
            "state.confidenceBacktestResult",
            "marketBreadthProxy(",
        )

        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, weighted_slices)

    def test_weighted_daily_refresh_does_not_wait_for_other_algorithm_backtests(self) -> None:
        daily_refresh_slice = source_between(
            self.frontend_source,
            "async function maybeRunDailyAlgorithmBacktests",
            "async function waitForDailyBacktestArtifacts",
        )

        weighted_start = daily_refresh_slice.index("runWeightedDailyBacktestRefresh")
        voting_start = daily_refresh_slice.index("runVotingEnsembleDailyBacktestRefresh")
        confidence_start = daily_refresh_slice.index("runConfidenceDailyBacktestFromPreparedCandles")
        self.assertLess(weighted_start, voting_start)
        self.assertLess(weighted_start, confidence_start)
        self.assertIn("weightedRefreshResultPromise", daily_refresh_slice)


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def source_between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def evaluate_payload() -> dict:
    rows = candle_rows(count=95)
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "account_equity": 100000,
        "available_buying_power": 100000,
        "capital_available": 100000,
    }


def candle_rows(count: int = 390) -> list[dict]:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
