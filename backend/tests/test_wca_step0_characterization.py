from __future__ import annotations

import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_TS = ROOT / "frontend" / "src" / "main.ts"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wca" / "golden_snapshots.json"


class WcaStep0CharacterizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_TS.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_current_wca_symbols_remain_inventoried(self) -> None:
        required_symbols = (
            "confidenceAggregationStrategies",
            "confidenceBaseWeights",
            "wcaBackendDecisionAsConfidenceResult",
            "wcaBackendTargetOrderRecommendation",
            "confidenceSystemWeightMultiplier",
            "confidenceHardFilters",
            "confidencePositionSizing",
            "confidenceDecisionSettings",
            "confidenceTradingSettings",
            "confidenceTargetOrderRecommendation",
            "maybeAutoSubmitConfidenceTargetOrder",
            "confidenceTradeHistory",
            "backtestConfidenceAggregation",
            "runConfidenceDailyBacktestFromPreparedCandles",
            "confidenceDecisionSettingsStorageKey",
            "confidenceTradingSettingsStorageKey",
            "confidenceTargetOrderOverridesStorageKey",
            "CONFIDENCE_TRADE_HISTORY_STORAGE_KEY",
            "CONFIDENCE_BACKTEST_RESULT_STORAGE_KEY",
        )

        for symbol in required_symbols:
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, self.source)

    def test_fixture_contains_at_least_100_representative_wca_snapshots(self) -> None:
        snapshots = self.fixture["snapshots"]
        signals = {snapshot["expected"]["signal"] for snapshot in snapshots}
        failed_filters = {filter_row["label"] for snapshot in snapshots for filter_row in snapshot["hardFilters"] if filter_row["status"] == "fail"}

        self.assertGreaterEqual(len(snapshots), 100)
        self.assertIn("Buy", signals)
        self.assertIn("Sell", signals)
        self.assertIn("Hold", signals)
        self.assertTrue({"Spread", "Liquidity", "ATR", "Time"}.issubset(failed_filters))

    def test_current_wca_decisions_reproduce_from_golden_snapshots(self) -> None:
        for snapshot in self.fixture["snapshots"]:
            with self.subTest(snapshot=snapshot["id"]):
                expected = snapshot["expected"]
                actual = reproduce_wca_decision(snapshot)

                self.assertEqual(actual["rawDecisionLabel"], expected["rawDecisionLabel"])
                self.assertEqual(actual["rawSignal"], expected["rawSignal"])
                self.assertEqual(actual["decisionLabel"], expected["decisionLabel"])
                self.assertEqual(actual["signal"], expected["signal"])
                self.assertEqual(actual["activeStrategyCount"], expected["activeStrategyCount"])
                self.assertEqual(actual["positionSize"], expected["positionSize"])
                self.assertEqual(actual["failedFilters"], expected["failedFilters"])
                assert_close_dict(
                    self,
                    actual,
                    expected,
                    keys=(
                        "buyScore",
                        "sellScore",
                        "netScore",
                        "activeWeight",
                        "normalizedNetScore",
                        "buyWeight",
                        "sellWeight",
                        "buyAgreement",
                        "sellAgreement",
                        "buyAverageConfidence",
                        "sellAverageConfidence",
                    ),
                )
                assert_close_dict(
                    self,
                    actual["sizing"],
                    expected["sizing"],
                    keys=(
                        "signalStrength",
                        "sizeMultiplier",
                        "riskDollars",
                        "stopDistance",
                        "sharesByRisk",
                        "sharesByOrder",
                        "sharesByCapital",
                        "sharesByBuyingPower",
                        "finalQuantity",
                        "availableBuyingPower",
                        "maxPositionDollars",
                        "currentPositionValue",
                    ),
                )


def reproduce_wca_decision(snapshot: dict) -> dict:
    strategies = snapshot["strategySignals"]
    buy_score = round4(sum(row["effectiveWeight"] * row["confidence"] for row in strategies if row["signal"] == "buy"))
    sell_score = round4(sum(row["effectiveWeight"] * row["confidence"] for row in strategies if row["signal"] == "sell"))
    active_weight = round4(sum(row["effectiveWeight"] for row in strategies if row["signal"] != "hold"))
    active_count = sum(1 for row in strategies if row["signal"] != "hold")
    buy_weight = round4(sum(row["effectiveWeight"] for row in strategies if row["signal"] == "buy"))
    sell_weight = round4(sum(row["effectiveWeight"] for row in strategies if row["signal"] == "sell"))
    net_score = round4(buy_score - sell_score)
    normalized_net_score = round4(net_score / active_weight) if active_weight else 0
    buy_agreement = round4(buy_weight / active_weight) if active_weight else 0
    sell_agreement = round4(sell_weight / active_weight) if active_weight else 0
    buy_average_confidence = round4(buy_score / buy_weight) if buy_weight else 0
    sell_average_confidence = round4(sell_score / sell_weight) if sell_weight else 0
    settings = snapshot["decisionSettings"]
    enough_active = active_count >= settings["minimumActiveStrategies"]
    buy_requirements_met = (
        enough_active
        and buy_agreement >= settings["minimumDirectionalAgreement"]
        and buy_average_confidence >= settings["minimumAverageConfidence"]
    )
    sell_requirements_met = (
        enough_active
        and sell_agreement >= settings["minimumDirectionalAgreement"]
        and sell_average_confidence >= settings["minimumAverageConfidence"]
    )
    if normalized_net_score >= settings["strongBuyThreshold"] and buy_requirements_met:
        raw_label = "Strong Buy"
    elif normalized_net_score >= settings["buyThreshold"] and buy_requirements_met:
        raw_label = "Buy"
    elif normalized_net_score <= settings["strongSellThreshold"] and sell_requirements_met:
        raw_label = "Strong Sell"
    elif normalized_net_score <= settings["sellThreshold"] and sell_requirements_met:
        raw_label = "Sell"
    else:
        raw_label = "Hold"
    raw_signal = "Buy" if raw_label in {"Strong Buy", "Buy"} else "Sell" if raw_label in {"Strong Sell", "Sell"} else "Hold"
    failed_filters = [row["label"] for row in snapshot["hardFilters"] if row["status"] == "fail"]
    signal = "Hold" if failed_filters else raw_signal
    decision_label = "Hold" if failed_filters else raw_label
    sizing = reproduce_wca_sizing(snapshot, signal, normalized_net_score)
    return {
        "buyScore": buy_score,
        "sellScore": sell_score,
        "netScore": net_score,
        "activeWeight": active_weight,
        "normalizedNetScore": normalized_net_score,
        "activeStrategyCount": active_count,
        "buyWeight": buy_weight,
        "sellWeight": sell_weight,
        "buyAgreement": buy_agreement,
        "sellAgreement": sell_agreement,
        "buyAverageConfidence": buy_average_confidence,
        "sellAverageConfidence": sell_average_confidence,
        "rawDecisionLabel": raw_label,
        "rawSignal": raw_signal,
        "decisionLabel": decision_label,
        "signal": signal,
        "positionSize": sizing["finalQuantity"],
        "sizing": sizing,
        "failedFilters": failed_filters,
    }


