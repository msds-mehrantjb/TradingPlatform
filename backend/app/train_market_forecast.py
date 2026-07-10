from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from .market_forecast import (
    DEFAULT_MAX_MODEL_DISAGREEMENT,
    DEFAULT_MIN_STOP_PCT,
    DEFAULT_MIN_TARGET_PCT,
    DEFAULT_PROFIT_TARGET_DOLLARS,
    DEFAULT_SUCCESS_THRESHOLD,
    DEFAULT_STOP_ATR_MULTIPLIER,
    DEFAULT_TARGET_ATR_MULTIPLIER,
    FORECAST_HORIZON_MINUTES,
    MICROSTRUCTURE_DIR,
    MODEL_VERSION,
    OUTCOME_LABELS,
    OUTCOME_ORDER,
    OUTCOME_STOP,
    OUTCOME_TARGET,
    OUTCOME_TIMEOUT,
    attach_microstructure_features,
    extract_market_forecast_features,
    flatten_forecast_features,
    market_forecast_artifact_path,
    normalize_candles,
)


BACKTEST_EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "backtests"
DEFAULT_SYMBOL = "SPY"
DEFAULT_PROFIT_TARGET = DEFAULT_PROFIT_TARGET_DOLLARS
DEFAULT_STOP_LOSS = 0.25
DEFAULT_MAX_ROWS = 120_000
DEFAULT_ATR_LOOKBACK_MINUTES = 5
DEFAULT_WALK_FORWARD_FOLDS = 4
DEFAULT_EMBARGO_MINUTES = FORECAST_HORIZON_MINUTES
DEFAULT_TRAINING_COST = 0.03


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the isolated 5-minute market forecast model.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--feed", default="iex")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--profit-target", type=float, default=DEFAULT_PROFIT_TARGET)
    parser.add_argument("--stop-loss", type=float, default=DEFAULT_STOP_LOSS)
    parser.add_argument("--min-target-pct", type=float, default=DEFAULT_MIN_TARGET_PCT)
    parser.add_argument("--min-stop-pct", type=float, default=DEFAULT_MIN_STOP_PCT)
    parser.add_argument("--target-atr-multiplier", type=float, default=DEFAULT_TARGET_ATR_MULTIPLIER)
    parser.add_argument("--stop-atr-multiplier", type=float, default=DEFAULT_STOP_ATR_MULTIPLIER)
    parser.add_argument("--atr-lookback-minutes", type=int, default=DEFAULT_ATR_LOOKBACK_MINUTES)
    parser.add_argument("--walk-forward-folds", type=int, default=DEFAULT_WALK_FORWARD_FOLDS)
    parser.add_argument("--embargo-minutes", type=int, default=DEFAULT_EMBARGO_MINUTES)
    parser.add_argument("--training-cost", type=float, default=DEFAULT_TRAINING_COST)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--model-kind", choices=["xgboost", "logistic"], default="xgboost")
    args = parser.parse_args()

    summary = train_market_forecast_model(
        symbol=args.symbol.upper(),
        feed=args.feed,
        start_date=args.start_date,
        end_date=args.end_date,
        profit_target=args.profit_target,
        stop_loss=args.stop_loss,
        min_target_pct=args.min_target_pct,
        min_stop_pct=args.min_stop_pct,
        target_atr_multiplier=args.target_atr_multiplier,
        stop_atr_multiplier=args.stop_atr_multiplier,
        atr_lookback_minutes=args.atr_lookback_minutes,
        walk_forward_folds=args.walk_forward_folds,
        embargo_minutes=args.embargo_minutes,
        training_cost=args.training_cost,
        max_rows=args.max_rows,
        model_kind=args.model_kind,
    )
    print(json.dumps(summary, indent=2))


