from __future__ import annotations

import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.contracts import (
    WcaAlgorithmRiskStatus,
    WcaDataQualityStatus,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaQuote,
    WcaVolatilityStatus,
)
from backend.app.algorithms.wca.market_status import WcaMarketStatusConfig, resolve_market_status
from backend.app.algorithms.wca.market_snapshot import WcaCandle


UTC = timezone.utc
ROOT = Path(__file__).parents[2]
MARKET_STATUS_PATH = ROOT / "backend" / "app" / "algorithms" / "wca" / "market_status.py"


class WcaStep7MarketStatusTest(unittest.TestCase):
    def test_market_status_does_not_import_regime_algorithm(self) -> None:
        tree = ast.parse(MARKET_STATUS_PATH.read_text(encoding="utf-8"), filename=str(MARKET_STATUS_PATH))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        self.assertFalse(any("regime" in module.lower() for module in imports))
        self.assertFalse(any(module.startswith("backend.app.algorithms.") and not module.startswith("backend.app.algorithms.wca") for module in imports))

    def test_status_is_deterministic_for_same_snapshot(self) -> None:
        snapshot = market_snapshot(range_pct=0.003, volume=120000)

        first = resolve_market_status(snapshot)
        second = resolve_market_status(snapshot)

        self.assertEqual(first.deterministic_json(), second.deterministic_json())
        self.assertEqual(first.data_quality, WcaDataQualityStatus.HEALTHY.value)
        self.assertEqual(first.algorithm_risk, WcaAlgorithmRiskStatus.NORMAL.value)

    def test_threshold_oscillation_holds_defensive_profile_until_confirmed(self) -> None:
        config = WcaMarketStatusConfig(minimum_profile_hold_seconds=300, improvement_confirmation_candles=3)
        defensive_snapshot = market_snapshot(range_pct=0.009, at=datetime(2026, 1, 6, 17, 0, tzinfo=UTC))
        previous = resolve_market_status(defensive_snapshot, config=config)
        self.assertEqual(previous.algorithm_risk, WcaAlgorithmRiskStatus.DEFENSIVE.value)

        brief_normal = market_snapshot(range_pct=0.003, at=datetime(2026, 1, 6, 17, 1, tzinfo=UTC))
        held = resolve_market_status(brief_normal, previous_status=previous, confirmation_count=1, config=config)

        self.assertEqual(held.algorithm_risk, WcaAlgorithmRiskStatus.DEFENSIVE.value)
        self.assertIn("wca.market.hysteresis.improvement_held", held.reason_codes)

        confirmed_normal = market_snapshot(range_pct=0.003, at=datetime(2026, 1, 6, 17, 7, tzinfo=UTC))
        released = resolve_market_status(confirmed_normal, previous_status=previous, confirmation_count=3, config=config)

        self.assertEqual(released.algorithm_risk, WcaAlgorithmRiskStatus.NORMAL.value)
        self.assertIn("wca.market.hysteresis.improvement_confirmed", released.reason_codes)

    def test_defensive_changes_apply_immediately(self) -> None:
        config = WcaMarketStatusConfig(minimum_profile_hold_seconds=300, improvement_confirmation_candles=3)
        normal = resolve_market_status(market_snapshot(range_pct=0.003), config=config)
        extreme = market_snapshot(range_pct=0.016, at=datetime(2026, 1, 6, 17, 1, tzinfo=UTC))

        status = resolve_market_status(extreme, previous_status=normal, confirmation_count=0, config=config)

        self.assertEqual(status.volatility, WcaVolatilityStatus.EXTREME.value)
        self.assertEqual(status.algorithm_risk, WcaAlgorithmRiskStatus.DAILY_STOP.value)
        self.assertIn("wca.market.hysteresis.defensive_immediate", status.reason_codes)

    def test_invalid_or_stale_inputs_cannot_produce_favorable_status(self) -> None:
        fresh_time = datetime(2026, 1, 6, 17, 0, tzinfo=UTC)
        stale = market_snapshot(range_pct=0.003, at=fresh_time, decision_at=fresh_time + timedelta(minutes=5))
        not_ready = market_snapshot(range_pct=0.003, at=fresh_time, data_ready=False)

        for label, snapshot in (("stale", stale), ("not_ready", not_ready)):
            with self.subTest(label=label):
                status = resolve_market_status(snapshot)
                self.assertEqual(status.status, WcaEvaluationStatus.INVALID.value)
                self.assertEqual(status.data_quality, WcaDataQualityStatus.INVALID.value)
                self.assertEqual(status.algorithm_risk, WcaAlgorithmRiskStatus.DAILY_STOP.value)
                self.assertEqual(status.classification_confidence, 0)


def market_snapshot(
    *,
    range_pct: float,
    volume: float = 120000,
    at: datetime = datetime(2026, 1, 6, 17, 0, tzinfo=UTC),
    decision_at: datetime | None = None,
    data_ready: bool = True,
) -> WcaMarketSnapshot:
    candles = tuple(
        candle(at - timedelta(minutes=39 - index), close=100 + ((index % 2) * 0.02), range_pct=range_pct, volume=volume)
        for index in range(40)
    )
    latest = candles[-1]
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=latest.timestamp,
        decision_timestamp=decision_at or latest.timestamp,
        candles=candles,
        quote=WcaQuote(timestamp=latest.timestamp, bid=99.99, ask=100.01),
        data_ready=data_ready,
    )


def candle(timestamp: datetime, *, close: float, range_pct: float, volume: float) -> WcaCandle:
    half_range = close * range_pct / 2
    return WcaCandle(
        timestamp=timestamp,
        open=close,
        high=close + half_range,
        low=close - half_range,
        close=close,
        volume=volume,
    )


if __name__ == "__main__":
    unittest.main()
