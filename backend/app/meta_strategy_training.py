from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any


LABELS = ["BUY", "SELL", "HOLD"]
LABEL_TO_INDEX = {label: index for index, label in enumerate(LABELS)}
BASELINE_NAMES = [
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
) -> dict[str, Any]:
    rows = load_labeled_rows(decision_snapshot_dir, symbol=symbol, session_date=session_date)
    if len(rows) < min_rows:
        return save_latest_training_status(decision_snapshot_dir, {
            "status": "insufficient_data",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": len(rows),
            "minimumRows": min_rows,
            "message": f"Need at least {min_rows} labeled decision snapshots before training baseline models.",
            "trainedAt": datetime.now(UTC).isoformat(),
            "trusted": False,
        })

    examples = [training_example(row) for row in rows]
    examples = [example for example in examples if example["label"] in LABELS]
    split_index = max(1, int(len(examples) * (1 - test_fraction)))
    split_index = min(split_index, len(examples) - 1)
    train_rows = examples[:split_index]
    test_rows = examples[split_index:]
    feature_names = sorted({feature for row in train_rows for feature in row["features"]})
    if len({row["label"] for row in train_rows}) < 2 or len(test_rows) < 5:
        return save_latest_training_status(decision_snapshot_dir, {
            "status": "insufficient_class_balance",
            "symbol": symbol.upper(),
            "sessionDate": session_date,
            "rows": len(examples),
            "trainRows": len(train_rows),
            "testRows": len(test_rows),
            "featureCount": len(feature_names),
            "labelCounts": dict(Counter(row["label"] for row in examples)),
            "validationLabelCounts": dict(Counter(row["validationLabel"] for row in examples)),
            "message": "Need at least two label classes in training data and five holdout rows.",
            "trainedAt": datetime.now(UTC).isoformat(),
            "trusted": False,
        })

    scaler = feature_scaler(train_rows, feature_names)
    logistic_model = train_softmax_logistic(train_rows, feature_names, scaler)
    random_forest_model = train_random_forest(train_rows, feature_names, tree_count=120, max_depth=7)
    logistic_calibration = tune_probability_calibration(
        train_rows,
        lambda features: predict_softmax_logistic_probabilities(logistic_model, features),
    )
    forest_calibration = tune_probability_calibration(
        train_rows,
        lambda features: predict_random_forest_probabilities(random_forest_model, features),
    )
    booster_models: dict[str, dict[str, Any]] = {}
    unavailable_models: dict[str, str] = {}
    xgboost_model = train_xgboost_booster(train_rows, feature_names)
    if xgboost_model.get("available"):
        booster_models["xgboost"] = xgboost_model
        booster_models["xgboost_calibrated"] = {
            **xgboost_model,
            "kind": "xgboost_calibrated",
            "calibration": tune_probability_calibration(
                train_rows,
                lambda features: predict_xgboost_probabilities(xgboost_model, features),
            ),
        }
    else:
        unavailable_models["xgboost"] = str(xgboost_model.get("reason") or "xgboost is not available")
    lightgbm_model = train_lightgbm_booster(train_rows, feature_names)
    if lightgbm_model.get("available"):
        booster_models["lightgbm"] = lightgbm_model
        booster_models["lightgbm_calibrated"] = {
            **lightgbm_model,
            "kind": "lightgbm_calibrated",
            "calibration": tune_probability_calibration(
                train_rows,
                lambda features: predict_lightgbm_probabilities(lightgbm_model, features),
            ),
        }
    else:
        unavailable_models["lightgbm"] = str(lightgbm_model.get("reason") or "lightgbm is not available")

    baseline_metrics = {
        "simple_voting": evaluate_predictions([baseline_prediction(row, "simple_voting") for row in test_rows], [row["label"] for row in test_rows]),
        "weighted_voting": evaluate_predictions([baseline_prediction(row, "weighted_voting") for row in test_rows], [row["label"] for row in test_rows]),
        "confidence_score_aggregation": evaluate_predictions([baseline_prediction(row, "confidence_score_aggregation") for row in test_rows], [row["label"] for row in test_rows]),
        "market_regime_strategy_selection": evaluate_predictions([baseline_prediction(row, "market_regime_strategy_selection") for row in test_rows], [row["label"] for row in test_rows]),
    }
    model_predictions = {
        "logistic_regression": [predict_softmax_logistic(logistic_model, row["features"]) for row in test_rows],
        "random_forest": [predict_random_forest(random_forest_model, row["features"]) for row in test_rows],
        "logistic_regression_calibrated": [
            predict_calibrated_probabilities(predict_softmax_logistic_probabilities(logistic_model, row["features"]), logistic_calibration)
            for row in test_rows
        ],
        "random_forest_calibrated": [
            predict_calibrated_probabilities(predict_random_forest_probabilities(random_forest_model, row["features"]), forest_calibration)
            for row in test_rows
        ],
    }
    for name, model in booster_models.items():
        if name == "xgboost":
            model_predictions[name] = [probability_label(predict_xgboost_probabilities(model, row["features"])) for row in test_rows]
        elif name == "xgboost_calibrated":
            model_predictions[name] = [
                predict_calibrated_probabilities(predict_xgboost_probabilities(model, row["features"]), model["calibration"])
                for row in test_rows
            ]
        elif name == "lightgbm":
            model_predictions[name] = [probability_label(predict_lightgbm_probabilities(model, row["features"])) for row in test_rows]
        elif name == "lightgbm_calibrated":
            model_predictions[name] = [
                predict_calibrated_probabilities(predict_lightgbm_probabilities(model, row["features"]), model["calibration"])
                for row in test_rows
            ]
    model_metrics = {
        name: evaluate_predictions(predictions, [row["label"] for row in test_rows])
        for name, predictions in model_predictions.items()
    }
    strict_validation_metrics = {
        "baselines": {
            name: evaluate_predictions([baseline_prediction(row, name) for row in test_rows], [row["validationLabel"] for row in test_rows])
            for name in BASELINE_NAMES
        },
        "models": {
            name: evaluate_predictions(predictions, [row["validationLabel"] for row in test_rows])
            for name, predictions in model_predictions.items()
        },
        "labelCounts": dict(Counter(row["validationLabel"] for row in examples)),
        "holdoutLabelCounts": dict(Counter(row["validationLabel"] for row in test_rows)),
        "policy": "Strict target-before-stop label is retained as validation only; models are trained on trainingLabel.",
    }
    best_baseline_score = max(metric["trustScore"] for metric in baseline_metrics.values())
    best_model_name, best_model_metric = sorted(
        model_metrics.items(),
        key=lambda item: (item[1]["trustScore"], item[1]["directionalMacroF1"], item[1]["macroF1"]),
        reverse=True,
    )[0]
    best_baseline_accuracy = max(metric["accuracy"] for metric in baseline_metrics.values())
    best_baseline_directional_f1 = max(metric["directionalMacroF1"] for metric in baseline_metrics.values())
    non_hold_labels_in_test = [
        label for label in ["BUY", "SELL"]
        if int(best_model_metric.get("actualCounts", {}).get(label) or 0) > 0
    ]
    non_hold_ready = bool(non_hold_labels_in_test) and all(
        float((best_model_metric.get("perClass", {}).get(label) or {}).get("recall") or 0.0) > 0.0
        for label in non_hold_labels_in_test
    )
    trusted = (
        best_model_metric["trustScore"] > best_baseline_score
        and best_model_metric["directionalMacroF1"] > best_baseline_directional_f1
        and non_hold_ready
    )
    trust_message = None
    if not trusted and not non_hold_ready:
        trust_message = "Best ML model did not detect every non-HOLD label class present in the holdout set."
    elif not trusted:
        trust_message = "Best ML model has not beaten every reconstructed baseline on directional trust score yet."

    artifact = {
        "status": "ready",
        "version": 1,
        "trainedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "symbol": symbol.upper(),
        "sessionDate": session_date,
        "rows": len(examples),
        "trainRows": len(train_rows),
        "testRows": len(test_rows),
        "featureCount": len(feature_names),
        "labelCounts": dict(Counter(row["label"] for row in examples)),
        "trainingLabelCounts": dict(Counter(row["label"] for row in examples)),
        "validationLabelCounts": dict(Counter(row["validationLabel"] for row in examples)),
        "labelPolicy": "Models train on ATR-friendly trainingLabel. Strict target-before-stop label remains available as validationLabel.",
        "models": {
            "logistic_regression": {
                "kind": "softmax_logistic_regression",
                "featureNames": feature_names,
                "scaler": scaler,
                "weights": logistic_model["weights"],
                "intercepts": logistic_model["intercepts"],
            },
            "logistic_regression_calibrated": {
                "kind": "softmax_logistic_regression_calibrated",
                "featureNames": feature_names,
                "scaler": scaler,
                "weights": logistic_model["weights"],
                "intercepts": logistic_model["intercepts"],
                "calibration": logistic_calibration,
            },
            "random_forest": {
                "kind": "local_random_forest",
                "featureNames": feature_names,
                "classes": LABELS,
                "trees": random_forest_model["trees"],
            },
            "random_forest_calibrated": {
                "kind": "local_random_forest_calibrated",
                "featureNames": feature_names,
                "classes": LABELS,
                "trees": random_forest_model["trees"],
                "calibration": forest_calibration,
            },
            **{name: serializable_booster_model(model) for name, model in booster_models.items()},
        },
        "metrics": {
            "baselines": baseline_metrics,
            "baselinePolicy": "Baselines are reconstructed from raw strategyOutputs when present, otherwise from saved family scores. They do not trust finalDecision.*.signal.",
            "models": model_metrics,
            "unavailableModels": unavailable_models,
            "strictValidation": strict_validation_metrics,
            "bestBaselineMacroF1": max(metric["macroF1"] for metric in baseline_metrics.values()),
            "bestBaselineTrustScore": best_baseline_score,
            "bestBaselineDirectionalMacroF1": best_baseline_directional_f1,
            "bestBaselineAccuracy": best_baseline_accuracy,
            "bestModel": best_model_name,
            "trusted": trusted,
            "trustRule": "Trust only when the best ML model beats every reconstructed baseline by directional trust score, beats baseline BUY/SELL F1, and recalls every non-HOLD holdout class.",
            "trustDiagnostics": {
                "nonHoldLabelsInTest": non_hold_labels_in_test,
                "nonHoldReady": non_hold_ready,
                "bestModelTrustScore": best_model_metric["trustScore"],
                "bestBaselineTrustScore": best_baseline_score,
                "bestModelDirectionalMacroF1": best_model_metric["directionalMacroF1"],
                "bestBaselineDirectionalMacroF1": best_baseline_directional_f1,
            },
        },
        "message": trust_message,
    }
    artifact_path = meta_strategy_artifact_path(decision_snapshot_dir, symbol=symbol, session_date=session_date)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = save_latest_training_status(decision_snapshot_dir, artifact)
    return {**latest, "artifactPath": str(artifact_path)}


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