def train_market_forecast_model(
    *,
    symbol: str,
    feed: str,
    start_date: str | None,
    end_date: str | None,
    profit_target: float,
    stop_loss: float,
    min_target_pct: float,
    min_stop_pct: float,
    target_atr_multiplier: float,
    stop_atr_multiplier: float,
    atr_lookback_minutes: int,
    walk_forward_folds: int,
    embargo_minutes: int,
    training_cost: float,
    max_rows: int,
    model_kind: str,
) -> dict[str, Any]:
    manifest = latest_manifest(symbol)
    candle_path = Path(str(manifest.get("files", {}).get("continuous1mJsonl") or ""))
    if not candle_path.exists():
        raise FileNotFoundError(f"1m candle file not found: {candle_path}")

    candles = filter_candles_by_date(normalize_candles(read_jsonl(candle_path)), start_date=start_date, end_date=end_date)
    microstructure_by_timestamp = load_microstructure_by_timestamp(symbol=symbol, feed=feed)
    rows = build_training_rows(
        candles,
        profit_target=profit_target,
        stop_loss=stop_loss,
        min_target_pct=min_target_pct,
        min_stop_pct=min_stop_pct,
        target_atr_multiplier=target_atr_multiplier,
        stop_atr_multiplier=stop_atr_multiplier,
        atr_lookback_minutes=atr_lookback_minutes,
        training_cost=training_cost,
        max_rows=max_rows,
        microstructure_by_timestamp=microstructure_by_timestamp,
        require_microstructure=model_kind == "xgboost",
    )
    if len(rows) < 1_000:
        raise ValueError(f"Need at least 1,000 labeled rows; found {len(rows)}")

    walk_forward = walk_forward_validate(
        rows,
        requested_folds=walk_forward_folds,
        embargo_minutes=embargo_minutes,
        model_kind=model_kind,
        symbol=symbol,
    )
    deployment_split_index = max(1, int(len(rows) * 0.8))
    train_rows = rows[:deployment_split_index]
    test_rows = rows[deployment_split_index:]
    feature_names = sorted({key for row in train_rows for key in row["features"]})
    model_file: str | None = None
    means: dict[str, float] = {}
    scales: dict[str, float] = {}

    if model_kind == "xgboost":
        model, train_metrics, test_metrics = train_xgboost_model(train_rows, test_rows, feature_names, symbol=symbol)
        model_file = model["modelFile"]
        raw_train_probabilities = xgboost_saved_probabilities(train_rows, feature_names, model_file)
        raw_test_probabilities = xgboost_saved_probabilities(test_rows, feature_names, model_file)
    else:
        means, scales = feature_stats(train_rows, feature_names)
        model = train_logistic_model(train_rows, feature_names, means, scales)
        raw_train_probabilities = [
            score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales)
            for row in train_rows
        ]
        raw_test_probabilities = [
            score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales)
            for row in test_rows
        ]
        train_metrics = evaluate_outcome_probabilities(zip(raw_train_probabilities, labels(train_rows), row_economics(train_rows)))
        test_metrics = evaluate_outcome_probabilities(zip(raw_test_probabilities, labels(test_rows), row_economics(test_rows)))

    calibration = fit_probability_calibration(raw_train_probabilities, labels(train_rows))
    calibrated_train_probabilities = [apply_probability_calibration(probabilities, calibration) for probabilities in raw_train_probabilities]
    calibrated_test_probabilities = [apply_probability_calibration(probabilities, calibration) for probabilities in raw_test_probabilities]
    uncertainty_ensemble = train_uncertainty_ensemble(train_rows, feature_names)
    ensemble_train_probabilities = combine_member_probabilities(
        calibrated_train_probabilities,
        [
            member_probabilities(train_rows, member)
            for member in uncertainty_ensemble["members"]
        ],
    )
    ensemble_test_probabilities = combine_member_probabilities(
        calibrated_test_probabilities,
        [
            member_probabilities(test_rows, member)
            for member in uncertainty_ensemble["members"]
        ],
    )
    raw_train_metrics = train_metrics
    raw_test_metrics = test_metrics
    calibrated_train_metrics = evaluate_outcome_probabilities(zip(calibrated_train_probabilities, labels(train_rows), row_economics(train_rows)))
    calibrated_test_metrics = evaluate_outcome_probabilities(zip(calibrated_test_probabilities, labels(test_rows), row_economics(test_rows)))
    train_metrics = evaluate_outcome_probabilities(zip(ensemble_train_probabilities, labels(train_rows), row_economics(train_rows)))
    test_metrics = evaluate_outcome_probabilities(zip(ensemble_test_probabilities, labels(test_rows), row_economics(test_rows)))
    optimized_probability_threshold = max(DEFAULT_SUCCESS_THRESHOLD, float(test_metrics.get("evOptimizedThreshold") or DEFAULT_SUCCESS_THRESHOLD))

    artifact = {
        "version": MODEL_VERSION,
        "modelKind": model_kind if model_kind == "xgboost" else "sparse-softmax-baseline",
        "trainedAt": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "feed": feed,
        "sourceManifest": manifest.get("manifest"),
        "sourceCandles": str(candle_path),
        "sourceMicrostructure": str(MICROSTRUCTURE_DIR / symbol.upper() / feed),
        "dateRange": {"startDate": start_date, "endDate": end_date},
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "label": {
            "name": "trade_decision_triple_barrier_5m",
            "profitTargetDollars": profit_target,
            "stopLossDollars": stop_loss,
            "minTargetPct": min_target_pct,
            "minStopPct": min_stop_pct,
            "targetAtrMultiplier": target_atr_multiplier,
            "stopAtrMultiplier": stop_atr_multiplier,
            "atrLookbackMinutes": atr_lookback_minutes,
            "trainingCostDollars": training_cost,
            "labels": OUTCOME_LABELS,
            "decisionLabels": {
                "buy_success": 1,
                "sell_success": -1,
                "no_trade": 0,
            },
            "rule": "+1 when the volatility-adjusted upper barrier is hit first; -1 when the volatility-adjusted lower barrier is hit first; 0 when neither barrier hits within the next 5 one-minute bars. Distances use max(fixed dollar floor, entry price percent floor, recent ATR times multiplier). The serving layer converts weak or close Buy/Sell probabilities into no_trade.",
        },
        "threshold": round(optimized_probability_threshold, 4),
        "validationPolicy": {
            "method": "walk_forward_purged_embargo",
            "requestedFolds": walk_forward_folds,
            "embargoMinutes": embargo_minutes,
            "labelHorizonMinutes": FORECAST_HORIZON_MINUTES,
            "note": "Each validation fold is later in time than its training fold; training observations whose label interval overlaps or falls inside the embargo window are removed.",
        },
        "regimePolicy": {
            "mode": "regime_as_features_with_serving_gates",
            "features": [
                "strong_uptrend",
                "weak_uptrend",
                "strong_downtrend",
                "weak_downtrend",
                "sideways",
                "low_volatility",
                "normal_volatility",
                "high_volatility",
                "opening_session",
                "midday_session",
                "power_hour_session",
            ],
        },
        "featurePolicy": {
            "mode": "noise_resistant_normalized_features",
            "normalization": "Returns, VWAP/EMA distances, candle range, spread, and time context are normalized by ATR, rolling averages, or cyclic encodings where possible.",
            "groups": [
                "trend",
                "mean_reversion",
                "volatility",
                "volume",
                "microstructure",
                "time",
                "regime",
                "algorithm",
            ],
        },
        "algorithmFeaturePolicy": {
            "mode": "strategy_outputs_as_trade_quality_features",
            "algorithms": [
                "RSI Mean Reversion",
                "Breakout Strategy",
                "MACD Strategy",
            ],
            "features": [
                "algorithm_n_signal",
                "algorithm_n_confidence",
                "weighted_buy_score",
                "weighted_sell_score",
                "weighted_hold_score",
                "buy_minus_sell_score",
                "winner_score_margin",
                "algorithm_disagreement",
            ],
        },
        "calibrationPolicy": {
            "method": "per_class_platt_sigmoid",
            "diagnostics": ["brier", "classBrier", "calibrationCurve"],
            "note": "Raw model probabilities are sigmoid-calibrated per outcome class, then renormalized before serving decisions and position sizing.",
        },
        "optimizationPolicy": {
            "objective": "expected_value",
            "formula": "P(success) * target_profit - P(adverse_stop) * stop_loss - spread_slippage_cost",
            "minimumExpectedValue": 0,
            "thresholdSource": "test_ev_optimized_threshold",
            "defaultMinimumProbability": DEFAULT_SUCCESS_THRESHOLD,
        },
        "uncertaintyPolicy": {
            "method": "primary_model_plus_calibrated_logistic_ensemble",
            "memberCount": 1 + len(uncertainty_ensemble["members"]),
            "maxModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
            "rule": "No trade when top-side final probability is below threshold, model disagreement exceeds 0.10, or expected value is non-positive.",
        },
        "trainingRows": len(train_rows),
        "testRows": len(test_rows),
        "positiveRate": round(label_rate(rows, 1), 4),
        "outcomeRates": outcome_rates(rows),
        "featureNames": feature_names,
        "modelFile": model_file,
        "featureMeans": means,
        "featureScales": scales,
        "intercept": model.get("intercept"),
        "intercepts": model.get("intercepts"),
        "weights": model.get("weights"),
        "weightsByClass": model.get("weightsByClass"),
        "calibration": calibration,
        "uncertaintyEnsemble": uncertainty_ensemble,
        "metrics": {
            "train": train_metrics,
            "test": test_metrics,
            "rawTrain": raw_train_metrics,
            "rawTest": raw_test_metrics,
            "calibratedTrain": calibrated_train_metrics,
            "calibratedTest": calibrated_test_metrics,
            "accuracy": test_metrics["accuracy"],
            "auc": test_metrics["auc"],
            "brier": test_metrics["brier"],
            "walkForward": walk_forward["summary"],
        },
        "walkForwardValidation": walk_forward,
        "topFeatures": model.get("topFeatures") or top_softmax_weights(model.get("weightsByClass") or {}, limit=20),
    }
    path = market_forecast_artifact_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return {
        "status": "trained",
        "artifact": str(path),
        "symbol": symbol,
        "rows": len(rows),
        "trainingRows": len(train_rows),
        "testRows": len(test_rows),
        "positiveRate": artifact["positiveRate"],
        "outcomeRates": artifact["outcomeRates"],
        "test": test_metrics,
        "walkForward": walk_forward["summary"],
        "topFeatures": artifact["topFeatures"][:8],
    }


