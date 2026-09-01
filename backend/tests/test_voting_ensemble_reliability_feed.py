"""Cover the closed-trade -> per-strategy observation attribution chain.

These tests drive a real ``VotingEnsemblePaperExecutionRepository`` rather than a stub,
so they also pin the record shapes the feed depends on: the local order carries the
decisionId and the entry/stop used to normalise into R, and the persisted decision
carries the votes and the reliability scope.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.voting_ensemble.paper_execution import VotingEnsemblePaperExecutionRepository
from backend.app.algorithms.voting_ensemble.reliability_feed import build_reliability_observations


NOW = datetime(2026, 3, 2, 15, 30, tzinfo=UTC)

SCOPE = {"regime": "trend_up", "sessionSegment": "regular_session", "volatilityState": "normal"}


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def vote(strategy_id: str, family: str, signal: str) -> dict:
    return {
        "strategy": strategy_id.replace("_", " ").title(),
        "family": family,
        "signal": signal,
        "features": {"strategyId": strategy_id},
    }


class _SeededPaperStore:
    """Shared fixture: a real repository seeded with an attributable closed trade."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = VotingEnsemblePaperExecutionRepository(Path(self._tmp.name) / "paper_execution.json")

    def seed(
        self,
        *,
        decision_id: str = "decision-1",
        client_order_id: str = "order-1",
        traded_side: str = "Buy",
        realized_pnl: float = 200.0,
        entry_price: float = 500.0,
        stop_price: float = 498.0,
        quantity: int = 100,
        votes: list[dict] | None = None,
        scope: dict | None = None,
        closed_at: datetime | None = None,
    ) -> None:
        self.repository.write_snapshot(
            f"decisions.{decision_id}",
            {
                "decisionId": decision_id,
                "decision": {
                    "decision_id": decision_id,
                    "data_timestamp": iso(NOW - timedelta(hours=2)),
                    "reliability_scope": SCOPE if scope is None else scope,
                    "votes": votes
                    if votes is not None
                    else [
                        vote("multi_timeframe_trend_alignment", "trend", "Buy"),
                        vote("atr_overextension_reversion", "mean_reversion", "Sell"),
                        vote("liquidity_sweep_reversal", "reversal", "Hold"),
                    ],
                },
            },
        )
        self.repository.write_snapshot(
            f"local_order.{client_order_id}",
            {
                "clientOrderId": client_order_id,
                "orderIntentId": f"intent-{client_order_id}",
                "decisionId": decision_id,
                "symbol": "SPY",
                "side": traded_side,
                "orderType": "LIMIT",
                "quantity": quantity,
                "entryPrice": entry_price,
                "stopPrice": stop_price,
                "submittedAt": iso(NOW - timedelta(hours=2)),
            },
        )
        # The runtime stamps a closed trade with the closing fill: clientOrderId is the
        # exit order and side is the closing (opposite) side.
        closing_side = "Sell" if traded_side == "Buy" else "Buy"
        self.repository.write_snapshot(
            f"local_closed_trade.{client_order_id}",
            {
                "closedTradeId": f"closed-{client_order_id}",
                "clientOrderId": f"exit-{client_order_id}",
                "entryOrderId": client_order_id,
                "exitOrderId": f"exit-{client_order_id}",
                "orderIntentId": f"intent-{client_order_id}",
                "symbol": "SPY",
                "side": closing_side,
                "quantity": quantity,
                "realizedPnl": realized_pnl,
                "closedAt": iso(closed_at or NOW),
            },
        )


