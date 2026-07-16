from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.domain.models import Signal
from backend.app.execution import (
    ALGORITHM_OWNERSHIP_LEDGER_VERSION,
    AlgorithmOwnershipLedger,
    OwnedOrderIntent,
    OwnedRiskReservation,
    default_capital_partition,
)
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, aggregate_global_account_risk


NOW = datetime(2026, 7, 14, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)
WEIGHTED_PARTITION = "weighted_voting.paper.default"
META_PARTITION = "meta_strategy.paper.default"


class AlgorithmOwnershipLedgerTest(unittest.TestCase):
    def test_ownership_records_require_all_step_27_fields(self) -> None:
        payload = risk_reservation_payload()
        payload.pop("riskReservationId")

        with self.assertRaises(ValidationError):
            OwnedRiskReservation(**payload)

        order_record = order_payload()
        order_record.pop("parentOrderId")
        with self.assertRaises(ValidationError):
            OwnedOrderIntent(**order_record)

    def test_weighted_voting_uses_only_its_capital_partition(self) -> None:
        ledger = ownership_ledger()

        accepted = ledger.reserve_risk(risk_reservation())
        rejected = ledger.reserve_risk(risk_reservation(reserved_risk=30_000.0, order_intent_id="wv-order-too-large", reservation_id="wv-risk-too-large"))

        self.assertTrue(accepted.accepted)
        self.assertFalse(rejected.accepted)
        self.assertIn("ownership.capital_partition_limit", rejected.reasonCodes)
        weighted = ledger.subledger_for("weighted_voting", WEIGHTED_PARTITION)
        meta = ledger.subledger_for("meta_strategy", META_PARTITION)
        self.assertEqual(weighted.reservedRiskDollars, 500.0)
        self.assertEqual(meta.reservedRiskDollars, 0.0)

    def test_weighted_voting_cannot_close_another_algorithm_position(self) -> None:
        ledger = ownership_ledger()
        ledger.reserve_risk(risk_reservation(algorithm_id="meta_strategy", partition_id=META_PARTITION, order_intent_id="meta-order-1", reservation_id="meta-risk-1"))
        ledger.register_order_intent(order_intent(algorithm_id="meta_strategy", partition_id=META_PARTITION, order_intent_id="meta-order-1", reservation_id="meta-risk-1"))
        position = ledger.open_position_from_order("meta-order-1", quantity=20, fill_price=100.0, opened_at=NOW)

        result = ledger.close_owned_position(
            algorithm_id="weighted_voting",
            position_id=ledger.subledger_for("meta_strategy", META_PARTITION).positionIds[0],
            quantity=5,
            exit_price=101.0,
            order_intent_id="wv-exit-1",
            decision_id="wv-decision-exit",
            risk_reservation_id="wv-risk-exit",
            closed_at=NOW,
        )

        self.assertEqual(position.positionOwner, "meta_strategy")
        self.assertFalse(result.accepted)
        self.assertIn("ownership.cross_algorithm_close_rejected", result.reasonCodes)
        self.assertEqual(ledger.subledger_for("meta_strategy", META_PARTITION).openQuantityBySymbol["SPY"], 20)
        self.assertEqual(ledger.subledger_for("weighted_voting", WEIGHTED_PARTITION).tradeCount, 0)

    def test_opposing_same_symbol_spy_intents_are_rejected_explicitly(self) -> None:
        ledger = ownership_ledger()
        ledger.reserve_risk(risk_reservation())
        ledger.register_order_intent(order_intent())
        ledger.open_position_from_order("wv-order-1", quantity=10, fill_price=100.0, opened_at=NOW)
        ledger.reserve_risk(
            risk_reservation(
                algorithm_id="meta_strategy",
                partition_id=META_PARTITION,
                side=Signal.SELL,
                order_intent_id="meta-short-1",
                reservation_id="meta-risk-short-1",
            )
        )

        result = ledger.register_order_intent(
            order_intent(
                algorithm_id="meta_strategy",
                partition_id=META_PARTITION,
                side=Signal.SELL,
                order_intent_id="meta-short-1",
                reservation_id="meta-risk-short-1",
            )
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.action, "REJECTED")
        self.assertIn("ownership.spy_conflicting_entry_rejected", result.reasonCodes)

    def test_pnl_and_trade_count_remain_attributed_by_algorithm(self) -> None:
        ledger = ownership_ledger()
        ledger.reserve_risk(risk_reservation())
        ledger.register_order_intent(order_intent())
        ledger.open_position_from_order("wv-order-1", quantity=10, fill_price=100.0, opened_at=NOW)
        position_id = ledger.subledger_for("weighted_voting", WEIGHTED_PARTITION).positionIds[0]

        close = ledger.close_owned_position(
            algorithm_id="weighted_voting",
            position_id=position_id,
            quantity=10,
            exit_price=103.0,
            order_intent_id="wv-exit-1",
            decision_id="wv-decision-exit",
            risk_reservation_id="wv-risk-exit",
            closed_at=NOW,
        )

        weighted = ledger.subledger_for("weighted_voting", WEIGHTED_PARTITION)
        meta = ledger.subledger_for("meta_strategy", META_PARTITION)
        self.assertTrue(close.accepted)
        self.assertEqual(weighted.realizedPnl, 30.0)
        self.assertEqual(weighted.tradeCount, 1)
        self.assertEqual(meta.realizedPnl, 0.0)
        self.assertEqual(meta.tradeCount, 0)

    def test_global_exposure_still_includes_all_algorithms_without_erasing_attribution(self) -> None:
        ledger = ownership_ledger()
        for algorithm_id, partition_id, order_id, risk_id, side in [
            ("weighted_voting", WEIGHTED_PARTITION, "wv-order-1", "wv-risk-1", Signal.BUY),
            ("meta_strategy", META_PARTITION, "meta-order-1", "meta-risk-1", Signal.BUY),
        ]:
            ledger.reserve_risk(risk_reservation(algorithm_id=algorithm_id, partition_id=partition_id, order_intent_id=order_id, reservation_id=risk_id, side=side))
            ledger.register_order_intent(order_intent(algorithm_id=algorithm_id, partition_id=partition_id, order_intent_id=order_id, reservation_id=risk_id, side=side))
            ledger.open_position_from_order(order_id, quantity=10, fill_price=100.0, opened_at=NOW)

        exposure = ledger.global_exposure()

        self.assertEqual(exposure["grossQuantity"], 20)
        self.assertEqual(exposure["netQuantityBySymbol"]["SPY"], 20)
        self.assertEqual(exposure["algorithmPositionCounts"], {"meta_strategy": 1, "weighted_voting": 1})

    def test_broker_level_risk_snapshot_keeps_algorithm_and_partition_attribution(self) -> None:
        snapshot = BrokerAccountSnapshot(
            accountId="paper-account",
            equity=100_000,
            buyingPower=100_000,
            positions=[
                BrokerPositionState(
                    algorithmId="weighted_voting",
                    capitalPartitionId=WEIGHTED_PARTITION,
                    decisionId="wv-decision-1",
                    orderIntentId="wv-order-1",
                    riskReservationId="wv-risk-1",
                    positionOwner="weighted_voting",
                    parentOrderId="wv-parent-1",
                    exitOwner="weighted_voting",
                    symbol="SPY",
                    side=Signal.BUY,
                    quantity=10,
                    averageEntryPrice=100.0,
                    markPrice=101.0,
                    stopPrice=99.0,
                    openedAt=NOW,
                )
            ],
            pendingOrders=[
                BrokerOrderState(
                    algorithmId="meta_strategy",
                    capitalPartitionId=META_PARTITION,
                    decisionId="meta-decision-1",
                    orderIntentId="meta-order-1",
                    riskReservationId="meta-risk-1",
                    positionOwner="meta_strategy",
                    parentOrderId="meta-parent-1",
                    exitOwner="meta_strategy",
                    symbol="SPY",
                    side=Signal.BUY,
                    clientOrderId="meta-client-order",
                    orderType="LIMIT",
                    quantity=5,
                    entryPrice=100.0,
                    stopPrice=99.0,
                    submittedAt=NOW,
                )
            ],
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
        )

        risk = aggregate_global_account_risk(snapshot, candidateSymbol="SPY", candidateSide=Signal.BUY)

        self.assertEqual(risk.riskState["algorithmExposure"]["weighted_voting"]["positionNotionalDollars"], 1010.0)
        self.assertEqual(risk.riskState["algorithmExposure"]["meta_strategy"]["pendingOrderNotionalDollars"], 500.0)
        self.assertIn(WEIGHTED_PARTITION, risk.riskState["capitalPartitionExposure"])
        self.assertIn(META_PARTITION, risk.riskState["capitalPartitionExposure"])


