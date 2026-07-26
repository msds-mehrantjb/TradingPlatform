from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluateRequest, WcaLegacyStrategySignal, WcaMarketSnapshot, WcaQuote, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.execution_pipeline import (
    WCA_PRODUCTION_PIPELINE_VERSION,
    WcaExecutionPipelineInput,
    run_wca_backtest_pipeline_adapter,
    run_wca_paper_pipeline_adapter,
    run_wca_replay_pipeline_adapter,
)
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.service import WcaService
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


UTC = timezone.utc


class WcaStep5ProductionPipelineTest(unittest.TestCase):
    def test_paper_replay_and_backtest_adapters_produce_identical_pre_execution_hashes(self) -> None:
        configuration = default_wca_configuration()
        snapshot = market_snapshot()
        weight_snapshot = baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="test.weights.v1")
        command = WcaExecutionPipelineInput(
            run_id="same-run",
            decision_id="same-decision",
            order_intent_id="same-intent",
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=weight_snapshot,
            global_gate_quantity_cap=1000,
            approved_risk_budget=1000,
        )

        paper = run_wca_paper_pipeline_adapter(command).decision
        replay = run_wca_replay_pipeline_adapter(command).decision
        backtest = run_wca_backtest_pipeline_adapter(command).decision

        self.assertEqual(paper.decision_hash, replay.decision_hash)
        self.assertEqual(paper.decision_hash, backtest.decision_hash)
        self.assertEqual(paper.model_copy(update={"runtime_mode": "pre_execution", "decision_hash": ""}).deterministic_hash(), paper.decision_hash)
        self.assertEqual(paper.called_module_versions["production_pipeline"], WCA_PRODUCTION_PIPELINE_VERSION)
        self.assertIn("configuration", paper.called_module_versions)
        self.assertIn("weights", paper.called_module_versions)
        self.assertIn("modifier.vwap_position", paper.called_module_versions)
        self.assertTrue(any(key.startswith("strategy.") for key in paper.called_module_versions))

    def test_missing_nbbo_blocks_paper_entries_without_synthetic_quote(self) -> None:
        configuration = default_wca_configuration()
        snapshot = market_snapshot(quote=None)
        result = run_wca_paper_pipeline_adapter(
            WcaExecutionPipelineInput(
                run_id="missing-quote",
                decision_id="missing-quote-decision",
                order_intent_id="missing-quote-intent",
                snapshot=snapshot,
                configuration_version=configuration.configuration_version,
                configuration=configuration,
                weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
            ),
            voters=fake_voters(WcaSide.BUY),
        ).decision

        self.assertIsNone(result.proposed_order)
        self.assertEqual(result.sizing.final_quantity, 0)
        self.assertTrue(any(gate.gate_id == "unsafe_spread" and gate.blocks_entry for gate in result.hard_filter_results))
        self.assertIn("wca.hard_filter.unsafe_spread.missing_quote", result.reason_codes + result.sizing.reason_codes + tuple(code for gate in result.hard_filter_results for code in gate.reason_codes))

    def test_global_risk_cap_is_applied_before_final_validation_and_never_increases_quantity(self) -> None:
        configuration = default_wca_configuration()
        snapshot = market_snapshot()
        base_command = WcaExecutionPipelineInput(
            run_id="global-risk",
            decision_id="global-risk-decision",
            order_intent_id="global-risk-intent",
            snapshot=snapshot,
            configuration_version=configuration.configuration_version,
            configuration=configuration,
            weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp),
            global_gate_quantity_cap=0,
            approved_risk_budget=1000,
        )

        result = run_wca_paper_pipeline_adapter(base_command, voters=fake_voters(WcaSide.BUY)).decision

        self.assertIsNotNone(result.global_gate_result)
        self.assertLessEqual(result.global_gate_result.allowed_quantity, result.global_gate_result.proposed_quantity)
        if result.proposed_order is not None:
            self.assertEqual(result.proposed_order.quantity, result.sizing.final_quantity)
            self.assertLessEqual(result.proposed_order.quantity, 1)
        self.assertTrue({"wca.global_risk.reduced_or_rejected", "wca.global_risk.approved"} & set(result.reason_codes))

    def test_legacy_request_ignores_externally_supplied_votes_as_authority(self) -> None:
        db_path = temp_db_path()
        repository = WcaSqliteRepository(f"sqlite:///{db_path}")
        service = WcaService(repository=repository)
        request = WcaEvaluateRequest(
            snapshotId="legacy-votes",
            symbol="SPY",
            timestamp=timestamp(),
            marketSnapshot={"close": 100, "bid": 99.99, "ask": 100.01, "latestVolume": 100000},
            strategySignals=(
                WcaLegacyStrategySignal(
                    key="external",
                    strategy="external",
                    name="External Vote",
                    family="external",
                    signal="Sell",
                    confidence=1.0,
                    baseWeight=1.0,
                    effectiveWeight=1.0,
                    direction=-1,
                ),
            ),
        )

        response = service.evaluate(request)

        self.assertIn("wca.legacy_external_strategy_signals_ignored", response.reason_codes)
        self.assertTrue(response.decision)
        self.assertNotIn("external", {row.strategy_id for row in response.decision.aggregation.strategy_evaluations})
        self.assertEqual(response.engine_version, WCA_PRODUCTION_PIPELINE_VERSION)


class FakeVoter:
    def __init__(self, strategy_id: str, side: WcaSide) -> None:
        self.strategy_id = strategy_id
        self.slug = strategy_id.lower()
        self.name = strategy_id
        self.family = strategy_id
        self.version = f"{strategy_id}.test.v1"
        self.base_weight = 1 / 3
        self.side = side

    def evaluate(self, snapshot: WcaMarketSnapshot) -> WcaStrategyEvaluation:
        direction = 1 if self.side == WcaSide.BUY else -1
        return WcaStrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            name=self.name,
            status="ACTIVE",
            signal=self.side,
            confidence=0.9,
            raw_confidence=0.9,
            calibrated_confidence=0.9,
            direction=self.side,
            applicability="ACTIVE",
            evidence_strength=0.9,
            data_quality_status="ACTIVE",
            base_weight=self.base_weight,
            effective_weight=self.base_weight,
            contribution=direction * self.base_weight * 0.9,
            reason_codes=(f"test.{self.strategy_id}",),
        )


def fake_voters(side: WcaSide):
    return (FakeVoter("C1", side), FakeVoter("C7", side), FakeVoter("C8", side))


def market_snapshot(*, quote: WcaQuote | None | bool = True) -> WcaMarketSnapshot:
    candles = candles_series(70)
    latest = candles[-1]
    if quote is True:
        quote_value = WcaQuote(timestamp=latest.timestamp, bid=latest.close - 0.01, ask=latest.close + 0.01)
    else:
        quote_value = quote
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=tuple(candles),
        quote=quote_value,
        data_ready=True,
        source="test",
        reason_codes=("test.completed_bar",),
    )


def candles_series(count: int) -> list[WcaCandle]:
    start = timestamp() - timedelta(minutes=count - 1)
    rows = []
    for index in range(count):
        close = 100 + index * 0.03
        rows.append(WcaCandle(timestamp=start + timedelta(minutes=index), open=close - 0.02, high=close + 0.08, low=close - 0.08, close=close, volume=150000, vwap=close))
    return rows


def timestamp() -> datetime:
    return datetime(2026, 1, 6, 17, 0, tzinfo=UTC)


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step5-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
