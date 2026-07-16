from __future__ import annotations

import json
import unittest
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.exit_policy import WeightedVotingExitInputs, evaluate_exit_lifecycle, open_exit_lifecycle
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedMarketQuality, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.algorithms.weighted_voting.weight_engine import apply_weight_controls
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 7, 14)
STRATEGY_FAMILIES = {
    "S1": WeightedStrategyFamily.BREAKOUT,
    "S8": WeightedStrategyFamily.BREAKOUT,
    "S2": WeightedStrategyFamily.TREND,
    "S3": WeightedStrategyFamily.TREND,
    "S4": WeightedStrategyFamily.MEAN_REVERSION,
    "S7": WeightedStrategyFamily.MEAN_REVERSION,
    "S5": WeightedStrategyFamily.REVERSAL,
    "S6": WeightedStrategyFamily.REVERSAL,
}


class WeightedVotingStep32ComprehensiveTest(unittest.TestCase):
    def test_weight_property_invariants_hold_across_concentrated_inputs(self) -> None:
        cases = (
            {strategy_id: 0.125 for strategy_id in STRATEGY_FAMILIES},
            {"S1": 0.80, "S8": 0.05, "S2": 0.03, "S3": 0.03, "S4": 0.03, "S7": 0.02, "S5": 0.02, "S6": 0.02},
            {"S4": 0.45, "S7": 0.35, "S1": 0.05, "S8": 0.05, "S2": 0.03, "S3": 0.03, "S5": 0.02, "S6": 0.02},
            {"S5": 0.60, "S6": 0.30, "S1": 0.02, "S8": 0.02, "S2": 0.02, "S3": 0.02, "S4": 0.01, "S7": 0.01},
        )

        for weights in cases:
            with self.subTest(weights=weights):
                result = apply_weight_controls(strategy_signals(weights))
                final_weights = {signal.strategy_id: signal.final_weight for signal in result.signals}
                family_totals = defaultdict(float)
                for signal in result.signals:
                    family_totals[signal.family] += signal.final_weight

                self.assertTrue(all(weight >= 0.0 for weight in final_weights.values()))
                self.assertAlmostEqual(sum(final_weights.values()), 1.0, delta=0.0000001)
                self.assertTrue(all(weight <= 0.25 + 0.0000001 for weight in final_weights.values()))
                self.assertTrue(all(weight <= 0.40 + 0.0000001 for weight in family_totals.values()))
                self.assertAlmostEqual(sum(adjustment.final_effective_weight for adjustment in result.adjustments), 1.0, delta=0.0000001)

    def test_global_gate_property_never_changes_buy_or_sell_side(self) -> None:
        actions = ("ALLOW", "REDUCE_QUANTITY", "REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE")
        for side in ("BUY", "SELL"):
            for action in actions:
                with self.subTest(side=side, action=action):
                    proposal = global_order_proposal(side=side, quantity=12)
                    response = GlobalGateResponse(
                        action=action,
                        maximumAllowedQuantity=6 if action in {"ALLOW", "REDUCE_QUANTITY"} else 0,
                        maximumAdditionalRiskDollars=60.0 if action in {"ALLOW", "REDUCE_QUANTITY"} else 0.0,
                        rejectionReasons=(f"global.step32.{action.lower()}",),
                        emergencyAction="liquidate_owned_risk_reducing_positions" if action == "EMERGENCY_LIQUIDATE" else None,
                        evaluatedAt=CREATED_AT,
                        configurationHash=f"global-step32-{action.lower()}",
                    )

                    applied = apply_global_gate_response(proposal, response)

                    self.assertEqual(applied.side, side)
                    self.assertEqual(proposal.side, side)
                    self.assertLessEqual(applied.globallyAllowedQuantity, proposal.quantity)
                    self.assertIn("global_gate.side_immutable", applied.immutableChecks)
                    self.assertEqual(proposal.entryFormula["kind"], "limit")
                    self.assertEqual(proposal.stopFormula["kind"], "structural_atr")
                    self.assertEqual(proposal.targetFormula["kind"], "r_multiple")

    def test_exit_property_stop_risk_never_increases_after_entry(self) -> None:
        settings = effective_settings()
        cases = (
            (WeightedSide.BUY, 100.0, 99.0, 102.2),
            (WeightedSide.SELL, 100.0, 101.0, 97.8),
        )
        for side, entry, stop, favorable_price in cases:
            with self.subTest(side=side.value):
                lifecycle = open_exit_lifecycle(
                    trade_id=f"step32-{side.value}",
                    symbol="SPY",
                    side=side,
                    quantity=20,
                    entry_price=entry,
                    entry_timestamp=SESSION_OPEN,
                    stop_price=stop,
                    effective_settings=settings,
                )

                decision = evaluate_exit_lifecycle(
                    WeightedVotingExitInputs(
                        lifecycle=lifecycle,
                        current_price=favorable_price,
                        current_timestamp=SESSION_OPEN + timedelta(minutes=20),
                        current_condition_quality=WeightedMarketQuality.CLEAN,
                    )
                )

                self.assertLessEqual(decision.risk_per_share, lifecycle.original_risk_per_share + 0.0000001)
                if side == WeightedSide.BUY:
                    self.assertGreaterEqual(decision.stop_price, lifecycle.protective_stop)
                else:
                    self.assertLessEqual(decision.stop_price, lifecycle.protective_stop)

    def test_backend_evaluation_ignores_ml_other_algorithm_and_scheduler_failures(self) -> None:
        baseline_payload = evaluate_payload()
        noisy_payload = {
            **evaluate_payload(),
            "votingEnsemble": {"winner": "SELL", "settings": {"minimumVotes": 99}, "schedulerStatus": "failed"},
            "confidenceAggregation": {"signal": "SELL", "serviceStatus": "failed"},
            "regimeSelection": {"marketState": "panic", "serviceStatus": "failed"},
            "metaStrategy": {"decision": "SELL", "serviceStatus": "failed"},
            "mlComparison": {"status": "unavailable"},
            "futurePricePrediction": {"status": "failed"},
            "tradingRagReadiness": {"status": "failed"},
            "otherAlgorithmScheduler": {"status": "failed"},
        }
        service = WeightedVotingService(store=MemoryStore())

        baseline = service.evaluate(baseline_payload)
        noisy = service.evaluate(noisy_payload)

        self.assertEqual(stable_evaluation_surface(noisy), stable_evaluation_surface(baseline))
        self.assertTrue(all(key.startswith("weighted_voting.") for key in service.store.snapshots))

    def test_golden_replay_fixed_dataset_produces_fixed_decision_and_trades(self) -> None:
        decision_result = WeightedVotingService(store=MemoryStore()).evaluate(evaluate_payload())
        scores = decision_result["decision"]["vote_scores"]

        self.assertEqual(decision_result["decision"]["signal"], "Buy")
        self.assertTrue(decision_result["decision"]["eligible"])
        self.assertEqual(scores["buy_score"], 0.72)
        self.assertEqual(scores["sell_score"], 0.0784)
        self.assertEqual(scores["hold_score"], 0.2016)
        self.assertEqual(scores["winner_edge"], 0.5184)
        self.assertEqual(decision_result["sizingResult"]["quantity"], 0)
        self.assertEqual(decision_result["sizingResult"]["limiting_cap"], "local_gates")

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=golden_signals):
            replay = run_weighted_voting_backtest(
                candles=golden_candles(),
                config=WeightedBacktestEngineConfig(symbol="SPY", run_id="golden-step32"),
                created_at=CREATED_AT,
            )

        self.assertEqual(replay.run.run_id, "golden-step32")
        self.assertEqual(len(replay.decisions), 10)
        self.assertEqual(len(replay.trades), 7)
        self.assertEqual(replay.algorithm_results.net_pnl, 1090.98812948)
        self.assertEqual(replay.algorithm_results.profit_factor, 4.0)
        self.assertEqual(replay.trades[0].entry_timestamp, SESSION_OPEN + timedelta(minutes=5))
        self.assertGreater(replay.trades[0].entry_timestamp, replay.decisions[0].data_timestamp)
        self.assertEqual(replay.trades[0].exit_reason, "target_hit")
        self.assertIn("weighted_voting.backtest.no_lookahead_next_candle_entry", replay.reason_codes)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def strategy_signals(weights: dict[str, float]) -> list[WeightedVotingSignal]:
    return [
        WeightedVotingSignal(
            strategy_id=strategy_id,
            strategy_name=f"{strategy_id} Step 32 property signal",
            strategy_version="weighted_strategy_step32_test_v1",
            family=family,
            signal=WeightedSide.BUY,
            p_buy=0.64,
            p_sell=0.10,
            p_hold=0.26,
            directional_confidence=0.64,
            signal_strength=0.64,
            expected_raw_movement=0.001,
            expected_return=0.001,
            expected_return_after_costs=0.0008,
            strength=0.64,
            final_weight=weights[strategy_id],
            eligible=True,
            data_ready=True,
            required_data_freshness_seconds=300,
            actual_data_freshness_seconds=0,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=SESSION_OPEN + timedelta(minutes=60),
            explanation="Synthetic Step 32 signal for weight property testing.",
        )
        for strategy_id, family in STRATEGY_FAMILIES.items()
    ]


