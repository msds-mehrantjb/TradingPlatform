from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_regime_positions"


def test_long_position_survives_restart_and_exits_idempotently_at_stop() -> None:
    repository, identity, path = _repository()
    manager = RegimePositionManager(repository)
    fill_result = manager.apply_fill_observation(identity, _fill(side="Buy", stop=99.0, target=103.0))
    position = fill_result["position"]

    restored = RegimePositionManager(RegimeRepository(f"sqlite:///{path}")).restore_open_positions(identity)
    assert restored[0]["positionId"] == position["positionId"]
    assert restored[0]["averageFillPrice"] == 100.0

    first = manager.evaluate_position(
        identity,
        restored[0],
        candle=_candle(low=98.5, high=100.5, close=99.2),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
    )
    counts_after_first = repository.table_counts()
    second = manager.evaluate_position(
        identity,
        first["position"],
        candle=_candle(low=98.0, high=100.0, close=99.0),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
    )

    assert first["exitAction"]["reason"] == "initial_stop"
    assert first["exitAction"]["side"] == "Sell"
    assert second["idempotent"] is True
    assert repository.table_counts()["regime_trades"] == counts_after_first["regime_trades"]


def test_short_position_target_and_stop_are_supported_while_short_entries_disabled_by_default() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    short = manager.apply_fill_observation(identity, _fill(side="Sell", stop=101.0, target=96.0))["position"]

    target = manager.evaluate_position(
        identity,
        short,
        candle=_candle(low=95.5, high=100.2, close=96.0),
        settings_snapshot=_settings(short_entries_enabled=False),
        confirmed_regime="strong_downtrend",
    )

    assert target["exitAction"]["reason"] == "profit_target"
    assert target["exitAction"]["action"] == "exit_short"
    assert target["exitAction"]["side"] == "Buy"


def test_position_manager_keeps_existing_position_protected_during_entry_pause() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    position = manager.apply_fill_observation(identity, _fill())["position"]

    result = manager.evaluate_position(
        identity,
        position,
        candle=_candle(low=99.5, high=101.0, close=100.5),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
        entry_paused=True,
    )

    assert result["action"] == "hold"
    restored = manager.restore_open_positions(identity)
    assert restored[0]["entryPausedWhileProtected"] is True
    assert restored[0]["unrealizedPnl"] > 0


@pytest.mark.parametrize(
    ("position_patch", "regime", "expected_reason"),
    [
        ({"holdingBars": 5}, "strong_uptrend", "maximum_holding_bars"),
        ({}, "event_risk", "risk_off_transition"),
        ({"invalidatedByRegime": True}, "range_bound", "regime_invalidation"),
        ({"strategyInvalidated": True}, "strong_uptrend", "strategy_invalidation"),
        ({"staleProtectiveOrder": True}, "strong_uptrend", "stale_protective_order"),
        ({"reconciliationState": "unresolved_discrepancy"}, "strong_uptrend", "broker_reconciliation_discrepancy"),
    ],
)
def test_exit_reasons_are_explicit(position_patch: dict, regime: str, expected_reason: str) -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    position = {**manager.apply_fill_observation(identity, _fill())["position"], **position_patch}

    result = manager.evaluate_position(
        identity,
        position,
        candle=_candle(low=99.5, high=101.0, close=100.2),
        settings_snapshot=_settings(maximum_holding_bars=5),
        confirmed_regime=regime,
    )

    assert result["exitAction"]["reason"] == expected_reason
    assert f"regime.position.exit.{expected_reason}" in result["reasonCodes"]


