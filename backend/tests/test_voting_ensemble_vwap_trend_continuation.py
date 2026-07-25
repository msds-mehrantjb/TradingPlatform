from __future__ import annotations

import unittest
from datetime import timedelta

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.ensemble.family_aware import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.voting_ensemble.service import (
    VotingEnsembleService,
    _strategy_signal_from_vote,
    _utc_timestamp,
    _vote,
    _vote_from_directional_signal,
)
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.vwap_trend_continuation import (
    VwapTrendContinuationConfig,
    VwapTrendContinuationStrategy,
)
from backend.app.domain.models import Direction, RegimeState
from backend.tests.test_voting_ensemble_snapshot import START, snapshot_payload


class VwapTrendContinuationStrategyTest(unittest.TestCase):
    def test_long_continuation_returns_buy_with_shared_event_id(self) -> None:
        snapshot = _snapshot(_vwap_rows("long"), vwap=100.0, slope=0.05)
        signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="ignored", regime_state=_regime(snapshot))

        self.assertEqual(signal.strategyId, "vwap_trend_continuation")
        self.assertEqual(signal.strategyName, "VWAP Trend Continuation")
        self.assertEqual(signal.family, "trend")
        self.assertEqual(signal.signal, "Buy")
        self.assertTrue(signal.eligible)
        self.assertTrue(signal.dataReady)
        self.assertIn("vwap_trend_continuation.buy_confirmed", signal.reasonCodes)
        self.assertEqual(signal.features["trendEvidenceRole"], "anchor_behavior")
        self.assertEqual(signal.features["eventCorrelationId"], signal.features["trendEventCorrelationId"])
        self.assertTrue(str(signal.features["eventCorrelationId"]).startswith("trend-event-vwap-"))
        self.assertEqual(signal.features["shadowOnly"], True)
        self.assertNotEqual(signal.correlationId, "ignored")

    def test_short_continuation_returns_sell(self) -> None:
        snapshot = _snapshot(_vwap_rows("short"), vwap=100.0, slope=-0.05)
        signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Sell")
        self.assertIn("vwap_trend_continuation.sell_confirmed", signal.reasonCodes)

    def test_missing_vwap_features_fail_closed(self) -> None:
        snapshot = _snapshot(_vwap_rows("long"), vwap=None, slope=None)
        signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertFalse(signal.dataReady)
        self.assertIn("vwap_trend_continuation.missing_vwap_features", signal.reasonCodes)

    def test_pullback_invalidation_returns_hold(self) -> None:
        snapshot = _snapshot(_vwap_rows("invalidated_long"), vwap=100.0, slope=0.05)
        signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("vwap_trend_continuation.pullback_invalidated", signal.reasonCodes)

    def test_higher_timeframe_permission_is_required(self) -> None:
        config = VwapTrendContinuationConfig(minHigherTimeframeTrendScore=0.90)
        snapshot = _snapshot(_vwap_rows("long"), vwap=100.0, slope=0.05)
        signal = VwapTrendContinuationStrategy(config).evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("vwap_trend_continuation.higher_timeframe_permission_missing", signal.reasonCodes)

    def test_excessive_entry_extension_returns_hold(self) -> None:
        snapshot = _snapshot(_vwap_rows("extended_long"), vwap=100.0, slope=0.05)
        signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="corr", regime_state=_regime(snapshot))

        self.assertEqual(signal.signal, "Hold")
        self.assertIn("vwap_trend_continuation.entry_extended", signal.reasonCodes)

    def test_shadow_output_is_captured_without_affecting_active_decision(self) -> None:
        original_directional = service_module.DIRECTIONAL_STRATEGIES
        original_context = service_module.CONTEXT_STRATEGIES
        original_regime = service_module.REGIME_CLASSIFIER

        class FakeRegimeClassifier:
            def evaluate_snapshot(self, snapshot):
                return _regime(snapshot)

        service_module.DIRECTIONAL_STRATEGIES = (
            lambda request: _vote(
                "Multi-Timeframe Trend Alignment",
                "trend",
                "Hold",
                10,
                "Active strategy held.",
                "test.active_hold",
            ),
        )
        service_module.CONTEXT_STRATEGIES = ()
        service_module.REGIME_CLASSIFIER = FakeRegimeClassifier()
        try:
            result = VotingEnsembleService().evaluate(_payload(_vwap_rows("long")))
        finally:
            service_module.DIRECTIONAL_STRATEGIES = original_directional
            service_module.CONTEXT_STRATEGIES = original_context
            service_module.REGIME_CLASSIFIER = original_regime

        self.assertEqual(result["final_signal"], "Hold")
        vwap_shadow = next(vote for vote in result["shadow_directional_votes"] if vote["features"].get("strategyId") == "vwap_trend_continuation")
        self.assertEqual(vwap_shadow["signal"], "Buy")
        self.assertFalse(vwap_shadow["active"])
        self.assertNotIn("trend", result["family_scores"])
        self.assertEqual(result["eligible_strategy_count"], 0)

    def test_overlap_control_recognizes_related_trend_events(self) -> None:
        snapshot = _snapshot(_vwap_rows("long"), vwap=100.0, slope=0.05)
        regime = _regime(snapshot)
        vwap_signal = VwapTrendContinuationStrategy().evaluate(snapshot, correlation_id="corr", regime_state=regime)
        vwap_vote = _vote_from_directional_signal(vwap_signal, regime, active=True)
        shared_event_id = str(vwap_vote.features["eventCorrelationId"])
        mtf_vote = _vote(
            "Multi-Timeframe Trend Alignment",
            "trend",
            "Buy",
            72,
            "Same trend event from timeframe agreement.",
            "test.mtf_same_event",
            features={
                "strategyId": "multi_timeframe_trend_alignment",
                "eventCorrelationId": shared_event_id,
                "trendEvidenceRole": "timeframe_agreement",
            },
        )
        evaluated_at = _utc_timestamp(snapshot.evaluationTimestamp)
        decision = FamilyAwareDeterministicEnsemble(
            FamilyAwareEnsembleConfig(minimumEligibleDirectionalStrategies=1, minimumIndependentSupportingFamilies=1)
        ).aggregate(
            strategySignals=[
                _strategy_signal_from_vote(vwap_vote, evaluated_at, evaluated_at.date(), snapshot.settingsHash),
                _strategy_signal_from_vote(mtf_vote, evaluated_at, evaluated_at.date(), snapshot.settingsHash),
            ],
            contextSignals=[],
            regimeState=regime,
            safetyDecision=None,
            decidedAt=evaluated_at,
            sessionDate=evaluated_at.date(),
        )

        diagnostic = next(signal for signal in decision.strategySignals if "trendOverlapControl" in signal.features)
        group = diagnostic.features["trendOverlapControl"]
        self.assertEqual(group["eventCorrelationId"], shared_event_id)
        self.assertEqual(set(group["strategyIds"]), {"vwap_trend_continuation", "multi_timeframe_trend_alignment"})
        self.assertEqual(group["adjustment"], "same_direction_confidence_aggregation")


