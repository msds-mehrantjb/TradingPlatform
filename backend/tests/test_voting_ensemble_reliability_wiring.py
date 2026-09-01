"""Regression cover for the Voting Ensemble accuracy-weighting decision path.

The reliability estimator, the settings that switch it on, and the family weights
are only useful if the live aggregation path actually consumes them. These tests
pin that wiring so a future refactor cannot silently return the ensemble to a
flat, non-discriminating reliability of 0.5.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from backend.app.algorithms.voting_ensemble.service import (
    _aggregate_with_family_engine,
    _family_engine_for_settings,
    _family_weights,
    _reliability_mode,
    _reliability_observations,
    _vote,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.domain.models import Direction, OperatingMode, RegimeState, StrategyFamily


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)

TREND_STRATEGIES = ("multi_timeframe_trend_alignment", "first_pullback_after_open")
FADE_STRATEGIES = (
    "failed_breakout_reversal",
    "liquidity_sweep_reversal",
    "bollinger_band_reversion",
    "atr_overextension_reversion",
)


def trending_regime() -> RegimeState:
    return RegimeState(
        regimeId="adx_atr_regime",
        label="trend_up",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.8,
        features={
            "trendFit": 0.92,
            "breakoutFit": 0.80,
            "reversalFit": 0.20,
            "meanReversionFit": 0.22,
            "gapSessionFit": 0.30,
        },
        evaluatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="test",
    )


def snapshot_stub() -> SimpleNamespace:
    return SimpleNamespace(
        evaluationTimestamp=NOW,
        settingsHash="test",
        sessionState={"sessionSegment": "regular_session"},
    )


def directional_votes() -> tuple:
    return (
        _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 85, "trend up", "rc.trend"),
        _vote("First Pullback After Open", "trend", "Buy", 80, "pullback", "rc.pullback"),
        _vote("Failed Breakout Reversal", "reversal", "Sell", 40, "failed breakout", "rc.fbr"),
        _vote("Liquidity Sweep Reversal", "reversal", "Sell", 35, "sweep", "rc.lsr"),
        _vote("Bollinger/ATR Reversion", "mean_reversion", "Sell", 55, "band", "rc.bb"),
        _vote("ATR Overextension Reversion", "mean_reversion", "Sell", 60, "extended", "rc.atr"),
    )


def observation(strategy_id: str, direction: str, outcome: float, index: int) -> dict:
    return {
        "strategyId": strategy_id,
        "direction": direction,
        "regime": "trend_up",
        "sessionSegment": "regular_session",
        "volatilityState": "normal",
        "sampleWindow": "rolling_60_trades",
        "outcomeR": outcome,
        "transactionCostR": 0.05,
        "decisionTimestamp": (NOW - timedelta(days=40 - index)).isoformat(),
        "completedAt": (NOW - timedelta(days=39 - index)).isoformat(),
    }


def accurate_trend_history() -> list[dict]:
    history: list[dict] = []
    for strategy_id in TREND_STRATEGIES:
        history += [observation(strategy_id, "BUY", 1.5, index) for index in range(20)]
    for strategy_id in FADE_STRATEGIES:
        history += [observation(strategy_id, "SELL", -1.2, index) for index in range(20)]
    return history


def aggregate(payload: dict):
    return _aggregate_with_family_engine(
        directional_votes(),
        (),
        snapshot_stub(),
        trending_regime(),
        None,
        settings=resolve_one_minute_trading_settings({}),
        payload=payload,
    )


class VotingEnsembleReliabilityWiringTest(unittest.TestCase):
    def test_baseline_settings_enable_active_accuracy_weighting(self) -> None:
        settings = resolve_one_minute_trading_settings({})

        self.assertEqual(settings.aggregationThresholds.reliabilityWeightingMode, "active")
        self.assertEqual(_family_engine_for_settings(settings).config.reliabilityMode, OperatingMode.ACTIVE)

    def test_settings_family_weights_reach_the_engine_config(self) -> None:
        settings = resolve_one_minute_trading_settings({})
        config = _family_engine_for_settings(settings).config

        self.assertEqual(
            {family.value for family in config.familyWeights},
            {"TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION"},
        )

    def test_family_weights_accept_snake_case_settings_keys(self) -> None:
        weights = _family_weights(SimpleNamespace(familyWeights={"trend": 2.0, "mean_reversion": 0.5}))

        self.assertEqual(weights[StrategyFamily.TREND], 2.0)
        self.assertEqual(weights[StrategyFamily.MEAN_REVERSION], 0.5)
        self.assertEqual(weights[StrategyFamily.REVERSAL], 1.0)

    def test_zero_weight_is_floored_so_a_muted_family_does_not_break_config(self) -> None:
        weights = _family_weights(SimpleNamespace(familyWeights={"reversal": 0.0}))

        self.assertGreater(weights[StrategyFamily.REVERSAL], 0.0)
        self.assertLess(weights[StrategyFamily.REVERSAL], 1e-3)

    def test_unknown_reliability_mode_falls_back_to_shadow(self) -> None:
        self.assertEqual(_reliability_mode(SimpleNamespace(reliabilityWeightingMode="nonsense")), OperatingMode.SHADOW)
        self.assertEqual(_reliability_mode(None), OperatingMode.SHADOW)

    def test_malformed_observation_rows_are_skipped_not_raised(self) -> None:
        payload = {
            "strategy_reliability_observations": [
                observation("multi_timeframe_trend_alignment", "BUY", 1.0, 0),
                {"strategyId": "broken"},
                "not-a-row",
                None,
            ]
        }

        observations = _reliability_observations(payload)

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].strategyId, "multi_timeframe_trend_alignment")

    def test_absent_history_leaves_every_strategy_on_neutral_reliability(self) -> None:
        decision = aggregate({})

        reliabilities = {signal.strategyId: float(signal.reliability) for signal in decision.strategySignals}
        self.assertEqual(set(reliabilities.values()), {0.5})

    def test_accuracy_history_separates_accurate_from_inaccurate_strategies(self) -> None:
        decision = aggregate({"strategy_reliability_observations": accurate_trend_history()})

        reliabilities = {signal.strategyId: float(signal.reliability) for signal in decision.strategySignals}
        for strategy_id in TREND_STRATEGIES:
            self.assertGreater(reliabilities[strategy_id], 0.5, strategy_id)
        for strategy_id in FADE_STRATEGIES:
            self.assertLess(reliabilities[strategy_id], 0.5, strategy_id)

    def test_accuracy_weighting_lifts_the_score_of_the_historically_accurate_side(self) -> None:
        without_history = aggregate({})
        with_history = aggregate({"strategy_reliability_observations": accurate_trend_history()})

        self.assertGreater(with_history.finalScore, without_history.finalScore)

    def test_observations_are_read_from_market_context_as_well(self) -> None:
        payload = {"market_context": {"strategyReliabilityObservations": accurate_trend_history()}}

        decision = aggregate(payload)

        reliabilities = {signal.strategyId: float(signal.reliability) for signal in decision.strategySignals}
        self.assertGreater(reliabilities["multi_timeframe_trend_alignment"], 0.5)


if __name__ == "__main__":
    unittest.main()
