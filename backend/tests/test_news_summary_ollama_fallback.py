from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app.main import app


class NewsSummaryOllamaFallbackTest(unittest.TestCase):
    def test_news_summary_reports_actionable_ollama_connection_failure(self) -> None:
        async def snapshot(_symbol: str, _limit: int) -> dict:
            return {
                "symbol": "SPY",
                "news": {"items": [{"headline": "SPY test headline"}]},
                "tradingAlerts": [],
                "vixRisk": {"activeLevel": {"label": "Normal"}},
                "esSnapshot": {"activeLevel": {"label": "Neutral"}},
            }

        async def unavailable(_snapshot: dict) -> tuple[str, dict]:
            request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
            raise httpx.ConnectError("All connection attempts failed", request=request)

        with (
            patch("backend.app.main.build_trade_summary_snapshot", side_effect=snapshot),
            patch("backend.app.main.ask_ollama_for_trade_summary", side_effect=unavailable),
        ):
            response = TestClient(app).get("/api/news-summary?symbol=SPY&limit=10")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "Rule fallback")
        self.assertEqual(body["ollamaHealth"]["status"], "connection_failed")
        self.assertIn("ollama serve", body["warning"])
        self.assertIn("OLLAMA_BASE_URL", body["warning"])
        self.assertIn("rule-based read", body["summary"]["conclusion"])

    def test_ollama_missing_model_warning_suggests_pull_or_model_override(self) -> None:
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        response = httpx.Response(404, request=request, text="model not found")
        exc = httpx.HTTPStatusError("not found", request=request, response=response)

        from backend.app.main import ollama_failure_context

        warning, health = ollama_failure_context(exc)

        self.assertEqual(health["status"], "model_not_available")
        self.assertIn("ollama pull", warning)
        self.assertIn("OLLAMA_MODEL", warning)


if __name__ == "__main__":
    unittest.main()
