from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.migration import (
    LEGACY_STRATEGY_PERFORMANCE_KEY,
    LEGACY_UI_STATE_KEY,
    LEGACY_WEIGHT_STATE_KEY,
    LEGACY_WEIGHTED_ORDER_CONTROL_MODES_KEY,
    LEGACY_WEIGHTED_SETTINGS_KEY,
    LEGACY_WEIGHTED_TRADE_HISTORY_KEY,
    WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY,
    WEIGHTED_VOTING_MIGRATION_RECORD_KEY,
    migrate_existing_weighted_voting_state,
)
from backend.app.algorithms.weighted_voting.persistence import WEIGHTED_VOTING_SETTINGS_KEY


MIGRATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class WeightedVotingMigrationTest(unittest.TestCase):
    def test_migration_preserves_compatible_settings_and_archives_untrusted_weights(self) -> None:
        store = MemoryStore()
        result = migrate_existing_weighted_voting_state(store=store, legacy_state=legacy_browser_state(), migrated_at=MIGRATED_AT)

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.settings_migrated)
        self.assertTrue(result.active_weights_initialized)
        self.assertTrue(result.untrusted_weight_history_archived)
        self.assertEqual(result.trade_records_migrated, 1)
        self.assertTrue(result.ui_preferences_migrated)

        settings = store.snapshots[WEIGHTED_VOTING_SETTINGS_KEY]
        self.assertEqual(settings["default_settings"]["base_risk_per_trade_percent"], 0.75)
        self.assertEqual(settings["default_settings"]["order_allocation_percent"], 8.0)
        self.assertEqual(settings["default_settings"]["maximum_shares"], 222)
        self.assertEqual(settings["maximum_spread_percent"], 0.002)
        self.assertIn("weighted_voting.settings.defaults_visible", settings["reason_codes"])

        active_weights = store.snapshots[WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY]
        self.assertEqual(active_weights["state_status"], "UNSEEDED_EQUAL_WEIGHTS")
        self.assertEqual(active_weights["reason_codes"], ["weighted_voting.weights.unseeded_equal"])
        self.assertTrue(all(weight == 0.125 for weight in active_weights["strategy_weights"].values()))

        archive_keys = [key for key in store.snapshots if key.startswith("weighted_voting.migration.archive.weight_history.")]
        self.assertEqual(len(archive_keys), 1)
        self.assertEqual(store.snapshots[archive_keys[0]]["trust_status"], "untrusted")
        self.assertIn("weighted_voting.migration.weight_history_missing_provenance", store.snapshots[archive_keys[0]]["reason_codes"])

    def test_migration_is_idempotent_and_does_not_duplicate_promotions(self) -> None:
        store = MemoryStore()
        first = migrate_existing_weighted_voting_state(store=store, legacy_state=legacy_browser_state(), migrated_at=MIGRATED_AT)
        write_count_after_first = dict(store.write_counts)
        second = migrate_existing_weighted_voting_state(store=store, legacy_state=legacy_browser_state(), migrated_at=MIGRATED_AT)

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "idempotent_noop")
        self.assertEqual(store.write_counts, write_count_after_first)
        self.assertEqual(store.write_counts[WEIGHTED_VOTING_MIGRATION_RECORD_KEY], 1)

    def test_other_algorithm_state_is_not_migrated_into_weighted_voting(self) -> None:
        state = legacy_browser_state()
        state["browserStorage"].update(
            {
                "weighted-confidence-trading-settings-v1": json.dumps({"baseRiskPercent": 99}),
                "regime-selection-trading-settings-v1": json.dumps({"baseRiskPercent": 88}),
                "meta-strategy-trading-settings-v1": json.dumps({"baseRiskPercent": 77}),
                "trading-dashboard.confidence-trade-history.v1": json.dumps([{"id": "confidence-state", "side": "Buy", "quantity": 1, "price": 1, "recordedAt": MIGRATED_AT.isoformat()}]),
            }
        )
        store = MemoryStore()

        migrate_existing_weighted_voting_state(store=store, legacy_state=state, migrated_at=MIGRATED_AT)

        serialized = json.dumps(store.snapshots, sort_keys=True)
        self.assertNotIn("confidence-state", serialized)
        self.assertNotIn("regime-selection", serialized)
        self.assertNotIn("meta-strategy", serialized)
        self.assertEqual(store.snapshots[WEIGHTED_VOTING_SETTINGS_KEY]["default_settings"]["base_risk_per_trade_percent"], 0.75)

    def test_trustworthy_weight_history_is_preserved_but_not_activated(self) -> None:
        trustworthy = {
            "algorithm_id": "weighted_voting",
            "weight_version": "trusted-wf-v1",
            "state_status": "BACKTEST_SEEDED",
            "strategy_weights": {"S1": 0.25, "S2": 0.15, "S3": 0.10, "S4": 0.10, "S5": 0.10, "S6": 0.10, "S7": 0.10, "S8": 0.10},
            "last_updated_at": MIGRATED_AT.isoformat(),
            "data_timestamp": MIGRATED_AT.isoformat(),
            "data_manifest_hash": "manifest-trusted",
            "configuration_hash": "config-trusted",
            "provenance": {"source": "weighted_voting_walk_forward_v2", "data_manifest_hash": "manifest-trusted"},
            "explanation": "Trusted walk-forward state from the Weighted Voting backend.",
        }
        state = legacy_browser_state(weight_payload=trustworthy, performance_payload=None)
        store = MemoryStore()

        result = migrate_existing_weighted_voting_state(store=store, legacy_state=state, migrated_at=MIGRATED_AT)

        self.assertTrue(result.trustworthy_performance_migrated)
        self.assertIn("weighted_voting.weights.history.trusted-wf-v1", store.snapshots)
        self.assertEqual(store.snapshots[WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY]["state_status"], "UNSEEDED_EQUAL_WEIGHTS")
        self.assertTrue(all(weight == 0.125 for weight in store.snapshots[WEIGHTED_VOTING_ACTIVE_WEIGHT_STATE_KEY]["strategy_weights"].values()))


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}
        self.write_counts: dict[str, int] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot
        self.write_counts[key] = self.write_counts.get(key, 0) + 1


