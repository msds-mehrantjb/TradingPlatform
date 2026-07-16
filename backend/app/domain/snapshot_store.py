from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.domain.models import DecisionSnapshotV2


MIGRATION_VERSION = "decision_snapshot_v2_normalized_001"


class DuplicateDecisionSnapshotError(ValueError):
    pass


class DuplicateOrderDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class SaveDecisionSnapshotResult:
    snapshotId: str
    inserted: bool
    normalizedRows: dict[str, int]
    rawSnapshotHash: str


def migrate_sqlite_database(path: str | Path) -> None:
    db_path = Path(path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        apply_decision_snapshot_v2_migrations(conn)


def apply_decision_snapshot_v2_migrations(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS decision_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            snapshot_schema_version TEXT NOT NULL,
            strategy_schema_version TEXT NOT NULL,
            feature_schema_version TEXT NOT NULL,
            label_version TEXT NOT NULL,
            execution_model_version TEXT NOT NULL,
            gate_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            code_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_data_feed TEXT NOT NULL,
            decision_timestamp_utc TEXT NOT NULL,
            session_date_new_york TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            strategy_configuration_hash TEXT NOT NULL,
            trading_settings_hash TEXT NOT NULL,
            eligible_for_training INTEGER NOT NULL,
            sampling_probability REAL NOT NULL,
            sample_weight REAL NOT NULL,
            sampling_reason TEXT NOT NULL,
            raw_snapshot_json TEXT NOT NULL,
            raw_snapshot_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                symbol,
                decision_timestamp_utc,
                algorithm_version,
                strategy_schema_version,
                configuration_hash
            )
        );

        CREATE TABLE IF NOT EXISTS strategy_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            strategy_id TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            family TEXT NOT NULL,
            role TEXT NOT NULL,
            signal TEXT NOT NULL,
            direction INTEGER NOT NULL,
            confidence REAL NOT NULL,
            reliability REAL NOT NULL,
            regime_fit REAL NOT NULL,
            eligible INTEGER NOT NULL,
            data_ready INTEGER NOT NULL,
            output_json TEXT NOT NULL,
            UNIQUE (snapshot_id, strategy_id)
        );

        CREATE TABLE IF NOT EXISTS context_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            context_id TEXT NOT NULL,
            signal TEXT NOT NULL,
            direction INTEGER NOT NULL,
            confidence REAL NOT NULL,
            data_ready INTEGER NOT NULL,
            output_json TEXT NOT NULL,
            UNIQUE (snapshot_id, context_id)
        );

        CREATE TABLE IF NOT EXISTS regime_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            regime_id TEXT NOT NULL,
            label TEXT NOT NULL,
            direction INTEGER NOT NULL,
            volatility TEXT NOT NULL,
            confidence REAL NOT NULL,
            state_json TEXT NOT NULL,
            UNIQUE (snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS family_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            family TEXT NOT NULL,
            buy_score REAL NOT NULL,
            sell_score REAL NOT NULL,
            hold_score REAL NOT NULL,
            confidence REAL NOT NULL,
            reliability REAL NOT NULL,
            score_json TEXT NOT NULL,
            UNIQUE (snapshot_id, family)
        );

        CREATE TABLE IF NOT EXISTS gate_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            gate_id TEXT NOT NULL,
            gate_name TEXT NOT NULL,
            status TEXT NOT NULL,
            blocks_trading INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            UNIQUE (snapshot_id, gate_id)
        );

        CREATE TABLE IF NOT EXISTS policy_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            mode TEXT NOT NULL,
            max_quantity INTEGER NOT NULL,
            max_notional REAL NOT NULL,
            risk_dollars REAL NOT NULL,
            policy_json TEXT NOT NULL,
            UNIQUE (snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            order_plan_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            eligible INTEGER NOT NULL,
            generated_at TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            order_json TEXT NOT NULL,
            UNIQUE (snapshot_id, order_plan_id),
            UNIQUE (symbol, generated_at, configuration_hash, candidate_id)
        );

        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            fill_id TEXT NOT NULL,
            order_plan_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            average_fill_price REAL NOT NULL,
            filled_at TEXT NOT NULL,
            fill_json TEXT NOT NULL,
            UNIQUE (fill_id)
        );

        CREATE TABLE IF NOT EXISTS trade_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            outcome_json TEXT NOT NULL,
            UNIQUE (snapshot_id)
        );

        CREATE TABLE IF NOT EXISTS model_training_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            training_schema_version TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            run_json TEXT NOT NULL,
            UNIQUE (run_id)
        );

        CREATE TABLE IF NOT EXISTS model_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES decision_snapshots(snapshot_id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            model_version TEXT NOT NULL,
            signal TEXT NOT NULL,
            confidence REAL NOT NULL,
            prediction_json TEXT NOT NULL,
            UNIQUE (snapshot_id, model_id, model_version)
        );

        CREATE INDEX IF NOT EXISTS idx_decision_snapshots_lookup
            ON decision_snapshots(symbol, decision_timestamp_utc, algorithm_version);
        CREATE INDEX IF NOT EXISTS idx_strategy_outputs_lookup
            ON strategy_outputs(strategy_id, family, signal);
        CREATE INDEX IF NOT EXISTS idx_gate_results_lookup
            ON gate_results(gate_id, status);
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (MIGRATION_VERSION,),
    )


