from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.configuration import default_wca_configuration, validate_wca_configuration
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.local_paper_account import WcaLocalPaperAccount, WcaLocalPaperLotSnapshot, WcaLocalPaperOrderSnapshot
from backend.app.algorithms.wca.paper_account import WCA_LOCAL_PAPER_SOURCE_AUTHORITY
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_state import load_wca_authoritative_runtime_state
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.app.algorithms.wca.contracts import WcaBrokerReconciliationResult, WcaRuntimeMode, WcaSide
from backend.app.domain.models import Signal
from backend.app.gates import BrokerAccountSnapshot, BrokerPositionState
from backend.tests.test_wca_step5_production_pipeline import FakeVoter, market_snapshot


UTC = timezone.utc


class WcaPhase2RuntimeStateTests(unittest.TestCase):
    def test_existing_wca_position_is_loaded_before_decision_and_blocks_new_entry(self) -> None:
        repository, snapshot = seeded_repository(open_quantity=5)
        state = load_state(repository, snapshot)

        decision = run_decision_from_state(repository, snapshot, state, WcaSide.BUY)

        self.assertEqual(state.current_quantity, 5)
        self.assertIsNotNone(state.to_open_position())
        self.assertEqual(state.to_open_position().stop_price, 98.5)
        self.assertEqual(state.to_open_position().target_price, 103.0)
        self.assertEqual(state.to_open_position().decision_id, "phase2-entry-decision")
        self.assertEqual(decision.aggregation.post_local_gate_decision, "HOLD")
        self.assertIn("wca.local_gate.pyramiding_restrictions", gate_reasons(decision))

    def test_open_position_without_reconstructable_protection_fails_closed(self) -> None:
        repository, snapshot = seeded_repository(open_quantity=5)
        with sqlite3.connect(repository.path) as conn:
            row = conn.execute("SELECT lot_id, payload_json FROM wca_owned_lots WHERE account_id = 'paper' AND symbol = 'SPY'").fetchone()
            payload = json.loads(row[1])
            payload.pop("stop_price", None)
            payload.pop("target_price", None)
            conn.execute("UPDATE wca_owned_lots SET payload_json = ? WHERE lot_id = ?", (json.dumps(payload), row[0]))

        state = load_state(repository, snapshot)
        result = run_worker(repository, snapshot, "phase2-unprotected")

        self.assertFalse(state.fresh)
        self.assertTrue(state.position_unprotected)
        self.assertIsNone(state.to_open_position())
        self.assertIn("wca.runtime_state.position_protection_missing", state.reason_codes)
        self.assertEqual(result["workers"]["decision_worker"]["status"], "blocked")
        self.assertIn("wca.runtime_state.position_protection_missing", result["workers"]["decision_worker"]["reasonCodes"])

    def test_daily_trade_limits_use_persisted_wca_state(self) -> None:
        repository, snapshot = seeded_repository(trades_completed_today=5)
        state = load_state(repository, snapshot)

        decision = run_decision_from_state(repository, snapshot, state, WcaSide.BUY)

        self.assertEqual(state.daily_trade_count, 5)
        self.assertEqual(decision.aggregation.post_local_gate_decision, "HOLD")
        self.assertIn("wca.local_gate.trade_count_limit", gate_reasons(decision))

    def test_daily_loss_limits_use_persisted_wca_state(self) -> None:
        repository, snapshot = seeded_repository(daily_loss=3_000)
        state = load_state(repository, snapshot)

        decision = run_decision_from_state(repository, snapshot, state, WcaSide.BUY)

        self.assertEqual(state.daily_loss, 3_000)
        self.assertEqual(decision.aggregation.post_local_gate_decision, "HOLD")
        self.assertIn("wca.local_gate.daily_loss_allocation", gate_reasons(decision))

    def test_buying_power_uses_wca_local_paper_account_snapshot(self) -> None:
        repository, snapshot = seeded_repository(buying_power=1)
        state = load_state(repository, snapshot)

        decision = run_decision_from_state(repository, snapshot, state, WcaSide.BUY)

        self.assertEqual(state.buying_power, 1)
        self.assertLessEqual(decision.sizing.shares_by_buying_power, 1)
        self.assertEqual(decision.authoritative_state_hash, state.state_hash)
    def test_local_automatic_paper_state_uses_local_account_inventory_and_orders_not_broker_snapshot(self) -> None:
        repository, snapshot = seeded_repository(seed_runtime_state=False)
        timestamp = snapshot.decision_timestamp.astimezone(UTC)
        opened_at = timestamp - timedelta(seconds=30)
        local_account = WcaLocalPaperAccount(
            account_id="paper",
            starting_balance=50_000,
            cash=49_000,
            session_date=timestamp.date(),
            reserved_risk=250,
            trades_today=2,
            lots=(
                WcaLocalPaperLotSnapshot(
                    lot_id="wca-local-runtime-state-lot",
                    algorithm_id="wca",
                    account_id="paper",
                    symbol="SPY",
                    side="BUY",
                    quantity=10,
                    remaining_quantity=10,
                    entry_price=100,
                    entry_timestamp=opened_at,
                    opened_at=opened_at,
                    decision_id="local-entry-decision",
                    order_intent_id="local-entry-intent",
                    stop_price=98,
                    target_price=104,
                ),
            ),
            open_orders=(
                WcaLocalPaperOrderSnapshot(
                    algorithm_id="wca",
                    account_id="paper",
                    symbol="SPY",
                    side="BUY",
                    quantity=3,
                    status="SUBMITTED",
                    client_order_id="wca-local-entry-order",
                    order_intent_id="local-entry-intent-2",
                    order_type="LIMIT",
                    limit_price=99,
                    submitted_at=timestamp,
                    local_order_id="wca-local-entry-order-id",
                    remaining_quantity=3,
                    created_at=timestamp,
                    updated_at=timestamp,
                    decision_id="local-pending-entry-decision",
                    idempotency_key="local-entry-key",
                ),
                WcaLocalPaperOrderSnapshot(
                    algorithm_id="wca",
                    account_id="paper",
                    symbol="SPY",
                    side="SELL",
                    quantity=10,
                    status="SUBMITTED",
                    client_order_id="wca-protection-stop-order",
                    order_intent_id="local-entry-intent",
                    order_type="STOP_LIMIT",
                    limit_price=97.9,
                    stop_price=98,
                    target_price=104,
                    submitted_at=timestamp,
                    position_owner="wca",
                    exit_owner="wca",
                    local_order_id="wca-protection-stop-order-id",
                    remaining_quantity=10,
                    created_at=timestamp,
                    updated_at=timestamp,
                    decision_id="local-protection-decision",
                    idempotency_key="local-protection-key",
                ),
            ),
        )
        local_account.mark_to_market(symbol="SPY", mark_price=101, marked_at=timestamp)
        local_account.persist(repository, symbol="SPY", timestamp=timestamp)
        repository.write_broker_account_snapshot(
            BrokerAccountSnapshot(
                accountId="paper",
                equity=1,
                buyingPower=1,
                realizedPnlToday=-999,
                positions=[
                    BrokerPositionState(
                        algorithmId="weighted_voting",
                        symbol="SPY",
                        side=Signal.BUY,
                        quantity=999,
                        averageEntryPrice=1,
                        markPrice=1,
                    )
                ],
                pendingOrders=[],
                partiallyFilledOrders=[],
                observedAt=timestamp + timedelta(seconds=1),
                sessionDate=timestamp.date(),
                sourceAuthority="broker",
                positionsReconciled=True,
                openOrdersReconciled=True,
            ),
            cash=1,
            configuration_version=default_wca_configuration().configuration_version,
            run_id="phase2-misleading-broker-state",
        )

        state = load_state(repository, snapshot, runtime_mode=WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER, market_data={"price": 102})

        self.assertTrue(state.fresh)
        self.assertEqual(state.broker_source_authority, WCA_LOCAL_PAPER_SOURCE_AUTHORITY)
        self.assertEqual(state.local_account["starting_balance"], 50_000)
        self.assertEqual(state.cash, 49_000)
        self.assertEqual(state.equity, 50_020)
        self.assertEqual(state.buying_power, 48_750)
        self.assertEqual(state.inventory["quantity"], 10)
        self.assertEqual(state.inventory["average_entry"], 100)
        self.assertEqual(state.inventory["stop"], 98)
        self.assertEqual(state.inventory["target"], 104)
        self.assertEqual(state.risk["reserved_risk"], 250)
        self.assertEqual(state.risk["trades_today"], 2)
        self.assertEqual(state.risk["circuit_breaker"], "closed")
        self.assertFalse(state.risk["cooldown"]["active"])
        self.assertEqual(len(state.orders["pending_entries"]), 1)
        self.assertEqual(len(state.orders["protective_orders"]), 1)
        self.assertEqual(state.current_broker_positions[0]["algorithmId"], "wca")
        self.assertNotIn("wca.runtime_state.shared_physical_account_position_conflict", state.reason_codes)

    def test_missing_state_blocks_entries_and_persists_hold_decision(self) -> None:
        repository, snapshot = seeded_repository(seed_runtime_state=False)
        result = run_worker(repository, snapshot, "phase2-missing")

        self.assertEqual(result["workers"]["decision_worker"]["status"], "blocked")
        self.assertIn("wca.runtime_state.inventory_missing", result["workers"]["decision_worker"]["reasonCodes"])
        decision = persisted_decision(repository, "wca-decision-phase2-missing")
        self.assertEqual(decision["aggregation"]["post_local_gate_decision"], "HOLD")
        self.assertTrue(decision["authoritative_state_hash"])

    def test_stale_state_blocks_entries(self) -> None:
        repository, snapshot = seeded_repository(broker_observed_offset_seconds=600)
        result = run_worker(repository, snapshot, "phase2-stale", max_state_age_seconds=60)

        self.assertEqual(result["workers"]["decision_worker"]["status"], "blocked")
        self.assertIn("wca.runtime_state.broker_snapshot_stale", result["workers"]["decision_worker"]["reasonCodes"])

    def test_caller_provided_inventory_cannot_override_repository_state(self) -> None:
        repository, snapshot = seeded_repository(open_quantity=0)
        result = run_worker(repository, snapshot, "phase2-override", event_payload={"current_quantity": 99, "position": "BUY"})

        self.assertEqual(result["workers"]["decision_worker"]["status"], "completed")
        decision = persisted_decision(repository, "wca-decision-phase2-override")
        self.assertTrue(decision["authoritative_state_hash"])
        self.assertNotIn("current_quantity", decision["market_snapshot"].get("payload", {}))

    def test_another_algorithm_position_cannot_appear_in_wca_local_inventory(self) -> None:
        repository, snapshot = seeded_repository(open_quantity=0, include_other_algorithm_broker_position=True)
        state = load_state(repository, snapshot)

        self.assertEqual(state.current_quantity, 0)
        self.assertEqual(state.wca_inventory["open_quantity"], 0)
        self.assertEqual(state.current_broker_positions, ())
        self.assertIn("wca.runtime_state.shared_physical_account_position_conflict", state.reason_codes)

    def test_every_runtime_decision_records_authoritative_state_version(self) -> None:
        repository, snapshot = seeded_repository()
        result = run_worker(repository, snapshot, "phase2-version")

        self.assertEqual(result["workers"]["decision_worker"]["status"], "completed")
        decision = persisted_decision(repository, "wca-decision-phase2-version")
        self.assertEqual(decision["authoritative_state_version"], "wca_authoritative_runtime_state_v1")
        self.assertTrue(decision["authoritative_state_hash"])
        self.assertIn("wca.runtime_state.fresh", decision["authoritative_state_reason_codes"])