def legacy_browser_state(weight_payload: dict | None = None, performance_payload: dict | None = None) -> dict:
    if weight_payload is None:
        weight_payload = {
            "weights": {"S1": 0.90, "S2": 0.02, "S3": 0.02, "S4": 0.02, "S5": 0.01, "S6": 0.01, "S7": 0.01, "S8": 0.01},
            "source": "daily",
            "lastUpdatedDate": "2026-07-14",
        }
    if performance_payload is None:
        performance_payload = {"S1": {"tradeCount": 3, "winRate": 1.0}}
    return {
        "browserStorage": {
            LEGACY_WEIGHTED_SETTINGS_KEY: json.dumps(
                {
                    "baseRiskPercent": 0.75,
                    "orderAllocationPercent": 8,
                    "maxAllowedShares": 222,
                    "maximumSpreadPercent": 0.2,
                    "minimumOneMinuteVolume": 15000,
                    "takeProfitR": 2.5,
                }
            ),
            LEGACY_WEIGHT_STATE_KEY: json.dumps(weight_payload),
            LEGACY_STRATEGY_PERFORMANCE_KEY: json.dumps(performance_payload) if performance_payload is not None else "",
            LEGACY_WEIGHTED_TRADE_HISTORY_KEY: json.dumps(
                [
                    {
                        "id": "weighted-buy-1",
                        "side": "Buy",
                        "quantity": 7,
                        "price": 100.25,
                        "symbol": "SPY",
                        "recordedAt": MIGRATED_AT.isoformat(),
                    },
                    {
                        "id": "bad-other-row",
                        "algorithmId": "confidence_aggregation",
                        "side": "Buy",
                        "quantity": 1,
                        "price": 1,
                        "recordedAt": MIGRATED_AT.isoformat(),
                    },
                ]
            ),
            LEGACY_UI_STATE_KEY: json.dumps(
                {
                    "algoTab": "weighted",
                    "tradingWindowMode": "weighted",
                    "weightedVotingExpanded": True,
                    "weightedControlsExpanded": False,
                    "confidenceRequirementsExpanded": True,
                }
            ),
            LEGACY_WEIGHTED_ORDER_CONTROL_MODES_KEY: json.dumps({"Buy": "Manual", "Sell": "Automatic"}),
        }
    }


if __name__ == "__main__":
    unittest.main()
