from __future__ import annotations

import unittest

from backend.tests.test_voting_ensemble_local_gates import evaluate_service_candidate


class SizedEconomicsTest(unittest.TestCase):
    """Costs and cost gates are evaluated on the sized order, not a zero-share candidate.

    Economics used to run before sizing, so participation, impact, the per-share TAF and
    the fillable-quantity check all described an order of no shares, and the candidate's
    expected value was a unitless score compared against a dollar threshold.
    """

    def test_economics_see_the_sized_quantity_and_expected_value_is_dollars(self) -> None:
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000})

        self.assertEqual(result["final_signal"], "Buy")
        economics = result["execution_economics"]
        risk_budget = result["risk_budget"]
        candidate = result["candidate"]
        self.assertGreater(risk_budget["quantity"], 0)
        self.assertEqual(economics["sizedQuantity"], risk_budget["quantity"])
        self.assertGreater(economics["participationRate"], 0.0)
        self.assertAlmostEqual(candidate["expectedValue"], round(economics["predictedNetEdgeDollars"] * economics["sizedQuantity"], 6), places=6)
        self.assertGreater(candidate["expectedValue"], 0.01)

    def test_a_gate_failure_zeroes_the_final_size_but_the_costs_were_still_real(self) -> None:
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000, "tradingEnabled": False})

        self.assertEqual(result["final_signal"], "Hold")
        self.assertGreater(result["execution_economics"]["sizedQuantity"], 0)
        self.assertEqual(result["risk_budget"]["quantity"], 0)
        self.assertIn("voting_ensemble.risk_budget.gates_failed", result["risk_budget"]["reason_codes"])

    def test_the_fillable_quantity_check_compares_against_the_real_order(self) -> None:
        # Ten shares quoted on the ask against an order sized well above that.
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000}, nbbo={"askSize": 10})

        self.assertEqual(result["execution_economics"]["availableFillableQuantity"], 10)
        # The liquidity cap sizes the order down to what is quoted rather than blocking it.
        self.assertLessEqual(result["risk_budget"]["quantity"], 10)
        self.assertGreater(result["risk_budget"]["quantity"], 0)

    def test_a_thin_net_edge_in_dollars_is_what_the_threshold_measures(self) -> None:
        # Gross edge just above the sized cost (about 0.34 a share on this thin tape, most of
        # it market impact at 2.4% participation): a positive but small net edge.
        result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.40, "currentOneMinuteVolume": 100000})

        economics = result["execution_economics"]
        self.assertGreater(economics["predictedNetEdgeDollars"], 0.0)
        expected_value = result["candidate"]["expectedValue"]
        self.assertAlmostEqual(expected_value, round(economics["predictedNetEdgeDollars"] * economics["sizedQuantity"], 6), places=6)


if __name__ == "__main__":
    unittest.main()
