from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.algorithms.meta_strategy import ACTIVE_DIRECTIONAL_STRATEGIES, DIRECTIONAL_STRATEGIES, SHADOW_DIRECTIONAL_STRATEGIES, MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SHADOW_ONLY = {"liquidity_sweep_reversal", "gap_continuation", "gap_fade", "economic_event_reaction"}


class MetaStrategyStep9DirectionalStrategiesTest(unittest.TestCase):
    maxDiff = None

    def test_active_directional_pool_has_common_contract_and_no_order_side_effects(self) -> None:
        self.assertEqual(tuple(entry.strategy_id for entry in ACTIVE_DIRECTIONAL_STRATEGIES), (
            "multi_timeframe_trend_alignment",
            "first_pullback_after_open",
            "opening_range_breakout",
            "vwap_trend_continuation",
            "volatility_breakout",
            "failed_breakout_reversal",
            "bollinger_atr_reversion",
            "vwap_mean_reversion",
        ))
        for entry in ACTIVE_DIRECTIONAL_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            cases = active_cases(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id, case="buy"):
                result = strategy.evaluate(snapshot_fixture(**cases["buy"]))
                self.assertEqual(result.signal, "BUY")
                self.assertTrue(result.eligible)
                self.assert_common_contract(result, entry)

            with self.subTest(strategy=entry.strategy_id, case="sell"):
                result = strategy.evaluate(snapshot_fixture(**cases["sell"]))
                self.assertEqual(result.signal, "SELL")
                self.assertTrue(result.eligible)
                self.assert_common_contract(result, entry)

            with self.subTest(strategy=entry.strategy_id, case="hold"):
                result = strategy.evaluate(snapshot_fixture(**cases["hold"]))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.eligible)

            with self.subTest(strategy=entry.strategy_id, case="missing_inputs"):
                result = strategy.evaluate(snapshot_fixture(**cases["missing"]))
                self.assertEqual(result.signal, "HOLD")
                self.assertIn("meta_strategy.strategy.missing_required_inputs", result.reason_codes)
                self.assertFalse(all(result.required_input_status.values()))

    def test_shadow_directional_strategies_generate_zero_order_influence(self) -> None:
        self.assertEqual({entry.strategy_id for entry in SHADOW_DIRECTIONAL_STRATEGIES}, SHADOW_ONLY)
        for entry in SHADOW_DIRECTIONAL_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            result = strategy.evaluate(snapshot_fixture(**shadow_case(entry.strategy_id)))
            with self.subTest(strategy=entry.strategy_id):
                self.assert_common_contract(result, entry)
                self.assertEqual(entry.mode, "SHADOW")
                self.assertEqual((result.evidence or {}).get("orderInfluence", 0.0), 0.0)
                self.assertFalse((result.evidence or {}).get("submitsOrdersDirectly"))

    def test_liquidity_sweep_requires_microstructure_and_holds_without_it(self) -> None:
        strategy = strategy_for("liquidity_sweep_reversal")

        missing = strategy.evaluate(snapshot_fixture(sweepSide="sell_side", rejectionWickRatio=1.2, features_extra={"microstructureEvidence": {}}))
        reliable = strategy.evaluate(
            snapshot_fixture(
                sweepSide="sell_side",
                rejectionWickRatio=1.2,
                features_extra={"microstructureEvidence": {"reliable": True, "orderFlowImbalance": -0.72}},
            )
        )

        self.assertEqual(missing.signal, "HOLD")
        self.assertIn("meta_strategy.directional.liquidity_sweep.microstructure_unavailable", missing.evidence["blockReasonCodes"])
        self.assertEqual(reliable.signal, "BUY")
        self.assertEqual(reliable.evidence["orderInfluence"], 0.0)

    def test_directional_registry_keeps_declared_shadow_strategies_out_of_active_pool(self) -> None:
        active_ids = {entry.strategy_id for entry in ACTIVE_DIRECTIONAL_STRATEGIES}
        self.assertFalse(active_ids.intersection(SHADOW_ONLY))
        self.assertEqual(len(DIRECTIONAL_STRATEGIES), 12)

    def assert_common_contract(self, result, entry) -> None:
        self.assertEqual(result.strategy_id, entry.strategy_id)
        self.assertEqual(result.strategy_version, entry.strategy_version)
        self.assertEqual(result.family, entry.family)
        self.assertEqual(result.required_inputs, entry.required_inputs)
        self.assertEqual(result.minimum_warmup, entry.minimum_warmup)
        self.assertEqual(result.supported_directions, tuple(entry.supported_directions))
        self.assertIn(result.signal, {"BUY", "SELL", "HOLD"})
        self.assertIsNotNone(result.evidence)
        self.assertTrue(result.evidence["completeEvidencePayload"])
        self.assertFalse(result.evidence["submitsOrdersDirectly"])
        self.assertIn("entryReference", result.evidence)
        self.assertIn("invalidationReference", result.evidence)
        self.assertIn("suggestedStopReference", result.evidence)
        self.assertTrue(result.required_input_status)


