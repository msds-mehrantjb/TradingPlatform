from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.local_paper_account import RegimeLocalPaperAccount
from backend.app.algorithms.regime.local_paper_broker import RegimeLocalPaperBroker
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.reconciliation import run_regime_broker_reconciliation
from backend.app.algorithms.regime.runtime_factory import build_regime_paper_runtime
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.config import ApplicationConfig, Settings
from backend.app.domain.models import Signal
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_local_paper_broker"
NOW = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)


def test_regime_local_paper_broker_fails_closed_when_account_missing() -> None:
    repository, identity = _repository(seed_account=False)
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)

    order = broker.submit_order(_order(limitPrice=100.0, quantity=1, clientOrderId="missing-account"))

    assert order["status"] == "REJECTED"
    assert order["rejectedReason"] == "regime.local_paper.account_missing_fail_closed"
    assert broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0}) == ()
    assert repository.read_local_paper_account_snapshot(identity) is None
    assert broker.get_open_orders("SPY") == []


def test_regime_local_paper_broker_limit_waits_for_executable_quote_and_persists_fill() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)

    order = broker.submit_order(_order(limitPrice=100.0, quantity=10))
    assert order["status"] == "ACCEPTED"
    reserved = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    assert reserved.reservedCash == 1_000.0
    assert reserved.availableBuyingPower == 99_000.0
    assert broker.refresh_order(order["clientOrderId"]) is None

    assert broker.process_market_update({"symbol": "SPY", "bid": 100.8, "ask": 101.0}) == ()
    fills = broker.process_market_update({"symbol": "SPY", "bid": 99.95, "ask": 100.0})

    assert len(fills) == 1
    assert fills[0].executionMode == "LOCAL_PAPER"
    assert fills[0].algorithmId == "regime"
    assert fills[0].accountId == identity["accountId"]
    assert fills[0].filledQuantity == 10
    assert broker.get_open_orders("SPY") == []

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    assert account.cash == 99_000.0
    assert account.reservedCash == 0.0
    assert account.availableBuyingPower == 99_000.0
    assert account.positions[0].quantity == 10
    assert inventory["quantity"] == 10
    assert inventory["averageEntryPrice"] == 100.0
    assert inventory["marketValue"] == 1_000.0
    assert inventory["lastFillId"] == fills[0].executionCostBreakdown["fillId"]

    restored_broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=1)
    assert restored_broker.get_fills(order["clientOrderId"])[0]["algorithmId"] == "regime"
    assert restored_broker.refresh_order(order["clientOrderId"]).filledQuantity == 10


def test_regime_local_paper_buy_100_spy_at_500_updates_cash_quantity_and_average_price() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)

    order = broker.submit_order(_order(clientOrderId="buy-100-spy", orderIntentId="buy-100-spy-intent", limitPrice=500.0, quantity=100))
    fills = broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    assert order["status"] == "ACCEPTED"
    assert len(fills) == 1
    assert account.cash == 50_000.0
    assert account.availableBuyingPower == 50_000.0
    assert inventory["quantity"] == 100
    assert inventory["averageEntryPrice"] == 500.0
    assert account.positions[0].quantity == 100
    assert account.positions[0].averageEntryPrice == 500.0


def test_regime_local_paper_sell_100_spy_at_505_closes_position_and_records_pnl_after_costs() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000, commission_per_share=0.01)
    broker.submit_order(_order(clientOrderId="sell-flow-entry", orderIntentId="sell-flow-entry-intent", limitPrice=500.0, quantity=100))
    broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})

    exit_order = broker.submit_order(_order(clientOrderId="sell-flow-exit", orderIntentId="sell-flow-exit-intent", side="SELL", limitPrice=505.0, quantity=100, positionEffect="exit_long"))
    fills = broker.process_market_update({"symbol": "SPY", "bid": 505.0, "ask": 505.1})

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    assert exit_order["status"] == "ACCEPTED"
    assert len(fills) == 1
    assert inventory["quantity"] == 0
    assert account.positions == ()
    assert account.cash == 100_498.0
    assert account.realizedPnl == 499.0
    assert account.dailyRealizedPnl == 499.0
    assert account.feesPaid == 2.0


