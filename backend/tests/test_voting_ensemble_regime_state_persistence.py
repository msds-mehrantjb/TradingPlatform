from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.algorithms.voting_ensemble.runtime.queue import VotingEnsemblePriorityQueue
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.runtime.worker import VotingEnsembleWorker
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.regime import adx_atr_regime_classifier as classifier_module
from backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier import (
    AdxAtrRegimeClassifier,
    AdxAtrRegimeConfig,
    InMemoryAdxAtrRegimeStateStore,
    LocalStoreAdxAtrRegimeStateStore,
    regime_state_key,
)
from backend.tests.test_voting_ensemble_regime_classifier import range_snapshot, strong_trend_snapshot
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


class RegimeTransitionStatePersistenceTest(unittest.TestCase):
    """The two-bar hysteresis used to live in process memory and reset on restart."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmp.name) / "paper_execution.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pending_transition_survives_a_repository_restart(self) -> None:
        config = AdxAtrRegimeConfig(transitionConfirmationBars=2)
        first_repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        before_restart = AdxAtrRegimeClassifier(config, state_store=LocalStoreAdxAtrRegimeStateStore(first_repository))

        stable = before_restart.evaluate_snapshot_output(strong_trend_snapshot())
        pending = before_restart.evaluate_snapshot_output(range_snapshot())

        self.assertEqual(stable.label, "strong_trend")
        self.assertEqual(pending.transitionState, "pending_transition")
        self.assertEqual(pending.label, "strong_trend")

        # A fresh repository from the same file is what a restarted worker sees.
        restarted_repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        after_restart = AdxAtrRegimeClassifier(config, state_store=LocalStoreAdxAtrRegimeStateStore(restarted_repository))

        confirmed = after_restart.evaluate_snapshot_output(range_snapshot())

        self.assertEqual(confirmed.transitionState, "confirmed_transition")
        self.assertEqual(confirmed.label, "low_volatility")

    def test_in_memory_state_still_resets_on_restart(self) -> None:
        # The replay runner and ad-hoc callers keep the in-memory store; this pins the
        # contrast so the persistent behaviour above is a deliberate wiring choice.
        config = AdxAtrRegimeConfig(transitionConfirmationBars=2)
        first = AdxAtrRegimeClassifier(config, state_store=InMemoryAdxAtrRegimeStateStore())
        first.evaluate_snapshot_output(strong_trend_snapshot())
        first.evaluate_snapshot_output(range_snapshot())

        second = AdxAtrRegimeClassifier(config, state_store=InMemoryAdxAtrRegimeStateStore())
        fresh = second.evaluate_snapshot_output(range_snapshot())

        self.assertEqual(fresh.transitionState, "stable")

    def test_state_is_stored_by_symbol_and_timeframe(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        classifier = AdxAtrRegimeClassifier(state_store=LocalStoreAdxAtrRegimeStateStore(repository))

        classifier.evaluate_snapshot_output(strong_trend_snapshot())

        self.assertEqual(regime_state_key("SPY"), "SPY:1Min")
        record = repository.read_snapshot("regime_transition_state.SPY.1Min")
        self.assertEqual(record["symbol"], "SPY")
        self.assertEqual(record["timeframe"], "1Min")
        self.assertEqual(record["stateKey"], "SPY:1Min")
        self.assertEqual(record["state"]["activeLabel"], "strong_trend")
        self.assertEqual(record["state"]["pendingCount"], 0)
        self.assertEqual(record["state"]["transitionState"], "stable")

    def test_an_unchanged_state_does_not_rewrite_the_store(self) -> None:
        # A write rewrites the whole snapshot file, and the classifier saves every bar.
        repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        classifier = AdxAtrRegimeClassifier(state_store=LocalStoreAdxAtrRegimeStateStore(repository))
        classifier.evaluate_snapshot_output(strong_trend_snapshot())
        first_write = self.store_path.stat().st_mtime_ns

        for _ in range(3):
            classifier.evaluate_snapshot_output(strong_trend_snapshot())

        self.assertEqual(self.store_path.stat().st_mtime_ns, first_write)

        # A real transition still persists.
        classifier.evaluate_snapshot_output(range_snapshot())
        record = repository.read_snapshot("regime_transition_state.SPY.1Min")
        self.assertEqual(record["state"]["transitionState"], "pending_transition")

    def test_both_entry_points_share_one_hysteresis_key(self) -> None:
        # The classifier has two entry points; two spellings of the key would give one
        # classifier two independent states and two records on disk.
        source = Path(classifier_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn('state_key="SPY"', source)
        self.assertEqual(source.count("state_key=regime_state_key("), 2)

    def test_unreadable_state_starts_from_unknown(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        repository.write_snapshot("regime_transition_state.SPY.1Min", {"state": {"activeLabel": "not-a-label"}})
        store = LocalStoreAdxAtrRegimeStateStore(repository)

        with self.assertLogs(classifier_module.logger, level="WARNING") as captured:
            state = store.load("SPY:1Min")

        self.assertEqual(state.activeLabel, "unknown")
        self.assertTrue(any("transition_state_unreadable" in line for line in captured.output))

    def test_worker_default_pipeline_persists_regime_state_in_its_repository(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository(self.store_path)
        runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            execution_mode="LOCAL_PAPER",
            auto_start=False,
        )
        worker = VotingEnsembleWorker(queue=VotingEnsemblePriorityQueue(), status_store=VotingEnsembleStatusStore(), paper_execution_runtime=runtime)

        service = worker.service.service
        self.assertIsInstance(service, VotingEnsembleService)
        store = service.regime_classifier.state_store
        self.assertIsInstance(store, LocalStoreAdxAtrRegimeStateStore)
        self.assertIs(store.repository, repository)

    def test_service_without_an_injected_classifier_stays_in_memory(self) -> None:
        service = VotingEnsembleService()
        self.assertIsInstance(service.regime_classifier.state_store, InMemoryAdxAtrRegimeStateStore)


class RealizedVolatilityFallbackTest(unittest.TestCase):
    def test_short_tape_logs_the_assumed_percentile_and_marks_the_output(self) -> None:
        classifier = AdxAtrRegimeClassifier(state_store=InMemoryAdxAtrRegimeStateStore())
        # Twenty candles give ADX and ATR, but fewer than five ranked realized-volatility
        # samples, so the percentile cannot be measured.
        short_tape = build_live_paper_snapshot(snapshot_payload(candles(20)))

        with self.assertLogs(classifier_module.logger, level="WARNING") as captured:
            output = classifier.evaluate_snapshot_output(short_tape)

        self.assertTrue(any("realized_volatility_percentile_assumed symbol=SPY candles=20" in line for line in captured.output))
        self.assertNotEqual(output.label, "unknown")
        self.assertIn("regime.realized_volatility_percentile_assumed_0_5", output.reasonCodes)

    def test_long_tape_measures_the_percentile_without_a_warning(self) -> None:
        classifier = AdxAtrRegimeClassifier(state_store=InMemoryAdxAtrRegimeStateStore())

        with self.assertNoLogs(classifier_module.logger, level="WARNING"):
            output = classifier.evaluate_snapshot_output(strong_trend_snapshot())

        self.assertNotIn("regime.realized_volatility_percentile_assumed_0_5", output.reasonCodes)


if __name__ == "__main__":
    unittest.main()
