from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.app.strategies.context import (
    EventReplayDecision,
    EventValidationThresholds,
    build_event_validation_report,
    evaluate_event_promotion_policy,
)


BASE = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
CALIBRATION_CUTOFF = BASE + timedelta(days=10)
HOLDOUT_START = BASE + timedelta(days=20)


def decision(
    index: int,
    *,
    event_type: str = "cpi",
    mode: str = "historical_replay",
    day_offset: int = 0,
    window: int = 15,
    gross: float = 7.0,
    cost: float = 2.0,
    net: float = 5.0,
    feed_healthy: bool = True,
    latency: float = 250.0,
    cost_error: float = 0.4,
    order_submitted: bool = False,
    cycle: str | None = None,
    future_leak: bool = False,
    operational_error: bool = False,
) -> EventReplayDecision:
    event_time = BASE + timedelta(days=day_offset, minutes=30)
    provider = event_time + timedelta(seconds=1)
    return EventReplayDecision(
        eventId=f"{event_type}-{index}",
        eventType=event_type,
        eventCycleId=cycle or f"{event_type}-cycle-{day_offset}",
        eventTime=event_time,
        providerTimestamp=provider,
        receivedAt=provider + timedelta(milliseconds=50),
        decisionTime=provider + timedelta(milliseconds=latency),
        mode=mode,  # type: ignore[arg-type]
        selectedWindowMinutes=window,
        grossEdgeBps=gross,
        expectedCostBps=cost,
        realizedNetEdgeBps=net,
        latencyMs=latency,
        costEstimateErrorBps=cost_error,
        feedHealthy=feed_healthy,
        orderSubmitted=order_submitted,
        releaseValuesVisibleBeforeRelease=future_leak,
        operationalError=operational_error,
    )


def passing_rows() -> list[EventReplayDecision]:
    rows: list[EventReplayDecision] = []
    for index in range(8):
        rows.append(decision(index, event_type="cpi", day_offset=index, window=15, net=4.0))
        rows.append(decision(index + 20, event_type="fomc_statement", day_offset=index, window=30, net=3.5))
    for index in range(6):
        rows.append(decision(index + 40, event_type="cpi", mode="walk_forward", day_offset=11 + index, window=15, net=3.0))
        rows.append(decision(index + 60, event_type="fomc_statement", mode="walk_forward", day_offset=11 + index, window=30, net=2.5))
    for index in range(5):
        rows.append(decision(index + 80, event_type="cpi", mode="holdout", day_offset=21 + index, window=15, net=2.0))
        rows.append(decision(index + 100, event_type="fomc_statement", mode="holdout", day_offset=21 + index, window=30, net=1.8))
    for index in range(24):
        rows.append(decision(index + 120, event_type="cpi" if index % 2 == 0 else "fomc_statement", mode="shadow", day_offset=30 + index, net=1.5))
    for index in range(4):
        rows.append(decision(index + 160, event_type="cpi" if index % 2 == 0 else "fomc_statement", mode="paper", day_offset=60 + index, net=1.2, cycle=f"paper-cycle-{index}"))
    return rows