def test_regime_local_paper_partial_fills_accumulate_without_duplicate_cash_deduction() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(
        repository=repository,
        identity=identity,
        starting_balance=100_000,
        maximum_fill_quantity=40,
        allow_partial_fills=True,
    )
    broker.submit_order(_order(clientOrderId="partial-entry", orderIntentId="partial-entry-intent", limitPrice=500.0, quantity=100))

    first = broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0, "volume": 1_000})
    broker.maximum_fill_quantity = 60
    second = broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0, "volume": 1_000})
    replay = broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0, "volume": 1_000})

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    assert [fill.filledQuantity for fill in first + second] == [40, 60]
    assert replay == ()
    assert inventory["quantity"] == 100
    assert account.cash == 50_000.0
    assert account.positions[0].quantity == 100
    assert broker.get_open_orders("SPY") == []


def test_regime_local_paper_replaying_same_fill_id_has_zero_effect() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    order = broker.submit_order(_order(clientOrderId="duplicate-fill-entry", orderIntentId="duplicate-fill-entry-intent", limitPrice=500.0, quantity=10))
    fills = broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})
    fill_record = broker.get_fills(order["clientOrderId"])[0]
    before = repository.read_local_paper_account_snapshot(identity)

    result = repository.apply_local_paper_fill_transaction(
        identity,
        order={**order, "remainingQuantity": 0, "status": "FILLED"},
        fill={
            "algorithmId": "regime",
            "algorithmInstanceId": identity["algorithmInstanceId"],
            "accountId": identity["accountId"],
            "runtimeMode": identity["runtimeMode"],
            "symbol": identity["symbol"],
            "fillId": fill_record["fillId"],
            "side": "Buy",
            "filledQuantity": fills[0].filledQuantity,
            "averageFillPrice": fills[0].averageFillPrice,
            "commission": fills[0].commission,
            "fees": fills[0].regulatoryFees,
            "slippage": fills[0].totalExecutionCost - fills[0].commission - fills[0].regulatoryFees,
            "filledAt": fills[0].filledAt.isoformat().replace("+00:00", "Z"),
            "orderIntentId": order["orderIntentId"],
        },
        orders_snapshot=[{**order, "remainingQuantity": 0, "status": "FILLED"}],
        fills_snapshot=[fill_record],
    )
    after = repository.read_local_paper_account_snapshot(identity)

    assert result["duplicate"] is True
    assert after["cash"] == before["cash"]
    assert after["positions"] == before["positions"]
    assert after["fills"] == before["fills"]


def test_regime_local_paper_insufficient_buying_power_rejects_regime_order() -> None:
    repository, identity = _repository(initial_balance=10_000)
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=10_000)

    rejected = broker.submit_order(_order(clientOrderId="too-large", orderIntentId="too-large-intent", limitPrice=500.0, quantity=100))

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    assert rejected["status"] == "REJECTED"
    assert "available buying power" in rejected["rejectedReason"]
    assert account.cash == 10_000.0
    assert account.reservedCash == 0.0
    assert broker.get_open_orders("SPY") == []


def test_regime_local_paper_multiple_pending_orders_cannot_overcommit_cash() -> None:
    repository, identity = _repository(initial_balance=10_000)
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=10_000)

    first = broker.submit_order(_order(clientOrderId="commit-a", orderIntentId="commit-a-intent", limitPrice=100.0, quantity=70))
    second = broker.submit_order(_order(clientOrderId="commit-b", orderIntentId="commit-b-intent", limitPrice=100.0, quantity=50))

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    assert first["status"] == "ACCEPTED"
    assert second["status"] == "REJECTED"
    assert account.cash == 10_000.0
    assert account.reservedCash == 7_000.0
    assert account.availableBuyingPower == 3_000.0
    assert [order["clientOrderId"] for order in broker.get_open_orders("SPY")] == ["commit-a"]

def test_regime_local_paper_broker_pending_buy_orders_reserve_available_buying_power() -> None:
    repository, identity = _repository(initial_balance=10_000)
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=10_000)

    first = broker.submit_order(_order(clientOrderId="reserve-a", orderIntentId="reserve-a-intent", limitPrice=100.0, quantity=70))
    second = broker.submit_order(_order(clientOrderId="reserve-b", orderIntentId="reserve-b-intent", limitPrice=100.0, quantity=50))

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    assert first["status"] == "ACCEPTED"
    assert first["reservedCash"] == 7_000.0
    assert second["status"] == "REJECTED"
    assert "reserve more cash than available buying power" in second["rejectedReason"]
    assert account.cash == 10_000.0
    assert account.reservedCash == 7_000.0
    assert account.availableBuyingPower == 3_000.0
    assert [order["clientOrderId"] for order in broker.get_open_orders("SPY")] == ["reserve-a"]


