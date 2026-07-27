"""Final acceptance ledger for the Meta-Strategy algorithm package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.observability import (
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore


META_STRATEGY_FINAL_ACCEPTANCE_VERSION = "meta_strategy_final_acceptance_v1"


class MetaStrategyAcceptanceStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MetaStrategyAcceptanceItem:
    item_id: str
    statement: str
    status: MetaStrategyAcceptanceStatus
    evidence: tuple[str, ...]
    category: str = "Final acceptance"
    required_for_completion: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "category": self.category,
            "statement": self.statement,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "requiredForCompletion": self.required_for_completion,
        }


def build_meta_strategy_final_acceptance_report() -> dict[str, object]:
    snapshot = build_meta_strategy_observability_snapshot(
        job_repository=MetaStrategyJobRepository(),
        inventory_repository=MetaStrategySqliteRepository(),
        settings_store=MetaStrategySettingsStore("./data/meta_strategy_settings.db"),
    )
    report = build_meta_strategy_evidence_acceptance_report(snapshot)
    return {**report, "version": META_STRATEGY_FINAL_ACCEPTANCE_VERSION}


def meta_strategy_acceptance_is_complete() -> bool:
    return bool(build_meta_strategy_final_acceptance_report()["complete"])


META_STRATEGY_FINAL_ACCEPTANCE_ITEMS: tuple[MetaStrategyAcceptanceItem, ...] = ()