def global_order_proposal(*, side: str, quantity: int) -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId=f"step32-{side.lower()}",
        orderIntentId=f"step32-{side.lower()}.order",
        intent="new_entry",
        symbol="SPY",
        side=side,
        quantity=quantity,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=99.0 if side == "BUY" else 101.0,
        targetPrice=102.0 if side == "BUY" else 98.0,
        plannedRiskDollars=120.0,
        settingsSnapshot={"settings_version": "step32"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural_atr"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="step32-strategy-state",
        proposedAt=CREATED_AT,
        sessionDate=SESSION_DATE,
        configurationHash="step32-proposal",
    )


def effective_settings():
    defaults = default_weighted_settings(timestamp=CREATED_AT)
    envelope = default_dynamic_envelope(timestamp=CREATED_AT)
    limits = default_hard_limits(timestamp=CREATED_AT)
    return resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=CREATED_AT)


def evaluate_payload() -> dict:
    rows = candle_rows(count=95)
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "account_equity": 100000,
        "available_buying_power": 100000,
        "capital_available": 100000,
    }


def candle_rows(count: int = 390) -> list[dict]:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return rows


def golden_candles() -> tuple[WeightedVotingCandle, ...]:
    return tuple(
        WeightedVotingCandle(
            timestamp=SESSION_OPEN + timedelta(minutes=index),
            open=100.0 + index * 0.03,
            high=100.0 + index * 0.03 + 0.45,
            low=100.0 + index * 0.03 - 0.18,
            close=100.0 + index * 0.03 + 0.08,
            volume=200000 if index != 5 else 5000,
        )
        for index in range(390)
    )