def test_regime_local_paper_broker_cancel_releases_buy_order_reservation() -> None:
    repository, identity = _repository(initial_balance=10_000)
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=10_000)
    broker.submit_order(_order(clientOrderId="reserve-cancel", orderIntentId="reserve-cancel-intent", limitPrice=100.0, quantity=70))

    assert broker.cancel_order("reserve-cancel") is True

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    assert account.cash == 10_000.0
    assert account.reservedCash == 0.0
    assert account.availableBuyingPower == 10_000.0
    assert broker.get_open_orders("SPY") == []


def test_regime_local_paper_broker_sell_fill_updates_account_and_inventory_atomically() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000, commission_per_share=0.1)

    broker.submit_order(_order(clientOrderId="entry-atomic", orderIntentId="entry-intent", limitPrice=100.0, quantity=10))
    broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0})
    broker.submit_order(_order(clientOrderId="exit-atomic", orderIntentId="exit-intent", decisionId="exit-decision", side="SELL", limitPrice=103.0, quantity=10, positionEffect="exit_long"))
    fills = broker.process_market_update({"symbol": "SPY", "bid": 103.0, "ask": 103.2})

    assert len(fills) == 1
    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    positions = repository.latest_regime_positions(identity)
    assert account.cash == 100_028.0
    assert account.equity == 100_028.0
    assert account.realizedPnl == 29.0
    assert account.dailyRealizedPnl == 29.0
    assert account.feesPaid == 2.0
    assert account.positions == ()
    assert inventory["quantity"] == 0
    assert inventory["marketValue"] == 0.0
    assert inventory["realizedPnl"] == 29.0
    assert positions[-1]["positionStatus"] == "closed"
    assert positions[-1]["authoritativeInventorySnapshot"]["quantity"] == 0


def test_regime_local_paper_broker_failed_fill_rolls_back_account_and_inventory() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    order = broker.submit_order(_order(clientOrderId="bad-exit", orderIntentId="bad-exit-intent", side="SELL", limitPrice=100.0, quantity=1, positionEffect="exit_long"))

    with pytest.raises(ValueError, match="sell more Regime quantity"):
        broker.process_market_update({"symbol": "SPY", "bid": 100.0, "ask": 100.2})

    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=100_000).get_account_snapshot()
    inventory = repository.current_inventory_snapshot(identity)
    assert account.cash == 100_000.0
    assert account.equity == 100_000.0
    assert account.fills == ()
    assert inventory["quantity"] == 0
    assert inventory["realizedPnl"] == 0.0
    assert broker.find_order_by_client_order_id(order["clientOrderId"])["status"] == "ACCEPTED"
    assert broker.get_fills(order["clientOrderId"]) == []


def test_regime_local_paper_broker_stop_limit_requires_trigger_then_limit() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    order = broker.submit_order(_order(orderType="STOP_LIMIT", stopPrice=101.0, limitPrice=100.5, quantity=3, clientOrderId="stop-limit-1"))

    assert broker.process_market_update({"symbol": "SPY", "bid": 99.8, "ask": 100.0}) == ()
    assert broker.process_market_update({"symbol": "SPY", "bid": 101.1, "ask": 101.2}) == ()
    assert broker.find_order_by_client_order_id("stop-limit-1")["stopTriggered"] is True

    fills = broker.process_market_update({"symbol": "SPY", "bid": 100.4, "ask": 100.5})

    assert len(fills) == 1
    assert fills[0].averageFillPrice == 100.5
    assert broker.find_order_by_client_order_id(order["clientOrderId"])["status"] == "FILLED"


def test_regime_local_paper_broker_cancel_keeps_cash_unchanged() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    broker.submit_order(_order(clientOrderId="cancel-me", limitPrice=100.0, quantity=5))

    assert broker.cancel_order("cancel-me") is True
    assert broker.get_open_orders("SPY") == []
    assert broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0}) == ()
    assert RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=100_000).get_account_snapshot().cash == 100_000.0


