from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.local_paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_LOCAL_PAPER_ACCOUNT_ID,
    WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    WCA_LOCAL_PAPER_STARTING_BALANCE,
    WcaLocalPaperAccount,
    validate_wca_local_paper_account,
)
from backend.app.algorithms.wca.local_paper_broker import (
    WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED,
    WcaLocalPaperBroker,
    WcaLocalPaperBrokerConfigurationError,
    WcaLocalPaperFillModel,
)
from backend.app.algorithms.wca.local_paper_risk import WcaLocalPaperRiskContext, WcaLocalPaperRiskManager, WcaLocalPaperRiskPolicy
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerOrderRequest, WcaPaperBrokerOutboxAdapter, build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.tests.test_wca_paper_execution_pipeline import decision_with_order


def test_wca_has_dedicated_local_account() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-paper-isolated", symbol="SPY", starting_balance=25_000.0)

    snapshot = broker.refresh_account_snapshot()
    broker._account().persist(repository, symbol="SPY", timestamp="2026-01-05T14:30:00+00:00")
    inventory = repository.read_wca_local_inventory_snapshot(local_account_id="wca-paper-isolated", symbol="SPY")

    assert snapshot.accountId == "wca-paper-isolated"
    assert snapshot.sourceAuthority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert inventory is not None
    assert inventory["account_snapshot"]["algorithm_id"] == WCA_ALGORITHM_ID
    assert inventory["account_snapshot"]["local_account_id"] == "wca-paper-isolated"
    with sqlite3.connect(repository.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO wca_local_paper_account (
                    algorithm_id, local_account_id, symbol, starting_balance, cash, equity,
                    buying_power, session_date, circuit_breaker_state, state_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("weighted_voting", "weighted-paper", "SPY", 50_000.0, 50_000.0, 50_000.0, 50_000.0, "2026-01-05", "closed", "foreign"),
            )


def test_wca_local_balance_is_independent() -> None:
    repository = repository_for_isolation()
    wca = WcaLocalPaperBroker(repository=repository, account_id="wca-balance", symbol="SPY", starting_balance=10_000.0)
    sibling = WcaLocalPaperBroker(repository=repository, account_id="weighted-voting-balance", symbol="SPY", starting_balance=50_000.0)

    wca_account = wca._account()
    wca_account.apply_fill(symbol="SPY", side="BUY", quantity=10, price=100.0, filled_at="2026-01-05T15:00:00+00:00")
    wca_account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:00:00+00:00")

    assert wca.refresh_account()["cash"] == 9_000.0
    assert sibling.refresh_account()["cash"] == 50_000.0


def test_wca_local_position_is_independent() -> None:
    repository = repository_for_isolation()
    wca = WcaLocalPaperBroker(repository=repository, account_id="wca-position", symbol="SPY", starting_balance=10_000.0)
    sibling = WcaLocalPaperBroker(repository=repository, account_id="voting-ensemble-position", symbol="SPY", starting_balance=10_000.0)

    wca_account = wca._account()
    wca_account.apply_fill(symbol="SPY", side="BUY", quantity=3, price=100.0, filled_at="2026-01-05T15:00:00+00:00")
    wca_account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:00:00+00:00")

    assert wca.refresh_account_snapshot().positions[0].quantity == 3
    assert list(sibling.refresh_account_snapshot().positions) == []


def test_wca_fill_updates_only_wca_inventory() -> None:
    repository = repository_for_isolation()
    with pytest.raises(sqlite3.IntegrityError, match="non-WCA algorithm_id rejected"):
        _insert_foreign_owned_lot(repository, algorithm_id="weighted_voting", account_id="paper-isolation", quantity=77)
    _, request, broker = _submit_wca_entry(repository, suffix="updates-only-wca", account_id="paper-isolation")

    fills = _fill_entry(repository, broker, request)

    assert len(fills) == 1
    assert repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id="paper-isolation", symbol="SPY").open_quantity == request.quantity
    assert _foreign_lot_quantity(repository, algorithm_id="weighted_voting", account_id="paper-isolation") == 0


def test_wca_sell_cannot_close_weighted_voting_position() -> None:
    _assert_wca_sell_cannot_close_foreign_position("weighted_voting")


def test_wca_sell_cannot_close_voting_ensemble_position() -> None:
    _assert_wca_sell_cannot_close_foreign_position("voting_ensemble")


def test_wca_stop_cannot_close_other_algorithm_position() -> None:
    repository = repository_for_isolation()
    _, request, broker = _submit_wca_entry(repository, suffix="stop-other-owner", account_id="wca-stop-block")
    _fill_entry(repository, broker, request)
    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute(
            "SELECT broker_order_id, payload_json FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND client_order_id = ?",
            (WCA_ALGORITHM_ID, request.account_id, stop_order.clientOrderId),
        ).fetchone()
        payload = json.loads(row[1])
        payload["ownership"]["protected_algorithm_id"] = "weighted_voting"
        payload["ownership"]["position_owner"] = "weighted_voting"
        conn.execute("UPDATE wca_broker_orders SET payload_json = ? WHERE broker_order_id = ?", (json.dumps(payload, sort_keys=True), row[0]))

    with pytest.raises(WcaLocalPaperBrokerConfigurationError, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        broker.simulate_fill(client_order_id=stop_order.clientOrderId, fill_price=stop_order.stopPrice or stop_order.entryPrice, quantity=1, filled_at="2026-01-05T15:01:00+00:00")

    assert repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol).open_quantity == request.quantity
    assert _cross_algorithm_block_count(repository, account_id="wca-stop-block") == 1


def test_other_algorithm_fill_does_not_change_wca_cash() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-cash-ignore", symbol="SPY", starting_balance=12_345.0)
    before = broker.refresh_account()["cash"]

    with pytest.raises(sqlite3.IntegrityError, match="non-WCA algorithm_id rejected"):
        _insert_foreign_fill(repository, algorithm_id="weighted_voting", quantity=100, price=500.0)

    assert broker.refresh_account()["cash"] == before


def test_other_algorithm_loss_does_not_change_wca_daily_loss() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-loss-ignore", symbol="SPY", starting_balance=12_345.0)
    before = broker._account().get_account_snapshot().daily_loss

    with pytest.raises(sqlite3.IntegrityError, match="non-WCA algorithm_id rejected"):
        _insert_foreign_trade(repository, algorithm_id="voting_ensemble", pnl=-9_999.0)

    assert broker._account().get_account_snapshot().daily_loss == before == 0.0


def test_other_algorithm_trade_does_not_increment_wca_trade_count() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-trades-ignore", symbol="SPY", starting_balance=12_345.0)
    before = broker._account().get_account_snapshot().trades_today

    with pytest.raises(sqlite3.IntegrityError, match="non-WCA algorithm_id rejected"):
        _insert_foreign_trade(repository, algorithm_id="weighted_voting", pnl=100.0)

    assert broker._account().get_account_snapshot().trades_today == before == 0


