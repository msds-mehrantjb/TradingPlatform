from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time, timedelta

from backend.app.backtesting import EventDrivenReplayEngine, ReplayComponents, ReplayEngineConfig
from backend.app.backtesting.event_replay import ReplaySessionRules, ReplaySessionState, apply_session_rules
from backend.app.domain.feature_engine import MarketCandle, PriorDayOHLC
from backend.app.domain.models import (
    Direction,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    Signal,
    StrategyFamily,
)
from backend.app.ensemble import FamilyAwareDeterministicEnsemble, FamilyAwareEnsembleConfig
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceConfig
from backend.app.strategies.base import StrategyEvaluationContext, hold_signal, strategy_signal
from backend.app.strategies.registry import directional_strategy_input_ids, resolve_strategy


SESSION_DATE = date(2026, 1, 5)
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class EventDrivenReplayEngineTest(unittest.TestCase):
    def test_live_style_and_replay_style_decisions_match_for_same_prefix(self) -> None:
        candles = candles_for_session(32)
        engine = replay_engine()
        prefix = candles[:20]

        live_style = engine.decide_at(
            symbol="SPY",
            sessionDate=SESSION_DATE,
            evaluationTimestamp=prefix[-1].timestamp,
            spy1mCandles=prefix,
            spy5mCandles=prefix,
            spy15mCandles=prefix,
            qqqCandles=prefix,
            iwmCandles=prefix,
            breadthComponents={"XLK": prefix},
            priorDayOHLC=prior_day(),
        )
        replay = engine.replay_session(
            symbol="SPY",
            sessionDate=SESSION_DATE,
            spy1mCandles=candles,
            spy5mCandles=candles,
            spy15mCandles=candles,
            qqqCandles=candles,
            iwmCandles=candles,
            breadthComponents={"XLK": candles},
            priorDayOHLC=prior_day(),
        )

        first_replay = replay.snapshots[0]
        self.assertEqual(first_replay.decisionTimestampUtc, live_style.decisionTimestampUtc)
        self.assertEqual(first_replay.ensembleDecision["signal"], live_style.ensembleDecision["signal"])
        self.assertEqual(first_replay.ensembleDecision["finalScore"], live_style.ensembleDecision["finalScore"])
        self.assertEqual(first_replay.maxInputTimestampUtc, first_replay.decisionTimestampUtc)

    def test_engine_replays_one_session_deterministically(self) -> None:
        candles = candles_for_session(34)
        engine = replay_engine()
        first = replay(engine, candles)
        second = replay(engine, candles)

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertGreater(first.decisionCount, 0)
        self.assertTrue(all("replay.point_in_time_prefix" in snapshot.reasonCodes for snapshot in first.snapshots))

    def test_replay_snapshots_record_the_shared_global_gate_engine(self) -> None:
        result = replay(replay_engine(), candles_for_session(24))

        first = result.snapshots[0]
        gate_ids = {gate["gateId"] for gate in first.gateDecision["gateResults"]}
        self.assertIn("operational.trading_enabled", gate_ids)
        self.assertIn("data.fresh_candle", gate_ids)
        self.assertIn("order.integrity_passed", gate_ids)
        self.assertNotEqual(first.gateDecision["configurationHash"], "test-gates")

    def test_future_candle_is_not_passed_into_decision_code(self) -> None:
        candles = candles_for_session(24)
        engine = replay_engine()
        evaluation = candles[19].timestamp

        with self.assertRaisesRegex(ValueError, "future candles"):
            engine.decide_at(
                symbol="SPY",
                sessionDate=SESSION_DATE,
                evaluationTimestamp=evaluation,
                spy1mCandles=candles[:21],
                spy5mCandles=candles[:20],
                spy15mCandles=candles[:20],
                qqqCandles=candles[:20],
                iwmCandles=candles[:20],
                breadthComponents={"XLK": candles[:20]},
                priorDayOHLC=prior_day(),
            )

    def test_every_trade_links_to_exact_decision_snapshot_and_fills_after_decision(self) -> None:
        candles = candles_for_session(36)
        result = replay(replay_engine(), candles)

        snapshot_by_id = {snapshot.snapshotId: snapshot for snapshot in result.snapshots}
        self.assertGreater(len(result.trades), 0)
        for trade in result.trades:
            self.assertIn(trade.decisionSnapshotId, snapshot_by_id)
            snapshot = snapshot_by_id[trade.decisionSnapshotId]
            self.assertGreater(trade.filledAt, snapshot.decisionTimestampUtc)
            self.assertEqual(snapshot.fill["filledAt"], trade.filledAt.isoformat().replace("+00:00", "Z"))

    def test_new_entry_cutoff_blocks_new_trades_after_cutoff(self) -> None:
        rules = ReplaySessionRules(
            newEntryCutoffTimeUtc=time(14, 50),
            maxEntriesPerSetup=100,
            maxConcurrentPositions=10,
            maxSymbolExposure=10,
            pyramidingAllowed=True,
        )
        result = replay(replay_engine(session_rules=rules), candles_for_session(28))

        after_cutoff = [snapshot for snapshot in result.snapshots if snapshot.decisionTimestampUtc.time() >= time(14, 50)]
        self.assertGreater(len(after_cutoff), 0)
        self.assertTrue(all(snapshot.orderPlan["orderType"] == "NO_ORDER" for snapshot in after_cutoff))
        self.assertTrue(any("session.new_entry_cutoff" in snapshot.orderPlan["validationErrors"] for snapshot in after_cutoff))
        self.assertTrue(all(trade.submittedAt.time() < time(14, 50) for trade in result.trades))

    def test_end_of_day_liquidation_occurs_at_configured_time(self) -> None:
        rules = ReplaySessionRules(
            newEntryCutoffTimeUtc=time(14, 55),
            endOfDayLiquidationTimeUtc=time(14, 52),
            maxEntriesPerSetup=100,
            maxConcurrentPositions=10,
            maxSymbolExposure=10,
            pyramidingAllowed=True,
        )
        engine = replay_engine(session_rules=rules, target_distance=50.0, stop_distance=50.0)

        result = replay(engine, candles_for_session(30))

        self.assertGreater(len(result.trades), 0)
        self.assertEqual(result.trades[0].exitAt.time(), time(14, 52))
        self.assertIn("execution.end_of_day_exit", result.trades[0].reasonCodes)

    def test_global_spy_exposure_blocks_independent_new_entries(self) -> None:
        rules = ReplaySessionRules(
            newEntryCutoffTimeUtc=time(15, 10),
            endOfDayLiquidationTimeUtc=time(15, 10),
            maxEntriesPerSetup=100,
            maxConcurrentPositions=10,
            maxSymbolExposure=1,
            pyramidingAllowed=True,
        )
        engine = replay_engine(session_rules=rules, target_distance=50.0, stop_distance=50.0)

        result = replay(engine, candles_for_session(36))

        blocked = [
            snapshot
            for snapshot in result.snapshots
            if "gate.risk.duplicate_spy_exposure" in snapshot.gateDecision["reasonCodes"]
            or (snapshot.orderPlan and "session.global_symbol_exposure_limit" in snapshot.orderPlan["validationErrors"])
        ]
        self.assertGreater(len(blocked), 0)
        self.assertEqual(len([trade for trade in result.trades if trade.symbol == "SPY"]), 1)

    def test_duplicate_order_prevention_blocks_same_symbol_side_timestamp(self) -> None:
        engine = replay_engine()
        decision = engine.decide_at(
            symbol="SPY",
            sessionDate=SESSION_DATE,
            evaluationTimestamp=candles_for_session(20)[-1].timestamp,
            spy1mCandles=candles_for_session(20),
            spy5mCandles=candles_for_session(20),
            spy15mCandles=candles_for_session(20),
            qqqCandles=candles_for_session(20),
            iwmCandles=candles_for_session(20),
            breadthComponents={"XLK": candles_for_session(20)},
            priorDayOHLC=prior_day(),
        )
        order = decision.orderPlan
        state = ReplaySessionState()
        accepted, _ = apply_session_rules(order_plan_from_payload(order), decision, state, ReplaySessionRules(maxEntriesPerSetup=100))
        duplicate, reasons = apply_session_rules(order_plan_from_payload(order), decision, state, ReplaySessionRules(maxEntriesPerSetup=100))

        self.assertEqual(accepted.orderType, "LIMIT")
        self.assertEqual(duplicate.orderType, "NO_ORDER")
        self.assertIn("session.duplicate_order_prevented", reasons)