def test_regime_local_paper_broker_rejects_foreign_algorithm_order() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)

    with pytest.raises(ValueError, match="cross-algorithm"):
        broker.submit_order(_order(algorithmId="wca", clientOrderId="foreign-order"))

    assert broker.get_open_orders("SPY") == []
    assert RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=100_000).get_account_snapshot().cash == 100_000.0


def test_regime_local_paper_reconciliation_uses_only_local_state() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    broker.submit_order(_order(clientOrderId="reconcile-entry", orderIntentId="reconcile-entry-intent", limitPrice=100.0, quantity=4))
    broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0})

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker=_BrokerThatMustNotBeCalled(),
        evaluated_at=NOW,
        trigger="local_periodic",
    )

    assert reconciliation["localPaper"] is True
    assert reconciliation["reconciled"] is True
    assert reconciliation["blockNewEntries"] is False
    assert reconciliation["counts"]["brokerPositions"] == 0
    assert reconciliation["counts"]["localFills"] == 1
    assert reconciliation["reasonCodes"][-1] == "regime.reconciliation.completed"


def test_regime_local_paper_reconciliation_blocks_entries_on_equity_mismatch() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    broker.submit_order(_order(clientOrderId="bad-equity-entry", orderIntentId="bad-equity-entry-intent", limitPrice=100.0, quantity=2))
    broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0})
    account = repository.read_local_paper_account_snapshot(identity)
    repository.write_local_paper_account_snapshot(identity, {**account, "equity": account["equity"] + 10.0})

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        evaluated_at=NOW,
        trigger="local_periodic",
    )

    assert reconciliation["localPaper"] is True
    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert any("equity_mismatch" in item for item in reconciliation["discrepancies"])

def test_regime_local_paper_reconciliation_blocks_entries_when_trade_record_missing() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    broker.submit_order(_order(clientOrderId="missing-trade-entry", orderIntentId="missing-trade-entry-intent", limitPrice=100.0, quantity=2))
    broker.process_market_update({"symbol": "SPY", "bid": 99.9, "ask": 100.0})
    with sqlite3.connect(repository.path) as conn:
        conn.execute("DELETE FROM regime_trades WHERE algorithm_id = 'regime' AND runtime_mode = 'local_paper'")

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        evaluated_at=NOW,
        trigger="local_periodic",
    )

    assert reconciliation["localPaper"] is True
    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert "regime.local_paper.reconciliation.trade_records_missing" in reconciliation["discrepancies"]

def test_regime_local_paper_restart_restores_account_inventory_and_open_order_state_exactly() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    pending = broker.submit_order(_order(clientOrderId="restart-pending", orderIntentId="restart-pending-intent", limitPrice=400.0, quantity=100))
    broker.submit_order(_order(clientOrderId="restart-entry", orderIntentId="restart-entry-intent", limitPrice=500.0, quantity=100))
    broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})
    before_account = repository.read_local_paper_account_snapshot(identity)
    before_inventory = repository.current_inventory_snapshot(identity)
    before_orders = broker.get_open_orders("SPY")

    restarted_repository = RegimeRepository(f"sqlite:///{repository.path}")
    restarted_broker = RegimeLocalPaperBroker(repository=restarted_repository, identity=identity, starting_balance=1)
    after_account = restarted_repository.read_local_paper_account_snapshot(identity)
    after_inventory = restarted_repository.current_inventory_snapshot(identity)
    after_orders = restarted_broker.get_open_orders("SPY")

    assert pending["status"] == "ACCEPTED"
    assert after_account["cash"] == before_account["cash"] == 50_000.0
    assert after_account["reservedCash"] == before_account["reservedCash"] == 40_000.0
    assert after_account["positions"] == before_account["positions"]
    assert after_inventory == before_inventory
    assert after_orders == before_orders
    assert [order["clientOrderId"] for order in after_orders] == ["restart-pending"]


def test_regime_local_paper_never_uses_broker_trading_endpoints_as_authority() -> None:
    repository, identity = _repository()
    broker = RegimeLocalPaperBroker(repository=repository, identity=identity, starting_balance=100_000)
    broker.submit_order(_order(clientOrderId="no-broker-authority", orderIntentId="no-broker-authority-intent", limitPrice=500.0, quantity=1))
    broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker=_BrokerThatMustNotBeCalled(),
        evaluated_at=NOW,
        trigger="no_broker_trading_calls",
    )

    assert broker.base_url == "local-paper://regime"
    assert "alpaca" not in broker.base_url.lower()
    assert broker.refresh_positions() == []
    assert reconciliation["localPaper"] is True
    assert reconciliation["counts"]["brokerOpenOrders"] == 0
    assert reconciliation["counts"]["brokerFills"] == 0
    assert reconciliation["counts"]["brokerPositions"] == 0

