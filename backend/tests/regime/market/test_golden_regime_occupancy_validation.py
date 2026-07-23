import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.occupancy_validation import (
    DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS,
    REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION,
    validate_golden_regime_occupancy,
)
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


class GoldenRegimeOccupancyValidationTest(unittest.TestCase):
    def test_reasonable_holdout_occupancy_is_inactive_by_default(self) -> None:
        report = validate_golden_regime_occupancy(
            _ledger(),
            partition="final_holdout",
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationVersion"], REGIME_GOLDEN_OCCUPANCY_VALIDATION_VERSION)
        self.assertEqual(report["validationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertTrue(report["diagnosticPassed"])
        self.assertFalse(report["validationAppliedToPromotion"])
        self.assertEqual(report["missingGoldenRegimes"], [])

    def test_explicit_paper_validation_can_apply_passing_occupancy(self) -> None:
        report = validate_golden_regime_occupancy(
            _ledger(),
            partition="paper_shadow",
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "pass")
        self.assertTrue(report["validationAppliedToPromotion"])
        self.assertGreaterEqual(report["distinctRegimesObserved"], len(DEFAULT_GOLDEN_REGIME_OCCUPANCY_BOUNDS))

    def test_missing_golden_regime_fails_occupancy_validation(self) -> None:
        ledger = [row for row in _ledger() if row["raw_regime"] != "event_risk"]

        report = validate_golden_regime_occupancy(
            ledger,
            partition="final_holdout",
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertIn("event_risk", report["missingGoldenRegimes"])
        self.assertIn(
            "regime.occupancy.golden_regime_underrepresented:event_risk",
            report["reasonCodes"],
        )

    def test_training_partition_and_non_chronological_records_do_not_count_as_oos_proof(self) -> None:
        ledger = _ledger()
        ledger[10], ledger[11] = ledger[11], ledger[10]

        report = validate_golden_regime_occupancy(
            ledger,
            partition="training",
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertFalse(report["outOfSample"])
        self.assertFalse(report["chronological"])
        self.assertIn("regime.occupancy.out_of_sample_partition_required", report["reasonCodes"])
        self.assertIn("regime.occupancy.timestamps_not_strictly_increasing", report["reasonCodes"])


def _ledger() -> list[dict[str, str]]:
    regimes = (
        ["strong_uptrend"] * 40
        + ["range_bound"] * 20
        + ["low_volatility_quiet"] * 20
        + ["intraday_expansion"] * 15
        + ["opening_breakout"] * 10
        + ["failed_breakout_reversal"] * 10
        + ["liquidity_stress"] * 10
        + ["event_risk"] * 5
        + ["weak_uptrend"] * 70
    )
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    return [
        {
            "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            "raw_regime": regime,
        }
        for index, regime in enumerate(regimes)
    ]


if __name__ == "__main__":
    unittest.main()