class FakeDirectionalStrategy:
    def __init__(self, strategy_id: str) -> None:
        self.registryEntry = resolve_strategy(strategy_id)

    def evaluate(self, context: StrategyEvaluationContext):
        if self.registryEntry.family in {StrategyFamily.TREND.value, StrategyFamily.BREAKOUT.value}:
            return strategy_signal(
                context,
                signal=Signal.BUY,
                confidence=0.8,
                eligible=True,
                setupDetected=True,
                regimeFit=1.0,
                reliability=1.0,
                reasonCodes=["test.buy_setup"],
                explanation="Synthetic replay setup.",
                featureNames=[],
            )
        return hold_signal(
            context,
            confidence=0.7,
            setupDetected=False,
            regimeFit=1.0,
            reliability=1.0,
            reasonCodes=["test.no_setup"],
            explanation="Synthetic replay hold.",
            featureNames=[],
        )


class PassingSafetyModule:
    registryEntry = resolve_strategy("cash_avoid_trading_filter")

    def evaluate(self, context):
        gate = GateResult(
            gateId="cash_avoid_trading_filter",
            gateName="Cash / Avoid Trading Filter",
            status=GateStatus.PASS,
            blocksTrading=False,
            reasonCodes=["test.gates_passed"],
            explanation="Synthetic replay gate pass.",
            checkedAt=context.checkedAt,
            configurationHash="test-gates",
        )
        return GlobalGateDecision(
            status=GateStatus.PASS,
            eligible=True,
            dataReady=True,
            gateResults=[gate],
            reasonCodes=["test.gates_passed"],
            explanation="Synthetic replay gates passed.",
            checkedAt=context.checkedAt,
            sessionDate=context.sessionDate,
            configurationHash="test-gates",
        )


