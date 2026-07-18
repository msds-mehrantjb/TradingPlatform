from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from pydantic import ValidationError

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS,
    WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS,
    WCA_SHARED_PLATFORM_COMPONENT_IDS,
    WCA_SHARED_PLATFORM_COMPONENT_INVENTORY,
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
        self.assertEqual(rules["Broker API client"], "Executes approved proposals only.")
        self.assertEqual(rules["Global account-risk engine"], "May reduce or reject WCA risk.")
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
