from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaRuntimeMode
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
    validate_wca_automatic_paper_account,
)
from backend.app.algorithms.wca.paper_broker import build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent, deterministic_finalized_bar_event_id
from backend.app.algorithms.wca.runtime_supervisor import WCA_RUNTIME_WORKERS
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.tests.test_wca_phase5_final_order_validation import valid_context
from backend.tests.test_wca_step5_production_pipeline import market_snapshot
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


ROOT = Path(__file__).resolve().parents[2]
WCA_API = ROOT / "backend" / "app" / "algorithms" / "wca" / "api.py"
WCA_PACKAGE = ROOT / "backend" / "app" / "algorithms" / "wca"
ALGORITHMS = ROOT / "backend" / "app" / "algorithms"
FRONTEND_WCA = ROOT / "frontend" / "src" / "features" / "wca"

PHASE14_EVIDENCE = {
    "architecture": (
        "test_wca_phase14_safety_isolation.py",
        "test_wca_step15_api_frontend_control_surface.py",
        "test_wca_step16_safety_critical_ci.py",
        "test_wca_step18_frontend_presentation.py",
    ),
    "isolation": (
        "test_wca_phase14_safety_isolation.py",
        "test_wca_step6_inventory_persistence.py",
        "test_wca_phase2_runtime_state.py",
        "test_wca_phase6_alpaca_paper_broker.py",
    ),
    "one_minute_runtime": (
        "test_wca_phase3_finalized_event_publisher.py",
        "test_wca_step7_background_runtime.py",
    ),
    "orders": (
        "test_wca_phase5_final_order_validation.py",
        "test_wca_phase6_alpaca_paper_broker.py",
        "test_wca_phase7_atomic_order_submission.py",
        "test_wca_phase9_position_protection.py",
        "test_wca_step10_paper_broker_outbox.py",
    ),
    "inventory_and_risk": (
        "test_wca_phase2_runtime_state.py",
        "test_wca_phase5_final_order_validation.py",
        "test_wca_step12_global_gate_engine.py",
    ),
    "recovery_and_reconciliation": (
        "test_wca_phase8_broker_reconciliation_recovery.py",
        "test_wca_phase9_position_protection.py",
        "test_wca_phase10_end_of_session.py",
        "test_wca_phase12_latency_health.py",
    ),
    "settings_and_parity": (
        "test_wca_phase11_settings_controls.py",
        "test_wca_phase13_authoritative_parity.py",
        "test_wca_step13_ml_forecast_decoupling.py",
        "test_wca_step8_dynamic_profile.py",
    ),
}

