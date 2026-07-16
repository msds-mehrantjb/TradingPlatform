from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.domain.models import OrderPlan, OperatingMode, Signal
from backend.app.gates import GlobalGateEngine
from backend.app.trading_policy import DynamicTradingPolicyConfig, DynamicTradingPolicyEngine
from backend.tests.test_dynamic_trading_policy_engine import (
    account_state as policy_account_state,
    hard_limits,
    policy_features,
    policy_inputs,
    trade_candidate,
)
from backend.tests.test_global_gate_engine import (
    account_state as gate_account_state,
    gate_input,
    order_plan,
    pass_broker_state,
    pass_data_state,
    pass_execution_state,
    pass_market_state,
)


ROOT = Path(__file__).resolve().parents[2]
BACKEND_TESTS = ROOT / "backend" / "tests"
FRONTEND = ROOT / "frontend"


class Phase12CoverageManifestTest(unittest.TestCase):
    def test_each_directional_strategy_has_isolated_fixture_coverage(self) -> None:
        strategy_test_files = {
            "Multi-Timeframe Trend Alignment": "test_multi_timeframe_trend_alignment.py",
            "First Pullback After Open": "test_first_pullback_after_open.py",
            "VWAP Trend Continuation": "test_vwap_trend_continuation.py",
            "Opening Range Breakout": "test_opening_range_breakout.py",
            "Volatility Breakout": "test_volatility_breakout.py",
            "Failed Breakout Reversal": "test_failed_breakout_reversal.py",
            "Liquidity Sweep Reversal": "test_liquidity_sweep_reversal.py",
            "VWAP Mean Reversion": "test_vwap_mean_reversion.py",
            "Bollinger/ATR Reversion": "test_bollinger_atr_reversion.py",
            "Gap Continuation / Gap Fade": "test_gap_continuation_gap_fade.py",
        }
        required_terms = ("buy", "sell", "hold", "missing")

        for strategy_name, filename in strategy_test_files.items():
            source = read_test(filename).lower()
            with self.subTest(strategy=strategy_name):
                for term in required_terms:
                    self.assertIn(term, source)

        reset_sources = "\n".join(
            read_test(filename).lower()
            for filename in ("test_first_pullback_after_open.py", "test_gap_continuation_gap_fade.py")
        )
        self.assertIn("session_state_resets", reset_sources)
        self.assertIn("boundary", read_test("test_first_pullback_after_open.py").lower())
        self.assertIn("no_lookahead", read_test("test_point_in_time_feature_engine.py").lower())

    def test_ensemble_sizing_gate_ml_and_backtest_requirements_are_in_ci_sources(self) -> None:
        expectations = {
            "test_family_aware_ensemble.py": (
                "aggregator_cannot_vote_for_itself",
                "context_cannot_cast_full_votes",
                "averaged_not_counted",
                "duplicating_strategy",
                "hold_for_ties",
                "independent_family_support",
                "context_conflict",
                "safety_block",
            ),
            "test_strategy_registry_v2.py": ("alias", "duplicate", "not_own_input"),
            "test_trading_settings_schema.py": ("0.25", "old_frontend_multiplier_clamp_is_not_present"),
            "test_dynamic_trading_policy_engine.py": (
                "hard_limits_cannot_be_overridden",
                "wider_stops_reduce_quantity",
                "daily_drawdown",
                "cross_algorithm_exposure",
                "quantity_zero",
            ),
            "test_global_gate_engine.py": (
                "fresh_quote",
                "account_restricted",
                "duplicateOrder",
                "conflictingOrder",
                "entryWindowOpen",
            ),
            "test_safe_ml_inference_modes.py": (
                "forbiddenFieldsChecked",
                "schema_mismatch",
                "out_of_distribution",
                "shadow_mode",
            ),
            "test_meta_strategy_nested_training.py": ("chronological", "purged", "embargo", "inner_out_of_fold"),
            "test_meta_probability_calibration.py": ("out_of_fold", "rejects_in_sample"),
            "test_candidate_meta_features.py": ("leakage", "ForbiddenMLFeatureFieldError"),
            "test_event_driven_replay_engine.py": (
                "future_candle",
                "filledAt",
                "deterministically",
                "end_of_day",
            ),
            "test_execution_simulation.py": (
                "same_bar_target_stop_ambiguity",
                "unfilled_limit",
                "partial_fill",
            ),
        }

        for filename, terms in expectations.items():
            source = read_test(filename)
            with self.subTest(filename=filename):
                for term in terms:
                    self.assertIn(term, source)

    def test_frontend_has_selected_type_script_test_runner_and_v2_tests(self) -> None:
        package_json = (FRONTEND / "package.json").read_text(encoding="utf-8")
        frontend_test = (FRONTEND / "tests" / "V2DecisionPanel.test.ts").read_text(encoding="utf-8")

        self.assertIn("node --experimental-strip-types", package_json)
        self.assertIn("node:test", frontend_test)
        self.assertIn("--experimental-strip-types", package_json)
        self.assertIn("Directional strategies", frontend_test)
        self.assertIn("Not evaluated", frontend_test)
        self.assertIn("hard-blocker", frontend_test)