def latest_manifest(symbol: str) -> dict[str, Any]:
    root = BACKTEST_EXPORT_DIR / symbol.upper()
    if not root.exists():
        raise FileNotFoundError(f"No backtest folder found for {symbol}: {root}")
    candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    for run in root.iterdir():
        manifest_path = run / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        manifest["manifest"] = str(manifest_path)
        coverage = manifest.get("coverage") or {}
        one_minute_count = int((coverage.get("oneMinute") or {}).get("count") or 0)
        candidates.append((one_minute_count, str(manifest.get("requestedEndDate") or run.name), manifest_path, manifest))
    if not candidates:
        raise FileNotFoundError(f"No manifest files found under {root}")
    return sorted(candidates, key=lambda item: (item[0], item[1], str(item[2])), reverse=True)[0][3]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def filter_candles_by_date(candles: list[dict[str, Any]], *, start_date: str | None, end_date: str | None) -> list[dict[str, Any]]:
    if not start_date and not end_date:
        return candles
    filtered: list[dict[str, Any]] = []
    for candle in candles:
        day = str(candle.get("timestamp") or "")[:10]
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        filtered.append(candle)
    return filtered


def load_microstructure_by_timestamp(*, symbol: str, feed: str) -> dict[str, dict[str, Any]]:
    root = MICROSTRUCTURE_DIR / symbol.upper() / feed
    rows: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return rows
    for path in sorted(root.glob("*/one_minute_microstructure.jsonl")):
        for row in read_jsonl(path):
            timestamp = str(row.get("timestamp") or "")
            if timestamp:
                rows[timestamp] = row
    return rows


def walk_forward_validate(
    rows: list[dict[str, Any]],
    *,
    requested_folds: int,
    embargo_minutes: int,
    model_kind: str,
    symbol: str,
) -> dict[str, Any]:
    folds = walk_forward_folds(rows, requested_folds=max(1, requested_folds), embargo_minutes=max(0, embargo_minutes))
    results: list[dict[str, Any]] = []
    for fold in folds:
        train_rows = fold["trainRows"]
        validation_rows = fold["validationRows"]
        if len(train_rows) < 200 or len(validation_rows) < 50:
            results.append({**fold_metadata(fold), "status": "skipped_insufficient_rows"})
            continue
        feature_names = sorted({key for row in train_rows for key in row["features"]})
        metrics = train_and_score_validation_fold(train_rows, validation_rows, feature_names)
        results.append({**fold_metadata(fold), "status": "validated", "metrics": metrics})
    validated = [result for result in results if result.get("status") == "validated"]
    return {
        "method": "walk_forward_purged_embargo",
        "modelKind": model_kind,
        "symbol": symbol,
        "embargoMinutes": embargo_minutes,
        "folds": results,
        "summary": walk_forward_summary(validated),
    }


def walk_forward_folds(rows: list[dict[str, Any]], *, requested_folds: int, embargo_minutes: int) -> list[dict[str, Any]]:
    total = len(rows)
    fold_count = max(1, min(requested_folds, 8))
    validation_size = max(50, total // (fold_count + 2))
    folds: list[dict[str, Any]] = []
    for fold_index in range(fold_count):
        validation_start = total - (fold_count - fold_index) * validation_size
        validation_end = validation_start + validation_size
        if validation_start <= validation_size or validation_end > total:
            continue
        validation_rows = rows[validation_start:validation_end]
        validation_start_time = row_event_start_minutes(validation_rows[0])
        validation_end_time = row_event_end_minutes(validation_rows[-1])
        train_rows = [
            row
            for row in rows[:validation_start]
            if row_event_end_minutes(row) < validation_start_time - embargo_minutes
        ]
        folds.append(
            {
                "fold": fold_index + 1,
                "trainRows": train_rows,
                "validationRows": validation_rows,
                "validationStart": validation_rows[0]["timestamp"],
                "validationEnd": validation_rows[-1]["timestamp"],
                "purgedRows": validation_start - len(train_rows),
            }
        )
    return folds


def train_and_score_validation_fold(train_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    means, scales = feature_stats(train_rows, feature_names)
    model = train_logistic_model(train_rows, feature_names, means, scales, epochs=24, learning_rate=0.03)
    raw_train = [
        score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales)
        for row in train_rows
    ]
    calibration = fit_probability_calibration(raw_train, labels(train_rows))
    validation_probabilities = [
        apply_probability_calibration(
            score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], feature_names, means, scales),
            calibration,
        )
        for row in validation_rows
    ]
    return evaluate_outcome_probabilities(zip(validation_probabilities, labels(validation_rows), row_economics(validation_rows)))


