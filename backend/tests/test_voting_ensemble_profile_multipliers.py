from __future__ import annotations

import unittest

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _exit_geometry, _vote
from backend.app.algorithms.voting_ensemble.snapshot import build_backtest_snapshot
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_local_gates import FixedHighFitClassifier
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload

OVERLAY = {"volatility": "high"}  # risk x0.55, stop x1.15, target x0.90, allocation x0.70


def evaluate_with_settings(settings_payload: dict) -> dict:
    """Drive the service to a sized Buy candidate under the given settings payload."""
    original = (service_module.DIRECTIONAL_STRATEGIES, service_module.CONTEXT_STRATEGIES, service_module.REGIME_CLASSIFIER)

    def trend_buy(request: VotingEnsembleEvaluateRequest):
        return _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 80, "trend", "test.trend", features={"strategyId": "multi_timeframe_trend_alignment"})

    def reversal_buy(request: VotingEnsembleEvaluateRequest):
        return _vote("Failed Breakout Reversal", "reversal", "Buy", 80, "reversal", "test.reversal", features={"strategyId": "failed_breakout_reversal"})

    payload = snapshot_payload(candles(30))
    payload["market_context"]["operationalHealthSnapshot"].update({"predictedGrossEdgeDollars": 2.0, "currentOneMinuteVolume": 10_000_000})
    payload["settings"] = dict(settings_payload)
    service_module.DIRECTIONAL_STRATEGIES = (trend_buy, reversal_buy)
    service_module.CONTEXT_STRATEGIES = ()
    service_module.REGIME_CLASSIFIER = FixedHighFitClassifier()
    try:
        return VotingEnsembleService().evaluate(payload)
    finally:
        service_module.DIRECTIONAL_STRATEGIES, service_module.CONTEXT_STRATEGIES, service_module.REGIME_CLASSIFIER = original


class ProfileMultipliersApplyOnceTest(unittest.TestCase):
    """An overlay's multipliers scale risk, stop and target linearly, at settings resolution only.

    They used to be applied to the resolved settings and then again in the risk budget and
    the candidate geometry, so a 0.55 risk multiplier cut risk to 0.30 and a 1.15 stop
    multiplier widened the stop by 1.32.
    """

    def test_resolved_settings_carry_the_multiplied_values_once(self) -> None:
        base = resolve_one_minute_trading_settings(None)
        overlay = resolve_one_minute_trading_settings(OVERLAY)
        profile = overlay.resolvedTradingProfile

        self.assertAlmostEqual(profile.riskMultiplier, 0.55)
        self.assertAlmostEqual(profile.stopMultiplier, 1.15)
        self.assertAlmostEqual(profile.targetMultiplier, 0.90)
        self.assertAlmostEqual(overlay.riskPerTrade.riskPerTradePercent / base.riskPerTrade.riskPerTradePercent, 0.55, places=6)
        self.assertAlmostEqual(overlay.stopPolicy.fixedStopDistanceDollars / base.stopPolicy.fixedStopDistanceDollars, 1.15, places=6)
        self.assertAlmostEqual(overlay.stopPolicy.atrMultiplier / base.stopPolicy.atrMultiplier, 1.15, places=6)
        self.assertAlmostEqual(overlay.targetPolicy.takeProfitR / base.targetPolicy.takeProfitR, 0.90, places=6)

    def test_stop_distance_scales_linearly_with_the_stop_multiplier(self) -> None:
        snap = build_backtest_snapshot(snapshot_payload(candles(60)))
        entry = snap.nbbo.ask
        base = _exit_geometry(snap, Signal.BUY, entry, resolve_one_minute_trading_settings(None))["features"]
        overlay = _exit_geometry(snap, Signal.BUY, entry, resolve_one_minute_trading_settings(OVERLAY))["features"]

        self.assertAlmostEqual(overlay["stopDistance"] / base["stopDistance"], 1.15, places=4)
        # Target R multiple scales by the target multiplier once, on the same stop unit.
        self.assertAlmostEqual(overlay["targetRMultiple"] / base["targetRMultiple"], 0.90, places=4)

    def test_risk_dollars_scale_linearly_with_the_risk_multiplier(self) -> None:
        base = evaluate_with_settings({})
        overlay = evaluate_with_settings(OVERLAY)

        self.assertEqual(base["final_signal"], "Buy")
        self.assertEqual(overlay["final_signal"], "Buy")
        base_risk = float(base["risk_budget"]["risk_budget"])
        overlay_risk = float(overlay["risk_budget"]["risk_budget"])
        self.assertGreater(base_risk, 0.0)
        self.assertAlmostEqual(overlay_risk / base_risk, 0.55, places=3)
        # No second application through the dynamic cap.
        self.assertIn("voting_ensemble.risk_budget.dynamic_cap:1.0000", overlay["risk_budget"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