def test_wca_restart_restores_local_account() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-restart-account", symbol="SPY", starting_balance=20_000.0)
    account = broker._account()
    account.reserve_risk(123.0)
    account.apply_fill(symbol="SPY", side="BUY", quantity=5, price=100.0, filled_at="2026-01-05T15:00:00+00:00")
    account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:01:00+00:00")

    restarted = WcaLocalPaperBroker(repository=repository, account_id="wca-restart-account", symbol="SPY", starting_balance=999_999.0)
    restored = restarted._account().get_account_snapshot()

    assert restored.starting_balance == 20_000.0
    assert restored.cash == 19_500.0
    assert restored.reserved_risk == 123.0


def test_wca_restart_restores_open_position() -> None:
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id="wca-restart-position", symbol="SPY", starting_balance=20_000.0)
    account = broker._account()
    account.apply_fill(symbol="SPY", side="BUY", quantity=8, price=101.0, filled_at="2026-01-05T15:00:00+00:00")
    account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:01:00+00:00")

    restarted = WcaLocalPaperBroker(repository=repository, account_id="wca-restart-position", symbol="SPY", starting_balance=999_999.0)

    assert restarted.refresh_account_snapshot().positions[0].quantity == 8
    assert restarted.refresh_account_snapshot().positions[0].averageEntryPrice == 101.0


def test_wca_duplicate_order_is_idempotent() -> None:
    repository = repository_for_isolation()
    decision, request, reservation = _reserve_wca_order(repository, suffix="duplicate-order", account_id="wca-idem-order")

    duplicate = _reserve_existing_order(repository, decision=decision, request=request, run_id="isolation-run")

    assert reservation.created is True
    assert duplicate.created is False
    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wca_execution_outbox WHERE algorithm_id = ? AND account_id = ?", (WCA_ALGORITHM_ID, "wca-idem-order")).fetchone()[0] == 1


def test_wca_partial_fill_is_idempotent() -> None:
    repository = repository_for_isolation()
    decision, request, _ = _reserve_wca_order(repository, suffix="partial-idem", account_id="wca-idem-partial")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="mandatory-partial-idem")
    fill_payload = {
        "fill": {"average_fill_price": request.limit_price},
        "client_order_id": request.client_order_id,
        "broker_order_id": f"wca-local-{request.client_order_id}",
        "entry_price": request.limit_price,
        "opened_at": "2026-01-05T15:00:00+00:00",
        "remaining_quantity": request.quantity - 2,
        "position_effect": "entry",
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    }

    first = repository.apply_fill_and_update_position(decision, fill_id="wca-partial-idempotent-fill", account_id=request.account_id, quantity=2, broker_order_id="wca-partial-idem-order", payload=fill_payload)
    second = repository.apply_fill_and_update_position(decision, fill_id="wca-partial-idempotent-fill", account_id=request.account_id, quantity=2, broker_order_id="wca-partial-idem-order", payload=fill_payload)

    assert first is True
    assert second is False
    inventory = repository.read_wca_local_inventory_snapshot(local_account_id=request.account_id, symbol=request.symbol)
    assert inventory is not None
    assert sum(row["quantity"] for row in inventory["fills"]) == 2
    assert inventory["positions"][0]["quantity"] == 2


def test_wca_local_risk_uses_local_equity() -> None:
    account = WcaLocalPaperAccount(account_id="wca-risk-equity", starting_balance=10_000.0)
    request = _risk_request(account_id="wca-risk-equity", quantity=100, limit_price=100.0, stop_price=99.0)

    decision = WcaLocalPaperRiskManager().evaluate_order(
        WcaLocalPaperRiskContext(
            account_snapshot=account.get_account_snapshot(),
            request=request,
            policy=WcaLocalPaperRiskPolicy(base_risk_percent=1.0, confidence_size_multiplier=0.5, edge_size_multiplier=0.5, max_position_percent=100.0),
        )
    )

    assert decision.local_equity == 10_000.0
    assert decision.risk_budget_dollars == 50.0
    assert "wca.local_risk.base_risk_percent_exceeded" in decision.reason_codes


def test_wca_buying_power_uses_local_account() -> None:
    account = WcaLocalPaperAccount(account_id="wca-risk-buying-power", starting_balance=1_000.0)
    request = _risk_request(account_id="wca-risk-buying-power", quantity=20, limit_price=100.0, stop_price=99.0)

    decision = WcaLocalPaperRiskManager().evaluate_order(
        WcaLocalPaperRiskContext(account_snapshot=account.get_account_snapshot(), request=request, policy=WcaLocalPaperRiskPolicy(max_position_percent=100.0, base_risk_percent=50.0))
    )

    assert decision.local_buying_power == 1_000.0
    assert "wca.local_risk.buying_power_exceeded" in decision.reason_codes
    assert "wca.local_risk.available_cash_exceeded" in decision.reason_codes


def test_wca_protective_orders_are_wca_owned() -> None:
    repository = repository_for_isolation()
    _, request, broker = _submit_wca_entry(repository, suffix="protective-owned", account_id="wca-protection-owner")
    _fill_entry(repository, broker, request)

    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT algorithm_id, account_id, symbol, payload_json
            FROM wca_broker_orders
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        payload = json.loads(row["payload_json"])
        ownership = payload["ownership"]
        assert row["algorithm_id"] == WCA_ALGORITHM_ID
        assert row["account_id"] == request.account_id
        assert ownership["protected_algorithm_id"] == WCA_ALGORITHM_ID
        assert ownership["position_owner"] == WCA_ALGORITHM_ID
        assert ownership["exit_owner"] == WCA_ALGORITHM_ID
        assert ownership["local_account_id"] == request.account_id


def test_cross_algorithm_mutation_is_rejected() -> None:
    repository = repository_for_isolation()
    _, request, broker = _submit_wca_entry(repository, suffix="cross-mutation", account_id="wca-cross-mutation")
    _fill_entry(repository, broker, request)
    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute(
            "SELECT broker_order_id, payload_json FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND client_order_id = ?",
            (WCA_ALGORITHM_ID, request.account_id, stop_order.clientOrderId),
        ).fetchone()
        payload = json.loads(row[1])
        payload["ownership"]["protected_algorithm_id"] = "weighted_voting"
        conn.execute("UPDATE wca_broker_orders SET payload_json = ? WHERE broker_order_id = ?", (json.dumps(payload, sort_keys=True), row[0]))

    with pytest.raises(WcaLocalPaperBrokerConfigurationError, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        broker.simulate_fill(client_order_id=stop_order.clientOrderId, fill_price=stop_order.stopPrice or stop_order.entryPrice, quantity=stop_order.quantity, filled_at="2026-01-05T15:02:00+00:00")

    assert repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol).open_quantity == request.quantity
    assert _cross_algorithm_block_count(repository, account_id=request.account_id) == 1


