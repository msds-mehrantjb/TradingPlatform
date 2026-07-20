# coverage: ignore file
from __future__ import annotations

import json
import hashlib
import math
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any


LABELS = ["BUY", "SELL", "HOLD"]
LABEL_TO_INDEX = {label: index for index, label in enumerate(LABELS)}
DEFAULT_RANDOM_SEED = 17
DEFAULT_META_LABEL_VERSION = "candidate_triple_barrier_v1"
META_STRATEGY_FEATURE_SCHEMA_VERSION = "meta_strategy_training_feature_vector_v2"
META_MODEL_V2_TRAINING_REPORT_VERSION = "meta_model_v2_training_validation_v1"
DETERMINISTIC_V2_BASELINE_VERSION = "deterministic_v2_static_baseline_v1"
DETERMINISTIC_BASELINE_NAME = "family_aware_deterministic"
BASELINE_NAMES = [
    DETERMINISTIC_BASELINE_NAME,
    "simple_voting",
    "weighted_voting",
    "confidence_score_aggregation",
    "market_regime_strategy_selection",
]
DIRECTIONAL_FAMILIES = ["trend", "breakout", "mean_reversion", "reversal", "vwap", "event"]
TREND_REGIME_FAMILIES = ["trend", "breakout", "vwap", "event"]
REVERSION_REGIME_FAMILIES = ["mean_reversion", "reversal", "vwap", "event"]
SIGNAL_MAP = {
    "BUY": 1.0,
    "Buy": 1.0,
    "buy": 1.0,
    "SELL": -1.0,
    "Sell": -1.0,
    "sell": -1.0,
    "HOLD": 0.0,
    "Hold": 0.0,
    "hold": 0.0,
    "NO-TRADE": 0.0,
    "No-trade": 0.0,
    "no_trade": 0.0,
    "no signal": 0.0,
    "No signal": 0.0,
    "NO SIGNAL": 0.0,
}


@dataclass(frozen=True)
class MetaTrainingConfig:
    minimumTotalCandidates: int = 120
    minimumBuyCandidates: int = 15
    minimumSellCandidates: int = 15
    minimumPositiveOutcomes: int = 20
    minimumNegativeOutcomes: int = 20
    minimumCandidatesPerOuterFold: int = 20
    minimumTradingSessions: int = 4
    minimumRegimesRepresented: int = 2
    minimumCalibrationRows: int = 60
    minimumIsotonicRows: int = 80
    maximumCalibrationBrier: float = 0.28
    maximumCalibrationLogLoss: float = 1.20
    maximumCalibrationEce: float = 0.12
    outerFolds: int = 3
    innerFolds: int = 3
    finalTestFraction: float = 0.20
    maximumHoldingHorizonMinutes: int = 30
    embargoMinutes: int = 30
    randomSeed: int = DEFAULT_RANDOM_SEED
    minimumNetExpectancyImprovement: float = 0.0
    maximumPromotionDrawdownMultiple: float = 1.05
    minimumPositiveEconomicOuterFolds: int = 2
    maximumSingleFoldProfitShare: float = 0.75
    maximumSingleRegimeProfitShare: float = 0.80
    minimumPromotionTradeCoverage: float = 0.05
    maximumPromotionTradeRejectionRate: float = 0.95
    minimumDirectionalTradesPerSide: int = 1

    def normalized(self) -> "MetaTrainingConfig":
        horizon = max(1, int(self.maximumHoldingHorizonMinutes))
        return MetaTrainingConfig(
            minimumTotalCandidates=max(1, int(self.minimumTotalCandidates)),
            minimumBuyCandidates=max(0, int(self.minimumBuyCandidates)),
            minimumSellCandidates=max(0, int(self.minimumSellCandidates)),
            minimumPositiveOutcomes=max(0, int(self.minimumPositiveOutcomes)),
            minimumNegativeOutcomes=max(0, int(self.minimumNegativeOutcomes)),
            minimumCandidatesPerOuterFold=max(2, int(self.minimumCandidatesPerOuterFold)),
            minimumTradingSessions=max(1, int(self.minimumTradingSessions)),
            minimumRegimesRepresented=max(1, int(self.minimumRegimesRepresented)),
            minimumCalibrationRows=max(1, int(self.minimumCalibrationRows)),
            minimumIsotonicRows=max(1, int(self.minimumIsotonicRows)),
            maximumCalibrationBrier=max(0.0, float(self.maximumCalibrationBrier)),
            maximumCalibrationLogLoss=max(0.0, float(self.maximumCalibrationLogLoss)),
            maximumCalibrationEce=max(0.0, float(self.maximumCalibrationEce)),
            outerFolds=max(1, int(self.outerFolds)),
            innerFolds=max(1, int(self.innerFolds)),
            finalTestFraction=min(0.5, max(0.05, float(self.finalTestFraction))),
            maximumHoldingHorizonMinutes=horizon,
            embargoMinutes=max(horizon, int(self.embargoMinutes)),
            randomSeed=int(self.randomSeed),
            minimumNetExpectancyImprovement=float(self.minimumNetExpectancyImprovement),
            maximumPromotionDrawdownMultiple=max(0.0, float(self.maximumPromotionDrawdownMultiple)),
            minimumPositiveEconomicOuterFolds=max(1, int(self.minimumPositiveEconomicOuterFolds)),
            maximumSingleFoldProfitShare=min(1.0, max(0.0, float(self.maximumSingleFoldProfitShare))),
            maximumSingleRegimeProfitShare=min(1.0, max(0.0, float(self.maximumSingleRegimeProfitShare))),
            minimumPromotionTradeCoverage=min(1.0, max(0.0, float(self.minimumPromotionTradeCoverage))),
            maximumPromotionTradeRejectionRate=min(1.0, max(0.0, float(self.maximumPromotionTradeRejectionRate))),
            minimumDirectionalTradesPerSide=max(0, int(self.minimumDirectionalTradesPerSide)),
        )


LOGISTIC_HYPERPARAMETER_GRID = (
    {"epochs": 70, "learningRate": 0.035, "l2": 0.0005},
    {"epochs": 110, "learningRate": 0.025, "l2": 0.0010},
    {"epochs": 150, "learningRate": 0.020, "l2": 0.0015},
)
RANDOM_FOREST_HYPERPARAMETER_GRID = (
    {"treeCount": 40, "maxDepth": 4},
    {"treeCount": 70, "maxDepth": 5},
)


