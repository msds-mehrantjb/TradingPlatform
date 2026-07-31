from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.persistence import REGIME_OWNED_TABLES
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory


PHASE1_TABLES = {
    "regime_runtime_state",
    "regime_bar_processing",
    "regime_inventory_events",
    "regime_inventory_snapshots",
    "regime_daily_risk_state",
    "regime_reconciliation_runs",
    "regime_runtime_alerts",
}
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase1_inventory"


def test_phase1_schema_has_regime_owned_tables_and_algorithm_check() -> None:
    repository, identity, path = _repository()

    assert PHASE1_TABLES.issubset(set(REGIME_OWNED_TABLES))
    assert {"regime_inventory_events", "regime_inventory_snapshots"}.issubset(
        set(regime_repository_inventory()["authoritativeOrderFillPositionTradeInventory"])
    )
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(regime_inventory_events)")}
        assert {"algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol", "sequence_version"}.issubset(columns)
        try:
            conn.execute(
                """
                INSERT INTO regime_inventory_events (
                    record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
                    algorithm_version, settings_version, strategy_version, profile_version,
                    timestamp, event_timestamp, symbol, data_timestamp, decision_id,
                    processing_status, sequence_version, payload_json
                )
                VALUES ('bad-inventory', 'wca', ?, ?, ?, 'v', 's', 'c', 'p', '', '', ?, '', 'bad',
                        'recorded', 1, '{}')
                """,
                (identity["algorithmInstanceId"], identity["accountId"], identity["runtimeMode"], identity["symbol"]),
            )
        except sqlite3.IntegrityError:
            pass
        else:  # pragma: no cover - explicit assertion keeps the check readable.
            raise AssertionError("regime_inventory_events accepted non-regime algorithm_id")


def test_phase1_inventory_moves_only_from_broker_fills_and_handles_rebuild() -> None:
    repository, identity, path = _repository()
    repository.insert_order_intent({**identity, "decisionId": "decision-1", "orderIntentId": "intent-1", "side": "Buy", "quantity": 10})

    unchanged = repository.current_inventory_snapshot(identity)
    first = repository.apply_inventory_fill(identity, _fill(identity, "fill-1", "Buy", 4, 100.0))
    second = repository.apply_inventory_fill(identity, _fill(identity, "fill-2", "Buy", 6, 102.0))
    duplicate = repository.apply_inventory_fill(identity, _fill(identity, "fill-2", "Buy", 6, 102.0))
    exit_fill = repository.apply_inventory_fill(identity, _fill(identity, "fill-3", "Sell", 3, 103.0))
    cancelled = repository.record_inventory_order_status({**identity, "algorithmId": "regime", "orderIntentId": "intent-1", "status": "cancelled"})

    assert unchanged["quantity"] == 0
    assert first["snapshot"]["quantity"] == 4
    assert second["snapshot"]["quantity"] == 10
    assert second["snapshot"]["averageEntryPrice"] == 101.2
    assert duplicate["duplicate"] is True
    assert exit_fill["snapshot"]["quantity"] == 7
    assert exit_fill["snapshot"]["realizedPnl"] == 5.4
    assert cancelled["snapshot"]["quantity"] == 7
    assert repository.table_counts()["regime_inventory_events"] == 4

    _write_bad_inventory_snapshot(path, identity)
    rebuilt = repository.verify_or_rebuild_inventory_snapshot(identity, broker_positions=[{"algorithmId": "regime", "positionId": exit_fill["snapshot"]["positionId"], "quantity": 7}])

    assert rebuilt["rebuilt"] is True
    assert rebuilt["reconciled"] is True
    assert rebuilt["snapshot"]["quantity"] == 7
    assert rebuilt["snapshot"]["lastBrokerReconciliationTime"]
    assert repository.current_inventory_snapshot(identity)["quantity"] == 7


def test_phase1_inventory_rejects_cross_algorithm_fill() -> None:
    repository, identity, _ = _repository()

    try:
        repository.apply_inventory_fill(identity, {**_fill(identity, "bad-fill", "Buy", 1, 100.0), "algorithmId": "weighted_voting"})
    except ValueError as exc:
        assert "cross-algorithm" in str(exc)
    else:  # pragma: no cover - explicit assertion keeps the check readable.
        raise AssertionError("Regime inventory accepted a cross-algorithm fill")


def _repository() -> tuple[RegimeRepository, dict[str, str], Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-phase1",
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return RegimeRepository(f"sqlite:///{path}"), identity, path


def _fill(identity: dict[str, str], fill_id: str, side: str, quantity: int, price: float) -> dict[str, object]:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": "decision-1",
        "orderIntentId": "intent-1",
        "brokerOrderId": "broker-1",
        "fillId": fill_id,
        "side": side,
        "filledQuantity": quantity,
        "averageFillPrice": price,
        "filledAt": f"2026-07-23T15:3{quantity}:00Z",
    }


def _write_bad_inventory_snapshot(path: Path, identity: dict[str, str]) -> None:
    payload = {
        **identity,
        "algorithmId": "regime",
        "quantity": 999,
        "averageEntryPrice": 1.0,
        "realizedPnl": 0.0,
        "unrealizedPnl": 0.0,
        "reservedCash": 0.0,
        "reservedRisk": 0.0,
        "openOrderQuantity": 0,
        "stateVersion": 99,
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO regime_inventory_snapshots (
                record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
                algorithm_version, settings_version, strategy_version, profile_version,
                timestamp, event_timestamp, symbol, data_timestamp, decision_id,
                processing_status, sequence_version, payload_json
            )
            VALUES (?, 'regime', ?, ?, ?, 'regime_algorithm_v3_backend_authoritative',
                    'settings', 'strategies', 'profiles', '2026-07-23T15:40:00Z',
                    '2026-07-23T15:40:00Z', ?, '2026-07-23T15:40:00Z',
                    'tampered-snapshot', 'current', 99, ?)
            """,
            (
                f"bad-snapshot-{uuid4().hex}",
                identity["algorithmInstanceId"],
                identity["accountId"],
                identity["runtimeMode"],
                identity["symbol"],
                json.dumps(payload, sort_keys=True),
            ),
        )
