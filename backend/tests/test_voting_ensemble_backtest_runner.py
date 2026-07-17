from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.algorithms.voting_ensemble import (
    VOTING_ENSEMBLE_DIRECTIONAL_CATALOG,
    VotingEnsembleBacktestConfig,
    VotingEnsembleBacktestRunner,
)
from backend.app.algorithms.voting_ensemble.models import VotingCandle, VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import evaluate_first_pullback_after_open
from backend.app.algorithms.voting_ensemble.ml_snapshots import stage_result_to_v2_training_row
from backend.app.api.trading_engine import V2TradingEngine
from backend.app.meta_strategy_training import DEFAULT_META_LABEL_VERSION, v2_training_compatibility_report


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class VotingEnsembleBacktestRunnerTest(unittest.TestCase):
    def test_first_pullback_helper_can_fire_before_twenty_two_session_candles(self) -> None:
        rows = (
            voting_candle(0, 100.00, 100.10, 99.90, 100.00, 100000),
            voting_candle(1, 100.00, 100.50, 99.95, 100.45, 230000),
            voting_candle(2, 100.45, 101.05, 100.35, 100.95, 240000),
            voting_candle(3, 100.95, 101.45, 100.90, 101.35, 260000),
            voting_candle(4, 101.35, 101.40, 100.95, 101.05, 120000),
            voting_candle(5, 101.05, 101.10, 100.80, 100.90, 110000),
            voting_candle(6, 100.90, 101.50, 100.85, 101.45, 150000),
        )

        vote = evaluate_first_pullback_after_open(VotingEnsembleEvaluateRequest(candles=rows, data_timestamp=rows[-1].timestamp))

        self.assertEqual(vote.signal, "Buy")
        self.assertTrue(vote.eligible)
        self.assertEqual(vote.features["reasonCode"], "voting_ensemble.first_pullback.completed_buy")

    def test_runner_uses_only_voting_ensemble_catalog(self) -> None:
        runner = VotingEnsembleBacktestRunner(config=VotingEnsembleBacktestConfig(warmupCandles=5, includeDecisionRecords=True))

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=candles(12),
            spy_5m_candles=candles(4, minutes=5),
            timeframe="1Min",
        )

        self.assertEqual(result["backtestVersion"], "voting_ensemble_dedicated_backtest_v1")
        self.assertEqual(tuple(result["strategyCatalog"]["directional"]), VOTING_ENSEMBLE_DIRECTIONAL_CATALOG)
        self.assertNotIn("VWAP Trend Continuation", result["strategyCatalog"]["directional"])
        self.assertNotIn("Opening Range Breakout", result["strategyCatalog"]["directional"])
        self.assertEqual(result["strategyCatalog"]["removedVoters"], ["Ensemble Strategy Voting"])

    def test_runner_reports_missing_auxiliary_data_without_synthetic_substitution(self) -> None:
        runner = VotingEnsembleBacktestRunner(config=VotingEnsembleBacktestConfig(warmupCandles=5, includeDecisionRecords=True))

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=candles(12),
            spy_5m_candles=candles(4, minutes=5),
            timeframe="1Min",
        )

        self.assertFalse(result["dataQuality"]["usesActualQqqIwm"])
        self.assertFalse(result["dataQuality"]["usesSyntheticQqqIwm"])
        self.assertIn("qqq_candles", result["dataQuality"]["missingInputs"])
        self.assertIn("iwm_candles", result["dataQuality"]["missingInputs"])
        first = result["decisionRecords"][0]
        relative_strength = [signal for signal in first["contextSignals"] if signal["strategy"] == "Relative Strength vs QQQ/IWM"][0]
        self.assertEqual(relative_strength["signal"], "Hold")
        self.assertFalse(relative_strength["dataReady"])

    def test_runner_accepts_real_independent_timeframes_and_auxiliary_streams(self) -> None:
        runner = VotingEnsembleBacktestRunner(config=VotingEnsembleBacktestConfig(warmupCandles=5, includeDecisionRecords=True))

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=candles(45),
            spy_5m_candles=candles(12, minutes=5),
            spy_15m_candles=candles(6, minutes=15),
            qqq_candles=candles(45, symbol="QQQ"),
            iwm_candles=candles(45, symbol="IWM"),
            breadth_components={
                "XLK": candles(45, symbol="XLK"),
                "XLF": candles(45, symbol="XLF"),
                "XLV": candles(45, symbol="XLV"),
            },
            timeframe="1Min",
        )

        self.assertTrue(result["dataQuality"]["usesActual5m"])
        self.assertTrue(result["dataQuality"]["usesActual15m"])
        self.assertTrue(result["dataQuality"]["usesActualQqqIwm"])
        self.assertFalse(result["dataQuality"]["usesSyntheticQqqIwm"])
        self.assertEqual(result["dataQuality"]["breadthComponentCount"], 3)
        self.assertNotIn("qqq_candles", result["dataQuality"]["missingInputs"])
        self.assertNotIn("iwm_candles", result["dataQuality"]["missingInputs"])

    def test_runner_uses_next_executable_price_after_decision(self) -> None:
        runner = VotingEnsembleBacktestRunner(
            service=AlwaysBuyService(),
            config=VotingEnsembleBacktestConfig(
                warmupCandles=3,
                targetDistance=0.20,
                stopDistance=0.20,
                quantity=1,
                includeDecisionRecords=True,
            ),
        )

        result = runner.run(symbol="SPY", spy_1m_candles=candles(8), timeframe="1Min")

        self.assertGreater(len(result["trades"]), 0)
        trade = result["trades"][0]
        self.assertGreater(trade["entryAt"], trade["decisionTimestampUtc"])
        first_record = result["decisionRecords"][0]
        self.assertIn("execution.market_entry_next_executable", first_record["fill"]["reasonCodes"])

    def test_replay_stage_result_exports_v2_training_snapshot(self) -> None:
        runner = VotingEnsembleBacktestRunner(
            service=AlwaysBuyService(),
            config=VotingEnsembleBacktestConfig(
                warmupCandles=3,
                targetDistance=0.20,
                stopDistance=0.20,
                quantity=1,
                includeDecisionRecords=True,
            ),
        )

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=candles(8),
            spy_5m_candles=candles(4, minutes=5),
            spy_15m_candles=candles(2, minutes=15),
            qqq_candles=candles(8, symbol="QQQ"),
            iwm_candles=candles(8, symbol="IWM"),
            breadth_components={"XLK": candles(8, symbol="XLK")},
            timeframe="1Min",
        )

        row = stage_result_to_v2_training_row(
            result["stageResults"][0],
            timeframe="1Min",
            data_quality=result["dataQuality"],
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["snapshotSchemaVersion"], "decision_snapshot_v2")
        self.assertEqual(row["sourceSchemaVersion"], "voting_ensemble_replay_stage_result_v1")
        self.assertTrue(row["eligibleForTraining"])
        self.assertEqual(row["trainingLabel"], "BUY")
        self.assertIn("familyAggregation", row["metaModelFeatures"])
        report = v2_training_compatibility_report([row], label_version=DEFAULT_META_LABEL_VERSION)
        self.assertEqual(report["compatibleRowCount"], 1)
        self.assertEqual(report["excludedRowCount"], 0)

    def test_runner_stores_stage_result_for_every_warmup_complete_timestamp(self) -> None:
        runner = VotingEnsembleBacktestRunner(
            service=AlwaysBuyService(),
            config=VotingEnsembleBacktestConfig(
                warmupCandles=3,
                targetDistance=10.0,
                stopDistance=10.0,
                maximumHoldingMinutes=30,
                includeDecisionRecords=True,
            ),
        )

        result = runner.run(symbol="SPY", spy_1m_candles=candles(8), timeframe="1Min")

        expected_timestamps = len(candles(8)) - 3 + 1
        self.assertEqual(result["decisionCount"], expected_timestamps)
        self.assertEqual(result["stageResultCount"], expected_timestamps)
        self.assertEqual(len(result["stageResults"]), expected_timestamps)
        self.assertTrue(all("stages" in record for record in result["stageResults"]))
        self.assertTrue(any(record["stages"]["safetyAndPosition"]["positionActive"] for record in result["stageResults"][1:]))
        first = result["stageResults"][0]["stages"]
        self.assertIn("inputData", first)
        self.assertIn("directionalStrategies", first)
        self.assertIn("contextSignals", first)
        self.assertIn("familyAwareEnsemble", first)
        self.assertIn("contextAdjustment", first)
        self.assertIn("safetyAndPosition", first)
        self.assertIn("candidateOrder", first)
        self.assertIn("execution", first)

    def test_v2_engine_default_replay_uses_only_approved_voting_ensemble_inputs(self) -> None:
        engine = V2TradingEngine()

        strategy_names = [strategy.registryEntry.strategyName for strategy in engine.replay_engine.components.directionalStrategies]
        context_names = [module.registryEntry.strategyName for module in engine.replay_engine.components.contextModules]

        self.assertEqual(
            strategy_names,
            [
                "Multi-Timeframe Trend Alignment",
                "First Pullback After Open",
                "Failed Breakout Reversal",
                "Liquidity Sweep Reversal",
                "Bollinger/ATR Reversion",
            ],
        )
        self.assertEqual(context_names, ["Relative Strength vs QQQ/IWM", "Market Breadth Momentum"])
        self.assertNotIn("VWAP Trend Continuation", strategy_names)
        self.assertNotIn("Opening Range Breakout", strategy_names)
        self.assertNotIn("Volatility Breakout", strategy_names)
        self.assertNotIn("VWAP Mean Reversion", strategy_names)
        self.assertNotIn("Gap Continuation / Gap Fade", strategy_names)


