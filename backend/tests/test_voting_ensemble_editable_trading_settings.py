"""The operator-editable trading settings, and the promise that editing one changes trading.

The point of these tests is the last part. A settings editor whose values never reach the
algorithm is worse than none, because it reads as configuration; so each group has a test
that follows an override from the store through to the resolved value the pipeline reads.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.voting_ensemble.trading_settings.editable import (
    EDITABLE_ONE_MINUTE_FIELDS,
    INERT_ONE_MINUTE_PARAMETERS,
    editable_field,
)
from backend.app.algorithms.voting_ensemble.trading_settings.baseline import one_minute_baseline_settings
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.app.algorithms.voting_ensemble.trading_settings.store import (
    VotingEnsembleTradingSettingsRepository,
    merged_settings_payload,
    override_validation_errors,
    set_trading_settings_repository,
    split_known_override_keys,
    stored_trading_settings_overrides,
)
from backend.app.algorithms.voting_ensemble.trading_settings.view import trading_settings_view


class TemporaryOverrideStore(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.repository = VotingEnsembleTradingSettingsRepository(Path(self._directory.name) / "trading_settings.json")
        set_trading_settings_repository(self.repository)
        self.addCleanup(set_trading_settings_repository, None)


class EditableFieldRegistryTest(unittest.TestCase):
    def test_every_editable_field_exists_in_the_baseline(self) -> None:
        """A field the baseline does not carry could never be applied."""
        baseline = one_minute_baseline_settings()
        for spec in EDITABLE_ONE_MINUTE_FIELDS:
            cursor = baseline
            for segment in spec.path:
                self.assertIsInstance(cursor, dict, f"{spec.key} is not addressable in the baseline")
                self.assertIn(segment, cursor, f"{spec.key} is not in the baseline")
                cursor = cursor[segment]

    def test_every_editable_field_declares_where_it_binds(self) -> None:
        for spec in EDITABLE_ONE_MINUTE_FIELDS:
            self.assertTrue(spec.bindsAt.strip(), f"{spec.key} does not name a read site")
            self.assertIn(spec.binding, {"live", "backtest", "both"})
            self.assertTrue(spec.help.strip(), f"{spec.key} has no operator-facing help")

    def test_parameters_documented_inert_are_never_offered_as_editable(self) -> None:
        """BASELINES.md records these as consumed by nothing. Offering them would lie."""
        editable_keys = {spec.key for spec in EDITABLE_ONE_MINUTE_FIELDS}
        for item in INERT_ONE_MINUTE_PARAMETERS:
            self.assertNotIn(item.key, editable_keys, f"{item.key} is documented inert but offered for editing")
            self.assertTrue(item.why.strip())

    def test_numeric_fields_carry_bounds(self) -> None:
        for spec in EDITABLE_ONE_MINUTE_FIELDS:
            if spec.kind in {"number", "integer"}:
                self.assertIsNotNone(spec.minimum, f"{spec.key} has no minimum")
                self.assertIsNotNone(spec.maximum, f"{spec.key} has no maximum")
                self.assertLess(float(spec.minimum), float(spec.maximum))
            if spec.kind == "choice":
                self.assertTrue(spec.choices, f"{spec.key} is a choice with no options")


class ResolverOverrideTest(unittest.TestCase):
    def test_no_overrides_resolve_to_the_recorded_reference_baseline(self) -> None:
        """The recorded baseline hash must not move just because overrides became possible."""
        self.assertEqual(resolve_one_minute_trading_settings({}).configurationHash, "54fae2592a09797b")

    def test_every_editable_field_changes_the_configuration_hash(self) -> None:
        """A field that cannot move the hash cannot be reaching the pipeline either."""
        baseline_hash = resolve_one_minute_trading_settings({}).configurationHash
        for spec in EDITABLE_ONE_MINUTE_FIELDS:
            with self.subTest(field=spec.key):
                resolved = resolve_one_minute_trading_settings({spec.key: _distinct_value(spec)})
                self.assertNotEqual(resolved.configurationHash, baseline_hash, f"{spec.key} did not change the settings")

    def test_order_geometry_overrides_reach_the_stop_and_target_policies(self) -> None:
        resolved = resolve_one_minute_trading_settings(
            {
                "stopAtrMultiplier": 2.5,
                "takeProfitR": 3.0,
                "minimumTakeProfitR": 1.5,
                "breakevenTriggerR": 0.5,
                "trailingStopR": 0.75,
                "trailingStopEnabled": False,
                "structuralTargets": False,
                "maximumHoldingMinutes": 45,
            }
        )

        self.assertEqual(resolved.stopPolicy.atrMultiplier, 2.5)
        self.assertEqual(resolved.targetPolicy.takeProfitR, 3.0)
        self.assertEqual(resolved.targetPolicy.minimumTakeProfitR, 1.5)
        self.assertEqual(resolved.stopPolicy.breakevenTriggerR, 0.5)
        self.assertEqual(resolved.stopPolicy.trailingStopR, 0.75)
        self.assertFalse(resolved.stopPolicy.trailingEnabled)
        self.assertFalse(resolved.targetPolicy.structuralTargets)
        self.assertEqual(resolved.holdingTimePolicy.maximumHoldingMinutes, 45)

    def test_decision_threshold_overrides_reach_the_aggregation_and_profile(self) -> None:
        resolved = resolve_one_minute_trading_settings(
            {
                "minVoteEdge": 0.35,
                "minEligibleDirectionalVotes": 3,
                "minimumFamiliesForTrade": 3,
                "reliabilityWeightingMode": "shadow",
                "reliabilitySampleWindow": "rolling_20_trades",
                "minimumEdgeToCostRatio": 1.4,
                "maximumSpreadBps": 12.5,
            }
        )

        self.assertEqual(resolved.aggregationThresholds.minVoteEdge, 0.35)
        self.assertEqual(resolved.aggregationThresholds.minEligibleDirectionalVotes, 3)
        self.assertEqual(resolved.aggregationThresholds.reliabilityWeightingMode, "shadow")
        self.assertEqual(resolved.aggregationThresholds.reliabilitySampleWindow, "rolling_20_trades")
        self.assertEqual(resolved.minimumFamilySupport.minimumFamiliesForTrade, 3)
        # The family engine and the local gate both read the profile, not the raw config.
        self.assertEqual(resolved.resolvedTradingProfile.minimumFinalScore, 0.35)
        self.assertEqual(resolved.resolvedTradingProfile.minimumIndependentFamilySupport, 3)
        self.assertEqual(resolved.resolvedTradingProfile.minimumEdgeToCostRatio, 1.4)
        self.assertEqual(resolved.resolvedTradingProfile.maximumSpreadBps, 12.5)

    def test_family_weights_accept_the_nested_and_the_dotted_form(self) -> None:
        nested = resolve_one_minute_trading_settings({"familyWeights": {"trend": 1.5}})
        dotted = resolve_one_minute_trading_settings({"familyWeights.trend": 1.5})

        self.assertEqual(nested.minimumFamilySupport.familyWeights["trend"], 1.5)
        self.assertEqual(nested.configurationHash, dotted.configurationHash)
        # Untouched families keep the baseline weight rather than being dropped.
        self.assertEqual(nested.minimumFamilySupport.familyWeights["breakout"], 1.0)

    def test_out_of_range_values_are_clamped_rather_than_raised_on(self) -> None:
        """The resolver runs on every bar; one bad value must not fail an evaluation."""
        resolved = resolve_one_minute_trading_settings({"riskPerTradePercent": 10_000.0, "stopAtrMultiplier": -5.0})

        self.assertEqual(resolved.riskPerTrade.riskPerTradePercent, 100.0)
        self.assertEqual(resolved.stopPolicy.atrMultiplier, 0.0)

    def test_unreadable_values_fall_back_to_the_baseline(self) -> None:
        resolved = resolve_one_minute_trading_settings({"takeProfitR": "not a number", "trailingStopEnabled": "maybe"})

        self.assertEqual(resolved.targetPolicy.takeProfitR, 1.5)
        self.assertTrue(resolved.stopPolicy.trailingEnabled)

    def test_legacy_sizing_keys_are_ignored_not_rejected(self) -> None:
        """An older dashboard still sends these; sizing never read either of them."""
        resolved = resolve_one_minute_trading_settings({"riskBudgetPercentOfOrder": 50, "positionSizingMode": "allocation"})

        self.assertEqual(resolved.configurationHash, "54fae2592a09797b")

    def test_a_positive_trade_cap_is_still_limited_by_what_the_daily_allocation_funds(self) -> None:
        resolved = resolve_one_minute_trading_settings(
            {"maxTradesPerDay": 40, "orderAllocationPercent": 10.0, "dailyAllocationPercent": 30.0}
        )

        self.assertEqual(resolved.maximumTrades.maxTradesPerDay, 3)


class OverrideStoreTest(TemporaryOverrideStore):
    def test_saved_overrides_are_visible_to_the_trading_paths(self) -> None:
        self.repository.save({"stopAtrMultiplier": 2.5}, updated_by="test", reason="unit")

        self.assertEqual(stored_trading_settings_overrides(), {"stopAtrMultiplier": 2.5})
        self.assertEqual(resolve_one_minute_trading_settings(stored_trading_settings_overrides()).stopPolicy.atrMultiplier, 2.5)

    def test_overrides_survive_a_new_repository_on_the_same_file(self) -> None:
        self.repository.save({"takeProfitR": 2.25}, updated_by="test", reason="unit")
        reopened = VotingEnsembleTradingSettingsRepository(self.repository.path)

        self.assertEqual(reopened.load_overrides(), {"takeProfitR": 2.25})

    def test_a_caller_payload_wins_over_a_stored_override(self) -> None:
        """A backtest sweep pins its own values; everything it omits follows the operator."""
        self.repository.save({"takeProfitR": 2.25, "stopAtrMultiplier": 2.0}, updated_by="test", reason="unit")

        merged = merged_settings_payload({"takeProfitR": 4.0})

        self.assertEqual(merged["takeProfitR"], 4.0)
        self.assertEqual(merged["stopAtrMultiplier"], 2.0)

    def test_a_corrupt_store_leaves_the_algorithm_on_the_baseline(self) -> None:
        self.repository.path.parent.mkdir(parents=True, exist_ok=True)
        self.repository.path.write_text("{ not json", encoding="utf-8")

        record = self.repository.load_record()

        self.assertEqual(record["overrides"], {})
        self.assertIn("voting_ensemble.trading_settings.override_file_unreadable_baseline_used", record["reasonCodes"])
        self.assertEqual(resolve_one_minute_trading_settings(stored_trading_settings_overrides()).configurationHash, "54fae2592a09797b")

    def test_clearing_returns_the_configuration_to_the_baseline(self) -> None:
        self.repository.save({"takeProfitR": 2.25}, updated_by="test", reason="unit")
        self.repository.clear(updated_by="test")

        self.assertEqual(stored_trading_settings_overrides(), {})
        self.assertEqual(resolve_one_minute_trading_settings(stored_trading_settings_overrides()).configurationHash, "54fae2592a09797b")

    def test_unknown_keys_are_reported_and_legacy_keys_are_dropped_quietly(self) -> None:
        accepted, ignored = split_known_override_keys(
            {"takeProfitR": 2.0, "riskBudgetPercentOfOrder": 50, "totallyMadeUp": 1}
        )

        self.assertEqual(accepted, {"takeProfitR": 2.0})
        self.assertEqual(ignored, ["totallyMadeUp"])

    def test_validation_reports_the_field_and_the_bound_it_broke(self) -> None:
        errors = override_validation_errors(
            {"riskPerTradePercent": 500.0, "reliabilityWeightingMode": "nope", "sessionStart": "9am", "trailingStopEnabled": "yes"}
        )

        self.assertEqual(len(errors), 4)
        self.assertTrue(any("riskPerTradePercent" in item for item in errors))
        self.assertTrue(any("reliabilityWeightingMode" in item for item in errors))
        self.assertTrue(any("sessionStart" in item for item in errors))
        self.assertTrue(any("trailingStopEnabled" in item for item in errors))


class TradingSettingsViewTest(TemporaryOverrideStore):
    def test_the_view_reports_the_baseline_when_nothing_is_overridden(self) -> None:
        view = trading_settings_view(self.repository.load_record())

        self.assertTrue(view["matchesBaseline"])
        self.assertEqual(view["overriddenKeys"], [])
        self.assertEqual([group["id"] for group in view["groups"]], ["tradingSettings", "targetOrder", "defaultSettings"])
        self.assertTrue(view["inertParameters"])

    def test_the_view_shows_the_clamped_value_that_will_actually_trade(self) -> None:
        """Echoing what the operator typed would show a number the algorithm never uses."""
        self.repository.save({"maxTradesPerDay": 40}, updated_by="test", reason="unit")

        view = trading_settings_view(self.repository.load_record())
        field = _field(view, "maxTradesPerDay")

        self.assertEqual(field["value"], 3)
        self.assertEqual(field["baseline"], 0)
        self.assertTrue(field["overridden"])

    def test_the_view_hash_matches_what_the_pipeline_resolves(self) -> None:
        self.repository.save({"takeProfitR": 2.25}, updated_by="test", reason="unit")

        view = trading_settings_view(self.repository.load_record())

        self.assertFalse(view["matchesBaseline"])
        self.assertEqual(view["configurationHash"], resolve_one_minute_trading_settings(stored_trading_settings_overrides()).configurationHash)


class TradingSettingsApiTest(TemporaryOverrideStore):
    def setUp(self) -> None:
        super().setUp()
        from backend.app.main import app

        self.client = TestClient(app)

    def test_get_returns_the_editable_catalog_with_current_values(self) -> None:
        response = self.client.get("/api/voting-ensemble/trading-settings")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["algorithmId"], "voting_ensemble")
        self.assertTrue(payload["matchesBaseline"])
        self.assertEqual(_field(payload, "stopAtrMultiplier")["value"], 1.5)

    def test_put_persists_the_override_and_the_next_resolution_reads_it(self) -> None:
        response = self.client.put(
            "/api/voting-ensemble/trading-settings",
            json={"overrides": {"stopAtrMultiplier": 2.5, "takeProfitR": 2.0}, "updatedBy": "test", "reason": "widen the stop"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["matchesBaseline"])
        self.assertEqual(sorted(payload["overriddenKeys"]), ["stopAtrMultiplier", "takeProfitR"])
        self.assertEqual(payload["updatedBy"], "test")
        self.assertEqual(payload["reason"], "widen the stop")

        # The promise: what the trading paths resolve now carries the edit.
        resolved = resolve_one_minute_trading_settings(stored_trading_settings_overrides())
        self.assertEqual(resolved.stopPolicy.atrMultiplier, 2.5)
        self.assertEqual(resolved.targetPolicy.takeProfitR, 2.0)

    def test_put_refuses_an_out_of_range_value_instead_of_clamping_it(self) -> None:
        response = self.client.put(
            "/api/voting-ensemble/trading-settings",
            json={"overrides": {"riskPerTradePercent": 500.0}},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("riskPerTradePercent", str(response.json()["detail"]["errors"]))
        self.assertEqual(stored_trading_settings_overrides(), {})

    def test_put_refuses_a_combination_the_settings_cannot_validate(self) -> None:
        """Order allocation above daily allocation would fail on every later evaluation."""
        response = self.client.put(
            "/api/voting-ensemble/trading-settings",
            json={"overrides": {"orderAllocationPercent": 60.0, "dailyAllocationPercent": 20.0}},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["reasonCodes"], ["voting_ensemble.trading_settings.invalid_combination"])
        self.assertEqual(stored_trading_settings_overrides(), {})

    def test_put_reset_returns_to_the_baseline(self) -> None:
        self.client.put("/api/voting-ensemble/trading-settings", json={"overrides": {"takeProfitR": 2.0}})

        response = self.client.put("/api/voting-ensemble/trading-settings", json={"reset": True})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["matchesBaseline"])
        self.assertEqual(stored_trading_settings_overrides(), {})

    def test_put_reports_an_unknown_key_rather_than_storing_it(self) -> None:
        response = self.client.put(
            "/api/voting-ensemble/trading-settings",
            json={"overrides": {"takeProfitR": 2.0, "totallyMadeUp": 3}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("totallyMadeUp", response.json()["ignoredKeys"])
        self.assertEqual(stored_trading_settings_overrides(), {"takeProfitR": 2.0})


def _field(view: dict, key: str) -> dict:
    for group in view["groups"]:
        for field in group["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError(f"{key} is not in the settings view")


def _distinct_value(spec) -> object:
    """A value for `spec` that differs from its baseline and stays inside its bounds."""
    baseline = one_minute_baseline_settings()
    cursor = baseline
    for segment in spec.path:
        cursor = cursor[segment]
    if spec.kind == "boolean":
        return not bool(cursor)
    if spec.kind == "choice":
        return next(choice for choice in spec.choices if choice != cursor)
    if spec.kind == "time":
        # Keep sessionStart < newTradesUntil < forceClose so validation still passes.
        return {"sessionStart": "09:40", "newTradesUntil": "15:20", "forceClose": "15:50"}[spec.key]
    if spec.key == "orderAllocationPercent":
        # Must stay at or below the daily allocation for the settings to validate.
        return 5.0
    step = float(spec.step or 1.0)
    candidate = float(cursor) + step
    if spec.maximum is not None and candidate > float(spec.maximum):
        candidate = float(cursor) - step
    if spec.kind == "integer":
        return int(round(candidate))
    return candidate


if __name__ == "__main__":
    unittest.main()
