from __future__ import annotations

import ast
import importlib
import pkgutil
import unittest
from pathlib import Path

from backend.app.algorithms.weighted_voting.architecture import weighted_voting_architecture_contract


PACKAGE_NAME = "backend.app.algorithms.weighted_voting"
PACKAGE_PATH = Path(__file__).parents[1] / "app" / "algorithms" / "weighted_voting"

EXPECTED_FILES = {
    "__init__.py",
    "architecture.py",
    "identity.py",
    "api.py",
    "service.py",
    "models.py",
    "config.py",
    "catalog.py",
    "market_snapshot.py",
    "market_condition.py",
    "signal_engine.py",
    "weight_engine.py",
    "aggregation.py",
    "decision_gates.py",
    "decision_kernel.py",
    "dynamic_settings.py",
    "inventory.py",
    "runtime_context.py",
    "risk_budget.py",
    "position_sizing.py",
    "position_trade_state.py",
    "entry_policy.py",
    "exit_policy.py",
    "order_proposal.py",
    "execution_gateway.py",
    "local_paper_broker.py",
    "performance_tracker.py",
    "persistence.py",
    "scheduler.py",
    "strategies/base.py",
    "strategies/opening_range_breakout.py",
    "strategies/first_pullback_after_open.py",
    "strategies/vwap_trend_continuation.py",
    "strategies/vwap_mean_reversion.py",
    "strategies/failed_breakout_reversal.py",
    "strategies/liquidity_sweep_reversal.py",
    "strategies/bollinger_atr_reversion.py",
    "strategies/volatility_breakout.py",
    "backtest/data_validation.py",
    "backtest/execution_simulator.py",
    "backtest/walk_forward.py",
    "backtest/engine.py",
}

FORBIDDEN_IMPORT_PREFIXES = (
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.strategies",
    "backend.app.trading_policy",
    "backend.app.backtesting",
    "backend.app.market_forecast",
    "backend.app.market_forecast_worker",
    "backend.app.meta_strategy_training",
    "backend.app.train_market_forecast",
    "frontend",
)

REQUIRED_ARCHITECTURE_STAGES = {
    "market_data_input",
    "finalised_one_minute_bar_events",
    "five_minute_confirmation_data",
    "strategy_evaluation",
    "market_condition_classification",
    "dynamic_settings_resolution",
    "weight_loading",
    "aggregation",
    "local_gates",
    "algorithm_inventory",
    "position_sizing",
    "global_risk_request",
    "paper_order_execution",
    "order_fill_reconciliation",
    "position_lifecycle",
    "trade_closing",
    "performance_attribution",
    "after_market_weight_updates",
    "backtesting_and_replay",
}

REQUIRED_OWNED_MUTABLE_DOMAINS = {
    "strategy_catalogue",
    "strategy_implementations",
    "signal_state",
    "weight_state",
    "configuration",
    "dynamic_profiles",
    "inventory",
    "capital_partition",
    "orders",
    "fills",
    "positions",
    "trades",
    "pnl",
    "backtests",
    "performance_history",
    "execution_attribution",
}

SIBLING_MUTABLE_IMPORT_PREFIXES = (
    "backend.app.algorithms.voting_ensemble",
    "backend.app.algorithms.wca",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.meta_strategy",
    "backend.app.algorithms.session",
    "backend.app.ensemble",
    "backend.app.strategies",
    "backend.app.trading_policy",
    "backend.app.backtesting",
)


