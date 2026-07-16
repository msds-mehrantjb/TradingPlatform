from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.aggregation import WcaAggregationConfig, aggregate_wca
from backend.app.algorithms.wca.contracts import WcaBaselineSettings, WcaEffectiveSettings, WcaGateStatus, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.local_gates import WcaLocalGateContext, apply_local_gates_to_decision, evaluate_wca_local_gates


UTC = timezone.utc
ROOT = Path(__file__).parents[2]
LOCAL_GATES_PATH = ROOT / "backend" / "app" / "algorithms" / "wca" / "local_gates.py"


class WcaStep10LocalGatesTest(unittest.TestCase):
    def test_local_gate_results_are_structured_and_cover_wca_checks(self) -> None:
        aggregation = directional_aggregation()
        settings = effective_settings()
        gates = evaluate_wca_local_gates(
            aggregation=aggregation,
            effective_settings=settings,
            context=WcaLocalGateContext(
                evaluation_timestamp=timestamp(),
                trades_today=0,
                planned_risk=50,
                remaining_allocated_risk_budget=100,
                allocated_daily_loss_budget=500,
            ),
        )

        gate_ids = {gate.gate_id for gate in gates}
        self.assertTrue(
            {
                "minimum_active_strategies",
                "minimum_agreement",
                "minimum_average_confidence",
                "minimum_score",
                "minimum_signal_edge",
                "minimum_expected_value_after_costs",
                "strategy_family_concentration",
                "strategy_health",
                "wca_maximum_trades",
                "wca_cooldown",
                "wca_pyramiding",
                "wca_daily_loss_budget",
                "wca_allocated_risk_budget",
                "wca_session_eligibility",
                "wca_dynamic_profile_restrictions",
            }.issubset(gate_ids)
        )
        for gate in gates:
            self.assertTrue(gate.reason_code)
            self.assertTrue(gate.detail)
            self.assertIsNotNone(gate.evaluated_value)
            self.assertIsNotNone(gate.required_value)
            self.assertIn(gate.status, {WcaGateStatus.PASS.value, WcaGateStatus.WARN.value, WcaGateStatus.NOT_APPLICABLE.value})

    def test_failed_local_gate_converts_proposed_entry_to_hold(self) -> None:
        aggregation = directional_aggregation()
        gates = evaluate_wca_local_gates(
            aggregation=aggregation,
            effective_settings=effective_settings(),
            context=WcaLocalGateContext(
                evaluation_timestamp=timestamp(),
                trades_today=8,
                planned_risk=50,
                remaining_allocated_risk_budget=100,
                allocated_daily_loss_budget=500,
            ),
        )

        self.assertTrue(any(gate.gate_id == "wca_maximum_trades" and gate.status == WcaGateStatus.FAIL.value for gate in gates))
        self.assertEqual(apply_local_gates_to_decision(WcaSide.BUY, gates), WcaSide.HOLD)

    def test_local_gates_do_not_modify_strategy_signals(self) -> None:
        evaluations = (
            evaluation("C1", WcaSide.BUY, 0.90, 0.10),
            evaluation("C7", WcaSide.BUY, 0.85, 0.10),
            evaluation("C9", WcaSide.SELL, 0.20, 0.05),
        )
        original = tuple(row.deterministic_json() for row in evaluations)
        aggregation = aggregate_wca(
            evaluations,
            config=WcaAggregationConfig(minimum_active_strategies=1, minimum_winner_edge=0.01, maximum_family_concentration=1.0),
        )

        _ = evaluate_wca_local_gates(
            aggregation=aggregation,
            effective_settings=effective_settings(entries_blocked=True, final_risk_percent=0),
            context=WcaLocalGateContext(evaluation_timestamp=timestamp()),
        )

        self.assertEqual(tuple(row.deterministic_json() for row in evaluations), original)

    def test_local_gates_cannot_block_risk_reducing_exit(self) -> None:
        gates = evaluate_wca_local_gates(
            aggregation=directional_aggregation(),
            effective_settings=effective_settings(entries_blocked=True, final_risk_percent=0),
            context=WcaLocalGateContext(evaluation_timestamp=timestamp(), is_risk_reducing_exit=True),
        )

        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0].status, WcaGateStatus.NOT_APPLICABLE.value)
        self.assertFalse(gates[0].blocks_entry)
        self.assertEqual(apply_local_gates_to_decision(WcaSide.SELL, gates, is_risk_reducing_exit=True), WcaSide.SELL)

    def test_dynamic_profile_restriction_blocks_new_entries(self) -> None:
        gates = evaluate_wca_local_gates(
            aggregation=directional_aggregation(),
            effective_settings=effective_settings(entries_blocked=True, final_risk_percent=0),
            context=WcaLocalGateContext(evaluation_timestamp=timestamp()),
        )

        gate = next(row for row in gates if row.gate_id == "wca_dynamic_profile_restrictions")
        self.assertEqual(gate.status, WcaGateStatus.FAIL.value)
        self.assertTrue(gate.blocks_entry)

    def test_local_gate_package_imports_no_ml_or_other_algorithm(self) -> None:
        tree = ast.parse(LOCAL_GATES_PATH.read_text(encoding="utf-8"), filename=str(LOCAL_GATES_PATH))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = ("backend.app.ml", "meta_strategy", "weighted_voting", "regime", "forecast")
        self.assertFalse(any(any(term in module.lower() for term in forbidden) for module in imports))
        self.assertFalse(any(module.startswith("backend.app.algorithms.") and not module.startswith("backend.app.algorithms.wca") for module in imports))


