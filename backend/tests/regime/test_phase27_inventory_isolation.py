from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime import stateful_core
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.reconciliation import run_regime_broker_reconciliation
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.trade_management import manage_regime_positions_for_completed_bar
from backend.tests.regime.test_phase22_automatic_paper_readiness import (
    IDENTITY,
    _buy_decision,
    _completed_bar_payload,
    _fresh_account,
)


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)
TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase27_inventory_isolation"


def test_phase27_inventory_rejects_cross_identity_fill_before_ledger_mutation() -> None:
    repository, identity = _repository()
    other_instance_fill = {**_fill(identity, "fill-cross", "Buy", 1, 100.0), "algorithmInstanceId": "another-regime-instance"}

    try:
        repository.apply_inventory_fill(identity, other_instance_fill)
    except ValueError as exc:
        assert "cross-identity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Regime inventory accepted a fill for a different identity")

    assert repository.current_inventory_snapshot(identity)["quantity"] == 0


def test_phase27_exit_fill_cannot_exceed_regime_owned_position_quantity() -> None:
    repository, identity = _repository()
    manager = RegimePositionManager(repository)
    manager.apply_fill_observation(identity, _fill(identity, "entry-fill", "Buy", 3, 100.0, position_effect="enter_long"))

    oversized_exit = _fill(identity, "exit-fill", "Sell", 5, 101.0, position_effect="exit_long")

    try:
        manager.apply_fill_observation(identity, oversized_exit)
    except ValueError as exc:
        assert "exceeds owned position" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Regime position manager accepted an oversized exit fill")

    assert repository.current_inventory_snapshot(identity)["quantity"] == 3
    assert repository.latest_open_regime_positions(identity)[0]["filledQuantity"] == 3


def test_phase27_aggregate_broker_spy_position_blocks_entries_without_assigning_shares_to_regime() -> None:
    repository, identity = _repository()
    RegimePositionManager(repository).apply_fill_observation(identity, _fill(identity, "entry-fill", "Buy", 2, 100.0, position_effect="enter_long"))

    reconciliation = run_regime_broker_reconciliation(
        repository=repository,
        identity=identity,
        broker_positions=[{"symbol": "SPY", "quantity": 10, "averageFillPrice": 100.0}],
        broker_open_orders=[],
        broker_fills=[],
        evaluated_at=NOW,
        trigger="phase27_aggregate_position",
    )

    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert "regime.reconciliation.unattributed_broker_position:unknown" in reconciliation["discrepancies"]
    assert repository.current_inventory_snapshot(identity)["quantity"] == 2
    assert repository.latest_open_regime_positions(identity)[0]["filledQuantity"] == 2


def test_phase27_trade_management_exit_quantity_is_capped_to_owned_position() -> None:
    repository, identity = _repository()
    RegimePositionManager(repository).apply_fill_observation(identity, _fill(identity, "entry-fill", "Buy", 4, 100.0, position_effect="enter_long"))

    result = manage_regime_positions_for_completed_bar(
        repository=repository,
        identity=identity,
        candle={"timestamp": NOW.isoformat().replace("+00:00", "Z"), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100_000},
        settings_snapshot={"settingsVersion": "phase27-settings", "profileVersion": "phase27-profile", "execution": {"orderTimeToLiveSeconds": 300}},
        confirmed_regime="strong_uptrend",
        global_emergency_flatten=True,
        evaluated_at=NOW,
    )

    assert result["exitIntentsCreated"] == 1
    assert result["exitIntents"][0]["quantity"] == 4
    assert result["exitIntents"][0]["ownedPositionQuantity"] == 4


def test_phase27_stateful_core_ignores_shared_account_position_as_regime_position(monkeypatch) -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
    captured_open_positions: list[dict] = []

    def decision(snapshot, *, settings, previous_state):
        captured_open_positions.append(dict(settings.get("openPosition") or {}))
        return _buy_decision(settings)

    monkeypatch.setattr(stateful_core, "calculate_regime_decision", decision)
    monkeypatch.setattr(stateful_core, "calculate_regime_position_size", lambda *args, **kwargs: _zero_sizing())

    stateful_core.process_completed_bar(
        snapshot=build_regime_market_snapshot(_completed_bar_payload()["marketData"]),
        settings_snapshot=settings,
        previous_state=None,
        inventory_snapshot={**IDENTITY, "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0, "inventoryReconciled": True},
        account_snapshot={**_fresh_account(), "position": {"symbol": "SPY", "quantity": 999}, "currentPosition": {"symbol": "SPY", "quantity": 999}},
    )

    assert captured_open_positions == [{}]


def _repository() -> tuple[RegimeRepository, dict[str, str]]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-paper-default",
        "accountId": "paper-account-123",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return RegimeRepository(f"sqlite:///{path}"), identity


def _fill(
    identity: dict[str, str],
    fill_id: str,
    side: str,
    quantity: int,
    price: float,
    *,
    position_effect: str = "enter_long",
) -> dict[str, object]:
    return {
        **identity,
        "algorithmId": "regime",
        "decisionId": f"decision-{fill_id}",
        "orderIntentId": "intent-entry" if position_effect.startswith("enter") else "intent-exit",
        "brokerOrderId": f"broker-{fill_id}",
        "fillId": fill_id,
        "side": side,
        "positionEffect": position_effect,
        "filledQuantity": quantity,
        "averageFillPrice": price,
        "filledAt": (NOW + timedelta(seconds=quantity)).isoformat().replace("+00:00", "Z"),
        "submittedQuantity": quantity,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
    }


def _zero_sizing():
    from backend.app.algorithms.regime.contracts import RegimeSizingResult

    return RegimeSizingResult(
        quantity=0,
        risk_dollars=0.0,
        stop_distance=0.0,
        stop_price=None,
        target_price=None,
        limiting_factor="test",
        quantity_caps=(),
        blockers=("test.zero_sizing",),
    )
