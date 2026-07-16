from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.execution import PaperGatewayBrokerAck, PaperOrderGateway
from backend.app.gates import GlobalGateResponse, apply_global_gate_response
from backend.app.risk import (
    AccountSnapshot,
    GlobalOrderIntent,
    GlobalPortfolioRiskManager,
    GlobalRiskSettings,
    MarketSnapshot,
    PendingOrder,
    PortfolioPosition,
    PortfolioSnapshot,
)
from backend.app.risk.order_gates import global_intent_key
from backend.app.main import app
from backend.tests.test_weighted_voting_paper_order_gateway import NOW, global_application, global_proposal, local_gate, validated_rollout_flags, validated_rollout_validation
from backend.app.algorithms.weighted_voting.execution_gateway import submit_weighted_voting_paper_order


class GlobalPortfolioRiskManagerPhase12Test(unittest.TestCase):
    def test_global_manager_approves_resizes_or_denies_without_mutating_intent(self) -> None:
        intent = order_intent(quantity=100)
        manager = GlobalPortfolioRiskManager(settings=GlobalRiskSettings(globalMaximumSymbolExposurePercent=10))
        account = account_snapshot(equity=100_000, buying_power=100_000)

        approved = manager.evaluate(intent=intent, account=account, market=market_snapshot(), portfolio=PortfolioSnapshot())
        resized = manager.evaluate(
            intent=intent,
            account=account,
            market=market_snapshot(),
            portfolio=PortfolioSnapshot(positions=(PortfolioPosition(algorithmId="weighted_voting", symbol="SPY", quantity=50, marketValue=5_000, openRiskDollars=50, side="long"),)),
        )
        denied = manager.evaluate(intent=intent, account=account, market=market_snapshot(tradingHalt=True), portfolio=PortfolioSnapshot())

        self.assertEqual(approved.status, "approved")
        self.assertEqual(resized.status, "resized")
        self.assertEqual(resized.approvedQuantity, 50)
        self.assertEqual(denied.status, "denied")
        self.assertEqual(intent.requestedQuantity, 100)
        self.assertTrue(any(gate.gateId == "trading_halt" for gate in denied.failedGates))

    def test_trading_off_blocks_new_entries_but_not_protective_exits(self) -> None:
        manager = GlobalPortfolioRiskManager(settings=GlobalRiskSettings(tradingEnabled=False))

        entry = manager.evaluate(intent=order_intent(position_effect="enter_long"), account=account_snapshot(), market=market_snapshot())
        exit_decision = manager.evaluate(intent=order_intent(position_effect="exit_long", intent_type="protective_exit", side="Sell"), account=account_snapshot(), market=market_snapshot())

        self.assertEqual(entry.status, "denied")
        self.assertEqual(exit_decision.status, "approved")
        self.assertTrue(any(gate.gateId == "normal_trading_enabled" and gate.status == "warning" for gate in exit_decision.warningGates))

    def test_order_integrity_blocks_duplicates_conflicts_expired_and_shortability(self) -> None:
        short_intent = order_intent(position_effect="enter_short", side="Sell")
        portfolio = PortfolioSnapshot(
            pendingOrders=(
                PendingOrder(
                    algorithmId="weighted_voting",
                    symbol="SPY",
                    side="Buy",
                    quantity=5,
                    notional=500,
                    riskDollars=20,
                    decisionId="existing-decision",
                    clientOrderId="existing-client",
                    intentKey=global_intent_key(order_intent()),
                    submittedAt=NOW,
                ),
            )
        )
        manager = GlobalPortfolioRiskManager(settings=GlobalRiskSettings(shortSalesEnabled=False))
        decision = manager.evaluate(intent=short_intent, account=account_snapshot(), market=market_snapshot(), portfolio=portfolio)

        failed_ids = {gate.gateId for gate in decision.failedGates}
        self.assertEqual(decision.status, "denied")
        self.assertIn("conflicting_simultaneous_orders", failed_ids)
        self.assertIn("insufficient_shortability", failed_ids)

    def test_atomic_reservation_prevents_two_algorithms_consuming_same_decision_capacity(self) -> None:
        manager = GlobalPortfolioRiskManager()
        first = manager.evaluate(intent=order_intent(decision_id="same-decision"), account=account_snapshot(), market=market_snapshot(), reserve=True)
        second = manager.evaluate(intent=order_intent(decision_id="same-decision"), account=account_snapshot(), market=market_snapshot(), reserve=True)

        self.assertEqual(first.status, "approved")
        self.assertEqual(first.reservationId, second.reservationId)
        self.assertEqual(len(manager.reservations.all()), 1)

    def test_simultaneous_regime_and_wca_requests_share_atomic_account_risk_capacity(self) -> None:
        manager = GlobalPortfolioRiskManager(settings=GlobalRiskSettings(globalMaximumOpenRiskPercent=1))
        account = account_snapshot(equity=100_000, buying_power=100_000)

        first = manager.evaluate(
            intent=order_intent(decision_id="wca-capacity", algorithm_id="weighted_voting", requested_risk=800),
            account=account,
            market=market_snapshot(),
            reserve=True,
        )
        second = manager.evaluate(
            intent=order_intent(decision_id="regime-capacity", algorithm_id="regime", requested_risk=800),
            account=account,
            market=market_snapshot(),
            reserve=True,
        )

        self.assertEqual(first.status, "approved")
        self.assertEqual(first.approvedRiskDollars, 800)
        self.assertEqual(second.status, "resized")
        self.assertLessEqual(second.approvedRiskDollars, 200)
        self.assertLessEqual(second.approvedQuantity, second.approvedRiskDollars)
        self.assertEqual(len(manager.reservations.all()), 2)

    def test_paper_gateway_enforces_global_manager_before_broker_submission(self) -> None:
        broker = Phase12Broker()
        store = MemoryStore()
        manager = GlobalPortfolioRiskManager(settings=GlobalRiskSettings(masterNewEntryEnabled=False))
        gateway = PaperOrderGateway(broker, store, global_risk_manager=manager)
        proposal = global_proposal()

        result = submit_weighted_voting_paper_order(
            gateway=gateway,
            proposal=proposal,
            global_application=global_application(proposal),
            local_gate_result=local_gate(True),
            mode="automatic",
            evaluated_at=NOW,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        self.assertFalse(result.submitted)
        self.assertEqual(broker.submit_count, 0)
        self.assertIn("paper_gateway.global_portfolio_risk_denied", result.reasonCodes)
        self.assertEqual(store.snapshots[f"paper_order_gateway.global_risk.{proposal.orderIntentId}"]["status"], "denied")

    def test_global_risk_api_evaluates_server_side(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/risk/global/evaluate",
            json={
                "intent": order_intent().model_dump(mode="json"),
                "account": account_snapshot().model_dump(mode="json"),
                "market": market_snapshot().model_dump(mode="json"),
                "portfolio": PortfolioSnapshot().model_dump(mode="json"),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["endpointVersion"], "global_risk_evaluate_v1")
        self.assertIn(body["decision"]["status"], {"approved", "resized", "denied"})


def order_intent(
    *,
    quantity: int = 10,
    decision_id: str = "decision-1",
    algorithm_id: str = "weighted_voting",
    position_effect: str = "enter_long",
    intent_type: str = "new_entry",
    side: str = "Buy",
    requested_risk: float | None = None,
) -> GlobalOrderIntent:
    return GlobalOrderIntent(
        decisionId=decision_id,
        clientOrderId=f"client-{decision_id}",
        algorithmId=algorithm_id,
        symbol="SPY",
        side=side,
        positionEffect=position_effect,
        intentType=intent_type,
        requestedQuantity=quantity,
        expectedEntryPrice=100.0,
        protectiveStopPrice=99.0 if side == "Buy" else 101.0,
        targetPrice=102.0 if side == "Buy" else 98.0,
        requestedRiskDollars=float(requested_risk if requested_risk is not None else quantity),
        marketDataTimestamp=NOW,
        generatedAt=NOW,
        expiresAt=NOW + timedelta(minutes=5),
        settingsVersion="settings-v1",
        profileVersion="profile-v1",
    )


def account_snapshot(*, equity: float = 100_000, buying_power: float = 100_000) -> AccountSnapshot:
    return AccountSnapshot(
        accountSnapshotId="acct-1",
        equity=equity,
        highWaterEquity=equity,
        availableBuyingPower=buying_power,
        observedAt=NOW,
    )


def market_snapshot(**overrides) -> MarketSnapshot:
    return MarketSnapshot(
        marketSnapshotId="market-1",
        candleTimestamp=NOW,
        quoteTimestamp=NOW,
        spreadPercent=0.01,
        oneMinuteVolume=100_000,
        estimatedSlippagePercent=0.01,
        evaluatedAt=NOW,
        **overrides,
    )


class Phase12Broker:
    def __init__(self) -> None:
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(clientOrderId=intent.clientOrderId, brokerOrderId="broker-1", status="ACCEPTED", acceptedAt=NOW)

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


if __name__ == "__main__":
    unittest.main()