class AlwaysBuyService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = payload["data_timestamp"]
        vote = {
            "strategy": "Multi-Timeframe Trend Alignment",
            "family": "trend",
            "role": "directional",
            "signal": "Buy",
            "direction": 1,
            "confidence": 0.8,
            "active": True,
            "eligible": True,
            "dataReady": True,
            "regimeFit": 1.0,
            "reliability": 1.0,
            "reason": "Synthetic buy setup.",
            "features": {},
        }
        return {
            "service_version": "test",
            "symbol": "SPY",
            "evaluated_at": timestamp,
            "data_timestamp": timestamp,
            "final_signal": "Buy",
            "votes": [vote],
            "context_signals": [],
            "context_confirmation": {
                "outcome": "not_applicable",
                "detail": "test",
                "evidence": [],
                "confirmations": 0,
                "conflicts": 0,
            },
            "counts": {"Buy": 1, "Sell": 0, "Hold": 0},
            "eligible_counts": {"Buy": 1, "Sell": 0, "Hold": 0},
            "family_scores": {"trend": 0.8},
            "base_score": 0.8,
            "context_adjusted_score": 0.8,
            "context_agreements": 0,
            "context_conflicts": 0,
            "context_adjustment_reason": "test",
            "family_support": {"Buy": 1, "Sell": 0, "Hold": 0},
            "safety_gate_failed": False,
            "removed_voters": ["Ensemble Strategy Voting"],
            "reason_codes": ["test.buy"],
        }


def candles(count: int, *, minutes: int = 1, symbol: str = "SPY") -> list[dict[str, Any]]:
    rows = []
    price = 100.0
    for index in range(count):
        timestamp = START + timedelta(minutes=index * minutes)
        close = price + 0.08
        rows.append(
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": round(price, 4),
                "high": round(close + 0.12, 4),
                "low": round(price - 0.05, 4),
                "close": round(close, 4),
                "volume": 1000 + index * 25,
                "symbol": symbol,
                "timeframe": "1Min" if minutes == 1 else "5Min",
            }
        )
        price = close
    return rows


def voting_candle(index: int, open_: float, high: float, low: float, close: float, volume: float) -> VotingCandle:
    return VotingCandle(
        timestamp=START + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


if __name__ == "__main__":
    unittest.main()
