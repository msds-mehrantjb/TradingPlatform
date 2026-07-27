from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.backtest.execution import simulate_order_execution
from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics
from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime import REGIME_BACKTEST_JOB_STATUSES, RegimeBackgroundJobManager
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.stateful_core import process_completed_bar
from backend.app.main import app


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-step8",
    "accountId": "paper-account-step8",
    "runtimeMode": "backtest",
    "symbol": "SPY",
}


def test_step8_backtest_run_api_enqueues_job_without_inline_execution() -> None:
    client = TestClient(app)
    payload = {"symbol": "SPY", "algorithmInstanceId": f"step8-api-{uuid4().hex}", "candles": fixture_candles(36)}

    started = time.perf_counter()
    response = client.post("/api/regime/backtests/run", json=payload)
    elapsed = time.perf_counter() - started

    assert response.status_code == 202, response.text
    body = response.json()
    assert elapsed < 0.25
    assert body["algorithmId"] == "regime"
    assert body["jobId"].startswith("regime-backtest-")
    assert body["status"] in set(REGIME_BACKTEST_JOB_STATUSES)
    assert body["runtimeMode"] == "backtest"
    assert body["apiHandlersExecuteHeavyWorkInline"] is False
    wait_for_api_job(client, body["jobId"])


def test_step8_background_job_namespace_cannot_mutate_paper_inventory_or_paper_settings() -> None:
    repository = temp_repository()
    paper_identity = {**IDENTITY, "runtimeMode": "paper"}
    repository.record_position_state(paper_identity, {"positionId": "paper-pos", "quantity": 5, "positionStatus": "open"})
    paper_positions_before = repository.read_owned_records("regime_positions", paper_identity)
    manager = RegimeBackgroundJobManager(lambda: RegimeApplicationService(repository=repository), max_concurrent_backtests=1)

    receipt = manager.enqueue(
        "backtest",
        {
            "symbol": "SPY",
            "algorithmInstanceId": IDENTITY["algorithmInstanceId"],
            "accountId": IDENTITY["accountId"],
            "runtimeMode": "paper",
            "inventorySnapshot": {"positions": [{"positionId": "malicious-paper-position", "quantity": 999}]},
            "candles": fixture_candles(36),
        },
    )
    final = wait_for_manager(manager, receipt["jobId"])
    durable = repository.read_backtest_job(receipt["jobId"])
    paper_positions_after = repository.read_owned_records("regime_positions", paper_identity)

    assert final["status"] == "completed"
    assert durable["runtimeMode"] == "backtest"
    assert durable["payload"]["runtimeMode"] == "backtest"
    assert "inventorySnapshot" not in durable["payload"]
    assert paper_positions_after == paper_positions_before
    assert repository.read_owned_records("regime_trades", paper_identity) == []


def test_step8_execution_model_can_miss_expire_and_calculate_costs() -> None:
    intent = {"symbol": "SPY", "side": "Buy", "quantity": 100, "entry_price": 99.0}
    future = [
        {"timestamp": "2026-07-23T13:31:00Z", "open": 100.1, "high": 100.4, "low": 100.0, "close": 100.2, "volume": 1_000, "bid": 100.18, "ask": 100.22},
        {"timestamp": "2026-07-23T13:32:00Z", "open": 100.2, "high": 100.5, "low": 100.1, "close": 100.3, "volume": 1_000, "bid": 100.28, "ask": 100.34},
    ]

    missed = simulate_order_execution(
        intent,
        future,
        start_index=0,
        settings={"maxParticipationPercent": 0.02, "orderTimeToLiveSeconds": 60, "maximumSlippageBps": 4.0},
        market_model={"latencyBars": 0, "ttlBars": 1, "feePerShare": 0.01, "adverseSelectionBps": 1.0},
    )
    filled = simulate_order_execution(
        {**intent, "entry_price": 100.2},
        future,
        start_index=0,
        settings={"maxParticipationPercent": 0.02, "orderTimeToLiveSeconds": 60, "maximumSlippageBps": 4.0},
        market_model={"latencyBars": 0, "ttlBars": 1, "feePerShare": 0.01, "adverseSelectionBps": 1.0},
    )

    assert missed["status"] == "expired"
    assert missed["filledQuantity"] == 0
    assert "regime.backtest.execution.ttl_expired_or_limit_not_reached" in missed["reasonCodes"]
    assert filled["status"] == "partially_filled"
    assert filled["filledQuantity"] == 20
    assert filled["spreadPerShare"] > 0
    assert filled["totalCost"] > filled["fees"]


