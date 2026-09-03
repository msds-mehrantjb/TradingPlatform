from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.algorithms.voting_ensemble import api as api_module


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str | None, str]] = []
        self.active = False
        self.reason: str | None = None

    def set_kill_switch(self, active: bool, *, reason: str | None = None, updated_by: str = "api", refresh_readiness: bool = True) -> dict:
        self.calls.append((active, reason, updated_by))
        self.active = active
        self.reason = reason
        return self.kill_switch_status()

    def kill_switch_status(self, *, refresh_readiness: bool = False) -> dict:
        return {"algorithmId": "voting_ensemble", "active": self.active, "reason": self.reason, "blocksNewEntries": self.active}


class VotingEnsembleKillSwitchApiTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(api_module.router)
        self.client = TestClient(app)
        self.supervisor = FakeSupervisor()

    def test_post_throws_and_clears_the_switch_with_a_reason(self) -> None:
        with patch.object(api_module, "get_voting_ensemble_runtime_supervisor", return_value=self.supervisor):
            thrown = self.client.post("/api/voting-ensemble/runtime/kill-switch", json={"active": True, "reason": "drill"})
            state = self.client.get("/api/voting-ensemble/runtime/kill-switch")
            cleared = self.client.post("/api/voting-ensemble/runtime/kill-switch", json={"active": False})

        self.assertEqual(thrown.status_code, 200)
        self.assertTrue(thrown.json()["active"])
        self.assertEqual(thrown.json()["reason"], "drill")
        self.assertTrue(state.json()["blocksNewEntries"])
        self.assertFalse(cleared.json()["active"])
        self.assertEqual(self.supervisor.calls, [(True, "drill", "api"), (False, None, "api")])

    def test_unknown_fields_are_rejected(self) -> None:
        with patch.object(api_module, "get_voting_ensemble_runtime_supervisor", return_value=self.supervisor):
            response = self.client.post("/api/voting-ensemble/runtime/kill-switch", json={"active": True, "flatten": True})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.supervisor.calls, [])


if __name__ == "__main__":
    unittest.main()