class WeightedVotingPackageArchitectureTest(unittest.TestCase):
    def test_requested_package_structure_exists(self) -> None:
        missing = sorted(path for path in EXPECTED_FILES if not (PACKAGE_PATH / path).is_file())

        self.assertEqual(missing, [])

    def test_package_and_all_modules_import_without_cycles(self) -> None:
        package = importlib.import_module(PACKAGE_NAME)
        imported = {package.__name__}

        for module in pkgutil.walk_packages(package.__path__, f"{PACKAGE_NAME}."):
            imported.add(importlib.import_module(module.name).__name__)

        self.assertIn(f"{PACKAGE_NAME}.service", imported)
        self.assertIn(f"{PACKAGE_NAME}.strategies.opening_range_breakout", imported)
        self.assertIn(f"{PACKAGE_NAME}.backtest.engine", imported)

    def test_weighted_voting_does_not_import_other_algorithm_packages(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_forbidden_import(alias.name):
                            violations.append(f"{path.relative_to(PACKAGE_PATH)} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if _is_forbidden_import(node.module):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports from {node.module}")
                    if node.module.startswith("backend.app.algorithms.") and not node.module.startswith(PACKAGE_NAME):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports sibling algorithm {node.module}")

        self.assertEqual(violations, [])

    def test_authoritative_architecture_contract_declares_all_step1_boundaries(self) -> None:
        contract = weighted_voting_architecture_contract()
        stages = {stage.stage_id: stage for stage in contract.pipeline_boundaries}
        ports = {port.port_id: port for port in contract.shared_ports}

        self.assertEqual(contract.algorithm_id, "weighted_voting")
        self.assertEqual(contract.authoritative_runtime, "backend_python")
        self.assertEqual(contract.authoritative_package, PACKAGE_NAME)
        self.assertEqual(contract.decision_kernel, "backend.app.algorithms.weighted_voting.service.WeightedVotingService.evaluate_context")
        self.assertEqual(contract.backtest_kernel, "backend.app.algorithms.weighted_voting.backtest.engine.run_weighted_voting_backtest")
        self.assertEqual(contract.storage_namespace, "weighted_voting.*")
        self.assertEqual(contract.filesystem_root, "data/algorithms/weighted_voting")
        self.assertEqual(contract.capital_partition_id, "weighted_voting.paper.default")
        self.assertFalse(contract.live_money_trading_allowed)
        self.assertFalse(contract.machine_learning_allowed)
        self.assertTrue(contract.fail_closed_on_missing_safety_inputs)
        self.assertTrue(contract.global_risk_decisions_are_external_inputs)
        self.assertIn("background_workers_trigger_one_minute_evaluation_order_submission_exit_and_reconciliation", contract.worker_role)
        self.assertIn("configuration_status_inspection_pause_resume_and_manual_paper_testing_only", contract.api_role)
        self.assertEqual(set(stages), REQUIRED_ARCHITECTURE_STAGES)
        self.assertEqual(set(contract.owned_mutable_domains), REQUIRED_OWNED_MUTABLE_DOMAINS)
        self.assertEqual(contract.broker_account_role, "not_used_for_local_paper; weighted_voting_inventory_is_account_authority")
        self.assertEqual(contract.inventory_owner, "weighted_voting")
        self.assertEqual(contract.supported_modes, ("backtesting", "replay", "shadow_evaluation", "automatic_paper_trading"))
        for port in ports.values():
            with self.subTest(port=port.port_id):
                self.assertFalse(port.mutable_state_allowed)
                self.assertFalse(port.client_request_may_create_global_risk_decision)
        self.assertEqual(ports["global_risk"].provider, "central_risk_service")
        self.assertIn("external_allow_reduce_or_reject_response", ports["global_risk"].access)
        for stage in stages.values():
            with self.subTest(stage=stage.stage_id):
                self.assertTrue(stage.authoritative_module.startswith(PACKAGE_NAME) or stage.authoritative_module == "central_risk_service")
                self.assertTrue(stage.fail_closed_rule)

    def test_dependency_scan_blocks_sibling_algorithm_mutable_imports(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                for module_name in imported:
                    if _is_sibling_mutable_import(module_name):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports mutable sibling surface {module_name}")

        self.assertEqual(violations, [])


def _is_forbidden_import(module_name: str) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _is_sibling_mutable_import(module_name: str) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in SIBLING_MUTABLE_IMPORT_PREFIXES)


if __name__ == "__main__":
    unittest.main()
