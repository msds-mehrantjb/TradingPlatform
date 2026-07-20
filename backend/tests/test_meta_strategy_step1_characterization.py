from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import (
    REQUIRED_CHARACTERIZATION_FIXTURES,
    build_current_behavior_characterization,
    meta_strategy_version_compatibility,
)


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "meta_strategy_current_behavior.json"


class MetaStrategyStep1CharacterizationTest(unittest.TestCase):
    maxDiff = None

    def test_current_behavior_fixture_catalog_is_complete_and_sanitized(self) -> None:
        payload = load_fixture_payload()

        self.assertEqual(payload["schemaVersion"], "meta_strategy_current_behavior_characterization_v1")
        self.assertEqual(payload["algorithmId"], "meta_strategy")
        self.assertTrue(meta_strategy_version_compatibility(payload)["valid"])
        self.assertEqual({fixture["id"] for fixture in payload["fixtures"]}, REQUIRED_CHARACTERIZATION_FIXTURES)
        self.assertFalse(payload["dataPolicy"]["containsCredentials"])
        self.assertFalse(payload["dataPolicy"]["containsPrivateAccountInformation"])
        self.assertFalse(payload["dataPolicy"]["requiresLiveFeed"])

        serialized = json.dumps(payload).lower()
        for forbidden in ("api_key", "secret", "alpaca_key", "oauth", "token"):
            self.assertNotIn(forbidden, serialized)

    def test_current_behavior_fixtures_match_legacy_outputs(self) -> None:
        expected = load_fixture_payload()

        first = build_current_behavior_characterization()
        second = build_current_behavior_characterization()

        self.assertEqual(first, second, "Meta-Strategy current-behavior generation is not deterministic.")
        self.assertEqual(first, expected)

    def test_each_fixture_records_required_phase_1_outputs(self) -> None:
        payload = load_fixture_payload()
        required_fields = {
            "directionalStrategyOutputs",
            "contextOutputs",
            "regimeOutput",
            "familyScores",
            "deterministicCandidate",
            "candidateGeometry",
            "featureVector",
            "featureSchemaHash",
            "label",
            "modelProbabilities",
            "mlDecision",
            "riskMultiplier",
            "finalCandidateStatus",
        }

        for fixture in payload["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                self.assertTrue(required_fields.issubset(fixture))
                self.assertEqual(fixture["featureSchemaHash"], payload["featureSchemaHash"])
                self.assertGreater(fixture["featureVector"]["featureCount"], 0)
                self.assertIn("completeFeatureVectorHash", fixture["featureVector"])
                self.assertIn(fixture["finalCandidateStatus"], {"ACCEPTED", "REJECTED", "HOLD_DIAGNOSTIC", "FALLBACK_ACCEPTED"})


def load_fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
