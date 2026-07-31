from __future__ import annotations

import ast
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase20"

UNIT_COVERAGE = {
    "indicator": ("backend/tests/regime/classification/test_indicator_inventory.py",),
    "classifier_axis": (
        "backend/tests/regime/classification/test_direction_axis.py",
        "backend/tests/regime/classification/test_volatility_axis.py",
        "backend/tests/regime/classification/test_structure_axis.py",
        "backend/tests/regime/classification/test_liquidity_axis.py",
        "backend/tests/regime/classification/test_session_axis.py",
        "backend/tests/regime/classification/test_event_risk_axis.py",
    ),
    "regime_state": (
        "backend/tests/regime/classification/test_composite_regimes.py",
        "backend/tests/regime/classification/test_golden_regime_patterns.py",
    ),
    "transition": ("backend/tests/regime/transitions/test_phase6_persistent_hysteresis.py",),
    "hysteresis_rule": (
        "backend/tests/regime/transitions/test_confirmation_bars.py",
        "backend/tests/regime/transitions/test_hysteresis.py",
        "backend/tests/regime/transitions/test_immediate_transition.py",
        "backend/tests/regime/transitions/test_minimum_dwell.py",
        "backend/tests/regime/transitions/test_regime_oscillation.py",
    ),
    "dynamic_profile": ("backend/tests/regime/profiles/test_dynamic_profile_matrix.py",),
    "strategy": ("backend/tests/regime/strategies/directional/test_phase7_strategy_contract.py",),
    "confirmation": ("backend/tests/regime/decision/test_phase9_aggregation_layers.py",),
    "safety_gate": ("backend/tests/regime/strategies/safety/test_cash_avoid_filter.py",),
    "aggregation_rule": (
        "backend/tests/regime/decision/test_family_aggregation.py",
        "backend/tests/regime/decision/test_phase9_aggregation_layers.py",
    ),
    "cost_gate": ("backend/tests/regime/decision/test_phase10_execution_cost_gate.py",),
    "sizing_rule": ("backend/tests/regime/decision/test_phase11_sizing_local_risk.py",),
    "inventory_transition": ("backend/tests/regime/test_phase1_inventory_ledger.py",),
    "order_validation_rule": ("backend/tests/regime/execution/test_order_validation.py",),
    "execution_state_transition": ("backend/tests/test_regime_step11_execution_outbox.py",),
    "reconciliation_path": ("backend/tests/regime/test_phase15_broker_reconciliation.py",),
    "exit_rule": (
        "backend/tests/regime/trade_management/test_exit_policy.py",
        "backend/tests/regime/test_phase14_trade_management_exits.py",
    ),
}

