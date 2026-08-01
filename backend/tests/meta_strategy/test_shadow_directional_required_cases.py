from __future__ import annotations

import unittest
from dataclasses import asdict, is_dataclass

from backend.tests.test_meta_strategy_step9_directional_strategies import snapshot_fixture, strategy_for


SHADOW_CASES = {
    "liquidity_sweep_reversal": {
        "buy": {"sweepSide": "sell_side", "rejectionWickRatio": 1.2, "features_extra": {"microstructureEvidence": {"reliable": True, "orderFlowImbalance": -0.8}}},
        "sell": {"sweepSide": "buy_side", "rejectionWickRatio": 1.2, "features_extra": {"microstructureEvidence": {"reliable": True, "orderFlowImbalance": 0.8}}},
        "hold": {"sweepSide": "none", "rejectionWickRatio": 0.2, "features_extra": {"microstructureEvidence": {"reliable": True, "orderFlowImbalance": 0.1}}},
        "missing": {"features_extra": {"microstructureEvidence": {}}},
        "wrong_regime": {"sweepSide": "sell_side", "rejectionWickRatio": 1.2, "features_extra": {"microstructureEvidence": {"reliable": False}}},
    },
    "gap_continuation": {
        "buy": {"gapState": "gap_up", "gapPercent": 1.0, "gapTradeType": "continuation", "spyVsQqq": 1.01, "session_phase": "MORNING"},
        "sell": {"gapState": "gap_down", "gapPercent": -1.0, "gapTradeType": "continuation", "spyVsQqq": 1.01, "session_phase": "MORNING"},
        "hold": {"gapState": "flat", "gapPercent": 0.1, "gapTradeType": "continuation", "session_phase": "MORNING"},
        "missing": {"gap_state": {}},
        "wrong_regime": {"gapState": "gap_up", "gapPercent": 1.0, "gapTradeType": "continuation", "session_phase": "CLOSED"},
    },
    "gap_fade": {
        "buy": {"gapState": "gap_down", "gapPercent": -1.0, "gapTradeType": "fade", "spyVsQqq": 1.01, "session_phase": "MORNING"},
        "sell": {"gapState": "gap_up", "gapPercent": 1.0, "gapTradeType": "fade", "spyVsQqq": 1.01, "session_phase": "MORNING"},
        "hold": {"gapState": "flat", "gapPercent": 0.1, "gapTradeType": "fade", "session_phase": "MORNING"},
        "missing": {"gap_state": {}},
        "wrong_regime": {"gapState": "gap_down", "gapPercent": -1.0, "gapTradeType": "fade", "session_phase": "CLOSED"},
    },
    "economic_event_reaction": {
        "buy": {"economic_event_state": {"state": "released", "active": True, "directionalBias": "bullish"}, "relative_volume": 2.5},
        "sell": {"economic_event_state": {"state": "released", "active": True, "directionalBias": "bearish"}, "relative_volume": 2.5},
        "hold": {"economic_event_state": {"state": "none", "active": False, "directionalBias": "none"}, "relative_volume": 1.0},
        "missing": {"economic_event_state": {}},
        "wrong_regime": {"economic_event_state": {"state": "scheduled", "active": False, "directionalBias": "bullish"}, "relative_volume": 0.0},
    },
}


class MetaStrategyShadowDirectionalRequiredCasesTest(unittest.TestCase):
    def test_shadow_directional_strategies_have_buy_sell_hold_and_fail_closed_cases(self) -> None:
        for strategy_id, cases in SHADOW_CASES.items():
            strategy = strategy_for(strategy_id)
            with self.subTest(strategy=strategy_id, case="buy"):
                self.assertEqual(strategy.evaluate(snapshot_fixture(**cases["buy"])).signal, "BUY")
            with self.subTest(strategy=strategy_id, case="sell"):
                self.assertEqual(strategy.evaluate(snapshot_fixture(**cases["sell"])).signal, "SELL")
            with self.subTest(strategy=strategy_id, case="hold"):
                self.assertEqual(strategy.evaluate(snapshot_fixture(**cases["hold"])).signal, "HOLD")
            with self.subTest(strategy=strategy_id, case="missing"):
                result = strategy.evaluate(snapshot_fixture(**cases["missing"]))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.eligible)
            with self.subTest(strategy=strategy_id, case="wrong_regime"):
                self.assertEqual(strategy.evaluate(snapshot_fixture(**cases["wrong_regime"])).signal, "HOLD")

    def test_shadow_directional_repeated_evaluation_is_deterministic(self) -> None:
        for strategy_id, cases in SHADOW_CASES.items():
            strategy = strategy_for(strategy_id)
            snapshot = snapshot_fixture(**cases["buy"])
            first = strategy.evaluate(snapshot)
            second = strategy.evaluate(snapshot)
            with self.subTest(strategy=strategy_id):
                self.assertEqual(result_payload(first), result_payload(second))

    def test_shadow_directional_insufficient_warmup_and_non_point_in_time_hold(self) -> None:
        for strategy_id, cases in SHADOW_CASES.items():
            strategy = strategy_for(strategy_id)
            warmup = strategy.evaluate(snapshot_fixture(**cases["buy"], candle_count=1))
            stale = strategy.evaluate(snapshot_fixture(**cases["buy"]).model_copy(update={"point_in_time": False}))
            with self.subTest(strategy=strategy_id):
                self.assertEqual(warmup.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.insufficient_warmup", warmup.reason_codes)
                self.assertEqual(stale.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.snapshot_not_point_in_time", stale.reason_codes)


def result_payload(result):
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if is_dataclass(result):
        return asdict(result)
    return dict(getattr(result, "__dict__", {}))


if __name__ == "__main__":
    unittest.main()
