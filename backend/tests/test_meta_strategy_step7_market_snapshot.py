from __future__ import annotations

import ast
import importlib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.app.algorithms.meta_strategy import (
    MetaStrategyMarketSnapshot,
    MetaStrategyMarketSnapshotRequest,
    MetaStrategySnapshotCandle,
    MetaStrategySnapshotQuote,
    build_meta_strategy_market_snapshot,
    meta_strategy_strategy_uses_snapshot_only,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"
DECISION_TIMESTAMP = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
PROHIBITED_STRATEGY_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "urllib",
    "sqlite3",
    "backend.app.api",
    "backend.app.broker",
    "backend.app.brokers",
    "backend.app.database",
    "backend.app.db",
    "backend.app.persistence",
)


class MetaStrategyStep7MarketSnapshotTest(unittest.TestCase):
    maxDiff = None

    def test_market_snapshot_files_exist_and_import(self) -> None:
        self.assertTrue((PACKAGE_DIR / "market_snapshot.py").is_file())
        self.assertTrue((PACKAGE_DIR / "indicators.py").is_file())
        self.assertIsNotNone(importlib.import_module("backend.app.algorithms.meta_strategy.market_snapshot"))
        self.assertIsNotNone(importlib.import_module("backend.app.algorithms.meta_strategy.indicators"))

    def test_snapshot_contains_required_point_in_time_sections(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())

        self.assertIsInstance(snapshot, MetaStrategyMarketSnapshot)
        self.assertTrue(snapshot.point_in_time)
        self.assertEqual(snapshot.algorithm_id, "meta_strategy")
        self.assertEqual(snapshot.timestamp, DECISION_TIMESTAMP)
        self.assertEqual(set(snapshot.candles), {"1m", "5m", "15m"})
        self.assertIsNotNone(snapshot.quote)
        self.assertIsNotNone(snapshot.vwap)
        self.assertEqual(set(snapshot.moving_averages), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.atr), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.adx), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.rsi), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.macd), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.bollinger_bands), {"1m", "5m", "15m"})
        self.assertEqual(set(snapshot.relative_volume), {"1m", "5m", "15m"})
        self.assertGreater(snapshot.volume, 0)
        self.assertIn("basisPoints", snapshot.spread)
        self.assertIn("level", snapshot.liquidity)
        self.assertIn(snapshot.session_phase, {"outside_session", "opening", "morning", "midday", "afternoon", "closing"})
        self.assertIn("state", snapshot.gap_state)
        self.assertIn("spyVsQqq", snapshot.qqq_iwm_context)
        self.assertIn("spyVsIwm", snapshot.qqq_iwm_context)
        self.assertIn("averageReturn", snapshot.breadth)
        self.assertEqual(snapshot.economic_event_state["state"], "none")

    def test_future_candles_and_quotes_do_not_change_snapshot(self) -> None:
        base_request = request_with()
        base = build_meta_strategy_market_snapshot(base_request)
        with_future = base_request.model_copy(
            update={
                "one_minute_candles": (
                    *base_request.one_minute_candles,
                    *candles("SPY", "1Min", count=8, end=DECISION_TIMESTAMP + timedelta(minutes=8), drift=10.0),
                ),
                "five_minute_candles": (
                    *base_request.five_minute_candles,
                    *candles("SPY", "5Min", count=3, step_minutes=5, end=DECISION_TIMESTAMP + timedelta(minutes=15), drift=10.0),
                ),
                "fifteen_minute_candles": (
                    *base_request.fifteen_minute_candles,
                    *candles("SPY", "15Min", count=3, step_minutes=15, end=DECISION_TIMESTAMP + timedelta(minutes=45), drift=10.0),
                ),
                "quotes": (
                    *base_request.quotes,
                    MetaStrategySnapshotQuote(timestamp=DECISION_TIMESTAMP + timedelta(seconds=1), bid=80.0, ask=120.0, symbol="SPY"),
                ),
                "qqq_candles": (
                    *base_request.qqq_candles,
                    *candles("QQQ", "1Min", count=5, end=DECISION_TIMESTAMP + timedelta(minutes=5), drift=9.0),
                ),
            }
        )
        future = build_meta_strategy_market_snapshot(with_future)

        self.assertEqual(future.deterministic_hash(), base.deterministic_hash())
        self.assertEqual(future.candles, base.candles)
        self.assertEqual(future.quote, base.quote)
        for timeframe, rows in future.candles.items():
            with self.subTest(timeframe=timeframe):
                self.assertTrue(all(row["timestamp"] <= DECISION_TIMESTAMP.isoformat() for row in rows))

    def test_incomplete_current_bar_is_excluded_until_bar_end(self) -> None:
        request = request_with(
            decision_timestamp=datetime(2026, 1, 5, 15, 45, 30, tzinfo=UTC),
            one_minute_end=datetime(2026, 1, 5, 15, 45, tzinfo=UTC),
        )
        snapshot = build_meta_strategy_market_snapshot(request)

        self.assertEqual(snapshot.candles["1m"][-1]["timestamp"], "2026-01-05T15:44:00Z")

    def test_direct_snapshot_contract_rejects_future_cutoff_timestamp(self) -> None:
        valid = build_meta_strategy_market_snapshot(request_with())
        payload = valid.model_dump(mode="python")
        payload["source_cutoff_timestamp"] = DECISION_TIMESTAMP + timedelta(seconds=1)

        with self.assertRaises(ValidationError):
            MetaStrategyMarketSnapshot(**payload)

    def test_strategy_adapter_passes_only_immutable_snapshot(self) -> None:
        testcase = self

        class SnapshotOnlyStrategy:
            def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> dict[str, Any]:
                with testcase.assertRaises(ValidationError):
                    setattr(snapshot, "last_price", 1.0)
                return {"decisionId": snapshot.decision_id, "lastPrice": snapshot.last_price}

        snapshot = build_meta_strategy_market_snapshot(request_with())
        result = meta_strategy_strategy_uses_snapshot_only(SnapshotOnlyStrategy(), snapshot)

        self.assertEqual(result["decisionId"], "decision-1")
        self.assertEqual(result["lastPrice"], snapshot.last_price)

    def test_meta_strategy_strategy_modules_do_not_import_io_bound_services(self) -> None:
        strategy_dir = PACKAGE_DIR / "strategies"
        if not strategy_dir.exists():
            self.assertFalse(strategy_dir.exists())
            return

        violations = []
        for path in sorted(strategy_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module_name in imported_module_names(tree):
                if starts_with_any(module_name, PROHIBITED_STRATEGY_IMPORT_PREFIXES):
                    violations.append(f"{path.relative_to(strategy_dir)} imports {module_name}")

        self.assertEqual(violations, [])


def request_with(
    *,
    decision_timestamp: datetime = DECISION_TIMESTAMP,
    one_minute_end: datetime | None = None,
) -> MetaStrategyMarketSnapshotRequest:
    end = one_minute_end or (decision_timestamp - timedelta(minutes=1))
    return MetaStrategyMarketSnapshotRequest(
        decision_id="decision-1",
        snapshot_id="snapshot-1",
        symbol="SPY",
        decision_timestamp=decision_timestamp,
        one_minute_candles=candles("SPY", "1Min", count=80, end=end),
        five_minute_candles=candles("SPY", "5Min", count=80, step_minutes=5, end=decision_timestamp - timedelta(minutes=5)),
        fifteen_minute_candles=candles("SPY", "15Min", count=80, step_minutes=15, end=decision_timestamp - timedelta(minutes=15)),
        quotes=(
            MetaStrategySnapshotQuote(timestamp=decision_timestamp - timedelta(seconds=10), bid=101.48, ask=101.5, symbol="SPY"),
        ),
        qqq_candles=candles("QQQ", "1Min", count=80, end=end, drift=0.03),
        iwm_candles=candles("IWM", "1Min", count=80, end=end, drift=0.02),
        breadth_components={
            "XLK": candles("XLK", "1Min", count=80, end=end, drift=0.02),
            "XLF": candles("XLF", "1Min", count=80, end=end, drift=-0.01),
        },
        prior_close=99.5,
        economic_event_state={"state": "none", "importance": "low"},
    )


def candles(
    symbol: str,
    timeframe: str,
    *,
    count: int,
    end: datetime,
    step_minutes: int = 1,
    drift: float = 0.04,
) -> tuple[MetaStrategySnapshotCandle, ...]:
    start = end - timedelta(minutes=step_minutes * (count - 1))
    rows = []
    for index in range(count):
        timestamp = start + timedelta(minutes=step_minutes * index)
        base = 100.0 + (index * drift)
        rows.append(
            MetaStrategySnapshotCandle(
                timestamp=timestamp,
                open=base - 0.02,
                high=base + 0.08,
                low=base - 0.08,
                close=base + 0.02,
                volume=100_000 + index * 100,
                symbol=symbol,
                timeframe=timeframe,
                provider="fixture",
            )
        )
    return tuple(rows)


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def starts_with_any(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


if __name__ == "__main__":
    unittest.main()
