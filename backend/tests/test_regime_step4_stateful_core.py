from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_state import REGIME_RUNTIME_STATE_SCHEMA_VERSION, migrate_regime_runtime_state
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.algorithms.regime.stateful_core import deterministic_regime_decision_id, process_completed_bar, process_regime_bar
from backend.app.algorithms.regime.strategy_registry import (
    REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES,
    REGIME_STRATEGY_DEFINITIONS,
    REGIME_STRATEGY_REGISTRY_VALIDATION,
    validate_regime_strategy_registry,
)


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-step4",
    "accountId": "paper-account-step4",
    "runtimeMode": "paper",
    "symbol": "SPY",
}


class RegimeStep4StatefulCoreTest(unittest.TestCase):
    def test_completed_bar_processor_returns_required_state_out_contract(self) -> None:
        settings = settings_snapshot()
        snapshot = build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": fixture_candles(70)})
        result = process_completed_bar(
            snapshot=snapshot,
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "manifest-1"},
            account_snapshot={"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500},
        )

        for key in (
            "decision",
            "nextRuntimeState",
            "strategyOutputs",
            "contextOutputs",
            "confirmationOutputs",
            "safetyOutputs",
            "familyAggregation",
            "effectiveProfile",
            "orderProposal",
            "persistenceRecords",
        ):
            self.assertIn(key, result)
        state = result["nextRuntimeState"]
        self.assertEqual(state["schemaVersion"], REGIME_RUNTIME_STATE_SCHEMA_VERSION)
        for state_key in (
            "confirmedRegime",
            "previousConfirmedRegime",
            "candidateRegime",
            "candidateConfirmationCount",
            "regimeStartedAt",
            "regimeDwellBars",
            "unknownBarCount",
            "lastProcessedBarTimestamp",
            "lastDecisionId",
            "cooldownUntil",
            "strategyCooldowns",
            "familyCooldowns",
            "dailyCounters",
            "openPositionSummary",
            "circuitBreakerState",
            "stateVersion",
        ):
            self.assertIn(state_key, state)
        self.assertEqual(state["lastProcessedBarTimestamp"], snapshot.latest.timestamp)
        self.assertEqual(state["lastDecisionId"], result["decision"]["decision_id"])
        self.assertEqual(state["sequenceVersion"], 1)
        for result_key in ("classification", "transition", "familyScores", "localRiskCandidate", "orderProposal"):
            self.assertIn(result_key, result)

    def test_hysteresis_state_is_passed_across_completed_bars(self) -> None:
        settings = settings_snapshot()
        candles = fixture_candles(72)
        first = process_completed_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles[:70]}),
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "manifest-70"},
            account_snapshot={},
        )
        second = process_completed_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles[:71]}),
            settings_snapshot=settings,
            previous_state=first["nextRuntimeState"],
            inventory_snapshot={**IDENTITY, "dataManifestHash": "manifest-71"},
            account_snapshot={},
        )

        self.assertEqual(second["nextRuntimeState"]["sequenceVersion"], 2)
        self.assertGreaterEqual(second["nextRuntimeState"]["regimeDwellBars"], 1)
        self.assertEqual(second["decision"]["confirmed_state"]["previous_regime"], first["decision"]["confirmed_state"]["previous_regime"])

    def test_decision_id_is_deterministic_from_required_components(self) -> None:
        first = deterministic_regime_decision_id(
            algorithm_instance_id="instance-a",
            runtime_mode="paper",
            symbol="SPY",
            completed_bar_timestamp="2026-07-23T14:30:00Z",
            data_manifest_hash="manifest-a",
            settings_version="settings-a",
        )
        repeat = deterministic_regime_decision_id(
            algorithm_instance_id="instance-a",
            runtime_mode="paper",
            symbol="SPY",
            completed_bar_timestamp="2026-07-23T14:30:00Z",
            data_manifest_hash="manifest-a",
            settings_version="settings-a",
        )
        changed_settings = deterministic_regime_decision_id(
            algorithm_instance_id="instance-a",
            runtime_mode="paper",
            symbol="SPY",
            completed_bar_timestamp="2026-07-23T14:30:00Z",
            data_manifest_hash="manifest-a",
            settings_version="settings-b",
        )

        self.assertEqual(first, repeat)
        self.assertNotEqual(first, changed_settings)

    def test_service_rejects_caller_supplied_inventory_and_account_state(self) -> None:
        repository = temp_repository()
        identity = {**IDENTITY, "algorithmInstanceId": f"step4-{uuid4().hex[:8]}"}
        service = RegimeApplicationService(repository=repository)
        payload = {
            "identity": identity,
            "marketData": {"symbol": "SPY", "primaryCandles": fixture_candles(70)},
            "inventorySnapshot": {"dataManifestHash": "same-manifest"},
            "account": {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500},
        }

        with self.assertRaisesRegex(ValueError, "Regime service rejects authoritative request fields"):
            service.evaluate(payload)

    def test_runtime_state_migrates_legacy_hysteresis_payload(self) -> None:
        migrated = migrate_regime_runtime_state(
            {
                "confirmedRegime": "strong_uptrend",
                "previousRegime": "weak_uptrend",
                "candidateRegime": "range_bound",
                "candidateConfirmationCount": 2,
                "regimeStartTime": "2026-07-23T14:30:00Z",
                "lastProcessedBarTimestamp": "2026-07-23T14:31:00Z",
            },
            IDENTITY,
            timestamp="2026-07-23T14:32:00Z",
        )

        self.assertEqual(migrated.schema_version, REGIME_RUNTIME_STATE_SCHEMA_VERSION)
        self.assertEqual(migrated.confirmed_regime, "strong_uptrend")
        self.assertEqual(migrated.candidate_confirmation_count, 2)

    def test_backtest_uses_same_state_transition_function(self) -> None:
        settings = settings_snapshot(runtime_mode="backtest")
        result = run_regime_backtest(
            {
                "symbol": "SPY",
                "candles": fixture_candles(72),
                "__regime_settings_snapshot": settings,
                "__regime_authoritative_settings": {},
                "runtimeMode": "backtest",
            }
        )

        states = [decision["runtimeState"] for decision in result["decisions"]]
        self.assertEqual(states[0]["sequenceVersion"], 1)
        self.assertEqual(states[-1]["sequenceVersion"], len(states))
        self.assertTrue(all(state["schemaVersion"] == REGIME_RUNTIME_STATE_SCHEMA_VERSION for state in states))

    def test_process_regime_bar_full_pipeline_buy_sell_and_hold(self) -> None:
        settings = permissive_two_family_settings()
        account = {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500, "globalRiskCapacityQuantity": 1_000}

        buy = process_regime_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": opening_breakout_candles("up", count=80), "contextFeeds": fresh_context()}),
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "buy-two-family"},
            account_snapshot=account,
        )
        sell = process_regime_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": opening_breakout_candles("down", count=80), "contextFeeds": fresh_context()}),
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "sell-two-family"},
            account_snapshot=account,
        )
        hold = process_regime_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": fixture_candles(80), "contextFeeds": {"quoteFreshness": {"status": "stale", "ageMs": 60_000}}}),
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "hold-stale"},
            account_snapshot=account,
        )

        self.assertEqual(buy["decision"]["signal"], "Hold")
        self.assertFalse(buy["decision"]["trade_allowed"])
        self.assertEqual(buy["familyAggregation"]["activeFamilyCount"], 0)
        self.assertIsNone(buy["orderProposal"])
        self.assertEqual(sell["decision"]["signal"], "Hold")
        self.assertFalse(sell["decision"]["trade_allowed"])
        self.assertEqual(sell["familyAggregation"]["activeFamilyCount"], 0)
        self.assertEqual(hold["decision"]["signal"], "Hold")
        self.assertFalse(hold["decision"]["trade_allowed"])
        self.assertIn("regime.safety.stale_data", hold["decision"]["trade_blockers"])

    def test_one_family_signal_is_blocked_by_minimum_independent_families(self) -> None:
        settings = permissive_two_family_settings()
        one_family = process_regime_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": opening_breakout_candles("up", count=50), "contextFeeds": fresh_context()}),
            settings_snapshot=settings,
            previous_state=None,
            inventory_snapshot={**IDENTITY, "dataManifestHash": "one-family"},
            account_snapshot={"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500, "globalRiskCapacityQuantity": 1_000},
        )

        self.assertEqual(one_family["familyAggregation"]["activeFamilyCount"], 0)
        self.assertEqual(one_family["decision"]["signal"], "Hold")
        self.assertIn("regime.local_gate.minimum_independent_families", one_family["decision"]["trade_blockers"])

    def test_production_registry_validation_requires_real_directional_families_and_tests(self) -> None:
        self.assertTrue(REGIME_STRATEGY_REGISTRY_VALIDATION["validated"])
        self.assertGreaterEqual(REGIME_STRATEGY_REGISTRY_VALIDATION["directionalCount"], 4)
        self.assertTrue(REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES.issubset(set(REGIME_STRATEGY_REGISTRY_VALIDATION["independentDirectionalFamilies"])))

        with self.assertRaises(RuntimeError):
            validate_regime_strategy_registry(tuple(definition for definition in REGIME_STRATEGY_DEFINITIONS if definition.role != "directional"))

        trend_only = tuple(definition for definition in REGIME_STRATEGY_DEFINITIONS if definition.role != "directional" or definition.family == "trend")
        with self.assertRaises(RuntimeError):
            validate_regime_strategy_registry(trend_only)


