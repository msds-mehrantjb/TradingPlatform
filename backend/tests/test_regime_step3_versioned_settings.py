from __future__ import annotations

import inspect
import json
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.regime import api as regime_api
from backend.app.algorithms.regime.configuration import (
    DEFAULT_REGIME_SETTINGS,
    REGIME_STRATEGY_IDS,
    flatten_regime_trading_settings,
    validate_regime_trading_settings_snapshot,
)
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.main import app


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-step3",
    "accountId": "paper-account-step3",
    "runtimeMode": "shadow",
    "symbol": "SPY",
}


class RegimeStep3VersionedSettingsTest(unittest.TestCase):
    def test_typed_settings_model_has_required_sections_and_conservative_defaults(self) -> None:
        snapshot = validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()
        flat = flatten_regime_trading_settings(snapshot)

        self.assertEqual(snapshot["identity"]["algorithmId"], "regime")
        self.assertEqual(snapshot["identity"]["symbol"], "SPY")
        self.assertEqual(set(snapshot["strategy_settings"]), set(REGIME_STRATEGY_IDS))
        self.assertEqual(flat["baseRiskPercent"], 0.10)
        self.assertEqual(flat["maxPositionPercent"], 10.0)
        self.assertEqual(flat["dailyAllocationPercent"], 20.0)
        self.assertEqual(flat["maxTradesPerDay"], 3)
        self.assertEqual(flat["maxConsecutiveLosses"], 3)
        self.assertEqual(flat["maxDailyLossPercent"], 0.50)
        self.assertEqual(flat["maxParticipationPercent"], 0.02)
        self.assertFalse(flat["pyramidingEnabled"])
        self.assertFalse(flat["shortEntriesEnabled"])
        self.assertEqual(flat["confirmationBars"], 3)
        self.assertEqual(flat["minimumDwellBars"], 5)
        self.assertEqual(flat["transitionConfidenceGap"], 0.10)
        self.assertEqual(flat["cooldownBars"], 5)
        self.assertEqual(flat["entryCutoffTimeEt"], "15:30")
        self.assertEqual(flat["flattenTimeEt"], "15:55")
        for finite_key in (
            "maxAllowedShares",
            "maxNotionalDollars",
            "maxHoldingBars",
            "orderTimeToLiveSeconds",
            "maxCancelReplaceAttempts",
            "maximumSlippageBps",
            "staleBarToleranceSeconds",
            "quoteAgeToleranceSeconds",
            "minimumNetExpectedEdge",
        ):
            self.assertGreaterEqual(float(flat[finite_key]), 0.0, finite_key)

    def test_repository_restores_active_version_and_rejects_invalid_activation_without_disturbing_it(self) -> None:
        repository = temp_repository()
        active = repository.ensure_active_settings_snapshot(IDENTITY)
        current_version = active["settingsVersion"]

        with self.assertRaises(ValueError):
            repository.activate_settings_snapshot(
                {
                    "actor": "risk-admin",
                    "settings": {
                        "identity": IDENTITY,
                        "position_sizing": {"baseRiskPercent": 0.05},
                        "execution": {"unsafeBrokerOverride": True},
                    },
                }
            )

        restored = RegimeRepository(f"sqlite:///{repository.path}").active_settings_snapshot(IDENTITY)
        self.assertEqual(restored["settingsVersion"], current_version)
        self.assertEqual(restored["flatSettings"]["baseRiskPercent"], DEFAULT_REGIME_SETTINGS["baseRiskPercent"])

    def test_dynamic_profile_overlays_can_only_reduce_risk_or_tighten_bounds(self) -> None:
        with self.assertRaises(ValueError):
            validate_regime_trading_settings_snapshot(
                {
                    "identity": IDENTITY,
                    "dynamic_profiles": {
                        "overlays": {
                            "weak_uptrend": {"baseRiskPercentCap": 0.20},
                        }
                    },
                }
            )
        valid = validate_regime_trading_settings_snapshot(
            {
                "identity": IDENTITY,
                "dynamic_profiles": {
                    "overlays": {
                        "weak_uptrend": {"baseRiskPercentCap": 0.05, "maximumSlippageBps": 4.0},
                    }
                },
            }
        ).as_dict()
        self.assertEqual(valid["dynamic_profiles"]["overlays"]["weak_uptrend"]["baseRiskPercentCap"], 0.05)

    def test_service_evaluation_rejects_caller_supplied_operational_settings(self) -> None:
        repository = temp_repository()
        service = RegimeApplicationService(repository=repository)
        repository.activate_settings_snapshot(
            {
                "actor": "risk-admin",
                "settings": {
                    "identity": IDENTITY,
                    "position_sizing": {"baseRiskPercent": 0.05},
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "Regime service rejects authoritative request fields"):
            service.evaluate(
                {
                    "identity": IDENTITY,
                    "marketData": {"symbol": "SPY", "primaryCandles": fixture_candles()},
                    "settings": {"baseRiskPercent": 5.0, "maxPositionPercent": 100.0},
                    "account": {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500},
                }
            )

    def test_decision_and_order_intent_records_persist_exact_settings_snapshot(self) -> None:
        repository = temp_repository()
        service = RegimeApplicationService(repository=repository)
        result = service.evaluate(
            {
                "identity": IDENTITY,
                "marketData": {"symbol": "SPY", "primaryCandles": fixture_candles()},
            }
        )

        with sqlite3.connect(repository.path) as conn:
            decision_payload = json.loads(conn.execute("SELECT payload_json FROM regime_decisions").fetchone()[0])
            active_payload = json.loads(conn.execute("SELECT payload_json FROM regime_active_settings").fetchone()[0])

        self.assertEqual(decision_payload["settingsSnapshot"]["settingsVersion"], result["settingsVersion"])
        self.assertEqual(active_payload["settingsSnapshot"]["settingsVersion"], result["settingsVersion"])
        if result["orderIntent"] is not None:
            self.assertEqual(result["orderIntent"]["settingsSnapshot"]["settingsVersion"], result["settingsVersion"])

    def test_settings_command_api_enqueues_background_job_without_inline_activation(self) -> None:
        route_source = inspect.getsource(regime_api.submit_regime_settings_command)
        self.assertIn('REGIME_JOB_MANAGER.enqueue("settings_activation"', route_source)
        self.assertNotIn("activate_settings", route_source)

        client = TestClient(app)
        response = client.post(
            "/api/regime/settings/commands",
            json={
                "actor": "risk-admin",
                "settings": {
                    "identity": {
                        **IDENTITY,
                        "algorithmInstanceId": f"api-step3-{uuid4().hex[:8]}",
                    },
                    "position_sizing": {"baseRiskPercent": 0.04},
                },
            },
        )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["algorithmId"], "regime")
        self.assertEqual(response.json()["jobKind"], "settings_activation")
        self.assertEqual(response.json()["status"], "queued")


def fixture_candles(count: int = 70) -> list[dict[str, float | str]]:
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


def temp_repository() -> RegimeRepository:
    root = Path(__file__).resolve().parent / "tmp" / "regime_step3"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


if __name__ == "__main__":
    unittest.main()
