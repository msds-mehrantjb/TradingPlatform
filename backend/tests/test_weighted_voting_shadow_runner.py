from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.weighted_voting.shadow_runner import run_shadow_evidence


def test_shadow_runner_collects_evidence_without_live_orders():
    output_dir = Path("backend/data/algorithms/weighted_voting/shadow_evidence/test_runs") / uuid4().hex

    evidence = run_shadow_evidence(events=2, output_dir=output_dir)

    assert evidence["algorithmId"] == "weighted_voting"
    assert evidence["mode"] == "background_shadow_evidence"
    assert evidence["liveOrdersSubmitted"] is False
    assert evidence["automaticPaperSubmissionEnabled"] is False
    assert evidence["decisions"]["count"] == 2
    assert evidence["skippedTrades"]["count"] == 2
    assert evidence["acceptedProposals"]["count"] == 1
    assert evidence["acceptedProposals"]["controlledAcceptanceProbe"]["accepted"] is True
    assert evidence["latency"]["eventCount"] == 2
    assert evidence["duplicatePrevention"]["duplicateEventPrevented"] is True
    assert evidence["reconciliationHealth"]["inventoryReconciled"] is True
    assert evidence["reconciliationHealth"]["discrepancyCount"] == 0
    assert evidence["restartRecovery"]["passed"] is True
    assert evidence["protectiveOrderBehavior"]["passed"] is True
    assert evidence["simulatedFills"]["observedSlippagePerShare"] >= 0
    assert evidence["pnl"]["netUnrealizedAfterFees"] is not None
