from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import DIRECTIONAL_STRATEGIES, MetaStrategyMarketSnapshot


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyStep9DirectionalStrategiesTest(unittest.TestCase):
    maxDiff = None

    def test_every_directional_strategy_has_focused_behavior_coverage(self) -> None:
        self.assertEqual(len(DIRECTIONAL_STRATEGIES), 10)
        for entry in DIRECTIONAL_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            cases = strategy_cases(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id, case="buy"):
                result = strategy.evaluate(snapshot_fixture(**cases["buy"]))
                self.assertEqual(result.signal, "BUY")
                self.assertTrue(result.eligible)
                self.assertEqual(result.family, entry.family)
                self.assertTrue(result.evidence)
                self.assertTrue(all(result.required_input_status.values()))

            with self.subTest(strategy=entry.strategy_id, case="sell"):
                result = strategy.evaluate(snapshot_fixture(**cases["sell"]))
                self.assertEqual(result.signal, "SELL")
                self.assertTrue(result.eligible)

            with self.subTest(strategy=entry.strategy_id, case="hold"):
                result = strategy.evaluate(snapshot_fixture(**cases["hold"]))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.eligible)

            with self.subTest(strategy=entry.strategy_id, case="missing_inputs"):
                result = strategy.evaluate(snapshot_fixture(**cases["missing"]))
                self.assertEqual(result.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.missing_required_inputs", result.reason_codes)
                self.assertFalse(all(result.required_input_status.values()))

            with self.subTest(strategy=entry.strategy_id, case="warmup"):
                result = strategy.evaluate(snapshot_fixture(**cases["buy"], candle_count=max(0, entry.minimum_warmup - 1)))
                self.assertEqual(result.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.insufficient_warmup", result.reason_codes)

            with self.subTest(strategy=entry.strategy_id, case="exact_threshold"):
                at_threshold = strategy.evaluate(snapshot_fixture(**cases["threshold_at"]))
                below_threshold = strategy.evaluate(snapshot_fixture(**cases["threshold_below"]))
                self.assertEqual(at_threshold.signal, cases.get("threshold_signal", "BUY"))
                self.assertEqual(below_threshold.signal, "HOLD")

            with self.subTest(strategy=entry.strategy_id, case="determinism"):
                snapshot = snapshot_fixture(**cases["buy"])
                self.assertEqual(strategy.evaluate(snapshot), strategy.evaluate(snapshot))

            with self.subTest(strategy=entry.strategy_id, case="snapshot_immutability"):
                snapshot = snapshot_fixture(**cases["buy"])
                before = snapshot.deterministic_hash()
                strategy.evaluate(snapshot)
                after = snapshot.deterministic_hash()
                self.assertEqual(after, before)
                with self.assertRaises(ValidationError):
                    setattr(snapshot, "last_price", 1.0)

            with self.subTest(strategy=entry.strategy_id, case="incorrect_regime"):
                result = strategy.evaluate(snapshot_fixture(**cases["buy"], liquidity={"level": "poor", "score": 0.1}))
                self.assertEqual(result.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.incorrect_regime", result.reason_codes)


def strategy_for(strategy_id: str):
    entry = next(item for item in DIRECTIONAL_STRATEGIES if item.strategy_id == strategy_id)
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


def strategy_cases(strategy_id: str) -> dict[str, dict[str, Any]]:
    base = {
        "buy": {},
        "sell": {"price": 99.0, "vwap": 100.0, "ma_down": True},
        "hold": {"price": 100.0, "vwap": 100.0, "adx": 20.0},
        "missing": {"vwap": None},
        "threshold_at": {},
        "threshold_below": {},
    }
    overrides = {
        "multi_timeframe_trend_alignment": {
            "buy": {"price": 101.0, "vwap": 100.0, "adx": 24.0, "ma_up": True},
            "sell": {"price": 99.0, "vwap": 100.0, "adx": 24.0, "ma_down": True},
            "hold": {"price": 100.0, "vwap": 100.0, "adx": 24.0, "ma_flat": True},
            "missing": {"moving_averages": {}},
            "threshold_at": {"price": 101.0, "vwap": 100.0, "adx": 18.0, "two_timeframes_up": True},
            "threshold_below": {"price": 101.0, "vwap": 100.0, "adx": 17.9, "two_timeframes_up": True},
        },
        "first_pullback_after_open": {
            "buy": {"price": 101.0, "vwap": 100.0, "relative_volume": 1.5, "pullbackDepthAtr": 0.75},
            "sell": {"price": 99.0, "vwap": 100.0, "relative_volume": 1.5, "pullbackDepthAtr": 0.75},
            "hold": {"price": 101.0, "vwap": 100.0, "relative_volume": 0.2, "pullbackDepthAtr": 0.1},
            "missing": {"relative_volume": {}},
            "threshold_at": {"price": 101.0, "vwap": 100.0, "relative_volume": 0.60, "pullbackDepthAtr": 0.10},
            "threshold_below": {"price": 101.0, "vwap": 100.0, "relative_volume": 0.59, "pullbackDepthAtr": 0.10},
        },
        "vwap_trend_continuation": {
            "buy": {"price": 101.0, "vwap": 100.0, "relative_volume": 1.5, "ma_up": True},
            "sell": {"price": 99.0, "vwap": 100.0, "relative_volume": 1.5, "ma_down": True},
            "hold": {"price": 100.05, "vwap": 100.0, "relative_volume": 0.2, "ma_up": True},
            "missing": {"moving_averages": {}},
            "threshold_at": {"price": 100.15, "vwap": 100.0, "relative_volume": 0.75, "moving_averages": {"1m": {"ema20": 100.0}}},
            "threshold_below": {"price": 100.149, "vwap": 100.0, "relative_volume": 0.74, "moving_averages": {"1m": {"ema20": 100.0}}},
        },
        "opening_range_breakout": {
            "buy": {"price": 100.2, "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "atr": 1.0, "relative_volume": 1.25},
            "sell": {"price": 98.8, "openingRangeHigh": 101.0, "openingRangeLow": 99.0, "atr": 1.0, "relative_volume": 1.25},
            "hold": {"price": 100.05, "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "atr": 1.0, "relative_volume": 1.25},
            "missing": {"features": {}},
            "threshold_at": {"price": 100.1, "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "atr": 1.0, "relative_volume": 1.25},
            "threshold_below": {"price": 100.09, "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "atr": 1.0, "relative_volume": 1.25},
        },
        "volatility_breakout": {
            "buy": {"price": 100.3, "upperBand": 100.0, "lowerBand": 99.0, "atr": 1.0, "relative_volume": 1.5, "bollingerWidthPercentile": 0.8},
            "sell": {"price": 98.7, "upperBand": 100.0, "lowerBand": 99.0, "atr": 1.0, "relative_volume": 1.5, "bollingerWidthPercentile": 0.8},
            "hold": {"price": 100.1, "upperBand": 100.0, "lowerBand": 99.0, "atr": 1.0, "relative_volume": 1.5, "bollingerWidthPercentile": 0.8},
            "missing": {"bollinger_bands": {}},
            "threshold_at": {"price": 100.2, "upperBand": 100.0, "lowerBand": 99.0, "atr": 1.0, "relative_volume": 1.5, "bollingerWidthPercentile": 0.8},
            "threshold_below": {"price": 100.19, "upperBand": 100.0, "lowerBand": 99.0, "atr": 1.0, "relative_volume": 1.5, "bollingerWidthPercentile": 0.8},
        },
        "failed_breakout_reversal": {
            "buy": {"failedBreakoutSide": "downside", "reclaimDistanceAtr": 0.15},
            "sell": {"failedBreakoutSide": "upside", "reclaimDistanceAtr": 0.15},
            "hold": {"failedBreakoutSide": "none", "reclaimDistanceAtr": 0.15},
            "missing": {"features": {}},
            "threshold_at": {"failedBreakoutSide": "downside", "reclaimDistanceAtr": 0.15},
            "threshold_below": {"failedBreakoutSide": "downside", "reclaimDistanceAtr": 0.149},
        },
        "liquidity_sweep_reversal": {
            "buy": {"sweepSide": "sell_side", "rejectionWickRatio": 0.80},
            "sell": {"sweepSide": "buy_side", "rejectionWickRatio": 0.80},
            "hold": {"sweepSide": "none", "rejectionWickRatio": 0.80},
            "missing": {"features": {}},
            "threshold_at": {"sweepSide": "sell_side", "rejectionWickRatio": 0.80},
            "threshold_below": {"sweepSide": "sell_side", "rejectionWickRatio": 0.79},
        },
        "vwap_mean_reversion": {
            "buy": {"price": 99.0, "vwap": 100.0, "atr": 1.0, "rsi": 30.0, "adx": 20.0},
            "sell": {"price": 101.0, "vwap": 100.0, "atr": 1.0, "rsi": 70.0, "adx": 20.0},
            "hold": {"price": 99.5, "vwap": 100.0, "atr": 1.0, "rsi": 40.0, "adx": 20.0},
            "missing": {"rsi_map": {}},
            "threshold_at": {"price": 99.25, "vwap": 100.0, "atr": 1.0, "rsi": 35.0, "adx": 25.0},
            "threshold_below": {"price": 99.26, "vwap": 100.0, "atr": 1.0, "rsi": 35.0, "adx": 25.0},
        },
        "bollinger_atr_reversion": {
            "buy": {"price": 99.7, "lowerBand": 100.0, "upperBand": 101.0, "atr": 1.0, "rsi": 30.0, "adx": 20.0},
            "sell": {"price": 101.3, "lowerBand": 99.0, "upperBand": 101.0, "atr": 1.0, "rsi": 70.0, "adx": 20.0},
            "hold": {"price": 99.9, "lowerBand": 100.0, "upperBand": 101.0, "atr": 1.0, "rsi": 40.0, "adx": 20.0},
            "missing": {"bollinger_bands": {}},
            "threshold_at": {"price": 99.8, "lowerBand": 100.0, "upperBand": 101.0, "atr": 1.0, "rsi": 35.0, "adx": 28.0},
            "threshold_below": {"price": 99.81, "lowerBand": 100.0, "upperBand": 101.0, "atr": 1.0, "rsi": 35.0, "adx": 28.0},
        },
        "gap_continuation_gap_fade": {
            "buy": {"gapState": "gap_up", "gapPercent": 0.75, "gapTradeType": "continuation", "spyVsQqq": 1.01},
            "sell": {"gapState": "gap_down", "gapPercent": -0.75, "gapTradeType": "continuation", "spyVsQqq": 0.99},
            "hold": {"gapState": "flat_open", "gapPercent": 0.0, "gapTradeType": "continuation"},
            "missing": {"gap_state": {}},
            "threshold_at": {"gapState": "gap_up", "gapPercent": 0.75, "gapTradeType": "continuation", "spyVsQqq": 1.0},
            "threshold_below": {"gapState": "gap_up", "gapPercent": 0.74, "gapTradeType": "continuation", "spyVsQqq": 1.0},
        },
    }
    return {key: {**base[key], **overrides[strategy_id][key]} for key in base}


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    vwap = overrides.get("vwap", 100.0)
    candle_count = int(overrides.get("candle_count", 60))
    ma_up = bool(overrides.get("ma_up", False))
    ma_down = bool(overrides.get("ma_down", False))
    ma_flat = bool(overrides.get("ma_flat", False))
    two_timeframes_up = bool(overrides.get("two_timeframes_up", False))
    moving_averages = overrides.get("moving_averages")
    if moving_averages is None:
        moving_averages = ma_values(ma_up=ma_up, ma_down=ma_down, ma_flat=ma_flat, two_timeframes_up=two_timeframes_up)
    features = {
        "pullbackDepthAtr": overrides.get("pullbackDepthAtr", 0.75),
        "openingRangeHigh": overrides.get("openingRangeHigh", 100.0),
        "openingRangeLow": overrides.get("openingRangeLow", 99.0),
        "bollingerWidthPercentile": overrides.get("bollingerWidthPercentile", 0.8),
        "failedBreakoutSide": overrides.get("failedBreakoutSide", "downside"),
        "reclaimDistanceAtr": overrides.get("reclaimDistanceAtr", 0.15),
        "sweepSide": overrides.get("sweepSide", "sell_side"),
        "rejectionWickRatio": overrides.get("rejectionWickRatio", 0.8),
        "gapTradeType": overrides.get("gapTradeType", "continuation"),
    }
    if "features" in overrides:
        features = overrides["features"]
    relative_volume_override = overrides.get("relative_volume", 1.5)
    if isinstance(relative_volume_override, dict):
        relative_volume_map = relative_volume_override
    else:
        relative_volume_map = {"1m": relative_volume_override, "5m": relative_volume_override, "15m": relative_volume_override}
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-1",
        snapshot_id="snapshot-1",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=price - 0.01,
        ask_price=price + 0.01,
        spread_bps=overrides.get("spread_bps", 5.0),
        volume=100_000,
        source_cutoff_timestamp=NOW,
        point_in_time=True,
        candles={"1m": candles(candle_count, price), "5m": candles(candle_count, price), "15m": candles(candle_count, price)},
        vwap=vwap,
        moving_averages=moving_averages,
        atr={"1m": overrides.get("atr", 1.0), "5m": overrides.get("atr", 1.0), "15m": overrides.get("atr", 1.0)},
        adx={"1m": overrides.get("adx", 20.0), "5m": overrides.get("adx", 20.0), "15m": overrides.get("adx", 20.0)},
        rsi=overrides.get("rsi_map", {"1m": overrides.get("rsi", 50.0), "5m": overrides.get("rsi", 50.0), "15m": overrides.get("rsi", 50.0)}),
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands=overrides.get("bollinger_bands", {"1m": {"upper": overrides.get("upperBand", 101.0), "middle": 100.0, "lower": overrides.get("lowerBand", 99.0)}}),
        relative_volume=relative_volume_map,
        spread={"basisPoints": overrides.get("spreadBps", 5.0), "dollars": 0.02},
        liquidity=overrides.get("liquidity", {"level": "good", "score": 1.0}),
        session_phase=overrides.get("session_phase", "morning"),
        gap_state=overrides.get("gap_state", {"state": overrides.get("gapState", "gap_up"), "gapPercent": overrides.get("gapPercent", 0.75)}),
        qqq_iwm_context={"spyVsQqq": overrides.get("spyVsQqq", 1.01), "spyVsIwm": 1.0},
        breadth={"averageReturn": 0.001, "componentCount": 2},
        economic_event_state=overrides.get("economic_event_state", {"state": "none"}),
        features=features,
    )


def ma_values(*, ma_up: bool = False, ma_down: bool = False, ma_flat: bool = False, two_timeframes_up: bool = False) -> dict[str, dict[str, float]]:
    if ma_down:
        return {"1m": {"ema20": 99.0, "ema50": 100.0}, "5m": {"ema20": 99.0, "ema50": 100.0}, "15m": {"ema20": 99.0, "ema50": 100.0}}
    if ma_flat:
        return {"1m": {"ema20": 100.0, "ema50": 100.0}, "5m": {"ema20": 100.0, "ema50": 100.0}, "15m": {"ema20": 100.0, "ema50": 100.0}}
    if two_timeframes_up:
        return {"1m": {"ema20": 101.0, "ema50": 100.0}, "5m": {"ema20": 101.0, "ema50": 100.0}, "15m": {"ema20": 100.0, "ema50": 100.0}}
    return {"1m": {"ema20": 101.0, "ema50": 100.0}, "5m": {"ema20": 101.0, "ema50": 100.0}, "15m": {"ema20": 101.0, "ema50": 100.0}}


def candles(count: int, close: float) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "timestamp": NOW.isoformat(),
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100_000,
        }
        for _ in range(count)
    )


if __name__ == "__main__":
    unittest.main()