def test_local_auto_paper_requires_no_alpaca_credentials() -> None:
    repository = repository_for_isolation()
    env = {
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
        WCA_LOCAL_PAPER_ACCOUNT_ID: "wca-no-alpaca",
        WCA_LOCAL_PAPER_STARTING_BALANCE: "25000",
    }

    validation = validate_wca_local_paper_account(account_id="wca-no-alpaca", environ=env)
    broker = WcaLocalPaperBroker.from_env(repository=repository, account_id="wca-no-alpaca", symbol="SPY", environ=env)

    assert validation.verified is True
    assert validation.source_authority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert WCA_LOCAL_PAPER_SOURCE_AUTHORITY == broker.refresh_account_snapshot().sourceAuthority
    assert broker.refresh_account()["equity"] == 25_000.0




def test_local_auto_paper_allows_alpaca_market_data_but_rejects_alpaca_paper_execution() -> None:
    repository = repository_for_isolation()
    market_data_env = {
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
        WCA_LOCAL_PAPER_ACCOUNT_ID: "wca-market-data-only",
        WCA_LOCAL_PAPER_STARTING_BALANCE: "50000",
        "APCA_API_KEY_ID": "alpaca-market-data-key",
        "APCA_API_SECRET_KEY": "alpaca-market-data-secret",
        "APCA_API_BASE_URL": "https://data.alpaca.markets",
    }

    validation = validate_wca_local_paper_account(account_id="wca-market-data-only", environ=market_data_env)
    broker = WcaLocalPaperBroker.from_env(repository=repository, account_id="wca-market-data-only", symbol="SPY", environ=market_data_env)

    assert validation.verified is True
    assert validation.source_authority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert "wca.local_paper_account.alpaca_paper_execution_disabled" not in validation.reason_codes
    assert "wca.local_paper_account.shared_alpaca_credentials_rejected" not in validation.reason_codes
    assert broker.refresh_account_snapshot().sourceAuthority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert broker.refresh_account()["equity"] == 50_000.0

    paper_execution_env = {
        **market_data_env,
        WCA_ALPACA_PAPER_ACCOUNT_ID: "alpaca-paper-account",
        WCA_ALPACA_PAPER_API_KEY_ID: "alpaca-paper-key",
        WCA_ALPACA_PAPER_API_SECRET_KEY: "alpaca-paper-secret",
        WCA_ALPACA_PAPER_BASE_URL: "https://paper-api.alpaca.markets",
    }
    paper_validation = validate_wca_local_paper_account(account_id="wca-market-data-only", environ=paper_execution_env)

    assert paper_validation.verified is False
    assert paper_validation.source_authority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert "wca.local_paper_account.alpaca_paper_execution_disabled" in paper_validation.reason_codes


def test_local_auto_paper_keeps_legacy_broker_paper_optional_without_dependency() -> None:
    wca_root = Path(__file__).resolve().parents[1] / "app" / "algorithms" / "wca"

    assert (wca_root / "alpaca_paper_broker.py").exists()
    assert (wca_root / "paper_account.py").exists()
    assert (wca_root / "broker_reconciliation.py").exists()

    local_account_source = (wca_root / "local_paper_account.py").read_text(encoding="utf-8")
    local_broker_source = (wca_root / "local_paper_broker.py").read_text(encoding="utf-8")
    runtime_supervisor_source = (wca_root / "runtime_supervisor.py").read_text(encoding="utf-8")

    assert "from backend.app.algorithms.wca.paper_account import" not in local_account_source
    assert "from backend.app.algorithms.wca.paper_account import" not in local_broker_source
    assert "validate_wca_local_paper_account" in local_broker_source
    assert "WcaLocalPaperBroker(" in runtime_supervisor_source
    assert "WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER" in runtime_supervisor_source
    assert "WcaAlpacaPaperBroker" not in runtime_supervisor_source
    assert "alpaca_paper_broker" not in runtime_supervisor_source

def test_wca_stop_loss_closes_position_records_loss_and_cancels_target_without_cross_algorithm_changes() -> None:
    starting_balance = 100_000.0
    account_id = "wca-stop-loss-flow"
    entry_price = 600.0
    stop_price = 598.0
    target_price = 604.0
    repository = repository_for_isolation()
    non_wca_before = _non_wca_inventory_row_count(repository)
    _, request, broker = _submit_wca_entry(
        repository,
        suffix="stop-loss-flow",
        account_id=account_id,
        limit_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=10,
    )

    entry_fills = _fill_entry(repository, broker, request)
    after_entry = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance).get_account_snapshot()
    quantity = int(request.quantity)
    assert len(entry_fills) == 1
    assert request.limit_price == entry_price
    assert request.target_price == target_price
    assert after_entry.positions[0].quantity == quantity
    assert after_entry.positions[0].average_entry_price == entry_price
    assert after_entry.cash == round(starting_balance - quantity * entry_price, 10)
    assert after_entry.reserved_risk == 0.0

    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    target_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "LIMIT" and order.clientOrderId != request.client_order_id)
    assert stop_order.stopPrice == stop_price
    assert stop_order.entryPrice == stop_price
    assert target_order.entryPrice == target_price

    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=2)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, account_id, request.symbol),
        )
    stop_fills = broker.process_market_update(
        {
            "symbol": request.symbol,
            "bid": stop_price,
            "ask": stop_price + 0.02,
            "timestamp": market_timestamp.isoformat(),
        }
    )

    expected_loss = round(quantity * (stop_price - entry_price), 10)
    expected_cash = round(starting_balance + expected_loss, 10)
    after_stop = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance, session_date=market_timestamp.date()).get_account_snapshot()
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=request.symbol)
    filled_stop = broker.get_order(stop_order.clientOrderId)
    cancelled_target = broker.get_order(target_order.clientOrderId)

    assert len(stop_fills) == 1
    assert stop_fills[0].client_order_id == stop_order.clientOrderId
    assert projection.open_quantity == 0
    assert after_stop.positions == ()
    assert after_stop.realized_pnl == expected_loss
    assert after_stop.daily_realized_pnl == expected_loss
    assert after_stop.daily_loss == abs(expected_loss)
    assert after_stop.cash == expected_cash
    assert after_stop.equity == expected_cash
    assert after_stop.buying_power == expected_cash
    assert after_stop.reserved_risk == 0.0
    assert projection.reserved_risk == 0.0
    assert after_stop.trades_today == 1
    assert filled_stop is not None
    assert filled_stop["status"] == "filled"
    assert cancelled_target is not None
    assert cancelled_target["status"] == "canceled"
    assert _non_wca_inventory_row_count(repository) == non_wca_before