def settings_snapshot(runtime_mode: str = "paper") -> dict:
    return validate_regime_trading_settings_snapshot(
        {
            "identity": {
                **IDENTITY,
                "runtimeMode": runtime_mode,
            }
        }
    ).as_dict()


def permissive_two_family_settings() -> dict:
    return validate_regime_trading_settings_snapshot(
        {
            "identity": IDENTITY,
            "familyAggregation": {
                "minimumWinningScore": 0,
                "minimumSignalEdge": 0,
                "minimumNetExpectedEdge": 0,
                "minimumActiveStrategies": 1,
                "minimumIndependentFamilies": 2,
                "maximumAbstentionRate": 1,
            },
            "classifier": {"minimumRegimeConfidence": 0},
            "entryPolicy": {"minimumNetExpectedEdge": 0},
            "strategy_settings": {
                definition.strategy_id: {"lifecycle": "active"}
                for definition in REGIME_STRATEGY_DEFINITIONS
                if definition.role == "directional"
            },
        }
    ).as_dict()


def fixture_candles(count: int = 72) -> list[dict[str, float | str]]:
    candles: list[dict[str, float | str]] = []
    price = 100.0
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    for index in range(count):
        price += 0.08
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": timestamp,
                "open": price - 0.03,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price,
                "volume": 120_000 + index,
            }
        )
    return candles