def training_example(row: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "label": normalize_label(str(row.get("trainingLabel") or row.get("label") or "HOLD")),
        "validationLabel": normalize_label(str(row.get("validationLabel") or row.get("label") or "HOLD")),
        "features": features,
        "baselines": baselines,
    }


def reconstructed_baseline_predictions(row: dict[str, Any]) -> dict[str, str]:
    return {
        "simple_voting": reconstructed_simple_voting(row),
        "weighted_voting": reconstructed_weighted_voting(row),
        "confidence_score_aggregation": reconstructed_confidence_score_aggregation(row),
        "market_regime_strategy_selection": reconstructed_market_regime_strategy_selection(row),
    }


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


def train_softmax_logistic(rows: list[dict[str, Any]], feature_names: list[str], scaler: dict[str, dict[str, float]]) -> dict[str, Any]:
    weights = {label: {feature: 0.0 for feature in feature_names} for label in LABELS}
    intercepts = {label: 0.0 for label in LABELS}
    counts = Counter(row["label"] for row in rows)
    class_weights = {label: len(rows) / (len(LABELS) * max(1, counts.get(label, 0))) for label in LABELS}
    learning_rate = 0.05
    l2 = 0.0005
    for _ in range(120):
        for row in rows:
            features = scaled_features(row["features"], feature_names, scaler)
            probabilities = softmax({label: intercepts[label] + dot(weights[label], features) for label in LABELS})
            for label in LABELS:
                target = 1.0 if row["label"] == label else 0.0
                gradient = (probabilities[label] - target) * class_weights[row["label"]]
                intercepts[label] -= learning_rate * gradient
                for feature, value in features.items():
                    weights[label][feature] -= learning_rate * ((gradient * value) + (l2 * weights[label][feature]))
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