def reproduce_wca_sizing(snapshot: dict, signal: str, normalized_net_score: float) -> dict:
    inputs = snapshot["sizingInputs"]
    signal_strength = abs(normalized_net_score)
    size_multiplier = confidence_size_multiplier(signal_strength)
    account_equity = inputs["accountEquity"]
    price = max(inputs["price"], 0.01)
    risk_dollars = account_equity * (inputs["baseRiskPercent"] / 100) * size_multiplier
    stop_distance = max(inputs["atr"] * inputs["atrStopMultiplier"], price * (inputs["minimumStopDistancePercent"] / 100))
    shares_by_risk = risk_dollars / stop_distance if stop_distance > 0 else 0
    shares_by_order = (account_equity * (inputs["orderAllocationPercent"] / 100)) / price
    shares_by_capital = (account_equity * (inputs["maxPositionPercent"] / 100)) / price
    max_position_dollars = account_equity * (inputs["maxPositionPercent"] / 100)
    daily_buying_power_dollars = account_equity * (inputs["dailyAllocationPercent"] / 100)
    available_buying_power = max(0, min(max_position_dollars, daily_buying_power_dollars) - inputs["currentPositionValue"])
    shares_by_buying_power = available_buying_power / price
    shares_by_liquidity = inputs["latestVolume"] * (inputs["maxParticipationPercent"] / 100)
    shares_by_max = inputs["maxAllowedShares"] if inputs["maxAllowedShares"] > 0 else math.inf
    caps = (
        ("risk budget", shares_by_risk),
        ("order limit", shares_by_order),
        ("max position", shares_by_capital),
        ("buying power", shares_by_buying_power),
        ("liquidity participation", shares_by_liquidity),
        ("max shares", shares_by_max),
    )
    limiting_factor, limiting_shares = min(caps, key=lambda item: item[1])
    raw_quantity = min(value for _, value in caps)
    final_quantity = (
        0
        if signal == "Hold" or size_multiplier <= 0 or stop_distance <= 0
        else max(0, math.floor(raw_quantity if math.isfinite(raw_quantity) else 0))
    )
    if signal == "Hold":
        blocked_reason = "final signal is Hold"
    elif size_multiplier <= 0:
        blocked_reason = f"signal strength {signal_strength:.4f} is below 50%"
    elif final_quantity < 1:
        blocked_reason = f"{limiting_factor} allows {limiting_shares:.2f} shares, below 1 share"
    else:
        blocked_reason = ""
    return {
        "signalStrength": round4(signal_strength),
        "sizeMultiplier": size_multiplier,
        "riskDollars": round(risk_dollars, 2),
        "stopDistance": round4(stop_distance),
        "sharesByRisk": round4(shares_by_risk),
        "sharesByOrder": round4(shares_by_order),
        "sharesByCapital": round4(shares_by_capital),
        "sharesByBuyingPower": round4(shares_by_buying_power),
        "sharesByLiquidity": round4(shares_by_liquidity),
        "finalQuantity": final_quantity,
        "availableBuyingPower": round(available_buying_power, 2),
        "accountEquity": account_equity,
        "maxPositionDollars": round(max_position_dollars, 2),
        "currentPositionValue": round(inputs["currentPositionValue"], 2),
        "limitingFactor": limiting_factor,
        "blockedReason": blocked_reason,
    }


def confidence_size_multiplier(signal_strength: float) -> float:
    if signal_strength >= 0.8:
        return 1
    if signal_strength >= 0.7:
        return 0.75
    if signal_strength >= 0.6:
        return 0.5
    if signal_strength >= 0.5:
        return 0.25
    return 0


def round4(value: float) -> float:
    return round(value, 4)


def assert_close_dict(testcase: unittest.TestCase, actual: dict, expected: dict, *, keys: tuple[str, ...]) -> None:
    for key in keys:
        testcase.assertAlmostEqual(actual[key], expected[key], places=4, msg=key)


if __name__ == "__main__":
    unittest.main()