def fold_metadata(fold: dict[str, Any]) -> dict[str, Any]:
    return {
        "fold": fold["fold"],
        "trainingRows": len(fold["trainRows"]),
        "validationRows": len(fold["validationRows"]),
        "validationStart": fold["validationStart"],
        "validationEnd": fold["validationEnd"],
        "purgedRows": fold["purgedRows"],
    }


def walk_forward_summary(validated: list[dict[str, Any]]) -> dict[str, Any]:
    if not validated:
        return {"folds": 0, "status": "no_validated_folds"}
    metric_names = ["accuracy", "auc", "brier", "targetPrecisionAtThreshold", "targetRecallAtThreshold", "selectedRateAtThreshold"]
    summary: dict[str, Any] = {"folds": len(validated), "status": "validated"}
    for name in metric_names:
        values = [float(result["metrics"].get(name, 0)) for result in validated]
        summary[name] = round(mean(values), 6)
        summary[f"{name}Std"] = round(pstdev(values), 6) if len(values) > 1 else 0.0
    optimized_thresholds = [float(result["metrics"].get("evOptimizedThreshold") or DEFAULT_SUCCESS_THRESHOLD) for result in validated]
    selected_ev = [float((result["metrics"].get("expectedValue") or {}).get("totalEvSelected") or 0.0) for result in validated]
    selected_ev_per_trade = [float((result["metrics"].get("expectedValue") or {}).get("averageEvSelected") or 0.0) for result in validated]
    summary["evOptimizedThreshold"] = round(mean(optimized_thresholds), 4)
    summary["totalEvSelected"] = round(sum(selected_ev), 6)
    summary["averageEvSelected"] = round(mean(selected_ev_per_trade), 6)
    return summary


def row_event_start_minutes(row: dict[str, Any]) -> float:
    timestamp = parse_row_timestamp(str(row.get("labelStart") or row.get("timestamp") or ""))
    return timestamp.timestamp() / 60


def row_event_end_minutes(row: dict[str, Any]) -> float:
    timestamp = parse_row_timestamp(str(row.get("labelEnd") or ""))
    if timestamp != datetime.min:
        return timestamp.timestamp() / 60
    return row_event_start_minutes(row) + FORECAST_HORIZON_MINUTES


def parse_row_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.min


def build_training_rows(
    candles: list[dict[str, Any]],
    *,
    profit_target: float,
    stop_loss: float,
    min_target_pct: float,
    min_stop_pct: float,
    target_atr_multiplier: float,
    stop_atr_multiplier: float,
    atr_lookback_minutes: int,
    training_cost: float,
    max_rows: int,
    microstructure_by_timestamp: dict[str, dict[str, Any]] | None = None,
    require_microstructure: bool = False,
) -> list[dict[str, Any]]:
    start_index = 60
    end_index = len(candles) - FORECAST_HORIZON_MINUTES - 1
    if end_index <= start_index:
        return []
    stride = max(1, math.ceil((end_index - start_index) / max(1, max_rows)))
    rows: list[dict[str, Any]] = []
    for index in range(start_index, end_index, stride):
        window = candles[index - 59 : index + 1]
        microstructure = (microstructure_by_timestamp or {}).get(str(candles[index]["timestamp"]))
        if require_microstructure and not microstructure:
            continue
        barriers = volatility_adjusted_label_barriers(
            candles,
            index,
            profit_target=profit_target,
            stop_loss=stop_loss,
            min_target_pct=min_target_pct,
            min_stop_pct=min_stop_pct,
            target_atr_multiplier=target_atr_multiplier,
            stop_atr_multiplier=stop_atr_multiplier,
            atr_lookback_minutes=atr_lookback_minutes,
        )
        label = future_trade_outcome_label(
            candles,
            index,
            profit_target=profit_target,
            stop_loss=stop_loss,
            min_target_pct=min_target_pct,
            min_stop_pct=min_stop_pct,
            target_atr_multiplier=target_atr_multiplier,
            stop_atr_multiplier=stop_atr_multiplier,
            atr_lookback_minutes=atr_lookback_minutes,
        )
        feature_groups = attach_microstructure_features(extract_market_forecast_features(window), microstructure)
        features = flatten_forecast_features(feature_groups)
        rows.append(
            {
                "timestamp": candles[index]["timestamp"],
                "labelStart": candles[index]["timestamp"],
                "labelEnd": candles[min(len(candles) - 1, index + FORECAST_HORIZON_MINUTES)]["timestamp"],
                "targetProfit": barriers["targetDistance"],
                "stopLoss": barriers["stopDistance"],
                "tradingCost": max(0.0, training_cost),
                "features": features,
                "target": label,
            }
        )
    return rows