def train_random_forest(rows: list[dict[str, Any]], feature_names: list[str], *, tree_count: int, max_depth: int) -> dict[str, Any]:
    if not feature_names:
        majority = Counter(row["label"] for row in rows).most_common(1)[0][0]
        return {"trees": [{"label": majority}], "featureNames": feature_names}
    rng = random.Random(17)
    trees = []
    for _ in range(tree_count):
        sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        trees.append(build_tree(sample, feature_names, rng, depth=0, max_depth=max_depth))
    return {"trees": trees, "featureNames": feature_names}


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


def tune_probability_calibration(rows: list[dict[str, Any]], probability_fn) -> dict[str, float]:
    if not rows:
        return {"buyThreshold": 0.34, "sellThreshold": 0.34, "minDirectionalEdge": 0.0, "score": 0.0}
    probability_rows = [
        (probability_fn(row["features"]), row["label"])
        for row in rows
    ]
    thresholds = [0.24, 0.28, 0.32, 0.36, 0.4, 0.45, 0.5, 0.55, 0.6]
    edges = [-0.08, -0.04, 0.0, 0.04, 0.08, 0.12]
    best = {"buyThreshold": 0.34, "sellThreshold": 0.34, "minDirectionalEdge": 0.0, "score": -1.0}
    labels = [label for _, label in probability_rows]
    for buy_threshold in thresholds:
        for sell_threshold in thresholds:
            for edge in edges:
                calibration = {
                    "buyThreshold": buy_threshold,
                    "sellThreshold": sell_threshold,
                    "minDirectionalEdge": edge,
                }
                predictions = [
                    predict_calibrated_probabilities(probabilities, calibration)
                    for probabilities, _ in probability_rows
                ]
                metric = evaluate_predictions(predictions, labels)
                score = float(metric["trustScore"])
                if score > float(best["score"]):
                    best = {**calibration, "score": round(score, 4)}
    return best


def predict_calibrated_probabilities(probabilities: dict[str, float], calibration: dict[str, float]) -> str:
    buy = float(probabilities.get("BUY") or 0.0)
    sell = float(probabilities.get("SELL") or 0.0)
    hold = float(probabilities.get("HOLD") or 0.0)
    buy_threshold = float(calibration.get("buyThreshold") or 0.34)
    sell_threshold = float(calibration.get("sellThreshold") or 0.34)
    min_edge = float(calibration.get("minDirectionalEdge") or 0.0)
    if buy >= buy_threshold and buy >= sell and buy - hold >= min_edge:
        return "BUY"
    if sell >= sell_threshold and sell > buy and sell - hold >= min_edge:
        return "SELL"
    return "HOLD"


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
