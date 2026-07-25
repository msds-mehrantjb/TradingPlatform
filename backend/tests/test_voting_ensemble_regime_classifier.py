from __future__ import annotations

import unittest

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier import (
    AdxAtrRegimeClassifier,
    AdxAtrRegimeConfig,
    InMemoryAdxAtrRegimeStateStore,
)
from backend.app.api.trading_engine import V2TradingEngine
from backend.app.algorithms.regime import strategy_registry as other_regime_registry
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


class VotingEnsembleRegimeClassifierTest(unittest.TestCase):
    def test_snapshot_classifier_outputs_owned_regime_contract_fields(self) -> None:
        classifier = AdxAtrRegimeClassifier()
        output = classifier.evaluate_snapshot_output(strong_trend_snapshot())

        self.assertEqual(output.regimeId, "adx_atr_regime")
        self.assertTrue(output.dataReady)
        self.assertIn(output.trendState, {"trend", "range", "unstable", "unknown"})
        self.assertIn(output.volatilityState, {"expansion", "contraction", "stable", "unknown"})
        self.assertTrue(output.marketStructureState)
        self.assertTrue(output.liquidityState)
        self.assertTrue(output.sessionState)
        self.assertTrue(output.eventRiskState)
        for fit in (output.trendFit, output.breakoutFit, output.reversalFit, output.meanReversionFit, output.gapSessionFit):
            self.assertGreaterEqual(fit, 0.0)
            self.assertLessEqual(fit, 1.0)
        self.assertEqual(output.configurationHash, classifier.config.configurationHash)
        self.assertEqual(output.persistenceNamespace, classifier.config.persistenceNamespace)
        self.assertIn("regime.snapshot_point_in_time", output.reasonCodes)

    def test_hysteresis_prevents_one_bar_regime_flapping(self) -> None:
        classifier = AdxAtrRegimeClassifier(
            AdxAtrRegimeConfig(transitionConfirmationBars=2),
            state_store=InMemoryAdxAtrRegimeStateStore(),
        )

        first = classifier.evaluate_snapshot_output(strong_trend_snapshot())
        pending = classifier.evaluate_snapshot_output(range_snapshot())
        confirmed = classifier.evaluate_snapshot_output(range_snapshot())

        self.assertEqual(first.label, "strong_trend")
        self.assertEqual(pending.rawLabel, "low_volatility")
        self.assertEqual(pending.label, "strong_trend")
        self.assertEqual(pending.transitionState, "pending_transition")
        self.assertIn("regime.transition_pending_confirmation", pending.reasonCodes)
        self.assertEqual(confirmed.label, "low_volatility")
        self.assertEqual(confirmed.transitionState, "confirmed_transition")

    def test_service_runs_classifier_once_and_passes_same_result_to_aggregator_and_votes(self) -> None:
        original_classifier = service_module.REGIME_CLASSIFIER
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        spy = SpyClassifier()

        def trend_buy(request: VotingEnsembleEvaluateRequest):
            return _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 80, "trend", "test.trend", features={"strategyId": "multi_timeframe_trend_alignment"})

        def reversal_buy(request: VotingEnsembleEvaluateRequest):
            return _vote("Failed Breakout Reversal", "reversal", "Buy", 80, "reversal", "test.reversal", features={"strategyId": "failed_breakout_reversal"})

        service_module.REGIME_CLASSIFIER = spy
        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        try:
            result = VotingEnsembleService().evaluate(snapshot_payload(candles(30)))
        finally:
            service_module.REGIME_CLASSIFIER = original_classifier
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context

        self.assertEqual(spy.calls, 1)
        self.assertEqual(result["final_signal"], "Buy")
        self.assertIn("regime.snapshot_point_in_time", result["reason_codes"])
        regime_hashes = {vote["features"]["regimeConfigurationHash"] for vote in result["votes"]}
        self.assertEqual(regime_hashes, {spy.last_hash})
        self.assertLess(result["base_score"], 0.8)

    def test_replay_engine_uses_voting_ensemble_owned_classifier(self) -> None:
        engine = V2TradingEngine()

        self.assertIsInstance(engine.replay_engine.components.regimeModule, AdxAtrRegimeClassifier)

    def test_other_algorithm_regime_settings_do_not_affect_voting_ensemble_classifier(self) -> None:
        classifier = AdxAtrRegimeClassifier()
        before = classifier.evaluate_snapshot_output(strong_trend_snapshot())
        original = dict(other_regime_registry.REGIME_STRATEGY_ALIASES)
        try:
            other_regime_registry.REGIME_STRATEGY_ALIASES["ADX/ATR Regime Classifier"] = "cash_avoid_filter"
            after = classifier.evaluate_snapshot_output(strong_trend_snapshot())
        finally:
            other_regime_registry.REGIME_STRATEGY_ALIASES.clear()
            other_regime_registry.REGIME_STRATEGY_ALIASES.update(original)

        self.assertEqual(before.configurationHash, after.configurationHash)
        self.assertEqual(before.trendFit, after.trendFit)
        self.assertEqual(before.label, after.label)


class SpyClassifier(AdxAtrRegimeClassifier):
    def __init__(self) -> None:
        super().__init__(state_store=InMemoryAdxAtrRegimeStateStore())
        self.calls = 0
        self.last_hash = ""

    def evaluate_snapshot(self, snapshot):
        self.calls += 1
        state = super().evaluate_snapshot(snapshot)
        features = {
            **state.features,
            "trendFit": 1.0,
            "reversalFit": 1.0,
            "reasonCodes": [*state.features.get("reasonCodes", []), "regime.spy_high_fit"],
        }
        adjusted = state.model_copy(update={"features": features})
        self.last_hash = adjusted.configurationHash
        return adjusted


def strong_trend_snapshot():
    snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
    return snapshot.model_copy(
        update={
            "features": snapshot.features.model_copy(update={"adx": 40.0, "atr": 0.22, "vwapSlope": 0.02}),
        }
    )


def range_snapshot():
    snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
    return snapshot.model_copy(
        update={
            "features": snapshot.features.model_copy(update={"adx": 10.0, "atr": 0.08, "vwapSlope": 0.0}),
        }
    )


if __name__ == "__main__":
    unittest.main()
