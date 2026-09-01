from __future__ import annotations

import json
import subprocess
import sys
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
    VOTING_ENSEMBLE_BACKGROUND_WORKERS,
    VOTING_ENSEMBLE_BACKTEST_REPLAY_ADAPTERS,
    VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    VOTING_ENSEMBLE_EXECUTION_ADAPTERS,
    VOTING_ENSEMBLE_MODULE_INVENTORY,
    VOTING_ENSEMBLE_ORDER_PLANNER_MODULES,
    VOTING_ENSEMBLE_REGIME_STRATEGIES,
    VOTING_ENSEMBLE_RISK_BUDGET_MODULES,
    VOTING_ENSEMBLE_SAFETY_STRATEGIES,
    VOTING_ENSEMBLE_STRATEGIES,
    VOTING_ENSEMBLE_TRADING_SETTINGS_RESOLVERS,
    StrategyCollection,
    active_module_ids,
    directional_strategy_input_ids,
    resolve_strategy,
    shadow_module_ids,
    validate_voting_ensemble_inventory_startup,
)
from backend.app.algorithms.voting_ensemble.service import (
    DIRECTIONAL_STRATEGIES,
    STRATEGY_EVALUATORS_BY_ID,
    VotingEnsembleService,
    voting_ensemble_service_runtime_bindings,
)


