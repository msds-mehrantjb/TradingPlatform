from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, date, datetime

from backend.app.domain.models import GateStatus
from backend.app.gates import (
    NEUTRAL_GLOBAL_GATE_SERVICE_VERSION,
    NeutralGlobalGateInput,
    NeutralGlobalGateService,
)


NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class NeutralGlobalGateServiceTest(unittest.TestCase):
    def test_all_neutral_gate_groups_pass_for_new_entry(self) -> None:
        decision = NeutralGlobalGateService().evaluate(neutral_input())

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.serviceVersion, NEUTRAL_GLOBAL_GATE_SERVICE_VERSION)
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.hardBlockers, ())
        self.assertEqual(decision.quantityMultiplierCap, 1.0)
        groups = {result.group for result in decision.gateResults}
        self.assertEqual(
            groups,
            {
                "Operational",
                "Data health",
                "Market safety",
                "Global account risk",
                "Order flow",
            },
        )

    def test_service_outputs_all_statuses_without_strategy_direction(self) -> None:
        payload = neutral_payload()
        payload["accountRisk"]["buyingPowerReservePercent"] = 12.0
        payload["market"]["absoluteSpreadBps"] = None
        decision = NeutralGlobalGateService().evaluate(payload)

        statuses = {result.status for result in decision.gateResults}
        self.assertIn(GateStatus.PASS.value, statuses)
        self.assertIn(GateStatus.CAUTION.value, statuses)
        self.assertIn(GateStatus.INFO.value, statuses)
        self.assertNotIn("BUY", str(decision.model_dump(mode="json")))
        self.assertNotIn("SELL", str(decision.model_dump(mode="json")))
        forbidden_fields = {"signal", "side", "winner", "weightedScore", "winnerEdge", "vwapAlignment", "mlQuality"}
        self.assertTrue(forbidden_fields.isdisjoint(decision.model_dump(mode="json")))

    def test_global_failures_reject_new_entries_without_mutating_strategy_state(self) -> None:
        payload = neutral_payload()
        payload["operational"]["masterTradingEnabled"] = False
        payload["data"]["freshQuote"] = False
        strategy_state = {"algorithm_id": "weighted_voting", "weights": {"S1": 0.125}, "winner": "Buy"}
        before = deepcopy(strategy_state)

        decision = NeutralGlobalGateService().evaluate(payload)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "exits_only")
        self.assertEqual(decision.quantityMultiplierCap, 0.0)
        self.assertIn("neutral_global_gate.operational.master_switch_off", decision.reasonCodes)
        self.assertIn("neutral_global_gate.data.stale_quote", decision.reasonCodes)
        self.assertEqual(strategy_state, before)

    def test_protective_exits_remain_allowed_when_new_entry_gates_fail(self) -> None:
        payload = neutral_payload(intent="protective_exit")
        payload["market"]["tradingHalt"] = True
        payload["data"]["validMarketData"] = False
        payload["accountRisk"]["totalOpenRiskPercent"] = 9.0

        decision = NeutralGlobalGateService().evaluate(payload)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "allow")
        self.assertTrue(decision.riskReducingExitAllowed)
        self.assertEqual(decision.hardBlockers, ())
        self.assertTrue(decision.cautions)
        self.assertIn("neutral_global_gate.order_flow.risk_reducing_exit_protected", decision.reasonCodes)

    def test_emergency_conditions_require_liquidation_but_still_allow_exit_intent(self) -> None:
        payload = neutral_payload(intent="end_of_day_liquidation")
        payload["operational"]["emergencyKillSwitch"] = True

        decision = NeutralGlobalGateService().evaluate(payload)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "emergency_liquidation")
        self.assertTrue(decision.emergencyLiquidationRequired)
        self.assertTrue(decision.riskReducingExitAllowed)
        self.assertIn("neutral_global_gate.operational.kill_switch", decision.reasonCodes)

    def test_directional_or_algorithm_specific_inputs_are_rejected_by_schema(self) -> None:
        payload = neutral_payload()
        payload["weightedScore"] = 0.91
        with self.assertRaisesRegex(ValueError, "weightedScore"):
            NeutralGlobalGateInput(**payload)

        payload = neutral_payload()
        payload["market"]["vwapAlignment"] = "bullish"
        with self.assertRaisesRegex(ValueError, "vwapAlignment"):
            NeutralGlobalGateInput(**payload)

    def test_duplicate_order_and_rate_limit_are_global_order_flow_failures(self) -> None:
        payload = neutral_payload()
        payload["orderFlow"]["duplicateOrder"] = True
        payload["orderFlow"]["idempotencyKeySeen"] = True
        payload["orderFlow"]["orderRateLastMinute"] = 31

        decision = NeutralGlobalGateService().evaluate(payload)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "reject_new_entry")
        self.assertIn("neutral_global_gate.order_flow.duplicate_order", decision.reasonCodes)
        self.assertIn("neutral_global_gate.order_flow.idempotency_duplicate", decision.reasonCodes)
        self.assertIn("neutral_global_gate.order_flow.rate_limit", decision.reasonCodes)


def neutral_input() -> NeutralGlobalGateInput:
    return NeutralGlobalGateInput(**neutral_payload())


def neutral_payload(intent: str = "new_entry") -> dict:
    return {
        "intent": intent,
        "evaluatedAt": NOW,
        "sessionDate": SESSION_DATE,
        "symbol": "SPY",
        "operational": {
            "masterTradingEnabled": True,
            "paperTradingMode": True,
            "liveTradingRequested": False,
            "allowedSession": True,
            "marketCalendarOpen": True,
            "entryWindowOpen": True,
            "orderApiHealthy": True,
            "brokerConnected": True,
            "accountNotRestricted": True,
            "systemClockHealthy": True,
            "systemClockDriftSeconds": 0.5,
            "emergencyKillSwitch": False,
        },
        "data": {
            "freshCandle": True,
            "freshQuote": True,
            "candleAgeSeconds": 60.0,
            "quoteAgeSeconds": 3.0,
            "validMarketData": True,
            "corruptedMarketData": False,
        },
        "market": {
            "tradingHalt": False,
            "luldActive": False,
            "marketWideCircuitBreaker": False,
            "absoluteSpreadBps": 5.0,
            "globalEventBlackout": False,
        },
        "accountRisk": {
            "equity": 100000.0,
            "dailyPnl": 250.0,
            "accountDrawdownPercent": 0.5,
            "totalOpenRiskPercent": 1.0,
            "grossExposurePercent": 30.0,
            "netExposurePercent": 10.0,
            "perSymbolExposurePercent": 15.0,
            "buyingPowerReservePercent": 30.0,
            "pendingOrderRiskPercent": 0.25,
        },
        "orderFlow": {
            "orderRateLastMinute": 1,
            "duplicateOrder": False,
            "idempotencyKeySeen": False,
            "idempotencyKeyValid": True,
        },
    }


if __name__ == "__main__":
    unittest.main()
