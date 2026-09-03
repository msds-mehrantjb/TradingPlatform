import unittest
from datetime import UTC, date, datetime

from backend.app.algorithms.voting_ensemble.ensemble.family_aware import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService
from backend.app.algorithms.voting_ensemble.snapshot import build_live_paper_snapshot
from backend.app.algorithms.voting_ensemble.strategies.context.pipeline import (
    VotingEnsembleContextPipeline,
    clear_shadow_context_outputs,
    shadow_context_outputs,
)
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, active_module_ids, shadow_module_ids
from backend.app.domain.models import ContextSignal, Direction, Signal, StrategyFamily, StrategyRole, StrategySignal
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "test-context-pipeline"


class VotingEnsembleContextPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_shadow_context_outputs()

    def test_context_modules_use_same_snapshot_and_never_cast_directional_votes(self) -> None:
        snapshot = build_live_paper_snapshot(snapshot_payload(candles(30)))

        result = VotingEnsembleContextPipeline().evaluate(
            snapshot,
            active_module_ids=active_module_ids(StrategyCollection.CONTEXT),
            shadow_module_ids=shadow_module_ids(StrategyCollection.CONTEXT),
        )
        all_outputs = (*result.active, *result.shadow)

        self.assertEqual({vote.signal for vote in all_outputs}, {"Hold"})
        self.assertEqual({vote.direction for vote in all_outputs}, {0})
        self.assertFalse(any(vote.eligible for vote in all_outputs))
        self.assertTrue(all(vote.role == "context" for vote in all_outputs))
        self.assertTrue(all(vote.features["pipelineVersion"] == "voting_ensemble_context_pipeline_v1" for vote in all_outputs))
        self.assertTrue(all("spyLatest=" in str(vote.features["sourceTimestamps"]) for vote in all_outputs))

    def test_shadow_context_outputs_are_persisted_but_not_active(self) -> None:
        payload = snapshot_payload(candles(30))
        payload["market_context"]["event"].update({"importance": "high", "state": "active"})
        snapshot = build_live_paper_snapshot(payload)

        result = VotingEnsembleContextPipeline().evaluate(
            snapshot,
            active_module_ids=active_module_ids(StrategyCollection.CONTEXT),
            shadow_module_ids=shadow_module_ids(StrategyCollection.CONTEXT),
        )

        self.assertEqual([vote.features["strategyId"] for vote in result.active], ["relative_strength_qqq_iwm", "market_breadth_momentum"])
        economic = next(vote for vote in result.shadow if vote.features["strategyId"] == "economic_event_context")
        self.assertFalse(economic.active)
        self.assertEqual(economic.features["contextEffect"], "entry_block")
        self.assertTrue(economic.features["entryBlackout"])
        persisted = shadow_context_outputs()
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["snapshotHash"], snapshot.snapshotHash)
        self.assertEqual(len(persisted[0]["outputs"]), len(result.shadow))

    def test_market_breadth_uses_component_proxy_without_external_feed(self) -> None:
        payload = snapshot_payload(candles(30))
        payload.pop("external_breadth_feed", None)
        snapshot = build_live_paper_snapshot(payload)

        result = VotingEnsembleContextPipeline().evaluate(
            snapshot,
            active_module_ids=active_module_ids(StrategyCollection.CONTEXT),
            shadow_module_ids=(),
        )

        breadth = next(vote for vote in result.active if vote.features["strategyId"] == "market_breadth_momentum")
        self.assertTrue(breadth.dataReady)
        self.assertNotIn("missing_inputs", breadth.features["reasonCode"])
        self.assertEqual(breadth.features["breadthSource"], "ETF breadth proxy")
        self.assertGreater(float(breadth.features["breadthCoverage"]), 0)

    def test_context_cannot_create_candidate_without_directional_evidence(self) -> None:
        context = context_signal("relative_strength_qqq_iwm", "confirm_long", confidence=1.0, max_adjustment=0.08)

        decision = engine().aggregate(
            strategySignals=[],
            contextSignals=[context],
            regimeState=None,
            safetyDecision=None,
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )

        self.assertEqual(decision.signal, Signal.HOLD.value)
        self.assertEqual(decision.rawScore, 0.0)
        self.assertEqual(decision.finalScore, 0.0)
        self.assertEqual(decision.contextAdjustments[0]["adjustment"], 0.0)

    def test_context_adjustments_are_bounded_and_cannot_reverse_direction(self) -> None:
        decision = engine(max_context_adjustment=0.05).aggregate(
            strategySignals=[strategy_signal("multi_timeframe_trend_alignment", StrategyFamily.TREND, Signal.BUY, confidence=0.8)],
            contextSignals=[context_signal("market_breadth_momentum", "conflict", confidence=1.0, max_adjustment=0.50)],
            regimeState=None,
            safetyDecision=None,
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
        )

        self.assertEqual(decision.signal, Signal.BUY.value)
        self.assertEqual(decision.contextAdjustments[0]["boundedBy"], 0.05)
        self.assertEqual(decision.contextAdjustments[0]["adjustment"], -0.05)
        self.assertAlmostEqual(decision.finalScore, 0.75, places=4)

    def test_service_status_reports_context_pipeline_shadow_inventory(self) -> None:
        status = VotingEnsembleService().status()

        self.assertEqual(status["contextSignals"], ["relative_strength_qqq_iwm", "market_breadth_momentum"])
        self.assertEqual(
            status["shadowContextSignals"],
            [
                "economic_event_context",
                "market_structure_context",
                "volume_confirmation_context",
                "vwap_position_context",
                "market_forecast_context",
            ],
        )
        self.assertTrue(status["inventoryStatus"]["valid"])


def engine(max_context_adjustment: float = 0.08) -> FamilyAwareDeterministicEnsemble:
    return FamilyAwareDeterministicEnsemble(
        FamilyAwareEnsembleConfig(
            minimumEligibleDirectionalStrategies=1,
            minimumIndependentSupportingFamilies=1,
            maximumContextConflict=1.0,
            maxContextAdjustmentPerSignal=max_context_adjustment,
        )
    )


def strategy_signal(strategy_id: str, family: StrategyFamily, signal: Signal, *, confidence: float) -> StrategySignal:
    return StrategySignal(
        strategyId=strategy_id,
        strategyName=strategy_id.replace("_", " ").title(),
        strategyVersion="test_v1",
        family=family,
        role=StrategyRole.DIRECTIONAL,
        signal=signal,
        direction=Direction.LONG if signal == Signal.BUY else Direction.SHORT if signal == Signal.SELL else Direction.FLAT,
        confidence=confidence,
        active=True,
        eligible=signal != Signal.HOLD,
        dataReady=True,
        setupDetected=signal != Signal.HOLD,
        regimeFit=1.0,
        reliability=1.0,
        reasonCodes=[f"test.{strategy_id}"],
        explanation="test signal",
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def context_signal(context_id: str, effect: str, *, confidence: float, max_adjustment: float) -> ContextSignal:
    return ContextSignal(
        contextId=context_id,
        signal=Signal.HOLD,
        direction=Direction.FLAT,
        confidence=confidence,
        dataReady=True,
        explanation="test context",
        features={"contextEffect": effect, "maxConfidenceAdjustment": max_adjustment},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


if __name__ == "__main__":
    unittest.main()
