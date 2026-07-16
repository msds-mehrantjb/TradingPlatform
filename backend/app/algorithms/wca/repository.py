"""Durable WCA repository backed by the existing SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    BacktestResult,
    ProposedOrder,
    WcaBrokerReconciliationResult,
    WcaBaselineSettings,
    WcaConfidenceCalibrationTable,
    WcaDecision,
    WcaEffectiveSettings,
    WcaMarketStatus,
    WcaPaperStabilityValidationResult,
    WcaShadowComparisonEvidence,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.config import get_settings
from backend.app.database import _sqlite_path

WCA_PERSISTENCE_MIGRATION_VERSION = "wca_authoritative_persistence_001"
WCA_IGNORED_LOCAL_STORAGE_KEYS = frozenset(
    {
        "weighted-confidence-decision-settings-v1",
        "weighted-confidence-trading-settings-v1",
        "weighted-confidence-target-order-overrides-v1",
        "confidence-backtest-result-v1",
        "wca-backtest-result-v1",
        "confidence-trade-history-v1",
        "confidence-order-control-modes-v1",
        "confidence-order-control-overrides-v1",
        "confidence-auto-submitted-order-keys-v1",
    }
)
WCA_ALLOWED_LOCAL_STORAGE_PREFIXES = ("ui-", "display-", "chart-", "panel-", "tab-")


class WcaRepository(Protocol):
    def initialize_defaults(self, *, symbol: str, configuration: dict[str, Any], weight_snapshot: WcaWeightSnapshot, engine_version: str) -> None:
        ...

    def save_configuration(self, payload: dict[str, Any], *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        ...

    def read_active_weights(self) -> WcaWeightSnapshot | None:
        ...

    def write_decision_snapshot(self, decision: WcaDecision, *, run_id: str | None = None) -> None:
        ...

    def reserve_order_intent(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str) -> WcaOrderIntentReservation:
        ...

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        ...

    def has_order_fill(self, order_intent_id: str) -> bool:
        ...

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        ...

    def write_shadow_comparison_evidence(self, evidence: WcaShadowComparisonEvidence) -> None:
        ...

    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        ...

    def save_backtest_result(self, result: BacktestResult) -> None:
        ...

    def load_backtest_result(self, run_id: str) -> BacktestResult | None:
        ...

    def table_counts(self) -> WcaPersistenceSummary:
        ...


@dataclass(frozen=True)
class WcaPersistenceSummary:
    table_counts: dict[str, int]
    migration_version: str = WCA_PERSISTENCE_MIGRATION_VERSION


@dataclass(frozen=True)
class WcaOrderIntentReservation:
    created: bool
    proposed_order: ProposedOrder
    idempotency_key: str


def classify_wca_local_storage_key(key: str) -> str:
    """Classify legacy browser storage handling for WCA.

    Authoritative WCA state now lives in SQLite. Old localStorage keys that
    carried settings, orders, trades, or backtest artifacts are safely ignored.
    """

    if key in WCA_IGNORED_LOCAL_STORAGE_KEYS:
        return "ignored_authoritative_backend_state"
    if key.startswith(WCA_ALLOWED_LOCAL_STORAGE_PREFIXES) or "expanded" in key or "collapsed" in key or "tab" in key:
        return "allowed_visual_preference"
    if "confidence" in key or "wca" in key:
        return "ignored_unknown_wca_local_storage"
    return "not_wca"


def migrate_wca_sqlite_database(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        apply_wca_persistence_migrations(conn)


def apply_wca_persistence_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wca_configuration_versions (
            configuration_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_versions (
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            family TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_id, strategy_version)
        );

        CREATE TABLE IF NOT EXISTS wca_weight_snapshots (
            weight_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_confidence_calibrations (
            calibration_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_market_status_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            trend TEXT NOT NULL,
            volatility TEXT NOT NULL,
            liquidity TEXT NOT NULL,
            session TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_effective_setting_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_decisions (
            decision_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_local_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            gate_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id, gate_id)
        );

        CREATE TABLE IF NOT EXISTS global_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_proposed_orders (
            order_intent_id TEXT PRIMARY KEY,
            idempotency_key TEXT,
            account_id TEXT NOT NULL DEFAULT 'paper',
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_execution_results (
            execution_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_trade_ledger (
            trade_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_broker_reconciliations (
            reconciliation_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            discrepancy_count INTEGER NOT NULL,
            hard_operational_warning INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_shadow_comparison_evidence (
            evidence_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            rollout_phase TEXT NOT NULL,
            within_tolerance INTEGER NOT NULL,
            mismatch_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_paper_stability_validations (
            validation_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            validation_days REAL NOT NULL,
            market_condition_count INTEGER NOT NULL,
            paper_trading_stable INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_backtest_runs (
            run_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            total_pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_backtest_trades (
            trade_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            family TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, strategy_id)
        );

        CREATE INDEX IF NOT EXISTS idx_wca_decisions_symbol_time
            ON wca_decisions(symbol, timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_backtest_runs_symbol_time
            ON wca_backtest_runs(symbol, timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_trade_ledger_decision
            ON wca_trade_ledger(decision_id);
        """
    )
    _ensure_column(conn, "wca_proposed_orders", "idempotency_key", "TEXT")
    _ensure_column(conn, "wca_proposed_orders", "account_id", "TEXT NOT NULL DEFAULT 'paper'")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_proposed_orders_idempotency
            ON wca_proposed_orders(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (WCA_PERSISTENCE_MIGRATION_VERSION,),
    )


class WcaSqliteRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.path = _sqlite_path(database_url or get_settings().database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_wca_sqlite_database(self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize_defaults(self, *, symbol: str, configuration: dict[str, Any], weight_snapshot: WcaWeightSnapshot, engine_version: str) -> None:
        timestamp = _utc_now()
        self.save_configuration(configuration, symbol=symbol, timestamp=timestamp, engine_version=engine_version)
        self.save_strategy_versions(symbol=symbol, timestamp=timestamp, configuration_version=str(configuration["configurationVersion"]), engine_version=engine_version)
        self.save_weight_snapshot(weight_snapshot, symbol=symbol, configuration_version=str(configuration["configurationVersion"]), engine_version=engine_version)

    def save_configuration(self, payload: dict[str, Any], *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        configuration_version = str(payload.get("configurationVersion") or payload.get("configuration_version") or "wca_configuration_unversioned")
        row = _common_row(
            symbol=symbol,
            timestamp=timestamp or _utc_now(),
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-config-{configuration_version}",
            decision_id=f"wca-config-{configuration_version}",
            run_id=f"wca-config-{configuration_version}",
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_configuration_versions (
                    configuration_version, algorithm_id, symbol, timestamp, engine_version,
                    market_snapshot_id, decision_id, run_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (configuration_version, row["algorithm_id"], symbol, row["timestamp"], engine_version, row["market_snapshot_id"], row["decision_id"], row["run_id"], _json(payload)),
            )

    def save_strategy_versions(self, *, symbol: str, timestamp: str, configuration_version: str, engine_version: str) -> None:
        row = _common_row(
            symbol=symbol,
            timestamp=timestamp,
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-strategies-{configuration_version}",
            decision_id=f"wca-strategies-{configuration_version}",
            run_id=f"wca-strategies-{configuration_version}",
        )
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO wca_strategy_versions (
                    strategy_id, strategy_version, algorithm_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id, decision_id,
                    run_id, family, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        definition.strategy_id,
                        f"{definition.slug}_catalog_v1",
                        row["algorithm_id"],
                        symbol,
                        row["timestamp"],
                        configuration_version,
                        engine_version,
                        row["market_snapshot_id"],
                        row["decision_id"],
                        row["run_id"],
                        definition.family,
                        _json(
                            {
                                "strategy_id": definition.strategy_id,
                                "slug": definition.slug,
                                "name": definition.name,
                                "family": definition.family,
                                "base_weight": definition.base_weight,
                                "role": _value(definition.role),
                            }
                        ),
                    )
                    for definition in WCA_STRATEGY_REGISTRY
                ],
            )

    def save_weight_snapshot(self, snapshot: WcaWeightSnapshot, *, symbol: str, configuration_version: str, engine_version: str, run_id: str = "wca-active-weights") -> None:
        timestamp = _dt(snapshot.created_at)
        row = _common_row(
            symbol=symbol,
            timestamp=timestamp,
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"{run_id}-weights",
            decision_id=f"{run_id}-weights",
            run_id=run_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_weight_snapshots (
                    weight_version, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot.weight_version, row["algorithm_id"], symbol, timestamp, configuration_version, engine_version, row["market_snapshot_id"], row["decision_id"], run_id, snapshot.model_dump_json()),
            )

    def read_active_weights(self) -> WcaWeightSnapshot | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM wca_weight_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return WcaWeightSnapshot.model_validate_json(row["payload_json"]) if row else None

    def save_confidence_calibration(self, calibration: WcaConfidenceCalibrationTable, *, symbol: str, configuration_version: str, engine_version: str) -> None:
        row = _common_row(
            symbol=symbol,
            timestamp=_dt(calibration.created_at),
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-calibration-{calibration.calibration_version}",
            decision_id=f"wca-calibration-{calibration.calibration_version}",
            run_id=f"wca-calibration-{calibration.calibration_version}",
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_confidence_calibrations (
                    calibration_version, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, strategy_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (calibration.calibration_version, row["algorithm_id"], symbol, row["timestamp"], configuration_version, engine_version, row["market_snapshot_id"], row["decision_id"], row["run_id"], calibration.strategy_id, calibration.model_dump_json()),
            )

    def write_decision_snapshot(self, decision: WcaDecision, *, run_id: str | None = None) -> None:
        run = run_id or decision.decision_id
        common = _decision_common(decision, run)
        with self.connect() as conn:
            self._insert_decision(conn, decision, common)

    def reserve_order_intent(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str) -> WcaOrderIntentReservation:
        if decision.proposed_order is None:
            raise ValueError("cannot reserve a missing WCA order intent")
        proposed = decision.proposed_order.model_copy(update={"idempotency_key": idempotency_key, "account_id": account_id})
        common = _decision_common(decision.model_copy(update={"proposed_order": proposed}), run_id)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_proposed_orders (
                    order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                    timestamp, configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, side, quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposed.order_intent_id,
                    idempotency_key,
                    account_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    decision.decision_id,
                    run_id,
                    _value(proposed.side),
                    proposed.quantity,
                    proposed.model_dump_json(),
                ),
            )
            row = conn.execute(
                "SELECT payload_json FROM wca_proposed_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to read reserved WCA order intent")
        return WcaOrderIntentReservation(
            created=cursor.rowcount > 0,
            proposed_order=ProposedOrder.model_validate_json(row["payload_json"]),
            idempotency_key=idempotency_key,
        )

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        sql = "SELECT payload_json FROM wca_proposed_orders"
        params: tuple[str, ...] = ()
        if account_id is not None:
            sql += " WHERE account_id = ?"
            params = (account_id,)
        sql += " ORDER BY created_at"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(ProposedOrder.model_validate_json(row["payload_json"]) for row in rows)

    def has_order_fill(self, order_intent_id: str) -> bool:
        pattern = f"%{order_intent_id}%"
        with self.connect() as conn:
            count = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM wca_execution_results WHERE payload_json LIKE ?)
                    + (SELECT COUNT(*) FROM wca_trade_ledger WHERE payload_json LIKE ?)
                """,
                (pattern, pattern),
            ).fetchone()[0]
        return int(count) > 0

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        first = result.discrepancies[0] if result.discrepancies else None
        common = _common_row(
            symbol=first.symbol if first is not None else "SPY",
            timestamp=_dt(result.evaluated_at),
            configuration_version="wca_broker_reconciliation",
            engine_version=result.reconciliation_version,
            market_snapshot_id=result.reconciliation_id,
            decision_id=first.decision_id if first is not None and first.decision_id else result.reconciliation_id,
            run_id=result.reconciliation_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_broker_reconciliations (
                    reconciliation_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, account_id,
                    discrepancy_count, hard_operational_warning, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.reconciliation_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    result.account_id,
                    len(result.discrepancies),
                    1 if result.hard_operational_warning else 0,
                    result.model_dump_json(),
                ),
            )

    def write_shadow_comparison_evidence(self, evidence: WcaShadowComparisonEvidence) -> None:
        common = _common_row(
            symbol=evidence.symbol,
            timestamp=_dt(evidence.evaluated_at),
            configuration_version=evidence.evidence_version,
            engine_version=evidence.evidence_version,
            market_snapshot_id=evidence.snapshot_id,
            decision_id=evidence.snapshot_id,
            run_id=evidence.evidence_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_shadow_comparison_evidence (
                    evidence_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, snapshot_id,
                    rollout_phase, within_tolerance, mismatch_count, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    evidence.snapshot_id,
                    evidence.rollout_phase,
                    1 if evidence.within_tolerance else 0,
                    len(evidence.mismatched_fields),
                    evidence.model_dump_json(),
                ),
            )

    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        common = _common_row(
            symbol="SPY",
            timestamp=_dt(result.ended_at),
            configuration_version=result.validation_version,
            engine_version=result.validation_version,
            market_snapshot_id=result.validation_id,
            decision_id=result.validation_id,
            run_id=result.validation_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_paper_stability_validations (
                    validation_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, account_id,
                    validation_days, market_condition_count, paper_trading_stable, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.validation_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    result.account_id,
                    result.validation_days,
                    len(result.market_conditions),
                    1 if result.paper_trading_stable else 0,
                    result.model_dump_json(),
                ),
            )

    def save_backtest_result(self, result: BacktestResult) -> None:
        config = result.run_configuration
        timestamp = _dt(config.end)
        market_snapshot_id = f"{config.run_id}-market"
        decision_id = f"{config.run_id}-run"
        common = _common_row(
            symbol=config.symbol,
            timestamp=timestamp,
            configuration_version=config.configuration_version,
            engine_version=str(result.metrics.get("engineVersion") or "wca_backtest_engine"),
            market_snapshot_id=market_snapshot_id,
            decision_id=decision_id,
            run_id=config.run_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_backtest_runs (
                    run_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, total_pnl, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (config.run_id, common["algorithm_id"], config.symbol, timestamp, config.configuration_version, common["engine_version"], market_snapshot_id, decision_id, result.total_pnl, result.model_dump_json()),
            )
            for decision in result.decisions:
                self._insert_decision(conn, decision, _decision_common(decision, config.run_id, engine_version=common["engine_version"]))
            for trade in result.trades:
                self._insert_trade(conn, trade, result, common)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wca_execution_results (
                        execution_id, algorithm_id, symbol, timestamp, configuration_version,
                        engine_version, market_snapshot_id, decision_id, run_id, status, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{trade.trade_id}-execution",
                        common["algorithm_id"],
                        trade.symbol,
                        _dt(trade.exit_at or trade.entry_at),
                        config.configuration_version,
                        common["engine_version"],
                        f"{trade.decision_id}-market",
                        trade.decision_id,
                        config.run_id,
                        "filled",
                        trade.model_dump_json(),
                    ),
                )
            self._insert_strategy_performance(conn, result, common)

    def load_backtest_result(self, run_id: str) -> BacktestResult | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload_json FROM wca_backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        return BacktestResult.model_validate_json(row["payload_json"]) if row else None

    def table_counts(self) -> WcaPersistenceSummary:
        tables = (
            "wca_configuration_versions",
            "wca_strategy_versions",
            "wca_weight_snapshots",
            "wca_confidence_calibrations",
            "wca_market_status_snapshots",
            "wca_effective_setting_snapshots",
            "wca_decisions",
            "wca_local_gate_evaluations",
            "global_gate_evaluations",
            "wca_proposed_orders",
            "wca_execution_results",
            "wca_trade_ledger",
            "wca_broker_reconciliations",
            "wca_shadow_comparison_evidence",
            "wca_paper_stability_validations",
            "wca_backtest_runs",
            "wca_backtest_trades",
            "wca_strategy_performance",
        )
        with self.connect() as conn:
            counts = {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
        return WcaPersistenceSummary(table_counts=counts)

    def _insert_decision(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        side = _value(decision.aggregation.post_local_gate_decision)
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_decisions (
                decision_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, run_id, side, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (decision.decision_id, common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["run_id"], side, decision.model_dump_json()),
        )
        self._insert_market_status(conn, decision.market_status, common)
        if decision.effective_settings is not None:
            self._insert_effective_settings(conn, decision.effective_settings, common)
        for gate in decision.local_gates:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_local_gate_evaluations (
                    algorithm_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, gate_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], decision.decision_id, common["run_id"], gate.gate_id, _value(gate.status), gate.model_dump_json()),
            )
        if decision.global_gate_result is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO global_gate_evaluations (
                    algorithm_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], decision.decision_id, common["run_id"], _value(decision.global_gate_result.status), decision.global_gate_result.model_dump_json()),
            )
        if decision.proposed_order is not None:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_proposed_orders (
                    order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                    timestamp, configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, side, quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.proposed_order.order_intent_id,
                    decision.proposed_order.idempotency_key,
                    decision.proposed_order.account_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    decision.decision_id,
                    common["run_id"],
                    _value(decision.proposed_order.side),
                    decision.proposed_order.quantity,
                    decision.proposed_order.model_dump_json(),
                ),
            )

    def _insert_trade(self, conn: sqlite3.Connection, trade, result: BacktestResult, common: dict[str, str]) -> None:
        config = result.run_configuration
        payload = trade.model_dump_json()
        values = (
            trade.trade_id,
            common["algorithm_id"],
            trade.symbol,
            _dt(trade.exit_at or trade.entry_at),
            config.configuration_version,
            common["engine_version"],
            f"{trade.decision_id}-market",
            trade.decision_id,
            config.run_id,
            _value(trade.side),
            trade.quantity,
            trade.pnl,
            payload,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_trade_ledger (
                trade_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, pnl, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_backtest_trades (
                trade_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, pnl, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def _insert_strategy_performance(self, conn: sqlite3.Connection, result: BacktestResult, common: dict[str, str]) -> None:
        by_strategy = result.metrics.get("diagnostics", {}).get("breakdowns", {}).get("byStrategy", {})
        for strategy_id, payload in by_strategy.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_strategy_performance (
                    algorithm_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, strategy_id, family, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], strategy_id, str(payload.get("family", "unknown")), _json(payload)),
            )

    def _insert_market_status(self, conn: sqlite3.Connection, status: WcaMarketStatus, common: dict[str, str]) -> None:  # type: ignore[no-redef]
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_market_status_snapshots (
                algorithm_id, symbol, timestamp, configuration_version, engine_version,
                market_snapshot_id, decision_id, run_id, trend, volatility, liquidity,
                session, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(status.trend), _value(status.volatility), _value(status.liquidity), _value(status.session), status.model_dump_json()),
        )

    def _insert_effective_settings(self, conn: sqlite3.Connection, settings: WcaEffectiveSettings, common: dict[str, str]) -> None:  # type: ignore[no-redef]
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_effective_setting_snapshots (
                algorithm_id, symbol, timestamp, configuration_version, engine_version,
                market_snapshot_id, decision_id, run_id, settings_version, profile_id,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], settings.settings_version, settings.profile_id, settings.model_dump_json()),
        )


def _common_row(*, symbol: str, timestamp: str, configuration_version: str, engine_version: str, market_snapshot_id: str, decision_id: str, run_id: str) -> dict[str, str]:
    return {
        "algorithm_id": WCA_ALGORITHM_ID,
        "symbol": symbol,
        "timestamp": timestamp,
        "configuration_version": configuration_version,
        "engine_version": engine_version,
        "market_snapshot_id": market_snapshot_id,
        "decision_id": decision_id,
        "run_id": run_id,
    }


def _decision_common(decision: WcaDecision, run_id: str, *, engine_version: str | None = None) -> dict[str, str]:
    return _common_row(
        symbol=decision.market_snapshot.symbol,
        timestamp=_dt(decision.decision_timestamp),
        configuration_version=decision.configuration_version,
        engine_version=engine_version or next((code for code in decision.reason_codes if code.startswith("wca_")), "wca_engine"),
        market_snapshot_id=f"{decision.decision_id}-market",
        decision_id=decision.decision_id,
        run_id=run_id,
    )


def _json(payload: Any) -> str:
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json()
    if hasattr(payload, "__dict__"):
        import json

        return json.dumps(payload.__dict__, sort_keys=True, separators=(",", ":"))
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _dt(value: datetime | str | None) -> str:
    if value is None:
        return _utc_now()
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


__all__ = [
    "WCA_IGNORED_LOCAL_STORAGE_KEYS",
    "WCA_PERSISTENCE_MIGRATION_VERSION",
    "WcaOrderIntentReservation",
    "WcaPersistenceSummary",
    "WcaRepository",
    "WcaSqliteRepository",
    "apply_wca_persistence_migrations",
    "classify_wca_local_storage_key",
    "migrate_wca_sqlite_database",
]
