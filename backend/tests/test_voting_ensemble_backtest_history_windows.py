from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import (
    VOTING_ENSEMBLE_LIVE_AUXILIARY_HISTORY_LIMIT,
    VOTING_ENSEMBLE_LIVE_ONE_MINUTE_HISTORY_LIMIT,
    VotingEnsembleBacktestConfig,
    backtest_config_from_live_settings,
)
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import VotingEnsembleFinalizedBarProducerConfig
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
from backend.tests.test_voting_ensemble_backtest_account_context import START, series


class _RecordingService:
    """Records the history each evaluate call was handed, and never trades."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate(self, payload: dict) -> dict:
        self.calls.append(
            {
                "candles": len(payload.get("candles") or []),
                "spy_5m": len(payload.get("spy_5m_candles") or []),
                "qqq": len(payload.get("qqq_candles") or []),
                "iwm": len(payload.get("iwm_candles") or []),
                "breadth": {name: len(rows) for name, rows in (payload.get("breadth_components") or {}).items()},
                "last_qqq": (payload.get("qqq_candles") or [{}])[-1].get("timestamp"),
                "data_timestamp": payload.get("data_timestamp"),
            }
        )
        return {"final_signal": "Hold", "votes": [], "reason_codes": ["test.recording_service"]}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def two_sessions(**kwargs) -> list[dict]:
    first = series(**kwargs)
    second = []
    for row in series(**kwargs):
        moment = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) + timedelta(days=1)
        second.append({**row, "timestamp": moment.isoformat().replace("+00:00", "Z")})
    return first + second


class BacktestHistoryWindowTest(unittest.TestCase):
    """Replay hands the pipeline the history live would, and no more.

    Live evaluates a bar against the producer's bounded windows: 390 SPY one-minute
    candles and 240 of each auxiliary stream. Replay used to hand it the whole prefix,
    which was a parity gap on any multi-session dataset and the reason a multi-year replay
    never finished. Pinned here from the producer's own defaults, so the two cannot drift
    apart without this failing.
    """

    def test_defaults_are_the_live_producer_windows(self) -> None:
        producer = VotingEnsembleFinalizedBarProducerConfig()
        config = VotingEnsembleBacktestConfig()

        self.assertEqual(config.oneMinuteHistoryLimit, producer.history_limit)
        self.assertEqual(config.oneMinuteHistoryLimit, VOTING_ENSEMBLE_LIVE_ONE_MINUTE_HISTORY_LIMIT)
        self.assertEqual(config.auxiliaryHistoryLimit, VOTING_ENSEMBLE_LIVE_AUXILIARY_HISTORY_LIMIT)
        self.assertEqual(config.fiveMinuteHistoryLimit, 78)
        self.assertEqual(config.fifteenMinuteHistoryLimit, 26)

    def test_auxiliary_history_is_bounded_and_point_in_time_across_sessions(self) -> None:
        service = _RecordingService()
        runner = VotingEnsembleBacktestRunner(
            service=service,
            config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=False),
        )

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=two_sessions(),
            qqq_candles=two_sessions(base=440.0, scale=1.2),
            iwm_candles=two_sessions(base=210.0, scale=0.8),
            breadth_components={"XLK": two_sessions(base=250.0, scale=1.1)},
            timeframe="1Min",
        )

        self.assertEqual(result["sessions"], 2)
        self.assertEqual(result["historyWindows"]["auxiliary1m"], 240)
        self.assertTrue(service.calls)
        # Never more than live's window, even on the second session where the full prefix
        # would have been up to 780 candles.
        self.assertEqual(max(call["qqq"] for call in service.calls), 240)
        self.assertEqual(max(call["iwm"] for call in service.calls), 240)
        self.assertEqual(max(call["breadth"]["XLK"] for call in service.calls), 240)
        # Point in time: the latest auxiliary candle never runs ahead of the bar.
        for call in service.calls:
            self.assertLessEqual(_utc(call["last_qqq"]), _utc(call["data_timestamp"]))
        # The second session's early bars still see the window filled from the previous
        # session, which is what the live producer would return too.
        second_session_first = next(call for call in service.calls if call["data_timestamp"] > (START + timedelta(days=1)).isoformat())
        self.assertEqual(second_session_first["qqq"], 240)

    def test_spy_history_stays_within_the_session(self) -> None:
        service = _RecordingService()
        runner = VotingEnsembleBacktestRunner(
            service=service,
            config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=False),
        )

        runner.run(symbol="SPY", spy_1m_candles=two_sessions(), timeframe="1Min")

        self.assertEqual(max(call["candles"] for call in service.calls), 390)
        # Bar 40 of each session sees 40 candles, not 40 plus the previous session.
        first_calls = [call["candles"] for call in service.calls[:2]]
        self.assertEqual(first_calls, [40, 41])

    def test_single_session_output_is_unchanged_by_the_windows(self) -> None:
        """The recorded baseline session fits inside every window, so its numbers must not move."""
        runner = VotingEnsembleBacktestRunner(config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=False))

        result = runner.run(
            symbol="SPY",
            spy_1m_candles=series(),
            qqq_candles=series(base=440.0, scale=1.2),
            iwm_candles=series(base=210.0, scale=0.8),
            breadth_components={
                "XLK": series(base=250.0, scale=1.1),
                "XLF": series(base=48.0, scale=0.9),
                "XLV": series(base=145.0, scale=0.7),
            },
            timeframe="1Min",
        )

        # The default-configuration row recorded in BASELINES.md.
        self.assertEqual(result["totalTrades"], 5)
        self.assertEqual(result["winners"], 1)
        self.assertEqual(result["netTotalPnl"], -50.0)
        self.assertEqual(result["decisionCount"], 351)
        self.assertEqual(result["engine"], "voting_ensemble_pipeline")
        self.assertTrue(result["matchesLiveAlgorithm"])


class BacktestConfigFromLiveSettingsTest(unittest.TestCase):
    """The replay configuration is derived from the live resolution, not restated beside it."""

    def test_capital_window_and_hash_come_from_the_resolved_settings(self) -> None:
        live = resolve_one_minute_trading_settings(None)
        config = backtest_config_from_live_settings()

        self.assertEqual(config.startingCapital, live.riskPerTrade.startingCapital)
        self.assertEqual(config.sessionStart, live.sessionWindows.sessionStart)
        self.assertEqual(config.newTradesUntil, live.sessionWindows.newTradesUntil)
        self.assertEqual(config.liveSettingsConfigurationHash, live.configurationHash)
        self.assertEqual(config.liveSettingsVersion, live.settingsVersion)
        # Shipped state: both gates disabled, no custom segment map.
        self.assertIsNone(config.sessionPolicy)
        self.assertIsNone(config.eventCalendar)
        self.assertIsNone(config.sessionSegments)

    def test_explicit_overrides_win(self) -> None:
        config = backtest_config_from_live_settings(startingCapital=100_000.0, applyEntryWindow=False)

        self.assertEqual(config.startingCapital, 100_000.0)
        self.assertFalse(config.applyEntryWindow)
        self.assertIsNotNone(config.liveSettingsConfigurationHash)


if __name__ == "__main__":
    unittest.main()
