from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.persistence import REGIME_OWNED_TABLES, RegimeSqliteRepository


EXPECTED_REQUIRED_STEP2_TABLES = (
    "regime_settings_versions",
    "regime_active_settings",
    "regime_strategy_settings",
    "regime_runtime_instances",
    "regime_runtime_commands",
    "regime_runtime_events",
    "regime_runtime_checkpoints",
    "regime_hysteresis_state",
    "regime_daily_counters",
    "regime_strategy_performance",
    "regime_decisions",
    "regime_classifications",
    "regime_transitions",
    "regime_strategy_outputs",
    "regime_context_outputs",
    "regime_confirmation_outputs",
    "regime_safety_results",
    "regime_family_scores",
    "regime_effective_profiles",
    "regime_local_risk_results",
    "regime_order_intents",
    "regime_execution_outbox",
    "regime_orders",
    "regime_fills",
    "regime_hypothetical_fills",
    "regime_positions",
    "regime_trades",
    "regime_reconciliation_events",
    "regime_backtest_jobs",
    "regime_backtest_runs",
    "regime_backtest_trades",
)


def test_regime_owned_inventory_tables_constraints_and_indexes() -> None:
    path = _temp_db_path()
    repository = RegimeSqliteRepository(f"sqlite:///{path}")

    assert REGIME_OWNED_TABLES[: len(EXPECTED_REQUIRED_STEP2_TABLES)] == EXPECTED_REQUIRED_STEP2_TABLES
    with sqlite3.connect(path) as conn:
        for table in EXPECTED_REQUIRED_STEP2_TABLES:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert {"algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol", "sequence_version"}.issubset(columns)
            indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}
            assert f"idx_{table}_instance_symbol" in indexes
            assert f"idx_{table}_decision" in indexes
            assert f"idx_{table}_order_intent" in indexes
            assert f"idx_{table}_settings_version" in indexes

        with pytest_raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO regime_runtime_events (
                    record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
                    algorithm_version, settings_version, strategy_version, profile_version,
                    timestamp, event_timestamp, symbol, data_timestamp, decision_id,
                    processing_status, sequence_version, payload_json
                )
                VALUES ('bad-algo', 'weighted_voting', 'regime-a', 'paper-a', 'paper',
                        'v', 's', 'c', 'p', '', '', 'SPY', '', 'event-a', 'recorded', 1, '{}')
                """
            )


def test_cross_algorithm_and_incomplete_ownership_reads_fail_closed() -> None:
    repository = RegimeSqliteRepository(f"sqlite:///{_temp_db_path()}")

    with pytest_raises(ValueError):
        repository.read_owned_records("broker_orders", _identity())
    with pytest_raises(ValueError):
        repository.read_owned_records("regime_runtime_events", {**_identity(), "algorithmId": "wca"})
    with pytest_raises(ValueError):
        repository.read_owned_records("regime_runtime_events", {"algorithmId": "regime", "symbol": "SPY"})


def test_duplicate_decision_order_intent_and_outbox_are_idempotent() -> None:
    repository = RegimeSqliteRepository(f"sqlite:///{_temp_db_path()}")
    snapshot = _decision_snapshot("decision-1")

    first_decision = repository.record_decision_snapshot(snapshot)
    duplicate_decision = repository.record_decision_snapshot(snapshot)
    assert first_decision["recorded"] is True
    assert duplicate_decision["recorded"] is False
    assert duplicate_decision["reason"] == "duplicate_decision"

    intent = {**_identity(), "decisionId": "decision-2", "orderIntentId": "intent-2", "side": "Buy", "quantity": 3}
    first_intent = repository.insert_order_intent(intent)
    duplicate_intent = repository.insert_order_intent(intent)
    assert first_intent["inserted"] is True
    assert duplicate_intent["inserted"] is False
    assert duplicate_intent["reason"] == "duplicate_order_intent"

    outbox = {**_identity(), "decisionId": "decision-3", "orderIntentId": "intent-3", "side": "Buy", "quantity": 1}
    first_outbox = repository.insert_execution_outbox_record(_identity(), outbox)
    duplicate_outbox = repository.insert_execution_outbox_record(_identity(), outbox)
    assert first_outbox["inserted"] is True
    assert duplicate_outbox["inserted"] is False
    assert duplicate_outbox["reason"] == "duplicate_execution_outbox"

    counts = repository.table_counts()
    assert counts["regime_decisions"] == 1
    assert counts["regime_order_intents"] == 1
    assert counts["regime_execution_outbox"] == 2


def test_stale_state_version_backtest_namespace_and_same_symbol_instances_are_isolated() -> None:
    repository = RegimeSqliteRepository(f"sqlite:///{_temp_db_path()}")

    first = repository.write_runtime_checkpoint({**_identity("instance-a"), "decisionId": "state-a", "payload": {"bar": 1}}, expected_sequence_version=0)
    stale = repository.write_runtime_checkpoint({**_identity("instance-a"), "decisionId": "state-a", "payload": {"bar": 2}}, expected_sequence_version=0)
    other = repository.write_runtime_checkpoint({**_identity("instance-b"), "decisionId": "state-b", "payload": {"bar": 9}}, expected_sequence_version=0)
    assert first["updated"] is True
    assert stale["updated"] is False
    assert stale["reason"] == "stale_state_version"
    assert other["updated"] is True
    assert repository.read_runtime_checkpoint(_identity("instance-a"))["payload"] == {"bar": 1}
    assert repository.read_runtime_checkpoint(_identity("instance-b"))["payload"] == {"bar": 9}

    repository.record_position_state(_identity("paper-instance", "paper"), {"positionId": "paper-pos", "quantity": 5})
    repository.record_backtest_result({**_identity("paper-instance", "paper"), "runId": "bt-1", "trades": [{"tradeId": "bt-trade", "pnl": 1.0}]})
    assert len(repository.latest_regime_positions(_identity("paper-instance", "paper"))) == 1
    assert repository.read_owned_records("regime_backtest_runs", _identity("paper-instance", "backtest"))[0]["runId"] == "bt-1"
    assert repository.read_owned_records("regime_backtest_runs", _identity("paper-instance", "paper")) == []


def test_broker_observations_copy_to_regime_owned_ledgers_with_stable_ids_and_redaction() -> None:
    path = _temp_db_path()
    repository = RegimeSqliteRepository(f"sqlite:///{path}")
    result = repository.copy_broker_observation(
        {
            **_identity(),
            "type": "fill",
            "decisionId": "decision-fill",
            "orderIntentId": "intent-fill",
            "brokerOrderId": "broker-fill",
            "api_key": "secret-value",
            "filledQuantity": 2,
        }
    )

    assert result["table"] == "regime_fills"
    with sqlite3.connect(path) as conn:
        shared_count = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        payload = conn.execute("SELECT payload_json FROM regime_fills").fetchone()[0]
    assert shared_count == 0
    assert "fillId" in payload
    assert "[REDACTED]" in payload
    assert "secret-value" not in payload


def _decision_snapshot(decision_id: str) -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-a",
        "accountId": "paper-a",
        "runtimeMode": "paper",
        "symbol": "SPY",
        "decision": {
            "algorithm_id": "regime",
            "algorithm_version": "regime_algorithm_v3_backend_authoritative",
            "settings_version": "settings-a",
            "strategy_catalog_version": "catalog-a",
            "profile_version": "profile-a",
            "decision_id": decision_id,
            "symbol": "SPY",
            "signal": "Hold",
            "raw_classification": {"timestamp": "2026-07-23T16:00:00Z", "raw_regime": "range_bound"},
            "confirmed_state": {"confirmed_regime": "range_bound"},
            "strategy_outputs": [],
            "family_scores": {},
            "effective_settings": {},
            "trade_blockers": ["regime.local_gate.minimum_winning_score"],
        },
        "orderValidation": {"valid": False, "reasonCodes": ["regime.local_gate.minimum_winning_score"]},
    }


def _identity(instance: str = "regime-a", runtime_mode: str = "paper") -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": instance,
        "accountId": "paper-a",
        "runtimeMode": runtime_mode,
        "symbol": "SPY",
    }


def _temp_db_path() -> Path:
    root = Path(__file__).resolve().parent / "tmp" / "regime_persistence_boundary"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid4().hex}.sqlite"


class pytest_raises:
    def __init__(self, expected: type[BaseException]) -> None:
        self.expected = expected

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: object) -> bool:
        if exc_type is None:
            raise AssertionError(f"Expected {self.expected.__name__}")
        return issubclass(exc_type, self.expected)
