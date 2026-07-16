from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.main import app


class WeightedVotingLegacyCompatibilityRemovalTest(unittest.TestCase):
    def test_legacy_compatible_service_method_is_removed(self) -> None:
        self.assertFalse(hasattr(WeightedVotingService, "evaluate_legacy_compatible"))

    def test_legacy_compatible_endpoint_is_removed(self) -> None:
        client = TestClient(app)
        response = client.post("/api/weighted-voting/evaluate-legacy-compatible", json={})

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
