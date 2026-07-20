"""Economic evaluation and promotion policy for Meta-Strategy training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def economic_performance(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("economic_performance", *args, **kwargs)


def trade_economic_result(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("trade_economic_result", *args, **kwargs)


def evaluate_economic_promotion(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("evaluate_economic_promotion", *args, **kwargs)


def summarize_outer_economic_results(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("summarize_outer_economic_results", *args, **kwargs)


def evaluate_model_economics(
    *,
    predictions: list[str],
    rows: list[dict[str, Any]],
    probability_distributions: list[dict[str, float]] | None = None,
    random_seed: int = 17,
) -> dict[str, Any]:
    metrics = economic_performance(
        predictions,
        rows,
        probability_distributions=probability_distributions,
        random_seed=random_seed,
    )
    return {
        "netExpectancy": metrics.get("netExpectancyAfterCosts"),
        "netPnl": metrics.get("netPnl"),
        "drawdown": metrics.get("maximumDrawdown"),
        "profitFactor": metrics.get("profitFactor"),
        "coverage": metrics.get("tradeCoverage"),
        "rejectionRate": metrics.get("tradeRejectionRate"),
        "performanceBySide": {
            "BUY": metrics.get("buyPerformance", {}),
            "SELL": metrics.get("sellPerformance", {}),
        },
        "performanceByRegime": metrics.get("performanceByRegime", {}),
        "rawMetrics": metrics,
        "reasonCodes": ("meta_strategy.economic.metrics_evaluated",),
    }


def evaluate_economic_promotion_report(
    *,
    model_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    outer_summary: dict[str, Any],
    calibration: dict[str, Any],
    config: Any,
) -> dict[str, Any]:
    promotion = evaluate_economic_promotion(
        model_metrics=model_metrics,
        baseline_metrics=baseline_metrics,
        outer_summary=outer_summary,
        calibration=calibration,
        config=config,
    )
    rejected = list(promotion.get("rejectedReasonCodes") or ())
    if "economic.one_fold_profit_concentration" in rejected and "economic.fold_profit_concentration" not in rejected:
        rejected.append("economic.fold_profit_concentration")
    return {
        **promotion,
        "rejectedReasonCodes": rejected,
        "classificationAccuracyUsedForPromotion": False,
        "promotionDependsOn": (
            "net_expectancy_after_costs",
            "drawdown",
            "profit_factor",
            "coverage",
            "rejection_rate",
            "side_performance",
            "regime_performance",
            "fold_concentration",
            "calibration_approval",
        ),
        "reasonCodes": tuple(
            dict.fromkeys(
                [
                    "meta_strategy.promotion.not_accuracy_only",
                    *(promotion.get("acceptedReasonCodes") or ()),
                    *rejected,
                    *(promotion.get("warningFlags") or ()),
                ]
            )
        ),
    }


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "economic_performance",
    "evaluate_model_economics",
    "evaluate_economic_promotion",
    "evaluate_economic_promotion_report",
    "summarize_outer_economic_results",
    "trade_economic_result",
]