INTEGRATION_COVERAGE = {
    "finalized_one_minute_spy_bar_triggers_background_processing": ("backend/tests/regime/test_phase3_event_runtime.py",),
    "partial_candle_does_not_trigger_processing": ("backend/tests/regime/test_phase3_event_runtime.py",),
    "duplicate_bar_creates_one_decision_and_no_duplicate_order": (
        "backend/tests/regime/test_phase3_event_runtime.py",
        "backend/tests/regime/test_persistence_isolation_boundary.py",
    ),
    "out_of_order_bar_does_not_corrupt_current_state": (
        "backend/tests/regime/test_phase3_event_runtime.py",
        "backend/tests/regime/transitions/test_phase6_persistent_hysteresis.py",
    ),
    "restart_preserves_hysteresis_and_inventory": (
        "backend/tests/regime/transitions/test_phase6_persistent_hysteresis.py",
        "backend/tests/regime/test_phase1_inventory_ledger.py",
    ),
    "caller_state_cannot_influence_authoritative_decision": (
        "backend/tests/regime/test_phase16_api_control_plane.py",
        "backend/tests/regime/test_phase2_settings_repository.py",
    ),
    "another_algorithm_cannot_mutate_regime_state": (
        "backend/tests/regime/test_phase1_inventory_ledger.py",
        "backend/tests/regime/test_persistence_isolation_boundary.py",
    ),
    "regime_cannot_mutate_another_algorithm_inventory": ("backend/tests/regime/test_persistence_isolation_boundary.py",),
    "shadow_strategies_cannot_affect_order": ("backend/tests/regime/strategies/test_phase8_registry_routing.py",),
    "confirmation_strategies_cannot_invent_direction": ("backend/tests/regime/decision/test_phase9_aggregation_layers.py",),
    "aliases_cannot_create_duplicate_votes": ("backend/tests/regime/strategies/test_phase8_registry_routing.py",),
    "insufficient_warmup_produces_hold": ("backend/tests/regime/test_phase5_classifier_production_safety.py",),
    "stale_data_produces_hold": ("backend/tests/regime/test_phase4_market_data_validation.py",),
    "unknown_regime_produces_hold": ("backend/tests/regime/classification/test_composite_regimes.py",),
    "event_risk_and_liquidity_stress_block_entries": (
        "backend/tests/regime/classification/test_golden_regime_patterns.py",
        "backend/tests/regime/strategies/safety/test_event_blackout.py",
        "backend/tests/regime/strategies/safety/test_insufficient_liquidity.py",
    ),
    "negative_net_expected_edge_blocks_entry": ("backend/tests/regime/decision/test_phase10_execution_cost_gate.py",),
    "missing_trusted_account_data_blocks_entry": ("backend/tests/regime/decision/test_phase11_sizing_local_risk.py",),
    "global_risk_can_reduce_or_reject_without_rewriting_signal": ("backend/tests/regime/execution/test_global_risk_boundary.py",),
    "broker_retry_does_not_duplicate_order": ("backend/tests/test_regime_step11_execution_outbox.py",),
    "partial_fills_update_only_filled_quantity": (
        "backend/tests/regime/test_phase1_inventory_ledger.py",
        "backend/tests/test_regime_step11_execution_outbox.py",
    ),
    "cancelled_quantities_are_released": ("backend/tests/regime/test_phase14_trade_management_exits.py",),
    "startup_reconciliation_detects_mismatches": ("backend/tests/regime/test_phase15_broker_reconciliation.py",),
    "live_broker_configuration_rejected_in_paper_only_mode": ("backend/tests/test_regime_step11_execution_outbox.py",),
    "filled_entry_receives_protective_exit_handling": ("backend/tests/regime/test_phase14_trade_management_exits.py",),
    "end_of_day_flatten_closes_regime_position": ("backend/tests/regime/test_phase14_trade_management_exits.py",),
    "backtest_and_paper_replay_produce_identical_decisions": (
        "backend/tests/regime/backtest/test_phase17_replay_paper_parity.py",
        "backend/tests/regime/execution/test_runtime_parity.py",
    ),
    "no_test_requires_real_broker_credentials": ("backend/tests/regime/execution/test_broker_boundary.py",),
}

DATABASE_COVERAGE = {
    "unique_idempotency_constraints": ("backend/tests/regime/test_persistence_isolation_boundary.py",),
    "algorithm_ownership_checks": ("backend/tests/regime/test_phase1_inventory_ledger.py",),
    "inventory_concurrency": ("backend/tests/regime/test_phase20_focused_tests.py",),
    "transaction_rollback": ("backend/tests/regime/test_phase20_focused_tests.py",),
    "outbox_atomicity": (
        "backend/tests/regime/test_persistence_isolation_boundary.py",
        "backend/tests/regime/test_phase20_focused_tests.py",
    ),
    "immutable_settings_versions": (
        "backend/tests/regime/test_phase2_settings_repository.py",
        "backend/tests/regime/test_phase20_focused_tests.py",
    ),
}


def test_phase20_unit_coverage_points_to_focused_executable_tests() -> None:
    _assert_coverage_paths_have_tests(UNIT_COVERAGE)