class DecisionSnapshotV2Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        apply_decision_snapshot_v2_migrations(self.conn)

    def save(self, snapshot: DecisionSnapshotV2) -> SaveDecisionSnapshotResult:
        raw_json = snapshot.model_dump_json()
        raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        existing = self._existing_decision(snapshot)
        if existing:
            if existing["raw_snapshot_hash"] != raw_hash:
                raise DuplicateDecisionSnapshotError("duplicate decision identity has different raw snapshot JSON")
            return SaveDecisionSnapshotResult(
                snapshotId=str(existing["snapshot_id"]),
                inserted=False,
                normalizedRows=self._normalized_counts(str(existing["snapshot_id"])),
                rawSnapshotHash=raw_hash,
            )

        try:
            with self.conn:
                self._insert_snapshot(snapshot, raw_json, raw_hash)
                counts = self._insert_normalized(snapshot)
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "orders" in message:
                raise DuplicateOrderDecisionError("duplicate order decision rejected") from exc
            raise
        return SaveDecisionSnapshotResult(
            snapshotId=snapshot.snapshotId,
            inserted=True,
            normalizedRows=counts,
            rawSnapshotHash=raw_hash,
        )

    def raw_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT raw_snapshot_json FROM decision_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return json.loads(row["raw_snapshot_json"]) if row else None

    def strategy_outputs(self, snapshot_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT strategy_id, family, signal, confidence, reliability, regime_fit FROM strategy_outputs WHERE snapshot_id = ? ORDER BY strategy_id",
            (snapshot_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _existing_decision(self, snapshot: DecisionSnapshotV2) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT snapshot_id, raw_snapshot_hash
            FROM decision_snapshots
            WHERE symbol = ?
              AND decision_timestamp_utc = ?
              AND algorithm_version = ?
              AND strategy_schema_version = ?
              AND configuration_hash = ?
            """,
            (
                snapshot.symbol,
                _iso(snapshot.decisionTimestampUtc or snapshot.decisionTimestamp),
                snapshot.engineVersion,
                snapshot.strategySchemaVersion,
                snapshot.configurationHash,
            ),
        ).fetchone()

    def _insert_snapshot(self, snapshot: DecisionSnapshotV2, raw_json: str, raw_hash: str) -> None:
        self.conn.execute(
            """
            INSERT INTO decision_snapshots (
                snapshot_id, snapshot_schema_version, strategy_schema_version,
                feature_schema_version, label_version, execution_model_version,
                gate_version, policy_version, model_version, algorithm_version,
                code_version, symbol, market_data_feed, decision_timestamp_utc,
                session_date_new_york, configuration_hash, strategy_configuration_hash,
                trading_settings_hash, eligible_for_training, sampling_probability,
                sample_weight, sampling_reason, raw_snapshot_json, raw_snapshot_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshotId,
                snapshot.snapshotSchemaVersion,
                snapshot.strategySchemaVersion,
                snapshot.featureSchemaVersion,
                snapshot.labelVersion,
                snapshot.executionModelVersion,
                snapshot.gateVersion,
                snapshot.policyVersion,
                snapshot.modelVersion,
                snapshot.engineVersion,
                snapshot.codeVersion,
                snapshot.symbol,
                snapshot.marketDataFeed,
                _iso(snapshot.decisionTimestampUtc or snapshot.decisionTimestamp),
                str(snapshot.sessionDateNewYork or snapshot.sessionDate),
                snapshot.configurationHash,
                snapshot.strategyConfigurationHash,
                snapshot.tradingSettingsHash,
                int(snapshot.eligibleForTraining),
                snapshot.samplingProbability,
                snapshot.sampleWeight,
                snapshot.samplingReason,
                raw_json,
                raw_hash,
            ),
        )

    def _insert_normalized(self, snapshot: DecisionSnapshotV2) -> dict[str, int]:
        counts = {name: 0 for name in _NORMALIZED_TABLES}
        for output in snapshot.directionalStrategyOutputs:
            self.conn.execute(
                """
                INSERT INTO strategy_outputs (
                    snapshot_id, strategy_id, strategy_name, family, role, signal,
                    direction, confidence, reliability, regime_fit, eligible, data_ready, output_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    output.strategyId,
                    output.strategyName,
                    output.family,
                    output.role,
                    output.signal,
                    int(output.direction),
                    output.confidence,
                    output.reliability,
                    output.regimeFit,
                    int(output.eligible),
                    int(output.dataReady),
                    output.model_dump_json(),
                ),
            )
            counts["strategy_outputs"] += 1
        for output in snapshot.contextOutputs:
            self.conn.execute(
                """
                INSERT INTO context_outputs (
                    snapshot_id, context_id, signal, direction, confidence, data_ready, output_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    output.contextId,
                    output.signal,
                    int(output.direction),
                    output.confidence,
                    int(output.dataReady),
                    output.model_dump_json(),
                ),
            )
            counts["context_outputs"] += 1
        self.conn.execute(
            """
            INSERT INTO regime_states (
                snapshot_id, regime_id, label, direction, volatility, confidence, state_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshotId,
                snapshot.regimeState.regimeId,
                snapshot.regimeState.label,
                int(snapshot.regimeState.direction),
                snapshot.regimeState.volatility,
                snapshot.regimeState.confidence,
                snapshot.regimeState.model_dump_json(),
            ),
        )
        counts["regime_states"] += 1
        for score in snapshot.ensembleDecision.familyScores:
            self.conn.execute(
                """
                INSERT INTO family_scores (
                    snapshot_id, family, buy_score, sell_score, hold_score,
                    confidence, reliability, score_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    score.family,
                    score.buyScore,
                    score.sellScore,
                    score.holdScore,
                    score.confidence,
                    score.reliability,
                    score.model_dump_json(),
                ),
            )
            counts["family_scores"] += 1
        for gate in snapshot.globalGateResults:
            self.conn.execute(
                """
                INSERT INTO gate_results (
                    snapshot_id, gate_id, gate_name, status, blocks_trading, result_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    gate.gateId,
                    gate.gateName,
                    gate.status,
                    int(gate.blocksTrading),
                    gate.model_dump_json(),
                ),
            )
            counts["gate_results"] += 1
        policy = snapshot.effectiveTradePolicy
        self.conn.execute(
            """
            INSERT INTO policy_decisions (
                snapshot_id, mode, max_quantity, max_notional, risk_dollars, policy_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshotId,
                policy.mode,
                policy.maxQuantity,
                policy.maxNotional,
                policy.riskDollars,
                policy.model_dump_json(),
            ),
        )
        counts["policy_decisions"] += 1
        if snapshot.orderPlan:
            order = snapshot.orderPlan
            self.conn.execute(
                """
                INSERT INTO orders (
                    snapshot_id, order_plan_id, candidate_id, symbol, side, order_type,
                    quantity, eligible, generated_at, configuration_hash, order_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    order.orderPlanId,
                    order.candidateId,
                    order.symbol,
                    order.side,
                    order.orderType,
                    order.quantity,
                    int(order.eligible),
                    _iso(order.generatedAt),
                    order.configurationHash,
                    order.model_dump_json(),
                ),
            )
            counts["orders"] += 1
        for fill in snapshot.fills:
            self.conn.execute(
                """
                INSERT INTO fills (
                    snapshot_id, fill_id, order_plan_id, symbol, side, quantity,
                    average_fill_price, filled_at, fill_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    fill.fillId,
                    fill.orderPlanId,
                    fill.symbol,
                    fill.side,
                    fill.quantity,
                    fill.averageFillPrice,
                    _iso(fill.filledAt),
                    fill.model_dump_json(),
                ),
            )
            counts["fills"] += 1
        if snapshot.finalOutcome is not None:
            self.conn.execute(
                "INSERT INTO trade_outcomes (snapshot_id, outcome_json) VALUES (?, ?)",
                (snapshot.snapshotId, json.dumps(snapshot.finalOutcome, sort_keys=True)),
            )
            counts["trade_outcomes"] += 1
        if snapshot.metaModelPrediction:
            prediction = snapshot.metaModelPrediction
            self.conn.execute(
                """
                INSERT INTO model_predictions (
                    snapshot_id, model_id, model_version, signal, confidence, prediction_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshotId,
                    prediction.modelId,
                    prediction.modelVersion,
                    prediction.signal,
                    prediction.confidence,
                    prediction.model_dump_json(),
                ),
            )
            counts["model_predictions"] += 1
        return counts

    def _normalized_counts(self, snapshot_id: str) -> dict[str, int]:
        return {
            table: int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()["count"]
            )
            for table in _NORMALIZED_TABLES
            if table != "model_training_runs"
        }


_NORMALIZED_TABLES = (
    "strategy_outputs",
    "context_outputs",
    "regime_states",
    "family_scores",
    "gate_results",
    "policy_decisions",
    "orders",
    "fills",
    "trade_outcomes",
    "model_predictions",
    "model_training_runs",
)


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z") if hasattr(value, "isoformat") else str(value)
