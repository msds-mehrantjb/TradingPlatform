from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.local_paper_account import (
    REGIME_LOCAL_PAPER_SOURCE_AUTHORITY,
    RegimeLocalPaperAccount,
)
from backend.app.algorithms.regime.persistence import RegimeSqliteRepository


def test_regime_local_paper_account_starts_with_independent_balance() -> None:
    regime = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)
    voting = RegimeLocalPaperAccount(accountId="voting-ensemble-paper", initialBalance=100_000)
    weighted = RegimeLocalPaperAccount(accountId="weighted-voting-paper", initialBalance=100_000)

    regime.apply_fill(_fill(accountId="regime-paper", side="Buy", quantity=10, price=100.0, fillId="regime-buy"))

    assert regime.get_account_snapshot().cash == 99_000.0
    assert voting.get_account_snapshot().cash == 100_000.0
    assert weighted.get_account_snapshot().cash == 100_000.0


def test_regime_trade_modifies_regime_cash_only_and_other_algorithm_balance_remains_unchanged() -> None:
    regime = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)
    voting = RegimeLocalPaperAccount(accountId="voting-ensemble-paper", initialBalance=100_000)
    weighted = RegimeLocalPaperAccount(accountId="weighted-voting-paper", initialBalance=100_000)

    regime.apply_fill(_fill(accountId="regime-paper", side="Buy", quantity=100, price=500.0, fillId="regime-only-cash"))

    assert regime.get_account_snapshot().cash == 50_000.0
    assert voting.get_account_snapshot().cash == 100_000.0
    assert weighted.get_account_snapshot().cash == 100_000.0

def test_regime_local_paper_account_tracks_cash_equity_pnl_and_trade_stats() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)

    entry = account.apply_fill(
        _fill(
            accountId="regime-paper",
            side="Buy",
            quantity=10,
            price=100.0,
            fillId="entry-fill",
            commission=1.0,
            slippage=0.5,
            stopPrice=98.0,
            targetPrice=104.0,
        )
    )
    assert entry.cash == 98_998.5
    assert entry.equity == 99_998.5
    assert entry.feesPaid == 1.0
    assert entry.slippagePaid == 0.5
    assert entry.positions[0].quantity == 10
    assert entry.positions[0].averageEntryPrice == 100.0
    assert entry.positions[0].stopPrice == 98.0
    assert entry.positions[0].targetPrice == 104.0

    marked = account.mark_to_market(symbol="SPY", marketPrice=103.0)
    assert marked.unrealizedPnl == 30.0
    assert marked.equity == 100_028.5
    assert marked.grossExposure == 1_030.0
    assert marked.netExposure == 1_030.0

    exit_snapshot = account.apply_fill(
        _fill(accountId="regime-paper", side="Sell", quantity=10, price=103.0, fillId="exit-fill", commission=1.0)
    )
    assert exit_snapshot.cash == 100_027.5
    assert exit_snapshot.equity == 100_027.5
    assert exit_snapshot.realizedPnl == 29.0
    assert exit_snapshot.dailyRealizedPnl == 29.0
    assert exit_snapshot.tradeCount == 1
    assert exit_snapshot.winningTrades == 1
    assert exit_snapshot.losingTrades == 0
    assert exit_snapshot.consecutiveLosses == 0
    assert exit_snapshot.positions == ()


def test_voting_and_weighted_fills_cannot_change_regime_inventory() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)

    for algorithm_id in ("voting_ensemble", "weighted_voting"):
        with pytest.raises(ValueError, match="cross-algorithm"):
            account.apply_fill(_fill(algorithmId=algorithm_id, accountId="regime-paper", fillId=f"{algorithm_id}-fill"))

    snapshot = account.get_account_snapshot()
    assert snapshot.cash == 100_000.0
    assert snapshot.positions == ()
    assert snapshot.realizedPnl == 0.0
    assert snapshot.fills == ()

def test_regime_local_paper_account_rejects_cross_algorithm_fill_and_account_mismatch() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)

    with pytest.raises(ValueError, match="cross-algorithm"):
        account.apply_fill(_fill(algorithmId="voting_ensemble", accountId="regime-paper", fillId="foreign-fill"))

    with pytest.raises(ValueError, match="accountId mismatch"):
        account.apply_fill(_fill(accountId="voting-ensemble-paper", fillId="wrong-account-fill"))

    snapshot = account.get_account_snapshot()
    assert snapshot.cash == 100_000.0
    assert snapshot.equity == 100_000.0
    assert snapshot.tradeCount == 0
    assert snapshot.fills == ()