def seeded_repository(
    *,
    seed_runtime_state: bool = True,
    open_quantity: int = 0,
    trades_completed_today: int = 0,
    daily_loss: float = 0.0,
    buying_power: float = 100_000,
    broker_observed_offset_seconds: int = 1,
    include_other_algorithm_broker_position: bool = False,
) -> tuple[WcaSqliteRepository, object]:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    base_configuration = default_wca_configuration()
    configuration = base_configuration.model_copy(
        update={
            "aggregation": base_configuration.aggregation.model_copy(
                update={"minimum_score": 0.1, "buy_threshold": 0.1}
            )
        }
    )
    configuration_payload = configuration.model_dump(mode="json")
    configuration_payload["content_hash"] = ""
    configuration = validate_wca_configuration(configuration_payload)
    snapshot = market_snapshot()
    repository.initialize_defaults(
        symbol="SPY",
        configuration=configuration.model_dump(mode="json"),
        weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="phase2.weights.v1"),
        engine_version="phase2-test",
    )
    if seed_runtime_state:
        seed_authoritative_state(
            repository,
            snapshot,
            open_quantity=open_quantity,
            trades_completed_today=trades_completed_today,
            daily_loss=daily_loss,
            buying_power=buying_power,
            broker_observed_offset_seconds=broker_observed_offset_seconds,
            include_other_algorithm_broker_position=include_other_algorithm_broker_position,
        )
    return repository, snapshot


