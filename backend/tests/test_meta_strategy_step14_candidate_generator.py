from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import (
    EXECUTION_SEQUENCE,
    CandidateGenerationConfig,
    FamilyAggregationConfig,
    MetaStrategyMarketSnapshot,
    generate_deterministic_candidate,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "meta_strategy_current_behavior.json"
GENERATION_CONFIG = CandidateGenerationConfig(
    aggregation=FamilyAggregationConfig(
        strategy_contribution_cap=1.0,
        family_contribution_cap=1.0,
        correlation_group_cap=1.0,
        minimum_active_strategies=2,
        minimum_independent_families=2,
        maximum_abstention_rate=0.90,
        minimum_conflict_edge=0.50,
    )
)


class MetaStrategyStep14CandidateGeneratorTest(unittest.TestCase):
    maxDiff = None

    def test_candidate_generation_runs_required_sequence_without_ml(self) -> None:
        candidate = generate_deterministic_candidate(snapshot_fixture(case="buy"), config=GENERATION_CONFIG)

        self.assertEqual(candidate.evidence["executionSequence"], EXECUTION_SEQUENCE)
        self.assertFalse(candidate.evidence["mlInvoked"])
        self.assertEqual(candidate.direction, "BUY")
        self.assertEqual(candidate.deterministic_candidate.signal, "BUY")
        self.assertTrue(candidate.deterministic_candidate.eligible)
        self.assertIn("directionalOutputs", candidate.evidence)
        self.assertIn("contextOutputs", candidate.evidence)
        self.assertIn("regimeOutputs", candidate.evidence)
        self.assertIn("safetyOutputs", candidate.evidence)
        self.assertIn("familyAggregation", candidate.evidence)
        self.assertIn("meta_strategy.candidate.generated_without_ml", candidate.reason_codes)

    def test_candidate_includes_scores_families_evidence_and_reason_codes(self) -> None:
        candidate = generate_deterministic_candidate(snapshot_fixture(case="buy"), config=GENERATION_CONFIG)

        self.assertGreater(candidate.deterministic_confidence, 0.0)
        self.assertGreater(candidate.winning_score, candidate.opposing_score)
        self.assertEqual(candidate.edge, round(candidate.winning_score - candidate.opposing_score, 6))
        self.assertIn("TREND", candidate.supporting_families)
        self.assertIn("BREAKOUT", candidate.supporting_families)
        self.assertEqual(candidate.opposing_families, ())
        self.assertTrue(candidate.evidence["familyAggregation"]["familyScores"])
        self.assertTrue(candidate.reason_codes)

    def test_sell_candidate_can_be_produced_without_ml(self) -> None:
        candidate = generate_deterministic_candidate(snapshot_fixture(case="sell"), config=GENERATION_CONFIG)

        self.assertEqual(candidate.direction, "SELL")
        self.assertEqual(candidate.deterministic_candidate.signal, "SELL")
        self.assertIn("REVERSAL", candidate.supporting_families)
        self.assertIn("TREND", candidate.supporting_families)
        self.assertGreater(candidate.winning_score, candidate.opposing_score)

    def test_hold_candidate_is_produced_for_conflict_without_ml(self) -> None:
        candidate = generate_deterministic_candidate(snapshot_fixture(case="hold_conflict"), config=GENERATION_CONFIG)

        self.assertEqual(candidate.direction, "HOLD")
        self.assertFalse(candidate.deterministic_candidate.eligible)
        self.assertIn("meta_strategy.aggregation.buy_sell_conflict", candidate.reason_codes)
        self.assertIn("meta_strategy.candidate.hold_without_ml", candidate.reason_codes)

    def test_safety_modules_block_entries_after_aggregation(self) -> None:
        candidate = generate_deterministic_candidate(
            snapshot_fixture(case="buy", features={"avoidTrading": True}),
            config=GENERATION_CONFIG,
        )

        self.assertEqual(candidate.direction, "HOLD")
        self.assertEqual(candidate.evidence["rawAggregationSignal"], "BUY")
        self.assertTrue(candidate.evidence["safetyBlocked"])
        self.assertIn("cash_avoid_trading_filter", candidate.evidence["safetyBlockers"])
        self.assertIn("meta_strategy.candidate.safety_blocked", candidate.reason_codes)

    def test_ml_modules_are_not_imported_by_candidate_generator_or_strategies(self) -> None:
        package_dir = Path(__file__).parents[1] / "app" / "algorithms" / "meta_strategy"
        checked_files = (
            package_dir / "candidate_generator.py",
            package_dir / "family_aggregation.py",
            *tuple((package_dir / "strategies").rglob("*.py")),
        )
        violations = []
        for path in checked_files:
            source = path.read_text(encoding="utf-8")
            if "backend.app.ml" in source or "apply_safe_ml_inference" in source:
                violations.append(str(path.relative_to(package_dir)))

        self.assertEqual(violations, [])

    def test_fixed_fixture_deterministic_outputs_match_legacy_for_candidate_cases(self) -> None:
        fixtures = {fixture["id"]: fixture for fixture in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["fixtures"]}
        cases = {
            "buy_candidate": snapshot_fixture(case="buy"),
            "sell_candidate": snapshot_fixture(case="sell"),
            "hold_candidate": snapshot_fixture(case="hold_conflict"),
        }
        for fixture_id, snapshot in cases.items():
            with self.subTest(fixture=fixture_id):
                candidate = generate_deterministic_candidate(snapshot, config=GENERATION_CONFIG)
                legacy = fixtures[fixture_id]["deterministicCandidate"]
                self.assertEqual(candidate.direction, legacy["signal"])
                self.assertEqual(candidate.evidence["documentedImprovements"][0], "Meta-Strategy candidate generation is package-owned, never calls ML, applies safety after deterministic aggregation, and caps correlated family influence.")


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    case = overrides.get("case", "buy")
    price = float(overrides.get("price", {"buy": 101.0, "sell": 99.0, "hold_conflict": 100.0}[case]))
    features = default_features(case)
    features.update(overrides.get("features", {}))
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id=f"decision-generator-{case}",
        snapshot_id=f"snapshot-generator-{case}",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=price - 0.01,
        ask_price=price + 0.01,
        spread_bps=5.0,
        volume=100_000,
        source_cutoff_timestamp=NOW,
        point_in_time=True,
        candles={"1m": candles(60, price), "5m": candles(60, price), "15m": candles(60, price)},
        vwap=overrides.get("vwap", 100.0),
        moving_averages=overrides.get("moving_averages", moving_averages(case)),
        atr=overrides.get("atr", {"1m": 1.0, "5m": 1.0, "15m": 1.0}),
        adx=overrides.get("adx", {"1m": 24.0 if case == "buy" else 20.0, "5m": 22.0, "15m": 20.0}),
        rsi=overrides.get("rsi", {"1m": 50.0 if case == "buy" else 70.0 if case == "sell" else 50.0}),
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands=overrides.get("bollinger_bands", {"1m": {"upper": 105.0, "middle": 100.0, "lower": 95.0}}),
        relative_volume=overrides.get("relative_volume", {"1m": 1.5, "5m": 1.5, "15m": 1.5}),
        spread=overrides.get("spread", {"basisPoints": 5.0, "dollars": 0.02}),
        liquidity=overrides.get("liquidity", {"level": "good", "score": 0.8}),
        session_phase=overrides.get("session_phase", "morning"),
        gap_state=overrides.get("gap_state", {"state": "flat_open", "gapPercent": 0.0}),
        qqq_iwm_context=overrides.get("qqq_iwm_context", {"spyVsQqq": 1.01 if case != "sell" else 0.99, "spyVsIwm": 1.0}),
        breadth=overrides.get("breadth", {"averageReturn": 0.001, "positiveShare": 0.56, "componentCount": 500}),
        economic_event_state=overrides.get("economic_event_state", {"state": "none", "severity": "none", "minutesToEvent": 60, "active": False}),
        features=features,
    )


