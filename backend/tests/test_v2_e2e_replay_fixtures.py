from __future__ import annotations

import hashlib
import json
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.api.v2 import build_replay_engine
from backend.app.domain.feature_engine import MarketCandle, PriorDayOHLC


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "v2_e2e_replay_fixtures.json"
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
REQUIRED_FIXTURES = {
    "strong_uptrend",
    "strong_downtrend",
    "range_bound_session",
    "low_volatility_session",
    "high_volatility_session",
    "gap_continuation",
    "gap_fade",
    "failed_breakout",
    "liquidity_sweep",
    "economic_event_shock",
}


class V2EndToEndReplayFixturesTest(unittest.TestCase):
    maxDiff = None

    def test_fixture_catalog_is_complete_sanitized_and_offline(self) -> None:
        payload = load_fixture_payload()

        self.assertEqual(payload["schemaVersion"], "v2_e2e_replay_fixture_v1")
        self.assertEqual(payload["marketDataFeed"], "sanitized_synthetic")
        self.assertFalse(payload["dataPolicy"]["containsCredentials"])
        self.assertFalse(payload["dataPolicy"]["containsPrivateAccountInformation"])
        self.assertFalse(payload["dataPolicy"]["requiresLiveFeed"])
        self.assertEqual({fixture["id"] for fixture in payload["fixtures"]}, REQUIRED_FIXTURES)

        serialized = json.dumps(payload).lower()
        for forbidden in ("api_key", "secret", "alpaca_key", "account_id", "oauth", "token"):
            self.assertNotIn(forbidden, serialized)

    def test_all_v2_replay_fixtures_match_expected_snapshots(self) -> None:
        engine = build_replay_engine()

        for fixture in load_fixture_payload()["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                candles = candles_for_fixture(fixture["id"])
                self.assertEqual(bar_hash(candles_to_rows(candles)), fixture["barsHash"])

                first = replay_summary(engine, fixture, candles)
                second = replay_summary(engine, fixture, candles)

                self.assertEqual(first, second, f"Replay fixture {fixture['id']} is not deterministic.")
                self.assertEqual(
                    first,
                    fixture["expected"],
                    json.dumps(
                        {
                            "fixture": fixture["id"],
                            "message": "V2 replay fixture snapshot changed.",
                            "expected": fixture["expected"],
                            "actual": first,
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                )


def replay_summary(engine: Any, fixture: dict[str, Any], candles: list[MarketCandle]) -> dict[str, Any]:
    result = engine.replay_session(
        symbol="SPY",
        sessionDate=SESSION_DATE,
        spy1mCandles=candles,
        spy5mCandles=candles,
        spy15mCandles=candles,
        qqqCandles=candles,
        iwmCandles=candles,
        breadthComponents={"XLK": candles, "XLF": candles, "XLV": candles},
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99.5, high=101.0, low=98.7, close=100.0),
        economicEventState=fixture["economicEventState"],
    )
    snapshot = result.snapshots[-1]
    features = snapshot.featureSnapshot.get("features", {})

    return {
        "decisionTimestampUtc": snapshot.decisionTimestampUtc.isoformat().replace("+00:00", "Z"),
        "featureStates": {
            "dataReady": snapshot.featureSnapshot.get("dataReady"),
            "vwap": feature_value(features, "sessionVwap"),
            "atr14": feature_value(features, "spy1mAtr14"),
            "adx14": feature_value(features, "spy1mAdx14"),
            "gapPercent": feature_value(features, "gapPercent"),
        },
        "strategyOutputs": {output["strategyId"]: output["signal"] for output in snapshot.strategyOutputs},
        "familyScores": {
            score["family"]: {
                "buyScore": rounded(score["buyScore"]),
                "sellScore": rounded(score["sellScore"]),
                "holdScore": rounded(score["holdScore"]),
            }
            for score in snapshot.ensembleDecision.get("familyScores", [])
        },
        "gateResults": {
            "status": snapshot.gateDecision.get("status"),
            "hardBlockerIds": [gate.get("gateId") for gate in snapshot.gateDecision.get("hardBlockers", [])],
        },
        "policyResult": {
            "tradeAllowed": snapshot.effectivePolicy.get("tradeAllowed"),
            "quantity": snapshot.effectivePolicy.get("quantity"),
            "riskDollars": snapshot.effectivePolicy.get("approvedRiskDollars"),
        },
        "paperOrderResult": {
            "orderType": (snapshot.orderPlan or {}).get("orderType"),
            "quantity": (snapshot.orderPlan or {}).get("quantity"),
            "eligible": (snapshot.orderPlan or {}).get("eligible"),
        },
    }


def feature_value(features: dict[str, Any], key: str) -> float | bool | None:
    return rounded((features.get(key) or {}).get("value"))


def rounded(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    return round(float(value), 4)


def load_fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def candles_for_fixture(fixture_id: str) -> list[MarketCandle]:
    return [
        MarketCandle(
            timestamp=START + timedelta(minutes=row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            tradeCount=int(row[5] / 10),
            symbol="SPY",
            timeframe="1Min",
        )
        for row in generated_rows(fixture_id)
    ]


def candles_to_rows(candles: list[MarketCandle]) -> list[list[float | int]]:
    rows: list[list[float | int]] = []
    for index, candle in enumerate(candles):
        rows.append([index, candle.open, candle.high, candle.low, candle.close, int(candle.volume)])
    return rows


def bar_hash(rows: list[list[float | int]]) -> str:
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def generated_rows(kind: str) -> list[list[float | int]]:
    price = 100.0
    rows: list[list[float | int]] = []
    for index in range(30):
        drift, wiggle = scenario_move(kind, index)
        if kind == "gap_continuation" and index == 0:
            price = 102.2
        if kind == "gap_fade" and index == 0:
            price = 102.4
        close = max(1.0, price + drift + wiggle)
        spread = abs(close - price)
        high = max(price, close) + max(0.05, spread * 0.45)
        low = min(price, close) - max(0.05, spread * 0.45)
        if kind == "failed_breakout" and index == 8:
            high = 101.55
        if kind == "liquidity_sweep" and index == 10:
            low = min(low, 98.8)
        rows.append([index, round(price, 4), round(high, 4), round(low, 4), round(close, 4), scenario_volume(kind, index)])
        price = close
    return rows


def scenario_move(kind: str, index: int) -> tuple[float, float]:
    if kind == "strong_uptrend":
        return 0.16 + (0.03 if index > 14 else 0), 0.02 if index % 3 == 0 else -0.01
    if kind == "strong_downtrend":
        return -0.16 - (0.03 if index > 14 else 0), -0.02 if index % 3 == 0 else 0.01
    if kind == "range_bound_session":
        return [0.08, -0.07, 0.06, -0.08, 0.03, -0.02][index % 6], 0.0
    if kind == "low_volatility_session":
        return [0.01, -0.008, 0.006, -0.006][index % 4], 0.0
    if kind == "high_volatility_session":
        return [0.42, -0.38, 0.34, -0.44, 0.30, -0.28][index % 6], 0.0
    if kind == "gap_continuation":
        return 0.13 if index < 10 else 0.07, 0.01
    if kind == "gap_fade":
        return -0.14 if index < 12 else -0.05, 0.0
    if kind == "failed_breakout":
        return 0.05 if index < 8 else (-0.18 if index in (8, 9, 10) else (-0.06 if index < 18 else 0.02)), 0.0
    if kind == "liquidity_sweep":
        return 0.04 if index < 10 else (-0.35 if index == 10 else (0.28 if index == 11 else 0.04)), 0.0
    if kind == "economic_event_shock":
        return 0.02 if index < 14 else [0.75, -0.62, 0.56, -0.50][(index - 14) % 4], 0.0
    raise ValueError(f"unknown fixture kind {kind}")


def scenario_volume(kind: str, index: int) -> int:
    volume = 1000 + index * 10
    if kind in {"high_volatility_session", "economic_event_shock"} and index >= 14:
        volume += 1200
    if kind in {"gap_continuation", "gap_fade"} and index < 6:
        volume += 700
    return volume


if __name__ == "__main__":
    unittest.main()