PHASE14_REQUIRED_MARKERS = {
    "Finalised candles only": ("test_wca_phase3_finalized_event_publisher.py", "test_unfinished_candles_are_rejected"),
    "Duplicate-event idempotency": ("test_wca_phase3_finalized_event_publisher.py", "test_replaying_same_event_creates_no_duplicate_decision_or_order"),
    "Missing-bar handling": ("test_wca_phase3_finalized_event_publisher.py", "test_missing_candle_history_blocks_entries"),
    "Out-of-order handling": ("test_wca_phase3_finalized_event_publisher.py", "test_out_of_order_candles_are_handled_deterministically"),
    "Stale-event blocking": ("test_wca_step7_background_runtime.py", "stale"),
    "Restart checkpoint recovery": ("test_wca_phase3_finalized_event_publisher.py", "test_events_remain_processable_after_worker_restart"),
    "Final validation is last": ("test_wca_phase5_final_order_validation.py", "test_final_pre_outbox_validation_runs_after_overrides_and_blocks_reservation"),
    "One intent creates at most one order": ("test_wca_phase7_atomic_order_submission.py", "test_duplicate_reservation_retries_and_multiple_workers_create_one_broker_order"),
    "Unknown submission recovery": ("test_wca_phase7_atomic_order_submission.py", "test_timeout_recovery_requires_reconciliation_instead_of_immediate_resubmit"),
    "Partial-fill handling": ("test_wca_phase9_position_protection.py", "partial"),
    "Rejection handling": ("test_wca_phase6_alpaca_paper_broker.py", "test_rejection_and_partial_fill_are_mapped_to_acknowledgement_contracts"),
    "Cancellation handling": ("test_wca_phase9_position_protection.py", "cancelled"),
    "Client-order-ID lookup": ("test_wca_phase6_alpaca_paper_broker.py", "find_order_by_client_order_id"),
    "Dedicated account enforcement": ("test_wca_phase6_alpaca_paper_broker.py", "test_alpaca_paper_endpoint_and_account_identity_are_enforced"),
    "Paper-endpoint enforcement": ("test_wca_phase6_alpaca_paper_broker.py", "paper_endpoint_required"),
    "Current position is authoritative": ("test_wca_phase2_runtime_state.py", "test_existing_wca_position_is_loaded_before_decision_and_blocks_new_entry"),
    "Daily loss is authoritative": ("test_wca_phase2_runtime_state.py", "test_daily_loss_limits_use_persisted_wca_state"),
    "Daily trade count is authoritative": ("test_wca_phase2_runtime_state.py", "test_daily_trade_limits_use_persisted_wca_state"),
    "Reserved risk is authoritative": ("test_wca_phase9_position_protection.py", "reserved_risk"),
    "Buying power is broker-derived": ("test_wca_phase2_runtime_state.py", "test_buying_power_uses_broker_snapshot"),
    "Global risk is read-only": ("test_wca_step12_global_gate_engine.py", "cannot_rewrite_wca_state"),
    "Entries and exits have separate permission": ("test_wca_phase5_final_order_validation.py", "test_risk_reducing_exit_ignores_entry_only_blocks_and_optional_context"),
    "Startup recovery": ("test_wca_phase8_broker_reconciliation_recovery.py", "test_startup_reconciliation_uses_real_alpaca_adapter_and_clears_startup_entry_gate"),
    "Worker crash": ("test_wca_phase7_atomic_order_submission.py", "crash"),
    "Database restart": ("test_wca_phase8_broker_reconciliation_recovery.py", "test_restart_reconciles_from_every_order_state_without_submission"),
    "Broker timeout": ("test_wca_phase7_atomic_order_submission.py", "timeout"),
    "Missed fill update": ("test_wca_phase8_broker_reconciliation_recovery.py", "partial_fill_not_processed"),
    "Position mismatch": ("test_wca_phase8_broker_reconciliation_recovery.py", "unexpected_account_spy_position"),
    "Orphan order": ("test_wca_phase8_broker_reconciliation_recovery.py", "orphan_protective_order"),
    "Unprotected position": ("test_wca_phase9_position_protection.py", "unprotected"),
    "End-of-session failure": ("test_wca_phase10_end_of_session.py", "flatten_rejected"),
    "Baseline is immutable": ("test_wca_phase11_settings_controls.py", "baseline"),
    "Overlays are bounded": ("test_wca_phase11_settings_controls.py", "overlay"),
    "No risk increase from initial overlays": ("test_wca_phase11_settings_controls.py", "risk"),
    "Configuration versions are reproducible": ("test_wca_phase11_settings_controls.py", "configuration"),
    "Replay and paper decisions match": ("test_wca_phase13_authoritative_parity.py", "zero_unexplained_decision_mismatches"),
    "WCA does not depend on ML": ("test_wca_step13_ml_forecast_decoupling.py", "ml"),
}


def test_phase14_evidence_matrix_names_every_safety_area_and_existing_test_file() -> None:
    expected_sections = {
        "architecture",
        "isolation",
        "one_minute_runtime",
        "orders",
        "inventory_and_risk",
        "recovery_and_reconciliation",
        "settings_and_parity",
    }

    assert set(PHASE14_EVIDENCE) == expected_sections
    for files in PHASE14_EVIDENCE.values():
        for file_name in files:
            path = ROOT / "backend" / "tests" / file_name
            assert path.exists(), file_name
            assert "test_" in path.read_text(encoding="utf-8")


def test_phase14_required_markers_are_backed_by_focused_tests() -> None:
    missing = []
    for requirement, (file_name, marker) in PHASE14_REQUIRED_MARKERS.items():
        source = (ROOT / "backend" / "tests" / file_name).read_text(encoding="utf-8")
        if marker not in source:
            missing.append(f"{requirement}: {file_name} missing {marker}")

    assert missing == []