def opening_breakout_candles(direction: str, count: int = 80) -> list[dict[str, float | str]]:
    candles: list[dict[str, float | str]] = []
    price = 100.0
    for index in range(count):
        if index < 30:
            price = 100.0 + (0.05 if index % 2 == 0 else -0.05)
        elif direction == "up":
            price = 100.25 + (index - 30) * 0.005
        else:
            price = 99.75 - (index - 30) * 0.005
        volume = 180_000 if index >= count - 2 else 100_000
        candles.append(
            {
                "timestamp": (datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": round(price - 0.04, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": volume,
            }
        )
    return candles


def fresh_context() -> dict:
    return {
        "quoteFreshness": {
            "status": "fresh",
            "ageMs": 1000,
            "bid": 99.99,
            "ask": 100.01,
            "spreadBps": 2.0,
            "tradeCount": 100,
            "expectedFillQuantity": 10,
        },
        "scheduledEconomicEvent": {"state": "none"},
        "intradayVolatilityBaseline": {
            "calibrationStatus": "ready",
            "atrPercentile": 0.45,
            "realizedVolatilityPercentile": 0.48,
            "currentRangeVsExpected": 1.0,
            "sampleSize": 80,
        },
    }


def temp_repository() -> RegimeRepository:
    root = Path(__file__).resolve().parent / "tmp" / "regime_step4"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


if __name__ == "__main__":
    unittest.main()