def future_trade_outcome_label(
    candles: list[dict[str, Any]],
    index: int,
    *,
    profit_target: float,
    stop_loss: float,
    min_target_pct: float = DEFAULT_MIN_TARGET_PCT,
    min_stop_pct: float = DEFAULT_MIN_STOP_PCT,
    target_atr_multiplier: float = DEFAULT_TARGET_ATR_MULTIPLIER,
    stop_atr_multiplier: float = DEFAULT_STOP_ATR_MULTIPLIER,
    atr_lookback_minutes: int = DEFAULT_ATR_LOOKBACK_MINUTES,
) -> int:
    entry = float(candles[index]["close"])
    barriers = volatility_adjusted_label_barriers(
        candles,
        index,
        profit_target=profit_target,
        stop_loss=stop_loss,
        min_target_pct=min_target_pct,
        min_stop_pct=min_stop_pct,
        target_atr_multiplier=target_atr_multiplier,
        stop_atr_multiplier=stop_atr_multiplier,
        atr_lookback_minutes=atr_lookback_minutes,
    )
    target = entry + barriers["targetDistance"]
    stop = entry - barriers["stopDistance"]
    for future in candles[index + 1 : index + 1 + FORECAST_HORIZON_MINUTES]:
        hit_target = float(future["high"]) >= target
        hit_stop = float(future["low"]) <= stop
        if hit_target and hit_stop:
            return 1 if float(future["close"]) >= entry else -1
        if hit_target:
            return 1
        if hit_stop:
            return -1
    return 0


def future_target_before_stop_label(candles: list[dict[str, Any]], index: int, *, profit_target: float, stop_loss: float) -> int:
    return 1 if future_trade_outcome_label(candles, index, profit_target=profit_target, stop_loss=stop_loss) == 1 else 0


def volatility_adjusted_label_barriers(
    candles: list[dict[str, Any]],
    index: int,
    *,
    profit_target: float,
    stop_loss: float,
    min_target_pct: float,
    min_stop_pct: float,
    target_atr_multiplier: float,
    stop_atr_multiplier: float,
    atr_lookback_minutes: int,
) -> dict[str, float]:
    entry = float(candles[index]["close"])
    atr_value = recent_average_true_range(candles, index, max(1, atr_lookback_minutes))
    return {
        "targetDistance": max(float(profit_target), entry * max(0.0, min_target_pct), atr_value * max(0.0, target_atr_multiplier)),
        "stopDistance": max(float(stop_loss), entry * max(0.0, min_stop_pct), atr_value * max(0.0, stop_atr_multiplier)),
    }


def recent_average_true_range(candles: list[dict[str, Any]], index: int, period: int) -> float:
    start = max(1, index - period + 1)
    ranges: list[float] = []
    for cursor in range(start, index + 1):
        current = candles[cursor]
        previous_close = float(candles[cursor - 1]["close"])
        ranges.append(
            max(
                float(current["high"]) - float(current["low"]),
                abs(float(current["high"]) - previous_close),
                abs(float(current["low"]) - previous_close),
            )
        )
    return mean(ranges) if ranges else 0.01


def feature_stats(rows: list[dict[str, Any]], feature_names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in feature_names:
        values = [float(row["features"].get(name, 0)) for row in rows]
        avg = mean(values)
        scale = pstdev(values) if len(values) > 1 else 1
        means[name] = round(avg, 10)
        scales[name] = round(scale if scale > 0 else 1, 10)
    return means, scales


def train_xgboost_model(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    symbol: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise RuntimeError("xgboost is not installed in the backend Python environment") from exc

    train_matrix = xgb.DMatrix(feature_matrix(train_rows, feature_names), label=xgboost_labels(train_rows), feature_names=feature_names)
    test_matrix = xgb.DMatrix(feature_matrix(test_rows, feature_names), label=xgboost_labels(test_rows), feature_names=feature_names)
    params = {
        "objective": "multi:softprob",
        "num_class": 3,
        "eval_metric": "mlogloss",
        "tree_method": "hist",
        "max_depth": 4,
        "eta": 0.045,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "min_child_weight": 8,
        "lambda": 1.25,
        "alpha": 0.05,
        "seed": 42,
        "nthread": 4,
    }
    booster = xgb.train(
        params,
        train_matrix,
        num_boost_round=180,
        evals=[(test_matrix, "test")],
        verbose_eval=False,
    )
    model_file = market_forecast_artifact_path(symbol).with_suffix(".xgboost.json")
    model_file.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_file))
    train_metrics = evaluate_outcome_probabilities(zip(map(outcome_probabilities_from_xgboost, booster.predict(train_matrix)), labels(train_rows), row_economics(train_rows)))
    test_metrics = evaluate_outcome_probabilities(zip(map(outcome_probabilities_from_xgboost, booster.predict(test_matrix)), labels(test_rows), row_economics(test_rows)))
    return (
        {
            "modelFile": str(model_file),
            "topFeatures": top_xgboost_features(booster, limit=20),
        },
        train_metrics,
        test_metrics,
    )


def xgboost_saved_probabilities(rows: list[dict[str, Any]], feature_names: list[str], model_file: str) -> list[dict[str, float]]:
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise RuntimeError("xgboost is not installed in the backend Python environment") from exc
    booster = xgb.Booster()
    booster.load_model(model_file)
    matrix = xgb.DMatrix(feature_matrix(rows, feature_names), feature_names=feature_names)
    return [outcome_probabilities_from_xgboost(raw) for raw in booster.predict(matrix)]


def feature_matrix(rows: list[dict[str, Any]], feature_names: list[str]) -> list[list[float]]:
    return [[float(row["features"].get(name, 0.0)) for name in feature_names] for row in rows]


def labels(rows: list[dict[str, Any]]) -> list[int]:
    return [int(row["target"]) for row in rows]