def ownership_ledger() -> AlgorithmOwnershipLedger:
    ledger = AlgorithmOwnershipLedger()
    ledger.register_partition(default_capital_partition("weighted_voting", session_date=SESSION_DATE, max_capital_dollars=25_000.0))
    ledger.register_partition(default_capital_partition("meta_strategy", session_date=SESSION_DATE, max_capital_dollars=25_000.0))
    return ledger


def risk_reservation(
    *,
    algorithm_id: str = "weighted_voting",
    partition_id: str = WEIGHTED_PARTITION,
    side: Signal = Signal.BUY,
    order_intent_id: str = "wv-order-1",
    reservation_id: str = "wv-risk-1",
    reserved_risk: float = 500.0,
) -> OwnedRiskReservation:
    return OwnedRiskReservation(**risk_reservation_payload(algorithm_id=algorithm_id, partition_id=partition_id, side=side, order_intent_id=order_intent_id, reservation_id=reservation_id, reserved_risk=reserved_risk))


def risk_reservation_payload(
    *,
    algorithm_id: str = "weighted_voting",
    partition_id: str = WEIGHTED_PARTITION,
    side: Signal = Signal.BUY,
    order_intent_id: str = "wv-order-1",
    reservation_id: str = "wv-risk-1",
    reserved_risk: float = 500.0,
) -> dict:
    return {
        "algorithmId": algorithm_id,
        "capitalPartitionId": partition_id,
        "decisionId": f"{algorithm_id}-decision-1",
        "orderIntentId": order_intent_id,
        "riskReservationId": reservation_id,
        "positionOwner": algorithm_id,
        "parentOrderId": f"{order_intent_id}-parent",
        "exitOwner": algorithm_id,
        "symbol": "SPY",
        "side": side,
        "reservedRiskDollars": reserved_risk,
        "reservedNotionalDollars": 5_000.0,
        "createdAt": NOW,
        "sessionDate": SESSION_DATE,
    }


