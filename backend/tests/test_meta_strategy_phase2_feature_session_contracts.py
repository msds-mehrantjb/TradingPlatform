from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    ALL_META_STRATEGY_STRATEGIES,
    META_STRATEGY_STARTUP_FEATURE_CONTRACT_VALIDATION,
    SAFETY_STRATEGIES,
    MetaStrategyAccountSnapshot,
    MetaStrategyEconomicEventSnapshot,
    MetaStrategyEvaluationContext,
    MetaStrategyGlobalRiskSnapshot,
    MetaStrategyOperationalHealthSnapshot,
    MetaStrategySession,
    build_meta_strategy_market_snapshot,
    canonical_session,
    meta_strategy_session_at,
    validate_required_input_producers,
)
from backend.tests.test_meta_strategy_step7_market_snapshot import DECISION_TIMESTAMP, request_with


class MetaStrategyPhase2FeatureSessionContractsTest(unittest.TestCase):
    maxDiff = None

    def test_evaluation_context_separates_market_account_risk_ops_and_settings(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())
        context = context_for(snapshot)

        self.assertEqual(context.market_snapshot, snapshot)
        self.assertEqual(context.account_snapshot.cash_available, 25_000.0)
        self.assertEqual(context.global_risk_snapshot.available_risk_dollars, 1_000.0)
        self.assertEqual(context.operational_health_snapshot.status, "OK")
        self.assertEqual(context.economic_event_snapshot.state, "none")
        self.assertNotIn("cashAvailable", snapshot.features)
        self.assertNotIn("avoidTrading", snapshot.features)
        self.assertNotIn("operationalHealth", snapshot.features)
        with self.assertRaises(ValidationError):
            MetaStrategyEvaluationContext(**{**context.model_dump(mode="python"), "evaluation_timestamp": snapshot.timestamp - timedelta(seconds=1)})

    def test_every_active_strategy_and_gate_has_required_input_producers(self) -> None:
        validation = validate_required_input_producers(ALL_META_STRATEGY_STRATEGIES)

        self.assertTrue(validation["valid"])
        self.assertTrue(META_STRATEGY_STARTUP_FEATURE_CONTRACT_VALIDATION["valid"])
        self.assertEqual(validation["missingProducers"], ())

    def test_every_active_strategy_receives_required_inputs_from_runtime_snapshot(self) -> None:
        context = context_for(build_meta_strategy_market_snapshot(request_with()))

        for entry in ALL_META_STRATEGY_STRATEGIES:
            strategy = strategy_for(entry)
            result = strategy.evaluate(context)
            with self.subTest(strategy=entry.strategy_id):
                self.assertEqual(set(result.required_input_status), set(entry.required_inputs))
                self.assertTrue(all(result.required_input_status.values()), result.required_input_status)

    def test_missing_critical_evidence_fails_closed_with_precise_reason(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with()).model_copy(update={"vwap": None})
        entry = next(item for item in SAFETY_STRATEGIES if item.strategy_id == "missing_critical_data_filter")
        result = strategy_for(entry).evaluate(context_for(snapshot))

        self.assertEqual(result.signal, "HOLD")
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason_codes, ("meta_strategy.safety.missing_critical_data.blocked",))
        self.assertTrue(result.evidence["missingDataSafe"])

    def test_malformed_context_evidence_is_rejected(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())

        with self.assertRaises(ValidationError):
            MetaStrategyAccountSnapshot(
                buying_power=50_000.0,
                cash_available=-1.0,
                account_equity=100_000.0,
                captured_at=snapshot.timestamp,
            )

    def test_point_in_time_snapshot_uses_only_finalized_bars_and_valid_quotes(self) -> None:
        base = build_meta_strategy_market_snapshot(request_with())
        with_future = build_meta_strategy_market_snapshot(
            request_with().model_copy(
                update={
                    "one_minute_candles": (
                        *request_with().one_minute_candles,
                        *request_with(one_minute_end=DECISION_TIMESTAMP + timedelta(minutes=5)).one_minute_candles[-3:],
                    )
                }
            )
        )

        self.assertEqual(base.candles, with_future.candles)
        self.assertEqual(base.features["openingRangeHigh"], with_future.features["openingRangeHigh"])

    def test_canonical_sessions_cover_dst_holidays_and_early_closes(self) -> None:
        self.assertEqual(meta_strategy_session_at(datetime(2026, 3, 9, 13, 35, tzinfo=UTC)), MetaStrategySession.OPENING)
        self.assertEqual(meta_strategy_session_at(datetime(2026, 11, 2, 14, 35, tzinfo=UTC)), MetaStrategySession.OPENING)
        self.assertEqual(meta_strategy_session_at(datetime(2026, 7, 3, 15, 0, tzinfo=UTC)), MetaStrategySession.CLOSED)
        self.assertEqual(meta_strategy_session_at(datetime(2026, 11, 27, 18, 15, tzinfo=UTC)), MetaStrategySession.AFTER_HOURS)
        self.assertEqual(canonical_session("power_hour"), MetaStrategySession.CLOSING)

    def test_runtime_backtest_snapshot_equivalence_for_same_point_in_time_request(self) -> None:
        runtime = build_meta_strategy_market_snapshot(request_with())
        backtest = build_meta_strategy_market_snapshot(request_with())

        self.assertEqual(runtime.deterministic_hash(), backtest.deterministic_hash())
        self.assertEqual(runtime.session_phase, backtest.session_phase)
        self.assertEqual(runtime.features, backtest.features)


def context_for(snapshot) -> MetaStrategyEvaluationContext:
    return MetaStrategyEvaluationContext(
        market_snapshot=snapshot,
        account_snapshot=MetaStrategyAccountSnapshot(
            buying_power=50_000.0,
            cash_available=25_000.0,
            account_equity=100_000.0,
            captured_at=snapshot.timestamp,
        ),
        global_risk_snapshot=MetaStrategyGlobalRiskSnapshot(
            available_risk_dollars=1_000.0,
            max_quantity=500,
            trading_permission=True,
            captured_at=snapshot.timestamp,
        ),
        operational_health_snapshot=MetaStrategyOperationalHealthSnapshot(
            status="OK",
            broker_connected=True,
            data_connected=True,
            trading_allowed=True,
            captured_at=snapshot.timestamp,
        ),
        economic_event_snapshot=MetaStrategyEconomicEventSnapshot(
            state="none",
            severity="none",
            minutes_to_event=60,
            active=False,
            captured_at=snapshot.timestamp,
        ),
        execution_mode="PAPER",
        evaluation_timestamp=snapshot.timestamp,
    )


def strategy_for(entry):
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


if __name__ == "__main__":
    unittest.main()