def test_wca_profit_target_closes_position_records_profit_and_cancels_stop_without_cross_algorithm_changes() -> None:
    starting_balance = 100_000.0
    account_id = "wca-profit-target-flow"
    entry_price = 600.0
    stop_price = 598.0
    target_price = 604.0
    repository = repository_for_isolation()
    non_wca_before = _non_wca_inventory_row_count(repository)
    _, request, broker = _submit_wca_entry(
        repository,
        suffix="profit-target-flow",
        account_id=account_id,
        limit_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=10,
    )

    entry_fills = _fill_entry(repository, broker, request)
    after_entry = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance).get_account_snapshot()
    quantity = int(request.quantity)
    assert len(entry_fills) == 1
    assert after_entry.positions[0].quantity == quantity
    assert after_entry.positions[0].average_entry_price == entry_price
    assert after_entry.cash == round(starting_balance - quantity * entry_price, 10)

    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    target_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "LIMIT" and order.clientOrderId != request.client_order_id)
    assert stop_order.stopPrice == stop_price
    assert target_order.entryPrice == target_price

    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=2)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, account_id, request.symbol),
        )
    target_fills = broker.process_market_update(
        {
            "symbol": request.symbol,
            "bid": target_price,
            "ask": target_price + 0.02,
            "timestamp": market_timestamp.isoformat(),
        }
    )

    expected_profit = round(quantity * (target_price - entry_price), 10)
    expected_cash = round(starting_balance + expected_profit, 10)
    after_target = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance, session_date=market_timestamp.date()).get_account_snapshot()
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=request.symbol)
    filled_target = broker.get_order(target_order.clientOrderId)
    cancelled_stop = broker.get_order(stop_order.clientOrderId)

    assert len(target_fills) == 1
    assert target_fills[0].client_order_id == target_order.clientOrderId
    assert projection.open_quantity == 0
    assert after_target.positions == ()
    assert after_target.realized_pnl == expected_profit
    assert after_target.daily_realized_pnl == expected_profit
    assert after_target.daily_loss == 0.0
    assert after_target.cash == expected_cash
    assert after_target.equity == expected_cash
    assert after_target.buying_power == expected_cash
    assert after_target.reserved_risk == 0.0
    assert projection.reserved_risk == 0.0
    assert after_target.trades_today == 1
    assert filled_target is not None
    assert filled_target["status"] == "filled"
    assert cancelled_stop is not None
    assert cancelled_stop["status"] == "canceled"
    assert _non_wca_inventory_row_count(repository) == non_wca_before

def test_wca_daily_loss_circuit_breaker_blocks_entries_but_allows_risk_reducing_exits_without_affecting_other_algorithms() -> None:
    configured_max_daily_loss = 100.0
    account_id = "wca-daily-loss-breaker"
    repository = repository_for_isolation()
    non_wca_before = _non_wca_inventory_row_count(repository)
    account = WcaLocalPaperAccount(account_id=account_id, starting_balance=100_000.0, session_date="2026-01-05")

    losses_generated = 0
    while account.get_account_snapshot().daily_loss < configured_max_daily_loss:
        losses_generated += 1
        account.apply_fill(symbol="SPY", side="BUY", quantity=10, price=600.0, filled_at=f"2026-01-05T15:{losses_generated:02d}:00+00:00")
        account.close_position(symbol="SPY", quantity=10, price=598.0, closed_at=f"2026-01-05T15:{losses_generated:02d}:30+00:00")
    account.apply_fill(symbol="SPY", side="BUY", quantity=5, price=600.0, filled_at="2026-01-05T15:20:00+00:00")
    account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:21:00+00:00")
    snapshot = account.get_account_snapshot()

    entry_request = WcaPaperBrokerOrderRequest(
        account_id=account_id,
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=1,
        order_type="LIMIT",
        limit_price=600.0,
        stop_price=598.0,
        target_price=604.0,
        client_order_id="wca-daily-loss-new-entry",
        idempotency_key="wca-daily-loss-new-entry",
        decision_id="wca-daily-loss-new-entry",
        order_intent_id="wca-daily-loss-new-entry",
        configuration_version="mandatory-daily-loss-circuit-breaker",
    )
    exit_request = WcaPaperBrokerOrderRequest(
        account_id=account_id,
        symbol="SPY",
        side=WcaSide.SELL,
        quantity=5,
        order_type="LIMIT",
        limit_price=599.0,
        client_order_id="wca-protection-daily-loss-risk-reducing-exit",
        idempotency_key="wca-protection-daily-loss-risk-reducing-exit",
        decision_id="wca-daily-loss-risk-reducing-exit",
        order_intent_id="wca-daily-loss-risk-reducing-exit",
        configuration_version="mandatory-daily-loss-circuit-breaker",
    )
    policy = WcaLocalPaperRiskPolicy(max_daily_loss=configured_max_daily_loss, base_risk_percent=100.0, max_position_percent=100.0, protective_target_required=True)

    entry_decision = WcaLocalPaperRiskManager().evaluate_order(WcaLocalPaperRiskContext(account_snapshot=snapshot, request=entry_request, policy=policy))
    exit_decision = WcaLocalPaperRiskManager().evaluate_order(WcaLocalPaperRiskContext(account_snapshot=snapshot, request=exit_request, policy=policy))

    assert snapshot.daily_loss >= configured_max_daily_loss
    assert losses_generated >= 5
    assert entry_decision.permitted is False
    assert "wca.local_risk.max_daily_loss_exceeded" in entry_decision.reason_codes
    assert exit_decision.permitted is True
    assert "wca.local_risk.passed" in exit_decision.reason_codes
    assert _algorithm_inventory_row_count(repository, "weighted_voting") == 0
    assert _algorithm_inventory_row_count(repository, "voting_ensemble") == 0
    assert _algorithm_inventory_row_count(repository, "regime") == 0
    assert _algorithm_inventory_row_count(repository, "meta_strategy") == 0
    assert _non_wca_inventory_row_count(repository) == non_wca_before
