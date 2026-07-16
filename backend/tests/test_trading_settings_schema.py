from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.app.domain import (
    TRADING_SETTINGS_FIELD_GROUPS,
    TRADING_SETTINGS_SCHEMA_VERSION,
    BaselineTradingSettings,
    DynamicPolicyBounds,
    HardRiskLimits,
    default_baseline_trading_settings,
    default_dynamic_policy_bounds,
    default_hard_risk_limits,
    risk_dollars_for_signal_multiplier,
    trading_settings_configuration_hash,
)


ROOT = Path(__file__).resolve().parents[2]


class TradingSettingsSchemaTest(unittest.TestCase):
    def test_schema_contains_required_step_40_fields(self) -> None:
        self.assertEqual(TRADING_SETTINGS_SCHEMA_VERSION, "canonical_trading_settings_v2")
        self.assertEqual(
            set(TRADING_SETTINGS_FIELD_GROUPS["baselineSettings"]),
            {
                "baseRiskPercent",
                "basePositionPercent",
                "baseOrderAllocationPercent",
                "baseDailyAllocationPercent",
                "baseAtrStopMultiplier",
                "baseMinimumStopPercent",
                "baseTargetR",
                "baseMaximumHoldingMinutes",
                "baseParticipationPercent",
                "baseEntryOffsetBps",
                "baseSlippagePerShare",
                "minimumExpectedValue",
                "minimumModelProbability",
            },
        )
        self.assertIn("maximumSpreadBps", TRADING_SETTINGS_FIELD_GROUPS["hardLimits"])
        self.assertIn("maximumAtrStopMultiplier", TRADING_SETTINGS_FIELD_GROUPS["dynamicBounds"])

    def test_backend_and_frontend_schema_field_groups_match(self) -> None:
        frontend_schema = (ROOT / "frontend" / "src" / "trading-settings" / "schema.ts").read_text(encoding="utf-8")
        self.assertIn(TRADING_SETTINGS_SCHEMA_VERSION, frontend_schema)

        for group_name, backend_fields in TRADING_SETTINGS_FIELD_GROUPS.items():
            frontend_fields = extract_frontend_group_fields(frontend_schema, group_name)
            self.assertEqual(tuple(frontend_fields), tuple(backend_fields), group_name)

    def test_signal_multiplier_below_one_reduces_baseline_risk(self) -> None:
        settings = BaselineTradingSettings(
            baseRiskPercent=1.0,
            configurationHash="settings-a",
        )

        self.assertEqual(
            risk_dollars_for_signal_multiplier(
                account_equity=10_000,
                baseline_settings=settings,
                signal_multiplier=0.25,
            ),
            25.0,
        )

    def test_configuration_hash_changes_for_every_effective_setting_group_and_artifact_input(self) -> None:
        baseline = default_baseline_trading_settings()
        limits = default_hard_risk_limits()
        bounds = default_dynamic_policy_bounds()
        base_hash = trading_settings_configuration_hash(
            baseline_settings=baseline,
            hard_limits=limits,
            dynamic_bounds=bounds,
            strategy_configuration_hash="strategy-a",
            ensemble_configuration_hash="ensemble-a",
            ml_configuration_hash="ml-a",
            risk_configuration_hash="risk-a",
            sizing_configuration_hash="sizing-a",
            entry_configuration_hash="entry-a",
            exit_configuration_hash="exit-a",
            gate_configuration_hash="gate-a",
            backtest_configuration_hash="backtest-a",
        )

        changed_baseline = baseline.model_copy(update={"baseRiskPercent": baseline.baseRiskPercent + 0.01})
        changed_limits = limits.model_copy(update={"maximumSpreadBps": limits.maximumSpreadBps + 1})
        changed_bounds = bounds.model_copy(update={"maximumRiskMultiplier": bounds.maximumRiskMultiplier + 0.1})
        self.assertNotEqual(
            base_hash,
            trading_settings_configuration_hash(
                baseline_settings=changed_baseline,
                hard_limits=limits,
                dynamic_bounds=bounds,
                strategy_configuration_hash="strategy-a",
                ensemble_configuration_hash="ensemble-a",
                ml_configuration_hash="ml-a",
                risk_configuration_hash="risk-a",
                sizing_configuration_hash="sizing-a",
                entry_configuration_hash="entry-a",
                exit_configuration_hash="exit-a",
                gate_configuration_hash="gate-a",
                backtest_configuration_hash="backtest-a",
            ),
        )
        self.assertNotEqual(
            base_hash,
            trading_settings_configuration_hash(
                baseline_settings=baseline,
                hard_limits=changed_limits,
                dynamic_bounds=bounds,
                strategy_configuration_hash="strategy-a",
                ensemble_configuration_hash="ensemble-a",
                ml_configuration_hash="ml-a",
                risk_configuration_hash="risk-a",
                sizing_configuration_hash="sizing-a",
                entry_configuration_hash="entry-a",
                exit_configuration_hash="exit-a",
                gate_configuration_hash="gate-a",
                backtest_configuration_hash="backtest-a",
            ),
        )
        self.assertNotEqual(
            base_hash,
            trading_settings_configuration_hash(
                baseline_settings=baseline,
                hard_limits=limits,
                dynamic_bounds=changed_bounds,
                strategy_configuration_hash="strategy-a",
                ensemble_configuration_hash="ensemble-a",
                ml_configuration_hash="ml-a",
                risk_configuration_hash="risk-a",
                sizing_configuration_hash="sizing-a",
                entry_configuration_hash="entry-a",
                exit_configuration_hash="exit-a",
                gate_configuration_hash="gate-a",
                backtest_configuration_hash="backtest-a",
            ),
        )
        self.assertNotEqual(
            base_hash,
            trading_settings_configuration_hash(
                baseline_settings=baseline,
                hard_limits=limits,
                dynamic_bounds=bounds,
                strategy_configuration_hash="strategy-b",
                ensemble_configuration_hash="ensemble-a",
                ml_configuration_hash="ml-a",
                risk_configuration_hash="risk-a",
                sizing_configuration_hash="sizing-a",
                entry_configuration_hash="entry-a",
                exit_configuration_hash="exit-a",
                gate_configuration_hash="gate-a",
                backtest_configuration_hash="backtest-a",
            ),
        )

    def test_old_frontend_multiplier_clamp_is_not_present(self) -> None:
        frontend_main = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")

        self.assertNotIn("Math.max(sizeMultiplier, 1)", frontend_main)
        self.assertIn("riskDollarsForSignalMultiplier", frontend_main)

    def test_dynamic_bounds_reject_invalid_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "maximumRiskMultiplier"):
            DynamicPolicyBounds(
                minimumRiskMultiplier=1.0,
                maximumRiskMultiplier=0.5,
                minConfidence=0.0,
                minReliability=0.0,
                minRegimeFit=0.0,
                maxSpreadPercent=100.0,
                maxParticipationPercent=100.0,
                minLiquidityShares=0,
                configurationHash="bounds",
            )


def extract_frontend_group_fields(source: str, group_name: str) -> list[str]:
    pattern = rf"{group_name}:\s*\[(.*?)\]"
    match = re.search(pattern, source, flags=re.DOTALL)
    if not match:
        raise AssertionError(f"missing frontend field group {group_name}")
    return re.findall(r'"([^"]+)"', match.group(1))


if __name__ == "__main__":
    unittest.main()