def row_economics(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    return [
        {
            "targetProfit": float(row.get("targetProfit", 1.0)),
            "stopLoss": float(row.get("stopLoss", 1.0)),
            "tradingCost": float(row.get("tradingCost", 0.0)),
        }
        for row in rows
    ]


def xgboost_labels(rows: list[dict[str, Any]]) -> list[int]:
    return [OUTCOME_ORDER.index(outcome_name(int(row["target"]))) for row in rows]


def outcome_name(label: int) -> str:
    for name, value in OUTCOME_LABELS.items():
        if value == label:
            return name
    return OUTCOME_TIMEOUT


def outcome_probabilities_from_xgboost(raw: Any) -> dict[str, float]:
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    values = list(raw) if isinstance(raw, (list, tuple)) else [float(raw)]
    if len(values) < len(OUTCOME_ORDER):
        target_probability = max(0.0, min(1.0, float(values[0])))
        remaining = 1 - target_probability
        return {OUTCOME_STOP: remaining * 0.5, OUTCOME_TIMEOUT: remaining * 0.5, OUTCOME_TARGET: target_probability}
    probabilities = {OUTCOME_ORDER[index]: max(0.0, min(1.0, float(values[index]))) for index in range(len(OUTCOME_ORDER))}
    total = sum(probabilities.values()) or 1
    return {name: value / total for name, value in probabilities.items()}


def outcome_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = max(1, len(rows))
    return {
        name: round(sum(1 for row in rows if int(row["target"]) == label) / total, 4)
        for name, label in OUTCOME_LABELS.items()
    }


def label_rate(rows: list[dict[str, Any]], label: int) -> float:
    return sum(1 for row in rows if int(row["target"]) == label) / max(1, len(rows))


def train_logistic_model(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
    *,
    epochs: int = 35,
    learning_rate: float = 0.035,
    l2: float = 0.0005,
) -> dict[str, Any]:
    weights_by_class: dict[str, dict[str, float]] = {name: defaultdict(float) for name in OUTCOME_ORDER}
    intercepts: dict[str, float] = {name: 0.0 for name in OUTCOME_ORDER}
    class_counts = {name: max(1, sum(1 for row in rows if outcome_name(int(row["target"])) == name)) for name in OUTCOME_ORDER}
    class_weights = {name: len(rows) / (len(OUTCOME_ORDER) * count) for name, count in class_counts.items()}
    for _ in range(epochs):
        for row in rows:
            target_name = outcome_name(int(row["target"]))
            probabilities = score_probabilities(row["features"], weights_by_class, intercepts, feature_names, means, scales)
            row_weight = class_weights[target_name]
            for class_name in OUTCOME_ORDER:
                gradient = (probabilities[class_name] - (1.0 if class_name == target_name else 0.0)) * row_weight
                intercepts[class_name] -= learning_rate * gradient
                for name in feature_names:
                    value = normalized_feature(row["features"], name, means, scales)
                    weights_by_class[class_name][name] -= learning_rate * ((gradient * value) + (l2 * weights_by_class[class_name][name]))
    return {
        "intercepts": {name: round(value, 10) for name, value in intercepts.items()},
        "weightsByClass": {
            class_name: {name: round(value, 10) for name, value in weights.items() if abs(value) >= 0.000001}
            for class_name, weights in weights_by_class.items()
        },
    }


def train_uncertainty_ensemble(train_rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    specs = [
        ("logistic_full", train_rows, feature_names, 28, 0.03),
        ("logistic_even_rows", train_rows[::2] or train_rows, feature_names, 24, 0.028),
        ("logistic_odd_rows", train_rows[1::2] or train_rows, feature_names, 24, 0.032),
        ("logistic_core_features", train_rows, core_uncertainty_feature_names(feature_names), 26, 0.03),
    ]
    members: list[dict[str, Any]] = []
    for name, rows, names, epochs, learning_rate in specs:
        if len(rows) < 50 or not names:
            continue
        means, scales = feature_stats(rows, names)
        model = train_logistic_model(rows, names, means, scales, epochs=epochs, learning_rate=learning_rate)
        raw_probabilities = [
            score_probabilities(row["features"], model["weightsByClass"], model["intercepts"], names, means, scales)
            for row in rows
        ]
        calibration = fit_probability_calibration(raw_probabilities, labels(rows))
        members.append(
            {
                "name": name,
                "kind": "sparse-softmax-ensemble",
                "featureNames": names,
                "featureMeans": means,
                "featureScales": scales,
                "intercepts": model["intercepts"],
                "weightsByClass": model["weightsByClass"],
                "calibration": calibration,
            }
        )
    return {
        "method": "calibrated_probability_ensemble",
        "maxModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
        "members": members,
    }


def core_uncertainty_feature_names(feature_names: list[str]) -> list[str]:
    prefixes = (
        "price.",
        "trend.",
        "mean_reversion.",
        "volatility.",
        "volume.",
        "algorithm.",
        "regime.",
        "time.",
    )
    return [name for name in feature_names if name == "bias" or name.startswith(prefixes)]


def member_probabilities(rows: list[dict[str, Any]], member: dict[str, Any]) -> list[dict[str, float]]:
    names = [str(name) for name in member.get("featureNames") or []]
    probabilities = [
        score_probabilities(row["features"], member["weightsByClass"], member["intercepts"], names, member["featureMeans"], member["featureScales"])
        for row in rows
    ]
    return [apply_probability_calibration(probability, member["calibration"]) for probability in probabilities]


def combine_member_probabilities(primary: list[dict[str, float]], member_sets: list[list[dict[str, float]]]) -> list[dict[str, float]]:
    combined: list[dict[str, float]] = []
    for index, probability in enumerate(primary):
        rows = [probability]
        rows.extend(member_set[index] for member_set in member_sets if index < len(member_set))
        combined.append(
            {
                outcome: sum(float(row[outcome]) for row in rows) / len(rows)
                for outcome in OUTCOME_ORDER
            }
        )
    return combined


def evaluate_model(
    rows: list[dict[str, Any]],
    model: dict[str, Any],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
) -> dict[str, Any]:
    weights_by_class = model["weightsByClass"]
    intercepts = model["intercepts"]
    return evaluate_outcome_probabilities(
        (score_probabilities(row["features"], weights_by_class, intercepts, feature_names, means, scales), int(row["target"]), row_economics([row])[0])
        for row in rows
    )


def evaluate_outcome_probabilities(scored_iterable: Any) -> dict[str, Any]:
    scored = [normalize_scored_item(item) for item in scored_iterable]
    threshold = DEFAULT_SUCCESS_THRESHOLD
    tp = sum(1 for probabilities, target, _ in scored if probabilities[OUTCOME_TARGET] >= threshold and target == 1)
    fp = sum(1 for probabilities, target, _ in scored if probabilities[OUTCOME_TARGET] >= threshold and target != 1)
    tn = sum(1 for probabilities, target, _ in scored if probabilities[OUTCOME_TARGET] < threshold and target != 1)
    fn = sum(1 for probabilities, target, _ in scored if probabilities[OUTCOME_TARGET] < threshold and target == 1)
    total = max(1, len(scored))
    correct = sum(1 for probabilities, target, _ in scored if OUTCOME_LABELS[max(probabilities.items(), key=lambda item: item[1])[0]] == target)
    ev_at_threshold = expected_value_metrics(scored, threshold=threshold)
    ev_optimized = optimize_expected_value_threshold(scored)
    return {
        "rows": len(scored),
        "positiveRate": round(sum(1 for _, target, _ in scored if target == 1) / total, 4),
        "outcomeRates": outcome_rates([{"target": target} for _, target, _ in scored]),
        "accuracy": round(correct / total, 4),
        "targetPrecisionAtThreshold": round(tp / max(1, tp + fp), 4),
        "targetRecallAtThreshold": round(tp / max(1, tp + fn), 4),
        "selectedRateAtThreshold": round((tp + fp) / total, 4),
        "auc": round(auc([(probabilities[OUTCOME_TARGET], 1 if target == 1 else 0) for probabilities, target, _ in scored]), 4),
        "brier": round(multiclass_brier_score(scored), 6),
        "classBrier": class_brier_scores(scored),
        "calibrationCurve": calibration_curves(scored),
        "expectedValue": ev_at_threshold,
        "evOptimizedThreshold": ev_optimized["threshold"],
        "evOptimized": ev_optimized,
    }


def evaluate_probabilities(scored_iterable: Any) -> dict[str, Any]:
    return evaluate_outcome_probabilities(
        ({OUTCOME_STOP: (1 - probability) * 0.5, OUTCOME_TIMEOUT: (1 - probability) * 0.5, OUTCOME_TARGET: probability}, target)
        for probability, target in scored_iterable
    )


def normalize_scored_item(item: Any) -> tuple[dict[str, float], int, dict[str, float]]:
    probabilities = item[0]
    target = int(item[1])
    economics = item[2] if len(item) > 2 else {}
    return (
        probabilities,
        target,
        {
            "targetProfit": float(economics.get("targetProfit", 1.0)),
            "stopLoss": float(economics.get("stopLoss", 1.0)),
            "tradingCost": float(economics.get("tradingCost", 0.0)),
        },
    )


def expected_value_for_candidate(probabilities: dict[str, float], economics: dict[str, float]) -> tuple[str, float, float]:
    buy_probability = float(probabilities[OUTCOME_TARGET])
    sell_probability = float(probabilities[OUTCOME_STOP])
    target_profit = float(economics.get("targetProfit", 1.0))
    stop_loss = float(economics.get("stopLoss", 1.0))
    trading_cost = float(economics.get("tradingCost", 0.0))
    if buy_probability >= sell_probability:
        confidence = buy_probability
        ev = (buy_probability * target_profit) - (sell_probability * stop_loss) - trading_cost
        return "buy", confidence, ev
    confidence = sell_probability
    ev = (sell_probability * target_profit) - (buy_probability * stop_loss) - trading_cost
    return "sell", confidence, ev


def expected_value_metrics(scored: list[tuple[dict[str, float], int, dict[str, float]]], *, threshold: float) -> dict[str, Any]:
    selected = []
    all_values = []
    for probabilities, _, economics in scored:
        _, confidence, ev = expected_value_for_candidate(probabilities, economics)
        all_values.append(ev)
        if confidence >= threshold and ev > 0:
            selected.append(ev)
    return {
        "threshold": round(threshold, 4),
        "selectedTrades": len(selected),
        "selectedRate": round(len(selected) / max(1, len(scored)), 4),
        "averageEvAllRows": round(mean(all_values), 6) if all_values else 0.0,
        "averageEvSelected": round(mean(selected), 6) if selected else 0.0,
        "totalEvSelected": round(sum(selected), 6),
    }


def optimize_expected_value_threshold(scored: list[tuple[dict[str, float], int, dict[str, float]]]) -> dict[str, Any]:
    candidates = [round(0.45 + (index * 0.01), 2) for index in range(41)]
    metrics = [expected_value_metrics(scored, threshold=threshold) for threshold in candidates]
    best = max(metrics, key=lambda item: (float(item["totalEvSelected"]), float(item["averageEvSelected"]), -float(item["threshold"])))
    return {
        **best,
        "thresholdGrid": metrics,
    }


def score_probabilities(
    features: dict[str, float],
    weights_by_class: dict[str, dict[str, float]],
    intercepts: dict[str, float],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for class_name in OUTCOME_ORDER:
        score = float(intercepts.get(class_name, 0.0))
        weights = weights_by_class.get(class_name) or {}
        for name in feature_names:
            score += float(weights.get(name, 0)) * normalized_feature(features, name, means, scales)
        scores[class_name] = score
    max_score = max(scores.values()) if scores else 0
    exp_scores = {name: math.exp(max(-30, min(30, score - max_score))) for name, score in scores.items()}
    total = sum(exp_scores.values()) or 1
    return {name: exp_scores.get(name, 0) / total for name in OUTCOME_ORDER}


def fit_probability_calibration(probabilities: list[dict[str, float]], targets: list[int]) -> dict[str, Any]:
    return {
        "method": "per_class_platt_sigmoid",
        "classes": {
            outcome: fit_platt_sigmoid(
                [float(row[outcome]) for row in probabilities],
                [1 if target == OUTCOME_LABELS[outcome] else 0 for target in targets],
            )
            for outcome in OUTCOME_ORDER
        },
    }


def fit_platt_sigmoid(probabilities: list[float], labels: list[int], *, epochs: int = 400, learning_rate: float = 0.05, l2: float = 0.001) -> dict[str, float]:
    if not probabilities or len(set(labels)) < 2:
        base_rate = sum(labels) / max(1, len(labels))
        return {"slope": 1.0, "intercept": logit(clamp_probability(base_rate))}
    slope = 1.0
    intercept = 0.0
    for _ in range(epochs):
        slope_gradient = 0.0
        intercept_gradient = 0.0
        for probability, label in zip(probabilities, labels):
            x = logit(clamp_probability(probability))
            calibrated = 1 / (1 + math.exp(-max(-30, min(30, (slope * x) + intercept))))
            error = calibrated - label
            slope_gradient += error * x
            intercept_gradient += error
        count = max(1, len(probabilities))
        slope -= learning_rate * ((slope_gradient / count) + (l2 * slope))
        intercept -= learning_rate * (intercept_gradient / count)
        slope = max(-8.0, min(8.0, slope))
        intercept = max(-8.0, min(8.0, intercept))
    return {"slope": round(slope, 8), "intercept": round(intercept, 8)}


def apply_probability_calibration(probabilities: dict[str, float], calibration: dict[str, Any]) -> dict[str, float]:
    classes = calibration.get("classes") or {}
    calibrated: dict[str, float] = {}
    for outcome in OUTCOME_ORDER:
        params = classes.get(outcome) or {}
        slope = float(params.get("slope", 1.0))
        intercept = float(params.get("intercept", 0.0))
        z = (slope * logit(clamp_probability(float(probabilities[outcome])))) + intercept
        calibrated[outcome] = 1 / (1 + math.exp(-max(-30, min(30, z))))
    total = sum(calibrated.values()) or 1.0
    return {outcome: calibrated[outcome] / total for outcome in OUTCOME_ORDER}


def multiclass_brier_score(scored: list[tuple[dict[str, float], int, dict[str, float]]]) -> float:
    if not scored:
        return 0.0
    total = 0.0
    for probabilities, target, _ in scored:
        for outcome in OUTCOME_ORDER:
            expected = 1.0 if target == OUTCOME_LABELS[outcome] else 0.0
            total += (float(probabilities[outcome]) - expected) ** 2
    return total / len(scored)


def class_brier_scores(scored: list[tuple[dict[str, float], int, dict[str, float]]]) -> dict[str, float]:
    if not scored:
        return {outcome: 0.0 for outcome in OUTCOME_ORDER}
    return {
        outcome: round(
            sum((float(probabilities[outcome]) - (1.0 if target == OUTCOME_LABELS[outcome] else 0.0)) ** 2 for probabilities, target, _ in scored)
            / len(scored),
            6,
        )
        for outcome in OUTCOME_ORDER
    }


def calibration_curves(scored: list[tuple[dict[str, float], int, dict[str, float]]], *, bins: int = 10) -> dict[str, list[dict[str, float | int]]]:
    curves: dict[str, list[dict[str, float | int]]] = {}
    for outcome in OUTCOME_ORDER:
        buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
        for probabilities, target, _ in scored:
            probability = max(0.0, min(1.0, float(probabilities[outcome])))
            index = min(bins - 1, int(probability * bins))
            buckets[index].append((probability, 1 if target == OUTCOME_LABELS[outcome] else 0))
        curves[outcome] = [
            {
                "bin": index,
                "count": len(bucket),
                "meanPredicted": round(mean(probability for probability, _ in bucket), 4) if bucket else 0.0,
                "observedRate": round(mean(actual for _, actual in bucket), 4) if bucket else 0.0,
            }
            for index, bucket in enumerate(buckets)
        ]
    return curves


def clamp_probability(value: float) -> float:
    return max(0.000001, min(0.999999, value))


def logit(value: float) -> float:
    value = clamp_probability(value)
    return math.log(value / (1 - value))


def calibrate_intercept(
    rows: list[dict[str, Any]],
    model: dict[str, Any],
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
) -> float:
    weights = model["weights"]
    target_rate = sum(int(row["target"]) for row in rows) / max(1, len(rows))
    low = -20.0
    high = 20.0
    for _ in range(60):
        mid = (low + high) / 2
        average_probability = mean(
            score_probability(row["features"], weights, mid, feature_names, means, scales)
            for row in rows
        )
        if average_probability < target_rate:
            low = mid
        else:
            high = mid
    return round((low + high) / 2, 10)


def score_probability(
    features: dict[str, float],
    weights: dict[str, float],
    intercept: float,
    feature_names: list[str],
    means: dict[str, float],
    scales: dict[str, float],
) -> float:
    z = intercept
    for name in feature_names:
        z += float(weights.get(name, 0)) * normalized_feature(features, name, means, scales)
    z = max(-30, min(30, z))
    return 1 / (1 + math.exp(-z))


def normalized_feature(features: dict[str, float], name: str, means: dict[str, float], scales: dict[str, float]) -> float:
    scale = scales.get(name, 1)
    return (float(features.get(name, 0)) - means.get(name, 0)) / (scale if scale > 0 else 1)


def auc(scored: list[tuple[float, int]]) -> float:
    positives = sum(target for _, target in scored)
    negatives = len(scored) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    sorted_scores = sorted(scored, key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(sorted_scores):
        end = index + 1
        while end < len(sorted_scores) and sorted_scores[end][0] == sorted_scores[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2
        rank_sum += average_rank * sum(target for _, target in sorted_scores[index:end])
        index = end
    return (rank_sum - (positives * (positives + 1) / 2)) / (positives * negatives)


def top_weights(weights: dict[str, float], *, limit: int) -> list[dict[str, float | str]]:
    return [
        {"feature": name, "weight": round(value, 6)}
        for name, value in sorted(weights.items(), key=lambda item: abs(item[1]), reverse=True)[:limit]
    ]


def top_softmax_weights(weights_by_class: dict[str, dict[str, float]], *, limit: int) -> list[dict[str, float | str]]:
    combined: dict[str, float] = defaultdict(float)
    for weights in weights_by_class.values():
        for name, value in weights.items():
            combined[name] = max(combined[name], abs(float(value)))
    return [
        {"feature": name, "maxAbsWeight": round(value, 6)}
        for name, value in sorted(combined.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def top_xgboost_features(booster: Any, *, limit: int) -> list[dict[str, float | str]]:
    scores = booster.get_score(importance_type="gain")
    return [
        {"feature": name, "gain": round(float(value), 6)}
        for name, value in sorted(scores.items(), key=lambda item: float(item[1]), reverse=True)[:limit]
    ]


if __name__ == "__main__":
    main()
