"""Evidence-derived final acceptance report for Regime staged rollout."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from backend.app.algorithms.regime.rollout import (
    REGIME_ROLLOUT_STAGES,
    REQUIRED_ML_PROMOTION_EVIDENCE,
    STAGE_REQUIREMENTS,
    RegimeRolloutEvidence,
    RegimeRolloutFlags,
    paper_submission_allowed,
)


REGIME_FINAL_ACCEPTANCE_VERSION = "regime_final_acceptance_v2"
REGIME_ALGORITHM_ID = "regime"

REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS = frozenset(
    {
        "backend/tests/regime",
        "backend/tests/regime/test_step10_paper_readiness_rollout.py",
        "backend/tests/test_regime_phase17_rollout.py",
        "backend/tests/test_regime_final_acceptance.py",
    }
)


class RegimeAcceptanceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class RegimeAcceptanceItem:
    statement: str
    status: RegimeAcceptanceStatus
    evidence: tuple[str, ...]
    category: str = "Final acceptance"
    limitations: tuple[str, ...] = ()
    required_for_completion: bool = True

    def with_result(
        self,
        status: RegimeAcceptanceStatus,
        *,
        evidence: tuple[str, ...],
        limitations: tuple[str, ...] = (),
    ) -> "RegimeAcceptanceItem":
        return replace(self, status=status, evidence=(*self.evidence, *evidence), limitations=limitations)

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "statement": self.statement,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
            "requiredForCompletion": self.required_for_completion,
        }


@dataclass(frozen=True)
class RegimeFinalAcceptanceEvidence:
    passing_test_files: frozenset[str] = frozenset()
    frontend_typecheck_passed: bool = False
    frontend_tests_passed: bool = False
    frontend_build_passed: bool = False
    backend_authority_scan_passed: bool = False
    no_live_trading_scan_passed: bool = False
    rollout_evidence: RegimeRolloutEvidence = field(default_factory=RegimeRolloutEvidence)
    flags: RegimeRolloutFlags = field(default_factory=RegimeRolloutFlags)


def build_regime_final_acceptance_report(evidence: RegimeFinalAcceptanceEvidence | RegimeRolloutEvidence | None = None) -> dict[str, object]:
    runtime_evidence = _coerce_acceptance_evidence(evidence)
    items = derive_regime_final_acceptance_items(runtime_evidence)
    blocking = [
        item
        for item in items
        if item.required_for_completion and item.status is not RegimeAcceptanceStatus.PASS
    ]
    counts = {
        "PASS": sum(1 for item in items if item.status is RegimeAcceptanceStatus.PASS),
        "FAIL": sum(1 for item in items if item.status is RegimeAcceptanceStatus.FAIL),
        "NOT_RUN": sum(1 for item in items if item.status is RegimeAcceptanceStatus.NOT_RUN),
        "INSUFFICIENT_EVIDENCE": sum(1 for item in items if item.status is RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE),
    }
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "version": REGIME_FINAL_ACCEPTANCE_VERSION,
        "complete": not blocking,
        "counts": counts,
        "blockingStatements": [item.statement for item in blocking],
        "items": [item.as_dict() for item in items],
        "evidenceDerived": True,
        "nonPassingStatuses": ("FAIL", "NOT_RUN", "INSUFFICIENT_EVIDENCE"),
        "requiredTests": tuple(sorted(REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS)),
        "rolloutStages": REGIME_ROLLOUT_STAGES,
    }


def derive_regime_final_acceptance_items(evidence: RegimeFinalAcceptanceEvidence) -> tuple[RegimeAcceptanceItem, ...]:
    return tuple(_apply_evidence(item, evidence) for item in REGIME_FINAL_ACCEPTANCE_ITEMS)


def regime_acceptance_is_complete(evidence: RegimeFinalAcceptanceEvidence | RegimeRolloutEvidence | None = None) -> bool:
    return bool(build_regime_final_acceptance_report(evidence)["complete"])


def _apply_evidence(item: RegimeAcceptanceItem, evidence: RegimeFinalAcceptanceEvidence) -> RegimeAcceptanceItem:
    rollout = evidence.rollout_evidence
    statement = item.statement
    if statement == "Stage A deterministic offline validation passed.":
        return _requirements_item(item, rollout, "stage_a_offline_validation")
    if statement == "Stage B background shadow runtime evidence passed.":
        return _requirements_item(item, rollout, "stage_b_shadow_runtime")
    if statement == "Stage C paper intent validation evidence passed without broker submission.":
        if rollout.broker_orders_created_in_intent_validation:
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.intent_validation_created_broker_orders",),
                limitations=("Stage C created broker orders, which is forbidden.",),
            )
        return _requirements_item(item, rollout, "stage_c_paper_intent_validation")
    if statement == "Stage D limited SPY paper submission gates passed.":
        return _requirements_item(item, rollout, "stage_d_limited_spy_paper_submission")
    if statement == "Stage E expanded paper validation gates passed.":
        return _requirements_item(item, rollout, "stage_e_expanded_paper_validation")
    if statement == "Focused, full backend, frontend and acceptance tests passed.":
        tests_passed = (
            REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS.issubset(evidence.passing_test_files)
            and evidence.frontend_typecheck_passed
            and evidence.frontend_tests_passed
            and evidence.frontend_build_passed
        )
        return _pass_or_not_run(
            item,
            tests_passed,
            tuple(sorted(REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS)) + ("frontend:typecheck", "frontend:test", "frontend:build:ci"),
            ("Requires focused Regime tests, full backend tests, frontend typecheck/tests/build, and final acceptance tests to pass.",),
        )
    if statement == "Paper submission remains disabled until all preceding gates pass.":
        if rollout.paper_submission_attempted_before_stage_d or rollout.broker_orders_created_in_shadow or rollout.broker_orders_created_in_intent_validation:
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.paper_submission_before_allowed_stage",),
                limitations=("Paper or broker submission occurred before Stage D gates.",),
            )
        if evidence.flags.paper_submission_enabled and not paper_submission_allowed(flags=evidence.flags, evidence=rollout):
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.paper_submission_flag_enabled_without_gates",),
                limitations=("Paper submission flag is enabled before Stage D gates pass.",),
            )
        return item.with_result(RegimeAcceptanceStatus.PASS, evidence=("regime.rollout.paper_submission_gated",))
    if statement == "Automatic order submission remains disabled by default.":
        if evidence.flags.automatic_order_submission_enabled or rollout.automatic_order_submission_enabled:
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.automatic_order_submission_enabled",),
                limitations=("Automatic order submission must remain disabled.",),
            )
        return item.with_result(RegimeAcceptanceStatus.PASS, evidence=("regime.rollout.automatic_submission_disabled",))
    if statement == "Live trading remains impossible.":
        if rollout.live_trading_enabled:
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.live_trading_enabled",),
                limitations=("Regime evidence indicates live trading was enabled.",),
            )
        return item.with_result(RegimeAcceptanceStatus.PASS, evidence=("regime.rollout.live_trading_never_allowed",))
    if statement == "ML remains shadow-only through every staged rollout step.":
        if evidence.flags.ml_mode != "shadow" or rollout.ml_mode != "shadow" or not rollout.ml_shadow_only:
            return item.with_result(
                RegimeAcceptanceStatus.FAIL,
                evidence=("regime.rollout.ml_not_shadow_only",),
                limitations=("ML must remain shadow-only unless a future promotion task accepts separate evidence.",),
            )
        return item.with_result(RegimeAcceptanceStatus.PASS, evidence=("regime.rollout.ml_shadow_only",))
    if statement == "Future ML promotion is blocked without separate stability, improvement, calibration, drift and rollback evidence.":
        missing = tuple(item for item in REQUIRED_ML_PROMOTION_EVIDENCE if item not in rollout.persisted_evidence_ids)
        if missing:
            return item.with_result(
                RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
                evidence=tuple(f"missing:{item}" for item in missing),
                limitations=("This is intentionally non-passing until a separate future ML promotion task supplies all required evidence.",),
            )
        return item.with_result(RegimeAcceptanceStatus.PASS, evidence=REQUIRED_ML_PROMOTION_EVIDENCE)
    if statement == "Backend Python remains the only authoritative decision and backtest path.":
        return _pass_or_not_run(
            item,
            evidence.backend_authority_scan_passed,
            ("backend.app.algorithms.regime.execution_pipeline", "backend.app.algorithms.regime.backtest.engine"),
            ("Requires authority-boundary scans to pass.",),
        )
    if statement == "No live-trading endpoint or mode is enabled.":
        return _pass_or_not_run(
            item,
            evidence.no_live_trading_scan_passed,
            ("backend/app/algorithms/regime", "frontend/tests/V2DecisionPanel.test.ts"),
            ("Requires no-live-trading enforcement scans to pass.",),
        )
    return item


def _requirements_item(item: RegimeAcceptanceItem, rollout: RegimeRolloutEvidence, stage: str) -> RegimeAcceptanceItem:
    missing = tuple(requirement for requirement in _requirements_through(stage) if not _requirement_passed(rollout, requirement))
    if missing:
        return item.with_result(
            RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
            evidence=tuple(f"missing:{requirement}" for requirement in missing),
            limitations=(f"Requires persisted evidence for {stage}.",),
        )
    return item.with_result(
        RegimeAcceptanceStatus.PASS,
        evidence=tuple(f"regime.rollout.evidence:{requirement}" for requirement in _requirements_through(stage)),
    )


def _pass_or_not_run(
    item: RegimeAcceptanceItem,
    accepted: bool,
    evidence: tuple[str, ...],
    limitations: tuple[str, ...],
) -> RegimeAcceptanceItem:
    return item.with_result(
        RegimeAcceptanceStatus.PASS if accepted else RegimeAcceptanceStatus.NOT_RUN,
        evidence=evidence,
        limitations=() if accepted else limitations,
    )


def _requirements_through(stage: str) -> tuple[str, ...]:
    selected: list[str] = []
    for candidate in REGIME_ROLLOUT_STAGES:
        selected.extend(STAGE_REQUIREMENTS[candidate])
        if candidate == stage:
            break
    return tuple(selected)


def _requirement_passed(evidence: RegimeRolloutEvidence, requirement: str) -> bool:
    return bool(getattr(evidence, requirement)) and (
        requirement in evidence.persisted_evidence_ids
        or f"regime.rollout.evidence:{requirement}" in evidence.persisted_evidence_ids
    )


def _coerce_acceptance_evidence(evidence: RegimeFinalAcceptanceEvidence | RegimeRolloutEvidence | None) -> RegimeFinalAcceptanceEvidence:
    if evidence is None:
        return RegimeFinalAcceptanceEvidence()
    if isinstance(evidence, RegimeRolloutEvidence):
        return RegimeFinalAcceptanceEvidence(rollout_evidence=evidence)
    return evidence


REGIME_FINAL_ACCEPTANCE_ITEMS: tuple[RegimeAcceptanceItem, ...] = (
    RegimeAcceptanceItem(
        "Stage A deterministic offline validation passed.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/tests/regime", "backend/app/algorithms/regime/backtest"),
        category="Stage A",
    ),
    RegimeAcceptanceItem(
        "Stage B background shadow runtime evidence passed.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/app/algorithms/regime/runtime_supervisor.py", "backend/app/algorithms/regime/runtime_workers.py"),
        category="Stage B",
    ),
    RegimeAcceptanceItem(
        "Stage C paper intent validation evidence passed without broker submission.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/app/algorithms/regime/execution_gateway.py", "backend/app/algorithms/regime/runtime_idempotency.py"),
        category="Stage C",
    ),
    RegimeAcceptanceItem(
        "Stage D limited SPY paper submission gates passed.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/app/algorithms/regime/rollout.py", "backend/app/algorithms/regime/configuration.py"),
        category="Stage D",
    ),
    RegimeAcceptanceItem(
        "Stage E expanded paper validation gates passed.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/app/algorithms/regime/position_manager.py", "backend/app/algorithms/regime/execution_gateway.py"),
        category="Stage E",
    ),
    RegimeAcceptanceItem(
        "Focused, full backend, frontend and acceptance tests passed.",
        RegimeAcceptanceStatus.NOT_RUN,
        ("scripts/ci_quality_gates.py",),
        category="Verification",
    ),
    RegimeAcceptanceItem(
        "Paper submission remains disabled until all preceding gates pass.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/rollout.py",),
        category="Execution safety",
    ),
    RegimeAcceptanceItem(
        "Automatic order submission remains disabled by default.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/rollout.py",),
        category="Execution safety",
    ),
    RegimeAcceptanceItem(
        "Live trading remains impossible.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/configuration.py", "backend/app/algorithms/regime/rollout.py"),
        category="Execution safety",
    ),
    RegimeAcceptanceItem(
        "ML remains shadow-only through every staged rollout step.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/ml/promotion_policy.py", "backend/app/algorithms/regime/rollout.py"),
        category="ML",
    ),
    RegimeAcceptanceItem(
        "Future ML promotion is blocked without separate stability, improvement, calibration, drift and rollback evidence.",
        RegimeAcceptanceStatus.INSUFFICIENT_EVIDENCE,
        ("backend/app/algorithms/regime/ml/promotion_policy.py",),
        category="ML",
    ),
    RegimeAcceptanceItem(
        "Backend Python remains the only authoritative decision and backtest path.",
        RegimeAcceptanceStatus.NOT_RUN,
        ("backend/app/algorithms/regime/execution_pipeline.py", "backend/app/algorithms/regime/backtest/engine.py"),
        category="Authority",
    ),
    RegimeAcceptanceItem(
        "No live-trading endpoint or mode is enabled.",
        RegimeAcceptanceStatus.NOT_RUN,
        ("backend/app/algorithms/regime", "frontend/tests/V2DecisionPanel.test.ts"),
        category="Execution safety",
    ),
)


__all__ = [
    "REGIME_FINAL_ACCEPTANCE_ITEMS",
    "REGIME_FINAL_ACCEPTANCE_REQUIRED_TESTS",
    "REGIME_FINAL_ACCEPTANCE_VERSION",
    "RegimeAcceptanceItem",
    "RegimeAcceptanceStatus",
    "RegimeFinalAcceptanceEvidence",
    "build_regime_final_acceptance_report",
    "derive_regime_final_acceptance_items",
    "regime_acceptance_is_complete",
]