def seed_authoritative_state(
    repository: WcaSqliteRepository,
    snapshot,
    *,
    open_quantity: int = 0,
    trades_completed_today: int = 0,
    daily_loss: float = 0.0,
    buying_power: float = 100_000,
    broker_observed_offset_seconds: int = 1,
    include_other_algorithm_broker_position: bool = False,
) -> None:
    timestamp = snapshot.decision_timestamp.astimezone(UTC)
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"phase2-reset-{uuid4().hex}",
            event_type="DAILY_STATE_RESET",
            broker_account_id="paper",
            symbol="SPY",
            event_timestamp=(timestamp - timedelta(seconds=10)).isoformat(),
            trade_date=timestamp.date().isoformat(),
            reconciliation_watermark="phase2-reconciled",
        )
    )
    if open_quantity:
        fill_id = f"phase2-fill-{uuid4().hex}"
        order_intent_id = f"phase2-intent-{uuid4().hex}"
        opened_at = (timestamp - timedelta(seconds=5)).isoformat()
        repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"phase2-open-{uuid4().hex}",
                event_type="FILL_RECEIVED",
                broker_account_id="paper",
                symbol="SPY",
                event_timestamp=opened_at,
                trade_date=timestamp.date().isoformat(),
                order_intent_id=order_intent_id,
                fill_id=fill_id,
                side="BUY",
                quantity=open_quantity,
                filled_quantity=open_quantity,
                fill_price=100.0,
                reconciliation_watermark="phase2-reconciled",
                configuration_version=default_wca_configuration().configuration_version,
                decision_id="phase2-entry-decision",
                run_id="phase2-entry-run",
                payload={
                    "order_intent_id": order_intent_id,
                    "decision_id": "phase2-entry-decision",
                    "entry_price": 100.0,
                    "stop_price": 98.5,
                    "target_price": 103.0,
                    "opened_at": opened_at,
                    "position_effect": "entry",
                },
            )
        )
        with sqlite3.connect(repository.path) as conn:
            conn.execute(
                """
                INSERT INTO wca_owned_lots (
                    lot_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, position_id,
                    side, quantity, status, payload_json
                )
                VALUES (?, 'wca', 'paper', 'SPY', ?, ?, 'phase2-test', 'phase2-snapshot',
                        'phase2-entry-decision', 'phase2-entry-run', 'phase2-position', 'BUY', ?, 'open', ?)
                """,
                (
                    f"wca-lot-{fill_id}",
                    opened_at,
                    default_wca_configuration().configuration_version,
                    open_quantity,
                    json.dumps(
                        {
                            "order_intent_id": order_intent_id,
                            "decision_id": "phase2-entry-decision",
                            "entry_price": 100.0,
                            "stop_price": 98.5,
                            "target_price": 103.0,
                            "opened_at": opened_at,
                        }
                    ),
                ),
            )
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_daily_state
            SET trades_completed_today = ?, realized_pnl_today = ?, daily_loss = ?
            WHERE algorithm_id = 'wca' AND broker_account_id = 'paper' AND symbol = 'SPY'
            """,
            (trades_completed_today, -daily_loss, daily_loss),
        )
    positions = []
    if include_other_algorithm_broker_position:
        positions.append(
            BrokerPositionState(
                algorithmId="weighted_voting",
                symbol="SPY",
                side=Signal.BUY,
                quantity=20,
                averageEntryPrice=100,
                markPrice=101,
            )
        )
    repository.write_broker_account_snapshot(
        BrokerAccountSnapshot(
            accountId="paper",
            equity=100_000,
            buyingPower=buying_power,
            realizedPnlToday=-daily_loss,
            positions=positions,
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=timestamp - timedelta(seconds=broker_observed_offset_seconds),
            sessionDate=timestamp.date(),
            sourceAuthority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            positionsReconciled=True,
            openOrdersReconciled=True,
        ),
        cash=buying_power,
        configuration_version=default_wca_configuration().configuration_version,
        run_id="phase2-broker-state",
    )
    repository.write_broker_reconciliation(
        WcaBrokerReconciliationResult(
            reconciliation_id=f"phase2-clean-reconciliation-{uuid4().hex}",
            account_id="paper",
            evaluated_at=timestamp - timedelta(seconds=1),
            intents_checked=0,
            broker_open_orders_checked=0,
            broker_positions_checked=0,
            discrepancies=(),
            hard_operational_warning=False,
            reason_codes=("wca.broker_reconciliation.clean",),
        )
    )


def load_state(repository: WcaSqliteRepository, snapshot, *, runtime_mode: WcaRuntimeMode | str | None = None, market_data: dict | None = None):
    return load_wca_authoritative_runtime_state(
        repository,
        broker_account_id="paper",
        symbol="SPY",
        state_timestamp=snapshot.decision_timestamp,
        maximum_permitted_state_age_seconds=120,
        runtime_mode=runtime_mode,
        market_data=market_data,
    )


def run_decision_from_state(repository: WcaSqliteRepository, snapshot, state, signal: WcaSide):
    configuration = repository.read_active_configuration()
    weights = repository.read_active_weights(as_of=snapshot.decision_timestamp)
    assert configuration is not None
    assert weights is not None
    return run_wca_paper_pipeline_adapter(
        WcaExecutionPipelineInput(
            run_id="phase2-run",
            decision_id=f"phase2-decision-{uuid4().hex}",
            order_intent_id=f"phase2-intent-{uuid4().hex}",
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=weights,
            account_id=state.broker_account_id,
            trades_today=state.daily_trade_count,
            open_position=state.to_open_position(),
            realized_daily_loss=state.daily_loss,
            account_equity=state.equity,
            available_buying_power=state.buying_power,
            remaining_allocated_risk_budget=state.remaining_portfolio_risk,
            total_account_exposure_snapshot=state.global_risk.get("riskState", {}),
            current_wca_attributed_exposure=(state.current_quantity or 0) * (state.average_entry_price or 0),
            authoritative_state_version=state.state_version,
            authoritative_state_hash=state.state_hash,
            authoritative_state_reason_codes=state.reason_codes,
        ),
        voters=(FakeVoter("C1", signal), FakeVoter("C4", signal), FakeVoter("C7", signal)),
    ).decision


def run_worker(repository: WcaSqliteRepository, snapshot, event_id: str, *, max_state_age_seconds: int = 120, event_payload: dict | None = None):
    runtime_repository = WcaRuntimeRepository(repository)
    event = WcaFinalizedBarEvent(
        event_id=event_id,
        symbol="SPY",
        finalized_candle_timestamp=snapshot.decision_timestamp,
        data_manifest_hash=f"manifest-{event_id}",
        publication_timestamp=snapshot.decision_timestamp + timedelta(seconds=1),
        source="phase2-test",
        snapshot=snapshot,
        payload=event_payload or {},
    )
    runtime_repository.publish_finalized_bar_event(event, now=snapshot.decision_timestamp + timedelta(seconds=1))
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(max_lag_seconds=99_999_999, max_state_age_seconds=max_state_age_seconds),
        owner_id=f"{event_id}-owner",
    )
    return supervisor.run_once()


def persisted_decision(repository: WcaSqliteRepository, decision_id: str) -> dict:
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute("SELECT payload_json FROM wca_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
    assert row is not None
    return json.loads(row[0])


def gate_reasons(decision) -> tuple[str, ...]:
    return tuple(code for gate in decision.local_gates for code in gate.reason_codes)


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-phase2-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
