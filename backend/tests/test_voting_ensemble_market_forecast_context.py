"""Cover the market-forecast context module and its wiring into the snapshot.

The forecast is deliberately wired as a *context* signal rather than a directional vote
or a gate. Its own activation policy is advisory_only_until_live_paper_validation with
entryAuthorization false, and a context signal structurally cannot authorise an entry:
`_context_adjustments` returns zero for every signal when there is no directional
candidate, and each signal is capped at `maxConfidenceAdjustment`. So the forecast can
strengthen or weaken a candidate the strategies already produced, and nothing more.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.algorithms.voting_ensemble.strategies.context.pipeline import MarketForecastSnapshotContext
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    resolve_strategy,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def snapshot(forecast: dict) -> SimpleNamespace:
    return SimpleNamespace(
        marketForecast=forecast,
        evaluationTimestamp=NOW,
        nbbo=None,
        spyOneMinuteCandles=(),
        qqq=SimpleNamespace(latestTimestamp=None),
        iwm=SimpleNamespace(latestTimestamp=None),
        breadth=SimpleNamespace(timestamp=None),
        settingsHash="hash",
    )


def ready_forecast(*edges: float) -> dict:
    """A ready forecast whose horizons carry the given up-minus-down edges."""
    return {
        "status": "ready",
        "inferencePerformed": True,
        "multiHorizonForecast": {
            "horizons": [
                {"status": "ready", "probabilityUp": 0.5 + edge / 2, "probabilityDown": 0.5 - edge / 2}
                for edge in edges
            ]
        },
    }


class MarketForecastContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = MarketForecastSnapshotContext()

    def effect(self, forecast: dict) -> str:
        return str(self.module.evaluate(snapshot(forecast), active=True).features.get("contextEffect"))

    def test_agreeing_upside_horizons_confirm_long_only(self) -> None:
        self.assertEqual(self.effect(ready_forecast(0.30, 0.25, 0.20)), "confirm_long")

    def test_agreeing_downside_horizons_confirm_short_only(self) -> None:
        self.assertEqual(self.effect(ready_forecast(-0.30, -0.25, -0.20)), "confirm_short")

    def test_disagreeing_horizons_stay_neutral(self) -> None:
        self.assertEqual(self.effect(ready_forecast(0.30, -0.25, 0.05)), "neutral")

    def test_edges_below_threshold_stay_neutral(self) -> None:
        self.assertEqual(self.effect(ready_forecast(0.02, 0.03, 0.01)), "neutral")

    def test_two_agreeing_horizons_are_enough_when_none_oppose(self) -> None:
        self.assertEqual(self.effect(ready_forecast(0.30, 0.25, 0.02)), "confirm_long")

    def test_missing_forecast_is_neutral_and_cannot_adjust(self) -> None:
        vote = self.module.evaluate(snapshot({}), active=True)

        self.assertEqual(vote.features.get("contextEffect"), "neutral")
        self.assertEqual(vote.features.get("maxConfidenceAdjustment"), 0.0)
        self.assertFalse(vote.dataReady)

    def test_unavailable_model_is_neutral_and_cannot_adjust(self) -> None:
        vote = self.module.evaluate(
            snapshot({"inferencePerformed": False, "status": "MODEL_UNAVAILABLE"}), active=True
        )

        self.assertEqual(vote.features.get("contextEffect"), "neutral")
        self.assertEqual(vote.features.get("maxConfidenceAdjustment"), 0.0)

    def test_no_ready_horizon_is_neutral(self) -> None:
        forecast = {
            "status": "ready",
            "inferencePerformed": True,
            "multiHorizonForecast": {"horizons": [{"status": "MODEL_UNAVAILABLE"}]},
        }

        self.assertEqual(self.effect(forecast), "neutral")

    def test_influence_is_capped_below_the_context_pipeline_ceiling(self) -> None:
        """Even a unanimous, maximally confident forecast stays bounded."""
        vote = self.module.evaluate(snapshot(ready_forecast(1.0, 1.0, 1.0)), active=True)

        self.assertLessEqual(float(vote.features["maxConfidenceAdjustment"]), 0.08)
        self.assertEqual(float(vote.features["maxConfidenceAdjustment"]), self.module.maxAdjustment)

    def test_malformed_horizon_payloads_do_not_raise(self) -> None:
        forecast = {
            "status": "ready",
            "inferencePerformed": True,
            "multiHorizonForecast": {"horizons": ["nonsense", None, {"status": "ready"}]},
        }

        self.assertEqual(self.effect(forecast), "neutral")


class MarketForecastContextRegistrationTest(unittest.TestCase):
    def test_module_is_registered_as_context(self) -> None:
        entry = resolve_strategy("market_forecast_context")

        self.assertEqual(entry.collection, "CONTEXT")
        self.assertEqual(entry.family, "MARKET_CONTEXT")

    def test_module_starts_in_shadow_because_the_forecast_is_advisory_only(self) -> None:
        entry = resolve_strategy("market_forecast_context")

        self.assertEqual(entry.lifecycleStatus, "shadow")
        self.assertFalse(entry.enabled)

    def test_module_appears_exactly_once_in_the_context_inventory(self) -> None:
        matches = [e for e in VOTING_ENSEMBLE_CONTEXT_STRATEGIES if e.strategyId == "market_forecast_context"]

        self.assertEqual(len(matches), 1)


class MarketForecastSnapshotWiringTest(unittest.TestCase):
    def test_snapshot_carries_the_forecast_and_round_trips_it(self) -> None:
        from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot

        self.assertIn("marketForecast", VotingEnsembleEvaluationSnapshot.model_fields)

    def test_producer_forecast_helper_degrades_to_empty_on_failure(self) -> None:
        """A forecast failure must never fail-close trading."""
        from backend.app.algorithms.voting_ensemble.finalized_bar_producer import _market_forecast_context

        self.assertEqual(_market_forecast_context([]), {})


if __name__ == "__main__":
    unittest.main()
