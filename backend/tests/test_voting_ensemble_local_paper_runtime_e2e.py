from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.config import get_settings
from backend.app.algorithms.voting_ensemble.paper_execution import (
    AlpacaPaperBrokerClient,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService, _vote
from backend.app.algorithms.voting_ensemble.snapshot.builder import _timestamp
from backend.app.domain.models import Direction, RegimeState
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


SESSION_DATE = date(2026, 1, 5)


class VotingEnsembleLocalPaperRuntimeE2ETest(unittest.TestCase):
    def test_complete_automatic_local_paper_entry_stateful_next_evaluation_and_exit_loop(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"local-paper-acceptance-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            "os.environ",
            {
                "APCA_API_KEY_ID": "",
                "APCA_API_SECRET_KEY": "",
                "ALPACA_TRADING_BASE_URL": "",
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_PAPER_EXECUTION_MODE": "LOCAL_PAPER",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_MAX_MARK_QUOTE_AGE_SECONDS": "999999999",
                "VOTING_ENSEMBLE_LOCAL_PAPER_MAX_QUOTE_AGE_MS": "999999999000",
            },
        ), patch(
            "backend.app.algorithms.voting_ensemble.paper_execution._default_paper_broker_client",
            side_effect=AssertionError("LOCAL_PAPER end-to-end test must not create a broker trading client"),
        ), patch.object(
            AlpacaPaperBrokerClient,
            "refresh_account_snapshot",
            side_effect=AssertionError("/account must not be requested in LOCAL_PAPER"),
        ), patch.object(
            AlpacaPaperBrokerClient,
            "refresh_positions",
            side_effect=AssertionError("/positions must not be requested in LOCAL_PAPER"),
        ), patch.object(
            AlpacaPaperBrokerClient,
            "refresh_open_orders",
            side_effect=AssertionError("/orders must not be requested in LOCAL_PAPER"),
        ):
            self.assertFalse(get_settings().has_alpaca_credentials)
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            execution_runtime = VotingEnsemblePaperExecutionRuntime(
                repository=repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                execution_mode="LOCAL_PAPER",
                auto_start=False,
            )
            payload_builder = LocalPaperRuntimePayloadBuilder(repository)
            orchestrator = VotingEnsembleRuntimeOrchestrator(
                service=VotingEnsembleService(),
                paper_execution_runtime=execution_runtime,
                automatic_payload_builder=payload_builder,
                auto_start=False,
            )
            self.assertEqual(execution_runtime.execution_mode, "LOCAL_PAPER")
            self.assertIsNone(execution_runtime.broker_client)

            initial = repository.local_account_snapshot(observed_at=datetime(2026, 1, 5, 14, 59, tzinfo=UTC))
            self.assertEqual(initial["initialCash"], 100000.0)
            self.assertEqual(initial["cash"], 100000.0)
            self.assertEqual(initial["equity"], 100000.0)
            self.assertEqual(initial["buyingPower"], 100000.0)
            self.assertEqual(repository.inventory_snapshot()["localPositions"], [])

            entry_payload = market_payload(candle_count=30, bid=100.0, ask=100.0)
            entry_job = orchestrator.enqueue_finalized_bar_event(finalized_event(entry_payload, correlation_id="corr-e2e-entry"))
            with patched_strategy_votes("Buy"):
                orchestrator.drain_in_process()
            entry_completed = orchestrator.get_job(entry_job["jobId"])
            entry_decision = entry_completed["result"]["decision"]

            self.assertEqual(entry_completed["status"], "completed")
            self.assertEqual(entry_decision["final_signal"], "Buy")
            self.assertIn("ensemble.family_aware_candidate", entry_decision["reason_codes"])
            self.assertEqual(entry_decision["eligible_strategy_count"], 2)
            self.assertEqual(entry_decision["counts"]["Buy"], 2)
            self.assertEqual(entry_decision["risk_budget"]["quantity"], 10)
            self.assertIn("voting_ensemble.risk_budget.cap.buying_power", entry_decision["risk_budget"]["reason_codes"])
            self.assertEqual(entry_decision["order_plan"]["side"], "BUY")
            self.assertEqual(entry_decision["order_plan"]["quantity"], 10)
            self.assertTrue(entry_completed["result"]["paperExecution"]["enqueued"])

            entry_execution = execution_runtime.process_once(evaluated_at=payload_time(entry_payload) + timedelta(seconds=1))
            after_entry = repository.inventory_snapshot()
            entry_account = after_entry["localPaperAccount"]
            entry_position = after_entry["localPositions"][0]

            self.assertIsNotNone(entry_execution)
            assert entry_execution is not None
            self.assertTrue(entry_execution["submitted"])
            self.assertEqual(entry_execution["status"], "FILLED")
            self.assertEqual(entry_position["signedQuantity"], 10)
            self.assertEqual(entry_position["averageEntryPrice"], 100.0)
            self.assertEqual(entry_account["cash"], 99000.0)
            self.assertEqual(entry_account["equity"], 100000.0)
            self.assertEqual(entry_account["buyingPower"], 99000.0)
            self.assertEqual(entry_account["unrealizedPnl"], 0.0)
            self.assertEqual(entry_account["tradesToday"], 1)
            entry_gateway_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{entry_execution['gatewayResult']['orderIntentId']}")
            self.assertEqual(entry_gateway_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(entry_gateway_account["availableBuyingPower"], 100000.0)

            exit_payload = market_payload(candle_count=31, bid=101.5, ask=101.55)
            exit_job = orchestrator.enqueue_finalized_bar_event(finalized_event(exit_payload, correlation_id="corr-e2e-exit"))
            with patched_strategy_votes("Sell"):
                orchestrator.drain_in_process()
            exit_completed = orchestrator.get_job(exit_job["jobId"])
            exit_decision = exit_completed["result"]["decision"]
            next_snapshot_account = payload_builder.built_payloads[-1]["market_context"]["accountRiskSnapshot"]

            self.assertEqual(exit_completed["status"], "completed")
            self.assertEqual(next_snapshot_account["sourceAuthority"], "voting_ensemble_local_paper_account")
            self.assertEqual(next_snapshot_account["cash"], 99000.0)
            self.assertEqual(next_snapshot_account["buyingPower"], 99000.0)
            self.assertEqual(next_snapshot_account["openPositionNotional"], 1015.0)
            self.assertEqual(next_snapshot_account["unrealizedPnl"], 15.0)
            self.assertEqual(next_snapshot_account["equity"], 100015.0)
            self.assertEqual(next_snapshot_account["tradesToday"], 1)
            self.assertEqual(exit_decision["final_signal"], "Sell")
            self.assertEqual(exit_decision["order_plan"]["side"], "SELL")
            self.assertEqual(exit_decision["order_plan"]["quantity"], 10)
            self.assertTrue(exit_completed["result"]["paperExecution"]["enqueued"])

            exit_execution = execution_runtime.process_once(evaluated_at=payload_time(exit_payload) + timedelta(seconds=1))
            after_exit = repository.inventory_snapshot()
            final_account = after_exit["localPaperAccount"]
            final_closed_trades = list(after_exit["closedTrades"])

            self.assertIsNotNone(exit_execution)
            assert exit_execution is not None
            self.assertTrue(exit_execution["submitted"])
            self.assertEqual(exit_execution["status"], "FILLED")
            self.assertEqual(after_exit["localPositions"], [])
            self.assertEqual(after_exit["closedTrades"][0]["quantity"], 10)
            self.assertEqual(after_exit["closedTrades"][0]["realizedPnl"], 15.0)
            self.assertEqual(final_account["cash"], 100015.0)
            self.assertEqual(final_account["equity"], 100015.0)
            self.assertEqual(final_account["realizedPnl"], 15.0)
            self.assertEqual(final_account["realizedPnlToday"], 15.0)
            self.assertEqual(final_account["unrealizedPnl"], 0.0)
            self.assertEqual(final_account["dailyNetPnl"], 15.0)
            self.assertEqual(final_account["tradesToday"], 2)
            exit_gateway_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{exit_execution['gatewayResult']['orderIntentId']}")
            self.assertEqual(exit_gateway_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(exit_gateway_account["availableBuyingPower"], 99000.0)

            restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_runtime = VotingEnsemblePaperExecutionRuntime(
                repository=restarted_repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                execution_mode="LOCAL_PAPER",
                auto_start=False,
            )
            recovery = restarted_runtime.reconcile_broker_state(evaluated_at=payload_time(exit_payload) + timedelta(seconds=2))
            after_restart = restarted_runtime.inventory_snapshot()
            restart_account = after_restart["localPaperAccount"]

            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertTrue(recovery["brokerReconciliationSkipped"])
            self.assertEqual(recovery["brokerAccountsObserved"], 0)
            self.assertEqual(recovery["brokerPositionsObserved"], 0)
            self.assertEqual(recovery["brokerOrdersObserved"], 0)
            self.assertEqual(after_restart["localPositions"], after_exit["localPositions"])
            self.assertEqual(after_restart["closedTrades"], final_closed_trades)
            self.assertEqual(restart_account["cash"], final_account["cash"])
            self.assertEqual(restart_account["equity"], final_account["equity"])
            self.assertEqual(restart_account["realizedPnl"], final_account["realizedPnl"])
            self.assertEqual(restart_account["realizedPnlToday"], final_account["realizedPnlToday"])
            self.assertEqual(restart_account["dailyNetPnl"], final_account["dailyNetPnl"])
            self.assertEqual(restart_account["tradesToday"], final_account["tradesToday"])
        store_path.unlink(missing_ok=True)


class LocalPaperRuntimePayloadBuilder:
    def __init__(self, repository: VotingEnsemblePaperExecutionRepository) -> None:
        self.repository = repository
        self.built_payloads: list[dict[str, Any]] = []

    def build(self, command: Any) -> dict[str, Any]:
        payload = deepcopy(dict(command.payload))
        observed_at = payload_time(payload)
        nbbo = dict(payload["nbbo"])
        context = dict(payload.get("market_context") or {})
        context["nbbo"] = nbbo
        self.repository.mark_local_positions_from_market_data(symbol="SPY", nbbo=nbbo, observed_at=observed_at)
        account = self.repository.local_account_snapshot(observed_at=observed_at)
        context["accountRiskSnapshot"] = account
        payload["accountRiskSnapshot"] = account
        payload["market_context"] = context
        self.built_payloads.append(deepcopy(payload))
        return payload


def patched_strategy_votes(side: str):
    return patch.multiple(
        service_module,
        DIRECTIONAL_STRATEGIES=(
            strategy_vote("Multi-Timeframe Trend Alignment", "trend", side, "multi_timeframe_trend_alignment"),
            strategy_vote("Failed Breakout Reversal", "reversal", side, "failed_breakout_reversal"),
        ),
        CONTEXT_STRATEGIES=(),
        REGIME_CLASSIFIER=FixedRegimeClassifier(),
    )


def strategy_vote(strategy_name: str, family: str, side: str, strategy_id: str):
    def evaluate(_request: Any):
        return _vote(
            strategy_name,
            family,
            side,
            80,
            f"deterministic {side.lower()} vote for local paper runtime e2e",
            f"test.e2e.{strategy_id}.{side.lower()}",
            features={"strategyId": strategy_id, "strategyVersion": "test_e2e"},
        )

    return evaluate


class FixedRegimeClassifier:
    def evaluate_snapshot(self, _snapshot: Any) -> RegimeState:
        return RegimeState(
            regimeId="adx_atr_regime",
            label="test_high_fit",
            direction=Direction.FLAT,
            volatility="NORMAL",
            confidence=0.9,
            features={
                "trendFit": 1.0,
                "breakoutFit": 1.0,
                "reversalFit": 1.0,
                "meanReversionFit": 1.0,
                "gapSessionFit": 1.0,
                "transitionState": "stable",
                "reasonCodes": ["regime.test_high_fit"],
            },
            evaluatedAt=datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
            sessionDate=SESSION_DATE,
            configurationHash="test-regime-hash",
        )


def market_payload(*, candle_count: int, bid: float, ask: float) -> dict[str, Any]:
    payload = snapshot_payload(candles(candle_count))
    observed = payload["data_timestamp"]
    nbbo = {
        "bid": bid,
        "ask": ask,
        "bidSize": 1000,
        "askSize": 1000,
        "quoteTimestamp": observed,
        "lastTradeTimestamp": observed,
        "marketDataReceiptTimestamp": observed,
    }
    payload["nbbo"] = nbbo
    payload["market_context"]["nbbo"] = nbbo
    return payload


def finalized_event(payload: dict[str, Any], *, correlation_id: str) -> FinalizedOneMinuteBarEvent:
    return FinalizedOneMinuteBarEvent(
        symbol="SPY",
        barEndTimestamp=payload_time(payload),
        finalized=True,
        settingsHash="local-paper-runtime-e2e",
        evaluationPayload=payload,
        correlationId=correlation_id,
    )


def payload_time(payload: dict[str, Any]) -> datetime:
    parsed = _timestamp(payload["data_timestamp"])
    assert parsed is not None
    return parsed


if __name__ == "__main__":
    unittest.main()