def test_wca_end_to_end_local_account_flow_restores_exactly_after_restart() -> None:
    starting_balance = 100_000.0
    account_id = "wca-e2e-account-flow"
    repository = repository_for_isolation()
    broker = WcaLocalPaperBroker(repository=repository, account_id=account_id, symbol="SPY", starting_balance=starting_balance)

    flat = broker.refresh_account()
    assert flat["cash"] == starting_balance
    assert flat["equity"] == starting_balance

    decision, request, reservation = _reserve_wca_order(repository, suffix="e2e-account-flow", account_id=account_id)
    entry_price = float(request.limit_price)
    quantity = int(request.quantity)
    entry_notional = round(quantity * entry_price, 10)
    risk = WcaLocalPaperRiskManager().evaluate_order(
        WcaLocalPaperRiskContext(
            account_snapshot=broker._account().get_account_snapshot(),
            request=request,
            decision=decision,
            policy=WcaLocalPaperRiskPolicy(base_risk_percent=100.0, max_position_percent=100.0, maximum_shares=quantity + 1),
        )
    )
    assert reservation.created is True
    assert risk.permitted is True
    assert risk.local_equity == starting_balance

    submitted = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="mandatory-e2e-account-flow")
    assert submitted.submitted is True
    assert submitted.state == WcaOrderStatus.ACKNOWLEDGED
    assert len(broker.get_open_orders(symbol=request.symbol)) == 1

    fills = _fill_entry(repository, broker, request)
    after_entry = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance).get_account_snapshot()
    inventory = repository.read_wca_local_inventory_snapshot(local_account_id=account_id, symbol=request.symbol)
    assert len(fills) == 1
    assert after_entry.positions[0].quantity == quantity
    assert after_entry.cash == round(starting_balance - entry_notional, 10)
    assert after_entry.equity == starting_balance
    assert after_entry.buying_power == after_entry.cash
    assert after_entry.lots[0].quantity == quantity
    assert after_entry.reserved_risk == 0.0
    assert inventory is not None
    assert len(inventory["lots"]) == 1
    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wca_exit_state WHERE algorithm_id = ? AND account_id = ?", (WCA_ALGORITHM_ID, account_id)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM wca_local_fills WHERE algorithm_id = ? AND local_account_id = ?", (WCA_ALGORITHM_ID, account_id)).fetchone()[0] == 1

    mark_price = round(entry_price + 5.0, 10)
    marked_account = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance)
    marked = marked_account.mark_to_market(symbol=request.symbol, mark_price=mark_price, marked_at="2026-01-05T15:03:00+00:00")
    marked_account.persist(repository, symbol=request.symbol, timestamp="2026-01-05T15:03:00+00:00")
    market_value = round(quantity * mark_price, 10)
    assert marked.unrealized_pnl == round(quantity * (mark_price - entry_price), 10)
    assert marked.equity == round(marked.cash + market_value, 10)

    exit_price = round(entry_price + 10.0, 10)
    exit_ack = broker.flatten_wca_positions(symbol=request.symbol, price=exit_price, evaluated_at="2026-01-05T15:04:00+00:00")
    after_exit = WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol=request.symbol, starting_balance=starting_balance, session_date="2026-01-05").get_account_snapshot()
    realized = round(quantity * (exit_price - entry_price), 10)
    expected_cash = round(starting_balance + realized, 10)

    assert exit_ack.fill is not None
    assert after_exit.positions == ()
    assert after_exit.cash == expected_cash
    assert after_exit.equity == expected_cash
    assert after_exit.buying_power == expected_cash
    assert after_exit.realized_pnl == realized
    assert after_exit.unrealized_pnl == 0.0
    assert after_exit.reserved_risk == 0.0
    assert after_exit.trades_today == 1

    restarted_repository = WcaSqliteRepository(f"sqlite:///{repository.path}")
    restarted_broker = WcaLocalPaperBroker(repository=restarted_repository, account_id=account_id, symbol=request.symbol, starting_balance=1.0)
    restored = WcaLocalPaperAccount.restore(restarted_repository, account_id=account_id, symbol=request.symbol, starting_balance=1.0, session_date="2026-01-05").get_account_snapshot()

    assert restarted_broker.refresh_account()["cash"] == expected_cash
    assert restored.starting_balance == starting_balance
    assert restored.cash == after_exit.cash
    assert restored.equity == after_exit.equity
    assert restored.buying_power == after_exit.buying_power
    assert restored.realized_pnl == after_exit.realized_pnl
    assert restored.unrealized_pnl == after_exit.unrealized_pnl
    assert restored.reserved_risk == after_exit.reserved_risk
    assert restored.trades_today == after_exit.trades_today
    assert restored.positions == after_exit.positions

def test_wca_restart_recovery_restores_all_local_paper_states_exactly_once() -> None:
    scenarios = (
        ("flat", _seed_recovery_flat, {"account_rows": 1, "position_rows": 0, "fill_rows": 0, "open_order_rows": 0}),
        ("open_position", _seed_recovery_open_position, {"account_rows": 1, "position_rows": 1, "fill_rows": 1, "open_order_rows": 2}),
        ("pending_entry", _seed_recovery_pending_entry, {"account_rows": 1, "position_rows": 0, "fill_rows": 0, "open_order_rows": 1}),
        ("partial_fill", _seed_recovery_partial_fill, {"account_rows": 1, "position_rows": 1, "fill_rows": 1, "open_order_rows": 3}),
        ("pending_exit", _seed_recovery_pending_exit, {"account_rows": 1, "position_rows": 1, "fill_rows": 1, "open_order_rows": 3}),
        ("stop_active", _seed_recovery_stop_active, {"account_rows": 1, "position_rows": 1, "fill_rows": 1, "active_stop_rows": 1}),
        ("target_active", _seed_recovery_target_active, {"account_rows": 1, "position_rows": 1, "fill_rows": 1, "active_target_rows": 1}),
    )

    for scenario_name, seed, expected in scenarios:
        repository = repository_for_isolation()
        account_id = f"wca-recovery-{scenario_name}"
        seed(repository, account_id=account_id)

        before = _wca_restart_recovery_fingerprint(repository, account_id=account_id)
        for field, expected_value in expected.items():
            assert before[field] == expected_value, (scenario_name, field, before)

        restarted_repository = WcaSqliteRepository(f"sqlite:///{repository.path}")
        restarted_broker = WcaLocalPaperBroker(repository=restarted_repository, account_id=account_id, symbol="SPY", starting_balance=1.0)
        restarted_broker.refresh_account_snapshot()
        restarted_broker.get_open_orders(symbol="SPY")
        WcaLocalPaperAccount.restore(restarted_repository, account_id=account_id, symbol="SPY", starting_balance=1.0).get_account_snapshot()

        after = _wca_restart_recovery_fingerprint(restarted_repository, account_id=account_id)
        again = _wca_restart_recovery_fingerprint(restarted_repository, account_id=account_id)
        assert after == before, (scenario_name, after, before)
        assert again == before, (scenario_name, again, before)


def _seed_recovery_flat(repository: WcaSqliteRepository, *, account_id: str) -> None:
    broker = WcaLocalPaperBroker(repository=repository, account_id=account_id, symbol="SPY", starting_balance=100_000.0)
    account = broker.refresh_account()
    broker._account().persist(repository, symbol="SPY", timestamp="2026-01-05T14:30:00+00:00")
    assert account["cash"] == 100_000.0
    assert account["equity"] == 100_000.0