def strategy_for(strategy_id: str):
    entry = next(item for item in DIRECTIONAL_STRATEGIES if item.strategy_id == strategy_id)
    settings = build_meta_strategy_settings(directional_strategies={strategy_id: {"enabled": True}})
    strategy_settings = settings.directional_strategies[strategy_id]
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)(strategy_settings)


def active_cases(strategy_id: str) -> dict[str, dict[str, Any]]:
    return {
        "multi_timeframe_trend_alignment": {
            "buy": {"price": 101.0, "vwap": 100.0, "adx": 28.0, "ma_up": True, "trend": "up"},
            "sell": {"price": 99.0, "vwap": 100.0, "adx": 28.0, "ma_down": True, "trend": "down"},
            "hold": {"price": 100.0, "vwap": 100.0, "adx": 28.0, "ma_flat": True, "trend": "flat"},
            "missing": {"moving_averages": {}},
        },
        "first_pullback_after_open": {
            "buy": {"price": 101.0, "vwap": 100.0, "trend": "pullback_buy", "relative_volume": 0.95, "pullbackDepthAtr": 0.75},
            "sell": {"price": 99.0, "vwap": 100.0, "trend": "pullback_sell", "relative_volume": 0.95, "pullbackDepthAtr": 0.75},
            "hold": {"price": 101.0, "vwap": 100.0, "trend": "flat", "relative_volume": 1.8, "pullbackDepthAtr": 1.8},
            "missing": {"relative_volume": {}},
        },
        "opening_range_breakout": {
            "buy": {"price": 100.25, "trend": "breakout_buy", "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "relative_volume": 1.4},
            "sell": {"price": 98.75, "trend": "breakout_sell", "openingRangeHigh": 101.0, "openingRangeLow": 99.0, "relative_volume": 1.4},
            "hold": {"price": 100.04, "openingRangeHigh": 100.0, "openingRangeLow": 99.0, "relative_volume": 1.4},
            "missing": {"features": {}},
        },
        "vwap_trend_continuation": {
            "buy": {"price": 101.0, "vwap": 100.0, "ma_up": True, "trend": "continuation_buy", "relative_volume": 1.2},
            "sell": {"price": 99.0, "vwap": 100.0, "ma_down": True, "trend": "continuation_sell", "relative_volume": 1.2},
            "hold": {"price": 100.05, "vwap": 100.0, "ma_up": True, "trend": "flat", "relative_volume": 1.2},
            "missing": {"moving_averages": {}},
        },
        "volatility_breakout": {
            "buy": {"price": 100.3, "trend": "breakout_buy", "upperBand": 100.0, "lowerBand": 99.0, "relative_volume": 1.7, "bollingerWidthPercentile": 0.85, "priorCompression": True},
            "sell": {"price": 98.7, "trend": "breakout_sell", "upperBand": 100.0, "lowerBand": 99.0, "relative_volume": 1.7, "bollingerWidthPercentile": 0.85, "priorCompression": True},
            "hold": {"price": 100.1, "upperBand": 100.0, "lowerBand": 99.0, "relative_volume": 0.8, "bollingerWidthPercentile": 0.85, "priorCompression": True},
            "missing": {"bollinger_bands": {}},
        },
        "failed_breakout_reversal": {
            "buy": {"price": 99.25, "trend": "failed_downside", "failedBreakoutSide": "downside", "reclaimDistanceAtr": 0.25, "openingRangeHigh": 101.0, "openingRangeLow": 99.0},
            "sell": {"price": 100.75, "trend": "failed_upside", "failedBreakoutSide": "upside", "reclaimDistanceAtr": 0.25, "openingRangeHigh": 101.0, "openingRangeLow": 99.0},
            "hold": {"failedBreakoutSide": "none", "reclaimDistanceAtr": 0.25},
            "missing": {"features": {}},
        },
        "bollinger_atr_reversion": {
            "buy": {"price": 99.55, "lowerBand": 100.0, "upperBand": 101.0, "rsi": 30.0, "adx": 20.0},
            "sell": {"price": 101.45, "lowerBand": 99.0, "upperBand": 101.0, "rsi": 70.0, "adx": 20.0},
            "hold": {"price": 99.9, "lowerBand": 100.0, "upperBand": 101.0, "rsi": 40.0, "adx": 20.0},
            "missing": {"bollinger_bands": {}},
        },
        "vwap_mean_reversion": {
            "buy": {"price": 99.0, "vwap": 100.0, "rsi": 30.0, "adx": 20.0, "relative_volume": 0.9},
            "sell": {"price": 101.0, "vwap": 100.0, "rsi": 70.0, "adx": 20.0, "relative_volume": 0.9},
            "hold": {"price": 99.5, "vwap": 100.0, "rsi": 40.0, "adx": 20.0},
            "missing": {"rsi_map": {}},
        },
    }[strategy_id]


def shadow_case(strategy_id: str) -> dict[str, Any]:
    return {
        "liquidity_sweep_reversal": {"sweepSide": "sell_side", "rejectionWickRatio": 1.2, "features_extra": {"microstructureEvidence": {"reliable": True, "orderFlowImbalance": -0.7}}},
        "gap_continuation": {"gapState": "gap_up", "gapPercent": 1.0, "gapTradeType": "continuation"},
        "gap_fade": {"gapState": "gap_down", "gapPercent": -1.0, "gapTradeType": "fade"},
        "economic_event_reaction": {"economic_event_state": {"state": "released", "active": True, "directionalBias": "bullish"}, "relative_volume": 2.5},
    }[strategy_id]


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    vwap = overrides.get("vwap", 100.0)
    candle_count = int(overrides.get("candle_count", 80))
    moving_averages = overrides.get("moving_averages")
    if moving_averages is None:
        moving_averages = ma_values(ma_up=bool(overrides.get("ma_up", False)), ma_down=bool(overrides.get("ma_down", False)), ma_flat=bool(overrides.get("ma_flat", False)))
    features = {
        "pullbackDepthAtr": overrides.get("pullbackDepthAtr", 0.75),
        "openingRangeHigh": overrides.get("openingRangeHigh", 100.0),
        "openingRangeLow": overrides.get("openingRangeLow", 99.0),
        "openingRangeDurationMinutes": 30,
        "bollingerWidthPercentile": overrides.get("bollingerWidthPercentile", 0.8),
        "priorCompression": overrides.get("priorCompression", False),
        "atrExpansion": overrides.get("atrExpansion", True),
        "failedBreakoutSide": overrides.get("failedBreakoutSide", "downside"),
        "reclaimDistanceAtr": overrides.get("reclaimDistanceAtr", 0.25),
        "sweepSide": overrides.get("sweepSide", "sell_side"),
        "rejectionWickRatio": overrides.get("rejectionWickRatio", 0.8),
        "gapTradeType": overrides.get("gapTradeType", "continuation"),
        "recentSwingHigh": overrides.get("recentSwingHigh", 101.0),
        "recentSwingLow": overrides.get("recentSwingLow", 99.0),
        **dict(overrides.get("features_extra") or {}),
    }
    if "features" in overrides:
        features = overrides["features"]
    relative_volume_override = overrides.get("relative_volume", 1.5)
    relative_volume_map = relative_volume_override if isinstance(relative_volume_override, dict) else {"1m": relative_volume_override, "5m": relative_volume_override, "15m": relative_volume_override}
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
        candles={
            "1m": candles(candle_count, price, trend=overrides.get("trend", "up")),
            "5m": candles(candle_count, price, trend=overrides.get("trend", "up")),
            "15m": candles(candle_count, price, trend=overrides.get("trend", "up")),
        },
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


def ma_values(*, ma_up: bool = False, ma_down: bool = False, ma_flat: bool = False) -> dict[str, dict[str, float]]:
    if ma_down:
        return {"1m": {"ema20": 99.2, "ema50": 99.8}, "5m": {"ema20": 99.2, "ema50": 99.8}, "15m": {"ema20": 99.2, "ema50": 99.8}}
    if ma_flat:
        return {"1m": {"ema20": 100.0, "ema50": 100.0}, "5m": {"ema20": 100.0, "ema50": 100.0}, "15m": {"ema20": 100.0, "ema50": 100.0}}
    return {"1m": {"ema20": 100.8, "ema50": 100.2}, "5m": {"ema20": 100.8, "ema50": 100.2}, "15m": {"ema20": 100.8, "ema50": 100.2}}


def candles(count: int, close: float, *, trend: str) -> tuple[dict[str, Any], ...]:
    rows = []
    for index in range(count):
        timestamp = NOW - timedelta(minutes=count - index)
        base = close - 1.0 + (index / max(1, count - 1))
        if trend in {"down", "continuation_sell", "breakout_sell"}:
            base = close + 1.0 - (index / max(1, count - 1))
        elif trend == "flat":
            base = close
        rows.append({"timestamp": timestamp.isoformat(), "open": base - 0.03, "high": base + 0.08, "low": base - 0.08, "close": base, "volume": 100_000})
    if trend == "pullback_buy" and count >= 15:
        opening_open = close - 1.1
        for index in range(15):
            impulse_close = opening_open + 0.02 * index
            rows[index] = {**rows[index], "open": opening_open if index == 0 else impulse_close - 0.02, "high": opening_open + 0.45 + 0.02 * index, "low": opening_open - 0.03, "close": impulse_close}
    if trend == "pullback_sell" and count >= 15:
        opening_open = close + 1.1
        for index in range(15):
            impulse_close = opening_open - 0.02 * index
            rows[index] = {**rows[index], "open": opening_open if index == 0 else impulse_close + 0.02, "high": opening_open + 0.03, "low": opening_open - 0.45 - 0.02 * index, "close": impulse_close}
    if trend in {"continuation_buy", "pullback_buy"} and count >= 2:
        rows[-2] = {**rows[-2], "close": close - 0.25, "high": close - 0.05, "low": close - 0.45}
        rows[-1] = {**rows[-1], "open": close - 0.10, "high": close + 0.12, "low": close - 0.12, "close": close}
    if trend in {"continuation_sell", "pullback_sell"} and count >= 2:
        rows[-2] = {**rows[-2], "close": close + 0.25, "high": close + 0.45, "low": close + 0.05}
        rows[-1] = {**rows[-1], "open": close + 0.10, "high": close + 0.12, "low": close - 0.12, "close": close}
    if trend == "failed_downside":
        rows[-1] = {**rows[-1], "open": close - 0.15, "high": close + 0.05, "low": 98.75, "close": close}
    if trend == "failed_upside":
        rows[-1] = {**rows[-1], "open": close + 0.15, "high": 101.25, "low": close - 0.05, "close": close}
    return tuple(rows)


if __name__ == "__main__":
    unittest.main()
