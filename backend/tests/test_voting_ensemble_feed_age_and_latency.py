from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.voting_ensemble.runtime.commands import manual_evaluation_command
from backend.app.algorithms.voting_ensemble.runtime.worker import (
    _maximum_queue_latency_ms,
    _payload_with_queue_measurements,
    _queue_delay_seconds,
)
from backend.app.algorithms.voting_ensemble.service import (
    VotingEnsembleService,
    _auxiliary_feed_age_seconds,
    _decision_deadline_reasons,
    _primary_candle_age_seconds,
)
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.trading_settings import resolve_one_minute_trading_settings
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


def payload_evaluated_at(seconds_after_last_bar_completion: int) -> dict:
    """The fixture stamps completion at the bar's own timestamp and evaluates there; move the clock and the quote."""
    payload = snapshot_payload(candles(30))
    evaluation = datetime.fromisoformat(payload["data_timestamp"]) + timedelta(seconds=seconds_after_last_bar_completion)
    stamp = evaluation.isoformat()
    nbbo = {**payload["nbbo"], "quoteTimestamp": stamp, "lastTradeTimestamp": stamp, "marketDataReceiptTimestamp": stamp}
    return {**payload, "data_timestamp": stamp, "nbbo": nbbo, "market_context": {**payload["market_context"], "nbbo": nbbo}}


class FeedAgeGateTest(unittest.TestCase):
    def test_candle_age_is_measured_from_the_bar_completion(self) -> None:
        snapshot = build_live_paper_snapshot(payload_evaluated_at(30))
        self.assertAlmostEqual(_primary_candle_age_seconds(snapshot) or 0.0, 30.0, places=3)

    def test_bar_older_than_the_primary_feed_limit_fails_the_fresh_candle_gate(self) -> None:
        settings = resolve_one_minute_trading_settings({})
        limit = settings.dataFreshness.maxPrimaryFeedAgeSeconds

        fresh = VotingEnsembleService().evaluate(payload_evaluated_at(limit - 5))
        stale = VotingEnsembleService().evaluate(payload_evaluated_at(limit + 5))

        self.assertNotIn("voting_ensemble.local_gate.stale_or_missing_candle", fresh["reason_codes"])
        self.assertIn("voting_ensemble.local_gate.stale_or_missing_candle", stale["reason_codes"])
        self.assertEqual(stale["final_signal"], "Hold")

    def test_auxiliary_feed_age_is_the_oldest_of_qqq_iwm_and_breadth(self) -> None:
        snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))
        aligned_age = _auxiliary_feed_age_seconds(snapshot)
        self.assertIsNotNone(aligned_age)

        old_breadth = snapshot.breadth.model_copy(update={"timestamp": snapshot.evaluationTimestamp - timedelta(seconds=400)})
        aged = snapshot.model_copy(update={"breadth": old_breadth})
        self.assertGreaterEqual(_auxiliary_feed_age_seconds(aged) or 0.0, 400.0)


class DecisionLatencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = resolve_one_minute_trading_settings({})
        self.snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))

    def with_operational(self, **values):
        return self.snapshot.model_copy(update={"operationalHealthSnapshot": {**self.snapshot.operationalHealthSnapshot, **values}})

    def test_each_limit_names_its_own_reason(self) -> None:
        limits = self.settings.latencyLimits
        quick = _decision_deadline_reasons(self.with_operational(decisionAgeSeconds=0.2, queueDelayMs=200.0), self.settings, 50.0)
        slow_queue = _decision_deadline_reasons(self.with_operational(decisionAgeSeconds=1.0, queueDelayMs=limits.maxQueueLatencyMs + 1), self.settings, 50.0)
        slow_process = _decision_deadline_reasons(self.with_operational(queueDelayMs=0.0), self.settings, limits.maxDecisionLatencyMs + 1)
        past_deadline = _decision_deadline_reasons(self.with_operational(decisionAgeSeconds=limits.commandDeadlineSeconds + 1), self.settings, 50.0)

        self.assertEqual(quick, [])
        self.assertEqual(slow_queue, ["voting_ensemble.latency.queue_latency_exceeded"])
        self.assertEqual(slow_process, ["voting_ensemble.latency.decision_latency_exceeded"])
        self.assertEqual(past_deadline, ["voting_ensemble.latency.command_deadline_exceeded"])

    def test_a_late_queue_fails_the_decision_deadline_gate(self) -> None:
        payload = snapshot_payload(candles(30))
        operational = {**payload["market_context"]["operationalHealthSnapshot"], "queueDelayMs": self.settings.latencyLimits.maxQueueLatencyMs + 1000.0}
        late = VotingEnsembleService().evaluate({**payload, "market_context": {**payload["market_context"], "operationalHealthSnapshot": operational}})

        # Refused at the pre-gate, before any candidate exists.
        self.assertIn("voting_ensemble.local_gate.decision_deadline_expired", late["reason_codes"])
        self.assertIn("voting_ensemble.evaluate.blocked_by_local_safety", late["reason_codes"])
        self.assertEqual(late["final_signal"], "Hold")


class WorkerQueueMeasurementTest(unittest.TestCase):
    def test_queue_delay_is_stamped_on_the_operational_snapshot(self) -> None:
        command = manual_evaluation_command(snapshot_payload(candles(30)))
        aged = command.model_copy(update={"createdAt": datetime.now(UTC) - timedelta(seconds=2)})

        self.assertGreaterEqual(_queue_delay_seconds(aged), 2.0)
        stamped = _payload_with_queue_measurements({"market_context": {"operationalHealthSnapshot": {"decisionAgeSeconds": 0.0}}}, aged)
        operational = stamped["market_context"]["operationalHealthSnapshot"]
        self.assertGreaterEqual(operational["queueDelayMs"], 2000.0)
        self.assertGreaterEqual(operational["decisionAgeSeconds"], 2.0)
        self.assertEqual(_maximum_queue_latency_ms(), 5000.0)


if __name__ == "__main__":
    unittest.main()