def _payload(rows: list[dict]) -> dict:
    payload = snapshot_payload(rows)
    if rows[-1]["close"] < 100.0:
        payload["spy_5m_candles"] = [_htf_row(index, minutes=5, close=100.0 - index * 0.20) for index in range(6)]
        payload["spy_15m_candles"] = [_htf_row(index, minutes=15, close=100.0 - index * 0.30) for index in range(2)]
    return payload


def _snapshot(rows: list[dict], *, vwap: float | None, slope: float | None):
    snapshot = build_live_paper_snapshot(_payload(rows))
    return snapshot.model_copy(update={"features": snapshot.features.model_copy(update={"atr": 1.0, "vwap": vwap, "vwapSlope": slope})})


def _regime(snapshot) -> RegimeState:
    return RegimeState(
        regimeId="test-trend-regime",
        label="test-trend-regime",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.75,
        features={
            "trendFit": 0.85,
            "breakoutFit": 0.35,
            "reversalFit": 0.25,
            "meanReversionFit": 0.25,
            "gapSessionFit": 0.25,
            "eventRiskState": "clear",
            "reasonCodes": ("test.regime",),
        },
        evaluatedAt=snapshot.evaluationTimestamp,
        sessionDate=snapshot.evaluationTimestamp.date(),
        configurationHash="test-regime",
    )


def _vwap_rows(kind: str) -> list[dict]:
    if kind == "short":
        rows = [_row(index, open_=99.90 - index * 0.02, high=100.00 - index * 0.02, low=99.70 - index * 0.02, close=99.82 - index * 0.02, volume=1000) for index in range(18)]
        rows.extend(
            (
                _row(18, open_=99.38, high=99.72, low=99.32, close=99.60, volume=850),
                _row(19, open_=99.60, high=99.82, low=99.46, close=99.70, volume=850),
                _row(20, open_=99.70, high=99.90, low=99.52, close=99.62, volume=850),
                _row(21, open_=99.62, high=99.74, low=99.28, close=99.38, volume=850),
                _row(22, open_=99.34, high=99.38, low=98.92, close=99.02, volume=1600),
            )
        )
        return rows
    rows = [_row(index, open_=100.10 + index * 0.02, high=100.22 + index * 0.02, low=100.02 + index * 0.02, close=100.14 + index * 0.02, volume=1000) for index in range(18)]
    if kind == "invalidated_long":
        rows.extend(
            (
                _row(18, open_=100.48, high=100.60, low=99.65, close=99.72, volume=850),
                _row(19, open_=99.72, high=100.25, low=99.68, close=100.12, volume=850),
                _row(20, open_=100.12, high=100.48, low=100.06, close=100.34, volume=850),
                _row(21, open_=100.34, high=100.58, low=100.30, close=100.46, volume=850),
                _row(22, open_=100.50, high=101.04, low=100.46, close=100.96, volume=1600),
            )
        )
    elif kind == "extended_long":
        rows.extend(
            (
                _row(18, open_=100.48, high=100.70, low=100.24, close=100.58, volume=850),
                _row(19, open_=100.58, high=100.74, low=100.26, close=100.64, volume=850),
                _row(20, open_=100.64, high=100.82, low=100.34, close=100.70, volume=850),
                _row(21, open_=100.70, high=100.88, low=100.42, close=100.78, volume=850),
                _row(22, open_=101.80, high=102.34, low=101.72, close=102.26, volume=1600),
            )
        )
    else:
        rows.extend(
            (
                _row(18, open_=100.48, high=100.72, low=100.22, close=100.58, volume=850),
                _row(19, open_=100.58, high=100.76, low=100.24, close=100.62, volume=850),
                _row(20, open_=100.62, high=100.78, low=100.30, close=100.66, volume=850),
                _row(21, open_=100.66, high=100.82, low=100.36, close=100.70, volume=850),
                _row(22, open_=100.74, high=101.05, low=100.68, close=100.96, volume=1600),
            )
        )
    return rows


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


def _htf_row(index: int, *, minutes: int, close: float) -> dict:
    timestamp = START + timedelta(minutes=index * minutes)
    return {
        "timestamp": timestamp.isoformat(),
        "open": round(close + 0.05, 4),
        "high": round(close + 0.08, 4),
        "low": round(close - 0.08, 4),
        "close": round(close, 4),
        "volume": 5000,
        "symbol": "SPY",
        "finalizationTimestamp": timestamp.isoformat(),
    }


if __name__ == "__main__":
    unittest.main()