def _seed_recovery_open_position(repository: WcaSqliteRepository, *, account_id: str) -> None:
    _, request, broker = _submit_wca_entry(repository, suffix=f"recovery-open-{account_id}", account_id=account_id, limit_price=600.0, stop_price=598.0, target_price=604.0, quantity=10)
    fills = _fill_entry(repository, broker, request)
    assert len(fills) == 1
    assert WcaLocalPaperAccount.restore(repository, account_id=account_id, symbol="SPY", starting_balance=100_000.0).get_account_snapshot().positions[0].quantity == 10


def _seed_recovery_pending_entry(repository: WcaSqliteRepository, *, account_id: str) -> None:
    _, _, broker = _submit_wca_entry(repository, suffix=f"recovery-pending-entry-{account_id}", account_id=account_id, limit_price=600.0, stop_price=598.0, target_price=604.0, quantity=10)
    broker._account().persist(repository, symbol="SPY", timestamp="2026-01-05T14:31:00+00:00")


def _seed_recovery_partial_fill(repository: WcaSqliteRepository, *, account_id: str) -> None:
    fill_model = WcaLocalPaperFillModel(max_fill_quantity=4, allow_partial_fills=True)
    _, request, broker = _submit_wca_entry(repository, suffix=f"recovery-partial-{account_id}", account_id=account_id, fill_model=fill_model, limit_price=600.0, stop_price=598.0, target_price=604.0, quantity=10)
    fills = _fill_entry(repository, broker, request, quantity=4)
    assert len(fills) == 1
    assert fills[0].remaining_quantity == 6


def _seed_recovery_pending_exit(repository: WcaSqliteRepository, *, account_id: str) -> None:
    _seed_recovery_open_position(repository, account_id=account_id)
    _insert_wca_pending_exit_order(repository, account_id=account_id, quantity=5, limit_price=610.0)


def _seed_recovery_stop_active(repository: WcaSqliteRepository, *, account_id: str) -> None:
    _seed_recovery_open_position(repository, account_id=account_id)
    assert _wca_restart_recovery_fingerprint(repository, account_id=account_id)["active_stop_rows"] == 1


def _seed_recovery_target_active(repository: WcaSqliteRepository, *, account_id: str) -> None:
    _seed_recovery_open_position(repository, account_id=account_id)
    assert _wca_restart_recovery_fingerprint(repository, account_id=account_id)["active_target_rows"] == 1


def _insert_wca_pending_exit_order(repository: WcaSqliteRepository, *, account_id: str, quantity: int, limit_price: float, symbol: str = "SPY") -> None:
    now = "2026-01-05T15:05:00+00:00"
    broker_order_id = f"wca-local-pending-exit-{account_id}"
    client_order_id = f"wca-pending-exit-{account_id}"
    payload = {
        "id": broker_order_id,
        "broker_order_id": broker_order_id,
        "algorithm_id": WCA_ALGORITHM_ID,
        "account_id": account_id,
        "local_account_id": account_id,
        "symbol": symbol,
        "side": WcaSide.SELL.value,
        "qty": quantity,
        "quantity": quantity,
        "limit_price": limit_price,
        "type": "limit",
        "order_type": "LIMIT",
        "status": "accepted",
        "client_order_id": client_order_id,
        "idempotency_key": client_order_id,
        "decision_id": client_order_id,
        "order_intent_id": client_order_id,
        "ownership": {"algorithm_id": WCA_ALGORITHM_ID, "account_id": account_id, "local_account_id": account_id, "symbol": symbol},
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    }
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_broker_orders (
                broker_order_id, algorithm_id, account_id, symbol, timestamp,
                configuration_version, engine_version, market_snapshot_id, decision_id,
                run_id, order_intent_id, idempotency_key, side, quantity,
                status, client_order_id, request_payload_json, response_payload_json,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                broker_order_id,
                WCA_ALGORITHM_ID,
                account_id,
                symbol,
                now,
                "mandatory-restart-recovery",
                "mandatory-restart-recovery",
                "recovery-market",
                client_order_id,
                "recovery-run",
                client_order_id,
                client_order_id,
                WcaSide.SELL.value,
                quantity,
                WcaOrderStatus.ACKNOWLEDGED.value,
                client_order_id,
                json.dumps(payload, sort_keys=True),
                json.dumps(payload, sort_keys=True),
                json.dumps(payload, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT INTO wca_local_orders (
                local_order_id, algorithm_id, local_account_id, client_order_id,
                symbol, side, order_type, quantity, remaining_quantity,
                limit_price, stop_price, target_price, status, created_at,
                updated_at, decision_id, idempotency_key, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                broker_order_id,
                WCA_ALGORITHM_ID,
                account_id,
                client_order_id,
                symbol,
                WcaSide.SELL.value,
                "LIMIT",
                quantity,
                quantity,
                limit_price,
                None,
                None,
                WcaOrderStatus.ACKNOWLEDGED.value,
                now,
                now,
                client_order_id,
                client_order_id,
                json.dumps({"snapshot": payload, "account_snapshot": {"algorithm_id": WCA_ALGORITHM_ID, "local_account_id": account_id, "symbol": symbol}}, sort_keys=True),
            ),
        )


def _wca_restart_recovery_fingerprint(repository: WcaSqliteRepository, *, account_id: str, symbol: str = "SPY") -> dict[str, object]:
    terminal = (WcaOrderStatus.FILLED.value, WcaOrderStatus.REJECTED.value, WcaOrderStatus.CANCELLED.value, WcaOrderStatus.RECONCILED.value)
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        account_rows = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT starting_balance, cash, equity, buying_power, realized_pnl,
                       unrealized_pnl, daily_realized_pnl, daily_unrealized_pnl,
                       daily_loss, gross_exposure, net_exposure, reserved_risk,
                       trades_today, session_date, circuit_breaker_state, cooldown_until,
                       last_mark_timestamp, state_version
                FROM wca_local_paper_account
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY local_account_id, symbol
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        positions = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT position_id, side, quantity, average_entry_price, stop_price,
                       target_price, realized_pnl, unrealized_pnl
                FROM wca_local_positions
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY position_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        lots = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT lot_id, side, quantity, remaining_quantity, entry_price,
                       decision_id, order_intent_id
                FROM wca_local_lots
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY lot_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        local_orders = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT local_order_id, client_order_id, side, order_type, quantity,
                       remaining_quantity, limit_price, stop_price, target_price,
                       status, decision_id, idempotency_key
                FROM wca_local_orders
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY local_order_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        fills = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT fill_id, order_id, side, quantity, fill_price,
                       commissions, fees, slippage, timestamp
                FROM wca_local_fills
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY fill_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        broker_orders = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT broker_order_id, client_order_id, side, quantity, status,
                       idempotency_key, order_intent_id
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY broker_order_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        outbox = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT outbox_id, client_order_id, order_intent_id, idempotency_key,
                       status
                FROM wca_execution_outbox
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY outbox_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        projection = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT open_quantity, average_entry_price, realized_pnl,
                       unrealized_pnl, reserved_risk
                FROM wca_inventory_projection
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                ORDER BY broker_account_id, symbol
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        daily_state = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT session_date, trades_completed_today, realized_pnl_today,
                       daily_loss, current_reserved_risk, cooldown_until,
                       circuit_breaker_state
                FROM wca_daily_state
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                ORDER BY session_date
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        ledger = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT inventory_event_id, event_type, client_order_id, broker_order_id,
                       fill_id, quantity, filled_quantity, remaining_quantity,
                       realized_pnl, unrealized_pnl, reserved_risk
                FROM wca_inventory_ledger
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                ORDER BY inventory_event_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        trade_ledger = tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT trade_id, side, quantity, pnl
                FROM wca_trade_ledger
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY trade_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        )
        open_orders = tuple(order for order in local_orders if order[9] not in terminal)
        active_stops = tuple(order for order in open_orders if str(order[1]).startswith("wca-protection-") and order[3] == "STOP_LIMIT")
        active_targets = tuple(order for order in open_orders if str(order[1]).startswith("wca-protection-") and order[3] == "LIMIT")
        return {
            "account": account_rows,
            "positions": positions,
            "lots": lots,
            "local_orders": local_orders,
            "fills": fills,
            "broker_orders": broker_orders,
            "outbox": outbox,
            "projection": projection,
            "daily_state": daily_state,
            "ledger": ledger,
            "trade_ledger": trade_ledger,
            "account_rows": len(account_rows),
            "position_rows": len(positions),
            "fill_rows": len(fills),
            "open_order_rows": len(open_orders),
            "active_stop_rows": len(active_stops),
            "active_target_rows": len(active_targets),
        }