class Phase12RiskInvariantPropertyStyleTest(unittest.TestCase):
    def test_hard_risk_cap_is_never_exceeded_across_representative_inputs(self) -> None:
        engine = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE))

        for max_risk_percent in (0.05, 0.1, 0.25, 0.5, 1.0):
            decision = engine.evaluate(
                policy_inputs(
                    candidate=trade_candidate(confidence=1.0, features=policy_features(currentVolume=100_000, expectedVolume=100_000)),
                    hard_limits=hard_limits(maximum_risk_per_trade_percent=max_risk_percent, maximum_shares=10_000),
                    account=policy_account_state(realized_pnl_today=0),
                    meta_probability=0.99,
                )
            )
            hard_cap_dollars = 10_000 * (max_risk_percent / 100.0)

            with self.subTest(max_risk_percent=max_risk_percent):
                self.assertLessEqual(decision.approvedRiskDollars, hard_cap_dollars + 1e-9)
                self.assertLessEqual(decision.capBreakdown.plannedRiskDollars, decision.approvedRiskDollars + 1e-9)

    def test_wider_stop_property_never_increases_share_quantity(self) -> None:
        engine = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE))
        previous_quantity: int | None = None

        for atr in (0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
            decision = engine.evaluate(
                policy_inputs(
                    candidate=trade_candidate(
                        confidence=1.0,
                        features=policy_features(atr=atr, currentVolume=100_000, expectedVolume=100_000),
                    ),
                    meta_probability=0.99,
                    hard_limits=hard_limits(maximum_shares=10_000),
                )
            )

            with self.subTest(atr=atr):
                if previous_quantity is not None:
                    self.assertLessEqual(decision.quantity, previous_quantity)
                previous_quantity = decision.quantity

    def test_zero_or_exhausted_capacity_always_blocks_new_entry(self) -> None:
        engine = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE))
        cases = (
            policy_inputs(hard_limits=hard_limits(maximum_shares=0), meta_probability=0.99),
            policy_inputs(account=policy_account_state(realized_pnl_today=-300), meta_probability=0.99),
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(globalExposureRemainingNotional=50)), meta_probability=0.99),
        )

        for inputs in cases:
            decision = engine.evaluate(inputs)
            with self.subTest(reason_codes=decision.reasonCodes):
                self.assertFalse(decision.tradeAllowed)
                self.assertEqual(decision.quantity, 0)


class Phase12GateAndOrderInvariantTest(unittest.TestCase):
    def test_required_gate_blockers_cover_entry_cutoff_stale_quote_halt_broker_and_order_conflicts(self) -> None:
        scenarios = {
            "entry_cutoff": {"operationalState": {**pass_operational_state(), "entryWindowOpen": False}},
            "stale_quote": {"dataState": {**pass_data_state(), "freshQuote": False}},
            "halt": {"marketState": {**pass_market_state(), "symbolHalt": True}},
            "broker_restriction": {"brokerState": {**pass_broker_state(), "accountNotRestricted": False}},
            "duplicate_order": {"executionState": {**pass_execution_state(), "duplicateOrder": True}},
            "conflicting_algorithm_order": {"executionState": {**pass_execution_state(), "conflictingOrder": True}},
        }

        for name, overrides in scenarios.items():
            decision = GlobalGateEngine().evaluate(gate_input(orderPlan=order_plan(), **overrides))
            with self.subTest(name=name):
                self.assertFalse(decision.allowed)
                self.assertGreater(len(decision.hardBlockers), 0)

    def test_protective_exit_remains_allowed_with_failed_entry_gates(self) -> None:
        decision = GlobalGateEngine().evaluate(
            gate_input(
                orderIntent="protective_exit",
                operationalState={**pass_operational_state(), "entryWindowOpen": False},
                dataState={**pass_data_state(), "freshQuote": False},
                marketState={**pass_market_state(), "symbolHalt": True},
                brokerState={**pass_broker_state(), "accountNotRestricted": False},
                orderPlan=order_plan(),
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.hardBlockers, [])
        self.assertGreater(len(decision.cautions), 0)

    def test_invalid_stop_and_target_geometry_is_rejected_by_domain_model(self) -> None:
        with self.assertRaises(ValidationError):
            OrderPlan(
                orderPlanId="invalid-long-stop",
                candidateId="candidate",
                symbol="SPY",
                side=Signal.BUY,
                orderType="LIMIT",
                quantity=10,
                entryPrice=100.0,
                stopPrice=101.0,
                targetPrice=102.0,
                limitPrice=100.0,
                timeInForce="DAY",
                eligible=True,
                explanation="Invalid long geometry.",
                generatedAt=gate_account_state().observedAt,
                sessionDate=gate_account_state().sessionDate,
                configurationHash="invalid",
            )


def pass_operational_state() -> dict[str, bool]:
    return {
        "tradingEnabled": True,
        "paperTradingMode": True,
        "marketOpen": True,
        "entryWindowOpen": True,
        "validSession": True,
    }


def read_test(filename: str) -> str:
    return (BACKEND_TESTS / filename).read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