def save_latest_training_status(decision_snapshot_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    latest_path = decision_snapshot_dir / "latest_meta_strategy_training.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**result, "latestPath": str(latest_path)}


def train_meta_strategy_baselines(
    *,
    decision_snapshot_dir: Path,
    symbol: str,
    session_date: str | None = None,
    min_rows: int = 30,
    test_fraction: float = 0.25,
    minimum_total_candidates: int | None = None,
    minimum_buy_candidates: int = 15,
    minimum_sell_candidates: int = 15,
    minimum_positive_outcomes: int = 20,
    minimum_negative_outcomes: int = 20,
    minimum_candidates_per_outer_fold: int = 20,
    minimum_trading_sessions: int = 4,
    minimum_regimes_represented: int = 2,
    outer_folds: int = 3,
    inner_folds: int = 3,
    final_test_fraction: float | None = None,
    maximum_holding_horizon_minutes: int = 30,
    embargo_minutes: int | None = None,
    minimum_calibration_rows: int = 60,
    minimum_isotonic_rows: int = 80,
    maximum_calibration_brier: float = 0.28,
    maximum_calibration_log_loss: float = 1.20,
    maximum_calibration_ece: float = 0.12,
    label_version: str = DEFAULT_META_LABEL_VERSION,
    random_seed: int = DEFAULT_RANDOM_SEED,
    minimum_net_expectancy_improvement: float = 0.0,
    maximum_promotion_drawdown_multiple: float = 1.05,
    minimum_positive_economic_outer_folds: int = 2,
    maximum_single_fold_profit_share: float = 0.75,
    maximum_single_regime_profit_share: float = 0.80,
    minimum_promotion_trade_coverage: float = 0.05,
    maximum_promotion_trade_rejection_rate: float = 0.95,
    minimum_directional_trades_per_side: int = 1,
    preloaded_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = MetaTrainingConfig(
        minimumTotalCandidates=minimum_total_candidates if minimum_total_candidates is not None else max(min_rows, 120),
        minimumBuyCandidates=minimum_buy_candidates,
        minimumSellCandidates=minimum_sell_candidates,
        minimumPositiveOutcomes=minimum_positive_outcomes,
        minimumNegativeOutcomes=minimum_negative_outcomes,
        minimumCandidatesPerOuterFold=minimum_candidates_per_outer_fold,
        minimumTradingSessions=minimum_trading_sessions,
        minimumRegimesRepresented=minimum_regimes_represented,
        minimumCalibrationRows=minimum_calibration_rows,
        minimumIsotonicRows=minimum_isotonic_rows,
        maximumCalibrationBrier=maximum_calibration_brier,
        maximumCalibrationLogLoss=maximum_calibration_log_loss,
        maximumCalibrationEce=maximum_calibration_ece,
        outerFolds=outer_folds,
        innerFolds=inner_folds,
        finalTestFraction=final_test_fraction if final_test_fraction is not None else test_fraction,
        maximumHoldingHorizonMinutes=maximum_holding_horizon_minutes,
        embargoMinutes=embargo_minutes if embargo_minutes is not None else maximum_holding_horizon_minutes,
        randomSeed=random_seed,
        minimumNetExpectancyImprovement=minimum_net_expectancy_improvement,
        maximumPromotionDrawdownMultiple=maximum_promotion_drawdown_multiple,
        minimumPositiveEconomicOuterFolds=minimum_positive_economic_outer_folds,
        maximumSingleFoldProfitShare=maximum_single_fold_profit_share,
        maximumSingleRegimeProfitShare=maximum_single_regime_profit_share,
        minimumPromotionTradeCoverage=minimum_promotion_trade_coverage,
        maximumPromotionTradeRejectionRate=maximum_promotion_trade_rejection_rate,
        minimumDirectionalTradesPerSide=minimum_directional_trades_per_side,
    ).normalized()
    rows = preloaded_rows if preloaded_rows is not None else load_labeled_rows(decision_snapshot_dir, symbol=symbol, session_date=session_date)
    examples = [training_example(row, maximum_holding_horizon_minutes=config.maximumHoldingHorizonMinutes) for row in rows]
    examples = sorted((example for example in examples if example["label"] in LABELS), key=lambda row: row["timestamp"])
    sufficiency = validate_meta_training_requirements(examples, config)
    if not sufficiency["sufficient"]:
        return save_latest_training_status(decision_snapshot_dir, {
            "status": "insufficient_data",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": len(examples),
            "minimumRequirements": config_report(config),
            "requirementDiagnostics": sufficiency,
            "message": "Insufficient candidate coverage for nested chronological purged walk-forward meta-training.",
            "trainedAt": datetime.now(UTC).isoformat(),
            "trusted": False,
        })

    plan = build_nested_walk_forward_plan(examples, config)
    if not plan["sufficient"]:
        return save_latest_training_status(decision_snapshot_dir, {
            "status": "insufficient_walk_forward_data",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": len(examples),
            "minimumRequirements": config_report(config),
            "validationPlan": plan["report"],
            "labelCounts": dict(Counter(row["label"] for row in examples)),
            "validationLabelCounts": dict(Counter(row["validationLabel"] for row in examples)),
            "message": plan["message"],
            "trainedAt": datetime.now(UTC).isoformat(),
            "trusted": False,
        })

    development_rows = plan["developmentRows"]
    final_test_rows = plan["finalTestRows"]
    outer_results = [run_outer_walk_forward_fold(fold, config) for fold in plan["outerFolds"]]
    validated_outer_results = [result for result in outer_results if result["status"] == "validated"]
    if not validated_outer_results:
        return save_latest_training_status(decision_snapshot_dir, {
            "status": "insufficient_walk_forward_data",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": len(examples),
            "minimumRequirements": config_report(config),
            "validationPlan": plan["report"],
            "message": "No outer walk-forward fold had enough purged training and validation rows.",
            "trainedAt": datetime.now(UTC).isoformat(),
            "trusted": False,
        })

    selected_hyperparameters = select_consensus_hyperparameters(validated_outer_results)
    final_feature_names = sorted({feature for row in development_rows for feature in row["features"]})
    final_scaler = feature_scaler(development_rows, final_feature_names)
    final_model = train_softmax_logistic(development_rows, final_feature_names, final_scaler, **selected_hyperparameters)
    final_inner_folds = chronological_purged_folds(
        development_rows,
        fold_count=config.innerFolds,
        minimum_validation_rows=config.minimumCandidatesPerOuterFold,
        embargo_minutes=config.embargoMinutes,
    )
    final_oof_probability_rows = inner_out_of_fold_probabilities(development_rows, final_inner_folds, selected_hyperparameters)
    final_calibration = tune_probability_calibration_from_probability_rows(
        final_oof_probability_rows,
        minimum_rows=config.minimumCalibrationRows,
        minimum_isotonic_rows=config.minimumIsotonicRows,
        maximum_brier=config.maximumCalibrationBrier,
        maximum_log_loss=config.maximumCalibrationLogLoss,
        maximum_ece=config.maximumCalibrationEce,
    )
    final_model_probabilities = [
        apply_probability_calibration_model(predict_softmax_logistic_probabilities(final_model, row["features"]), final_calibration)
        for row in final_test_rows
    ]
    final_predictions = [threshold_calibrated_probability_label(probabilities, final_calibration) for probabilities in final_model_probabilities]
    final_model_metrics = evaluate_predictions(final_predictions, [row["label"] for row in final_test_rows])
    feature_schema_hash = meta_strategy_feature_schema_hash(final_feature_names)
    training_window = row_window(development_rows)
    final_holdout_window = row_window(final_test_rows)
    metrics_by_fold = collect_model_metrics_by_fold(validated_outer_results)
    random_forest_hyperparameters = select_random_forest_hyperparameters_from_inner_folds(final_inner_folds) if final_inner_folds else dict(RANDOM_FOREST_HYPERPARAMETER_GRID[0])
    random_forest_oof_probability_rows = inner_out_of_fold_probabilities_for_random_forest(
        final_inner_folds,
        random_forest_hyperparameters,
        random_seed=config.randomSeed,
    )
    random_forest_calibration = tune_probability_calibration_from_probability_rows(
        random_forest_oof_probability_rows,
        minimum_rows=config.minimumCalibrationRows,
        minimum_isotonic_rows=config.minimumIsotonicRows,
        maximum_brier=config.maximumCalibrationBrier,
        maximum_log_loss=config.maximumCalibrationLogLoss,
        maximum_ece=config.maximumCalibrationEce,
    )
    random_forest_model = train_random_forest(
        development_rows,
        final_feature_names,
        tree_count=int(random_forest_hyperparameters["treeCount"]),
        max_depth=int(random_forest_hyperparameters["maxDepth"]),
        random_seed=config.randomSeed,
    )
    random_forest_final_probabilities = [
        apply_probability_calibration_model(predict_random_forest_probabilities(random_forest_model, row["features"]), random_forest_calibration)
        for row in final_test_rows
    ]
    random_forest_final_predictions = [
        threshold_calibrated_probability_label(probabilities, random_forest_calibration)
        for probabilities in random_forest_final_probabilities
    ]
    random_forest_metrics = evaluate_predictions(random_forest_final_predictions, [row["label"] for row in final_test_rows])
    final_deterministic_baseline_predictions = [deterministic_baseline_prediction(row) for row in final_test_rows]
    final_deterministic_economic_metrics = economic_performance(final_deterministic_baseline_predictions, final_test_rows)
    final_model_economic_metrics = economic_performance(
        final_predictions,
        final_test_rows,
        probability_distributions=final_model_probabilities,
        random_seed=config.randomSeed,
    )
    random_forest_economic_metrics = economic_performance(
        random_forest_final_predictions,
        final_test_rows,
        probability_distributions=random_forest_final_probabilities,
        random_seed=config.randomSeed,
    )
    optional_challenger_models, unavailable_challengers, optional_challenger_metrics = train_optional_challengers(
        development_rows=development_rows,
        final_test_rows=final_test_rows,
        feature_names=final_feature_names,
        feature_schema_hash=feature_schema_hash,
        training_window=training_window,
        final_holdout_window=final_holdout_window,
        label_version=label_version,
        metrics_by_fold=metrics_by_fold,
    )
    champion_model_artifact = model_artifact_with_hash(
        {
            "role": "champion",
            "available": True,
            "kind": "softmax_regularized_logistic_regression",
            "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "featureSchemaHash": feature_schema_hash,
            "labelVersion": label_version,
            "trainingWindow": training_window,
            "featureNames": final_feature_names,
            "featureScaling": "standard_z_score",
            "scaler": final_scaler,
            "weights": final_model["weights"],
            "intercepts": final_model["intercepts"],
            "classes": LABELS,
            "hyperparameters": selected_hyperparameters,
            "calibrationMethod": final_calibration.get("method"),
            "thresholds": calibration_thresholds(final_calibration),
            "calibration": final_calibration,
            "metricsByFold": metrics_by_fold.get("logistic_regression_champion", []),
            "finalHoldoutMetrics": final_model_metrics,
            "economicMetrics": final_model_economic_metrics,
            "randomSeed": config.randomSeed,
        }
    )
    random_forest_model_artifact = model_artifact_with_hash(
        {
            "role": "challenger",
            "available": True,
            "kind": "random_forest",
            "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "featureSchemaHash": feature_schema_hash,
            "labelVersion": label_version,
            "trainingWindow": training_window,
            "featureNames": final_feature_names,
            "featureScaling": "not_required_for_tree_model",
            "trees": random_forest_model["trees"],
            "classes": LABELS,
            "hyperparameters": random_forest_hyperparameters,
            "calibrationMethod": random_forest_calibration.get("method"),
            "thresholds": calibration_thresholds(random_forest_calibration),
            "calibration": random_forest_calibration,
            "metricsByFold": metrics_by_fold.get("random_forest_challenger", []),
            "finalHoldoutMetrics": random_forest_metrics,
            "economicMetrics": random_forest_economic_metrics,
            "randomSeed": config.randomSeed,
        }
    )
    model_artifacts = {
        "logistic_regression_champion": champion_model_artifact,
        "logistic_regression_nested_calibrated": champion_model_artifact,
        "random_forest_challenger": random_forest_model_artifact,
        **optional_challenger_models,
    }
    final_model_metrics_by_name = {
        "logistic_regression_champion": final_model_metrics,
        "logistic_regression_nested_calibrated": final_model_metrics,
        "random_forest_challenger": random_forest_metrics,
        **optional_challenger_metrics,
    }
    final_baseline_metrics = {
        name: evaluate_predictions([baseline_prediction(row, name) for row in final_test_rows], [row["label"] for row in final_test_rows])
        for name in BASELINE_NAMES
    }
    strict_validation_metrics = {
        "baselines": {
            name: evaluate_predictions([baseline_prediction(row, name) for row in final_test_rows], [row["validationLabel"] for row in final_test_rows])
            for name in BASELINE_NAMES
        },
        "models": {
            name: evaluate_predictions(predictions, [row["validationLabel"] for row in final_test_rows])
            for name, predictions in {
                "logistic_regression_champion": final_predictions,
                "random_forest_challenger": random_forest_final_predictions,
            }.items()
        },
        "labelCounts": dict(Counter(row["validationLabel"] for row in examples)),
        "finalTestLabelCounts": dict(Counter(row["validationLabel"] for row in final_test_rows)),
        "policy": "Final holdout is untouched by feature selection, calibration, and threshold tuning.",
    }
    best_baseline_score = max(metric["trustScore"] for metric in final_baseline_metrics.values())
    best_baseline_directional_f1 = max(metric["directionalMacroF1"] for metric in final_baseline_metrics.values())
    outer_summary = summarize_outer_results(validated_outer_results)
    outer_economic_summary = summarize_outer_economic_results(validated_outer_results, model_name="logistic_regression_champion")
    economic_promotion = evaluate_economic_promotion(
        model_metrics=final_model_economic_metrics,
        baseline_metrics=final_deterministic_economic_metrics,
        outer_summary=outer_economic_summary,
        calibration=final_calibration,
        config=config,
    )
    non_hold_labels_in_final_test = [
        label for label in ["BUY", "SELL"]
        if int(final_model_metrics.get("actualCounts", {}).get(label) or 0) > 0
    ]
    non_hold_ready = bool(non_hold_labels_in_final_test) and all(
        float((final_model_metrics.get("perClass", {}).get(label) or {}).get("recall") or 0.0) > 0.0
        for label in non_hold_labels_in_final_test
    )
    trusted = (
        len(validated_outer_results) >= 2
        and non_hold_ready
        and bool(economic_promotion.get("promoted"))
    )
    trust_message = None
    if not trusted and len(validated_outer_results) < 2:
        trust_message = "Need at least two validated outer walk-forward folds before trusting the meta-model."
    elif not trusted and not non_hold_ready:
        trust_message = "Nested meta-model did not detect every non-HOLD label class present in the untouched final test."
    elif not trusted and economic_promotion.get("rejectedReasonCodes"):
        trust_message = "Economic promotion rejected the meta-model: " + ", ".join(economic_promotion["rejectedReasonCodes"])
    elif not trusted:
        trust_message = "Nested meta-model has not satisfied economic promotion criteria across folds and final holdout."

    artifact = {
        "status": "ready",
        "version": 2,
        "trainedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbol": symbol.upper(),
        "sessionDate": session_date,
        "rows": len(examples),
        "developmentRows": len(development_rows),
        "finalTestRows": len(final_test_rows),
        "featureCount": len(final_feature_names),
        "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
        "featureSchemaHash": feature_schema_hash,
        "labelVersion": label_version,
        "trainingWindow": training_window,
        "finalHoldoutWindow": final_holdout_window,
        "randomSeed": config.randomSeed,
        "minimumRequirements": config_report(config),
        "labelCounts": dict(Counter(row["label"] for row in examples)),
        "trainingLabelCounts": dict(Counter(row["label"] for row in examples)),
        "validationLabelCounts": dict(Counter(row["validationLabel"] for row in examples)),
        "labelPolicy": "Models train on candidate-only training labels. Strict validation labels remain available for final untouched reporting.",
        "trusted": trusted,
        "championModel": "logistic_regression_champion",
        "challengerModels": [
            "random_forest_challenger",
            "xgboost_challenger",
            "lightgbm_challenger",
        ],
        "unavailableChallengers": unavailable_challengers,
        "modelHashes": {name: model.get("modelHash") for name, model in model_artifacts.items()},
        "validationPolicy": {
            "method": "nested_chronological_purged_walk_forward",
            "outerFolds": config.outerFolds,
            "innerFolds": config.innerFolds,
            "maximumHoldingHorizonMinutes": config.maximumHoldingHorizonMinutes,
            "embargoMinutes": config.embargoMinutes,
            "embargoPolicy": "training rows are allowed only when labelEnd < validationStart - embargo",
            "finalHoldoutPolicy": "most recent sufficiently large period is untouched by feature selection, calibration, and threshold tuning",
            "plan": plan["report"],
            "outerFoldReports": [result["report"] for result in outer_results],
            "metricsByFold": metrics_by_fold,
        },
        "models": model_artifacts,
        "finalHoldoutMetrics": {
            "models": final_model_metrics_by_name,
            "baselines": final_baseline_metrics,
            "economic": {
                "models": {
                    "logistic_regression_champion": final_model_economic_metrics,
                    "random_forest_challenger": random_forest_economic_metrics,
                },
                "deterministicBaseline": final_deterministic_economic_metrics,
            },
            "window": final_holdout_window,
        },
        "metrics": {
            "baselines": final_baseline_metrics,
            "baselinePolicy": "Baselines are reconstructed from raw strategyOutputs when present, otherwise from saved family scores. They do not trust finalDecision.*.signal.",
            "models": final_model_metrics_by_name,
            "strictValidation": strict_validation_metrics,
            "outerWalkForward": outer_summary,
            "outerEconomic": outer_economic_summary,
            "economicPromotion": economic_promotion,
            "bestBaselineMacroF1": max(metric["macroF1"] for metric in final_baseline_metrics.values()),
            "bestBaselineTrustScore": best_baseline_score,
            "bestBaselineDirectionalMacroF1": best_baseline_directional_f1,
            "bestBaselineAccuracy": max(metric["accuracy"] for metric in final_baseline_metrics.values()),
            "bestModel": "logistic_regression_champion",
            "trusted": trusted,
            "probabilitySizingApproved": bool(final_calibration.get("probabilitySizingApproved")),
            "probabilitySizingApprovalReasonCodes": final_calibration.get("approvalReasonCodes") or [],
            "trustRule": "Trust only after sufficient data, at least two validated outer purged walk-forward folds, out-of-fold calibration, and economic promotion versus the corrected family-aware deterministic baseline.",
            "trustDiagnostics": {
                "nonHoldLabelsInFinalTest": non_hold_labels_in_final_test,
                "nonHoldReady": non_hold_ready,
                "bestModelTrustScore": final_model_metrics["trustScore"],
                "bestBaselineTrustScore": best_baseline_score,
                "bestModelDirectionalMacroF1": final_model_metrics["directionalMacroF1"],
                "bestBaselineDirectionalMacroF1": best_baseline_directional_f1,
                "economicPromotionPromoted": bool(economic_promotion.get("promoted")),
                "economicPromotionReasons": economic_promotion.get("rejectedReasonCodes") or [],
                "economicPromotionWarnings": economic_promotion.get("warningFlags") or [],
            },
        },
        "message": trust_message,
    }
    artifact_path = meta_strategy_artifact_path(decision_snapshot_dir, symbol=symbol, session_date=session_date)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = save_latest_training_status(decision_snapshot_dir, artifact)
    return {**latest, "artifactPath": str(artifact_path)}


def train_and_validate_meta_model_v2(
    *,
    decision_snapshot_dir: Path,
    symbol: str,
    session_date: str | None = None,
    label_version: str = DEFAULT_META_LABEL_VERSION,
    **training_kwargs: Any,
) -> dict[str, Any]:
    raw_rows = load_labeled_rows(decision_snapshot_dir, symbol=symbol, session_date=session_date)
    compatibility = v2_training_compatibility_report(raw_rows, label_version=label_version)
    compatible_rows = [item["row"] for item in compatibility["compatibleRows"]]
    if not compatible_rows:
        result = {
            "status": "insufficient_compatible_v2_data",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": 0,
            "rawRows": len(raw_rows),
            "trusted": False,
            "v2TrainingValidation": build_meta_model_v2_validation_package(
                training_result={
                    "status": "insufficient_compatible_v2_data",
                    "trusted": False,
                    "message": "No compatible V2 decision snapshots were available for Meta-Model V2 training.",
                },
                compatibility=compatibility,
                label_version=label_version,
            ),
            "message": "Meta-Model V2 training requires compatible V2 snapshots only; no compatible rows were found.",
            "trainedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        return save_latest_training_status(decision_snapshot_dir, result)

    result = train_meta_strategy_baselines(
        decision_snapshot_dir=decision_snapshot_dir,
        symbol=symbol,
        session_date=session_date,
        label_version=label_version,
        preloaded_rows=compatible_rows,
        **training_kwargs,
    )
    validation = build_meta_model_v2_validation_package(
        training_result=result,
        compatibility=compatibility,
        label_version=label_version,
    )
    trusted = bool(result.get("trusted")) and bool(validation["promotionDecision"]["trusted"])
    validated = {
        **result,
        "trusted": trusted,
        "v2TrainingValidation": validation,
        "message": validation["promotionDecision"]["message"],
    }
    _rewrite_artifact_if_available(validated)
    return save_latest_training_status(decision_snapshot_dir, validated)


def v2_training_compatibility_report(rows: list[dict[str, Any]], *, label_version: str) -> dict[str, Any]:
    compatible_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for index, row in enumerate(rows):
        reasons = v2_training_incompatibility_reasons(row, label_version=label_version)
        row_id = str(row.get("snapshotId") or row.get("id") or row.get("capturedAt") or index)
        if reasons:
            excluded_rows.append({"rowId": row_id, "reasonCodes": reasons})
            reason_counts.update(reasons)
        else:
            compatible_rows.append({"rowId": row_id, "row": row})
    return {
        "version": "v2_training_compatibility_report_v1",
        "rawRows": len(rows),
        "compatibleRows": compatible_rows,
        "compatibleRowCount": len(compatible_rows),
        "excludedRows": excluded_rows,
        "excludedRowCount": len(excluded_rows),
        "excludedReasonCounts": dict(sorted(reason_counts.items())),
        "v1RowsExcluded": sum(1 for row in excluded_rows if any("v1" in code for code in row["reasonCodes"])),
        "trainingDataPolicy": "Meta-Model V2 trains only on compatible DecisionSnapshotV2-derived rows. V1, demo/fallback, and leaky upstream-feature rows are excluded before training.",
    }


def v2_training_incompatibility_reasons(row: dict[str, Any], *, label_version: str) -> list[str]:
    reasons: list[str] = []
    snapshot_schema = str(row.get("snapshotSchemaVersion") or row.get("schemaVersion") or "")
    source_schema = str(row.get("sourceSchemaVersion") or row.get("sourceSnapshotSchemaVersion") or "")
    if snapshot_schema != "decision_snapshot_v2":
        reasons.append("v2_training.non_v2_snapshot")
    if source_schema.startswith("voting_ensemble_v1") or snapshot_schema.startswith("voting_ensemble_v1"):
        reasons.append("v2_training.v1_snapshot_excluded")
    if row.get("trainingCompatibleWithV2") is False:
        reasons.append("v2_training.explicitly_incompatible_with_v2")
    if row.get("eligibleForTraining") is not True:
        reasons.append("v2_training.not_marked_training_eligible")
    if str(row.get("labelVersion") or "") != label_version:
        reasons.append("v2_training.label_version_mismatch")
    if _uses_demo_or_fallback_market_data(row):
        reasons.append("v2_training.demo_or_fallback_market_data")
    if _has_duplicated_v1_signal_payload(row):
        reasons.append("v2_training.contains_old_v1_or_duplicated_signals")
    if _upstream_forecast_not_out_of_sample(row):
        reasons.append("v2_training.upstream_forecast_not_out_of_sample")
    return sorted(set(reasons))


def build_meta_model_v2_validation_package(
    *,
    training_result: dict[str, Any],
    compatibility: dict[str, Any],
    label_version: str,
) -> dict[str, Any]:
    champion = ((training_result.get("models") or {}).get("logistic_regression_champion") or {})
    calibration = champion.get("calibration") or {}
    final_holdout = training_result.get("finalHoldoutMetrics") or {}
    metrics = training_result.get("metrics") or {}
    economic_promotion = metrics.get("economicPromotion") or {}
    outer_reports = ((training_result.get("validationPolicy") or {}).get("outerFoldReports") or [])
    final_holdout_policy = str((training_result.get("validationPolicy") or {}).get("finalHoldoutPolicy") or "")
    final_holdout_untouched = "untouched" in final_holdout_policy.lower()
    calibration_approved = bool(calibration.get("probabilitySizingApproved"))
    economic_promoted = bool(economic_promotion.get("promoted"))
    minimum_requirements = training_result.get("minimumRequirements") or {}
    compatibility_ok = compatibility.get("compatibleRowCount", 0) > 0 and compatibility.get("v1RowsExcluded", 0) >= 0
    trusted = bool(training_result.get("trusted")) and compatibility_ok and final_holdout_untouched and calibration_approved and economic_promoted
    rejected_reasons = []
    if compatibility.get("compatibleRowCount", 0) <= 0:
        rejected_reasons.append("v2_training.no_compatible_v2_rows")
    if compatibility.get("v1RowsExcluded", 0) > 0:
        rejected_reasons.append("v2_training.v1_rows_excluded")
    if not final_holdout_untouched:
        rejected_reasons.append("validation.final_holdout_not_confirmed_untouched")
    if not calibration_approved:
        rejected_reasons.append("calibration.not_approved")
    if not economic_promoted:
        rejected_reasons.extend(economic_promotion.get("rejectedReasonCodes") or ["economic.not_promoted"])
    if not training_result.get("trusted"):
        rejected_reasons.append("training_result.not_trusted")
    side_regime = side_and_regime_breakdown(final_holdout)
    return {
        "version": META_MODEL_V2_TRAINING_REPORT_VERSION,
        "trainingRows": compatibility.get("compatibleRowCount", 0),
        "excludedRows": compatibility.get("excludedRowCount", 0),
        "compatibility": {
            "rawRows": compatibility.get("rawRows", 0),
            "compatibleRowCount": compatibility.get("compatibleRowCount", 0),
            "excludedRowCount": compatibility.get("excludedRowCount", 0),
            "excludedRows": compatibility.get("excludedRows", []),
            "excludedReasonCounts": compatibility.get("excludedReasonCounts", {}),
            "trainingDataPolicy": compatibility.get("trainingDataPolicy"),
        },
        "outerFoldReports": outer_reports,
        "finalHoldoutReport": {
            "window": training_result.get("finalHoldoutWindow"),
            "rows": training_result.get("finalTestRows"),
            "untouched": final_holdout_untouched,
            "policy": final_holdout_policy,
            "metrics": final_holdout,
        },
        "calibrationReport": {
            "method": calibration.get("method"),
            "source": calibration.get("source"),
            "trainingRows": calibration.get("trainingRows"),
            "probabilitySizingApproved": calibration_approved,
            "approvalReasonCodes": calibration.get("approvalReasonCodes") or [],
            "metrics": calibration.get("metrics") or {},
        },
        "economicComparison": {
            "baseline": "deterministic_v2_static_baseline",
            "deterministicBaseline": ((final_holdout.get("economic") or {}).get("deterministicBaseline") or {}),
            "model": (((final_holdout.get("economic") or {}).get("models") or {}).get("logistic_regression_champion") or {}),
            "promotion": economic_promotion,
        },
        "sideAndRegimeBreakdown": side_regime,
        "modelCard": {
            "modelName": "Meta-Model V2 candidate-success filter",
            "objective": "candidate_success_probability",
            "candidatePolicy": "Deterministic V2 proposes side; ML may later accept/reject or cap risk but cannot flip side or create trades from Hold.",
            "trainingData": "Compatible DecisionSnapshotV2 rows only; V1 rows and demo/fallback data are excluded.",
            "featureSchemaVersion": training_result.get("featureSchemaVersion") or META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "featureSchemaHash": training_result.get("featureSchemaHash"),
            "labelVersion": label_version,
            "championModel": training_result.get("championModel") or "logistic_regression_champion",
            "challengerModels": training_result.get("challengerModels") or [],
            "minimumRequirements": minimum_requirements,
            "limitations": [
                "Trusted status requires compatible V2 samples, validated outer folds, approved OOF calibration, untouched final holdout, and economic promotion.",
                "Upstream forecast features must be out of sample for historical rows.",
            ],
        },
        "promotionDecision": {
            "trusted": trusted,
            "sourceTrusted": bool(training_result.get("trusted")),
            "criteria": {
                "compatibleV2RowsOnly": compatibility_ok,
                "minimumRequirements": minimum_requirements,
                "validatedOuterFolds": (metrics.get("outerWalkForward") or {}).get("validatedFolds"),
                "finalHoldoutUntouched": final_holdout_untouched,
                "calibrationApproved": calibration_approved,
                "economicPromotion": economic_promoted,
                "documentedPromotionCriteria": promotion_criteria_report_from_result(training_result),
            },
            "rejectedReasonCodes": sorted(set(rejected_reasons)),
            "message": (
                "Meta-Model V2 is trusted under documented promotion criteria."
                if trusted
                else "Meta-Model V2 remains untrusted until V2 compatibility, fold, calibration, final-holdout, and economic promotion criteria pass."
            ),
        },
    }


def side_and_regime_breakdown(final_holdout_metrics: dict[str, Any]) -> dict[str, Any]:
    economic = final_holdout_metrics.get("economic") or {}
    model = ((economic.get("models") or {}).get("logistic_regression_champion") or {})
    return {
        "buyPerformance": model.get("buyPerformance") or {},
        "sellPerformance": model.get("sellPerformance") or {},
        "performanceByRegime": model.get("performanceByRegime") or {},
        "performanceByTimeOfDay": model.get("performanceByTimeOfDay") or {},
    }


def promotion_criteria_report_from_result(training_result: dict[str, Any]) -> dict[str, Any]:
    promotion = (((training_result.get("metrics") or {}).get("economicPromotion") or {}).get("criteria") or {})
    if promotion:
        return promotion
    return {
        "minimumSamplesAndFolds": training_result.get("minimumRequirements") or {},
        "calibration": "probability sizing must be approved from out-of-fold calibration rows",
        "economic": "model must improve deterministic V2 net expectancy without unacceptable drawdown or concentrated fold/regime profit",
    }


def _uses_demo_or_fallback_market_data(row: dict[str, Any]) -> bool:
    feed = str(row.get("marketDataFeed") or "").lower()
    if feed in {"demo", "fallback", "demo/fallback"}:
        return True
    payload = json.dumps(row.get("rawMarketReferences") or row.get("rawMarketData") or {}, sort_keys=True).lower()
    return '"provider": "demo"' in payload or '"provider": "fallback"' in payload


def _has_duplicated_v1_signal_payload(row: dict[str, Any]) -> bool:
    if row.get("containsDuplicatedV1Signals") or row.get("containsOldDuplicatedSignals"):
        return True
    strategy_ids = [str(item.get("strategyId") or item.get("strategyName") or "") for item in row.get("strategySignals", []) if isinstance(item, dict)]
    if "ensemble_strategy_voting" in strategy_ids or "Ensemble Strategy Voting" in strategy_ids:
        return True
    return any(name in strategy_ids for name in ("Failed Breakout Strategy", "Bollinger Band Reversion", "ATR Overextension Reversion"))


def _upstream_forecast_not_out_of_sample(row: dict[str, Any]) -> bool:
    forecast = row.get("forecastFeature") or ((row.get("metaModelFeatures") or {}).get("forecastFeature") if isinstance(row.get("metaModelFeatures"), dict) else None)
    if not isinstance(forecast, dict):
        return False
    status = str(forecast.get("status") or "")
    if status not in {"out_of_sample", "live_approved_artifact", "missing_approved_forecast_model"}:
        return True
    if status == "missing_approved_forecast_model":
        return False
    decision_at = example_timestamp(row)
    training_end_value = forecast.get("trainingWindowEndUtc") or forecast.get("trainingWindowEnd") or forecast.get("trainedThroughUtc")
    if not training_end_value:
        return True
    return parse_datetime_utc(str(training_end_value)) >= decision_at


def _rewrite_artifact_if_available(result: dict[str, Any]) -> None:
    artifact_path = result.get("artifactPath")
    if not artifact_path:
        return
    path = Path(str(artifact_path))
    if not path.exists():
        return
    try:
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def meta_strategy_feature_schema_hash(feature_names: list[str]) -> str:
    return stable_json_hash(
        {
            "schemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "featureNames": list(feature_names),
            "labelSpace": LABELS,
        }
    )


def row_window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_rows = sorted(rows, key=lambda row: row["timestamp"])
    if not sorted_rows:
        return {"start": None, "end": None, "rows": 0}
    return {
        "start": iso_or_none(sorted_rows[0]["timestamp"]),
        "end": iso_or_none(sorted_rows[-1]["timestamp"]),
        "rows": len(sorted_rows),
    }


def collect_model_metrics_by_fold(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        fold_number = result.get("fold")
        for model_name, metric in (result.get("modelMetrics") or {}).items():
            metrics[model_name].append({"fold": fold_number, **metric})
    return dict(metrics)


def calibration_thresholds(calibration: dict[str, Any]) -> dict[str, float]:
    return {
        "buyThreshold": float(calibration.get("buyThreshold") or 0.0),
        "sellThreshold": float(calibration.get("sellThreshold") or 0.0),
        "minDirectionalEdge": float(calibration.get("minDirectionalEdge") or 0.0),
    }


def model_artifact_with_hash(model: dict[str, Any]) -> dict[str, Any]:
    return {**model, "modelHash": stable_json_hash(model)}


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def train_optional_challengers(
    *,
    development_rows: list[dict[str, Any]],
    final_test_rows: list[dict[str, Any]],
    feature_names: list[str],
    feature_schema_hash: str,
    training_window: dict[str, Any],
    final_holdout_window: dict[str, Any],
    label_version: str,
    metrics_by_fold: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    optional_specs = [
        ("xgboost_challenger", "xgboost", train_xgboost_booster, predict_xgboost_probabilities),
        ("lightgbm_challenger", "lightgbm", train_lightgbm_booster, predict_lightgbm_probabilities),
    ]
    models: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, str] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for model_name, kind, trainer, predictor in optional_specs:
        trained = trainer(development_rows, feature_names)
        if not trained.get("available"):
            reason = str(trained.get("reason") or f"{kind} unavailable")
            unavailable[model_name] = reason
            models[model_name] = model_artifact_with_hash(
                {
                    "role": "challenger",
                    "available": False,
                    "kind": kind,
                    "reason": reason,
                    "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
                    "featureSchemaHash": feature_schema_hash,
                    "labelVersion": label_version,
                    "trainingWindow": training_window,
                    "finalHoldoutMetrics": None,
                    "metricsByFold": metrics_by_fold.get(model_name, []),
                    "calibrationMethod": "not_trained",
                    "thresholds": {},
                }
            )
            continue
        probabilities = [predictor(trained, row["features"]) for row in final_test_rows]
        predictions = [probability_label(row) for row in probabilities]
        final_metrics = evaluate_predictions(predictions, [row["label"] for row in final_test_rows])
        metrics[model_name] = final_metrics
        models[model_name] = model_artifact_with_hash(
            {
                "role": "challenger",
                "available": True,
                "kind": kind,
                "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
                "featureSchemaHash": feature_schema_hash,
                "labelVersion": label_version,
                "trainingWindow": training_window,
                "finalHoldoutWindow": final_holdout_window,
                "featureNames": feature_names,
                "featureScaling": "not_required_for_booster_tree_model",
                "classes": LABELS,
                "hyperparameters": trained.get("params") or {},
                "calibrationMethod": "uncalibrated_challenger",
                "thresholds": {},
                "calibration": {
                    "method": "uncalibrated_challenger",
                    "probabilitySizingApproved": False,
                    "approvalReasonCodes": ["challenger.not_probability_calibrated"],
                },
                "metricsByFold": metrics_by_fold.get(model_name, []),
                "finalHoldoutMetrics": final_metrics,
                "modelPayload": serializable_booster_model(trained),
                "randomSeed": DEFAULT_RANDOM_SEED,
            }
        )
    return models, unavailable, metrics


def load_meta_strategy_model_artifact(path: Path, *, expected_feature_schema_hash: str) -> dict[str, Any]:
    return load_meta_strategy_model_artifact_data(
        json.loads(path.read_text(encoding="utf-8")),
        expected_feature_schema_hash=expected_feature_schema_hash,
    )


def load_meta_strategy_model_artifact_data(artifact: dict[str, Any], *, expected_feature_schema_hash: str) -> dict[str, Any]:
    actual = str(artifact.get("featureSchemaHash") or "")
    if actual != expected_feature_schema_hash:
        raise ValueError(
            f"Meta-strategy artifact feature schema mismatch: expected {expected_feature_schema_hash}, got {actual or 'missing'}"
        )
    for model_name, model in (artifact.get("models") or {}).items():
        model_hash = model.get("modelHash")
        if model_hash and model_hash != stable_json_hash({key: value for key, value in model.items() if key != "modelHash"}):
            raise ValueError(f"Meta-strategy artifact model hash mismatch for {model_name}")
        model_schema_hash = str(model.get("featureSchemaHash") or "")
        if model_schema_hash and model_schema_hash != expected_feature_schema_hash:
            raise ValueError(f"Meta-strategy artifact model {model_name} uses a different feature schema")
    return artifact


def deterministic_baseline_prediction(row: dict[str, Any]) -> str:
    baselines = row.get("baselines") or {}
    return normalize_label(
        str(
            baselines.get(DETERMINISTIC_BASELINE_NAME)
            or baselines.get("confidence_score_aggregation")
            or baselines.get("market_regime_strategy_selection")
            or "HOLD"
        )
    )


def economic_performance(
    predictions: list[str],
    rows: list[dict[str, Any]],
    *,
    probability_distributions: list[dict[str, float]] | None = None,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    trades = [trade_economic_result(prediction, row) for prediction, row in zip(predictions, rows)]
    retained = [trade for trade in trades if trade["retained"]]
    pnl_values = [float(trade["pnl"]) for trade in retained]
    risk_values = [float(trade["risk"]) for trade in retained]
    gains = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    labels = [row["label"] for row in rows]
    brier_score = multiclass_brier_score(probability_distributions, labels) if probability_distributions else None
    calibration_error = expected_calibration_error(probability_distributions, labels) if probability_distributions else None
    return {
        "rows": len(rows),
        "retainedTrades": len(retained),
        "rejectedTrades": sum(1 for prediction in predictions if normalize_label(str(prediction)) == "HOLD"),
        "tradeCoverage": round(len(retained) / max(1, len(rows)), 6),
        "tradeRejectionRate": round(sum(1 for prediction in predictions if normalize_label(str(prediction)) == "HOLD") / max(1, len(rows)), 6),
        "netPnl": round(sum(pnl_values), 6),
        "netExpectancyAfterCosts": round(sum(pnl_values) / max(1, len(retained)), 6),
        "profitFactor": round((sum(gains) / abs(sum(losses))) if losses else (999.0 if gains else 0.0), 6),
        "maximumDrawdown": round(maximum_drawdown(pnl_values), 6),
        "worstDay": round(worst_group_pnl(retained, "sessionDate"), 6),
        "returnPerUnitRisk": round(sum(pnl_values) / max(1e-9, sum(risk_values)), 6),
        "buyPerformance": side_performance(retained, "BUY"),
        "sellPerformance": side_performance(retained, "SELL"),
        "performanceByRegime": grouped_trade_performance(retained, "regime"),
        "performanceByTimeOfDay": grouped_trade_performance(retained, "timeOfDay"),
        "brierScore": round(brier_score, 6) if brier_score is not None else None,
        "calibrationError": round(calibration_error, 6) if calibration_error is not None else None,
        "bootstrap": bootstrap_expectancy_interval(pnl_values, random_seed=random_seed),
    }


def trade_economic_result(prediction: str, row: dict[str, Any]) -> dict[str, Any]:
    label = normalize_label(str(prediction))
    retained = label in {"BUY", "SELL"}
    target = max(0.01, first_number(
        row["features"].get("barriers.targetDistance"),
        row["features"].get("barriers.target"),
        row["features"].get("barriers.profitTarget"),
        default=1.0,
    ))
    stop = max(0.01, first_number(
        row["features"].get("barriers.stopDistance"),
        row["features"].get("barriers.stop"),
        row["features"].get("barriers.protectiveStop"),
        default=1.0,
    ))
    costs = max(0.0, first_number(row["features"].get("entry.spread"), default=0.0))
    costs += max(0.0, first_number(row["features"].get("entry.slippage"), default=0.0))
    costs += max(0.0, first_number(row["features"].get("entry.fees"), row["features"].get("entry.commission"), default=0.0))
    successful = retained and label == row["label"] and int(row.get("binaryOutcome") or 0) == 1
    pnl = (target - costs) if successful else (-stop - costs if retained else 0.0)
    return {
        "retained": retained,
        "prediction": label,
        "actual": row["label"],
        "pnl": pnl,
        "risk": stop + costs,
        "sessionDate": row.get("sessionDate") or "unknown",
        "regime": row.get("regime") or "unknown",
        "timeOfDay": time_of_day_bucket(row["timestamp"]),
    }


def maximum_drawdown(pnl_values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def worst_group_pnl(trades: list[dict[str, Any]], group_key: str) -> float:
    if not trades:
        return 0.0
    totals: dict[str, float] = defaultdict(float)
    for trade in trades:
        totals[str(trade.get(group_key) or "unknown")] += float(trade["pnl"])
    return min(totals.values()) if totals else 0.0


def side_performance(trades: list[dict[str, Any]], side: str) -> dict[str, Any]:
    side_trades = [trade for trade in trades if trade["prediction"] == side]
    pnl_values = [float(trade["pnl"]) for trade in side_trades]
    wins = sum(1 for value in pnl_values if value > 0)
    return {
        "trades": len(side_trades),
        "netPnl": round(sum(pnl_values), 6),
        "expectancy": round(sum(pnl_values) / max(1, len(side_trades)), 6),
        "winRate": round(wins / max(1, len(side_trades)), 6),
    }


def grouped_trade_performance(trades: list[dict[str, Any]], group_key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(group_key) or "unknown")].append(trade)
    return {
        group: {
            "trades": len(items),
            "netPnl": round(sum(float(item["pnl"]) for item in items), 6),
            "expectancy": round(sum(float(item["pnl"]) for item in items) / max(1, len(items)), 6),
        }
        for group, items in sorted(groups.items())
    }


def bootstrap_expectancy_interval(pnl_values: list[float], *, random_seed: int) -> dict[str, Any]:
    if not pnl_values:
        return {"method": "bootstrap", "samples": 0, "expectancyMean": 0.0, "lower95": 0.0, "upper95": 0.0}
    rng = random.Random(random_seed)
    estimates = []
    for _ in range(200):
        sample = [pnl_values[rng.randrange(len(pnl_values))] for _ in range(len(pnl_values))]
        estimates.append(sum(sample) / len(sample))
    estimates.sort()
    return {
        "method": "bootstrap",
        "samples": 200,
        "expectancyMean": round(sum(estimates) / len(estimates), 6),
        "lower95": round(estimates[int(0.025 * (len(estimates) - 1))], 6),
        "upper95": round(estimates[int(0.975 * (len(estimates) - 1))], 6),
    }


def time_of_day_bucket(timestamp: datetime) -> str:
    hour = timestamp.hour + (timestamp.minute / 60.0)
    if hour < 15.5:
        return "open"
    if hour < 18.0:
        return "midday"
    return "late"


def summarize_outer_economic_results(results: list[dict[str, Any]], *, model_name: str) -> dict[str, Any]:
    fold_rows = []
    for result in results:
        economics = result.get("economicMetrics") or {}
        model = economics.get(model_name) or {}
        baseline = economics.get("deterministicBaseline") or {}
        model_pnl = float(model.get("netPnl") or 0.0)
        baseline_pnl = float(baseline.get("netPnl") or 0.0)
        fold_rows.append(
            {
                "fold": result.get("fold"),
                "modelNetPnl": round(model_pnl, 6),
                "baselineNetPnl": round(baseline_pnl, 6),
                "modelNetExpectancyAfterCosts": model.get("netExpectancyAfterCosts", 0.0),
                "baselineNetExpectancyAfterCosts": baseline.get("netExpectancyAfterCosts", 0.0),
                "improvedNetPnl": model_pnl > baseline_pnl,
            }
        )
    positive_pnls = [max(0.0, float(row["modelNetPnl"])) for row in fold_rows]
    total_positive = sum(positive_pnls)
    return {
        "model": model_name,
        "folds": fold_rows,
        "positiveEconomicFolds": sum(1 for row in fold_rows if row["improvedNetPnl"]),
        "singleFoldProfitShare": round((max(positive_pnls) / total_positive) if total_positive > 0 else 0.0, 6),
    }


def evaluate_economic_promotion(
    *,
    model_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    outer_summary: dict[str, Any],
    calibration: dict[str, Any],
    config: MetaTrainingConfig,
) -> dict[str, Any]:
    rejected: list[str] = []
    warnings: list[str] = []
    expectancy_delta = float(model_metrics.get("netExpectancyAfterCosts") or 0.0) - float(baseline_metrics.get("netExpectancyAfterCosts") or 0.0)
    if expectancy_delta <= config.minimumNetExpectancyImprovement:
        rejected.append("economic.expectancy_not_improved")
    baseline_drawdown = float(baseline_metrics.get("maximumDrawdown") or 0.0)
    model_drawdown = float(model_metrics.get("maximumDrawdown") or 0.0)
    drawdown_limit = max(0.01, baseline_drawdown * config.maximumPromotionDrawdownMultiple)
    if model_drawdown > drawdown_limit:
        rejected.append("economic.drawdown_unacceptable")
    if int(outer_summary.get("positiveEconomicFolds") or 0) < config.minimumPositiveEconomicOuterFolds:
        rejected.append("economic.insufficient_positive_outer_folds")
    if float(outer_summary.get("singleFoldProfitShare") or 0.0) > config.maximumSingleFoldProfitShare:
        rejected.append("economic.one_fold_profit_concentration")
        warnings.append("economic.profit_concentrated_in_one_fold")
    regime_share = largest_positive_group_share(model_metrics.get("performanceByRegime") or {})
    if regime_share > config.maximumSingleRegimeProfitShare:
        rejected.append("economic.one_regime_profit_concentration")
        warnings.append("economic.profit_concentrated_in_one_regime")
    if float(model_metrics.get("tradeCoverage") or 0.0) < config.minimumPromotionTradeCoverage:
        rejected.append("economic.trade_coverage_too_low")
    if float(model_metrics.get("tradeRejectionRate") or 0.0) > config.maximumPromotionTradeRejectionRate:
        rejected.append("economic.trade_rejection_rate_too_high")
    for side, key in (("BUY", "buyPerformance"), ("SELL", "sellPerformance")):
        side_metrics = model_metrics.get(key) or {}
        if int(side_metrics.get("trades") or 0) < config.minimumDirectionalTradesPerSide:
            rejected.append(f"economic.{side.lower()}_sample_too_small")
        elif float(side_metrics.get("expectancy") or 0.0) <= 0.0:
            rejected.append(f"economic.{side.lower()}_expectancy_not_positive")
    if not bool(calibration.get("probabilitySizingApproved")):
        rejected.append("calibration.not_approved")
    if model_metrics.get("calibrationError") is not None and float(model_metrics["calibrationError"]) > config.maximumCalibrationEce:
        rejected.append("calibration.error_too_high")
    return {
        "promoted": not rejected,
        "baseline": DETERMINISTIC_BASELINE_NAME,
        "classificationAccuracyUsedForPromotion": False,
        "criteria": promotion_criteria_report(config),
        "comparisons": {
            "netExpectancyDelta": round(expectancy_delta, 6),
            "drawdownLimit": round(drawdown_limit, 6),
            "modelMaximumDrawdown": round(model_drawdown, 6),
            "baselineMaximumDrawdown": round(baseline_drawdown, 6),
            "singleRegimeProfitShare": round(regime_share, 6),
        },
        "rejectedReasonCodes": rejected,
        "warningFlags": warnings,
        "explanation": (
            "Model promoted by economic criteria versus the corrected deterministic baseline."
            if not rejected
            else "Model rejected by economic criteria versus the corrected deterministic baseline."
        ),
    }


def largest_positive_group_share(groups: dict[str, Any]) -> float:
    positive = [max(0.0, float((group or {}).get("netPnl") or 0.0)) for group in groups.values()]
    total = sum(positive)
    return (max(positive) / total) if total > 0 else 0.0


def promotion_criteria_report(config: MetaTrainingConfig) -> dict[str, Any]:
    return {
        "minimumNetExpectancyImprovement": config.minimumNetExpectancyImprovement,
        "maximumPromotionDrawdownMultiple": config.maximumPromotionDrawdownMultiple,
        "minimumPositiveEconomicOuterFolds": config.minimumPositiveEconomicOuterFolds,
        "maximumSingleFoldProfitShare": config.maximumSingleFoldProfitShare,
        "maximumSingleRegimeProfitShare": config.maximumSingleRegimeProfitShare,
        "minimumPromotionTradeCoverage": config.minimumPromotionTradeCoverage,
        "maximumPromotionTradeRejectionRate": config.maximumPromotionTradeRejectionRate,
        "minimumDirectionalTradesPerSide": config.minimumDirectionalTradesPerSide,
        "maximumCalibrationEce": config.maximumCalibrationEce,
    }


def config_report(config: MetaTrainingConfig) -> dict[str, Any]:
    return {
        "minimumTotalCandidates": config.minimumTotalCandidates,
        "minimumBuyCandidates": config.minimumBuyCandidates,
        "minimumSellCandidates": config.minimumSellCandidates,
        "minimumPositiveOutcomes": config.minimumPositiveOutcomes,
        "minimumNegativeOutcomes": config.minimumNegativeOutcomes,
        "minimumCandidatesPerOuterFold": config.minimumCandidatesPerOuterFold,
        "minimumTradingSessions": config.minimumTradingSessions,
        "minimumRegimesRepresented": config.minimumRegimesRepresented,
        "minimumCalibrationRows": config.minimumCalibrationRows,
        "minimumIsotonicRows": config.minimumIsotonicRows,
        "maximumCalibrationBrier": config.maximumCalibrationBrier,
        "maximumCalibrationLogLoss": config.maximumCalibrationLogLoss,
        "maximumCalibrationEce": config.maximumCalibrationEce,
        "outerFolds": config.outerFolds,
        "innerFolds": config.innerFolds,
        "finalTestFraction": config.finalTestFraction,
        "maximumHoldingHorizonMinutes": config.maximumHoldingHorizonMinutes,
        "embargoMinutes": config.embargoMinutes,
        "randomSeed": config.randomSeed,
        "minimumNetExpectancyImprovement": config.minimumNetExpectancyImprovement,
        "maximumPromotionDrawdownMultiple": config.maximumPromotionDrawdownMultiple,
        "minimumPositiveEconomicOuterFolds": config.minimumPositiveEconomicOuterFolds,
        "maximumSingleFoldProfitShare": config.maximumSingleFoldProfitShare,
        "maximumSingleRegimeProfitShare": config.maximumSingleRegimeProfitShare,
        "minimumPromotionTradeCoverage": config.minimumPromotionTradeCoverage,
        "maximumPromotionTradeRejectionRate": config.maximumPromotionTradeRejectionRate,
        "minimumDirectionalTradesPerSide": config.minimumDirectionalTradesPerSide,
    }


def validate_meta_training_requirements(examples: list[dict[str, Any]], config: MetaTrainingConfig) -> dict[str, Any]:
    label_counts = Counter(row["label"] for row in examples)
    outcome_counts = Counter(row["binaryOutcome"] for row in examples)
    sessions = {row["sessionDate"] for row in examples if row.get("sessionDate")}
    regimes = {row["regime"] for row in examples if row.get("regime")}
    failures = []
    checks = {
        "totalCandidates": len(examples),
        "buyCandidates": label_counts.get("BUY", 0),
        "sellCandidates": label_counts.get("SELL", 0),
        "positiveOutcomes": outcome_counts.get(1, 0),
        "negativeOutcomes": outcome_counts.get(0, 0),
        "tradingSessions": len(sessions),
        "regimesRepresented": len(regimes),
    }
    thresholds = {
        "totalCandidates": config.minimumTotalCandidates,
        "buyCandidates": config.minimumBuyCandidates,
        "sellCandidates": config.minimumSellCandidates,
        "positiveOutcomes": config.minimumPositiveOutcomes,
        "negativeOutcomes": config.minimumNegativeOutcomes,
        "tradingSessions": config.minimumTradingSessions,
        "regimesRepresented": config.minimumRegimesRepresented,
    }
    for name, minimum in thresholds.items():
        if checks[name] < minimum:
            failures.append(f"{name}<{minimum}")
    return {
        "sufficient": not failures,
        "failures": failures,
        "checks": checks,
        "thresholds": thresholds,
        "labelCounts": dict(label_counts),
        "outcomeCounts": dict(outcome_counts),
    }


def build_nested_walk_forward_plan(examples: list[dict[str, Any]], config: MetaTrainingConfig) -> dict[str, Any]:
    sorted_examples = sorted(examples, key=lambda row: row["timestamp"])
    final_size = max(config.minimumCandidatesPerOuterFold, int(math.ceil(len(sorted_examples) * config.finalTestFraction)))
    if len(sorted_examples) <= final_size + (config.minimumCandidatesPerOuterFold * 2):
        return {
            "sufficient": False,
            "message": "Not enough rows remain after reserving the final untouched test period.",
            "report": {"rows": len(sorted_examples), "finalTestRows": final_size},
        }
    development_rows = sorted_examples[:-final_size]
    final_test_rows = sorted_examples[-final_size:]
    outer_folds = chronological_purged_folds(
        development_rows,
        fold_count=config.outerFolds,
        minimum_validation_rows=config.minimumCandidatesPerOuterFold,
        embargo_minutes=config.embargoMinutes,
    )
    sufficient_folds = [
        fold
        for fold in outer_folds
        if len(fold["trainRows"]) >= config.minimumCandidatesPerOuterFold
        and len(fold["validationRows"]) >= config.minimumCandidatesPerOuterFold
        and len({row["label"] for row in fold["trainRows"]}) >= 2
    ]
    report = {
        "method": "nested_chronological_purged_walk_forward",
        "developmentRows": len(development_rows),
        "finalTestRows": len(final_test_rows),
        "finalTestStart": final_test_rows[0]["timestamp"].isoformat(),
        "finalTestEnd": final_test_rows[-1]["timestamp"].isoformat(),
        "outerFoldCount": len(outer_folds),
        "sufficientOuterFoldCount": len(sufficient_folds),
        "embargoMinutes": config.embargoMinutes,
        "maximumHoldingHorizonMinutes": config.maximumHoldingHorizonMinutes,
    }
    return {
        "sufficient": bool(sufficient_folds),
        "message": "No sufficient purged outer folds were available." if not sufficient_folds else "ok",
        "developmentRows": development_rows,
        "finalTestRows": final_test_rows,
        "outerFolds": outer_folds,
        "report": report,
    }


def chronological_purged_folds(
    rows: list[dict[str, Any]],
    *,
    fold_count: int,
    minimum_validation_rows: int,
    embargo_minutes: int,
) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda row: row["timestamp"])
    total = len(sorted_rows)
    if total < minimum_validation_rows * 2:
        return []
    validation_size = max(minimum_validation_rows, total // (fold_count + 2))
    folds = []
    embargo = timedelta(minutes=max(0, embargo_minutes))
    for index in range(max(1, fold_count)):
        validation_start_index = total - (fold_count - index) * validation_size
        validation_end_index = min(total, validation_start_index + validation_size)
        if validation_start_index <= minimum_validation_rows or validation_end_index > total:
            continue
        validation_rows = sorted_rows[validation_start_index:validation_end_index]
        validation_start = validation_rows[0]["timestamp"]
        cutoff = validation_start - embargo
        candidate_train_rows = sorted_rows[:validation_start_index]
        train_rows = [row for row in candidate_train_rows if row["labelEnd"] < cutoff]
        folds.append(
            {
                "fold": len(folds) + 1,
                "trainRows": train_rows,
                "validationRows": validation_rows,
                "purgedRows": len(candidate_train_rows) - len(train_rows),
                "embargoMinutes": embargo_minutes,
                "trainingWindowStart": train_rows[0]["timestamp"] if train_rows else None,
                "trainingWindowEnd": max((row["timestamp"] for row in train_rows), default=None),
                "validationWindowStart": validation_rows[0]["timestamp"],
                "validationWindowEnd": validation_rows[-1]["timestamp"],
                "labelWindowCutoff": cutoff,
            }
        )
    return folds


def run_outer_walk_forward_fold(fold: dict[str, Any], config: MetaTrainingConfig) -> dict[str, Any]:
    train_rows = fold["trainRows"]
    validation_rows = fold["validationRows"]
    report = fold_report(fold)
    if len(train_rows) < config.minimumCandidatesPerOuterFold or len(validation_rows) < config.minimumCandidatesPerOuterFold:
        return {**fold_result_base(fold, report), "status": "skipped_insufficient_rows"}
    if len({row["label"] for row in train_rows}) < 2:
        return {**fold_result_base(fold, report), "status": "skipped_insufficient_training_classes"}
    assert_fold_is_chronological_and_purged(fold)
    inner_folds = chronological_purged_folds(
        train_rows,
        fold_count=config.innerFolds,
        minimum_validation_rows=max(5, min(config.minimumCandidatesPerOuterFold, len(train_rows) // 4)),
        embargo_minutes=config.embargoMinutes,
    )
    hyperparameters = select_hyperparameters_from_inner_folds(inner_folds) if inner_folds else LOGISTIC_HYPERPARAMETER_GRID[0]
    oof_probability_rows = inner_out_of_fold_probabilities(train_rows, inner_folds, hyperparameters)
    calibration = tune_probability_calibration_from_probability_rows(
        oof_probability_rows,
        minimum_rows=config.minimumCalibrationRows,
        minimum_isotonic_rows=config.minimumIsotonicRows,
        maximum_brier=config.maximumCalibrationBrier,
        maximum_log_loss=config.maximumCalibrationLogLoss,
        maximum_ece=config.maximumCalibrationEce,
    )
    feature_names = sorted({feature for row in train_rows for feature in row["features"]})
    scaler = feature_scaler(train_rows, feature_names)
    model = train_softmax_logistic(train_rows, feature_names, scaler, **hyperparameters)
    model_probabilities = [
        apply_probability_calibration_model(predict_softmax_logistic_probabilities(model, row["features"]), calibration)
        for row in validation_rows
    ]
    predictions = [threshold_calibrated_probability_label(probabilities, calibration) for probabilities in model_probabilities]
    model_metric = evaluate_predictions(predictions, [row["label"] for row in validation_rows])
    random_forest_hyperparameters = (
        select_random_forest_hyperparameters_from_inner_folds(inner_folds)
        if inner_folds
        else dict(RANDOM_FOREST_HYPERPARAMETER_GRID[0])
    )
    random_forest_model = train_random_forest(
        train_rows,
        feature_names,
        tree_count=int(random_forest_hyperparameters["treeCount"]),
        max_depth=int(random_forest_hyperparameters["maxDepth"]),
        random_seed=config.randomSeed,
    )
    random_forest_probabilities = [predict_random_forest_probabilities(random_forest_model, row["features"]) for row in validation_rows]
    random_forest_predictions = [probability_label(probabilities) for probabilities in random_forest_probabilities]
    random_forest_metric = evaluate_predictions(random_forest_predictions, [row["label"] for row in validation_rows])
    deterministic_baseline_predictions = [deterministic_baseline_prediction(row) for row in validation_rows]
    economic_metrics = {
        "logistic_regression_champion": economic_performance(
            predictions,
            validation_rows,
            probability_distributions=model_probabilities,
            random_seed=config.randomSeed,
        ),
        "random_forest_challenger": economic_performance(
            random_forest_predictions,
            validation_rows,
            probability_distributions=random_forest_probabilities,
            random_seed=config.randomSeed,
        ),
        "deterministicBaseline": economic_performance(deterministic_baseline_predictions, validation_rows, random_seed=config.randomSeed),
    }
    baseline_metrics = {
        name: evaluate_predictions([baseline_prediction(row, name) for row in validation_rows], [row["label"] for row in validation_rows])
        for name in BASELINE_NAMES
    }
    best_baseline_metric = max(baseline_metrics.values(), key=lambda metric: metric["trustScore"])
    return {
        **fold_result_base(fold, report),
        "status": "validated",
        "hyperparameters": hyperparameters,
        "challengerHyperparameters": {
            "random_forest_challenger": random_forest_hyperparameters,
        },
        "calibration": calibration,
        "innerFoldCount": len(inner_folds),
        "outOfFoldCalibrationRows": len(oof_probability_rows),
        "modelMetric": model_metric,
        "modelMetrics": {
            "logistic_regression_champion": model_metric,
            "random_forest_challenger": random_forest_metric,
        },
        "challengerMetrics": {
            "random_forest_challenger": random_forest_metric,
        },
        "economicMetrics": economic_metrics,
        "baselineMetrics": baseline_metrics,
        "bestBaselineMetric": best_baseline_metric,
    }


def fold_result_base(fold: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    return {"fold": fold["fold"], "report": report}


def fold_report(fold: dict[str, Any]) -> dict[str, Any]:
    training_end = fold.get("trainingWindowEnd")
    validation_start = fold.get("validationWindowStart")
    gap_minutes = ((validation_start - training_end).total_seconds() / 60.0) if training_end and validation_start else None
    return {
        "fold": fold["fold"],
        "trainingRows": len(fold["trainRows"]),
        "validationRows": len(fold["validationRows"]),
        "purgedRows": fold["purgedRows"],
        "embargoMinutes": fold["embargoMinutes"],
        "actualGapMinutes": round(gap_minutes, 4) if gap_minutes is not None else None,
        "trainingWindowStart": iso_or_none(fold.get("trainingWindowStart")),
        "trainingWindowEnd": iso_or_none(training_end),
        "validationWindowStart": iso_or_none(validation_start),
        "validationWindowEnd": iso_or_none(fold.get("validationWindowEnd")),
        "labelWindowCutoff": iso_or_none(fold.get("labelWindowCutoff")),
    }


def assert_fold_is_chronological_and_purged(fold: dict[str, Any]) -> None:
    if not fold["trainRows"] or not fold["validationRows"]:
        return
    validation_start = fold["validationRows"][0]["timestamp"]
    cutoff = fold["labelWindowCutoff"]
    if any(row["timestamp"] >= validation_start for row in fold["trainRows"]):
        raise ValueError("training timestamp is not earlier than validation timestamp")
    if any(row["labelEnd"] >= cutoff for row in fold["trainRows"]):
        raise ValueError("purged fold contains a training label window inside the embargo gap")


def select_hyperparameters_from_inner_folds(inner_folds: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for params in LOGISTIC_HYPERPARAMETER_GRID:
        fold_scores = []
        for fold in inner_folds:
            if len(fold["trainRows"]) < 5 or len(fold["validationRows"]) < 5 or len({row["label"] for row in fold["trainRows"]}) < 2:
                continue
            assert_fold_is_chronological_and_purged(fold)
            feature_names = sorted({feature for row in fold["trainRows"] for feature in row["features"]})
            scaler = feature_scaler(fold["trainRows"], feature_names)
            model = train_softmax_logistic(fold["trainRows"], feature_names, scaler, **params)
            predictions = [probability_label(predict_softmax_logistic_probabilities(model, row["features"])) for row in fold["validationRows"]]
            metric = evaluate_predictions(predictions, [row["label"] for row in fold["validationRows"]])
            fold_scores.append(metric["trustScore"])
        average_score = mean(fold_scores) if fold_scores else -1.0
        scored.append((average_score, params))
    return dict(max(scored, key=lambda item: item[0])[1])


def inner_out_of_fold_probabilities(
    rows: list[dict[str, Any]],
    inner_folds: list[dict[str, Any]],
    hyperparameters: dict[str, Any],
) -> list[dict[str, Any]]:
    probability_rows: list[dict[str, Any]] = []
    for fold in inner_folds:
        if len(fold["trainRows"]) < 5 or len(fold["validationRows"]) < 1 or len({row["label"] for row in fold["trainRows"]}) < 2:
            continue
        assert_fold_is_chronological_and_purged(fold)
        feature_names = sorted({feature for row in fold["trainRows"] for feature in row["features"]})
        scaler = feature_scaler(fold["trainRows"], feature_names)
        model = train_softmax_logistic(fold["trainRows"], feature_names, scaler, **hyperparameters)
        for row in fold["validationRows"]:
            probability_rows.append(
                {
                    "probabilities": predict_softmax_logistic_probabilities(model, row["features"]),
                    "label": row["label"],
                    "candidateSide": row.get("label"),
                    "marketRegime": row.get("regime") or "unknown",
                    "rowId": row.get("rowId"),
                    "source": "inner_out_of_fold",
                }
            )
    return probability_rows


def select_random_forest_hyperparameters_from_inner_folds(inner_folds: list[dict[str, Any]]) -> dict[str, Any]:
    scored = []
    for params in RANDOM_FOREST_HYPERPARAMETER_GRID:
        fold_scores = []
        for fold in inner_folds:
            if len(fold["trainRows"]) < 8 or len(fold["validationRows"]) < 5 or len({row["label"] for row in fold["trainRows"]}) < 2:
                continue
            assert_fold_is_chronological_and_purged(fold)
            feature_names = sorted({feature for row in fold["trainRows"] for feature in row["features"]})
            model = train_random_forest(
                fold["trainRows"],
                feature_names,
                tree_count=int(params["treeCount"]),
                max_depth=int(params["maxDepth"]),
            )
            predictions = [predict_random_forest(model, row["features"]) for row in fold["validationRows"]]
            metric = evaluate_predictions(predictions, [row["label"] for row in fold["validationRows"]])
            fold_scores.append(metric["trustScore"])
        average_score = mean(fold_scores) if fold_scores else -1.0
        scored.append((average_score, params))
    return dict(max(scored, key=lambda item: item[0])[1])


def inner_out_of_fold_probabilities_for_random_forest(
    inner_folds: list[dict[str, Any]],
    hyperparameters: dict[str, Any],
    *,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> list[dict[str, Any]]:
    probability_rows: list[dict[str, Any]] = []
    for fold in inner_folds:
        if len(fold["trainRows"]) < 8 or len(fold["validationRows"]) < 1 or len({row["label"] for row in fold["trainRows"]}) < 2:
            continue
        assert_fold_is_chronological_and_purged(fold)
        feature_names = sorted({feature for row in fold["trainRows"] for feature in row["features"]})
        model = train_random_forest(
            fold["trainRows"],
            feature_names,
            tree_count=int(hyperparameters["treeCount"]),
            max_depth=int(hyperparameters["maxDepth"]),
            random_seed=random_seed,
        )
        for row in fold["validationRows"]:
            probability_rows.append(
                {
                    "probabilities": predict_random_forest_probabilities(model, row["features"]),
                    "label": row["label"],
                    "candidateSide": row.get("label"),
                    "marketRegime": row.get("regime") or "unknown",
                    "rowId": row.get("rowId"),
                    "source": "inner_out_of_fold",
                }
            )
    return probability_rows


def select_consensus_hyperparameters(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(json.dumps(result["hyperparameters"], sort_keys=True) for result in results if result.get("hyperparameters"))
    if not counts:
        return dict(LOGISTIC_HYPERPARAMETER_GRID[0])
    return json.loads(counts.most_common(1)[0][0])


def summarize_outer_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    model_scores = [float(result["modelMetric"]["trustScore"]) for result in results]
    baseline_scores = [float(result["bestBaselineMetric"]["trustScore"]) for result in results]
    return {
        "validatedFolds": len(results),
        "averageModelTrustScore": round(mean(model_scores), 4) if model_scores else 0.0,
        "averageBestBaselineTrustScore": round(mean(baseline_scores), 4) if baseline_scores else 0.0,
        "foldsBeatingBaseline": sum(1 for model, baseline in zip(model_scores, baseline_scores) if model > baseline),
    }


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def load_labeled_rows(decision_snapshot_dir: Path, *, symbol: str, session_date: str | None) -> list[dict[str, Any]]:
    safe_symbol = safe_name(symbol.upper())
    if session_date:
        paths = [decision_snapshot_dir / safe_name(session_date) / f"{safe_symbol}_decision_labels.jsonl"]
    else:
        paths = sorted(decision_snapshot_dir.glob(f"*/{safe_symbol}_decision_labels.jsonl"))
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return sorted(rows, key=lambda row: str(row.get("capturedAt") or ""))


def training_example(row: dict[str, Any], *, maximum_holding_horizon_minutes: int = 30) -> dict[str, Any]:
    features: dict[str, float] = {}
    add_numeric_features(features, "family.meta", ((row.get("familyScores") or {}).get("meta") or {}))
    add_numeric_features(features, "family.forecast", ((row.get("familyScores") or {}).get("forecast") or {}))
    add_numeric_features(features, "meta.familyAggregation", ((row.get("metaModelFeatures") or {}).get("familyAggregation") or {}))
    add_numeric_features(features, "meta", row.get("metaModelFeatures") or {}, max_depth=1)
    add_numeric_features(features, "final", row.get("finalDecision") or {}, max_depth=2)
    add_numeric_features(features, "barriers", row.get("barriers") or {})
    add_numeric_features(features, "entry", row.get("entry") or {})
    final = row.get("finalDecision") or {}
    features["signal.voting"] = signal_value(((final.get("voting") or {}).get("signal")))
    features["signal.weighted"] = signal_value(((final.get("weighted") or {}).get("signal")))
    features["signal.confidence"] = signal_value(((final.get("confidence") or {}).get("signal")))
    features["signal.regime"] = signal_value(((final.get("regime") or {}).get("signal")))
    features["signal.meta"] = signal_value(((final.get("meta") or {}).get("signal")))
    baselines = reconstructed_baseline_predictions(row)
    timestamp = example_timestamp(row)
    label_start = example_label_start(row, timestamp)
    label_end = example_label_end(row, label_start, maximum_holding_horizon_minutes)
    label = normalize_label(str(row.get("trainingLabel") or row.get("label") or "HOLD"))
    return {
        "rowId": str(row.get("snapshotId") or row.get("id") or row.get("capturedAt") or timestamp.isoformat()),
        "timestamp": timestamp,
        "labelStart": label_start,
        "labelEnd": label_end,
        "sessionDate": str(row.get("sessionDateNewYork") or row.get("sessionDate") or timestamp.date().isoformat()),
        "regime": example_regime(row),
        "label": label,
        "validationLabel": normalize_label(str(row.get("validationLabel") or row.get("label") or "HOLD")),
        "binaryOutcome": binary_outcome(row, label),
        "features": features,
        "baselines": baselines,
    }


def example_timestamp(row: dict[str, Any]) -> datetime:
    for key in ("decisionTimestampUtc", "decisionTimestamp", "capturedAt", "timestamp"):
        if row.get(key):
            return parse_datetime_utc(str(row[key]))
    return datetime.now(UTC)


def example_label_start(row: dict[str, Any], fallback: datetime) -> datetime:
    for key in ("labelStart", "entryTimestampUtc", "entryTimestamp"):
        if row.get(key):
            return parse_datetime_utc(str(row[key]))
    return fallback


def example_label_end(row: dict[str, Any], label_start: datetime, maximum_holding_horizon_minutes: int) -> datetime:
    candidates = [
        row.get("labelEnd"),
        row.get("exitTimestampUtc"),
        row.get("exitTimestamp"),
        (row.get("outcome") or {}).get("closedAt") if isinstance(row.get("outcome"), dict) else None,
        (row.get("finalOutcome") or {}).get("closedAt") if isinstance(row.get("finalOutcome"), dict) else None,
    ]
    for value in candidates:
        if value:
            return parse_datetime_utc(str(value))
    return label_start + timedelta(minutes=max(1, maximum_holding_horizon_minutes))


def example_regime(row: dict[str, Any]) -> str:
    for path in (
        ("regimeState", "label"),
        ("metaModelFeatures", "regime", "label"),
        ("marketRegime", "trend"),
    ):
        value: Any = row
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value:
            return str(value)
    return "unknown"


def binary_outcome(row: dict[str, Any], label: str) -> int:
    for key in ("costAdjustedTrainingLabel", "strictOutcomeLabel", "trainingOutcome", "successfulCandidate"):
        if key in row:
            return 1 if row.get(key) in {1, "1", True, "true", "success", "SUCCESS"} else 0
    outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    if outcome:
        for key in ("costAdjustedTrainingLabel", "strictOutcomeLabel", "success", "profitable"):
            if key in outcome:
                return 1 if outcome.get(key) in {1, "1", True, "true", "success", "SUCCESS"} else 0
    return 1 if label in {"BUY", "SELL"} else 0


def parse_datetime_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def reconstructed_baseline_predictions(row: dict[str, Any]) -> dict[str, str]:
    predictions = {
        "simple_voting": reconstructed_simple_voting(row),
        "weighted_voting": reconstructed_weighted_voting(row),
        "confidence_score_aggregation": reconstructed_confidence_score_aggregation(row),
        "market_regime_strategy_selection": reconstructed_market_regime_strategy_selection(row),
    }
    final_meta_signal = (((row.get("finalDecision") or {}).get("meta") or {}).get("signal"))
    predictions[DETERMINISTIC_BASELINE_NAME] = normalize_label(str(final_meta_signal)) if final_meta_signal else predictions["confidence_score_aggregation"]
    return predictions


def reconstructed_simple_voting(row: dict[str, Any]) -> str:
    votes = ((row.get("strategyOutputs") or {}).get("voting") or [])
    if isinstance(votes, list) and votes:
        counts = Counter(signal_to_label(vote.get("signal")) for vote in votes if isinstance(vote, dict))
        return winner_from_scores({"BUY": counts.get("BUY", 0), "SELL": counts.get("SELL", 0), "HOLD": counts.get("HOLD", 0)}, min_edge=1e-9)

    family_scores = family_score_map(row)
    counts = Counter(family_vote(score) for family, score in family_scores.items() if family in DIRECTIONAL_FAMILIES)
    return winner_from_scores({"BUY": counts.get("BUY", 0), "SELL": counts.get("SELL", 0), "HOLD": counts.get("HOLD", 0)}, min_edge=1e-9)


def reconstructed_weighted_voting(row: dict[str, Any]) -> str:
    weighted_outputs = ((row.get("strategyOutputs") or {}).get("weighted") or [])
    if isinstance(weighted_outputs, list) and weighted_outputs:
        scores = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        for output in weighted_outputs:
            if not isinstance(output, dict):
                continue
            weight = first_number(
                output.get("recalculatedWeight"),
                output.get("finalWeight"),
                output.get("smoothedWeight"),
                output.get("adjustedWeight"),
                output.get("effective_weight"),
                output.get("baseWeight"),
                default=1.0,
            )
            scores["BUY"] += number(output.get("pBuy")) * weight
            scores["SELL"] += number(output.get("pSell")) * weight
            scores["HOLD"] += number(output.get("pHold")) * weight
        if any(value > 0 for value in scores.values()):
            return winner_from_scores(scores, min_edge=0.01)

    family_scores = family_score_map(row)
    scores = aggregate_family_scores(family_scores, DIRECTIONAL_FAMILIES)
    return winner_from_scores(scores, min_edge=0.03)


def reconstructed_confidence_score_aggregation(row: dict[str, Any]) -> str:
    confidence_outputs = ((row.get("strategyOutputs") or {}).get("confidence") or [])
    if isinstance(confidence_outputs, list) and confidence_outputs:
        net_score = 0.0
        for output in confidence_outputs:
            if not isinstance(output, dict):
                continue
            contribution = parse_optional_number(output.get("contribution"))
            if contribution is None:
                direction = signal_value(output.get("signal"))
                contribution = direction * number(output.get("confidence")) * first_number(output.get("effective_weight"), output.get("base_weight"), default=1.0)
            net_score += contribution
        return signed_score_to_label(net_score, threshold=0.03)

    aggregation = family_aggregation_map(row)
    buy_total = sum(number(aggregation.get(f"{family}_buy_score")) for family in ["trend", "breakout", "mean_reversion", "reversal"])
    sell_total = sum(number(aggregation.get(f"{family}_sell_score")) for family in ["trend", "breakout", "mean_reversion", "reversal"])
    context = number(aggregation.get("confirmation_score")) + number(aggregation.get("regime_score"))
    return signed_score_to_label((buy_total - sell_total) + (context * 0.25), threshold=0.05)


def reconstructed_market_regime_strategy_selection(row: dict[str, Any]) -> str:
    regime_outputs = ((row.get("strategyOutputs") or {}).get("regime") or {}).get("selectedStrategies") or []
    if isinstance(regime_outputs, list) and regime_outputs:
        scores = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        for output in regime_outputs:
            if not isinstance(output, dict):
                continue
            label = signal_to_label(output.get("signal"))
            confidence = number(output.get("confidence"))
            weight = first_number(output.get("effective_weight"), output.get("base_weight"), default=1.0)
            scores[label] += confidence * weight
        if any(value > 0 for value in scores.values()):
            return winner_from_scores(scores, min_edge=0.01)

    family_scores = family_score_map(row)
    aggregation = family_aggregation_map(row)
    regime_score = number(aggregation.get("regime_score"))
    if regime_score > 0.05:
        selected_families = TREND_REGIME_FAMILIES
    elif regime_score < -0.05:
        selected_families = REVERSION_REGIME_FAMILIES
    else:
        selected_families = strongest_directional_families(family_scores)
    return winner_from_scores(aggregate_family_scores(family_scores, selected_families), min_edge=0.03)


def family_score_map(row: dict[str, Any]) -> dict[str, dict[str, float]]:
    family_scores = ((row.get("metaModelFeatures") or {}).get("familyScores") or {})
    if isinstance(family_scores, dict) and any(isinstance(value, dict) for value in family_scores.values()):
        return {
            str(family): {
                "buy": number(score.get("buy") if isinstance(score, dict) else 0),
                "sell": number(score.get("sell") if isinstance(score, dict) else 0),
                "hold": number(score.get("hold") if isinstance(score, dict) else 0),
            }
            for family, score in family_scores.items()
        }

    aggregation = family_aggregation_map(row)
    return {
        family: {
            "buy": number(aggregation.get(f"{family}_buy_score")),
            "sell": number(aggregation.get(f"{family}_sell_score")),
            "hold": 0.0,
        }
        for family in DIRECTIONAL_FAMILIES
    }


def family_aggregation_map(row: dict[str, Any]) -> dict[str, float]:
    candidates = [
        ((row.get("metaModelFeatures") or {}).get("familyAggregation") or {}),
        ((row.get("familyScores") or {}).get("meta") or {}),
        ((row.get("familyScores") or {}).get("forecast") or {}),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return {str(key): number(value) for key, value in candidate.items()}
    return {}


def family_vote(score: dict[str, float]) -> str:
    return winner_from_scores(
        {
            "BUY": number(score.get("buy")),
            "SELL": number(score.get("sell")),
            "HOLD": number(score.get("hold")),
        },
        min_edge=0.01,
    )


def aggregate_family_scores(family_scores: dict[str, dict[str, float]], families: list[str]) -> dict[str, float]:
    scores = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    for family in families:
        score = family_scores.get(family)
        if not isinstance(score, dict):
            continue
        scores["BUY"] += number(score.get("buy"))
        scores["SELL"] += number(score.get("sell"))
        scores["HOLD"] += number(score.get("hold"))
    return scores


def strongest_directional_families(family_scores: dict[str, dict[str, float]]) -> list[str]:
    ranked = sorted(
        DIRECTIONAL_FAMILIES,
        key=lambda family: max(number((family_scores.get(family) or {}).get("buy")), number((family_scores.get(family) or {}).get("sell"))),
        reverse=True,
    )
    return [family for family in ranked[:2] if max(number((family_scores.get(family) or {}).get("buy")), number((family_scores.get(family) or {}).get("sell"))) > 0.01] or DIRECTIONAL_FAMILIES


def winner_from_scores(scores: dict[str, float], *, min_edge: float) -> str:
    buy = number(scores.get("BUY"))
    sell = number(scores.get("SELL"))
    hold = number(scores.get("HOLD"))
    directional_edge = abs(buy - sell)
    if hold >= buy and hold >= sell:
        return "HOLD"
    if directional_edge < min_edge:
        return "HOLD"
    return "BUY" if buy > sell else "SELL"


def signed_score_to_label(score: float, *, threshold: float) -> str:
    if score > threshold:
        return "BUY"
    if score < -threshold:
        return "SELL"
    return "HOLD"


def first_number(*values: Any, default: float) -> float:
    for value in values:
        parsed = parse_optional_number(value)
        if parsed is not None:
            return parsed
    return default


def number(value: Any) -> float:
    parsed = parse_optional_number(value)
    return parsed if parsed is not None else 0.0


def parse_optional_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def add_numeric_features(target: dict[str, float], prefix: str, value: Any, *, max_depth: int = 4) -> None:
    if max_depth < 0:
        return
    if isinstance(value, bool):
        target[prefix] = 1.0 if value else 0.0
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        target[prefix] = float(value)
    elif isinstance(value, str) and value in SIGNAL_MAP:
        target[prefix] = SIGNAL_MAP[value]
    elif isinstance(value, dict):
        for key, child in value.items():
            clean_key = str(key).replace(" ", "_").replace(".", "_")
            add_numeric_features(target, f"{prefix}.{clean_key}", child, max_depth=max_depth - 1)


def normalize_label(value: str) -> str:
    value = value.upper().replace("NO-TRADE", "HOLD").replace("NO_TRADE", "HOLD")
    return value if value in LABELS else "HOLD"


def signal_to_label(value: Any) -> str:
    score = signal_value(value)
    if score > 0:
        return "BUY"
    if score < 0:
        return "SELL"
    return "HOLD"


def signal_value(value: Any) -> float:
    return SIGNAL_MAP.get(str(value), 0.0)


def feature_scaler(rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, dict[str, float]]:
    scaler: dict[str, dict[str, float]] = {}
    for feature in feature_names:
        values = [float(row["features"].get(feature, 0.0)) for row in rows]
        avg = mean(values) if values else 0.0
        variance = mean([(value - avg) ** 2 for value in values]) if values else 0.0
        scale = math.sqrt(variance) or 1.0
        scaler[feature] = {"mean": avg, "scale": scale}
    return scaler


def train_softmax_logistic(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    scaler: dict[str, dict[str, float]],
    *,
    epochs: int = 120,
    learningRate: float = 0.05,
    l2: float = 0.0005,
) -> dict[str, Any]:
    weights = {label: {feature: 0.0 for feature in feature_names} for label in LABELS}
    intercepts = {label: 0.0 for label in LABELS}
    counts = Counter(row["label"] for row in rows)
    class_weights = {label: len(rows) / (len(LABELS) * max(1, counts.get(label, 0))) for label in LABELS}
    learning_rate = float(learningRate)
    regularization = float(l2)
    for _ in range(max(1, int(epochs))):
        for row in rows:
            features = scaled_features(row["features"], feature_names, scaler)
            probabilities = softmax({label: intercepts[label] + dot(weights[label], features) for label in LABELS})
            for label in LABELS:
                target = 1.0 if row["label"] == label else 0.0
                gradient = (probabilities[label] - target) * class_weights[row["label"]]
                intercepts[label] -= learning_rate * gradient
                for feature, value in features.items():
                    weights[label][feature] -= learning_rate * ((gradient * value) + (regularization * weights[label][feature]))
    return {"weights": weights, "intercepts": intercepts, "featureNames": feature_names, "scaler": scaler}


def predict_softmax_logistic(model: dict[str, Any], features: dict[str, float]) -> str:
    probabilities = predict_softmax_logistic_probabilities(model, features)
    return max(probabilities.items(), key=lambda item: item[1])[0]


def predict_softmax_logistic_probabilities(model: dict[str, Any], features: dict[str, float]) -> dict[str, float]:
    feature_names = model["featureNames"]
    scaled = scaled_features(features, feature_names, model["scaler"])
    scores = {
        label: float(model["intercepts"][label]) + dot(model["weights"][label], scaled)
        for label in LABELS
    }
    return softmax(scores)


def train_random_forest(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    tree_count: int,
    max_depth: int,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    if not feature_names:
        majority = Counter(row["label"] for row in rows).most_common(1)[0][0]
        return {"trees": [{"label": majority}], "featureNames": feature_names, "randomSeed": random_seed}
    rng = random.Random(random_seed)
    trees = []
    for _ in range(tree_count):
        sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        trees.append(build_tree(sample, feature_names, rng, depth=0, max_depth=max_depth))
    return {"trees": trees, "featureNames": feature_names, "randomSeed": random_seed}


def predict_random_forest(model: dict[str, Any], features: dict[str, float]) -> str:
    probabilities = predict_random_forest_probabilities(model, features)
    return max(probabilities.items(), key=lambda item: item[1])[0] if probabilities else "HOLD"


def predict_random_forest_probabilities(model: dict[str, Any], features: dict[str, float]) -> dict[str, float]:
    votes = [predict_tree(tree, features) for tree in model["trees"]]
    if not votes:
        return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}
    counts = Counter(votes)
    total = len(votes) or 1
    return {label: counts.get(label, 0) / total for label in LABELS}


def tune_probability_calibration(rows: list[dict[str, Any]], probability_fn) -> dict[str, Any]:
    probability_rows = [
        {
            "probabilities": probability_fn(row["features"]),
            "label": row["label"],
            "candidateSide": row.get("label"),
            "marketRegime": row.get("regime") or "unknown",
            "rowId": row.get("rowId"),
            "source": "legacy_out_of_fold_required",
        }
        for row in rows
    ]
    return tune_probability_calibration_from_probability_rows(probability_rows)


def tune_probability_calibration_from_probability_rows(
    probability_rows: list[Any],
    *,
    minimum_rows: int = 60,
    minimum_isotonic_rows: int = 80,
    maximum_brier: float = 0.28,
    maximum_log_loss: float = 1.20,
    maximum_ece: float = 0.12,
) -> dict[str, Any]:
    normalized_rows = normalize_probability_rows(probability_rows)
    if not normalized_rows:
        return {
            "method": "none",
            "buyThreshold": 0.34,
            "sellThreshold": 0.34,
            "minDirectionalEdge": 0.0,
            "score": 0.0,
            "source": "empty_oof_fallback",
            "trainingRows": 0,
            "probabilitySizingApproved": False,
            "approvalReasonCodes": ["calibration.no_oof_rows"],
        }
    if any(row.get("source") == "in_sample" for row in normalized_rows):
        raise ValueError("calibration rows must be out-of-fold predictions, not in-sample predictions")
    probability_calibration = select_probability_calibration(
        normalized_rows,
        minimum_isotonic_rows=minimum_isotonic_rows,
        minimum_rows=minimum_rows,
        maximum_brier=maximum_brier,
        maximum_log_loss=maximum_log_loss,
        maximum_ece=maximum_ece,
    )
    thresholds = [0.24, 0.28, 0.32, 0.36, 0.4, 0.45, 0.5, 0.55, 0.6]
    edges = [-0.08, -0.04, 0.0, 0.04, 0.08, 0.12]
    best: dict[str, Any] = {"buyThreshold": 0.34, "sellThreshold": 0.34, "minDirectionalEdge": 0.0, "score": -1.0, "source": "inner_out_of_fold"}
    labels = [row["label"] for row in normalized_rows]
    for buy_threshold in thresholds:
        for sell_threshold in thresholds:
            for edge in edges:
                calibration = {
                    **probability_calibration,
                    "buyThreshold": buy_threshold,
                    "sellThreshold": sell_threshold,
                    "minDirectionalEdge": edge,
                }
                predictions = [
                    predict_calibrated_probabilities(row["probabilities"], calibration)
                    for row in normalized_rows
                ]
                metric = evaluate_predictions(predictions, labels)
                score = float(metric["trustScore"])
                if score > float(best["score"]):
                    best = {**calibration, "score": round(score, 4), "source": "inner_out_of_fold"}
    return best


def normalize_probability_rows(probability_rows: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for item in probability_rows:
        if isinstance(item, dict) and "probabilities" in item:
            row = dict(item)
        elif isinstance(item, tuple) and len(item) >= 2:
            row = {"probabilities": item[0], "label": item[1], "candidateSide": item[1], "marketRegime": "unknown", "source": "inner_out_of_fold"}
        else:
            continue
        row["label"] = normalize_label(str(row.get("label") or "HOLD"))
        row["candidateSide"] = normalize_label(str(row.get("candidateSide") or row["label"]))
        row["marketRegime"] = str(row.get("marketRegime") or "unknown")
        row["probabilities"] = normalize_probability_distribution(row.get("probabilities") or {})
        normalized.append(row)
    return normalized


def select_probability_calibration(
    rows: list[dict[str, Any]],
    *,
    minimum_isotonic_rows: int,
    minimum_rows: int,
    maximum_brier: float,
    maximum_log_loss: float,
    maximum_ece: float,
) -> dict[str, Any]:
    raw = calibration_candidate_report("none", {}, rows)
    candidates = [raw, calibration_candidate_report("sigmoid_platt", fit_platt_calibrators(rows), rows)]
    if len(rows) >= minimum_isotonic_rows:
        candidates.append(calibration_candidate_report("isotonic", fit_isotonic_calibrators(rows), rows))
    selected = min(candidates, key=lambda item: (item["metrics"]["logLoss"], item["metrics"]["brierScore"], item["metrics"]["expectedCalibrationError"]))
    metrics = selected["metrics"]
    approval_reasons = []
    if len(rows) < minimum_rows:
        approval_reasons.append("calibration.insufficient_oof_rows")
    if metrics["brierScore"] > maximum_brier:
        approval_reasons.append("calibration.brier_score_too_high")
    if metrics["logLoss"] > maximum_log_loss:
        approval_reasons.append("calibration.log_loss_too_high")
    if metrics["expectedCalibrationError"] > maximum_ece:
        approval_reasons.append("calibration.ece_too_high")
    return {
        "method": selected["method"],
        "calibrators": selected["calibrators"],
        "methodsEvaluated": [
            {"method": candidate["method"], "metrics": candidate["metrics"]}
            for candidate in candidates
        ],
        "metrics": metrics,
        "trainingRows": len(rows),
        "trainingRowIds": [row.get("rowId") for row in rows if row.get("rowId")],
        "source": "inner_out_of_fold",
        "probabilitySizingApproved": not approval_reasons,
        "approvalReasonCodes": approval_reasons or ["calibration.probability_sizing_approved"],
    }


def calibration_candidate_report(method: str, calibrators: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    calibrated_rows = [
        {
            **row,
            "calibratedProbabilities": apply_probability_calibration_model(row["probabilities"], {"method": method, "calibrators": calibrators}),
        }
        for row in rows
    ]
    metrics = calibration_metrics(calibrated_rows)
    return {"method": method, "calibrators": calibrators, "metrics": metrics}


def calibration_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    probabilities = [row["calibratedProbabilities"] for row in rows]
    labels = [row["label"] for row in rows]
    return {
        "brierScore": round(multiclass_brier_score(probabilities, labels), 6),
        "logLoss": round(multiclass_log_loss(probabilities, labels), 6),
        "expectedCalibrationError": round(expected_calibration_error(probabilities, labels), 6),
        "reliabilityCurve": reliability_curve(probabilities, labels),
        "byCandidateSide": grouped_calibration_metrics(rows, "candidateSide"),
        "byMarketRegime": grouped_calibration_metrics(rows, "marketRegime"),
    }


def predict_calibrated_probabilities(probabilities: dict[str, float], calibration: dict[str, Any]) -> str:
    calibrated = apply_probability_calibration_model(probabilities, calibration)
    return threshold_calibrated_probability_label(calibrated, calibration)


def threshold_calibrated_probability_label(probabilities: dict[str, float], calibration: dict[str, Any]) -> str:
    calibrated = normalize_probability_distribution(probabilities)
    buy = float(calibrated.get("BUY") or 0.0)
    sell = float(calibrated.get("SELL") or 0.0)
    hold = float(calibrated.get("HOLD") or 0.0)
    buy_threshold = float(calibration.get("buyThreshold") or 0.34)
    sell_threshold = float(calibration.get("sellThreshold") or 0.34)
    min_edge = float(calibration.get("minDirectionalEdge") or 0.0)
    if buy >= buy_threshold and buy >= sell and buy - hold >= min_edge:
        return "BUY"
    if sell >= sell_threshold and sell > buy and sell - hold >= min_edge:
        return "SELL"
    return "HOLD"


def apply_probability_calibration_model(probabilities: dict[str, float], calibration: dict[str, Any]) -> dict[str, float]:
    raw = normalize_probability_distribution(probabilities)
    method = str(calibration.get("method") or "none")
    calibrators = calibration.get("calibrators") or {}
    if method == "sigmoid_platt":
        values = {label: platt_predict(float(raw[label]), calibrators.get(label) or {}) for label in LABELS}
        return normalize_probability_distribution(values)
    if method == "isotonic":
        values = {label: isotonic_predict(float(raw[label]), calibrators.get(label) or []) for label in LABELS}
        return normalize_probability_distribution(values)
    return raw


def normalize_probability_distribution(probabilities: dict[str, Any]) -> dict[str, float]:
    values = {label: max(0.0, float(probabilities.get(label) or 0.0)) for label in LABELS}
    total = sum(values.values())
    if total <= 0:
        return {"BUY": 1 / 3, "SELL": 1 / 3, "HOLD": 1 / 3}
    return {label: values[label] / total for label in LABELS}


def fit_platt_calibrators(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        label: fit_platt_binary(
            [float(row["probabilities"][label]) for row in rows],
            [1 if row["label"] == label else 0 for row in rows],
        )
        for label in LABELS
    }


def fit_platt_binary(probabilities: list[float], outcomes: list[int], *, epochs: int = 350, learning_rate: float = 0.08, l2: float = 0.001) -> dict[str, float]:
    if not probabilities or len(set(outcomes)) < 2:
        base_rate = sum(outcomes) / max(1, len(outcomes))
        return {"slope": 1.0, "intercept": logit(clamp_probability(base_rate))}
    slope = 1.0
    intercept = 0.0
    logits = [logit(clamp_probability(value)) for value in probabilities]
    for _ in range(epochs):
        slope_gradient = 0.0
        intercept_gradient = 0.0
        for raw_logit, outcome in zip(logits, outcomes):
            prediction = sigmoid((slope * raw_logit) + intercept)
            error = prediction - outcome
            slope_gradient += error * raw_logit
            intercept_gradient += error
        slope_gradient = (slope_gradient / len(logits)) + (l2 * slope)
        intercept_gradient /= len(logits)
        slope -= learning_rate * slope_gradient
        intercept -= learning_rate * intercept_gradient
    return {"slope": round(slope, 8), "intercept": round(intercept, 8)}


def platt_predict(probability: float, calibrator: dict[str, float]) -> float:
    slope = float(calibrator.get("slope", 1.0))
    intercept = float(calibrator.get("intercept", 0.0))
    return sigmoid((slope * logit(clamp_probability(probability))) + intercept)


def fit_isotonic_calibrators(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, float]]]:
    return {
        label: fit_isotonic_binary(
            [float(row["probabilities"][label]) for row in rows],
            [1 if row["label"] == label else 0 for row in rows],
        )
        for label in LABELS
    }


def fit_isotonic_binary(probabilities: list[float], outcomes: list[int]) -> list[dict[str, float]]:
    pairs = sorted(zip(probabilities, outcomes), key=lambda item: item[0])
    if not pairs:
        return [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}]
    blocks: list[dict[str, float]] = []
    for probability, outcome in pairs:
        blocks.append({"sumY": float(outcome), "weight": 1.0, "minX": float(probability), "maxX": float(probability)})
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            if left["sumY"] / left["weight"] <= right["sumY"] / right["weight"]:
                break
            merged = {
                "sumY": left["sumY"] + right["sumY"],
                "weight": left["weight"] + right["weight"],
                "minX": left["minX"],
                "maxX": right["maxX"],
            }
            blocks[-2:] = [merged]
    points = [{"x": 0.0, "y": blocks[0]["sumY"] / blocks[0]["weight"]}]
    for block in blocks:
        points.append({"x": round(block["maxX"], 8), "y": round(block["sumY"] / block["weight"], 8)})
    if points[-1]["x"] < 1.0:
        points.append({"x": 1.0, "y": points[-1]["y"]})
    return points


def isotonic_predict(probability: float, points: list[dict[str, float]]) -> float:
    if not points:
        return probability
    previous = points[0]
    for point in points[1:]:
        if probability <= point["x"]:
            left_x = float(previous["x"])
            right_x = float(point["x"])
            if right_x <= left_x:
                return float(point["y"])
            fraction = (probability - left_x) / (right_x - left_x)
            return float(previous["y"]) + fraction * (float(point["y"]) - float(previous["y"]))
        previous = point
    return float(points[-1]["y"])


def multiclass_brier_score(probabilities: list[dict[str, float]], labels: list[str]) -> float:
    if not probabilities:
        return 1.0
    total = 0.0
    for row, label in zip(probabilities, labels):
        total += sum((float(row[class_name]) - (1.0 if label == class_name else 0.0)) ** 2 for class_name in LABELS) / len(LABELS)
    return total / len(probabilities)


def multiclass_log_loss(probabilities: list[dict[str, float]], labels: list[str]) -> float:
    if not probabilities:
        return 99.0
    return -sum(math.log(clamp_probability(float(row[label]))) for row, label in zip(probabilities, labels)) / len(probabilities)


def expected_calibration_error(probabilities: list[dict[str, float]], labels: list[str], *, bins: int = 10) -> float:
    curve = reliability_curve(probabilities, labels, bins=bins)
    total = sum(int(bucket["count"]) for bucket in curve) or 1
    return sum((int(bucket["count"]) / total) * abs(float(bucket["accuracy"]) - float(bucket["averageConfidence"])) for bucket in curve)


def reliability_curve(probabilities: list[dict[str, float]], labels: list[str], *, bins: int = 10) -> list[dict[str, Any]]:
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for row, label in zip(probabilities, labels):
        predicted_label, confidence = max(row.items(), key=lambda item: item[1])
        bucket_index = min(bins - 1, int(confidence * bins))
        buckets[bucket_index].append((float(confidence), 1 if predicted_label == label else 0))
    curve = []
    for index, bucket in enumerate(buckets):
        lower = index / bins
        upper = (index + 1) / bins
        count = len(bucket)
        average_confidence = sum(value for value, _ in bucket) / count if count else 0.0
        accuracy = sum(hit for _, hit in bucket) / count if count else 0.0
        curve.append(
            {
                "binLower": round(lower, 4),
                "binUpper": round(upper, 4),
                "count": count,
                "averageConfidence": round(average_confidence, 6),
                "accuracy": round(accuracy, 6),
            }
        )
    return curve


def grouped_calibration_metrics(rows: list[dict[str, Any]], group_key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_key) or "unknown")].append(row)
    return {
        group: {
            "rows": len(items),
            "brierScore": round(multiclass_brier_score([item["calibratedProbabilities"] for item in items], [item["label"] for item in items]), 6),
            "logLoss": round(multiclass_log_loss([item["calibratedProbabilities"] for item in items], [item["label"] for item in items]), 6),
            "expectedCalibrationError": round(expected_calibration_error([item["calibratedProbabilities"] for item in items], [item["label"] for item in items]), 6),
        }
        for group, items in groups.items()
    }


def clamp_probability(value: float) -> float:
    return max(1e-6, min(1 - 1e-6, float(value)))


def logit(value: float) -> float:
    probability = clamp_probability(value)
    return math.log(probability / (1.0 - probability))


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(max(-35.0, min(35.0, -value))))


def probability_label(probabilities: dict[str, float]) -> str:
    return max(probabilities.items(), key=lambda item: item[1])[0] if probabilities else "HOLD"


def feature_matrix(rows: list[dict[str, Any]], feature_names: list[str]) -> list[list[float]]:
    return [
        [float(row["features"].get(feature, 0.0)) for feature in feature_names]
        for row in rows
    ]


def feature_vector(features: dict[str, float], feature_names: list[str]) -> list[float]:
    return [float(features.get(feature, 0.0)) for feature in feature_names]


def train_xgboost_booster(rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    try:
        import xgboost as xgb  # type: ignore[import-not-found]
    except Exception as exc:
        return {"available": False, "reason": f"xgboost import failed: {exc}"}
    try:
        dtrain = xgb.DMatrix(
            feature_matrix(rows, feature_names),
            label=[LABEL_TO_INDEX[row["label"]] for row in rows],
            feature_names=feature_names,
        )
        booster = xgb.train(
            {
                "objective": "multi:softprob",
                "num_class": len(LABELS),
                "eval_metric": "mlogloss",
                "eta": 0.05,
                "max_depth": 3,
                "subsample": 0.85,
                "colsample_bytree": 0.85,
                "min_child_weight": 2,
                "lambda": 1.0,
                "alpha": 0.05,
                "seed": 17,
                "nthread": 1,
                "verbosity": 0,
            },
            dtrain,
            num_boost_round=90,
        )
        return {
            "available": True,
            "kind": "xgboost",
            "featureNames": feature_names,
            "classes": LABELS,
            "booster": booster,
            "modelJson": bytes(booster.save_raw(raw_format="json")).decode("utf-8"),
            "params": {"numBoostRound": 90, "maxDepth": 3, "eta": 0.05},
        }
    except Exception as exc:
        return {"available": False, "reason": f"xgboost training failed: {exc}"}


def predict_xgboost_probabilities(model: dict[str, Any], features: dict[str, float]) -> dict[str, float]:
    booster = model.get("booster")
    if booster is None:
        return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}
    import xgboost as xgb  # type: ignore[import-not-found]

    dmatrix = xgb.DMatrix(
        [feature_vector(features, model["featureNames"])],
        feature_names=model["featureNames"],
    )
    values = booster.predict(dmatrix)[0]
    return {label: float(values[index]) for index, label in enumerate(LABELS)}


def train_lightgbm_booster(rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    try:
        import lightgbm as lgb  # type: ignore[import-not-found]
        import numpy as np
    except Exception as exc:
        return {"available": False, "reason": f"lightgbm import failed: {exc}"}
    try:
        dataset = lgb.Dataset(
            np.asarray(feature_matrix(rows, feature_names), dtype=float),
            label=np.asarray([LABEL_TO_INDEX[row["label"]] for row in rows], dtype=int),
            feature_name=feature_names,
            free_raw_data=False,
        )
        booster = lgb.train(
            {
                "objective": "multiclass",
                "num_class": len(LABELS),
                "metric": "multi_logloss",
                "learning_rate": 0.05,
                "num_leaves": 15,
                "max_depth": 4,
                "feature_fraction": 0.85,
                "bagging_fraction": 0.85,
                "bagging_freq": 1,
                "min_data_in_leaf": 12,
                "verbosity": -1,
                "seed": 17,
                "num_threads": 1,
            },
            dataset,
            num_boost_round=90,
        )
        return {
            "available": True,
            "kind": "lightgbm",
            "featureNames": feature_names,
            "classes": LABELS,
            "booster": booster,
            "modelText": booster.model_to_string(),
            "params": {"numBoostRound": 90, "maxDepth": 4, "learningRate": 0.05},
        }
    except Exception as exc:
        return {"available": False, "reason": f"lightgbm training failed: {exc}"}


def predict_lightgbm_probabilities(model: dict[str, Any], features: dict[str, float]) -> dict[str, float]:
    booster = model.get("booster")
    if booster is None:
        return {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}
    import numpy as np

    values = booster.predict(np.asarray([feature_vector(features, model["featureNames"])], dtype=float))[0]
    return {label: float(values[index]) for index, label in enumerate(LABELS)}


def serializable_booster_model(model: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in model.items()
        if key not in {"booster", "available"}
    }


def build_tree(rows: list[dict[str, Any]], feature_names: list[str], rng: random.Random, *, depth: int, max_depth: int) -> dict[str, Any]:
    labels = [row["label"] for row in rows]
    majority = Counter(labels).most_common(1)[0][0]
    if depth >= max_depth or len(set(labels)) == 1 or len(rows) < 8 or not feature_names:
        return {"label": majority}
    candidate_count = max(1, int(math.sqrt(max(1, len(feature_names)))))
    candidates = rng.sample(feature_names, min(candidate_count, len(feature_names)))
    split = best_split(rows, candidates)
    if split is None:
        return {"label": majority}
    feature, threshold, left_rows, right_rows = split
    return {
        "feature": feature,
        "threshold": threshold,
        "default": majority,
        "left": build_tree(left_rows, feature_names, rng, depth=depth + 1, max_depth=max_depth),
        "right": build_tree(right_rows, feature_names, rng, depth=depth + 1, max_depth=max_depth),
    }


def best_split(rows: list[dict[str, Any]], features: list[str]) -> tuple[str, float, list[dict[str, Any]], list[dict[str, Any]]] | None:
    best: tuple[float, str, float, list[dict[str, Any]], list[dict[str, Any]]] | None = None
    for feature in features:
        values = sorted({float(row["features"].get(feature, 0.0)) for row in rows})
        if len(values) < 2:
            continue
        step = max(1, len(values) // 8)
        thresholds = [(values[index - 1] + values[index]) / 2 for index in range(1, len(values), step)]
        for threshold in thresholds:
            left = [row for row in rows if float(row["features"].get(feature, 0.0)) <= threshold]
            right = [row for row in rows if float(row["features"].get(feature, 0.0)) > threshold]
            if len(left) < 3 or len(right) < 3:
                continue
            impurity = (len(left) / len(rows)) * gini(left) + (len(right) / len(rows)) * gini(right)
            if best is None or impurity < best[0]:
                best = (impurity, feature, threshold, left, right)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def predict_tree(tree: dict[str, Any], features: dict[str, float]) -> str:
    if "label" in tree:
        return str(tree["label"])
    value = float(features.get(str(tree["feature"]), 0.0))
    branch = tree["left"] if value <= float(tree["threshold"]) else tree["right"]
    return predict_tree(branch, features)


def gini(rows: list[dict[str, Any]]) -> float:
    counts = Counter(row["label"] for row in rows)
    total = len(rows) or 1
    return 1 - sum((count / total) ** 2 for count in counts.values())


def evaluate_predictions(predictions: list[str], labels: list[str]) -> dict[str, Any]:
    total = len(labels) or 1
    accuracy = sum(1 for pred, label in zip(predictions, labels) if pred == label) / total
    per_class = {}
    f1_scores = []
    recall_scores = []
    for label in LABELS:
        tp = sum(1 for pred, actual in zip(predictions, labels) if pred == label and actual == label)
        fp = sum(1 for pred, actual in zip(predictions, labels) if pred == label and actual != label)
        fn = sum(1 for pred, actual in zip(predictions, labels) if pred != label and actual == label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
        f1_scores.append(f1)
        if any(actual == label for actual in labels):
            recall_scores.append(recall)
    directional_f1_values = [float(per_class[label]["f1"]) for label in ["BUY", "SELL"]]
    directional_recall_values = [
        float(per_class[label]["recall"])
        for label in ["BUY", "SELL"]
        if any(actual == label for actual in labels)
    ]
    macro_f1 = sum(f1_scores) / len(f1_scores)
    directional_macro_f1 = sum(directional_f1_values) / len(directional_f1_values)
    non_hold_recall = sum(directional_recall_values) / len(directional_recall_values) if directional_recall_values else 0.0
    balanced_accuracy = sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
    trust_score = (0.45 * directional_macro_f1) + (0.35 * macro_f1) + (0.20 * non_hold_recall)
    return {
        "accuracy": round(accuracy, 4),
        "macroF1": round(macro_f1, 4),
        "directionalMacroF1": round(directional_macro_f1, 4),
        "nonHoldRecall": round(non_hold_recall, 4),
        "balancedAccuracy": round(balanced_accuracy, 4),
        "trustScore": round(trust_score, 4),
        "perClass": per_class,
        "predictedCounts": dict(Counter(predictions)),
        "actualCounts": dict(Counter(labels)),
    }


def baseline_prediction(row: dict[str, Any], name: str) -> str:
    return row["baselines"].get(name, "HOLD")


def scaled_features(features: dict[str, float], feature_names: list[str], scaler: dict[str, dict[str, float]]) -> dict[str, float]:
    return {
        feature: (float(features.get(feature, 0.0)) - scaler[feature]["mean"]) / scaler[feature]["scale"]
        for feature in feature_names
    }


def dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(float(weights.get(feature, 0.0)) * value for feature, value in features.items())


def softmax(scores: dict[str, float]) -> dict[str, float]:
    max_score = max(scores.values()) if scores else 0.0
    exps = {label: math.exp(max(-35.0, min(35.0, score - max_score))) for label, score in scores.items()}
    total = sum(exps.values()) or 1.0
    return {label: value / total for label, value in exps.items()}


def safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"_", "-"} else "-" for character in value).strip("-") or "unknown"


def meta_strategy_artifact_path(decision_snapshot_dir: Path, *, symbol: str, session_date: str | None) -> Path:
    suffix = safe_name(session_date) if session_date else "all_sessions"
    return decision_snapshot_dir / "models" / f"{safe_name(symbol.upper())}_meta_strategy_baselines_{suffix}.json"
