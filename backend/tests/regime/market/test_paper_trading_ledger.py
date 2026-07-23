import shutil
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.regime import paper_trading_ledger
from backend.app.algorithms.regime.paper_trading_ledger import (
    REGIME_PAPER_TRADING_LEDGER_VERSION,
    PaperTradingProofPolicy,
    normalize_paper_trading_proof_record,
    read_regime_paper_trading_proof_ledger,
    record_regime_paper_trading_proof,
    validate_regime_paper_trading_proof_ledger,
)
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


class RegimePaperTradingLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path("backend/.test_artifacts") / f"regime_paper_ledger_{uuid.uuid4().hex}"
        self.original_dir = paper_trading_ledger.REGIME_PAPER_TRADING_LEDGER_DIR
        paper_trading_ledger.REGIME_PAPER_TRADING_LEDGER_DIR = self.scratch

    def tearDown(self) -> None:
        paper_trading_ledger.REGIME_PAPER_TRADING_LEDGER_DIR = self.original_dir
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_normalized_record_proves_decision_latency_cost_fill_and_outcome(self) -> None:
        record = normalize_paper_trading_proof_record(_proof(0))

        self.assertEqual(record["ledgerVersion"], REGIME_PAPER_TRADING_LEDGER_VERSION)
        self.assertEqual(record["activationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertEqual(record["latency"]["decisionToSubmissionLatencyMs"], 250.0)
        self.assertEqual(record["latency"]["submissionToFillLatencyMs"], 500.0)
        self.assertEqual(record["costs"]["realizedVsEstimatedCostError"], 0.01)
        self.assertEqual(record["fill"]["partialFillFraction"], 1.0)
        self.assertEqual(record["realizedOutcome"]["status"], "completed")
        self.assertTrue(all(record["requiredProofFieldsPresent"].values()))

    def test_record_and_read_paper_trading_ledger_idempotently(self) -> None:
        first = record_regime_paper_trading_proof(_proof(0))
        updated = _proof(0)
        updated["realizedOutcome"] = {**updated["realizedOutcome"], "netPnl": 7.5}
        second = record_regime_paper_trading_proof(updated)
        ledger = read_regime_paper_trading_proof_ledger("SPY", date="2026-07-23")

        self.assertTrue(first["saved"])
        self.assertEqual(second["records"], 1)
        self.assertEqual(ledger["ledgerName"], paper_trading_ledger.REGIME_PAPER_TRADING_LEDGER_NAME)
        self.assertEqual(len(ledger["records"]), 1)
        self.assertEqual(ledger["records"][0]["realizedOutcome"]["netPnl"], 7.5)

    def test_validation_passes_diagnostics_but_stays_inactive_by_default(self) -> None:
        records = [normalize_paper_trading_proof_record(_proof(index)) for index in range(4)]

        report = validate_regime_paper_trading_proof_ledger(
            records,
            policy=PaperTradingProofPolicy(minimum_records=4, minimum_completed_outcomes=4),
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["validationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertTrue(report["diagnosticPassed"])
        self.assertFalse(report["validationAppliedToLivePaperTrading"])
        self.assertEqual(report["fillRate"], 1.0)
        self.assertEqual(report["positiveNetValueRate"], 1.0)

    def test_validation_can_be_explicitly_applied_during_live_paper_trading(self) -> None:
        report = validate_regime_paper_trading_proof_ledger(
            [normalize_paper_trading_proof_record(_proof(index)) for index in range(4)],
            policy=PaperTradingProofPolicy(minimum_records=4, minimum_completed_outcomes=4),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["validationStatus"], "pass")
        self.assertTrue(report["validationAppliedToLivePaperTrading"])

    def test_missing_cost_fill_and_outcome_proof_fails_validation(self) -> None:
        incomplete = {
            "sourceMode": "paper",
            "symbol": "SPY",
            "decisionId": "regime-decision-incomplete",
            "decisionTimestamp": START.isoformat(),
        }

        report = validate_regime_paper_trading_proof_ledger(
            [incomplete],
            policy=PaperTradingProofPolicy(minimum_records=1, minimum_completed_outcomes=1),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00Z",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertIn("regime.paper_ledger.insufficient_completed_outcomes", report["reasonCodes"])
        self.assertIn("regime.paper_ledger.required_fields_missing", report["reasonCodes"])
        self.assertIn("regime.paper_ledger.fill_rate_too_low", report["reasonCodes"])


def _proof(index: int) -> dict:
    decision_at = START + timedelta(minutes=index)
    submitted_at = decision_at + timedelta(milliseconds=250)
    filled_at = submitted_at + timedelta(milliseconds=500)
    exit_at = filled_at + timedelta(minutes=5)
    return {
        "proofId": f"proof-{index}",
        "sourceMode": "paper",
        "symbol": "SPY",
        "decisionId": f"regime-decision-{index}",
        "orderIntentId": f"regime-order-{index}",
        "eventTimestamp": decision_at.isoformat(),
        "barFinalizationTimestamp": (decision_at - timedelta(milliseconds=100)).isoformat(),
        "featureReadyTimestamp": (decision_at - timedelta(milliseconds=50)).isoformat(),
        "decisionTimestamp": decision_at.isoformat(),
        "orderSubmissionTimestamp": submitted_at.isoformat(),
        "signal": "Buy",
        "rawRegime": "strong_uptrend",
        "confidence": 0.82,
        "tradeAllowed": True,
        "costs": {
            "estimatedExecutionCost": 0.04,
            "realizedExecutionCost": 0.05,
            "fees": 0.005,
            "slippage": 0.015,
        },
        "fill": {
            "status": "FILLED",
            "clientOrderId": f"client-{index}",
            "brokerOrderId": f"broker-{index}",
            "submittedQuantity": 100,
            "filledQuantity": 100,
            "averageFillPrice": 100.02,
            "submittedAt": submitted_at.isoformat(),
            "filledAt": filled_at.isoformat(),
        },
        "realizedOutcome": {
            "status": "completed",
            "exitReason": "target",
            "exitTimestamp": exit_at.isoformat(),
            "exitPrice": 100.12,
            "grossPnl": 10.0,
            "netPnl": 8.0,
            "incrementalRealizedNetValueAfterExecutionCosts": 0.08,
        },
    }


if __name__ == "__main__":
    unittest.main()
