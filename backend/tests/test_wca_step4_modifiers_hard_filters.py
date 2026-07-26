from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.configuration import (
    CashAvoidTradingSettings,
    EconomicEventRiskSettings,
    ExtremeVolatilitySettings,
    InvalidOrStaleDataSettings,
    MarketBreadthSettings,
    RelativeStrengthVsQqqIwmSettings,
    SessionEntryBlockSettings,
    UnsafeLiquiditySettings,
    UnsafeSpreadSettings,
    WCA_MODIFIER_SETTINGS_MODELS,
    WcaHardFilterSettings,
)
from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaQuote
from backend.app.algorithms.wca.local_gates import WCA_HARD_FILTER_IDS, WcaLocalGateContext, evaluate_wca_hard_filters
from backend.app.algorithms.wca.modifiers import WCA_MODIFIERS, evaluate_all_modifiers
from backend.app.algorithms.wca.modifiers.market_breadth import MarketBreadthModifier
from backend.app.algorithms.wca.modifiers.relative_strength_vs_qqq_iwm import RelativeStrengthVsQqqIwmModifier
from backend.app.algorithms.wca.strategy_registry import WCA_HARD_FILTER_REGISTRY, WCA_MODIFIER_REGISTRY


UTC = timezone.utc
ROOT = Path(__file__).parents[2]
WCA_PATH = ROOT / "backend" / "app" / "algorithms" / "wca"