def order_intent(
    *,
    algorithm_id: str = "weighted_voting",
    partition_id: str = WEIGHTED_PARTITION,
    side: Signal = Signal.BUY,
    order_intent_id: str = "wv-order-1",
    reservation_id: str = "wv-risk-1",
) -> OwnedOrderIntent:
    return OwnedOrderIntent(**order_payload(algorithm_id=algorithm_id, partition_id=partition_id, side=side, order_intent_id=order_intent_id, reservation_id=reservation_id))


def order_payload(
    *,
    algorithm_id: str = "weighted_voting",
    partition_id: str = WEIGHTED_PARTITION,
    side: Signal = Signal.BUY,
    order_intent_id: str = "wv-order-1",
    reservation_id: str = "wv-risk-1",
) -> dict:
    return {
        "algorithmId": algorithm_id,
        "capitalPartitionId": partition_id,
        "decisionId": f"{algorithm_id}-decision-1",
        "orderIntentId": order_intent_id,
        "riskReservationId": reservation_id,
        "positionOwner": algorithm_id,
        "parentOrderId": f"{order_intent_id}-parent",
        "exitOwner": algorithm_id,
        "symbol": "SPY",
        "side": side,
        "intent": "new_entry",
        "quantity": 10,
        "entryPrice": 100.0,
        "createdAt": NOW,
        "sessionDate": SESSION_DATE,
    }


if __name__ == "__main__":
    unittest.main()