class VotingEnsembleModuleInventoryTest(unittest.TestCase):
    maxDiff = None

    def test_authoritative_inventory_matches_requested_active_shadow_split(self) -> None:
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.directional),
            (
                ("multi_timeframe_trend_alignment", "active"),
                ("first_pullback_after_open", "active"),
                ("failed_breakout_reversal", "active"),
                ("liquidity_sweep_reversal", "active"),
                ("bollinger_band_reversion", "active"),
                ("atr_overextension_reversion", "active"),
                ("opening_range_breakout", "shadow"),
                ("vwap_trend_continuation", "shadow"),
                ("gap_continuation_fade", "shadow"),
            ),
        )
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.context),
            (
                ("relative_strength_qqq_iwm", "active"),
                ("market_breadth_momentum", "active"),
                ("economic_event_context", "shadow"),
                ("market_structure_context", "shadow"),
                ("volume_confirmation_context", "shadow"),
                ("vwap_position_context", "shadow"),
                ("market_forecast_context", "shadow"),
            ),
        )
        self.assertEqual(
            module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.regime),
            (
                ("adx_atr_regime_classifier", "active"),
            ),
        )
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.safety), (("cash_avoid_trading_filter", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.aggregator), (("ensemble_strategy_voting", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.tradingSettingsResolver), (("trading_settings_resolver", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.riskBudget), (("risk_budget", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.orderPlanner), (("order_planner", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.executionAdapter), (("execution_adapter", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.backtestReplayAdapter), (("backtest_replay_adapter", "active"),))
        self.assertEqual(module_pairs(VOTING_ENSEMBLE_MODULE_INVENTORY.backgroundWorker), (("background_worker", "active"),))

    def test_registry_is_derived_from_inventory_statuses(self) -> None:
        inventory_ids = {
            module.strategyId: module.lifecycleStatus
            for module in VOTING_ENSEMBLE_MODULE_INVENTORY.modules
        }
        registry_entries = (
            *VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
            *VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
            *VOTING_ENSEMBLE_REGIME_STRATEGIES,
            *VOTING_ENSEMBLE_SAFETY_STRATEGIES,
            *VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
            *VOTING_ENSEMBLE_TRADING_SETTINGS_RESOLVERS,
            *VOTING_ENSEMBLE_RISK_BUDGET_MODULES,
            *VOTING_ENSEMBLE_ORDER_PLANNER_MODULES,
            *VOTING_ENSEMBLE_EXECUTION_ADAPTERS,
            *VOTING_ENSEMBLE_BACKTEST_REPLAY_ADAPTERS,
            *VOTING_ENSEMBLE_BACKGROUND_WORKERS,
        )

        self.assertEqual({entry.strategyId for entry in registry_entries}, set(inventory_ids))
        for entry in registry_entries:
            with self.subTest(strategy=entry.strategyId):
                self.assertEqual(entry.lifecycleStatus, inventory_ids[entry.strategyId])
                self.assertEqual(entry.enabled, entry.lifecycleStatus == "active")

    def test_every_inventory_module_has_complete_authoritative_contract_fields(self) -> None:
        required = {
            "strategyId",
            "strategyName",
            "strategyVersion",
            "family",
            "role",
            "collection",
            "lifecycleStatus",
            "requiredInputs",
            "implementationPath",
            "runtimeBinding",
            "backtestBinding",
            "settingsNamespace",
            "stateNamespace",
            "persistenceNamespace",
            "testPath",
            "promotionEvidence",
            "enabled",
        }
        self.assertEqual(len({entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES}), len(VOTING_ENSEMBLE_STRATEGIES))
        for entry in VOTING_ENSEMBLE_STRATEGIES:
            with self.subTest(module=entry.strategyId):
                payload = entry.model_dump(mode="json")
                self.assertTrue(required.issubset(payload))
                self.assertTrue(entry.implementationPath)
                self.assertFalse(entry.implementationPath.startswith("backend.app.algorithms.meta_strategy"))
                self.assertFalse(entry.implementationPath.startswith("backend.app.algorithms.wca"))

    def test_runtime_directional_inputs_are_active_only(self) -> None:
        self.assertEqual(
            directional_strategy_input_ids(),
            (
                "multi_timeframe_trend_alignment",
                "first_pullback_after_open",
                "failed_breakout_reversal",
                "liquidity_sweep_reversal",
                "bollinger_band_reversion",
                "atr_overextension_reversion",
            ),
        )
        self.assertEqual(
            shadow_module_ids(StrategyCollection.DIRECTIONAL),
            ("opening_range_breakout", "vwap_trend_continuation", "gap_continuation_fade"),
        )
        self.assertEqual(tuple(getattr(strategy, "strategyId") for strategy in DIRECTIONAL_STRATEGIES), directional_strategy_input_ids())

    def test_shadow_context_modules_are_registered_but_not_active(self) -> None:
        self.assertEqual(active_module_ids(StrategyCollection.CONTEXT), ("relative_strength_qqq_iwm", "market_breadth_momentum"))
        self.assertEqual(
            shadow_module_ids(StrategyCollection.CONTEXT),
            (
                "economic_event_context",
                "market_structure_context",
                "volume_confirmation_context",
                "vwap_position_context",
                "market_forecast_context",
            ),
        )

        with self.assertRaises(KeyError):
            resolve_strategy("Unregistered Strategy")

        self.assertEqual(len({entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES}), len(VOTING_ENSEMBLE_STRATEGIES))

    def test_service_status_exposes_inventory_and_active_runtime_modules(self) -> None:
        status = VotingEnsembleService().status()

        self.assertEqual(status["moduleInventory"], VOTING_ENSEMBLE_MODULE_INVENTORY.model_dump(mode="json"))
        self.assertEqual(
            status["directionalStrategies"],
            [
                "multi_timeframe_trend_alignment",
                "first_pullback_after_open",
                "failed_breakout_reversal",
                "liquidity_sweep_reversal",
                "bollinger_band_reversion",
                "atr_overextension_reversion",
            ],
        )
        self.assertEqual(status["shadowDirectionalStrategies"], ["opening_range_breakout", "vwap_trend_continuation", "gap_continuation_fade"])
        self.assertEqual(status["dynamicRoleStrategies"], [])
        self.assertEqual(status["contextSignals"], ["relative_strength_qqq_iwm", "market_breadth_momentum"])
        self.assertTrue(status["inventoryStatus"]["valid"])

    def test_validation_fails_closed_when_runtime_and_inventory_disagree(self) -> None:
        missing = validate_voting_ensemble_inventory_startup({StrategyCollection.DIRECTIONAL.value: ("multi_timeframe_trend_alignment",)})
        duplicate = validate_voting_ensemble_inventory_startup(
            {
                StrategyCollection.DIRECTIONAL.value: (
                    "multi_timeframe_trend_alignment",
                    "multi_timeframe_trend_alignment",
                    "first_pullback_after_open",
                    "failed_breakout_reversal",
                    "liquidity_sweep_reversal",
                    "bollinger_band_reversion",
                    "atr_overextension_reversion",
                )
            }
        )
        shadow = validate_voting_ensemble_inventory_startup(
            {StrategyCollection.CONTEXT.value: ("relative_strength_qqq_iwm", "market_breadth_momentum", "economic_event_context")}
        )

        self.assertFalse(missing["valid"])
        self.assertTrue(any(code.startswith("active_directional_executed_zero_times:first_pullback_after_open") for code in missing["errors"]))
        self.assertFalse(duplicate["valid"])
        self.assertIn("active_directional_executed_more_than_once:multi_timeframe_trend_alignment", duplicate["errors"])
        self.assertFalse(shadow["valid"])
        self.assertIn("shadow_module_affects_active_decision:economic_event_context", shadow["errors"])

    def test_inventory_status_endpoint_reports_actual_runtime_bindings(self) -> None:
        response = TestClient(app).get("/api/voting-ensemble/inventory/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertTrue(body["valid"])
        self.assertEqual(body["actualRuntimeBindings"]["DIRECTIONAL"], list(directional_strategy_input_ids()))
        self.assertEqual(body["actualRuntimeBindings"]["CONTEXT"], ["relative_strength_qqq_iwm", "market_breadth_momentum"])
        background_worker = next(module for module in body["modules"] if module["strategyId"] == "background_worker")
        self.assertEqual(background_worker["actualRuntimeCount"], 1)
        self.assertEqual(body["actualRuntimeBindings"]["BACKGROUND_WORKER"], ["background_worker"])
        self.assertTrue(background_worker["implementationImportable"])

    def test_service_runtime_bindings_are_authoritative(self) -> None:
        runtime = voting_ensemble_service_runtime_bindings()

        self.assertTrue(runtime["validation"]["valid"])
        self.assertEqual(runtime["actualRuntimeBindings"][StrategyCollection.DIRECTIONAL.value], directional_strategy_input_ids())
        self.assertEqual(runtime["actualRuntimeBindings"][StrategyCollection.CONTEXT.value], active_module_ids(StrategyCollection.CONTEXT))

    def test_voting_ensemble_package_imports_without_other_algorithm_internals(self) -> None:
        code = (
            "import json, sys\n"
            "import backend.app.algorithms.voting_ensemble\n"
            "forbidden = [name for name in sys.modules if name.startswith('backend.app.algorithms.') "
            "and not name.startswith('backend.app.algorithms.voting_ensemble')]\n"
            "print(json.dumps(forbidden))\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
        forbidden = json.loads(result.stdout)
        self.assertEqual(forbidden, [])


def module_pairs(modules) -> tuple[tuple[str, str], ...]:
    return tuple((module.id, module.status) for module in modules)


if __name__ == "__main__":
    unittest.main()