def test_regime_local_paper_account_rejects_cross_algorithm_cash_mutation() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)

    with pytest.raises(ValueError, match="cross-algorithm mutation"):
        account.reserve_cash(100.0, algorithmId="wca")

    assert account.get_account_snapshot().reservedCash == 0.0


def test_regime_local_paper_account_rejects_impossible_restored_cash_state() -> None:
    snapshot = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000).get_account_snapshot().to_dict()
    snapshot["cash"] = -1.0

    with pytest.raises(ValueError, match="cash cannot be negative"):
        RegimeLocalPaperAccount.from_snapshot(snapshot)

def test_regime_local_paper_account_duplicate_fill_is_idempotent() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)
    fill = _fill(accountId="regime-paper", quantity=5, price=200.0, fillId="same-fill")

    first = account.apply_fill(fill)
    second = account.apply_fill(fill)

    assert first.cash == second.cash == 99_000.0
    assert first.positions[0].quantity == second.positions[0].quantity == 5
    assert len(second.fills) == 1


def test_regime_local_paper_account_snapshot_is_immutable() -> None:
    account = RegimeLocalPaperAccount(accountId="regime-paper", initialBalance=100_000)
    snapshot = account.get_account_snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.cash = 1.0  # type: ignore[misc]


def test_regime_local_paper_account_persists_and_restores_from_regime_repository() -> None:
    repository = RegimeSqliteRepository(f"sqlite:///{_temp_db_path()}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-a",
        "accountId": "regime-paper",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    account = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=100_000)
    account.apply_fill(
        _fill(
            algorithmInstanceId="regime-a",
            accountId="regime-paper",
            quantity=4,
            price=250.0,
            fillId="persist-entry",
        )
    )
    account.mark_to_market(symbol="SPY", marketPrice=251.0)
    before = account.persist(repository, symbol="SPY").to_dict()

    restored = RegimeLocalPaperAccount.restore(repository, identity=identity, initialBalance=1).get_account_snapshot().to_dict()

    assert restored["algorithmId"] == "regime"
    assert restored["sourceAuthority"] == REGIME_LOCAL_PAPER_SOURCE_AUTHORITY
    assert restored["initialBalance"] == before["initialBalance"] == 100_000.0
    assert restored["cash"] == before["cash"]
    assert restored["equity"] == before["equity"]
    assert restored["availableBuyingPower"] == before["availableBuyingPower"]
    assert restored["positions"][0]["quantity"] == 4
    assert restored["openOrders"] == []
    assert restored["reservations"] == []
    assert restored["dailyCounters"]["dailyUnrealizedPnl"] == 4.0
    assert restored["riskState"]["grossExposure"] == 1004.0

    with sqlite3.connect(repository.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(regime_local_paper_accounts)")}
        assert {"algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol", "payload_json"}.issubset(columns)
        row = conn.execute(
            """
            SELECT algorithm_id, algorithm_instance_id, account_id, runtime_mode, symbol, payload_json
            FROM regime_local_paper_accounts
            WHERE algorithm_id = 'regime'
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "regime"
    assert row[1] == "regime-a"
    assert row[2] == "regime-paper"
    assert row[3] == "paper"
    assert row[4] == "SPY"


def test_regime_local_paper_account_persistence_rejects_foreign_snapshot() -> None:
    repository = RegimeSqliteRepository(f"sqlite:///{_temp_db_path()}")
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-a",
        "accountId": "regime-paper",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }

    with pytest.raises(ValueError, match="algorithmId='regime'"):
        repository.write_local_paper_account_snapshot(
            identity,
            {
                "algorithmId": "weighted_voting",
                "algorithmInstanceId": "regime-a",
                "accountId": "regime-paper",
                "runtimeMode": "paper",
                "symbol": "SPY",
                "initialBalance": 100_000,
                "cash": 100_000,
                "equity": 100_000,
                "buyingPower": 100_000,
                "availableBuyingPower": 100_000,
            },
        )


def _fill(**overrides: object) -> dict[str, object]:
    quantity = overrides.pop("quantity", 1)
    price = overrides.pop("price", 100.0)
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "regime-paper",
        "runtimeMode": "paper",
        "symbol": "SPY",
        "side": "Buy",
        "filledQuantity": quantity,
        "averageFillPrice": price,
        "filledAt": "2026-07-23T16:00:00Z",
        **overrides,
    }


def _temp_db_path() -> Path:
    return Path("backend") / "tmp" / "tests" / f"regime-local-paper-account-{uuid4().hex}.db"