def default_features(case: str) -> dict[str, Any]:
    common = {
        "cashAvailable": 10_000.0,
        "avoidTrading": False,
        "haltLuldState": "clear",
        "operationalHealth": {"status": "ok", "brokerConnected": True, "dataConnected": True},
        "pullbackDepthAtr": 0.0,
        "openingRangeHigh": 100.0,
        "openingRangeLow": 99.0,
        "bollingerWidthPercentile": 0.5,
        "failedBreakoutSide": "none",
        "reclaimDistanceAtr": 0.0,
        "sweepSide": "none",
        "rejectionWickRatio": 0.0,
        "gapTradeType": "continuation",
    }
    if case == "buy":
        common.update({"openingRangeHigh": 100.0, "pullbackDepthAtr": 0.75})
    elif case == "sell":
        common.update({"failedBreakoutSide": "upside", "reclaimDistanceAtr": 0.15})
    elif case == "hold_conflict":
        common.update({"openingRangeHigh": 100.0, "openingRangeLow": 100.0, "failedBreakoutSide": "upside", "reclaimDistanceAtr": 0.15})
    return common


def moving_averages(case: str) -> dict[str, dict[str, float]]:
    if case == "sell":
        return {"1m": {"ema20": 99.0, "ema50": 100.0}, "5m": {"ema20": 99.0, "ema50": 100.0}, "15m": {"ema20": 99.0, "ema50": 100.0}}
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
