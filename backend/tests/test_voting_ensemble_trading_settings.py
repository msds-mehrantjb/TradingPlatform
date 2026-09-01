from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from backend.app.algorithms.voting_ensemble import settings as settings_facade
from backend.app.algorithms.voting_ensemble.runtime.commands import manual_evaluation_command
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService
from backend.app.algorithms.voting_ensemble.trading_settings.legacy import legacy_multi_timeframe_compatibility_config
from backend.app.algorithms.voting_ensemble.trading_settings.models import VotingEnsembleOneMinuteSettings
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import dynamic_risk_config, resolve_one_minute_trading_settings
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


class VotingEnsembleTradingSettingsTest(unittest.TestCase):
    def test_resolved_settings_are_typed_immutable_and_include_required_metadata(self) -> None:
        resolved = resolve_one_minute_trading_settings({})

        self.assertIsInstance(resolved, VotingEnsembleOneMinuteSettings)
        self.assertEqual(resolved.algorithmId, "voting_ensemble")
        self.assertEqual(resolved.sourceBaselineVersion, "voting_ensemble_baseline_settings_v1")
        self.assertEqual(resolved.profileVersion, "voting_ensemble_trading_profile_v1")
        self.assertEqual(resolved.appliedOverlays, ("baseline",))
        self.assertTrue(resolved.configurationHash)
        self.assertTrue(resolved.resolutionTimestamp)
        self.assertIn("voting_ensemble.trading_settings.one_minute_resolved", resolved.reasonCodes)
        with self.assertRaises(ValidationError):
            resolved.paperExecutionMode = resolved.paperExecutionMode.model_copy(update={"paperOnly": False})  # type: ignore[misc]

    def test_current_one_minute_baseline_values_are_migrated_without_hourly_daily_weekly_keys(self) -> None:
        config = dynamic_risk_config({})

        self.assertEqual(config["startingCapital"], 25000.0)
        self.assertEqual(config["riskPerTradePercent"], 0.5)
        self.assertEqual(config["maxDailyLossPercent"], 2.0)
        self.assertEqual(config["maxTradesPerDay"], 3)
        self.assertEqual(config["sessionStart"], "09:35")
        self.assertEqual(config["newTradesUntil"], "15:30")
        self.assertEqual(config["forceClose"], "15:55")
        self.assertEqual(config["stopLossPercent"], 0.35)
        self.assertEqual(config["fixedStopDistanceDollars"], 1.0)
        self.assertEqual(config["takeProfitR"], 1.5)
        self.assertEqual(config["slippagePerShare"], 0.02)
        self.assertEqual(config["entryConfirmationBars"], 3)
        self.assertEqual(config["warmupBars"], 50)
        self.assertEqual(
            config["allowedEntryHours"],
            ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
        )
        serialized = json.dumps(config, sort_keys=True)
        for forbidden in ("1Hour", "1Day", "1Week", "hybridOneHour", "swing", "openCloseEvents", "directionalWinnerMinVotesByTimeframe"):
            self.assertNotIn(forbidden, serialized)

    def test_legacy_multitimeframe_data_is_available_only_as_labelled_compatibility(self) -> None:
        legacy = legacy_multi_timeframe_compatibility_config()

        self.assertIn("hybridOneHour", legacy)
        self.assertIn("swing", legacy)
        self.assertIn("1Day", legacy["swing"])
        self.assertIn("1Week", legacy["swing"])
        self.assertNotIn("hybridOneHour", settings_facade.VOTING_ENSEMBLE_RISK_CONFIG)
        self.assertIn("hybridOneHour", settings_facade.VOTING_ENSEMBLE_LEGACY_MULTI_TIMEFRAME_CONFIG)

    def test_facade_imports_continue_to_work(self) -> None:
        config = settings_facade.dynamic_risk_config({"tradingProfile": "reduced"})
        profile = settings_facade.resolve_dynamic_trading_profile({"tradingProfile": "reduced"})

        self.assertEqual(config["algorithmId"], "voting_ensemble")
        self.assertIn("manual.reduced", config["appliedOverlays"])
        self.assertEqual(profile["profileId"], "dynamic-manual_reduced")
        self.assertEqual(settings_facade.risk_config_hash(config), config["configurationHash"])

    def test_invalid_one_minute_settings_fail_validation(self) -> None:
        with self.assertRaises(ValueError):
            resolve_one_minute_trading_settings({"swing": {"1Day": {"takeProfitR": 2.0}}})
        with self.assertRaises(ValidationError):
            VotingEnsembleOneMinuteSettings.model_validate(
                {
                    **resolve_one_minute_trading_settings({}).model_dump(mode="json"),
                    "paperExecutionMode": {
                        "paperOnly": False,
                        "liveTradingEnabled": True,
                        "executionAdapter": "live",
                    },
                }
            )

    def test_settings_hashes_are_deterministic_and_drive_runtime_idempotency(self) -> None:
        first = resolve_one_minute_trading_settings({"tradingProfile": "defensive"})
        second = resolve_one_minute_trading_settings({"tradingProfile": "defensive"})
        first_config = dynamic_risk_config({"tradingProfile": "defensive"})
        second_config = dynamic_risk_config({"tradingProfile": "defensive"})

        self.assertEqual(first.configurationHash, second.configurationHash)
        self.assertEqual(first_config["configurationHash"], second_config["configurationHash"])
        command = manual_evaluation_command(
            {
                "symbol": "SPY",
                "data_timestamp": "2026-01-05T14:30:00+00:00",
                "candles": [
                    {"timestamp": "2026-01-05T14:30:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
                ],
                "settings": {"tradingProfile": "defensive"},
            }
        )

        self.assertEqual(command.settingsHash, first.configurationHash)

    def test_dynamic_profile_applies_conservative_bounded_overlays(self) -> None:
        payload = {
            "volatility": "high",
            "liquidity": "thin",
            "spreadBps": 22.0,
            "expectedTransactionCostBps": 10.0,
            "eventRisk": "high",
            "dataQuality": "degraded",
            "marketDataAgeSeconds": 35,
            "executionLatencyMs": 1200,
            "timeOfDay": "15:25",
            "currentDrawdownPercent": 2.2,
            "consecutiveLosses": 2,
            "currentExposurePercent": 30,
            "strategyFamilySupport": 1,
            "voteEdge": 0.21,
        }

        resolved = resolve_one_minute_trading_settings(payload)
        profile = resolved.resolvedTradingProfile

        self.assertEqual(profile.entryPermission, "allow_new_entries")
        self.assertFalse(profile.entriesBlocked)
        self.assertEqual(profile.riskMultiplier, 0.35)
        self.assertEqual(resolved.riskPerTrade.riskPerTradePercent, 0.175)
        self.assertEqual(resolved.positionNotionalCap.orderAllocationPercent, 5.0)
        self.assertEqual(resolved.maximumTrades.maxTradesPerDay, 1)
        self.assertEqual(profile.minimumFinalScore, 0.30)
        self.assertEqual(profile.minimumIndependentFamilySupport, 2)
        self.assertEqual(profile.minimumEdgeToCostRatio, 2.5)
        self.assertEqual(profile.maximumSpreadBps, 18.0)
        self.assertEqual(resolved.slippageLimits.slippagePerShare, 0.04)
        self.assertEqual(profile.cancelReplaceTimeoutSeconds, 30)
        self.assertEqual(profile.cooldownSeconds, 300)
        self.assertEqual(profile.maximumHoldingMinutes, 15)
        self.assertIn("volatility.high", profile.activeOverlays)
        self.assertIn("transaction_cost.elevated", profile.activeOverlays)

    def test_blocking_overlay_blocks_entries_but_keeps_exit_management_enabled(self) -> None:
        resolved = resolve_one_minute_trading_settings({"marketDataAgeSeconds": 65, "executionLatencyMs": 2500})
        profile = resolved.resolvedTradingProfile

        self.assertTrue(resolved.entriesBlocked)
        self.assertEqual(profile.entryPermission, "block_new_entries")
        self.assertTrue(profile.exitManagementEnabled)
        self.assertEqual(resolved.riskPerTrade.riskPerTradePercent, 0.0)
        self.assertEqual(resolved.positionNotionalCap.orderAllocationPercent, 0.0)
        self.assertEqual(resolved.maximumTrades.maxTradesPerDay, 0)
        self.assertIn("market_data_age.stale", profile.activeOverlays)
        self.assertIn("execution_latency.blocked", profile.activeOverlays)

    def test_dynamic_profile_hashes_are_deterministic(self) -> None:
        payload = {"volatility": "high", "spreadBps": 22.0, "voteEdge": 0.21}

        first = resolve_one_minute_trading_settings(payload)
        second = resolve_one_minute_trading_settings(payload)

        self.assertEqual(first.configurationHash, second.configurationHash)
        self.assertEqual(first.resolvedTradingProfile.model_dump(mode="json"), second.resolvedTradingProfile.model_dump(mode="json"))

    def test_service_persists_resolved_profile_with_decision(self) -> None:
        payload = snapshot_payload(candles(30))
        payload["settings"] = {"volatility": "high", "spreadBps": 22.0, "strategyFamilySupport": 1, "voteEdge": 0.21}

        result = VotingEnsembleService().evaluate(payload)

        self.assertIn("resolved_trading_profile", result)
        profile = result["resolved_trading_profile"]
        self.assertEqual(profile["minimumIndependentFamilySupport"], 2)
        self.assertEqual(profile["minimumFinalScore"], 0.25)
        self.assertIn("volatility.high", profile["activeOverlays"])


if __name__ == "__main__":
    unittest.main()
