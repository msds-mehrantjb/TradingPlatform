from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.atr_overextension_reversion import AtrOverextensionReversionStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.bollinger_band_reversion import BollingerBandReversionStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.failed_breakout_reversal import SnapshotFailedBreakoutReversalStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.first_pullback_after_open import SnapshotFirstPullbackAfterOpenStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.liquidity_sweep_reversal import SnapshotLiquiditySweepReversalStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.multi_timeframe_trend_alignment import SnapshotMultiTimeframeTrendAlignmentStrategy
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import DirectionalStrategySignal
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, active_module_ids, canonical_strategy_id
from backend.tests.test_voting_ensemble_snapshot import START, candle, candles, snapshot_payload


class VotingEnsembleDirectionalStrategyTest(unittest.TestCase):
    def test_every_active_strategy_returns_typed_snapshot_signal_contract(self) -> None:
        snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
        strategies = (
            SnapshotMultiTimeframeTrendAlignmentStrategy(),
            SnapshotFirstPullbackAfterOpenStrategy(),
            SnapshotFailedBreakoutReversalStrategy(),
            SnapshotLiquiditySweepReversalStrategy(),
            BollingerBandReversionStrategy(),
            AtrOverextensionReversionStrategy(),
        )

        for strategy in strategies:
            with self.subTest(strategy=strategy.strategyId):
                signal = strategy.evaluate(snapshot, correlation_id="corr-1")
                self.assertIsInstance(signal, DirectionalStrategySignal)
                self.assertIn(signal.signal, {"Buy", "Sell", "Hold"})
                self.assertEqual(signal.correlationId, "corr-1")
                self.assertEqual(signal.strategyId, strategy.strategyId)
                self.assertTrue(signal.strategyVersion)
                self.assertTrue(signal.evidence)
                self.assertTrue(signal.reasonCodes)
                self.assertTrue(signal.eventCorrelationId)
                self.assertTrue(signal.setupId)
                self.assertTrue(signal.evidenceRole)
                self.assertTrue(signal.triggerTimestamp)
                self.assertTrue(signal.confirmationTimestamp)
                for key in ("eventCorrelationId", "setupId", "evidenceRole", "referenceLevelId", "triggerTimestamp", "confirmationTimestamp"):
                    self.assertIn(key, signal.features)

    def test_bollinger_and_atr_reversion_are_independent_mean_reversion_modules(self) -> None:
        bollinger_snapshot = build_live_paper_snapshot(snapshot_payload(reentry_rows()))
        bollinger_snapshot = bollinger_snapshot.model_copy(
            update={
                "features": bollinger_snapshot.features.model_copy(
                    update={"bollingerLower": 100.0, "bollingerMiddle": 100.5, "bollingerUpper": 101.0, "atr": 1.0, "vwap": 100.5}
                )
            }
        )
        atr_snapshot = build_live_paper_snapshot(snapshot_payload(overextension_rows()))
        atr_snapshot = atr_snapshot.model_copy(
            update={
                "features": atr_snapshot.features.model_copy(
                    update={"bollingerLower": 99.0, "bollingerMiddle": 100.0, "bollingerUpper": 101.0, "atr": 1.0, "vwap": 100.0}
                )
            }
        )

        bollinger = BollingerBandReversionStrategy().evaluate(bollinger_snapshot, correlation_id="boll")
        atr = AtrOverextensionReversionStrategy().evaluate(atr_snapshot, correlation_id="atr")

        self.assertEqual(bollinger.strategyId, "bollinger_band_reversion")
        self.assertEqual(atr.strategyId, "atr_overextension_reversion")
        self.assertEqual(bollinger.family, "mean_reversion")
        self.assertEqual(atr.family, "mean_reversion")
        self.assertEqual(bollinger.signal, "Buy")
        self.assertEqual(atr.signal, "Sell")
        self.assertIn("bandPosition", bollinger.features)
        self.assertIn("extensionAtr", atr.features)
        self.assertNotEqual(bollinger.reasonCodes, atr.reasonCodes)

    def test_deprecated_bollinger_atr_alias_does_not_create_duplicate_active_votes(self) -> None:
        active = active_module_ids(StrategyCollection.DIRECTIONAL)

        self.assertEqual(canonical_strategy_id("Bollinger/ATR Reversion"), "bollinger_band_reversion")
        self.assertEqual(canonical_strategy_id("Bollinger Band Reversion"), "bollinger_band_reversion")
        self.assertEqual(canonical_strategy_id("ATR Overextension Reversion"), "atr_overextension_reversion")
        self.assertNotIn("bollinger_atr_reversion", active)
        self.assertEqual(active.count("bollinger_band_reversion"), 1)
        self.assertEqual(active.count("atr_overextension_reversion"), 1)
        self.assertEqual(len(active), len(set(active)))

    def test_service_contains_orchestration_not_embedded_directional_algorithms(self) -> None:
        service_source = Path("backend/app/algorithms/voting_ensemble/service.py").read_text(encoding="utf-8")

        for embedded_helper in (
            "def _timeframe_trend_state",
            "def _opening_impulse",
            "def _failed_breakout_levels",
            "def _liquidity_sweep_levels",
            "def _bollinger",
            "def _atr_overextension",
        ):
            self.assertNotIn(embedded_helper, service_source)


def reentry_rows() -> list[dict]:
    rows = candles(29)
    rows.append(
        candle(START.replace(minute=START.minute + 29), close=100.10)
        | {"open": 99.80, "high": 100.20, "low": 99.50, "close": 100.10, "volume": 1200}
    )
    return rows


def overextension_rows() -> list[dict]:
    rows = candles(29)
    rows.append(
        candle(START.replace(minute=START.minute + 29), close=102.00)
        | {"open": 102.30, "high": 102.50, "low": 101.90, "close": 102.00, "volume": 1200}
    )
    return rows


if __name__ == "__main__":
    unittest.main()
