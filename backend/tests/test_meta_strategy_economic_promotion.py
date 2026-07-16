from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.meta_strategy_training import (
    MetaTrainingConfig,
    evaluate_economic_promotion,
    train_meta_strategy_baselines,
)
from backend.tests.test_meta_strategy_champion_challengers import patched_optional_boosters_unavailable
from backend.tests.test_meta_strategy_nested_training import labeled_row, patched_training_io


class MetaStrategyEconomicPromotionTest(unittest.TestCase):
    def test_training_report_explains_economic_promotion_not_accuracy_only(self) -> None:
        with patched_training_io([labeled_row(index) for index in range(180)]), patched_optional_boosters_unavailable():
            result = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
            )

        promotion = result["metrics"]["economicPromotion"]
        self.assertIn("explanation", promotion)
        self.assertIn("rejectedReasonCodes", promotion)
        self.assertFalse(promotion["classificationAccuracyUsedForPromotion"])
        self.assertIn("deterministicBaseline", result["finalHoldoutMetrics"]["economic"])
        self.assertIn("netExpectancyAfterCosts", result["models"]["logistic_regression_champion"]["economicMetrics"])

    def test_model_with_higher_pnl_but_unacceptable_drawdown_is_rejected(self) -> None:
        config = MetaTrainingConfig(
            maximumPromotionDrawdownMultiple=1.0,
            minimumPositiveEconomicOuterFolds=2,
            minimumDirectionalTradesPerSide=1,
        ).normalized()
        model_metrics = economic_fixture(
            net_pnl=20.0,
            expectancy=2.0,
            drawdown=12.0,
            buy_expectancy=1.0,
            sell_expectancy=1.0,
        )
        baseline_metrics = economic_fixture(net_pnl=8.0, expectancy=0.5, drawdown=4.0)
        promotion = evaluate_economic_promotion(
            model_metrics=model_metrics,
            baseline_metrics=baseline_metrics,
            outer_summary={"positiveEconomicFolds": 2, "singleFoldProfitShare": 0.5},
            calibration={"probabilitySizingApproved": True},
            config=config,
        )

        self.assertFalse(promotion["promoted"])
        self.assertIn("economic.drawdown_unacceptable", promotion["rejectedReasonCodes"])

    def test_profit_concentrated_in_one_regime_is_flagged(self) -> None:
        config = MetaTrainingConfig(
            maximumSingleRegimeProfitShare=0.70,
            minimumPositiveEconomicOuterFolds=2,
            minimumDirectionalTradesPerSide=1,
        ).normalized()
        model_metrics = economic_fixture(
            net_pnl=20.0,
            expectancy=2.0,
            drawdown=1.0,
            regime_pnl={"strong_trend": 20.0, "range": 0.0},
        )
        baseline_metrics = economic_fixture(net_pnl=5.0, expectancy=0.1, drawdown=2.0)
        promotion = evaluate_economic_promotion(
            model_metrics=model_metrics,
            baseline_metrics=baseline_metrics,
            outer_summary={"positiveEconomicFolds": 2, "singleFoldProfitShare": 0.5},
            calibration={"probabilitySizingApproved": True},
            config=config,
        )

        self.assertFalse(promotion["promoted"])
        self.assertIn("economic.one_regime_profit_concentration", promotion["rejectedReasonCodes"])
        self.assertIn("economic.profit_concentrated_in_one_regime", promotion["warningFlags"])


def economic_fixture(
    *,
    net_pnl: float,
    expectancy: float,
    drawdown: float,
    buy_expectancy: float = 0.5,
    sell_expectancy: float = 0.5,
    regime_pnl: dict[str, float] | None = None,
) -> dict:
    regimes = regime_pnl or {"range": net_pnl / 2, "trend": net_pnl / 2}
    return {
        "netPnl": net_pnl,
        "netExpectancyAfterCosts": expectancy,
        "maximumDrawdown": drawdown,
        "worstDay": -1.0,
        "tradeCoverage": 0.5,
        "tradeRejectionRate": 0.5,
        "buyPerformance": {"trades": 4, "expectancy": buy_expectancy},
        "sellPerformance": {"trades": 4, "expectancy": sell_expectancy},
        "performanceByRegime": {
            regime: {"trades": 4, "netPnl": pnl, "expectancy": pnl / 4}
            for regime, pnl in regimes.items()
        },
        "calibrationError": 0.02,
    }


if __name__ == "__main__":
    unittest.main()
