"""Independent Meta-Strategy background worker entry points."""

from __future__ import annotations

from backend.app.algorithms.meta_strategy.execution import (
    MetaStrategyPaperOrderReconciliationWorker,
    MetaStrategyPaperOrderSubmissionWorker,
    MetaStrategyStaleOrderCancellationWorker,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.research_workers import (
    MetaStrategyBacktestingWorker as MetaStrategyResearchBacktestingWorker,
    MetaStrategyModelEvaluationWorker as MetaStrategyResearchModelEvaluationWorker,
    MetaStrategyPromotionWorker as MetaStrategyResearchPromotionWorker,
    MetaStrategyReplayWorker as MetaStrategyResearchReplayWorker,
    MetaStrategyReportingWorker as MetaStrategyResearchReportingWorker,
    MetaStrategyTrainingWorker as MetaStrategyResearchTrainingWorker,
)


class MetaStrategyFinalisedBarDecisionWorker(MetaStrategyWorker):
    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.finalised_bar_decision_worker") -> None:
        super().__init__(repository=repository, queue_name="finalised_bar_decisions", worker_id=worker_id)


class MetaStrategyOrderSubmissionWorker(MetaStrategyPaperOrderSubmissionWorker):
    pass


class MetaStrategyOrderReconciliationWorker(MetaStrategyPaperOrderReconciliationWorker):
    pass


class MetaStrategyStaleOrderHandlingWorker(MetaStrategyStaleOrderCancellationWorker):
    pass


class MetaStrategyInventoryReconciliationWorker(MetaStrategyWorker):
    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.inventory_reconciliation_worker") -> None:
        super().__init__(repository=repository, queue_name="inventory_reconciliation", worker_id=worker_id)


class MetaStrategyTrainingWorker(MetaStrategyResearchTrainingWorker):
    pass


class MetaStrategyBacktestingWorker(MetaStrategyResearchBacktestingWorker):
    pass


class MetaStrategyReplayWorker(MetaStrategyResearchReplayWorker):
    pass


class MetaStrategyModelEvaluationWorker(MetaStrategyResearchModelEvaluationWorker):
    pass


class MetaStrategyPromotionWorker(MetaStrategyResearchPromotionWorker):
    pass


class MetaStrategyReportingWorker(MetaStrategyResearchReportingWorker):
    pass


META_STRATEGY_WORKER_CLASSES = (
    MetaStrategyFinalisedBarDecisionWorker,
    MetaStrategyOrderSubmissionWorker,
    MetaStrategyOrderReconciliationWorker,
    MetaStrategyStaleOrderHandlingWorker,
    MetaStrategyInventoryReconciliationWorker,
    MetaStrategyTrainingWorker,
    MetaStrategyBacktestingWorker,
    MetaStrategyReplayWorker,
    MetaStrategyModelEvaluationWorker,
    MetaStrategyPromotionWorker,
    MetaStrategyReportingWorker,
)


__all__ = [
    "META_STRATEGY_WORKER_CLASSES",
    "MetaStrategyBacktestingWorker",
    "MetaStrategyFinalisedBarDecisionWorker",
    "MetaStrategyInventoryReconciliationWorker",
    "MetaStrategyModelEvaluationWorker",
    "MetaStrategyOrderReconciliationWorker",
    "MetaStrategyOrderSubmissionWorker",
    "MetaStrategyPromotionWorker",
    "MetaStrategyReplayWorker",
    "MetaStrategyReportingWorker",
    "MetaStrategyStaleOrderHandlingWorker",
    "MetaStrategyTrainingWorker",
]
