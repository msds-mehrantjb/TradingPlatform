import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository, MetaStrategyJobStatus
from backend.app.algorithms.meta_strategy.research_workers import (
    META_STRATEGY_RESEARCH_WORKFLOW_JOB_TYPES,
    MetaStrategyBacktestingWorker,
    MetaStrategyModelEvaluationWorker,
    MetaStrategyPromotionWorker,
    MetaStrategyReplayWorker,
    MetaStrategyReportingWorker,
    MetaStrategyTrainingWorker,
)
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase10ResearchWorkersTest(unittest.TestCase):
    def test_api_research_requests_enqueue_jobs_without_running_heavy_functions(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        service = MetaStrategyApplicationService(job_repository=repository)

        training = service.train({"trainingArguments": {"datasetVersion": "dataset-v1", "rows": []}})
        backtest = service.backtest({"decisionRequests": [request_with().model_dump(mode="json")]})
        replay = service.deterministic_replay({"snapshotSetVersion": "snapshots-v1", "decisionRequests": [request_with().model_dump(mode="json")]})
        walk = service.walk_forward_evaluation({"datasetVersion": "wf-v1"})
        holdout = service.holdout_evaluation({"datasetVersion": "holdout-v1"})
        costs = service.cost_slippage_analysis({"executionDatasetVersion": "cost-v1"})
        inference = service.model_inference_validation({"datasetVersion": "inference-v1", "modelVersion": "candidate-v1"})
        report = service.generate_report({"reportType": "phase10"})

        responses = (training, backtest, replay, walk, holdout, costs, inference, report)
        self.assertTrue(all(response["status"] == "OK" for response in responses))
        self.assertTrue(all(response["payload"]["durableQueue"] for response in responses))
        self.assertEqual(repository.queue_status()["queues"]["finalised_bar_decisions"]["pending"], 0)
        self.assertGreaterEqual(repository.queue_status()["queues"]["training"]["pending"], 1)
        self.assertGreaterEqual(repository.queue_status()["queues"]["backtesting"]["pending"], 3)
        self.assertGreaterEqual(repository.queue_status()["queues"]["model_evaluation"]["pending"], 2)
        self.assertGreaterEqual(repository.queue_status()["queues"]["reporting"]["pending"], 1)

    def test_every_required_workflow_type_is_registered(self) -> None:
        self.assertEqual(
            set(META_STRATEGY_RESEARCH_WORKFLOW_JOB_TYPES),
            {
                "deterministic_replay",
                "backtesting",
                "walk_forward_evaluation",
                "holdout_evaluation",
                "cost_slippage_analysis",
                "training",
                "model_inference_validation",
                "paper_stability_evaluation",
                "model_promotion",
                "settings_promotion",
                "report_generation",
            },
        )

    def test_backtest_worker_stores_reproducible_immutable_artifact_and_progress(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        job = repository.enqueue_job(job_type="backtesting", idempotency_key="backtest-v1", payload={"decisionRequests": [request_with().model_dump(mode="json")], "settingsVersion": "settings-v1", "datasetVersion": "bars-v1"}, now=NOW)
        worker = MetaStrategyBacktestingWorker(repository=repository, runner=recording_runner("backtesting"))

        worker.run_once(now=NOW)

        stored = repository.read_job(job.job_id)
        artifact = repository.latest_workflow_artifact(job.job_id)
        progress = repository.job_progress(job.job_id)
        self.assertEqual(stored.status, MetaStrategyJobStatus.SUCCEEDED)
        self.assertEqual(artifact["workflowType"], "backtesting")
        self.assertEqual(artifact["metadata"]["dataVersion"], "bars-v1")
        self.assertEqual(artifact["metadata"]["settingsVersion"], "settings-v1")
        self.assertEqual(artifact["metadata"]["featureVersion"], "meta_strategy_feature_schema_v1")
        self.assertEqual(artifact["metadata"]["randomSeed"], 0)
        self.assertIn("codeBuildIdentifier", artifact["metadata"])
        self.assertEqual(progress[-1]["status"], "SUCCEEDED")

    def test_worker_uses_same_runtime_strategy_pipeline_for_replay(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        repository.enqueue_job(job_type="deterministic_replay", idempotency_key="replay-v1", payload={"decisionRequests": [request_with().model_dump(mode="json")]}, now=NOW)
        called = []

        def runner(workflow_type, payload):
            called.append((workflow_type, payload["runtimeParityEntrypoint"]))
            return {"decisionCount": len(payload["decisionRequests"])}

        MetaStrategyReplayWorker(repository=repository, runner=runner).run_once(now=NOW)

        self.assertEqual(called, [("deterministic_replay", "backend.app.algorithms.meta_strategy.execution_pipeline.run_meta_strategy_execution_pipeline")])

    def test_training_worker_cancellation_does_not_write_artifact(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        job = repository.enqueue_job(job_type="training", idempotency_key="training-cancel", payload={"trainingArguments": {"datasetVersion": "dataset-v1"}}, cancellable=True, now=NOW)
        repository.cancel_job(job.job_id, now=NOW)

        result = MetaStrategyTrainingWorker(repository=repository, runner=recording_runner("training")).run_once(now=NOW)

        self.assertIsNone(result)
        self.assertEqual(repository.workflow_artifacts(job.job_id), ())

    def test_promotion_requires_paper_stability_and_cannot_promote_itself(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        no_evidence = repository.enqueue_job(job_type="model_promotion", idempotency_key="promotion-reject", payload={"candidateArtifact": {"artifactId": "candidate-1"}, "evidence": {"validated": True}}, now=NOW)
        self_promoting = repository.enqueue_job(job_type="model_promotion", idempotency_key="promotion-self", payload={"candidateArtifact": {"artifactId": "candidate-2", "generatedByJobId": "self"}, "evidence": complete_promotion_evidence()}, now=NOW)
        worker = MetaStrategyPromotionWorker(repository=repository, runner=recording_runner("model_promotion"))

        worker.run_once(now=NOW)
        worker.run_once(now=NOW)

        self.assertEqual(repository.read_job(no_evidence.job_id).status, MetaStrategyJobStatus.DEAD_LETTER)
        self.assertEqual(repository.read_job(self_promoting.job_id).status, MetaStrategyJobStatus.DEAD_LETTER)
        self.assertEqual(repository.workflow_artifacts(no_evidence.job_id), ())
        self.assertEqual(repository.workflow_artifacts(self_promoting.job_id), ())

    def test_model_promotion_with_paper_stability_is_atomic_and_reversible(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        job = repository.enqueue_job(
            job_type="model_promotion",
            idempotency_key="promotion-ok",
            payload={"candidateArtifact": {"artifactId": "candidate-3"}, "evidence": complete_promotion_evidence()},
            now=NOW,
        )

        MetaStrategyPromotionWorker(repository=repository, runner=recording_runner("model_promotion")).run_once(now=NOW)

        artifact = repository.latest_workflow_artifact(job.job_id)
        active = repository.active_model_pointer()
        self.assertEqual(active["modelArtifactId"], "candidate-3")
        self.assertEqual(active["promotionJobId"], job.job_id)
        self.assertTrue(artifact["payload"]["promotion"]["reversible"])
        self.assertEqual(artifact["payload"]["promotion"]["previousModelArtifactId"], "shadow-only")

    def test_model_promotion_rejects_reinforcement_learning_for_paper_execution(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        job = repository.enqueue_job(
            job_type="model_promotion",
            idempotency_key="promotion-rl-reject",
            payload={"candidateArtifact": {"artifactId": "candidate-rl", "modelKind": "reinforcement_learning"}, "evidence": complete_promotion_evidence()},
            now=NOW,
        )

        result = MetaStrategyPromotionWorker(repository=repository, runner=recording_runner("model_promotion")).run_once(now=NOW)

        self.assertEqual(repository.read_job(job.job_id).status, MetaStrategyJobStatus.DEAD_LETTER)
        self.assertEqual(result["reasonCodes"], ("meta_strategy.promotion.reinforcement_learning_not_allowed_for_paper_execution",))

    def test_research_queues_do_not_use_decision_worker_queue(self) -> None:
        repository = MetaStrategyJobRepository(f"sqlite:///{temp_db_path()}")
        service = MetaStrategyApplicationService(job_repository=repository)

        service.train({"trainingArguments": {"datasetVersion": "dataset-v1"}})
        service.backtest({"decisionRequests": [request_with().model_dump(mode="json")]})
        service.deterministic_replay({"decisionRequests": [request_with().model_dump(mode="json")]})
        queues = repository.queue_status()["queues"]

        self.assertEqual(queues["finalised_bar_decisions"]["pending"], 0)
        self.assertGreater(queues["training"]["pending"], 0)
        self.assertGreater(queues["backtesting"]["pending"], 0)
        self.assertGreater(queues["replay"]["pending"], 0)


def recording_runner(expected: str):
    def runner(workflow_type, payload):
        if workflow_type != expected:
            raise AssertionError((workflow_type, expected))
        return {"workflowType": workflow_type, "payloadKeys": sorted(payload.keys())}

    return runner


def complete_promotion_evidence() -> dict:
    return {
        "minimumSampleSize": {"passed": True, "rows": 500},
        "chronologicalHoldout": {"passed": True},
        "walkForward": {"passed": True},
        "probabilityCalibration": {"passed": True},
        "costAdjustedPerformance": {"passed": True},
        "regimeStability": {"passed": True},
        "missingDataBehavior": {"passed": True},
        "outOfDistributionHandling": {"passed": True},
        "deterministicReplay": {"passed": True},
        "paperStability": {"stable": True},
        "rollbackTesting": {"passed": True},
    }


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-phase10-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
