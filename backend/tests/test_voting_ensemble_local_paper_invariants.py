from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.voting_ensemble.paper_execution import (
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsemblePaperExecutionRepository,
)
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, local_engine_intent, seed_local_quote


class VotingEnsembleLocalPaperInvariantTest(unittest.TestCase):
    def test_cash_and_realized_pnl_require_matching_valid_local_fill_events(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000"}):
            repository = VotingEnsemblePaperExecutionRepository()
            account = repository.local_account_snapshot(observed_at=NOW)
            repository.snapshots[_snapshot_key("local_account.latest")]["cash"] = account["cash"] - 1.0
            repository.snapshots[_snapshot_key("local_account.latest")]["realizedPnl"] = 1.0

            consistency = repository.validate_local_consistency(evaluated_at=NOW)

            self.assertEqual(consistency["status"], "LOCAL_CONSISTENCY_REQUIRED")
            self.assertIn("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", consistency["reasonCodes"])
            self.assertIn("voting_ensemble.local_paper_recovery.realized_pnl_fill_invariant_failed", consistency["reasonCodes"])

    def test_ownership_invariants_reject_foreign_fill_position_order_and_risk_records(self) -> None:
        repository = _seed_long_position(quantity=10, price=100.0)
        VotingEnsembleLocalPaperExecutionEngine(repository).submit_order(local_engine_intent(client_order_id="owned-order-before-corruption", quantity=1, limit_price=100.0))
        for key in (
            _first_key(repository, ".paper_order_gateway.fill."),
            _first_key(repository, ".local_position."),
            _first_key(repository, ".local_order."),
        ):
            repository.snapshots[key]["algorithmId"] = "algorithm_b"
        repository.snapshots[_snapshot_key("local_risk_snapshot.latest")]["accountId"] = "algorithm_b.paper.account"

        consistency = repository.validate_local_consistency(evaluated_at=NOW)

        self.assertEqual(consistency["status"], "LOCAL_CONSISTENCY_REQUIRED")
        self.assertIn("voting_ensemble.local_paper_recovery.foreign_algorithm_record", consistency["reasonCodes"])
        self.assertIn("voting_ensemble.local_paper_consistency.risk_account_mismatch", consistency["reasonCodes"])

    def test_exit_must_reference_voting_ensemble_owned_position(self) -> None:
        repository = _seed_long_position(quantity=10, price=100.0)
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        position_key = _first_key(repository, ".local_position.")
        repository.snapshots[position_key]["positionOwner"] = "algorithm_b"

        ack = engine.submit_order(local_engine_intent(client_order_id="foreign-owned-exit", quantity=1, side=Signal.SELL, limit_price=101.0))

        self.assertEqual(ack.status, "REJECTED")
        self.assertEqual(ack.rejectedReason, "voting_ensemble.local_paper.sell_cannot_mutate_foreign_or_absent_position")

    def test_fill_effect_sum_position_quantity_and_duplicate_fill_invariants_are_validated(self) -> None:
        repository = _seed_long_position(quantity=10, price=100.0)
        position_key = _first_key(repository, ".local_position.")
        account_key = _snapshot_key("local_account.latest")
        repository.snapshots[position_key]["quantity"] = 11
        repository.snapshots[position_key]["signedQuantity"] = 11
        fill_ids = list(repository.snapshots[account_key]["appliedFillIds"])
        repository.snapshots[account_key]["appliedFillIds"] = [*fill_ids, fill_ids[0]]

        consistency = repository.validate_local_consistency(evaluated_at=NOW)

        self.assertEqual(consistency["status"], "LOCAL_CONSISTENCY_REQUIRED")
        self.assertIn("voting_ensemble.local_paper_recovery.position_quantity_fill_invariant_failed", consistency["reasonCodes"])
        self.assertIn("voting_ensemble.local_paper_consistency.account_applied_fill_id_duplicate", consistency["reasonCodes"])

    def test_exit_fill_cannot_cross_through_zero_or_create_short_when_shorts_are_disabled(self) -> None:
        repository = _seed_long_position(quantity=10, price=100.0)
        before_sell = repository.inventory_snapshot()

        sell_fill = repository.inventory_ledger.apply_fill(
            client_order_id="cross-zero-sell",
            order_intent_id="intent-cross-zero-sell",
            symbol="SPY",
            side=Signal.SELL,
            requested_quantity=15,
            fill_price=101.0,
            filled_at=NOW + timedelta(seconds=1),
        )
        after_sell = repository.inventory_snapshot()

        self.assertIsNotNone(sell_fill)
        assert sell_fill is not None
        self.assertEqual(before_sell["localPositions"][0]["signedQuantity"], 10)
        self.assertEqual(sell_fill.filledQuantity, 10)
        self.assertEqual(after_sell["localPositions"], [])
        self.assertEqual(after_sell["localPaperAccount"]["unrealizedPnl"], 0.0)
        self.assertEqual(after_sell["localPaperAccount"]["realizedPnl"], 10.0)

    def test_new_long_order_cannot_exceed_local_capital_or_risk_capacity(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "1000"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)

            cash_ack = engine.submit_order(local_engine_intent(client_order_id="capacity-cash", quantity=3, limit_price=500.0))
            risk_ack = engine.submit_order(local_engine_intent(client_order_id="capacity-risk", quantity=1, planned_risk=2000.0))

            self.assertEqual(cash_ack.status, "REJECTED")
            self.assertEqual(cash_ack.rejectedReason, "voting_ensemble.local_paper.insufficient_buying_power")
            self.assertEqual(risk_ack.status, "REJECTED")
            self.assertEqual(risk_ack.rejectedReason, "voting_ensemble.local_paper.local_risk_limit_exceeded")

    def test_mark_to_market_changes_unrealized_not_realized_and_equity_matches_cash_plus_inventory(self) -> None:
        repository = _seed_long_position(quantity=10, price=100.0)
        before_mark = repository.inventory_snapshot()

        seed_local_quote(repository, bid=105.0, ask=105.05, observed_at=NOW + timedelta(seconds=1))
        after_mark = repository.inventory_snapshot()
        account = after_mark["localPaperAccount"]
        position = after_mark["localPositions"][0]

        self.assertEqual(before_mark["localPaperAccount"]["realizedPnl"], 0.0)
        self.assertEqual(account["realizedPnl"], before_mark["localPaperAccount"]["realizedPnl"])
        self.assertEqual(position["unrealizedPnl"], 50.0)
        self.assertEqual(account["unrealizedPnl"], 50.0)
        self.assertEqual(account["equity"], account["cash"] + position["marketValue"])
        self.assertEqual(repository.validate_local_consistency(evaluated_at=NOW + timedelta(seconds=1))["status"], "VALIDATED")

    def test_restart_cannot_change_account_economics(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_focused") / f"invariant-restart-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000", "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = _seed_long_position(quantity=10, price=100.0, store_path=store_path)
            seed_local_quote(repository, bid=105.0, ask=105.05, observed_at=NOW + timedelta(seconds=1))
            before_restart = deepcopy(repository.inventory_snapshot())

            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            restarted.validate_local_consistency(evaluated_at=NOW + timedelta(seconds=2))
            after_restart = restarted.inventory_snapshot()

            self.assertEqual(after_restart["localPaperAccount"]["cash"], before_restart["localPaperAccount"]["cash"])
            self.assertEqual(after_restart["localPaperAccount"]["equity"], before_restart["localPaperAccount"]["equity"])
            self.assertEqual(after_restart["localPaperAccount"]["realizedPnl"], before_restart["localPaperAccount"]["realizedPnl"])
            self.assertEqual(after_restart["localPaperAccount"]["unrealizedPnl"], before_restart["localPaperAccount"]["unrealizedPnl"])
            self.assertEqual(after_restart["localPositions"][0]["signedQuantity"], before_restart["localPositions"][0]["signedQuantity"])
        store_path.unlink(missing_ok=True)

    def test_global_risk_reads_local_inventory_without_owning_or_mutating_it(self) -> None:
        repository = _seed_long_position(quantity=100, price=100.0)
        local_before = deepcopy(repository.inventory_snapshot()["localPositions"])
        repository.snapshots["global_risk.read_only_position.algorithm_b.spy"] = {
            "algorithmId": "algorithm_b",
            "capitalPartitionId": "algorithm_b.paper.default",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 50,
            "marketValue": 5000.0,
            "readOnly": True,
            "sourceAuthority": "global_risk.read_only_aggregate",
        }

        inventory = repository.inventory_snapshot()
        consistency = repository.validate_local_consistency(evaluated_at=NOW)

        self.assertEqual(consistency["status"], "VALIDATED")
        self.assertEqual(inventory["localPositions"], local_before)
        self.assertFalse(any(position.get("algorithmId") == "algorithm_b" for position in inventory["localPositions"]))
        self.assertEqual(repository.snapshots["global_risk.read_only_position.algorithm_b.spy"]["quantity"], 50)


def _seed_long_position(*, quantity: int, price: float, store_path: Path | None = None) -> VotingEnsemblePaperExecutionRepository:
    repository = VotingEnsemblePaperExecutionRepository(store_path)
    repository.local_account_snapshot(observed_at=NOW)
    repository.inventory_ledger.apply_fill(
        client_order_id="invariant-buy",
        order_intent_id="intent-invariant-buy",
        symbol="SPY",
        side=Signal.BUY,
        requested_quantity=quantity,
        fill_price=price,
        filled_at=NOW,
    )
    return repository


def _snapshot_key(local_key: str) -> str:
    return f"voting_ensemble.paper_execution.{local_key}"


def _first_key(repository: VotingEnsemblePaperExecutionRepository, suffix: str) -> str:
    return next(key for key in repository.snapshots if suffix in key)


if __name__ == "__main__":
    unittest.main()