def test_api_paths_are_enqueue_and_read_boundaries_only() -> None:
    source = WCA_API.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WCA_API))
    forbidden_imports = {
        "backend.app.algorithms.wca.execution_pipeline",
        "backend.app.algorithms.wca.backtest.engine",
        "backend.app.algorithms.wca.paper_broker",
        "backend.app.algorithms.wca.alpaca_paper_broker",
        "backend.app.algorithms.wca.strategies.primary_voters",
    }
    forbidden_runtime_calls = {
        "run_wca_execution_pipeline",
        "run_wca_paper_pipeline_adapter",
        "run_wca_backtest",
        "run_wca_backtest_modes",
        "submit_order",
        "process_next_outbox",
        "evaluate_all_modifiers",
        "aggregate_wca",
    }
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in forbidden_imports:
            violations.append(f"imports heavy module {node.module}")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in forbidden_runtime_calls:
                violations.append(f"calls {name}")

    assert violations == []
    assert "enqueue_backtest" in source
    assert "enqueue_paper_command" in source
    assert "enqueue_reconciliation_request" in source


def test_background_workers_own_heavy_wca_processing() -> None:
    runtime_source = (WCA_PACKAGE / "runtime_supervisor.py").read_text(encoding="utf-8")
    research_source = (WCA_PACKAGE / "research_worker.py").read_text(encoding="utf-8")

    assert "run_wca_paper_pipeline_adapter" in runtime_source
    assert "load_wca_authoritative_runtime_state" in runtime_source
    assert "execution_outbox_worker" in WCA_RUNTIME_WORKERS
    assert "broker_reconciliation_worker" in WCA_RUNTIME_WORKERS
    assert "end_of_session_worker" in WCA_RUNTIME_WORKERS
    assert "self.service.run_backtest(" in research_source
    assert "WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS = True" in research_source


def test_frontend_wca_package_is_presentation_and_transport_only() -> None:
    forbidden = (
        "strategyEnsembleSignals",
        "backtestConfidenceAggregation",
        "submitOpenOrder",
        "submitSelectedOpenOrder",
        "localStorage",
        "WCA_PRIMARY_VOTERS",
        "run_wca_",
        "alpaca",
    )
    violations: list[str] = []
    for path in FRONTEND_WCA.rglob("*.ts"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                violations.append(f"{path}: {token}")

    api_source = (FRONTEND_WCA / "api.ts").read_text(encoding="utf-8")
    assert violations == []
    assert "/api/wca/status" in api_source
    assert "/api/wca/backtests" in api_source


def test_wca_inventory_settings_weights_and_credentials_are_algorithm_isolated() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    snapshot = market_snapshot()
    configuration = default_wca_configuration()
    weights = baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="phase14.weights.v1")
    repository.initialize_defaults(
        symbol="SPY",
        configuration=configuration.model_dump(mode="json"),
        weight_snapshot=weights,
        engine_version="phase14",
    )

    with pytest.raises(ValueError, match="algorithm_id='wca'"):
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id="phase14-foreign-inventory",
                event_type="FILL_RECEIVED",
                algorithm_id="weighted_voting",
                broker_account_id="paper",
                symbol="SPY",
                event_timestamp=snapshot.decision_timestamp.isoformat(),
                trade_date=snapshot.decision_timestamp.date().isoformat(),
                fill_id="phase14-foreign-fill",
                side="BUY",
                quantity=1,
                filled_quantity=1,
                fill_price=100.0,
            )
        )
    with pytest.raises(ValueError, match="algorithm_id='wca'"):
        repository.read_inventory_projection(algorithm_id="weighted_voting", broker_account_id="paper", symbol="SPY")
    assert _cross_algorithm_inventory_mutation_count(repository) == 0

    with sqlite3.connect(repository.path) as conn:
        for statement, parameters in _foreign_settings_and_weights_rows(configuration, weights):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement, parameters)

    assert repository.read_active_configuration().configuration_version == configuration.configuration_version
    assert repository.read_active_weights(as_of=snapshot.decision_timestamp).weight_version == "phase14.weights.v1"

    shared = validate_wca_automatic_paper_account(
        account_id="wca-paper",
        environ={
            WCA_AUTOMATIC_PAPER_ENABLED: "true",
            WCA_ALPACA_PAPER_API_KEY_ID: "shared-key",
            WCA_ALPACA_PAPER_API_SECRET_KEY: "shared-secret",
            WCA_ALPACA_PAPER_ACCOUNT_ID: "wca-paper",
            WCA_ALPACA_PAPER_BASE_URL: WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
            "APCA_API_KEY_ID": "shared-key",
            "APCA_API_SECRET_KEY": "shared-secret",
        },
    )
    assert shared.verified is False
    assert "wca.paper_account.shared_alpaca_credentials_rejected" in shared.reason_codes