def repository_for_isolation() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-mandatory-isolation-{uuid4().hex}.sqlite'}")


def _reserve_wca_order(
    repository: WcaSqliteRepository,
    *,
    suffix: str,
    account_id: str,
    limit_price: float | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
    quantity: int | None = None,
):
    decision = decision_with_order()
    assert decision.proposed_order is not None
    order_updates = {
        "decision_id": f"{decision.decision_id}-{suffix}",
        "idempotency_key": f"mandatory-{suffix}",
        "account_id": account_id,
        "configuration_version": decision.configuration_version,
        "configuration_hash": decision.configuration_hash,
    }
    if quantity is not None:
        order_updates["quantity"] = int(quantity)
    if limit_price is not None:
        order_updates["limit_price"] = limit_price
        order_updates["trigger_price"] = limit_price
    if stop_price is not None:
        order_updates["stop_price"] = stop_price
    if target_price is not None:
        order_updates["target_price"] = target_price
    proposed = decision.proposed_order.model_copy(update=order_updates)
    decision_updates = {"decision_id": f"{decision.decision_id}-{suffix}", "proposed_order": proposed}
    if limit_price is not None and decision.market_snapshot.candles:
        candles = tuple(decision.market_snapshot.candles)
        reference = candles[-1].model_copy(update={"open": limit_price, "high": max(limit_price, target_price or limit_price), "low": min(limit_price, stop_price or limit_price), "close": limit_price})
        decision_updates["market_snapshot"] = decision.market_snapshot.model_copy(update={"candles": (*candles[:-1], reference)})
    if quantity is not None or limit_price is not None or stop_price is not None or target_price is not None:
        sized_quantity = int(quantity or proposed.quantity)
        sized_entry = float(limit_price or proposed.limit_price or decision.sizing.entry_price)
        sized_stop = float(stop_price or proposed.stop_price or decision.sizing.stop_price)
        sized_target = float(target_price or proposed.target_price or decision.sizing.target_price)
        stop_distance = abs(sized_entry - sized_stop)
        stop_risk = round(sized_quantity * stop_distance, 10)
        reward_risk = abs(sized_target - sized_entry) / stop_distance if stop_distance > 0 else decision.sizing.reward_risk_ratio
        decision_updates["sizing"] = decision.sizing.model_copy(update={"final_quantity": sized_quantity, "entry_price": sized_entry, "stop_price": sized_stop, "target_price": sized_target, "stop_distance": stop_distance, "risk_dollars": stop_risk, "stop_risk_dollars": stop_risk, "approved_risk_budget": max(stop_risk, float(decision.sizing.approved_risk_budget or 0.0)), "reward_risk_ratio": reward_risk})
    decision = decision.model_copy(update=decision_updates)
    request = build_wca_paper_broker_request(proposed)
    reservation = _reserve_existing_order(repository, decision=decision, request=request, run_id="isolation-run")
    return decision, request, reservation

def _reserve_existing_order(repository: WcaSqliteRepository, *, decision, request, run_id: str):
    return repository.reserve_decision_order_and_outbox(
        decision,
        run_id=run_id,
        account_id=request.account_id,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=_validation_context(decision, request),
    )


def _validation_context(decision, request) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=request.account_id,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER,
        requires_executable_paper_stage=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=None,
        candle_freshness_seconds=120,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        max_approved_quantity=1000,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        protective_exit_plan_present=True,
        idempotency_required=True,
    )


def _submit_wca_entry(
    repository: WcaSqliteRepository,
    *,
    suffix: str,
    account_id: str,
    fill_model: WcaLocalPaperFillModel | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
    quantity: int | None = None,
):
    decision, request, reservation = _reserve_wca_order(
        repository,
        suffix=suffix,
        account_id=account_id,
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=quantity,
    )
    assert reservation.created is True
    broker = WcaLocalPaperBroker(repository=repository, account_id=account_id, symbol=request.symbol, fill_model=fill_model)
    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id=f"mandatory-{suffix}")
    assert result.submitted is True
    assert result.state == WcaOrderStatus.ACKNOWLEDGED
    return decision, request, broker

def _fill_entry(repository: WcaSqliteRepository, broker: WcaLocalPaperBroker, request, *, quantity: int | None = None):
    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND idempotency_key = ?
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, request.account_id, request.symbol, request.idempotency_key),
        )
    update = {
        "symbol": request.symbol,
        "bid": request.limit_price - 0.01,
        "ask": request.limit_price,
        "timestamp": market_timestamp.isoformat(),
        "volume": (quantity or request.quantity) * 10,
    }
    if quantity is not None:
        broker.fill_model = WcaLocalPaperFillModel(max_fill_quantity=quantity, allow_partial_fills=True)
    return broker.process_market_update(update)



