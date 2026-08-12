from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from backend.app.algorithms.wca.global_risk import SharedGlobalRiskReservationEngine, WcaGlobalRiskAdapter, build_wca_global_risk_proposal
from backend.app.algorithms.wca.local_paper_broker import WcaLocalPaperBroker
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerOutboxAdapter
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.tests.test_wca_step10_paper_broker_outbox import fill_local_entry, reserve
from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS,
    WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS,
    WCA_SHARED_PLATFORM_COMPONENT_IDS,
    WCA_SHARED_PLATFORM_COMPONENT_INVENTORY,
    WcaOrderStatus,
    WcaSide,
)
from backend.app.risk import (
    GLOBAL_GATE_ENGINE_VERSION,
    GlobalGateAccountState,
    GlobalGateDecision,
    GlobalGateEngine,
    GlobalGateInput,
    GlobalGateLedgerState,
    GlobalGateMarketState,
    GlobalGateOrderSide,
    GlobalGatePendingOrderState,
    GlobalGatePolicy,
    GlobalGatePositionState,
    GlobalGateProposedOrder,
    GlobalGateResult,
    build_global_gate_idempotency_key,
)


class WcaStep12GlobalGateEngineTests(unittest.TestCase):
    def test_wca_shared_platform_component_inventory_is_explicit(self) -> None:
        self.assertEqual(
            WCA_SHARED_PLATFORM_COMPONENT_IDS,
            {
                "raw_and_normalized_market_data_services",
                "clock_and_market_calendar_service",
                "account_equity_and_buying_power_snapshot",
                "broker_api_client",
                "global_account_risk_engine",
                "global_portfolio_risk_ledger",
                "global_emergency_controls",
                "idempotency_service",
                "broker_reconciliation_infrastructure",
                "database_connection_path_utilities",
                "logging_metrics_and_tracing",
                "api_framework_and_authentication",
            },
        )
        rules = {component.shared_component: component.sharing_rule for component in WCA_SHARED_PLATFORM_COMPONENT_INVENTORY}
        self.assertEqual(rules["Raw and normalized market-data services"], "Read-only input.")
        self.assertEqual(
            rules["Account-equity and buying-power snapshot"],
            "Read-only legacy/global observation; local automatic paper uses WCA local account.",
        )
        self.assertEqual(rules["Broker API client"], "Legacy broker modes only; local automatic paper uses WcaLocalPaperBroker.")
        self.assertEqual(
            rules["Global account-risk engine"],
            "May reduce or reject WCA proposals only; must not own WCA local account or inventory.",
        )
        self.assertEqual(rules["Global portfolio-risk ledger"], "Read-only aggregate observation; must preserve algorithm attribution.")
        self.assertEqual(rules["Global emergency controls"], "May block new entries or emit explicit WCA risk-reduction commands.")
        self.assertEqual(rules["Broker reconciliation infrastructure"], "Legacy broker modes only; must preserve WCA ownership.")
        self.assertEqual(rules["Idempotency service"], "Must include WCA algorithm and intent identifiers.")
        self.assertEqual(rules["Logging, metrics, and tracing"], "Must tag records with algorithm_id=wca.")
        self.assertEqual(rules["API framework and authentication"], "Transport only.")

    def test_shared_global_risk_constraints_are_one_way_and_cannot_rewrite_wca_state(self) -> None:
        self.assertEqual(
            WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS,
            {
                "wca_signals",
                "strategy_confidence",
                "strategy_weights",
                "wca_thresholds",
                "wca_dynamic_profiles",
                "wca_stop_logic",
                "wca_backtest_results",
                "wca_local_cash",
                "wca_local_equity",
                "wca_local_buying_power",
                "wca_local_positions",
                "wca_local_lots",
                "wca_local_orders",
                "wca_local_fills",
                "wca_inventory_projection",
                "wca_inventory_ledger",
                "wca_daily_state",
                "wca_reserved_risk",
                "wca_trade_history",
                "other_algorithm_inventory",
            },
        )
        self.assertEqual(WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS, {"reduce_wca_risk", "reject_wca_entry", "block_new_entries"})

        order = proposal(quantity=100)
        result = GlobalGateEngine().evaluate(global_gate_input(proposed_order=order, account_state=account(available_buying_power=5_000)))

        self.assertEqual(result.algorithm_id, WCA_ALGORITHM_ID)
        self.assertEqual(result.requested_quantity, 100)
        self.assertEqual(result.approved_quantity, 50)
        self.assertEqual(order.quantity, 100)
        self.assertEqual(order.stop_price, 98)
        for forbidden in WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS:
            self.assertFalse(hasattr(result, forbidden), forbidden)
        with self.assertRaises(ValidationError):
            GlobalGateResult(
                decision=GlobalGateDecision.ALLOW,
                algorithm_id=WCA_ALGORITHM_ID,
                proposed_quantity=10,
                allowed_quantity=10,
                requested_quantity=10,
                approved_quantity=10,
                strategy_weights={"C1": 1.0},
            )

    def test_global_gate_idempotency_key_changes_by_algorithm_and_intent(self) -> None:
        order = proposal()
        base_key = build_global_gate_idempotency_key(order)

        self.assertNotEqual(base_key, build_global_gate_idempotency_key(order.model_copy(update={"algorithm_id": "other_algorithm"})))
        self.assertNotEqual(base_key, build_global_gate_idempotency_key(order.model_copy(update={"order_intent_id": "intent-2"})))

    def test_two_algorithms_cannot_exceed_shared_spy_exposure_limit(self) -> None:
        gate_input = global_gate_input(
            proposed_order=proposal(quantity=200, limit_price=100),
            ledger_state=GlobalGateLedgerState(
                positions=(
                    GlobalGatePositionState(
                        algorithm_id="weighted_voting",
                        symbol="SPY",
                        side=GlobalGateOrderSide.BUY,
                        quantity=900,
                        market_value=90_000,
                        open_stop_risk=900,
                    ),
                )
            ),
            policy=policy(max_symbol_exposure=100_000),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        self.assertEqual(result.decision, GlobalGateDecision.REDUCE_QUANTITY.value)
        self.assertEqual(result.requested_quantity, 200)
        self.assertEqual(result.approved_quantity, 100)
        self.assertIn("global_gate.quantity.reduced_by_symbol_exposure", result.warnings)
        self.assertEqual(result.account_ledger.symbol_exposure["SPY"], 90_000)

    def test_duplicate_idempotency_key_blocks_duplicate_broker_orders(self) -> None:
        order = proposal()
        key = build_global_gate_idempotency_key(order)
        gate_input = global_gate_input(
            proposed_order=order,
            ledger_state=GlobalGateLedgerState(completed_idempotency_keys=(key,)),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        self.assertEqual(result.decision, GlobalGateDecision.REJECT_NEW_ENTRY.value)
        self.assertEqual(result.approved_quantity, 0)
        self.assertIn("global_gate.order_flow.duplicate_order", result.blockers)
        self.assertEqual(result.idempotency_key, key)

    def test_entry_disabled_mode_still_permits_risk_reducing_exits(self) -> None:
        gate_input = global_gate_input(
            proposed_order=proposal(side=GlobalGateOrderSide.SELL, quantity=25, is_risk_reducing_exit=True),
            policy=policy(master_entry_enabled=False),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        self.assertEqual(result.decision, GlobalGateDecision.ALLOW.value)
        self.assertFalse(result.allow_new_entries)
        self.assertTrue(result.allow_risk_reducing_exits)
        self.assertEqual(result.approved_quantity, 25)
        self.assertNotIn("global_gate.entry.master_switch_off", result.blockers)

    def test_global_gates_reduce_quantity_but_do_not_change_order_side_or_strategy_state(self) -> None:
        order = proposal(side=GlobalGateOrderSide.BUY, quantity=100, limit_price=100)
        gate_input = global_gate_input(
            proposed_order=order,
            account_state=account(available_buying_power=5_000),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        self.assertEqual(order.side, GlobalGateOrderSide.BUY.value)
        self.assertEqual(result.approved_quantity, 50)
        self.assertLess(result.approved_quantity, result.requested_quantity)
        self.assertFalse(hasattr(result, "strategy_weights"))
        self.assertFalse(hasattr(result, "internal_signal"))

    def test_entry_gate_blockers_cover_operational_data_reconciliation_and_geometry(self) -> None:
        gate_input = global_gate_input(
            proposed_order=proposal(stop_price=102, target_price=99),
            account_state=account(
                broker_connected=False,
                broker_market_clock_open=False,
                new_entry_cutoff_reached=True,
                status="RESTRICTED",
                realized_pl=-1_000,
                daily_loss_limit=500,
                equity=95_000,
                high_water_equity=100_000,
                drawdown_limit=1_000,
            ),
            market_state=market(
                authoritative_broker_market_clock_open=False,
                market_data_fresh=False,
                market_data_complete=False,
                symbol_halted=True,
                luld_active=True,
                broker_position_reconciled=False,
                broker_open_orders_reconciled=False,
                spread=1.25,
                liquidity=10,
                estimated_slippage=0.75,
                high_impact_event_blackout=True,
            ),
            policy=policy(
                absolute_spread_ceiling=0.5,
                absolute_liquidity_floor=100,
                slippage_ceiling=0.25,
                high_impact_event_blackout_enabled=True,
            ),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        expected = {
            "global_gate.entry.broker_market_clock_closed",
            "global_gate.entry.new_entry_cutoff",
            "global_gate.broker.connectivity_unavailable",
            "global_gate.account.status_not_active",
            "global_gate.market_data.stale",
            "global_gate.market_data.incomplete",
            "global_gate.market.symbol_halt",
            "global_gate.market.luld_active",
            "global_gate.reconciliation.position_mismatch",
            "global_gate.reconciliation.open_order_mismatch",
            "global_gate.account.daily_loss_limit",
            "global_gate.account.drawdown_limit",
            "global_gate.market.absolute_spread_ceiling",
            "global_gate.market.absolute_liquidity_floor",
            "global_gate.market.slippage_ceiling",
            "global_gate.order.final_geometry_invalid",
            "global_gate.event.high_impact_blackout",
        }
        self.assertEqual(result.decision, GlobalGateDecision.REJECT_NEW_ENTRY.value)
        self.assertTrue(expected.issubset(set(result.blockers)))

    def test_frontend_cannot_forge_quantity_increase_or_missing_backend_source(self) -> None:
        with self.assertRaisesRegex(ValidationError, "global gates cannot increase"):
            GlobalGateResult(
                decision=GlobalGateDecision.ALLOW,
                algorithm_id="wca",
                proposed_quantity=10,
                allowed_quantity=11,
                requested_quantity=10,
                approved_quantity=11,
            )

        result = GlobalGateEngine().evaluate(global_gate_input())
        self.assertEqual(result.source, "backend_global_gate_engine")
        self.assertIn(GLOBAL_GATE_ENGINE_VERSION, result.reason_codes)

    def test_account_wide_ledger_aggregates_across_all_algorithms(self) -> None:
        gate_input = global_gate_input(
            ledger_state=GlobalGateLedgerState(
                positions=(
                    GlobalGatePositionState(
                        algorithm_id="wca",
                        symbol="SPY",
                        side=GlobalGateOrderSide.BUY,
                        quantity=10,
                        market_value=1_000,
                        open_stop_risk=100,
                    ),
                    GlobalGatePositionState(
                        algorithm_id="weighted_voting",
                        symbol="QQQ",
                        side=GlobalGateOrderSide.SELL,
                        quantity=5,
                        market_value=500,
                        open_stop_risk=50,
                    ),
                ),
                pending_orders=(
                    GlobalGatePendingOrderState(
                        algorithm_id="meta_strategy",
                        symbol="SPY",
                        side=GlobalGateOrderSide.BUY,
                        quantity=2,
                        reserved_buying_power=200,
                        pending_risk=20,
                        order_intent_id="pending-1",
                        idempotency_key="pending-key",
                    ),
                ),
            ),
        )

        result = GlobalGateEngine().evaluate(gate_input)

        self.assertEqual(result.account_ledger.gross_exposure, 1_500)
        self.assertEqual(result.account_ledger.net_exposure, 500)
        self.assertEqual(result.account_ledger.symbol_exposure, {"SPY": 1_000, "QQQ": 500})
        self.assertEqual(result.account_ledger.open_stop_risk, 150)
        self.assertEqual(result.account_ledger.pending_order_risk, 20)
        self.assertEqual(result.account_ledger.reserved_buying_power, 200)


    def test_shared_wca_global_risk_engine_does_not_mutate_local_account_or_inventory(self) -> None:
        repository = repository_for_step12()
        before = wca_local_execution_counts(repository)
        proposal = build_wca_global_risk_proposal(
            account_id="wca-local-step12",
            symbol="SPY",
            side=WcaSide.BUY,
            requested_quantity=10,
            requested_risk=20.0,
            stop_distance=2.0,
            expected_holding_period_seconds=900,
            current_wca_attributed_exposure=1_000.0,
            total_account_exposure_snapshot={
                "global_gate_quantity_cap": 3,
                "maximum_open_risk_dollars": 6.0,
                "current_open_risk_dollars": 0.0,
                "reserved_open_risk_dollars": 0.0,
            },
            configuration_version="config-v1",
            configuration_hash="hash-v1",
            decision_id="decision-step12-global",
            idempotency_key="idem-step12-global",
        )

        decision = WcaGlobalRiskAdapter(SharedGlobalRiskReservationEngine()).evaluate_wca_proposal(proposal)
        after = wca_local_execution_counts(repository)

        self.assertEqual(decision.algorithm_id, WCA_ALGORITHM_ID)
        self.assertEqual(decision.requested_quantity, 10)
        self.assertEqual(decision.approved_quantity, 3)
        self.assertEqual(decision.approved_risk, 6.0)
        self.assertEqual(before, after)
        proposal_fields = set(proposal.__class__.model_fields)
        decision_fields = set(decision.__class__.model_fields)
        self.assertTrue({"current_wca_attributed_exposure", "total_account_exposure_snapshot"}.issubset(proposal_fields))
        self.assertFalse({"cash", "buying_power", "equity", "positions", "lots", "open_orders"} & proposal_fields)
        self.assertFalse({"cash", "buying_power", "equity", "positions", "lots", "open_orders"} & decision_fields)

    def test_wca_local_fill_after_global_gate_keeps_execution_state_attributed_to_wca(self) -> None:
        repository = repository_for_step12()
        _, request = reserve(repository, suffix="step12-global-isolation")
        broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)

        result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step12")
        fills = fill_local_entry(repository, broker, request)
        algorithm_ids = wca_algorithm_ids_by_table(
            repository,
            (
                "wca_local_paper_account",
                "wca_inventory_projection",
                "wca_owned_lots",
                "wca_local_positions",
                "wca_local_lots",
                "wca_local_orders",
                "wca_local_fills",
                "wca_inventory_ledger",
            ),
        )

        self.assertEqual(result.state, WcaOrderStatus.ACKNOWLEDGED)
        self.assertEqual(len(fills), 1)
        for table, ids in algorithm_ids.items():
            self.assertEqual(ids, (WCA_ALGORITHM_ID,), table)

def repository_for_step12() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-step12-{uuid4().hex}.sqlite'}")


def wca_local_execution_counts(repository: WcaSqliteRepository) -> dict[str, int]:
    tables = (
        "wca_local_paper_account",
        "wca_inventory_projection",
        "wca_owned_lots",
        "wca_local_positions",
        "wca_local_lots",
        "wca_local_orders",
        "wca_local_fills",
        "wca_inventory_ledger",
        "wca_daily_state",
    )
    with sqlite3.connect(repository.path) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def wca_algorithm_ids_by_table(repository: WcaSqliteRepository, tables: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    with sqlite3.connect(repository.path) as conn:
        return {
            table: tuple(row[0] for row in conn.execute(f"SELECT DISTINCT algorithm_id FROM {table} ORDER BY algorithm_id").fetchall())
            for table in tables
        }


def global_gate_input(
    *,
    proposed_order: GlobalGateProposedOrder | None = None,
    account_state: GlobalGateAccountState | None = None,
    market_state: GlobalGateMarketState | None = None,
    ledger_state: GlobalGateLedgerState | None = None,
    policy: GlobalGatePolicy | None = None,
) -> GlobalGateInput:
    return GlobalGateInput(
        proposed_order=proposed_order or proposal(),
        account_state=account_state or account(),
        market_state=market_state or market(),
        ledger_state=ledger_state or GlobalGateLedgerState(),
        policy=policy or globals()["policy"](),
        evaluation_timestamp=NOW,
    )


def proposal(
    *,
    side: GlobalGateOrderSide = GlobalGateOrderSide.BUY,
    quantity: int = 100,
    limit_price: float = 100,
    stop_price: float | None = 98,
    target_price: float | None = 104,
    is_risk_reducing_exit: bool = False,
) -> GlobalGateProposedOrder:
    if side == GlobalGateOrderSide.SELL and stop_price == 98 and target_price == 104:
        stop_price, target_price = 102, 96
    return GlobalGateProposedOrder(
        account_id="paper-account",
        algorithm_id="wca",
        symbol="SPY",
        side=side,
        quantity=quantity,
        order_intent_id="intent-1",
        decision_id="decision-1",
        decision_timestamp=NOW,
        configuration_version="config-v1",
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=target_price,
        planned_risk=quantity * 2,
        is_risk_reducing_exit=is_risk_reducing_exit,
    )


def account(**overrides) -> GlobalGateAccountState:
    payload = {
        "account_id": "paper-account",
        "account_snapshot_id": "acct-snap-1",
        "equity": 100_000,
        "high_water_equity": 100_000,
        "available_buying_power": 100_000,
        "daily_loss_limit": 5_000,
        "drawdown_limit": 10_000,
    }
    payload.update(overrides)
    return GlobalGateAccountState(**payload)


def market(**overrides) -> GlobalGateMarketState:
    payload = {
        "market_snapshot_id": "mkt-snap-1",
        "spread": 0.05,
        "liquidity": 50_000,
        "estimated_slippage": 0.01,
    }
    payload.update(overrides)
    return GlobalGateMarketState(**payload)


def policy(**overrides) -> GlobalGatePolicy:
    payload = {
        "max_symbol_exposure": 0,
        "max_gross_exposure": 0,
        "max_net_exposure": 0,
        "max_open_stop_risk": 0,
        "max_open_orders": 0,
        "absolute_spread_ceiling": 0.25,
        "absolute_liquidity_floor": 1_000,
        "slippage_ceiling": 0.10,
    }
    payload.update(overrides)
    return GlobalGatePolicy(**payload)


NOW = datetime(2026, 7, 15, 15, 30, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