def test_regime_end_to_end_auto_paper_updates_inventory_account_pnl_and_exit() -> None:
    repository, identity = _repository()
    supervisor = build_regime_paper_runtime(
        service=RegimeApplicationService(repository),
        settings=_settings_without_alpaca(),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=identity["algorithmInstanceId"],
            default_account_id=identity["accountId"],
            default_runtime_mode="local_paper",
            symbol="SPY",
        ),
    )
    entry = _proposal(identity, decisionId="e2e-entry-decision", orderIntentId="e2e-entry-intent", quantity=10, limitPrice=500.0, triggerPrice=500.0, stopPrice=498.0, targetPrice=505.0)

    entry_result = supervisor.paper_gateway.submit(
        proposal=entry,
        global_application=_global_application_for(entry),
        local_gate_passed=True,
        mode="automatic",
        evaluated_at=NOW,
    )
    entry_fills = supervisor.paper_gateway.broker.process_market_update({"symbol": "SPY", "bid": 499.95, "ask": 500.0})
    marked = supervisor.paper_gateway.broker.process_market_update({"symbol": "SPY", "bid": 505.0, "ask": 505.0})
    account_after_entry = repository.read_local_paper_account_snapshot(identity)
    inventory_after_entry = repository.current_inventory_snapshot(identity)

    exit_proposal = _proposal(
        identity,
        decisionId="e2e-exit-decision",
        orderIntentId="e2e-exit-intent",
        intent="risk_reducing",
        side=Signal.SELL,
        quantity=10,
        limitPrice=505.0,
        triggerPrice=505.0,
        plannedRiskDollars=0.0,
    )
    exit_result = supervisor.paper_gateway.submit(
        proposal=exit_proposal,
        global_application=_global_application_for(exit_proposal, risk_reducing=True),
        local_gate_passed=True,
        mode="automatic",
        evaluated_at=NOW,
    )
    exit_fills = supervisor.paper_gateway.broker.process_market_update({"symbol": "SPY", "bid": 505.0, "ask": 505.1})
    account_after_exit = repository.read_local_paper_account_snapshot(identity)
    inventory_after_exit = repository.current_inventory_snapshot(identity)

    assert entry_result.submitted is True
    assert len(entry_fills) == 1
    assert marked == ()
    assert account_after_entry["cash"] == 95_000.0
    assert account_after_entry["unrealizedPnl"] == 50.0
    assert inventory_after_entry["quantity"] == 10
    assert inventory_after_entry["averageEntryPrice"] == 500.0
    assert exit_result.submitted is True
    assert len(exit_fills) == 1
    assert inventory_after_exit["quantity"] == 0
    assert account_after_exit["cash"] == 100_050.0
    assert account_after_exit["realizedPnl"] == 50.0
    assert account_after_exit["dailyRealizedPnl"] == 50.0

def test_regime_local_paper_gateway_routes_to_local_simulator_without_alpaca_credentials(monkeypatch) -> None:
    monkeypatch.delenv("REGIME_ALPACA_PAPER_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("REGIME_PAPER_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_ACCOUNT_ID", raising=False)
    repository, identity = _repository()
    RegimeLocalPaperAccount(algorithmInstanceId=identity["algorithmInstanceId"], accountId=identity["accountId"], runtimeMode="local_paper").persist(repository, symbol=identity["symbol"])
    supervisor = build_regime_paper_runtime(
        service=RegimeApplicationService(repository),
        settings=_settings_without_alpaca(),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=identity["algorithmInstanceId"],
            default_account_id=identity["accountId"],
            default_runtime_mode="local_paper",
            symbol="SPY",
        ),
    )

    result = supervisor.paper_gateway.submit(
        proposal=_proposal(identity),
        global_application=_global_application(),
        local_gate_passed=True,
        mode="automatic",
        evaluated_at=NOW,
    )

    assert supervisor.paper_gateway.execution_mode == "LOCAL_PAPER"
    assert isinstance(supervisor.paper_gateway.broker, RegimeLocalPaperBroker)
    assert result.submitted is True
    assert result.status == "ACCEPTED"
    assert supervisor.paper_gateway.broker.find_order_by_client_order_id(result.clientOrderId)["status"] == "ACCEPTED"