class WcaStep4ModifiersHardFiltersTest(unittest.TestCase):
    def test_modifier_and_hard_filter_inventory_match_authoritative_registry(self) -> None:
        self.assertEqual(tuple(modifier.modifier_id for modifier in WCA_MODIFIERS), tuple(row.slug for row in WCA_MODIFIER_REGISTRY))
        self.assertEqual(WCA_HARD_FILTER_IDS, {row.slug for row in WCA_HARD_FILTER_REGISTRY})
        self.assertEqual(len(WCA_MODIFIERS), 11)
        self.assertEqual(len(WCA_HARD_FILTER_IDS), 7)

    def test_modifiers_do_not_contribute_primary_directional_score(self) -> None:
        evaluations = evaluate_all_modifiers(snapshot_with_external_context())

        self.assertEqual({row.modifier_id for row in evaluations}, {row.slug for row in WCA_MODIFIER_REGISTRY})
        for evaluation in evaluations:
            self.assertEqual(evaluation.primary_vote_contribution, 0)
            self.assertEqual(evaluation.directional_score_contribution, 0)
            self.assertFalse(hasattr(evaluation, "signal"))
            self.assertFalse(hasattr(evaluation, "direction"))
            self.assertGreaterEqual(evaluation.confidence_multiplier, 0)
            self.assertGreaterEqual(evaluation.weight_multiplier, 0)
            self.assertLessEqual(evaluation.risk_multiplier, 1)
            self.assertLessEqual(evaluation.position_size_multiplier, 1)

    def test_modifier_multiplier_caps_cannot_be_exceeded(self) -> None:
        snapshot = snapshot_with_external_context(large_move=True)
        by_id = {modifier.modifier_id: modifier for modifier in WCA_MODIFIERS}

        for modifier_id, settings_model in WCA_MODIFIER_SETTINGS_MODELS.items():
            settings = settings_model(
                minimum_confidence_multiplier=0.99,
                maximum_confidence_multiplier=1.01,
                minimum_weight_multiplier=0.98,
                maximum_weight_multiplier=1.02,
                minimum_risk_multiplier=0.40,
                maximum_risk_multiplier=0.90,
                minimum_position_size_multiplier=0.30,
                maximum_position_size_multiplier=0.80,
                minimum_entry_requirement_multiplier=1.05,
                maximum_entry_requirement_multiplier=1.10,
            )
            evaluation = by_id[modifier_id].evaluate(snapshot, settings)

            self.assertGreaterEqual(evaluation.confidence_multiplier, settings.minimum_confidence_multiplier)
            self.assertLessEqual(evaluation.confidence_multiplier, settings.maximum_confidence_multiplier)
            self.assertGreaterEqual(evaluation.weight_multiplier, settings.minimum_weight_multiplier)
            self.assertLessEqual(evaluation.weight_multiplier, settings.maximum_weight_multiplier)
            self.assertGreaterEqual(evaluation.risk_multiplier, settings.minimum_risk_multiplier)
            self.assertLessEqual(evaluation.risk_multiplier, settings.maximum_risk_multiplier)
            self.assertGreaterEqual(evaluation.position_size_multiplier, settings.minimum_position_size_multiplier)
            self.assertLessEqual(evaluation.position_size_multiplier, settings.maximum_position_size_multiplier)
            self.assertGreaterEqual(evaluation.entry_requirement_multiplier, settings.minimum_entry_requirement_multiplier)
            self.assertLessEqual(evaluation.entry_requirement_multiplier, settings.maximum_entry_requirement_multiplier)

    def test_relative_strength_requires_timestamp_aligned_qqq_iwm_data(self) -> None:
        modifier = RelativeStrengthVsQqqIwmModifier()
        settings = RelativeStrengthVsQqqIwmSettings(lookback_bars=5, stale_after_seconds=60)

        missing = modifier.evaluate(snapshot_with_external_context(include_relative_strength=False), settings)
        stale = modifier.evaluate(snapshot_with_external_context(relative_strength_timestamp=timestamp() - timedelta(minutes=5)), settings)
        aligned = modifier.evaluate(snapshot_with_external_context(), settings)

        self.assertEqual(missing.status, WcaEvaluationStatus.NOT_APPLICABLE.value)
        self.assertEqual(stale.status, WcaEvaluationStatus.NOT_APPLICABLE.value)
        self.assertEqual(aligned.status, WcaEvaluationStatus.ACTIVE.value)
        self.assertIn("relative_strength", aligned.market_status_contributions)
        self.assertEqual(aligned.primary_vote_contribution, 0)

    def test_market_breadth_requires_configured_fresh_breadth_inputs(self) -> None:
        modifier = MarketBreadthModifier()
        settings = MarketBreadthSettings(stale_after_seconds=60)

        missing = modifier.evaluate(snapshot_with_external_context(include_breadth=False), settings)
        stale = modifier.evaluate(snapshot_with_external_context(breadth_timestamp=timestamp() - timedelta(minutes=5)), settings)
        valid = modifier.evaluate(snapshot_with_external_context(), settings)

        self.assertEqual(missing.status, WcaEvaluationStatus.NOT_APPLICABLE.value)
        self.assertEqual(stale.status, WcaEvaluationStatus.NOT_APPLICABLE.value)
        self.assertEqual(valid.status, WcaEvaluationStatus.ACTIVE.value)
        self.assertIn("breadth_score", valid.market_status_contributions)
        self.assertEqual(valid.primary_vote_contribution, 0)

    def test_each_hard_filter_can_independently_block_new_entries_and_preserve_exits(self) -> None:
        cases = {
            "cash_avoid_trading": (safe_snapshot(), WcaLocalGateContext(evaluation_timestamp=timestamp(), remaining_allocated_risk_budget=0), WcaHardFilterSettings(cash_avoid_trading=CashAvoidTradingSettings(enabled=True))),
            "economic_event_risk": (safe_snapshot(reason_codes=("economic_event_risk",)), safe_context(), WcaHardFilterSettings(economic_event_risk=EconomicEventRiskSettings(enabled=True))),
            "invalid_or_stale_data": (safe_snapshot(data_timestamp=timestamp() - timedelta(minutes=10)), safe_context(), WcaHardFilterSettings(invalid_or_stale_data=InvalidOrStaleDataSettings(stale_after_seconds=60))),
            "unsafe_spread": (safe_snapshot(quote=WcaQuote(timestamp=timestamp(), bid=100, ask=101)), safe_context(), WcaHardFilterSettings(unsafe_spread=UnsafeSpreadSettings(maximum_spread_percent=0.001))),
            "unsafe_liquidity": (safe_snapshot(volume=100), safe_context(), WcaHardFilterSettings(unsafe_liquidity=UnsafeLiquiditySettings(minimum_average_volume=10000))),
            "extreme_volatility": (safe_snapshot(wide_range=True), safe_context(), WcaHardFilterSettings(extreme_volatility=ExtremeVolatilitySettings(maximum_atr_percent=0.001, reduction_atr_percent=0.0005))),
            "session_entry_block": (safe_snapshot(data_timestamp=datetime(2026, 1, 6, 22, 0, tzinfo=UTC)), safe_context(), WcaHardFilterSettings(session_entry_block=SessionEntryBlockSettings(entry_cutoff_minutes=15 * 60 + 30))),
        }

        for filter_id, (snapshot, context, settings) in cases.items():
            outcome = next(row for row in evaluate_wca_hard_filters(snapshot=snapshot, context=context, settings=settings) if row.gate_id == filter_id)

            self.assertEqual(outcome.status, "FAIL", filter_id)
            self.assertTrue(outcome.blocks_entry, filter_id)
            self.assertTrue(outcome.entry_blocked, filter_id)
            self.assertTrue(outcome.exit_allowed, filter_id)
            self.assertTrue(outcome.reason_codes, filter_id)

    def test_hard_filters_separate_warnings_from_entry_blocks_and_reductions(self) -> None:
        settings = WcaHardFilterSettings(unsafe_spread=UnsafeSpreadSettings(maximum_spread_percent=0.010, reduction_spread_percent=0.001, reduction_multiplier=0.25))
        outcome = next(row for row in evaluate_wca_hard_filters(snapshot=safe_snapshot(quote=WcaQuote(timestamp=timestamp(), bid=100, ask=100.2)), context=safe_context(), settings=settings) if row.gate_id == "unsafe_spread")

        self.assertEqual(outcome.status, "WARN")
        self.assertFalse(outcome.blocks_entry)
        self.assertTrue(outcome.warning)
        self.assertEqual(outcome.quantity_multiplier, 0.25)
        self.assertEqual(outcome.risk_multiplier, 0.25)
        self.assertTrue(outcome.exit_allowed)

    def test_modifiers_and_hard_filters_import_no_sibling_algorithm_mutable_state(self) -> None:
        paths = [*sorted((WCA_PATH / "modifiers").glob("*.py")), WCA_PATH / "local_gates.py"]
        forbidden = ("weighted_voting", "voting_ensemble", "regime", "session", "meta_strategy")
        imports: list[str] = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)

        self.assertFalse(any(any(term in module.lower() for term in forbidden) for module in imports))
        self.assertFalse(any(module.startswith("backend.app.algorithms.") and not module.startswith("backend.app.algorithms.wca") for module in imports))


