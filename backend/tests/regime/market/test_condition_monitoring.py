import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.condition_monitoring import (
    REGIME_CONDITION_MONITORING_VERSION,
    RegimeConditionMonitoringPolicy,
    regime_condition_monitoring_alerts,
)
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


class RegimeConditionMonitoringTest(unittest.TestCase):
    def test_condition_failures_are_detected_but_inactive_by_default(self) -> None:
        records = [_proof(index, raw_regime="strong_uptrend", direction="strong_up", net_value=-0.04) for index in range(8)]

        report = regime_condition_monitoring_alerts(
            records,
            policy=RegimeConditionMonitoringPolicy(minimum_samples_per_condition=6),
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["monitoringVersion"], REGIME_CONDITION_MONITORING_VERSION)
        self.assertEqual(report["monitoringStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertFalse(report["alertsAppliedToPaperTrading"])
        alert_ids = {alert["id"] for alert in report["alerts"]}
        self.assertIn("regime.monitor.condition_negative_net_value", alert_ids)
        self.assertIn("regime.monitor.condition_win_rate_failure", alert_ids)
        self.assertIn("regime:strong_uptrend", report["conditionSummaries"])
        self.assertIn("direction:strong_up", report["conditionSummaries"])

    def test_alerts_can_be_explicitly_applied_during_paper_trading(self) -> None:
        records = [_proof(index, raw_regime="range_bound", structure="range", net_value=-0.01) for index in range(8)]

        report = regime_condition_monitoring_alerts(
            records,
            policy=RegimeConditionMonitoringPolicy(minimum_samples_per_condition=6),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["monitoringStatus"], "ALERTS_PRESENT")
        self.assertTrue(report["alertsAppliedToPaperTrading"])

    def test_cost_latency_fill_and_block_rate_alerts_are_condition_specific(self) -> None:
        records = [
            _proof(
                index,
                raw_regime="liquidity_stress",
                liquidity="unknown",
                net_value=0.02,
                cost_error=0.12,
                filled_quantity=0 if index % 2 == 0 else 100,
                submission_latency_ms=2_400,
                blockers=("regime.safety.liquidity_fail_closed",),
                trade_allowed=False,
            )
            for index in range(8)
        ]

        report = regime_condition_monitoring_alerts(
            records,
            policy=RegimeConditionMonitoringPolicy(minimum_samples_per_condition=6),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00Z",
        )

        by_id = {alert["id"]: alert for alert in report["alerts"]}
        self.assertIn("regime.monitor.condition_cost_error", by_id)
        self.assertIn("regime.monitor.condition_latency_drift", by_id)
        self.assertIn("regime.monitor.condition_fill_rate_deterioration", by_id)
        self.assertIn("regime.monitor.condition_block_rate", by_id)
        self.assertIn("liquidity:unknown", report["conditionSummaries"])
        self.assertEqual(
            report["conditionSummaries"]["regime:liquidity_stress"]["topBlockers"][0]["blocker"],
            "regime.safety.liquidity_fail_closed",
        )

    def test_conditions_with_too_few_samples_do_not_alert(self) -> None:
        records = [_proof(index, raw_regime="event_risk", event_risk="blackout", net_value=-0.10) for index in range(3)]

        report = regime_condition_monitoring_alerts(
            records,
            policy=RegimeConditionMonitoringPolicy(minimum_samples_per_condition=6),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["monitoringStatus"], "NO_ALERTS")
        self.assertEqual(report["alerts"], [])


def _proof(
    index: int,
    *,
    raw_regime: str,
    direction: str = "neutral",
    volatility: str = "normal",
    structure: str = "trend",
    liquidity: str = "good",
    session: str = "midday",
    event_risk: str = "none",
    net_value: float,
    cost_error: float = 0.01,
    filled_quantity: int = 100,
    submission_latency_ms: int = 300,
    blockers: tuple[str, ...] = (),
    trade_allowed: bool = True,
) -> dict:
    decision_at = START + timedelta(minutes=index)
    submitted_at = decision_at + timedelta(milliseconds=submission_latency_ms)
    filled_at = submitted_at + timedelta(milliseconds=300)
    exit_at = filled_at + timedelta(minutes=5)
    return {
        "sourceMode": "paper",
        "symbol": "SPY",
        "decisionId": f"decision-{index}",
        "orderIntentId": f"order-{index}",
        "decisionTimestamp": decision_at.isoformat(),
        "orderSubmissionTimestamp": submitted_at.isoformat(),
        "rawRegime": raw_regime,
        "axes": {
            "direction": direction,
            "volatility": volatility,
            "structure": structure,
            "liquidity": liquidity,
            "session": session,
            "eventRisk": event_risk,
        },
        "tradeAllowed": trade_allowed,
        "tradeBlockers": blockers,
        "costs": {
            "estimatedExecutionCost": 0.04,
            "realizedExecutionCost": 0.04 + cost_error,
        },
        "fill": {
            "status": "FILLED" if filled_quantity else "EXPIRED",
            "submittedQuantity": 100,
            "filledQuantity": filled_quantity,
            "averageFillPrice": 100.01 if filled_quantity else None,
            "submittedAt": submitted_at.isoformat(),
            "filledAt": filled_at.isoformat() if filled_quantity else None,
        },
        "realizedOutcome": {
            "status": "completed",
            "exitTimestamp": exit_at.isoformat(),
            "exitReason": "target" if net_value > 0 else "stop",
            "incrementalRealizedNetValueAfterExecutionCosts": net_value,
        },
    }


if __name__ == "__main__":
    unittest.main()
