from __future__ import annotations

import unittest

from backend.app.algorithms.voting_ensemble.risk_budget import resolve_voting_ensemble_risk_budget
from backend.app.algorithms.voting_ensemble.service import _contract_multiplier


BASE = {
    "candidateSignal": "BUY",
    "gatesPassed": True,
    "netEdgePassed": True,
    "riskPerTradePercent": 0.5,
    "orderAllocationPercent": 100.0,
    "dailyAllocationPercent": 100.0,
    "maximumPositionPercent": 100.0,
    "profileMaximumShares": 100000,
    "availableBuyingPower": 1000000.0,
    "availableFillableQuantity": 100000.0,
    "currentOneMinuteVolume": 10000000.0,
    "maximumVolumeParticipationPercent": 100.0,
    "globalExposureAllowanceDollars": 10000000.0,
    "localExposureAllowanceDollars": 10000000.0,
    # Without these the family-support multiplier is zero and nothing sizes at all.
    "voteEdge": 0.8,
    "independentFamilySupport": 2,
    "minimumIndependentFamilySupport": 2,
}


def budget(*, entry_price: float, stop_distance: float = 5.0, equity: float = 100000.0, **overrides):
    return resolve_voting_ensemble_risk_budget(
        {**BASE, **overrides}, equity=equity, entry_price=entry_price, stop_distance=stop_distance
    )


class ContractMultiplierTest(unittest.TestCase):
    """Sizing turns a dollar budget into a quantity, which needs dollars per unit.

    Every cap divides dollars by a price or a stop distance, which silently assumes one unit
    moves one dollar per dollar of price. That holds for a share and for nothing else. An MES
    point is worth $5, so the share arithmetic returns a quantity five times too large --
    plausible, not obviously broken, and wrong.
    """

    def test_the_registry_supplies_the_multiplier(self) -> None:
        self.assertEqual(_contract_multiplier("SPY"), 1.0)
        self.assertEqual(_contract_multiplier("MES"), 5.0)
        self.assertEqual(_contract_multiplier("MNQ"), 2.0)

    def test_an_unregistered_symbol_is_treated_as_a_share(self) -> None:
        self.assertEqual(_contract_multiplier("XLK"), 1.0)
        self.assertEqual(_contract_multiplier(""), 1.0)

    def test_equity_sizing_is_unchanged(self) -> None:
        """The regression that matters: no multiplier must equal a multiplier of one."""
        absent = budget(entry_price=500.0)
        explicit = budget(entry_price=500.0, contractMultiplier=1.0)

        self.assertEqual(absent.quantity, explicit.quantity)
        self.assertEqual(absent.planned_risk, explicit.planned_risk)
        self.assertGreater(absent.quantity, 0)

    def test_risk_per_contract_scales_with_the_point_value(self) -> None:
        """A 5-point stop on MES risks $25 a contract, not $5."""
        shares = budget(entry_price=500.0, contractMultiplier=1.0)
        contracts = budget(entry_price=500.0, contractMultiplier=5.0)

        self.assertEqual(shares.quantity, contracts.quantity * 5)

    def test_the_planned_risk_is_reported_in_dollars(self) -> None:
        sized = budget(entry_price=5000.0, contractMultiplier=5.0)

        self.assertEqual(sized.planned_risk, round(sized.quantity * 5.0 * 5.0, 6))

    def test_notional_binds_before_risk_on_a_large_contract(self) -> None:
        """Worth knowing which cap is holding: MES notional is the contract's, not a share's.

        At 5000 index points an MES contract carries $25,000 of notional, so $100k of equity
        holds four of them however small the stop is. This is conservative -- futures post
        margin rather than notional -- but it is the cap that binds, and sizing that ignored
        the multiplier would have allowed twenty.
        """
        sized = budget(entry_price=5000.0, stop_distance=1.0, contractMultiplier=5.0)

        self.assertEqual(sized.quantity, 4)
        self.assertIn("voting_ensemble.risk_budget.cap.position_notional", sized.reason_codes)

    def test_a_missing_or_absurd_multiplier_does_not_divide_by_zero(self) -> None:
        for value in (0.0, -5.0, None, "not a number"):
            with self.subTest(value=value):
                sized = budget(entry_price=500.0, contractMultiplier=value)
                self.assertGreaterEqual(sized.quantity, 0)

    def test_the_multiplier_is_part_of_the_recorded_sizing_configuration(self) -> None:
        """Two runs that sized different instruments must not claim the same configuration.

        The hash is what a recorded baseline is reproduced against, so an input that changes
        the quantity has to be inside it.
        """
        shares = budget(entry_price=5000.0, contractMultiplier=1.0).to_payload()["configuration_hash"]
        contracts = budget(entry_price=5000.0, contractMultiplier=5.0).to_payload()["configuration_hash"]
        again = budget(entry_price=5000.0, contractMultiplier=5.0).to_payload()["configuration_hash"]

        self.assertNotEqual(shares, contracts)
        self.assertEqual(contracts, again)


if __name__ == "__main__":
    unittest.main()