def test_phase20_integration_coverage_points_to_regression_tests() -> None:
    _assert_coverage_paths_have_tests(INTEGRATION_COVERAGE)


def test_phase20_database_coverage_points_to_integrity_tests() -> None:
    _assert_coverage_paths_have_tests(DATABASE_COVERAGE)


def test_phase20_every_registered_strategy_keeps_a_focused_behavioral_test() -> None:
    manifest = json.loads((ROOT / "backend/tests/regime/coverage_manifest.json").read_text(encoding="utf-8"))
    entries = {
        component["component_id"]: component
        for component in manifest["components"]
        if component["component_type"] in {"directional_strategy", "confirmation_module", "context_module", "safety_gate"}
    }

    assert set(entries) == {definition.strategy_id for definition in REGIME_STRATEGY_DEFINITIONS}
    for definition in REGIME_STRATEGY_DEFINITIONS:
        test_path = ROOT / entries[definition.strategy_id]["focused_test_path"]
        test_names = _test_names(test_path)
        source = test_path.read_text(encoding="utf-8")

        assert test_names, entries[definition.strategy_id]["focused_test_path"]
        assert definition.strategy_id in source
        assert "evaluate_strategy" in source or "assert_" in source


def test_phase20_duplicate_fill_from_two_workers_updates_inventory_once() -> None:
    path = _temp_db_path()
    repository = RegimeRepository(f"sqlite:///{path}")
    identity = _identity()
    fill = _fill(identity, "phase20-fill-1", "Buy", 5, 100.25)

    def apply() -> dict:
        return repository.apply_inventory_fill(identity, fill)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply(), range(2)))

    snapshot = RegimeRepository(f"sqlite:///{path}").current_inventory_snapshot(identity)
    updated_count = sum(1 for result in results if result.get("updated") is True)
    duplicate_count = sum(1 for result in results if result.get("duplicate") is True)

    assert updated_count == 1
    assert duplicate_count == 1
    assert snapshot["quantity"] == 5
    assert _table_count(path, "regime_inventory_events") == 1


def test_phase20_sql_transaction_rollback_leaves_no_partial_intent_or_outbox() -> None:
    path = _temp_db_path()
    RegimeRepository(f"sqlite:///{path}")

    with sqlite3.connect(path) as conn:
        try:
            conn.execute("BEGIN")
            _insert_raw_record(conn, "regime_order_intents", "phase20-intent-row", "regime", "decision-rollback", order_intent_id="intent-rollback")
            _insert_raw_record(conn, "regime_execution_outbox", "phase20-bad-outbox", "weighted_voting", "decision-rollback", order_intent_id="intent-rollback")
        except sqlite3.IntegrityError:
            conn.rollback()
        else:  # pragma: no cover - a CHECK constraint should always reject this.
            raise AssertionError("cross-algorithm outbox row did not fail")

    assert _table_count(path, "regime_order_intents") == 0
    assert _table_count(path, "regime_execution_outbox") == 0


def test_phase20_database_unique_indexes_reject_duplicate_authoritative_keys() -> None:
    path = _temp_db_path()
    RegimeRepository(f"sqlite:///{path}")

    with sqlite3.connect(path) as conn:
        _insert_raw_record(conn, "regime_decisions", "phase20-decision-a", "regime", "decision-unique")
        _insert_raw_record(conn, "regime_order_intents", "phase20-intent-a", "regime", "decision-intent", order_intent_id="intent-unique")
        _insert_raw_record(conn, "regime_execution_outbox", "phase20-outbox-a", "regime", "decision-outbox", order_intent_id="intent-outbox", processing_status="created")
        conn.commit()

        with _raises(sqlite3.IntegrityError):
            _insert_raw_record(conn, "regime_decisions", "phase20-decision-b", "regime", "decision-unique")
        with _raises(sqlite3.IntegrityError):
            _insert_raw_record(conn, "regime_order_intents", "phase20-intent-b", "regime", "decision-intent-b", order_intent_id="intent-unique")
        with _raises(sqlite3.IntegrityError):
            _insert_raw_record(conn, "regime_execution_outbox", "phase20-outbox-b", "regime", "decision-outbox-b", order_intent_id="intent-outbox", processing_status="created")