def replay_engine(
    *,
    session_rules: ReplaySessionRules | None = None,
    target_distance: float = 0.20,
    stop_distance: float = 0.20,
) -> EventDrivenReplayEngine:
    strategies = tuple(FakeDirectionalStrategy(strategy_id) for strategy_id in directional_strategy_input_ids())
    return EventDrivenReplayEngine(
        ReplayComponents(
            directionalStrategies=strategies,
            familyEnsemble=FamilyAwareDeterministicEnsemble(
                FamilyAwareEnsembleConfig(
                    minimumEligibleDirectionalStrategies=2,
                    minimumIndependentSupportingFamilies=2,
                    minimumFinalScore=0.2,
                )
            ),
            safetyModule=PassingSafetyModule(),
            mlConfig=SafeMLInferenceConfig(mode="OFF"),
        ),
        ReplayEngineConfig(
            minWarmupCandles=20,
            defaultTargetDistance=target_distance,
            defaultStopDistance=stop_distance,
            sessionRules=session_rules or ReplaySessionRules(maxEntriesPerSetup=100),
            configurationHash="test-replay-config",
        ),
    )


def replay(engine: EventDrivenReplayEngine, candles: list[MarketCandle]):
    return engine.replay_session(
        symbol="SPY",
        sessionDate=SESSION_DATE,
        spy1mCandles=candles,
        spy5mCandles=candles,
        spy15mCandles=candles,
        qqqCandles=candles,
        iwmCandles=candles,
        breadthComponents={"XLK": candles},
        priorDayOHLC=prior_day(),
    )


def candles_for_session(count: int) -> list[MarketCandle]:
    candles = []
    price = 100.0
    for index in range(count):
        timestamp = START + timedelta(minutes=index)
        open_price = price
        close_price = price + 0.08
        candles.append(
            MarketCandle(
                timestamp=timestamp,
                open=open_price,
                high=close_price + 0.15,
                low=open_price - 0.05,
                close=close_price,
                volume=1000 + index,
                tradeCount=100 + index,
                symbol="SPY",
                timeframe="1Min",
            )
        )
        price = close_price
    return candles


def prior_day() -> PriorDayOHLC:
    return PriorDayOHLC(
        sessionDate=date(2026, 1, 2),
        open=99.0,
        high=101.0,
        low=98.5,
        close=99.5,
    )


def order_plan_from_payload(payload: dict):
    from backend.app.domain.models import OrderPlan

    return OrderPlan.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