def test_end_of_day_and_global_emergency_flatten_are_forced() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    eod_position = manager.apply_fill_observation(identity, _fill(order_intent_id="eod"))["position"]
    emergency_position = manager.apply_fill_observation(identity, _fill(order_intent_id="emergency"))["position"]

    eod = manager.evaluate_position(
        identity,
        eod_position,
        candle=_candle(timestamp="2026-07-23T19:55:00Z", low=99.8, high=100.4, close=100.1),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
    )
    emergency = manager.evaluate_position(
        identity,
        emergency_position,
        candle=_candle(low=99.8, high=100.4, close=100.1),
        settings_snapshot=_settings(),
        confirmed_regime="strong_uptrend",
        global_emergency_flatten=True,
    )

    assert eod["exitAction"]["reason"] == "end_of_day_flatten"
    assert emergency["exitAction"]["reason"] == "global_emergency_flatten"


def test_stop_target_changes_are_audited_and_risk_widening_is_rejected() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    position = manager.apply_fill_observation(identity, _fill(stop=99.0, target=103.0))["position"]

    rejected = manager.update_stop_target(identity, position, stop_price=98.5, reason="regime_transition", settings_version="settings-v2")
    tightened = manager.update_stop_target(identity, position, stop_price=99.5, target_price=102.5, reason="trail_stop", settings_version="settings-v2")

    assert rejected["updated"] is False
    assert rejected["reason"] == "regime.position.stop_widening_rejected"
    assert tightened["updated"] is True
    history = tightened["position"]["stopTargetHistory"]
    assert history[-1]["reason"] == "trail_stop"
    assert history[-1]["settingsVersion"] == "settings-v2"


def test_cross_algorithm_inventory_cannot_mutate_regime_trade_state() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)

    with pytest.raises(ValueError):
        manager.apply_fill_observation(identity, {**_fill(), "algorithmId": "weighted_voting"})

    result = manager.reconcile_broker_observations(identity, [{"algorithmId": "weighted_voting", "positionId": "wv-pos", "quantity": 999}])
    assert result["reconciled"] is True
    assert repository.table_counts()["regime_positions"] == 0
    assert repository.table_counts()["regime_trades"] == 0


def test_reconciliation_discrepancy_blocks_new_entries() -> None:
    repository, identity, _ = _repository()
    manager = RegimePositionManager(repository)
    position = manager.apply_fill_observation(identity, _fill())["position"]

    result = manager.reconcile_broker_observations(identity, [{"algorithmId": "regime", "positionId": position["positionId"], "quantity": 5}])

    assert result["reconciled"] is False
    assert result["blockNewEntries"] is True
    restored = manager.restore_open_positions(identity)
    assert restored[0]["reconciliationState"] == "unresolved_discrepancy"


def _repository() -> tuple[RegimeRepository, dict[str, str], Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"
    identity = {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-position-test",
        "accountId": "paper-account",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }
    return RegimeRepository(f"sqlite:///{path}"), identity, path


def _fill(
    *,
    side: str = "Buy",
    stop: float = 99.0,
    target: float = 103.0,
    order_intent_id: str = "regime-intent-1",
) -> dict:
    return {
        "algorithmId": "regime",
        "decisionId": f"decision-{order_intent_id}",
        "orderIntentId": order_intent_id,
        "fillId": f"fill-{order_intent_id}",
        "brokerOrderId": f"broker-{order_intent_id}",
        "symbol": "SPY",
        "side": side,
        "filledQuantity": 10,
        "submittedQuantity": 10,
        "averageFillPrice": 100.0,
        "stopPrice": stop,
        "targetPrice": target,
        "filledAt": "2026-07-23T14:30:00Z",
        "settingsVersion": "settings-v1",
    }


def _candle(
    *,
    timestamp: str = "2026-07-23T14:31:00Z",
    low: float = 99.5,
    high: float = 100.5,
    close: float = 100.0,
) -> dict:
    return {"timestamp": timestamp, "open": 100.0, "high": high, "low": low, "close": close, "volume": 100_000}


def _settings(*, maximum_holding_bars: int = 20, short_entries_enabled: bool = False) -> dict:
    return {
        "settingsVersion": "settings-v1",
        "maximumHoldingBars": maximum_holding_bars,
        "flattenTimeEt": "15:55",
        "shortEntriesEnabled": short_entries_enabled,
        "exit_policy": {"timeStopBars": 0},
    }