def test_wca_orders_always_use_wca_account_identity_and_final_validation_marker() -> None:
    decision = decision_with_order("phase14-order-decision", "phase14-order-intent", "phase14-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": "phase14-wca-paper"})
    decision = decision.model_copy(update={"proposed_order": proposed})
    request = build_wca_paper_broker_request(proposed)
    context = valid_context(decision, runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER)
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")

    reservation = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="phase14-order-run",
        account_id="phase14-wca-paper",
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=context,
    )

    assert reservation.created is True
    assert request.account_id == "phase14-wca-paper"
    assert request.client_order_id.startswith("wca-phase14-wca-paper-")
    assert reservation.proposed_order.algorithm_id == WCA_ALGORITHM_ID
    assert "wca.order_validation.final_pre_outbox.passed" in reservation.proposed_order.reason_codes


def test_finalized_bar_contract_blocks_partial_foreign_and_future_runtime_events() -> None:
    snapshot = market_snapshot()
    event_id = deterministic_finalized_bar_event_id(
        symbol="SPY",
        timeframe="1Min",
        candle_timestamp=snapshot.data_timestamp,
        source="phase14-feed",
    )
    event = WcaFinalizedBarEvent(
        event_id=event_id,
        symbol="SPY",
        finalized_candle_timestamp=snapshot.data_timestamp,
        data_manifest_hash="phase14-manifest",
        publication_timestamp=snapshot.data_timestamp,
        source="phase14-feed",
        snapshot=snapshot,
    )

    assert event.event_id == event_id
    assert event.algorithm_id == WCA_ALGORITHM_ID
    assert event.timeframe == "1Min"
    assert event.data_readiness_result == "READY"

    with pytest.raises(ValueError, match="incomplete"):
        WcaFinalizedBarEvent(
            event_id="phase14-partial",
            symbol="SPY",
            finalized_candle_timestamp=snapshot.data_timestamp,
            data_manifest_hash="phase14-manifest",
            publication_timestamp=snapshot.data_timestamp,
            source="phase14-feed",
            snapshot=snapshot,
            is_finalized=False,
        )
    with pytest.raises(ValueError, match="WCA scoped"):
        WcaFinalizedBarEvent(
            algorithm_id="weighted_voting",
            event_id="phase14-foreign",
            symbol="SPY",
            finalized_candle_timestamp=snapshot.data_timestamp,
            data_manifest_hash="phase14-manifest",
            publication_timestamp=snapshot.data_timestamp,
            source="phase14-feed",
            snapshot=snapshot,
        )


def test_sibling_algorithm_packages_do_not_mutate_wca_inventory_or_credentials() -> None:
    forbidden = (
        "wca_inventory_ledger",
        "wca_inventory_projection",
        "wca_daily_state",
        "WCA_ALPACA_PAPER_API_SECRET_KEY",
        "WCA_ALPACA_PAPER_ACCOUNT_ID",
        "apply_fill_and_update_position",
        "record_inventory_event(",
    )
    violations: list[str] = []
    for path in ALGORITHMS.rglob("*.py"):
        if WCA_PACKAGE in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                violations.append(f"{path}: {token}")

    assert violations == []


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _cross_algorithm_inventory_mutation_count(repository: WcaSqliteRepository) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM wca_inventory_ledger WHERE algorithm_id <> 'wca'").fetchone()[0])


def _foreign_settings_and_weights_rows(configuration, weights):
    return (
        (
            """
            INSERT INTO wca_active_configuration (
                algorithm_id, configuration_version, activated_at, content_hash, payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "weighted_voting",
                "foreign-config",
                configuration.activation_timestamp.isoformat(),
                "foreign-hash",
                configuration.model_dump_json(),
            ),
        ),
        (
            """
            INSERT INTO wca_weight_snapshots (
                weight_version, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "foreign-weights",
                "weighted_voting",
                "SPY",
                weights.created_at.isoformat(),
                "foreign-config",
                "phase14",
                "foreign-market",
                "foreign-decision",
                "foreign-run",
                weights.model_dump_json(),
            ),
        ),
    )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-phase14-{uuid4().hex}.sqlite"