def test_phase20_settings_version_snapshot_is_not_mutated_by_caller_copy() -> None:
    repository, identity = _repository()
    service = RegimeApplicationService(repository=repository)
    created = service.handle_settings_command(
        {
            "commandType": "create_version",
            "actor": "phase20-test",
            "source": "phase20",
            "reason": "immutability-check",
            "settings": {"identity": identity, "positionSizing": {"baseRiskPercent": 0.03}},
        }
    )
    returned_snapshot = created["settingsSnapshot"]
    returned_snapshot["baselineSettings"]["baseRiskPercent"] = 99.0
    returned_snapshot["reasonForActivationOrRollback"] = "caller-mutated-copy"

    persisted = repository.settings_version_snapshot(identity, created["settingsVersion"])

    assert persisted is not None
    assert persisted["baselineSettings"]["baseRiskPercent"] == 0.10
    assert persisted["positionSizing"]["baseRiskPercent"] == 0.03
    assert persisted["reasonForActivationOrRollback"] == "immutability-check"


def _assert_coverage_paths_have_tests(coverage: dict[str, tuple[str, ...]]) -> None:
    assert coverage
    for item, relative_paths in coverage.items():
        assert relative_paths, item
        for relative_path in relative_paths:
            path = ROOT / relative_path
            assert path.exists(), f"{item}: {relative_path}"
            assert _test_names(path), f"{item}: {relative_path} has no test functions"


def _test_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.endswith("Test"):
            names.append(node.name)
    return names


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    identity = _identity()
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    return RegimeRepository(f"sqlite:///{path}"), identity


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": f"phase20-{uuid4().hex[:8]}",
        "accountId": "paper-account-phase20",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _fill(identity: dict[str, str], fill_id: str, side: str, quantity: int, price: float) -> dict[str, object]:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": "phase20-decision-1",
        "orderIntentId": "phase20-intent-1",
        "brokerOrderId": "phase20-broker-1",
        "fillId": fill_id,
        "side": side,
        "filledQuantity": quantity,
        "averageFillPrice": price,
        "filledAt": "2026-07-23T15:30:00Z",
    }


def _temp_db_path() -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"


def _table_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _insert_raw_record(
    conn: sqlite3.Connection,
    table: str,
    record_id: str,
    algorithm_id: str,
    decision_id: str,
    *,
    order_intent_id: str | None = None,
    processing_status: str = "recorded",
) -> None:
    payload = {
        "algorithmId": algorithm_id,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
    }
    conn.execute(
        f"""
        INSERT INTO {table} (
            record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
            algorithm_version, settings_version, strategy_version, profile_version,
            model_version, timestamp, event_timestamp, symbol, data_timestamp,
            decision_id, order_id, order_intent_id, broker_order_id, position_id,
            trade_id, processing_status, sequence_version, payload_json
        )
        VALUES (?, ?, 'phase20-db', 'paper-account-phase20', 'paper',
                'regime_algorithm_v3_backend_authoritative', 'settings-phase20',
                'strategy-catalog-phase20', 'profile-phase20', NULL,
                '2026-07-23T15:30:00Z', '2026-07-23T15:30:00Z', 'SPY',
                '2026-07-23T15:30:00Z', ?, NULL, ?, NULL, NULL, NULL, ?, 1, ?)
        """,
        (record_id, algorithm_id, decision_id, order_intent_id, processing_status, json.dumps(payload, sort_keys=True)),
    )


class _raises:
    def __init__(self, expected: type[BaseException]) -> None:
        self.expected = expected

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: object) -> bool:
        if exc_type is None:
            raise AssertionError(f"Expected {self.expected.__name__}")
        return issubclass(exc_type, self.expected)
