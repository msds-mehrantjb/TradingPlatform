from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_POSITION_SIZING_VERSION,
    MetaStrategyBaselineSettings,
    MetaStrategyEffectiveSettings,
    MetaStrategySizingConfig,
    MetaStrategySizingContext,
    calculate_meta_strategy_position_size,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.local_settings_risk import MetaStrategyLocalSettingsRiskSource
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.domain.models import Signal
from backend.app.gates import GlobalOrderProposal
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime.now(UTC)


class SiblingInventoryFixture:
    def __init__(self, algorithm_id: str, *, quantity: float = 0.0, cash: float = 100_000.0, realised_pnl: float = 0.0) -> None:
        self.algorithm_id = algorithm_id
        self.quantity = float(quantity)
        self.cash = float(cash)
        self.realised_pnl = float(realised_pnl)

    def current_inventory_snapshot(self) -> dict[str, object]:
        return {
            "algorithmId": self.algorithm_id,
            "symbol": "SPY",
            "quantity": self.quantity,
            "cash": self.cash,
            "realisedPnl": self.realised_pnl,
        }


EXPECTED_CAP_IDS = {
    "risk_based_quantity",
    "position_cap_quantity",
    "buying_power_quantity",
    "liquidity_quantity",
    "maximum_share_quantity",
    "remaining_algorithm_risk_quantity",
    "global_risk_quantity_cap",
}


def baseline_settings(**overrides: object) -> MetaStrategyBaselineSettings:
    values = {
        "risk_percentage": 0.10,
        "position_cap": 0.50,
        "stop_multiplier": 1.0,
        "target_multiplier": 2.0,
        "maximum_holding_minutes": 30,
    }
    values.update(overrides)
    return MetaStrategyBaselineSettings(**values)


def effective_settings(baseline: MetaStrategyBaselineSettings | None = None, **overrides: object) -> MetaStrategyEffectiveSettings:
    base = baseline or baseline_settings()
    values = {
        "baseline_configuration_version": base.configuration_version,
        "baseline_settings_hash": base.settings_hash,
        "entry_threshold": base.entry_threshold,
        "model_probability_threshold": base.model_probability_threshold,
        "risk_percentage": base.risk_percentage,
        "position_cap": base.position_cap,
        "stop_multiplier": base.stop_multiplier,
        "target_multiplier": base.target_multiplier,
        "maximum_holding_minutes": base.maximum_holding_minutes,
        "spread_limit_bps": base.spread_limit_bps,
        "liquidity_requirement": base.liquidity_requirement,
        "trade_count_limit": base.trade_count_limit,
        "allow_long": base.allow_long,
        "allow_short": base.allow_short,
    }
    values.update(overrides)
    return MetaStrategyEffectiveSettings(**values)


def base_context(**overrides: object) -> MetaStrategySizingContext:
    baseline = overrides.pop("baseline_settings", baseline_settings())
    effective = overrides.pop("effective_settings", effective_settings(baseline))
    values = {
        "side": "BUY",
        "candidate_accepted": True,
        "local_gates_passed": True,
        "baseline_settings": baseline,
        "effective_settings": effective,
        "model_risk_multiplier": 1.0,
        "account_equity": 100_000.0,
        "available_buying_power": 100_000.0,
        "entry_price": 100.0,
        "stop_distance": 1.0,
        "market_liquidity": 10_000.0,
        "remaining_algorithm_risk": 100_000.0,
        "global_available_risk": 100_000.0,
        "global_quantity_cap": 100_000,
    }
    values.update(overrides)
    return MetaStrategySizingContext(**values)


class MetaStrategyStep29PositionSizingTest(unittest.TestCase):
    def test_every_sizing_cap_is_visible_and_final_quantity_is_minimum_valid_cap(self) -> None:
        result = calculate_meta_strategy_position_size(base_context())

        self.assertEqual(result.position_sizing_version, META_STRATEGY_POSITION_SIZING_VERSION)
        self.assertEqual({cap.cap_id for cap in result.caps}, EXPECTED_CAP_IDS)
        expected = min(
            result.risk_based_quantity,
            result.position_cap_quantity,
            result.buying_power_quantity,
            result.liquidity_quantity,
            result.maximum_share_quantity,
            result.remaining_algorithm_risk_quantity,
            result.global_risk_quantity_cap,
        )

        self.assertEqual(result.quantity, expected)
        self.assertEqual(result.limiting_cap, "position_cap_quantity")
        self.assertEqual(result.quantity, 500)
        self.assertIn("meta_strategy.sizing.calculated", result.reason_codes)

    def test_each_cap_has_focused_boundary_case(self) -> None:
        cases = {
            "risk_based_quantity": (
                base_context(stop_distance=2_500.0, global_available_risk=100_000_000.0, remaining_algorithm_risk=100_000_000.0),
                4,
            ),
            "position_cap_quantity": (
                base_context(),
                500,
            ),
            "buying_power_quantity": (
                base_context(available_buying_power=300.0),
                3,
            ),
            "liquidity_quantity": (
                base_context(market_liquidity=30.0),
                3,
            ),
            "maximum_share_quantity": (
                base_context(),
                3,
                MetaStrategySizingConfig(maximum_share_quantity=3),
            ),
            "remaining_algorithm_risk_quantity": (
                base_context(remaining_algorithm_risk=3.0),
                3,
            ),
            "global_risk_quantity_cap": (
                base_context(global_available_risk=3.0, global_quantity_cap=100),
                3,
            ),
        }

        for cap_id, case in cases.items():
            context = case[0]
            expected_quantity = case[1]
            config = case[2] if len(case) > 2 else MetaStrategySizingConfig()
            with self.subTest(cap_id=cap_id):
                result = calculate_meta_strategy_position_size(context, config=config)

                self.assertEqual(result.limiting_cap, cap_id)
                self.assertEqual(result.quantity, expected_quantity)
                self.assertEqual(result.quantity, min(cap.quantity for cap in result.caps))
                self.assertIn(f"meta_strategy.sizing.cap.{cap_id}", result.reason_codes)

    def test_ml_risk_multiplier_cannot_increase_quantity(self) -> None:
        full = calculate_meta_strategy_position_size(base_context())
        reduced = calculate_meta_strategy_position_size(base_context(model_risk_multiplier=0.50))

        self.assertLessEqual(reduced.quantity, full.quantity)
        self.assertLessEqual(reduced.ml_adjusted_risk_dollars, reduced.dynamic_profile_risk_dollars)
        self.assertLessEqual(full.dynamic_profile_risk_dollars, full.base_risk_dollars)
        self.assertIn("meta_strategy.sizing.ml_cannot_increase_quantity", full.reason_codes)
        with self.assertRaisesRegex(ValueError, "model_risk_multiplier_out_of_bounds"):
            base_context(model_risk_multiplier=1.01)
        with self.assertRaisesRegex(ValueError, "model_risk_multiplier_out_of_bounds"):
            base_context(model_risk_multiplier=-0.01)

    def test_dynamic_profile_risk_and_risk_off_profiles_reduce_quantity(self) -> None:
        baseline = baseline_settings(risk_percentage=0.10)
        normal = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.10, position_cap=2.0),
                available_buying_power=1_000_000.0,
                market_liquidity=1_000_000.0,
            )
        )
        defensive = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.01, position_cap=2.0),
                available_buying_power=1_000_000.0,
                market_liquidity=1_000_000.0,
            )
        )
        risk_off = calculate_meta_strategy_position_size(
            base_context(
                baseline_settings=baseline,
                effective_settings=effective_settings(baseline, risk_percentage=0.0, position_cap=0.0, trade_count_limit=0, allow_long=False, allow_short=False),
            )
        )

        self.assertLess(defensive.quantity, normal.quantity)
        self.assertEqual(risk_off.quantity, 0)
        self.assertEqual(risk_off.dynamic_profile_risk_dollars, 0.0)

    def test_hold_rejected_candidates_and_failed_local_gates_receive_zero_quantity(self) -> None:
        cases = {
            "hold": base_context(side="HOLD"),
            "rejected": base_context(candidate_accepted=False),
            "local_gate_failed": base_context(local_gates_passed=False),
        }

        for name, context in cases.items():
            with self.subTest(name=name):
                result = calculate_meta_strategy_position_size(context)
                self.assertEqual(result.quantity, 0)
                self.assertEqual(result.ml_adjusted_risk_dollars, 0.0)

        self.assertIn("meta_strategy.sizing.candidate_rejected_or_hold", calculate_meta_strategy_position_size(cases["hold"]).reason_codes)
        self.assertIn("meta_strategy.sizing.local_gate_failed", calculate_meta_strategy_position_size(cases["local_gate_failed"]).reason_codes)

    def test_invalid_prices_and_stop_distances_fail_safely(self) -> None:
        cases = {
            "entry_price": (base_context(entry_price=0.0), "meta_strategy.sizing.invalid_entry_price"),
            "stop_distance": (base_context(stop_distance=0.0), "meta_strategy.sizing.invalid_stop_distance"),
            "account_equity": (base_context(account_equity=0.0), "meta_strategy.sizing.invalid_account_equity"),
            "buying_power": (base_context(available_buying_power=-1.0), "meta_strategy.sizing.invalid_buying_power"),
            "liquidity": (base_context(market_liquidity=-1.0), "meta_strategy.sizing.invalid_liquidity"),
            "remaining_algorithm_risk": (
                base_context(remaining_algorithm_risk=-1.0),
                "meta_strategy.sizing.invalid_remaining_algorithm_risk",
            ),
            "global_risk": (base_context(global_available_risk=-1.0), "meta_strategy.sizing.invalid_global_risk"),
        }

        for name, (context, reason_code) in cases.items():
            with self.subTest(name=name):
                result = calculate_meta_strategy_position_size(context)
                self.assertEqual(result.quantity, 0)
                self.assertEqual(result.limiting_cap, "invalid_market")
                self.assertIn(reason_code, result.reason_codes)

    def test_existing_symbol_exposure_reduces_remaining_position_capacity(self) -> None:
        result = calculate_meta_strategy_position_size(
            base_context(existing_symbol_exposure=50_000.0)
        )

        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.limiting_cap, "invalid_market")
        self.assertIn("meta_strategy.sizing.maximum_position_exceeded", result.reason_codes)

    def test_zero_authoritative_values_do_not_become_fixture_defaults(self) -> None:
        result = calculate_meta_strategy_position_size(
            base_context(
                account_equity=0.0,
                available_buying_power=0.0,
                remaining_algorithm_risk=0.0,
                global_available_risk=0.0,
            )
        )

        self.assertEqual(result.quantity, 0)
        self.assertIn("meta_strategy.sizing.zero_account_equity", result.reason_codes)
        self.assertIn("meta_strategy.sizing.zero_buying_power", result.reason_codes)
        self.assertIn("meta_strategy.sizing.zero_algorithm_risk", result.reason_codes)
        self.assertIn("meta_strategy.sizing.zero_global_risk", result.reason_codes)

    def test_local_risk_blocks_zero_allocated_capital_zero_equity_and_zero_buying_power(self) -> None:
        cases = {
            "zero_allocated_capital": lambda: _risk_harness(allocated_capital=0.0),
            "zero_equity": lambda: _risk_harness(allocated_capital=100.0, realised_loss=100.0),
            "zero_buying_power": lambda: _risk_harness(allocated_capital=5_000.0, open_quantity=10, open_price=500.0),
        }

        for name, build in cases.items():
            with self.subTest(name=name):
                repository, risk_source = build()
                repository.current_inventory_snapshot(mark_prices={"SPY": 500.0})
                response = risk_source.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))
                account = risk_source.read_account_snapshot(at=NOW)

                self.assertEqual(response.action, "REJECT_NEW_ENTRY")
                self.assertEqual(response.maximumAllowedQuantity, 0)
                self.assertIn("meta_strategy.sizing.approved_quantity_zero", response.rejectionReasons)
                if name in {"zero_allocated_capital", "zero_equity"}:
                    self.assertEqual(account["accountEquity"], 0.0)
                    self.assertIn("meta_strategy.sizing.zero_account_equity", response.rejectionReasons)
                if name == "zero_buying_power":
                    self.assertEqual(account["buyingPower"], 0.0)
                    self.assertIn("meta_strategy.sizing.zero_buying_power", response.rejectionReasons)

    def test_local_risk_blocks_daily_loss_exceeded_and_maximum_open_risk_exceeded(self) -> None:
        daily_repository, daily_risk = _risk_harness(allocated_capital=100_000.0, realised_loss=100.0, maximum_daily_loss=50.0)
        daily_response = daily_risk.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))
        daily_snapshot = daily_risk.read_global_risk_snapshot(at=NOW, capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        self.assertEqual(daily_response.action, "REJECT_NEW_ENTRY")
        self.assertEqual(daily_response.maximumAllowedQuantity, 0)
        self.assertEqual(daily_repository.current_inventory_snapshot(as_of=NOW).daily_realised_pnl, -100.0)
        self.assertIn("meta_strategy.local_settings_risk.daily_loss_limit_exceeded", daily_response.rejectionReasons)
        self.assertIn("meta_strategy.local_settings_risk.daily_loss_limit_exceeded", daily_snapshot["reasonCodes"])

        open_risk_repository, open_risk = _risk_harness(allocated_capital=100_000.0, reserved_risk=1_000.0, maximum_open_risk=1_000.0)
        open_risk_response = open_risk.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))
        open_risk_snapshot = open_risk.read_global_risk_snapshot(at=NOW, capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        self.assertEqual(open_risk_response.action, "REJECT_NEW_ENTRY")
        self.assertEqual(open_risk_response.maximumAllowedQuantity, 0)
        self.assertEqual(open_risk_repository.current_inventory_snapshot().reserved_risk_dollars, 1_000.0)
        self.assertIn("meta_strategy.local_settings_risk.maximum_open_risk_exceeded", open_risk_response.rejectionReasons)
        self.assertIn("meta_strategy.local_settings_risk.maximum_open_risk_exceeded", open_risk_snapshot["reasonCodes"])

    def test_local_risk_reduces_or_blocks_when_maximum_position_is_exceeded(self) -> None:
        reduced_repository, reduced_risk = _risk_harness(allocated_capital=100_000.0, open_quantity=15, open_price=500.0, position_cap=0.10)
        reduced_repository.current_inventory_snapshot(mark_prices={"SPY": 500.0})

        reduced = reduced_risk.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))

        self.assertEqual(reduced.action, "REDUCE_QUANTITY")
        self.assertEqual(reduced.maximumAllowedQuantity, 5)
        self.assertIn("meta_strategy.local_settings_risk.quantity_reduced_to_position_cap", reduced.rejectionReasons)

        blocked_repository, blocked_risk = _risk_harness(allocated_capital=100_000.0, open_quantity=20, open_price=500.0, position_cap=0.10)
        blocked_repository.current_inventory_snapshot(mark_prices={"SPY": 500.0})

        blocked = blocked_risk.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))

        self.assertEqual(blocked.action, "REJECT_NEW_ENTRY")
        self.assertEqual(blocked.maximumAllowedQuantity, 0)
        self.assertIn("meta_strategy.local_settings_risk.quantity_reduced_to_position_cap", blocked.rejectionReasons)

    def test_existing_meta_strategy_risk_reservation_reduces_next_trade_sizing(self) -> None:
        repository, risk_source = _risk_harness(allocated_capital=100_000.0, reserved_risk=800.0, maximum_open_risk=1_000.0)
        before_risk = risk_source.read_global_risk_snapshot(at=NOW, capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        response = risk_source.approve_order(_proposal(quantity=10, price=500.0, planned_risk=1_000.0))

        self.assertEqual(repository.current_inventory_snapshot().reserved_risk_dollars, 800.0)
        self.assertEqual(before_risk["availableRiskDollars"], 200.0)
        self.assertEqual(response.action, "REDUCE_QUANTITY")
        self.assertEqual(response.maximumAllowedQuantity, 2)
        self.assertEqual(response.maximumAdditionalRiskDollars, 200.0)
        self.assertIn("meta_strategy.local_settings_risk.quantity_reduced_to_available_risk", response.rejectionReasons)

    def test_foreign_algorithm_loss_and_position_do_not_change_meta_strategy_local_risk_or_buying_power(self) -> None:
        foreign_loss = SiblingInventoryFixture("weighted_voting", quantity=0.0, cash=95_000.0, realised_pnl=-5_000.0)
        foreign_position = SiblingInventoryFixture("voting_ensemble", quantity=100.0, cash=50_000.0, realised_pnl=0.0)
        repository, risk_source = _risk_harness(allocated_capital=100_000.0, risk_percentage=0.01, maximum_open_risk=1_000.0, position_cap=1.0)
        before_account = risk_source.read_account_snapshot(at=NOW)
        before_risk = risk_source.read_global_risk_snapshot(at=NOW, capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        sibling_snapshots = (foreign_loss.current_inventory_snapshot(), foreign_position.current_inventory_snapshot())
        response = risk_source.approve_order(_proposal(quantity=10, price=500.0, planned_risk=100.0))
        after_account = risk_source.read_account_snapshot(at=NOW)
        after_risk = risk_source.read_global_risk_snapshot(at=NOW, capital_partition_id=META_STRATEGY_DEFAULT_CAPITAL_PARTITION)

        self.assertEqual(sibling_snapshots[0]["realisedPnl"], -5_000.0)
        self.assertEqual(sibling_snapshots[1]["quantity"], 100.0)
        self.assertEqual(before_account["accountEquity"], 100_000.0)
        self.assertEqual(before_account["buyingPower"], 100_000.0)
        self.assertEqual(after_account["buyingPower"], before_account["buyingPower"])
        self.assertEqual(after_risk["availableRiskDollars"], before_risk["availableRiskDollars"])
        self.assertEqual(response.action, "ALLOW")
        self.assertEqual(response.maximumAllowedQuantity, 10)
        self.assertEqual(repository.current_inventory_snapshot().open_positions, ())

    def test_none_authoritative_values_in_paper_mode_do_not_use_fixture_defaults(self) -> None:
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(
                mode="PAPER",
                snapshot_request=request_with(),
                account_equity=None,
                available_buying_power=None,
                remaining_algorithm_risk=None,
                global_available_risk=None,
                global_quantity_cap=None,
            ),
            config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
        )

        self.assertIsNone(result.order_intent)
        self.assertEqual(result.sizing.quantity, 0)
        self.assertIn("meta_strategy.sizing.account_equity_unavailable", result.reason_codes)
        self.assertIn("meta_strategy.sizing.buying_power_unavailable", result.reason_codes)
        self.assertIn("meta_strategy.sizing.algorithm_risk_unavailable", result.reason_codes)


def _risk_harness(
    *,
    allocated_capital: float,
    realised_loss: float = 0.0,
    open_quantity: int = 0,
    open_price: float = 500.0,
    reserved_risk: float = 0.0,
    risk_percentage: float = 0.01,
    maximum_daily_loss: float = 1_000.0,
    maximum_open_risk: float = 1_000.0,
    position_cap: float = 1.0,
) -> tuple[MetaStrategySqliteRepository, MetaStrategyLocalSettingsRiskSource]:
    repository = MetaStrategySqliteRepository(f"sqlite:///{_temp_db_path('inventory')}")
    settings_store = MetaStrategySettingsStore(_temp_db_path("settings"))
    settings = settings_store.create_baseline(
        build_meta_strategy_settings(
            settings_version=f"settings-risk-{uuid4().hex}",
            local_risk={
                "risk_percentage": risk_percentage,
                "maximum_daily_loss": maximum_daily_loss,
                "maximum_open_risk": maximum_open_risk,
            },
            position_sizing={
                "position_cap": position_cap,
                "maximum_share_quantity": 10_000,
                "liquidity_participation_rate": 0.10,
            },
        ),
        actor="test",
    )
    settings_store.activate_settings(settings.settings_version, actor="test")
    repository.record_allocated_capital({**_inventory_payload("allocated-capital", quantity=0), "allocatedCapital": allocated_capital})
    if realised_loss > 0.0:
        repository.ingest_broker_fill(_fill_payload("loss-entry", side="BUY", quantity=1, price=500.0))
        repository.ingest_broker_fill(_fill_payload("loss-exit", side="SELL", quantity=1, price=500.0 - realised_loss))
    if open_quantity > 0:
        repository.ingest_broker_fill(_fill_payload("open-position", side="BUY", quantity=open_quantity, price=open_price))
        repository.current_inventory_snapshot(mark_prices={"SPY": open_price})
    if reserved_risk > 0.0:
        repository.record_order_intent(_inventory_payload("reserved-risk", quantity=10, price=500.0, reserved_risk=reserved_risk))
    return repository, MetaStrategyLocalSettingsRiskSource(settings_store=settings_store, inventory_repository=repository)


def _proposal(*, quantity: int, price: float, planned_risk: float) -> GlobalOrderProposal:
    suffix = uuid4().hex
    return GlobalOrderProposal(
        algorithmId=ALGORITHM_ID,
        capitalPartitionId=META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        decisionId=f"decision-risk-{suffix}",
        orderIntentId=f"intent-risk-{suffix}",
        intent="new_entry",
        symbol="SPY",
        side=Signal.BUY,
        quantity=quantity,
        triggerPrice=price,
        limitPrice=price,
        stopPrice=max(0.01, price - 10.0),
        targetPrice=price + 20.0,
        plannedRiskDollars=planned_risk,
        settingsSnapshot={"settingsVersion": "settings-risk-test"},
        entryFormula={"price": price},
        stopFormula={"price": max(0.01, price - 10.0)},
        targetFormula={"price": price + 20.0},
        strategyStateHash=f"state-{suffix}",
        proposedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash=f"config-{suffix}",
    )


def _inventory_payload(event_id: str, *, quantity: float, price: float = 500.0, reserved_risk: float = 0.0) -> dict[str, object]:
    return {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "settingsVersion": "settings-risk-test",
        "decisionId": f"decision-{event_id}",
        "jobId": "job-risk-test",
        "eventId": event_id,
        "orderIntentId": f"intent-{event_id}",
        "clientOrderId": f"client-{event_id}",
        "correlationId": f"corr-{event_id}",
        "symbol": "SPY",
        "side": "BUY",
        "quantity": quantity,
        "limitPrice": price,
        "price": price,
        "reservedRiskDollars": reserved_risk,
        "timestamp": NOW.isoformat(),
    }


def _fill_payload(event_id: str, *, side: str, quantity: float, price: float) -> dict[str, object]:
    return {
        **_inventory_payload(event_id, quantity=quantity, price=price),
        "eventId": f"fill-{event_id}",
        "brokerOrderId": f"broker-{event_id}",
        "brokerFillId": f"broker-fill-{event_id}",
        "side": side,
        "filledQuantity": quantity,
        "fillPrice": price,
        "commission": 0.0,
        "estimatedSlippage": 0.0,
        "timestamp": NOW.isoformat(),
    }


def _temp_db_path(kind: str) -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-risk-{kind}-{uuid4().hex}.sqlite"
if __name__ == "__main__":
    unittest.main()
