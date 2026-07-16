from __future__ import annotations

import ast
import importlib
import json
import pkgutil
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.algorithms.wca.contracts import (
    BacktestResult,
    BacktestRunConfiguration,
    GlobalGateResult,
    ProposedOrder,
    WcaAggregationResult,
    WcaBaselineSettings,
    WcaCandle,
    WcaDecision,
    WcaEvaluationStatus,
    WcaGateStatus,
    WcaLocalGateResult,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaSide,
    WcaSizingResult,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.main import app


ROOT = Path(__file__).parents[2]
WCA_PACKAGE = "backend.app.algorithms.wca"
WCA_PATH = ROOT / "backend" / "app" / "algorithms" / "wca"
RISK_PATH = ROOT / "backend" / "app" / "risk"
EXECUTION_PATH = ROOT / "backend" / "app" / "execution"

EXPECTED_WCA_FILES = {
    "__init__.py",
    "contracts.py",
    "configuration.py",
    "strategy_registry.py",
    "market_snapshot.py",
    "market_status.py",
    "confidence.py",
    "weights.py",
    "aggregation.py",
    "dynamic_profile.py",
    "local_gates.py",
    "sizing.py",
    "exits.py",
    "engine.py",
    "service.py",
    "repository.py",
    "api.py",
    "strategies/__init__.py",
    "strategies/base.py",
    "modifiers/__init__.py",
    "modifiers/base.py",
    "backtest/__init__.py",
    "backtest/engine.py",
    "backtest/execution.py",
    "backtest/ledger.py",
    "backtest/metrics.py",
    "backtest/walk_forward.py",
    "backtest/reports.py",
}

EXPECTED_RISK_FILES = {
    "__init__.py",
    "global_gate_engine.py",
    "account_risk_ledger.py",
    "gate_contracts.py",
    "exposure.py",
}

EXPECTED_EXECUTION_FILES = {
    "order_contracts.py",
    "order_validator.py",
    "idempotency.py",
    "reconciliation.py",
    "broker_adapter.py",
}

FORBIDDEN_WCA_IMPORTS = (
    "frontend",
    "backend.app.algorithms.weighted_voting",
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.market_forecast",
    "backend.app.meta_strategy_training",
    "backend.app.strategies",
    "backend.app.trading_policy",
)

FORBIDDEN_STRATEGY_IMPORT_TERMS = ("broker", "database", "alpaca", "repository")


class WcaStep1BackendStructureTest(unittest.TestCase):
    def test_requested_module_structure_exists(self) -> None:
        missing_wca = sorted(path for path in EXPECTED_WCA_FILES if not (WCA_PATH / path).is_file())
        missing_risk = sorted(path for path in EXPECTED_RISK_FILES if not (RISK_PATH / path).is_file())
        missing_execution = sorted(path for path in EXPECTED_EXECUTION_FILES if not (EXECUTION_PATH / path).is_file())

        self.assertEqual(missing_wca, [])
        self.assertEqual(missing_risk, [])
        self.assertEqual(missing_execution, [])

    def test_wca_modules_import_without_cycles(self) -> None:
        package = importlib.import_module(WCA_PACKAGE)
        imported = {package.__name__}

        for module in pkgutil.walk_packages(package.__path__, f"{WCA_PACKAGE}."):
            imported.add(importlib.import_module(module.name).__name__)

        self.assertIn(f"{WCA_PACKAGE}.contracts", imported)
        self.assertIn(f"{WCA_PACKAGE}.strategies.base", imported)
        self.assertIn(f"{WCA_PACKAGE}.backtest.engine", imported)

    def test_wca_modules_do_not_import_frontend_or_other_algorithms(self) -> None:
        violations: list[str] = []
        for path in sorted(WCA_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module_names: list[str] = []
                if isinstance(node, ast.Import):
                    module_names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module_names = [node.module]
                for module_name in module_names:
                    if _matches_prefix(module_name, FORBIDDEN_WCA_IMPORTS):
                        violations.append(f"{path.relative_to(WCA_PATH)} imports {module_name}")
                    if module_name.startswith("backend.app.algorithms.") and not module_name.startswith(WCA_PACKAGE):
                        violations.append(f"{path.relative_to(WCA_PATH)} imports sibling algorithm {module_name}")

        self.assertEqual(violations, [])

    def test_strategy_interfaces_contain_no_broker_or_database_calls(self) -> None:
        violations: list[str] = []
        for path in sorted((WCA_PATH / "strategies").rglob("*.py")):
            source = path.read_text(encoding="utf-8").lower()
            for term in FORBIDDEN_STRATEGY_IMPORT_TERMS:
                if term in source:
                    violations.append(f"{path.relative_to(WCA_PATH)} contains {term}")

        self.assertEqual(violations, [])

    def test_contracts_serialize_deterministically_and_validate_statuses(self) -> None:
        snapshot = sample_market_snapshot()
        strategy = WcaStrategyEvaluation(
            strategy_id="C1",
            name="Moving Average Trend",
            status=WcaEvaluationStatus.ACTIVE,
            signal=WcaSide.BUY,
            confidence=0.75,
            base_weight=0.11,
            effective_weight=0.11,
            contribution=0.0825,
            reason_codes=("wca.strategy.fixture",),
        )
        aggregation = WcaAggregationResult(
            signal=WcaSide.BUY,
            decision_label="Buy",
            buy_score=0.0825,
            sell_score=0,
            net_score=0.0825,
            active_weight=0.11,
            normalized_net_score=0.75,
            active_strategy_count=1,
            buy_agreement=1,
            sell_agreement=0,
            buy_average_confidence=0.75,
            sell_average_confidence=0,
            strategy_evaluations=(strategy,),
        )
        sizing = WcaSizingResult(
            final_quantity=10,
            risk_dollars=25,
            stop_distance=1,
            shares_by_risk=25,
            shares_by_order=10,
            shares_by_capital=10,
            shares_by_buying_power=10,
            shares_by_liquidity=100,
            limiting_factor="order limit",
        )
        order = ProposedOrder(
            decision_id="decision-1",
            order_intent_id="intent-1",
            symbol="SPY",
            side=WcaSide.BUY,
            quantity=10,
            limit_price=501,
        )
        decision = WcaDecision(
            decision_id="decision-1",
            configuration_version="config-v1",
            weight_version="weights-v1",
            data_timestamp=snapshot.data_timestamp,
            decision_timestamp=snapshot.decision_timestamp,
            market_snapshot=snapshot,
            market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
            aggregation=aggregation,
            local_gates=(WcaLocalGateResult(gate_id="fixture", status=WcaGateStatus.PASS, blocks_entry=False),),
            sizing=sizing,
            proposed_order=order,
        )

        first = decision.deterministic_json()
        second = decision.deterministic_json()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["algorithm_id"], "wca")
        baseline = WcaBaselineSettings()
        self.assertEqual(baseline.deterministic_json(), baseline.deterministic_json())
        with self.assertRaises(ValidationError):
            WcaStrategyEvaluation(
                strategy_id="C0",
                name="Not applicable directional",
                status=WcaEvaluationStatus.NOT_APPLICABLE,
                signal=WcaSide.BUY,
                confidence=0,
                base_weight=0,
                effective_weight=0,
                contribution=0,
            )
        with self.assertRaises(ValidationError):
            WcaWeightSnapshot(weights={"C1": 0.6, "C2": 0.3})
        with self.assertRaises(ValidationError):
            GlobalGateResult(status=WcaGateStatus.PASS, proposed_quantity=1, allowed_quantity=2)

    def test_openapi_schemas_are_generated_successfully(self) -> None:
        client = TestClient(app)
        response = client.get("/openapi.json")

        self.assertEqual(response.status_code, 200, response.text)
        schemas = response.json()["components"]["schemas"]
        paths = response.json()["paths"]
        self.assertIn("WcaDecision", schemas)
        self.assertIn("WcaMarketSnapshot", schemas)
        self.assertIn("WcaStrategyEvaluation", schemas)
        self.assertIn("/api/wca/status", paths)
        self.assertIn("/api/wca/config/baseline", paths)


def sample_market_snapshot() -> WcaMarketSnapshot:
    timestamp = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        candles=(
            WcaCandle(
                timestamp=timestamp,
                open=500,
                high=501,
                low=499,
                close=500.5,
                volume=100000,
            ),
        ),
    )


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


if __name__ == "__main__":
    unittest.main()