def golden_signals(snapshot, _config=None) -> tuple[WeightedVotingSignal, ...]:
    signals = []
    for strategy_id, family in STRATEGY_FAMILIES.items():
        confidence = 0.86 if strategy_id in {"S2", "S3"} else 0.72
        signals.append(
            WeightedVotingSignal(
                strategy_id=strategy_id,
                strategy_name=f"{strategy_id} Step 32 golden signal",
                strategy_version="weighted_strategy_step32_golden_v1",
                family=family,
                signal=WeightedSide.BUY,
                p_buy=confidence,
                p_sell=0.05,
                p_hold=round(1.0 - confidence - 0.05, 6),
                directional_confidence=confidence,
                signal_strength=confidence,
                expected_raw_movement=0.02,
                expected_return=0.02,
                expected_return_after_costs=0.018,
                strength=confidence,
                final_weight=0.125,
                eligible=True,
                data_ready=True,
                required_data_freshness_seconds=300,
                actual_data_freshness_seconds=0,
                data_quality_status=WeightedDataQualityStatus.FULL,
                invalidation_level=snapshot.one_minute_candles[-1].low - 0.20,
                data_timestamp=snapshot.data_timestamp,
                reason_codes=("weighted_voting.step32.golden_signal",),
                explanation="Synthetic full-quality signal used for deterministic Step 32 golden replay.",
            )
        )
    return tuple(signals)


def stable_evaluation_surface(result: dict) -> str:
    stable = {
        "decision": result["decision"],
        "signals": result["signals"],
        "gateResult": result["gateResult"],
        "sizingResult": result["sizingResult"],
        "globalOrderProposal": result["globalOrderProposal"],
        "globalGateResponse": result["globalGateResponse"],
        "globalGateApplication": result["globalGateApplication"],
        "reasonCodes": result["reasonCodes"],
    }
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    unittest.main()