def directional_aggregation():
    return aggregate_wca(
        (
            evaluation("C1", WcaSide.BUY, 0.90, 0.10),
            evaluation("C7", WcaSide.BUY, 0.85, 0.10),
            evaluation("C9", WcaSide.SELL, 0.20, 0.05),
        ),
        estimated_expectancy_after_costs=0.05,
        config=WcaAggregationConfig(minimum_active_strategies=1, minimum_winner_edge=0.01, maximum_family_concentration=1.0),
    )


def effective_settings(**overrides: object) -> WcaEffectiveSettings:
    baseline = WcaBaselineSettings(
        minimum_active_strategies=1,
        max_daily_trades=3,
        minimum_score=0.35,
        minimum_directional_agreement=0.50,
        minimum_average_confidence=0.45,
    )
    values = {
        "baseline": baseline,
        "final_risk_percent": 1.0,
        "final_minimum_score": 0.35,
        "final_minimum_agreement": 0.50,
        "final_minimum_confidence": 0.45,
        "final_max_daily_trades": 3,
        "final_entry_cutoff_minutes": 15 * 60 + 30,
        "entries_blocked": False,
        "final_pyramiding_enabled": False,
    }
    values.update(overrides)
    return WcaEffectiveSettings(**values)


def evaluation(strategy_id: str, signal: WcaSide, confidence: float, weight: float) -> WcaStrategyEvaluation:
    direction = 1 if signal == WcaSide.BUY else -1 if signal == WcaSide.SELL else 0
    return WcaStrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version=f"wca_{strategy_id.lower()}_test_v1",
        name=strategy_id,
        status="ACTIVE",
        signal=signal,
        confidence=confidence,
        raw_confidence=confidence,
        calibrated_confidence=confidence,
        direction=signal,
        applicability="ACTIVE",
        evidence_strength=confidence,
        data_quality_status="ACTIVE",
        base_weight=weight,
        effective_weight=weight,
        contribution=round(direction * weight * confidence, 4),
        reason_codes=(f"wca.strategy.{strategy_id.lower()}",),
    )


def timestamp() -> datetime:
    return datetime(2026, 1, 6, 17, 0, tzinfo=UTC)


if __name__ == "__main__":
    unittest.main()