def test_step8_metrics_costs_drawdown_missed_fill_rate_and_segments_are_calculated() -> None:
    trades = [
        {
            "exitAt": "2026-07-23T13:31:00Z",
            "grossPnl": 120.0,
            "netPnl": 100.0,
            "totalCosts": 20.0,
            "quantity": 10,
            "entryPrice": 100.0,
            "entrySlippage": 1.0,
            "exitSlippage": 2.0,
            "regime": "strong_uptrend",
            "strategyFamily": "trend",
            "strategyId": "moving_average_trend",
            "sessionPhase": "opening",
            "spreadBucket": "tight_spread",
        },
        {
            "exitAt": "2026-07-23T13:32:00Z",
            "grossPnl": -130.0,
            "netPnl": -150.0,
            "totalCosts": 20.0,
            "quantity": 10,
            "entryPrice": 101.0,
            "entrySlippage": 2.0,
            "exitSlippage": 3.0,
            "regime": "range_bound",
            "strategyFamily": "mean_reversion",
            "strategyId": "rsi_mean_reversion",
            "sessionPhase": "intraday",
            "spreadBucket": "wide_spread",
        },
    ]
    decisions = [
        {"signal": "Buy", "orderIntent": {"id": "a"}, "execution": {"status": "filled"}},
        {"signal": "Buy", "orderIntent": {"id": "b"}, "execution": {"status": "expired"}},
    ]

    metrics = calculate_backtest_metrics(trades, decisions, 1_000.0)

    assert metrics["grossPnl"] == -10.0
    assert metrics["netProfit"] == -50.0
    assert metrics["netProfit"] != metrics["grossPnl"]
    assert metrics["maximumDrawdown"] == 150.0
    assert metrics["fillRate"] == 0.5
    assert metrics["missedFillRate"] == 0.5
    assert metrics["realisedSlippage"] == 8.0
    assert metrics["segments"]["confirmedRegime"]["strong_uptrend"]["tradeCount"] == 1
    assert metrics["segments"]["strategyFamily"]["trend"]["netProfit"] == 100.0
    assert metrics["segments"]["spreadBucket"]["wide_spread"]["totalCosts"] == 20.0


def test_step8_walk_forward_and_holdout_do_not_pass_when_not_run_or_insufficient() -> None:
    empty = walk_forward_summary([], [], folds=3, holdout_fraction=0.25)
    no_trade = walk_forward_summary(fixture_candles(12), [], folds=3, holdout_fraction=0.25)
    failing = walk_forward_summary(
        fixture_candles(12),
        [{"exitAt": "2026-07-23T13:32:00Z", "netPnl": -5.0}, {"exitAt": "2026-07-23T13:41:00Z", "netPnl": 0.0}],
        folds=3,
        holdout_fraction=0.25,
        minimum_fold_net_profit=0.0,
        minimum_holdout_net_profit=1.0,
    )

    assert empty["accepted"] is False
    assert empty["status"] == "INSUFFICIENT_EVIDENCE"
    assert empty["holdout"]["status"] == "NOT_RUN"
    assert no_trade["accepted"] is False
    assert any(item["status"] == "INSUFFICIENT_EVIDENCE" for item in no_trade["foldResults"])
    assert failing["accepted"] is False
    assert failing["walkForwardStable"] is False
    assert failing["holdout"]["accepted"] is False


def test_step8_backtest_replay_matches_paper_shadow_for_identical_input() -> None:
    settings = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
    candles = fixture_candles(42)
    account = {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500, "globalRiskCapacityQuantity": 1_000}

    result = run_regime_backtest({"symbol": "SPY", "candles": candles, "__regime_settings_snapshot": settings, "runtimeMode": "backtest", "account": account})
    index = 10
    replay_decision = result["decisions"][index]
    direct = process_completed_bar(
        snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles[: index + 1], "oneMinuteCandles": candles[: index + 1]}),
        settings_snapshot=settings,
        previous_state=result["decisions"][index - 1]["runtimeState"],
        inventory_snapshot={**IDENTITY, "dataManifestHash": replay_decision["dataManifestHash"]},
        account_snapshot=account,
    )

    assert replay_decision["decisionId"] == direct["decision"]["decision_id"]
    assert replay_decision["signal"] == direct["decision"]["signal"]
    assert replay_decision["regime"] == direct["decision"]["confirmed_state"]["confirmed_regime"]
    assert replay_decision["runtimeState"] == direct["nextRuntimeState"]
    assert replay_decision["pointInTime"]["futureCandlesVisible"] == 0


def fixture_candles(count: int) -> list[dict[str, float | str]]:
    candles: list[dict[str, float | str]] = []
    price = 100.0
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    for index in range(count):
        price += 0.08
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": timestamp,
                "open": price - 0.03,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price,
                "volume": 120_000 + index,
                "bid": price - 0.01,
                "ask": price + 0.01,
            }
        )
    return candles


def temp_repository() -> RegimeRepository:
    root = Path(__file__).resolve().parents[1] / "tmp" / "regime_step8"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


def wait_for_manager(manager: RegimeBackgroundJobManager, job_id: str) -> dict:
    for _ in range(80):
        payload = manager.get(job_id)
        if payload["status"] in {"completed", "failed", "cancelled", "quarantined"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Regime backtest job did not finish: {job_id}")


def wait_for_api_job(client: TestClient, job_id: str) -> dict:
    for _ in range(80):
        response = client.get(f"/api/regime/backtests/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "quarantined"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Regime API backtest job did not finish: {job_id}")