def snapshot_with_external_context(
    *,
    include_relative_strength: bool = True,
    include_breadth: bool = True,
    relative_strength_timestamp: datetime | None = None,
    breadth_timestamp: datetime | None = None,
    large_move: bool = False,
) -> WcaMarketSnapshot:
    candles = candles_series(count=70, start_price=100, step=0.08 if large_move else 0.03, volume=100000)
    external_market_data = {}
    input_timestamps = {}
    if include_relative_strength:
        external_market_data["QQQ"] = candles_series(count=70, start_price=100, step=0.01, volume=100000)
        external_market_data["IWM"] = candles_series(count=70, start_price=100, step=0.005, volume=100000)
        input_timestamps["relative_strength_vs_qqq_iwm"] = relative_strength_timestamp or timestamp()
    if include_breadth:
        input_timestamps["market_breadth"] = breadth_timestamp or timestamp()
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=timestamp(),
        decision_timestamp=timestamp(),
        candles=tuple(candles),
        quote=WcaQuote(timestamp=timestamp(), bid=101, ask=101.02),
        external_market_data=external_market_data,
        external_input_timestamps=input_timestamps,
        market_breadth_inputs={"advancers": 3200, "decliners": 1800, "up_volume": 600000, "down_volume": 400000} if include_breadth else {},
    )


def safe_snapshot(**overrides) -> WcaMarketSnapshot:
    values = {
        "symbol": "SPY",
        "data_timestamp": timestamp(),
        "decision_timestamp": timestamp(),
        "candles": tuple(candles_series(count=40, start_price=100, step=0.01, volume=100000, wide_range=overrides.pop("wide_range", False))),
        "quote": WcaQuote(timestamp=timestamp(), bid=100, ask=100.02),
        "reason_codes": (),
    }
    volume = overrides.pop("volume", None)
    if volume is not None:
        values["candles"] = tuple(candles_series(count=40, start_price=100, step=0.01, volume=volume))
    values.update(overrides)
    return WcaMarketSnapshot(**values)


def safe_context() -> WcaLocalGateContext:
    return WcaLocalGateContext(evaluation_timestamp=timestamp(), remaining_allocated_risk_budget=1000, planned_risk=10)


def candles_series(*, count: int, start_price: float, step: float, volume: float, wide_range: bool = False) -> list[WcaCandle]:
    rows: list[WcaCandle] = []
    start = timestamp() - timedelta(minutes=count - 1)
    for index in range(count):
        close = start_price + index * step
        spread = 2.0 if wide_range else 0.10
        rows.append(
            WcaCandle(
                timestamp=start + timedelta(minutes=index),
                open=max(close - step, 0.01),
                high=close + spread,
                low=max(close - spread, 0.01),
                close=close,
                volume=volume,
                vwap=close,
            )
        )
    return rows


def timestamp() -> datetime:
    return datetime(2026, 1, 6, 17, 0, tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()