class VotingEnsembleReliabilityFeedTest(_SeededPaperStore, unittest.TestCase):
    def test_winning_trade_credits_agreeing_strategy_and_debits_the_dissenter(self) -> None:
        # risk = |500 - 498| * 100 = 200 dollars, so +200 pnl is exactly +1R
        self.seed(realized_pnl=200.0)

        observations = {row["strategyId"]: row for row in build_reliability_observations(repository=self.repository)}

        self.assertEqual(observations["multi_timeframe_trend_alignment"]["outcomeR"], 1.0)
        self.assertEqual(observations["multi_timeframe_trend_alignment"]["direction"], "BUY")
        self.assertEqual(observations["atr_overextension_reversion"]["outcomeR"], -1.0)
        self.assertEqual(observations["atr_overextension_reversion"]["direction"], "SELL")

    def test_losing_trade_vindicates_the_dissenting_strategy(self) -> None:
        self.seed(realized_pnl=-200.0)

        observations = {row["strategyId"]: row for row in build_reliability_observations(repository=self.repository)}

        self.assertEqual(observations["multi_timeframe_trend_alignment"]["outcomeR"], -1.0)
        self.assertEqual(observations["atr_overextension_reversion"]["outcomeR"], 1.0)

    def test_hold_votes_produce_no_observation(self) -> None:
        self.seed()

        strategy_ids = {row["strategyId"] for row in build_reliability_observations(repository=self.repository)}

        self.assertNotIn("liquidity_sweep_reversal", strategy_ids)

    def test_observations_carry_the_decision_scope_for_lookup(self) -> None:
        self.seed()

        row = build_reliability_observations(repository=self.repository)[0]

        self.assertEqual(row["regime"], "trend_up")
        self.assertEqual(row["sessionSegment"], "regular_session")
        self.assertEqual(row["volatilityState"], "normal")
        self.assertEqual(row["sampleWindow"], "rolling_60_trades")
        self.assertEqual(row["algorithmId"], "voting_ensemble")

    def test_trade_without_reconstructable_risk_is_skipped_not_guessed(self) -> None:
        self.seed(entry_price=500.0, stop_price=500.0)

        self.assertEqual(build_reliability_observations(repository=self.repository), [])

    def test_trade_without_a_persisted_decision_is_skipped(self) -> None:
        self.seed()
        self.repository.write_snapshot("decisions.decision-1", {"decisionId": "decision-1"})

        self.assertEqual(build_reliability_observations(repository=self.repository), [])

    def test_decision_without_scope_is_skipped_rather_than_misfiled(self) -> None:
        self.seed(scope={})

        self.assertEqual(build_reliability_observations(repository=self.repository), [])

    def test_observations_are_ordered_oldest_first(self) -> None:
        self.seed(decision_id="d1", client_order_id="o1", closed_at=NOW - timedelta(days=2))
        self.seed(decision_id="d2", client_order_id="o2", closed_at=NOW)

        completed = [row["completedAt"] for row in build_reliability_observations(repository=self.repository)]

        self.assertEqual(completed, sorted(completed))

    def test_empty_store_yields_no_observations(self) -> None:
        self.assertEqual(build_reliability_observations(repository=self.repository), [])

    def test_direction_comes_from_the_entry_order_not_the_closing_fill(self) -> None:
        """Regression: the closed trade's own side is the closing side and would invert this."""
        self.seed(traded_side="Buy", realized_pnl=200.0)

        observations = {row["strategyId"]: row for row in build_reliability_observations(repository=self.repository)}

        # The BUY voter agreed with the entry and the trade won, so it earns +1R.
        self.assertEqual(observations["multi_timeframe_trend_alignment"]["outcomeR"], 1.0)

    def test_trade_without_an_entry_order_link_is_skipped(self) -> None:
        self.seed()
        trade_key = "voting_ensemble.paper_execution.local_closed_trade.order-1"
        record = dict(self.repository.snapshots[trade_key])
        record.pop("entryOrderId")
        self.repository.snapshots[trade_key] = record

        self.assertEqual(build_reliability_observations(repository=self.repository), [])

    def test_observations_validate_against_the_estimator_contract(self) -> None:
        from backend.app.algorithms.voting_ensemble.reliability.models import VotingEnsembleReliabilityObservation

        self.seed()

        for row in build_reliability_observations(repository=self.repository):
            VotingEnsembleReliabilityObservation.model_validate(row)


class VotingEnsembleReliabilityClosedLoopTest(_SeededPaperStore, unittest.TestCase):
    """The full loop: realised trades -> observations -> estimator -> weighted decision."""

    def seed_track_record(self, *, trend_wins: bool, trades: int = 8) -> None:
        for index in range(trades):
            self.seed(
                decision_id=f"d{index}",
                client_order_id=f"o{index}",
                traded_side="Buy",
                realized_pnl=200.0 if trend_wins else -200.0,
                closed_at=NOW - timedelta(days=trades - index),
            )

    def decide(self):
        from types import SimpleNamespace

        from backend.app.algorithms.voting_ensemble.service import _aggregate_with_family_engine, _vote
        from backend.app.algorithms.voting_ensemble.trading_settings.resolver import resolve_one_minute_trading_settings
        from backend.app.domain.models import Direction, RegimeState

        observations = build_reliability_observations(repository=self.repository)
        regime = RegimeState(
            regimeId="adx_atr_regime",
            label="trend_up",
            direction=Direction.LONG,
            volatility="NORMAL",
            confidence=0.8,
            features={"trendFit": 0.92, "breakoutFit": 0.8, "reversalFit": 0.2, "meanReversionFit": 0.22, "gapSessionFit": 0.3},
            evaluatedAt=NOW,
            sessionDate=NOW.date(),
            configurationHash="test",
        )
        snapshot = SimpleNamespace(
            evaluationTimestamp=NOW,
            settingsHash="test",
            sessionState={"sessionSegment": "regular_session"},
        )
        votes = (
            _vote("Multi-Timeframe Trend Alignment", "trend", "Buy", 85, "trend", "rc.trend"),
            _vote("ATR Overextension Reversion", "mean_reversion", "Sell", 60, "extended", "rc.atr"),
        )
        decision = _aggregate_with_family_engine(
            votes,
            (),
            snapshot,
            regime,
            None,
            settings=resolve_one_minute_trading_settings({}),
            payload={"strategy_reliability_observations": observations},
        )
        return {signal.strategyId: float(signal.reliability) for signal in decision.strategySignals}

    def test_realised_trades_flow_through_to_weighted_reliability(self) -> None:
        self.seed_track_record(trend_wins=True)

        reliabilities = self.decide()

        self.assertGreater(reliabilities["multi_timeframe_trend_alignment"], 0.5)
        self.assertLess(reliabilities["atr_overextension_reversion"], 0.5)

    def test_a_losing_track_record_downweights_the_previously_trusted_strategy(self) -> None:
        self.seed_track_record(trend_wins=False)

        reliabilities = self.decide()

        self.assertLess(reliabilities["multi_timeframe_trend_alignment"], 0.5)
        self.assertGreater(reliabilities["atr_overextension_reversion"], 0.5)


if __name__ == "__main__":
    unittest.main()