class _BrokerThatMustNotBeCalled:
    def refresh_positions(self) -> list[dict[str, object]]:
        raise AssertionError("LOCAL_PAPER reconciliation must not inspect broker positions")

    def refresh_open_orders(self) -> list[dict[str, object]]:
        raise AssertionError("LOCAL_PAPER reconciliation must not inspect broker orders")

    def refresh_fills(self) -> list[dict[str, object]]:
        raise AssertionError("LOCAL_PAPER reconciliation must not inspect broker fills")

def _repository(*, initial_balance: float = 100_000, seed_account: bool = True) -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-local-paper-default",
        "accountId": "regime-local-paper-account",
        "runtimeMode": "local_paper",
        "symbol": "SPY",
    }
    if seed_account:
        RegimeLocalPaperAccount(
            algorithmInstanceId=identity["algorithmInstanceId"],
            accountId=identity["accountId"],
            runtimeMode=identity["runtimeMode"],
            initialBalance=initial_balance,
        ).persist(repository, symbol=identity["symbol"])
    return repository, identity


def _order(**overrides: object) -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-local-paper-default",
        "accountId": "regime-local-paper-account",
        "runtimeMode": "local_paper",
        "symbol": "SPY",
        "clientOrderId": "local-order-1",
        "orderIntentId": "local-intent-1",
        "decisionId": "local-decision-1",
        "side": "BUY",
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "quantity": 10,
        "limitPrice": 100.0,
        **overrides,
    }


def _proposal(identity: dict[str, str], **overrides: object) -> GlobalOrderProposal:
    payload = {
        "algorithmId": "regime",
        "capitalPartitionId": "regime-local-partition",
        "decisionId": "gateway-decision-1",
        "orderIntentId": "gateway-intent-1",
        "intent": "new_entry",
        "symbol": identity["symbol"],
        "side": Signal.BUY,
        "quantity": 1,
        "triggerPrice": 100.0,
        "limitPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "plannedRiskDollars": 1.0,
        "settingsSnapshot": {"maximumOrderAgeSeconds": 300},
        "entryFormula": {"orderType": "LIMIT", "timeInForce": "DAY"},
        "stopFormula": {},
        "targetFormula": {"orderType": "LIMIT"},
        "strategyStateHash": "strategy-state",
        "proposedAt": NOW,
        "sessionDate": date(2026, 7, 23),
        "configurationHash": "config-hash",
    }
    payload.update(overrides)
    return GlobalOrderProposal(**payload)

def _global_application() -> AppliedGlobalGateDecision:
    return AppliedGlobalGateDecision(
        algorithmId="regime",
        decisionId="gateway-decision-1",
        orderIntentId="gateway-intent-1",
        action="ALLOW",
        side=Signal.BUY,
        proposedQuantity=1,
        globallyAllowedQuantity=1,
        proposedPlannedRiskDollars=1.0,
        maximumAdditionalRiskDollars=1.0,
        quantityReduced=False,
        riskReducingExitAllowed=False,
        immutableChecks=("test",),
        proposalHash="proposal-hash",
        responseHash="response-hash",
        evaluatedAt=NOW,
        explanation="test global approval",
    )


def _global_application_for(proposal: GlobalOrderProposal, *, risk_reducing: bool = False) -> AppliedGlobalGateDecision:
    return AppliedGlobalGateDecision(
        algorithmId="regime",
        decisionId=proposal.decisionId,
        orderIntentId=proposal.orderIntentId,
        action="ALLOW",
        side=proposal.side,
        proposedQuantity=proposal.quantity,
        globallyAllowedQuantity=proposal.quantity,
        proposedPlannedRiskDollars=proposal.plannedRiskDollars,
        maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
        quantityReduced=False,
        riskReducingExitAllowed=risk_reducing,
        immutableChecks=("test",),
        proposalHash=f"proposal-hash-{proposal.orderIntentId}",
        responseHash=f"response-hash-{proposal.orderIntentId}",
        evaluatedAt=NOW,
        explanation="test global approval",
    )

def _settings_without_alpaca() -> Settings:
    return Settings(
        alpaca_key_id="",
        alpaca_secret_key="",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///./data/trading.db",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )

