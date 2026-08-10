from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.dynamic_settings import resolve_effective_settings
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.runtime_context import (
    RUNTIME_CONTEXT_FIELD_NAMES,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingExecutionCostEstimate,
    WeightedVotingStaticAccountPort,
    WeightedVotingStaticGlobalRiskPort,
    WeightedVotingStaticMarketDataPort,
    payload_contains_forbidden_authoritative_evaluation_inputs,
)
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.gates import GlobalGateResponse


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


class WeightedVotingRuntimeContextTest(unittest.TestCase):
    def test_public_evaluation_ignores_http_supplied_safety_state_and_holds(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        payload = evaluate_payload()
        payload.update(
            {
                "account_equity": 9_999_999.0,
                "available_buying_power": 9_999_999.0,
                "capital_available": 9_999_999.0,
                "global_available_risk": 9_999_999.0,
                "global_max_shares": 999_999,
                "globalGateResponse": {
                    "action": "ALLOW",
                    "maximumAllowedQuantity": 999_999,
                    "maximumAdditionalRiskDollars": 9_999_999.0,
                    "evaluatedAt": payload["data_timestamp"],
                    "configurationHash": "client-manufactured-global-risk",
                },
            }
        )

        result = service.evaluate_research_shadow(payload)

        self.assertTrue(payload_contains_forbidden_authoritative_evaluation_inputs(payload))
        self.assertEqual(result["decision"]["signal"], "Hold")
        self.assertFalse(result["decision"]["eligible"])
        self.assertEqual(result["sizingResult"]["quantity"], 0)
        self.assertIn("weighted_voting.runtime_context.http_safety_inputs_ignored", result["reasonCodes"])
        self.assertIn("globalGateResponse", result["deprecatedIgnoredInputs"])
        self.assertIn("weighted_voting.runtime_context.global_risk_service_unavailable", result["runtimeContext"]["failureReasonCodes"])
        self.assertEqual(result["globalRiskResponse"]["action"], "REJECT")
        self.assertEqual(result["globalRiskResponse"]["maximum_quantity"], 0)
        self.assertEqual(result["globalGateApplication"]["globallyAllowedQuantity"], 0)
        self.assertTrue(result["researchOnly"])
        self.assertFalse(result["productionStateMutated"])
        self.assertEqual(service.store.snapshots, {})
        self.assertFalse(any(snapshot.get("action") == "ALLOW" for snapshot in service.store.snapshots.values() if isinstance(snapshot, dict)))

    def test_production_missing_safety_inputs_use_explicit_unavailable_reason_codes(self) -> None:
        result = WeightedVotingService(store=MemoryStore()).evaluate_research_shadow(evaluate_payload(include_session=True))

        self.assertEqual(result["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.context.inventory_unavailable", result["runtimeContext"]["failureReasonCodes"])
        self.assertIn("weighted_voting.context.cost_model_unavailable", result["runtimeContext"]["failureReasonCodes"])
        self.assertIn("weighted_voting.runtime_context.missing_read_only_account_equity", result["runtimeContext"]["failureReasonCodes"])
        self.assertIn("weighted_voting.runtime_context.global_risk_capacity_unavailable", result["runtimeContext"]["failureReasonCodes"])
        self.assertEqual(result["globalRiskResponse"]["action"], "REJECT")

    def test_global_risk_request_and_response_are_persisted_for_audit(self) -> None:
        store = MemoryStore()
        service = WeightedVotingService(store=store)

        result = service.evaluate_replay_fixture(evaluate_payload())

        self.assertEqual(result["globalRiskResponse"]["action"], "ALLOW")
        self.assertTrue(any(key.startswith("weighted_voting.global_risk_requests.") for key in store.snapshots))
        self.assertTrue(any(key.startswith("weighted_voting.global_risk_responses.") for key in store.snapshots))
        request = store.snapshots[f"weighted_voting.global_risk_requests.{result['globalRiskRequest']['request_id']}"]
        response = store.snapshots[f"weighted_voting.global_risk_responses.{result['globalRiskRequest']['request_id']}"]
        self.assertEqual(request["algorithm_id"], "weighted_voting")
        self.assertEqual(response["request_id"], request["request_id"])
        self.assertEqual(response["proposal_id"], request["proposal_id"])

    def test_global_risk_request_uses_weighted_voting_local_inventory_not_broker_account(self) -> None:
        context = valid_fixture_context(account_equity=9_999_999.0, broker_buying_power=9_999_999.0)

        result = WeightedVotingService(store=MemoryStore()).evaluate_context(context)

        observations = result["globalRiskRequest"]["account_level_risk_observations"]
        self.assertEqual(observations["localEquity"], context.inventory_snapshot.equity)
        self.assertEqual(observations["localCash"], context.inventory_snapshot.cash_available)
        self.assertEqual(observations["localBuyingPower"], context.inventory_snapshot.buying_power)
        self.assertEqual(observations["localReservedBuyingPower"], context.inventory_snapshot.reserved_buying_power)
        self.assertEqual(observations["localGrossExposure"], context.inventory_snapshot.gross_exposure)
        self.assertEqual(observations["inventorySnapshotVersion"], context.inventory_snapshot.snapshot_version)
        self.assertEqual(observations["source"], "weighted_voting.local_inventory")
        self.assertNotIn("accountEquity", observations)
        self.assertNotIn("brokerBuyingPower", observations)

    def test_stale_quote_is_explicitly_unavailable_not_favourable(self) -> None:
        result = WeightedVotingService(store=MemoryStore()).evaluate_replay_fixture(evaluate_payload(data_freshness_seconds=9999))

        self.assertEqual(result["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.context.quote_stale", result["runtimeContext"]["failureReasonCodes"])

    def test_replay_fixture_is_explicitly_separate_from_production_context(self) -> None:
        result = WeightedVotingService(store=MemoryStore()).evaluate_replay_fixture(evaluate_payload())

        self.assertEqual(result["runtimeContext"]["mode"], "replay_fixture")
        self.assertEqual(result["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.insufficient_active_strategies", result["decision"]["reason_codes"])
        self.assertFalse(result["decision"]["eligible"])
        self.assertNotIn("weighted_voting.runtime_context.missing_read_only_account_equity", result["runtimeContext"]["failureReasonCodes"])

    def test_context_fields_have_source_attribution_and_manifest_hash(self) -> None:
        context = valid_fixture_context()

        self.assertEqual(set(context.source_attribution), set(RUNTIME_CONTEXT_FIELD_NAMES))
        self.assertTrue(context.manifest_hash)
        self.assertIs(context.market_snapshot, context.finalised_one_minute_market_snapshot)
        self.assertEqual(context.paper_account_snapshot.account_equity, context.read_only_account_equity)
        self.assertIs(context.session_state, context.exchange_session_state)
        self.assertEqual(context.cost_estimate.slippage_per_share, context.estimated_slippage)
        self.assertIs(context.settings, context.effective_settings)
        self.assertIs(context.weight_state, context.active_weight_state)
        self.assertIs(context.global_risk_capacity, context.global_risk_state)
        for field_name, source in context.source_attribution.items():
            with self.subTest(field=field_name):
                self.assertEqual(source.field_name, field_name)
                self.assertTrue(source.source_id)
                self.assertIsNotNone(source.observed_at)
                self.assertIsNotNone(source.data_timestamp)

    def test_execution_capable_evaluate_requires_authoritative_runtime_context(self) -> None:
        service = WeightedVotingService(store=MemoryStore())

        with self.assertRaisesRegex(TypeError, "WeightedVotingRuntimeContext"):
            service.evaluate(evaluate_payload())

    def test_stale_absent_and_conflicting_context_fail_closed_with_reason_codes(self) -> None:
        service = WeightedVotingService(store=MemoryStore())
        stale = service.evaluate_replay_fixture(evaluate_payload(data_freshness_seconds=9999))
        absent = service.evaluate_research_shadow(evaluate_payload(include_session=True))
        conflicting = service.evaluate_context(valid_fixture_context(inventory_symbol="QQQ"))

        self.assertEqual(stale["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.runtime_context.stale_market_data", stale["runtimeContext"]["failureReasonCodes"])
        self.assertEqual(absent["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.runtime_context.missing_read_only_account_equity", absent["runtimeContext"]["failureReasonCodes"])
        self.assertEqual(conflicting["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.runtime_context.conflicting_inventory_symbol", conflicting["runtimeContext"]["failureReasonCodes"])

    def test_market_snapshot_rejects_explicit_incomplete_one_minute_bar(self) -> None:
        payload = evaluate_payload(include_session=True)
        payload["candles"][-1]["finalized"] = False

        with self.assertRaisesRegex(ValueError, "completed bars"):
            build_weighted_voting_market_snapshot(payload)

    def test_market_snapshot_accepts_explicit_completed_one_minute_bar(self) -> None:
        payload = evaluate_payload(include_session=True)
        payload["candles"][-1]["finalized"] = True

        snapshot = build_weighted_voting_market_snapshot(payload)

        self.assertTrue(snapshot.one_minute_candles[-1].finalized)

    def test_missing_actual_quote_is_not_manufactured_and_blocks_entry(self) -> None:
        payload = evaluate_payload(include_session=True)
        payload.pop("bid")
        payload.pop("ask")

        snapshot = build_weighted_voting_market_snapshot(payload)
        result = WeightedVotingService(store=MemoryStore()).evaluate_research_shadow(payload)

        self.assertIsNone(snapshot.bid)
        self.assertIsNone(snapshot.ask)
        self.assertIsNone(snapshot.spread)
        self.assertEqual(result["decision"]["signal"], "Hold")
        self.assertIn("weighted_voting.runtime_context.missing_quote_state", result["runtimeContext"]["failureReasonCodes"])
        self.assertIn("weighted_voting.decision_kernel.missing_actual_quote_blocks_trade", result["reasonCodes"])


def valid_fixture_context(*, inventory_symbol: str = "SPY", account_equity: float = 100000.0, broker_buying_power: float = 100000.0):
    store = MemoryStore()
    service = WeightedVotingService(store=store)
    payload = evaluate_payload(include_session=True)
    snapshot = build_weighted_voting_market_snapshot(payload)
    active_weights = service.active_weight_state()
    effective_model = resolve_effective_settings(timestamp=snapshot.data_timestamp)
    gate_response = GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=100000,
        maximumAdditionalRiskDollars=1000.0,
        evaluatedAt=snapshot.data_timestamp,
        configurationHash="central-risk-fixture",
    )
    return WeightedVotingRuntimeContextBuilder(
        market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
        inventory_repository=WeightedVotingInventoryRepository(store, symbol=inventory_symbol, allocated_capital=100000.0),
        account_port=WeightedVotingStaticAccountPort(account_equity=account_equity, broker_buying_power=broker_buying_power),
        global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=1000.0, global_max_shares=100000, gate_response=gate_response),
        effective_settings=effective_model,
        active_weight_state=active_weights,
        observed_at=snapshot.data_timestamp,
        mode="test_fixture",
        cost_estimate=WeightedVotingExecutionCostEstimate(
            slippage_per_share=0.01,
            fee_per_share=0.01,
            observed_at=snapshot.data_timestamp,
            source_id="weighted_voting.test_fixture.cost_model",
            reason_codes=("weighted_voting.test_fixture.cost_model",),
        ),
    ).build()


def evaluate_payload(*, include_session: bool = False, data_freshness_seconds: float | None = None) -> dict:
    rows = candle_rows(count=95)
    payload = {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
    }
    if include_session:
        payload["session_phase"] = "morning"
    if data_freshness_seconds is not None:
        payload["data_freshness_seconds"] = data_freshness_seconds
    return payload


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


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


if __name__ == "__main__":
    unittest.main()