class EconomicEventPerformanceValidationTest(unittest.TestCase):
    def test_replay_records_are_point_in_time(self) -> None:
        with self.assertRaisesRegex(ValidationError, "decision time cannot be before provider timestamp"):
            EventReplayDecision(
                eventId="bad",
                eventType="cpi",
                eventCycleId="c1",
                eventTime=BASE,
                providerTimestamp=BASE + timedelta(seconds=10),
                receivedAt=BASE + timedelta(seconds=11),
                decisionTime=BASE + timedelta(seconds=9),
                mode="historical_replay",
                selectedWindowMinutes=15,
                grossEdgeBps=1.0,
                expectedCostBps=0.5,
                realizedNetEdgeBps=0.5,
                latencyMs=10,
                costEstimateErrorBps=0.1,
                feedHealthy=True,
            )

    def test_calibrates_windows_separately_by_event_type_and_excludes_holdout(self) -> None:
        rows = [
            decision(1, event_type="cpi", day_offset=1, window=15, net=2.0),
            decision(2, event_type="cpi", day_offset=2, window=30, net=0.5),
            decision(3, event_type="fomc_statement", day_offset=3, window=30, net=3.0),
            decision(4, event_type="fomc_statement", day_offset=4, window=15, net=1.0),
            decision(5, event_type="cpi", mode="holdout", day_offset=30, window=30, net=99.0),
        ]

        report = build_event_validation_report(rows, calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)
        selected = {item.eventType: item for item in report.calibrationByEventType}

        self.assertEqual(selected["cpi"].selectedWindowMinutes, 15)
        self.assertEqual(selected["fomc_statement"].selectedWindowMinutes, 30)
        self.assertTrue(all(not item.selectedUsingHoldout for item in report.calibrationByEventType))
        self.assertIn("event.validation.holdout_excluded_from_calibration", selected["cpi"].reasonCodes)

    def test_measures_gross_versus_net_event_edge(self) -> None:
        report = build_event_validation_report(passing_rows(), calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)

        self.assertTrue(report.grossVersusNetMeasured)
        self.assertGreater(report.walkForward.grossEdgeBps, report.walkForward.realizedNetEdgeBps)
        self.assertGreater(report.walkForward.expectedCostBps, 0)
        self.assertGreater(report.untouchedHoldout.realizedNetEdgeBps, 0)

    def test_shadow_mode_is_record_only_and_paper_cycles_are_counted(self) -> None:
        report = build_event_validation_report(passing_rows(), calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)

        self.assertFalse(report.shadowModeSubmittedOrders)
        self.assertEqual(report.shadow.orderSubmissionCount, 0)
        self.assertGreaterEqual(report.shadow.rowCount, 20)
        self.assertGreaterEqual(report.paper.eventCycleCount, 3)
        self.assertGreaterEqual(report.paper.distinctEventTypes, 2)

    def test_promotion_requires_latency_cost_feed_and_operational_thresholds(self) -> None:
        rows = passing_rows()
        rows.append(decision(999, mode="paper", day_offset=80, feed_healthy=False, latency=2000, cost_error=9.0, operational_error=True))
        report = build_event_validation_report(rows, calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)

        decision_result = evaluate_event_promotion_policy(report)

        self.assertFalse(decision_result.promoted)
        self.assertEqual(decision_result.targetMode, "shadow")
        self.assertIn("event.promotion.paper_latency_too_high", decision_result.reasonCodes)
        self.assertIn("event.promotion.paper_cost_error_too_high", decision_result.reasonCodes)
        self.assertIn("event.promotion.paper_feed_stability_too_low", decision_result.reasonCodes)
        self.assertIn("event.promotion.paper_operational_errors_present", decision_result.reasonCodes)

    def test_promotion_allows_only_paper_veto_reduce_after_all_evidence_passes(self) -> None:
        report = build_event_validation_report(passing_rows(), calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)

        decision_result = evaluate_event_promotion_policy(
            report,
            thresholds=EventValidationThresholds(
                minimumWalkForwardNetEdgeBps=0.5,
                minimumHoldoutNetEdgeBps=0.5,
                minimumShadowDecisions=20,
                minimumPaperEventCycles=3,
                minimumDistinctPaperEventTypes=2,
            ),
            frontend_supplied_evidence={"trusted": True},
        )

        self.assertTrue(decision_result.frontendSuppliedEvidenceRejected)
        self.assertFalse(decision_result.promoted)
        self.assertIn("event.promotion.frontend_supplied_evidence_rejected", decision_result.reasonCodes)

        backend_only = evaluate_event_promotion_policy(
            report,
            thresholds=EventValidationThresholds(
                minimumWalkForwardNetEdgeBps=0.5,
                minimumHoldoutNetEdgeBps=0.5,
                minimumShadowDecisions=20,
                minimumPaperEventCycles=3,
                minimumDistinctPaperEventTypes=2,
            ),
        )
        self.assertTrue(backend_only.promoted)
        self.assertEqual(backend_only.targetMode, "paper_veto_reduce_only")
        self.assertIn("event.promotion.paper_veto_reduce_only_allowed", backend_only.reasonCodes)

    def test_point_in_time_leakage_and_shadow_submission_block_promotion(self) -> None:
        rows = passing_rows()
        rows.append(decision(500, mode="holdout", day_offset=25, future_leak=True))
        rows.append(decision(501, mode="shadow", day_offset=45, order_submitted=True))
        report = build_event_validation_report(rows, calibration_cutoff=CALIBRATION_CUTOFF, holdout_start=HOLDOUT_START, generated_at=BASE)
        decision_result = evaluate_event_promotion_policy(report)

        self.assertFalse(report.pointInTimePassed)
        self.assertTrue(report.shadowModeSubmittedOrders)
        self.assertIn("event.promotion.point_in_time_replay_required", decision_result.reasonCodes)
        self.assertIn("event.promotion.shadow_must_not_submit_orders", decision_result.reasonCodes)


if __name__ == "__main__":
    unittest.main()