def _assert_wca_sell_cannot_close_foreign_position(algorithm_id: str) -> None:
    repository = repository_for_isolation()
    with pytest.raises(sqlite3.IntegrityError, match="non-WCA algorithm_id rejected"):
        _insert_foreign_owned_lot(repository, algorithm_id=algorithm_id, account_id="paper-sell-block", quantity=11)
    broker = WcaLocalPaperBroker(repository=repository, account_id="paper-sell-block", symbol="SPY")

    with pytest.raises(WcaLocalPaperBrokerConfigurationError, match="wca.local_paper.wca_owned_quantity_required"):
        broker.close_or_reduce_wca_position(symbol="SPY", quantity=1, side=WcaSide.BUY, client_order_id=f"wca-sell-{algorithm_id}", price=101.0)

    assert _foreign_lot_quantity(repository, algorithm_id=algorithm_id, account_id="paper-sell-block") == 0

def _insert_foreign_owned_lot(repository: WcaSqliteRepository, *, algorithm_id: str, account_id: str, quantity: int, symbol: str = "SPY") -> None:
    now = "2026-01-05T15:00:00+00:00"
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_owned_lots (
                lot_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, position_id,
                side, quantity, status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{algorithm_id}-lot-{uuid4().hex}",
                algorithm_id,
                account_id,
                symbol,
                now,
                "foreign-config",
                "foreign-engine",
                "foreign-market",
                "foreign-decision",
                "foreign-run",
                f"{algorithm_id}-position",
                "BUY",
                quantity,
                "open",
                json.dumps({"owner": algorithm_id, "entry_price": 100.0}),
            ),
        )


def _foreign_lot_quantity(repository: WcaSqliteRepository, *, algorithm_id: str, account_id: str, symbol: str = "SPY") -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(
            conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM wca_owned_lots WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open'",
                (algorithm_id, account_id, symbol),
            ).fetchone()[0]
        )


def _insert_foreign_broker_order(repository: WcaSqliteRepository, *, algorithm_id: str, account_id: str, client_order_id: str, symbol: str = "SPY") -> str:
    now = "2026-01-05T15:00:00+00:00"
    broker_order_id = f"{algorithm_id}-broker-{uuid4().hex}"
    payload = {
        "id": broker_order_id,
        "broker_order_id": broker_order_id,
        "algorithm_id": algorithm_id,
        "account_id": account_id,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "side": "sell",
        "qty": "1",
        "limit_price": "99.0",
        "stop_price": "99.0",
        "status": "accepted",
        "ownership": {
            "protected_algorithm_id": algorithm_id,
            "position_owner": algorithm_id,
            "exit_owner": algorithm_id,
            "local_account_id": account_id,
            "symbol": symbol,
            "position_id": f"{algorithm_id}-position",
        },
    }
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_broker_orders (
                broker_order_id, algorithm_id, account_id, symbol, timestamp,
                configuration_version, engine_version, market_snapshot_id, decision_id,
                run_id, order_intent_id, idempotency_key, side, quantity, status,
                client_order_id, request_payload_json, response_payload_json, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                broker_order_id,
                algorithm_id,
                account_id,
                symbol,
                now,
                "foreign-config",
                "foreign-engine",
                "foreign-market",
                "foreign-decision",
                "foreign-run",
                "foreign-intent",
                f"{client_order_id}-idem",
                "SELL",
                1,
                WcaOrderStatus.ACKNOWLEDGED.value,
                client_order_id,
                json.dumps(payload, sort_keys=True),
                json.dumps(payload, sort_keys=True),
                json.dumps(payload, sort_keys=True),
            ),
        )
    return broker_order_id


def _insert_foreign_fill(repository: WcaSqliteRepository, *, algorithm_id: str, quantity: int, price: float, symbol: str = "SPY") -> None:
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_attributed_fills (
                fill_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{algorithm_id}-fill-{uuid4().hex}",
                algorithm_id,
                symbol,
                "2026-01-05T15:00:00+00:00",
                "foreign-config",
                "foreign-engine",
                "foreign-market",
                "foreign-decision",
                "foreign-run",
                "BUY",
                quantity,
                json.dumps({"owner": algorithm_id, "fill_price": price}),
            ),
        )


def _insert_foreign_trade(repository: WcaSqliteRepository, *, algorithm_id: str, pnl: float, symbol: str = "SPY") -> None:
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_trade_ledger (
                trade_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, pnl, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{algorithm_id}-trade-{uuid4().hex}",
                algorithm_id,
                symbol,
                "2026-01-05T15:00:00+00:00",
                "foreign-config",
                "foreign-engine",
                "foreign-market",
                "foreign-decision",
                "foreign-run",
                "SELL",
                1,
                pnl,
                json.dumps({"owner": algorithm_id}),
            ),
        )




def _algorithm_inventory_row_count(repository: WcaSqliteRepository, algorithm_id: str) -> int:
    tables = (
        "wca_local_paper_account",
        "wca_local_positions",
        "wca_local_lots",
        "wca_local_orders",
        "wca_local_fills",
        "wca_inventory_ledger",
        "wca_owned_lots",
        "wca_attributed_fills",
        "wca_trade_ledger",
    )
    with sqlite3.connect(repository.path) as conn:
        return sum(int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE algorithm_id = ?", (algorithm_id,)).fetchone()[0]) for table in tables)

def _non_wca_inventory_row_count(repository: WcaSqliteRepository) -> int:
    tables = (
        "wca_local_paper_account",
        "wca_local_positions",
        "wca_local_lots",
        "wca_local_orders",
        "wca_local_fills",
        "wca_inventory_ledger",
        "wca_owned_lots",
        "wca_attributed_fills",
        "wca_trade_ledger",
    )
    with sqlite3.connect(repository.path) as conn:
        return sum(int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE algorithm_id <> ?", (WCA_ALGORITHM_ID,)).fetchone()[0]) for table in tables)

def _cross_algorithm_block_count(repository: WcaSqliteRepository, *, account_id: str, symbol: str = "SPY") -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM wca_inventory_ledger
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND payload_json LIKE ?
                """,
                (WCA_ALGORITHM_ID, account_id, symbol, f"%{WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED}%"),
            ).fetchone()[0]
        )


def _risk_request(*, account_id: str, quantity: int, limit_price: float, stop_price: float) -> WcaPaperBrokerOrderRequest:
    return WcaPaperBrokerOrderRequest(
        account_id=account_id,
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=quantity,
        order_type="STOP_LIMIT",
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=limit_price + 3.0,
        client_order_id=f"{account_id}-client",
        idempotency_key=f"{account_id}-idem",
        decision_id=f"{account_id}-decision",
        order_intent_id=f"{account_id}-intent",
        configuration_version="mandatory-isolation-risk",
    )
