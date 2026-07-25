from __future__ import annotations

import unittest

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.tests.test_voting_ensemble_local_gates import FixedHighFitClassifier
from backend.tests.test_voting_ensemble_snapshot import candles as snapshot_candles
from backend.tests.test_voting_ensemble_snapshot import snapshot_payload


class VotingEnsemblePipelineParityTest(unittest.TestCase):
    def test_golden_pre_execution_parity_across_paper_replay_and_backtest_modes(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_classifier = service_module.REGIME_CLASSIFIER

        def trend_buy(request: VotingEnsembleEvaluateRequest):
            return _vote(
                "Multi-Timeframe Trend Alignment",
                "trend",
                "Buy",
                80,
                "trend",
                "test.trend",
                features={"strategyId": "multi_timeframe_trend_alignment"},
            )

        def reversal_buy(request: VotingEnsembleEvaluateRequest):
            return _vote(
                "Failed Breakout Reversal",
                "reversal",
                "Buy",
                80,
                "reversal",
                "test.reversal",
                features={"strategyId": "failed_breakout_reversal"},
            )

        payload = snapshot_payload(snapshot_candles(30))
        payload["market_context"]["operationalHealthSnapshot"].update(
            {"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000}
        )
        service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
        try:
            pipeline = VotingEnsemblePipeline(service=VotingEnsembleService())
            paper = pipeline.run(payload, mode="paper")
            replay = pipeline.run(payload, mode="replay")
            backtest = pipeline.run(payload, mode="backtest")
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_classifier

        self.assertEqual(paper["componentOrder"], replay["componentOrder"])
        self.assertEqual(paper["componentOrder"], backtest["componentOrder"])
        self.assertEqual(paper["preExecutionDecision"], replay["preExecutionDecision"])
        self.assertEqual(paper["preExecutionDecision"], backtest["preExecutionDecision"])
        self.assertEqual(paper["orderPlan"], replay["orderPlan"])
        self.assertEqual(paper["orderPlan"], backtest["orderPlan"])
        self.assertEqual(paper["orderPlan"]["orderType"], "LIMIT")
        self.assertGreater(paper["orderPlan"]["quantity"], 0)
        self.assertIn("historical_event_delivery", backtest["modeSpecificResponsibilities"])


if __name__ == "__main__":
    unittest.main()
