"""Cover model-artifact path handling across a relocated repository.

Forecast artifacts used to record the saved booster as an absolute path. Moving the
project (as the move off OneDrive did) invalidated every artifact at once: the loader's
existence check failed, ``select_approved_forecast_artifact`` returned None, and the
card silently fell back to MODEL_UNAVAILABLE with no error anywhere.

Artifacts now record a reference relative to the forecast data root, and the resolver
still accepts the absolute paths older artifacts contain.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.market_forecast import (
    FORECAST_CANDIDATE_ARTIFACT_DIR,
    MODEL_ARTIFACT_DIR,
    forecast_model_file_reference,
    resolve_forecast_model_file,
)


class ForecastModelFileReferenceTest(unittest.TestCase):
    def test_path_under_the_data_root_is_recorded_relatively(self) -> None:
        reference = forecast_model_file_reference(FORECAST_CANDIDATE_ARTIFACT_DIR / "abc.xgboost.json")

        self.assertEqual(reference, "artifacts/candidates/abc.xgboost.json")
        self.assertFalse(Path(reference).is_absolute())

    def test_path_outside_the_data_root_is_left_absolute(self) -> None:
        outside = Path(__file__).resolve().parent / "elsewhere.xgboost.json"

        self.assertEqual(forecast_model_file_reference(outside), str(outside))


class ResolveForecastModelFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model_path = FORECAST_CANDIDATE_ARTIFACT_DIR / "unit_test_model.xgboost.json"
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: self.model_path.unlink(missing_ok=True))

    def test_relative_reference_resolves_against_the_data_root(self) -> None:
        resolved = resolve_forecast_model_file("artifacts/candidates/unit_test_model.xgboost.json")

        self.assertTrue(resolved.is_file())

    def test_absolute_path_still_resolves_for_older_artifacts(self) -> None:
        resolved = resolve_forecast_model_file(str(self.model_path))

        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved, self.model_path)

    def test_absolute_path_from_a_previous_location_falls_back_by_filename(self) -> None:
        stale = Path(r"C:\Users\someone\OneDrive\docs\Trading\backend\data\market_forecast\artifacts\candidates") / self.model_path.name

        resolved = resolve_forecast_model_file(str(stale))

        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, self.model_path.name)

    def test_unknown_filename_yields_no_file(self) -> None:
        self.assertFalse(resolve_forecast_model_file("artifacts/candidates/does_not_exist.xgboost.json").is_file())

    def test_blank_and_none_yield_no_file(self) -> None:
        self.assertFalse(resolve_forecast_model_file("").is_file())
        self.assertFalse(resolve_forecast_model_file(None).is_file())

    def test_reference_then_resolve_round_trips(self) -> None:
        reference = forecast_model_file_reference(self.model_path)

        self.assertFalse(Path(reference).is_absolute())
        self.assertEqual(resolve_forecast_model_file(reference).resolve(), self.model_path.resolve())

    def test_resolution_is_anchored_to_the_forecast_data_root(self) -> None:
        resolved = resolve_forecast_model_file("artifacts/candidates/unit_test_model.xgboost.json")

        self.assertTrue(str(resolved.resolve()).startswith(str(MODEL_ARTIFACT_DIR.resolve())))


if __name__ == "__main__":
    unittest.main()
