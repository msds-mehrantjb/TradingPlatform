from __future__ import annotations

import importlib.util
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


FORECAST_HORIZON_MINUTES = 5
PREDICTION_LOG_INTERVAL_MINUTES = 5
LEDGER_NUMBER_DECIMAL_PLACES = 2
MIN_FEATURE_FORECAST_CANDLES = 2
DEFAULT_SUCCESS_THRESHOLD = 0.6
DEFAULT_MIN_EDGE_GAP = 0.1
DEFAULT_MAX_MODEL_DISAGREEMENT = 0.1
DEFAULT_MAX_SPREAD_ATR = 0.2
DEFAULT_PROFIT_TARGET_DOLLARS = 1.0
DEFAULT_MIN_TARGET_PCT = 0.0
DEFAULT_MIN_STOP_PCT = 0.0025
DEFAULT_TARGET_ATR_MULTIPLIER = 1.0
DEFAULT_STOP_ATR_MULTIPLIER = 1.0
HIGH_VOLATILITY_THRESHOLD_ADJUSTMENT = 0.08
SIDEWAYS_THRESHOLD_ADJUSTMENT = 0.05
HIGH_VOLATILITY_POSITION_SIZE_MULTIPLIER = 0.5
SIDEWAYS_POSITION_SIZE_MULTIPLIER = 0.65
MODEL_VERSION = "market_forecast_v11"
MODEL_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "data" / "market_forecast"
FUTURE_MARKET_PREDICTION_LEDGER_NAME = "future_market_prediction_ledger"
FUTURE_MARKET_PREDICTION_LEDGER_TITLE = "Future Market Prediction Ledger"
FUTURE_MARKET_PREDICTION_LEDGER_RULE = (
    "futurePredictionPrice is a real 5-minute expected future close estimate derived from "
    "buy/sell/timeout probabilities, trend indicators, VWAP/RSI/Bollinger mean-reversion "
    "features, strategy scores, regime, session, volatility, and timeout/no-edge uncertainty."
)
PREDICTION_LOG_DIR = MODEL_ARTIFACT_DIR / FUTURE_MARKET_PREDICTION_LEDGER_NAME
LEGACY_PREDICTION_LOG_DIR = MODEL_ARTIFACT_DIR / "prediction_logs"
MICROSTRUCTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "microstructure"
OUTCOME_STOP = "stop_hit_first"
OUTCOME_TIMEOUT = "timeout_no_edge"
OUTCOME_TARGET = "target_hit_first"
OUTCOME_LABELS = {
    OUTCOME_STOP: -1,
    OUTCOME_TIMEOUT: 0,
    OUTCOME_TARGET: 1,
}
OUTCOME_ORDER = [OUTCOME_STOP, OUTCOME_TIMEOUT, OUTCOME_TARGET]
DECISION_BUY = "buy"
DECISION_SELL = "sell"
DECISION_NO_TRADE = "no_trade"
SKIPPED_RAW_FEATURES = {
    "microstructure.avg_spread",
    "microstructure.avg_spread_pct",
    "microstructure.min_spread",
    "microstructure.max_spread",
    "microstructure.avg_bid_size",
    "microstructure.avg_ask_size",
    "microstructure.trade_volume",
    "microstructure.buy_volume",
    "microstructure.sell_volume",
}


def market_forecast_prediction(
    candles: list[dict[str, Any]],
    *,
    microstructure_rows: list[dict[str, Any]] | None = None,
    spread: float | None = None,
    slippage: float = 0.02,
    fees: float = 0.0,
) -> dict[str, Any]:
    normalized = normalize_candles(candles)
    symbol = normalized[-1]["symbol"] if normalized else "SPY"
    missing_inputs = missing_runtime_inputs(symbol)
    model_status = model_runtime_status(symbol)

    if len(normalized) < MIN_FEATURE_FORECAST_CANDLES:
        future_price_prediction = (
            no_edge_future_price_prediction(
                normalized[-1]["close"],
                f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for feature-based forecast",
            )
            if normalized
            else None
        )
        return {
            "status": "insufficient_data",
            "symbol": normalized[-1]["symbol"] if normalized else "SPY",
            "horizonMinutes": FORECAST_HORIZON_MINUTES,
            "probabilitySuccess": None,
            "probabilityBuySuccess": None,
            "probabilitySellSuccess": None,
            "probabilityStop": None,
            "probabilityTimeout": None,
            "outcome": {
                "predicted": OUTCOME_TIMEOUT,
                "probabilities": {name: None for name in OUTCOME_ORDER},
                "labels": OUTCOME_LABELS,
            },
            "decision": {
                "action": DECISION_NO_TRADE,
                "candidateAction": DECISION_NO_TRADE,
                "confidence": None,
                "edgeGap": None,
                "minimumConfidence": DEFAULT_SUCCESS_THRESHOLD,
                "minimumEdgeGap": DEFAULT_MIN_EDGE_GAP,
                "modelDisagreement": None,
                "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
                "spreadAtr": None,
                "maximumSpreadAtr": DEFAULT_MAX_SPREAD_ATR,
                "positionSizeMultiplier": 0.0,
                "reasons": [f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for baseline forecast"],
            },
            "threshold": DEFAULT_SUCCESS_THRESHOLD,
            "minimumEdgeGap": DEFAULT_MIN_EDGE_GAP,
            "expectedValue": None,
            "barriers": {
                "targetDistance": None,
                "stopDistance": None,
                "fixedTargetDollars": DEFAULT_PROFIT_TARGET_DOLLARS,
                "minTargetPct": DEFAULT_MIN_TARGET_PCT,
                "minStopPct": DEFAULT_MIN_STOP_PCT,
                "targetAtrMultiplier": DEFAULT_TARGET_ATR_MULTIPLIER,
                "stopAtrMultiplier": DEFAULT_STOP_ATR_MULTIPLIER,
            },
            "expectedMove": None,
            "futurePricePrediction": future_price_prediction,
            "costs": round((spread or 0) + (slippage * 2) + fees, 4),
            "allowed": False,
            "model": model_status,
            "regime": {"trend": "unknown", "volatility": "unknown", "vwap": "unknown", "timeOfDay": "unknown"},
            "marketRegime": {
                "trend": "unknown",
                "volatility": "unknown",
                "session": "unknown",
                "allowedLong": False,
                "allowedShort": False,
                "thresholdAdjustment": 0.0,
                "positionSizeMultiplier": 0.0,
                "notes": [f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for regime detection"],
            },
            "algorithmSignals": {},
            "uncertainty": {
                "modelCount": 0,
                "modelDisagreement": None,
                "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
                "members": [],
            },
            "features": {},
            "topDrivers": [f"Need at least {MIN_FEATURE_FORECAST_CANDLES} one-minute candles for baseline forecast"],
            "missingInputs": missing_inputs,
            "updatedAt": datetime.utcnow().isoformat() + "Z",
        }

    latest_microstructure = latest_microstructure_for_candle(normalized[-1], microstructure_rows or [])
    features = attach_microstructure_features(extract_market_forecast_features(normalized), latest_microstructure)
    artifact = load_market_forecast_artifact(normalized[-1]["symbol"])
    ensemble = ensemble_probabilities(features, artifact) if artifact else fallback_ensemble_probabilities(features)
    probabilities = ensemble["probabilities"]
    buy_probability = probabilities[OUTCOME_TARGET]
    sell_probability = probabilities[OUTCOME_STOP]
    timeout_probability = probabilities[OUTCOME_TIMEOUT]
    predicted_outcome = max(probabilities.items(), key=lambda item: item[1])[0]
    latest = normalized[-1]
    costs = round((spread if spread is not None else 0) + (slippage * 2) + fees, 4)
    barriers = volatility_adjusted_barriers(features, latest["close"], artifact=artifact)
    expected_move = barriers["targetDistance"]
    expected_loss = barriers["stopDistance"]
    buy_expected_value = round((buy_probability * expected_move) - (sell_probability * expected_loss) - costs, 4)
    sell_expected_value = round((sell_probability * expected_move) - (buy_probability * expected_loss) - costs, 4)
    market_regime = market_regime_profile(features)
    future_price_prediction = forecast_future_price_prediction(
        features,
        latest["close"],
        probabilities=probabilities,
        barriers=barriers,
        market_regime=market_regime,
    )
    regime_allows = regime_allows_forecast(features, market_regime)
    base_threshold = forecast_probability_threshold(artifact)
    decision = forecast_trade_decision(
        probabilities,
        buy_expected_value=buy_expected_value,
        sell_expected_value=sell_expected_value,
        regime_allows=regime_allows,
        market_regime=market_regime,
        uncertainty=ensemble,
        features=features,
        base_threshold=base_threshold,
    )

    return {
        "status": "ready" if artifact else "fallback",
        "symbol": latest["symbol"],
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "probabilitySuccess": round(buy_probability, 4),
        "probabilityBuySuccess": round(buy_probability, 4),
        "probabilitySellSuccess": round(sell_probability, 4),
        "probabilityStop": round(sell_probability, 4),
        "probabilityTimeout": round(timeout_probability, 4),
        "outcome": {
            "predicted": predicted_outcome,
            "probabilities": {name: round(probabilities[name], 4) for name in OUTCOME_ORDER},
            "labels": OUTCOME_LABELS,
        },
        "decision": decision,
        "threshold": decision["minimumConfidence"],
        "minimumEdgeGap": decision["minimumEdgeGap"],
        "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
        "expectedValue": decision["expectedValue"],
        "buyExpectedValue": buy_expected_value,
        "sellExpectedValue": sell_expected_value,
        "barriers": {
            "targetDistance": round(barriers["targetDistance"], 4),
            "stopDistance": round(barriers["stopDistance"], 4),
            "minTargetPct": barriers["minTargetPct"],
            "minStopPct": barriers["minStopPct"],
            "targetAtrMultiplier": barriers["targetAtrMultiplier"],
            "stopAtrMultiplier": barriers["stopAtrMultiplier"],
            "fixedTargetDollars": barriers["fixedTargetDollars"],
            "fixedStopDollars": barriers["fixedStopDollars"],
            "atr5m": round(barriers["atr5m"], 4),
        },
        "expectedMove": round(expected_move, 4),
        "futurePricePrediction": future_price_prediction,
        "costs": costs,
        "allowed": decision["action"] in {DECISION_BUY, DECISION_SELL},
        "model": model_runtime_status(normalized[-1]["symbol"]),
        "regime": forecast_regime(features),
        "marketRegime": market_regime,
        "algorithmSignals": algorithm_signal_summary(features),
        "uncertainty": ensemble,
        "features": features,
        "topDrivers": forecast_drivers(features, probabilities, decision, regime_allows),
        "missingInputs": missing_inputs,
        "updatedAt": datetime.utcnow().isoformat() + "Z",
    }


def normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        try:
            rows.append(
                {
                    "symbol": str(candle.get("symbol") or "SPY").upper(),
                    "timestamp": str(candle["timestamp"]),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume") or 0),
                    "vwap": float(candle["vwap"]) if candle.get("vwap") is not None else None,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda row: row["timestamp"])


def record_market_forecast_prediction(
    symbol: str,
    feed: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_candles(candles)
    if not normalized:
        return {
            "saved": False,
            "reason": "No candles available for prediction log",
            "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
            "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
        }

    candle_days = sorted({prediction_log_day(row["timestamp"]) for row in normalized if row.get("timestamp")})
    updated_files: list[str] = []
    resolved_count = 0
    pending_count = 0

    for day in candle_days:
        result = resolve_market_forecast_prediction_day(symbol, day, normalized)
        if result["updated"]:
            updated_files.append(str(result["path"]))
        resolved_count += result["resolved"]
        pending_count += result["pending"]

    latest = normalized[-1]
    if not is_prediction_log_cadence(latest["timestamp"]):
        return {
            "saved": False,
            "reason": f"Waiting for the next {PREDICTION_LOG_INTERVAL_MINUTES}-minute prediction log boundary",
            "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
            "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
            "intervalMinutes": PREDICTION_LOG_INTERVAL_MINUTES,
            "latestCandleTimestamp": latest["timestamp"],
            "nextRecordAt": next_prediction_log_boundary(latest["timestamp"]),
            "updatedFiles": updated_files,
            "resolvedRecords": resolved_count,
            "pendingRecords": pending_count,
        }

    record = build_market_forecast_prediction_record(symbol, feed, timeframe, latest, forecast)
    day = prediction_log_day(record["predictionTimestamp"])
    document = read_prediction_log_document(symbol, day)
    records = document["records"]
    existing = next((index for index, item in enumerate(records) if item.get("id") == record["id"]), None)
    if existing is None:
        records.append(record)
    else:
        preserved_actual = records[existing].get("actual") or {}
        record["actual"] = preserved_actual if preserved_actual.get("status") == "resolved" else record["actual"]
        records[existing] = merge_prediction_log_record(records[existing], record)

    records.sort(key=lambda item: str(item.get("predictionTimestamp") or ""))
    resolution = resolve_market_forecast_record(records[-1], normalized)
    records[-1] = resolution["record"]
    if resolution["resolved"]:
        resolved_count += 1
    elif records[-1].get("actual", {}).get("status") == "pending":
        pending_count += 1

    try:
        write_prediction_log_document(symbol, day, records)
    except OSError as exc:
        return {
            "saved": False,
            "reason": f"Prediction log write failed: {exc}",
            "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
            "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
            "recordId": record["id"],
            "predictionTimestamp": record["predictionTimestamp"],
            "date": day,
        }
    path = prediction_log_path(symbol, day)
    if str(path) not in updated_files:
        updated_files.append(str(path))

    return {
        "saved": True,
        "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
        "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
        "ledgerRule": FUTURE_MARKET_PREDICTION_LEDGER_RULE,
        "path": str(path),
        "recordId": record["id"],
        "predictionTimestamp": record["predictionTimestamp"],
        "date": day,
        "intervalMinutes": PREDICTION_LOG_INTERVAL_MINUTES,
        "updatedFiles": updated_files,
        "resolvedRecords": resolved_count,
        "pendingRecords": pending_count,
    }


def read_market_forecast_prediction_log(
    symbol: str,
    *,
    date: str | None = None,
    feed: str | None = None,
    timeframe: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    symbol = symbol.upper()
    paths = [existing_prediction_log_path(symbol, date)] if date else sorted(prediction_symbol_log_paths(symbol))
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for record in document.get("records") or []:
            if feed and record.get("feed") != feed:
                continue
            if timeframe and record.get("timeframe") != timeframe:
                continue
            records.append(record)

    records.sort(key=lambda item: str(item.get("predictionTimestamp") or ""), reverse=True)
    limited = records[: max(1, limit)]
    return {
        "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
        "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
        "ledgerRule": FUTURE_MARKET_PREDICTION_LEDGER_RULE,
        "symbol": symbol,
        "date": date,
        "records": limited,
        "summary": summarize_prediction_log(limited),
    }


def resolve_market_forecast_prediction_day(symbol: str, day: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    path = existing_prediction_log_path(symbol, day)
    if not path.exists():
        return {"path": path, "updated": False, "resolved": 0, "pending": 0}
    document = read_prediction_log_document(symbol, day)
    records = document["records"]
    updated = False
    resolved = 0
    pending = 0
    for index, record in enumerate(records):
        if (record.get("actual") or {}).get("status") == "resolved":
            continue
        resolution = resolve_market_forecast_record(record, candles)
        records[index] = resolution["record"]
        if resolution["updated"]:
            updated = True
        if resolution["resolved"]:
            resolved += 1
        elif records[index].get("actual", {}).get("status") == "pending":
            pending += 1
    if updated:
        try:
            write_prediction_log_document(symbol, day, records)
        except OSError:
            return {"path": path, "updated": False, "resolved": resolved, "pending": pending}
    return {"path": path, "updated": updated, "resolved": resolved, "pending": pending}


def build_market_forecast_prediction_record(
    symbol: str,
    feed: str,
    timeframe: str,
    latest: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    timestamp = str(latest["timestamp"])
    barriers = forecast.get("barriers") or {}
    decision = forecast.get("decision") or {}
    outcome = forecast.get("outcome") or {}
    future_price_prediction = forecast.get("futurePricePrediction") or {}
    predicted_future = predicted_future_market_snapshot(
        latest,
        decision.get("candidateAction") or decision.get("action") or DECISION_NO_TRADE,
        int(forecast.get("horizonMinutes") or FORECAST_HORIZON_MINUTES),
        numeric(barriers.get("targetDistance")),
        numeric(barriers.get("stopDistance")),
        future_price_prediction=future_price_prediction,
    )
    return json_safe(
        {
            "id": f"{symbol.upper()}|{feed}|{timeframe}|{timestamp}|{MODEL_VERSION}",
            "symbol": symbol.upper(),
            "feed": feed,
            "timeframe": timeframe,
            "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
            "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
            "ledgerRule": FUTURE_MARKET_PREDICTION_LEDGER_RULE,
            "modelVersion": MODEL_VERSION,
            "predictionTimestamp": timestamp,
            "generatedAt": forecast.get("updatedAt") or datetime.utcnow().isoformat() + "Z",
            "horizonMinutes": int(forecast.get("horizonMinutes") or FORECAST_HORIZON_MINUTES),
            "entryPrice": round(float(latest["close"]), 4),
            "predictionMarket": candle_market_snapshot(latest),
            "prediction": {
                "status": forecast.get("status"),
                "predictedOutcome": outcome.get("predicted"),
                "probabilityBuySuccess": forecast.get("probabilityBuySuccess"),
                "probabilitySellSuccess": forecast.get("probabilitySellSuccess"),
                "probabilityTimeout": forecast.get("probabilityTimeout"),
                "decisionAction": decision.get("action"),
                "candidateAction": decision.get("candidateAction"),
                "confidence": decision.get("confidence"),
                "edgeGap": decision.get("edgeGap"),
                "expectedValue": decision.get("expectedValue"),
                "buyExpectedValue": forecast.get("buyExpectedValue"),
                "sellExpectedValue": forecast.get("sellExpectedValue"),
                "allowed": forecast.get("allowed"),
                "reasons": decision.get("reasons") or [],
                "topDrivers": forecast.get("topDrivers") or [],
            },
            "barriers": {
                "targetDistance": numeric(barriers.get("targetDistance")),
                "stopDistance": numeric(barriers.get("stopDistance")),
                "atr5m": barriers.get("atr5m"),
                "minTargetPct": barriers.get("minTargetPct"),
                "minStopPct": barriers.get("minStopPct"),
                "targetAtrMultiplier": barriers.get("targetAtrMultiplier"),
                "stopAtrMultiplier": barriers.get("stopAtrMultiplier"),
            },
            "predictedFutureMarket": predicted_future,
            "futurePricePrediction": future_price_prediction,
            "priceComparison": price_comparison_snapshot(latest, predicted_future),
            "costs": numeric(forecast.get("costs")),
            "marketRegime": forecast.get("marketRegime") or {},
            "regime": forecast.get("regime") or {},
            "algorithmSignals": forecast.get("algorithmSignals") or {},
            "uncertainty": forecast.get("uncertainty") or {},
            "features": forecast.get("features") or {},
            "actual": {
                "status": "pending",
                "outcome": None,
                "resolvedAt": None,
                "expectedAt": add_minutes_iso(timestamp, int(forecast.get("horizonMinutes") or FORECAST_HORIZON_MINUTES)),
                "actualFutureMarket": None,
                "reason": "Waiting for future candles inside the prediction horizon",
            },
        }
    )


def resolve_market_forecast_record(record: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    timestamp = str(record.get("predictionTimestamp") or "")
    if not timestamp:
        return {"record": record, "updated": False, "resolved": False}
    index = next((position for position, candle in enumerate(candles) if str(candle.get("timestamp")) == timestamp), None)
    if index is None:
        return {"record": record, "updated": False, "resolved": False}

    horizon = max(1, int(record.get("horizonMinutes") or FORECAST_HORIZON_MINUTES))
    future = candles[index + 1 : index + 1 + horizon]
    if not future:
        return {"record": record, "updated": False, "resolved": False}

    entry = float(record.get("entryPrice") or candles[index]["close"])
    barriers = record.get("barriers") or {}
    target_distance = numeric(barriers.get("targetDistance"))
    stop_distance = numeric(barriers.get("stopDistance"))
    if target_distance <= 0 or stop_distance <= 0:
        return {"record": record, "updated": False, "resolved": False}

    buy_target_price = entry + target_distance
    buy_stop_price = entry - stop_distance
    sell_target_price = entry - target_distance
    sell_stop_price = entry + stop_distance
    latest_timestamp = parse_timestamp(str(candles[-1].get("timestamp") or ""))
    prediction_timestamp = parse_timestamp(timestamp)
    horizon_elapsed = bool(
        latest_timestamp
        and prediction_timestamp
        and latest_timestamp >= prediction_timestamp + timedelta(minutes=horizon)
    )

    buy_resolution = directional_trade_resolution(
        future,
        entry=entry,
        side=DECISION_BUY,
        target_distance=target_distance,
        stop_distance=stop_distance,
    )
    sell_resolution = directional_trade_resolution(
        future,
        entry=entry,
        side=DECISION_SELL,
        target_distance=target_distance,
        stop_distance=stop_distance,
    )
    record = dict(record)
    action = ((record.get("prediction") or {}).get("decisionAction") or DECISION_NO_TRADE)
    candidate_action = ((record.get("prediction") or {}).get("candidateAction") or DECISION_NO_TRADE)
    candidate_resolution = (
        buy_resolution
        if candidate_action == DECISION_BUY
        else sell_resolution
        if candidate_action == DECISION_SELL
        else first_directional_market_resolution(buy_resolution, sell_resolution)
    )
    outcome = market_outcome_from_directional_resolution(candidate_resolution)

    if outcome == OUTCOME_TIMEOUT:
        if len(future) < horizon and not horizon_elapsed:
            return {"record": record, "updated": False, "resolved": False}

    hit_at = candidate_resolution["hitAt"] if outcome != OUTCOME_TIMEOUT else future[-1]["timestamp"]
    resolved_candle = candidate_resolution["resolvedCandle"] if outcome != OUTCOME_TIMEOUT else future[-1]
    bars_held = int(candidate_resolution["barsHeld"] if outcome != OUTCOME_TIMEOUT else len(future))
    exit_price = float(candidate_resolution["exitPrice"] if outcome != OUTCOME_TIMEOUT else future[-1]["close"])
    costs = numeric(record.get("costs"))
    buy_value = directional_resolution_value(buy_resolution, entry, float(future[-1]["close"]), target_distance, stop_distance)
    sell_value = directional_resolution_value(sell_resolution, entry, float(future[-1]["close"]), target_distance, stop_distance)
    horizon_candle = future[horizon - 1] if len(future) >= horizon else future[-1]
    horizon_close = float(horizon_candle["close"])
    max_high = max(float(candle["high"]) for candle in future)
    min_low = min(float(candle["low"]) for candle in future)
    price_comparison = resolved_price_comparison_snapshot(record, horizon_candle, entry)
    actual = {
        "status": "resolved",
        "outcome": outcome,
        "actualClass": actual_class_name(outcome),
        "resolvedAt": hit_at,
        "expectedAt": add_minutes_iso(timestamp, horizon),
        "barsHeld": bars_held,
        "entryPrice": round(entry, 4),
        "exitPrice": round(exit_price, 4),
        "predictionMarket": record.get("predictionMarket") or candle_market_snapshot(candles[index]),
        "actualMarketAtResolution": candle_market_snapshot(resolved_candle),
        "actualFutureMarket": candle_market_snapshot(horizon_candle),
        "actualFutureClose": round(horizon_close, 4),
        "actualFutureChangeDollars": round(horizon_close - entry, 4),
        "actualFutureReturnPct": round(safe_return(horizon_close, entry), 6),
        "maxHighDuringHorizon": round(max_high, 4),
        "minLowDuringHorizon": round(min_low, 4),
        "maxBuyFavorableMoveDollars": round(max_high - entry, 4),
        "maxBuyAdverseMoveDollars": round(entry - min_low, 4),
        "maxSellFavorableMoveDollars": round(entry - min_low, 4),
        "maxSellAdverseMoveDollars": round(max_high - entry, 4),
        "upperBarrier": round(buy_target_price, 4),
        "lowerBarrier": round(sell_target_price, 4),
        "buyProfitTargetPrice": round(buy_target_price, 4),
        "buyStopPrice": round(buy_stop_price, 4),
        "sellProfitTargetPrice": round(sell_target_price, 4),
        "sellStopPrice": round(sell_stop_price, 4),
        "candidateProfitTargetPrice": round(float(candidate_resolution["targetPrice"]), 4),
        "candidateStopPrice": round(float(candidate_resolution["stopPrice"]), 4),
        "candidateOutcome": candidate_resolution["status"],
        "candidateProfitTargetHit": candidate_resolution["status"] == "profit_target_hit_first",
        "candidateStopHit": candidate_resolution["status"] == "stop_loss_hit_first",
        "targetDistance": round(target_distance, 4),
        "stopDistance": round(stop_distance, 4),
        "buyValueDollars": round(buy_value, 4),
        "sellValueDollars": round(sell_value, 4),
        "decisionAction": action,
        "candidateAction": candidate_action,
        "executedDecisionCorrect": executed_decision_correct(action, outcome),
        "candidateDirectionCorrect": candidate_direction_correct(candidate_action, outcome),
        "tradeResult": realized_trade_result(action, outcome),
        "realizedDecisionValueDollars": round(realized_decision_value(action, buy_value, sell_value, costs), 4),
    }
    record["actual"] = json_safe(actual)
    record["priceComparison"] = json_safe(price_comparison)
    return {"record": record, "updated": True, "resolved": True}


def directional_trade_resolution(
    future: list[dict[str, Any]],
    *,
    entry: float,
    side: str,
    target_distance: float,
    stop_distance: float,
) -> dict[str, Any]:
    if side == DECISION_SELL:
        target_price = entry - target_distance
        stop_price = entry + stop_distance
    else:
        target_price = entry + target_distance
        stop_price = entry - stop_distance

    for offset, candle in enumerate(future, start=1):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if side == DECISION_SELL:
            target_hit = low <= target_price
            stop_hit = high >= stop_price
            target_wins_tie = close <= entry
        else:
            target_hit = high >= target_price
            stop_hit = low <= stop_price
            target_wins_tie = close >= entry

        status = None
        if target_hit and stop_hit:
            status = "profit_target_hit_first" if target_wins_tie else "stop_loss_hit_first"
        elif target_hit:
            status = "profit_target_hit_first"
        elif stop_hit:
            status = "stop_loss_hit_first"

        if status:
            return {
                "side": side,
                "status": status,
                "hitAt": candle["timestamp"],
                "barsHeld": offset,
                "exitPrice": target_price if status == "profit_target_hit_first" else stop_price,
                "targetPrice": target_price,
                "stopPrice": stop_price,
                "resolvedCandle": candle,
            }

    fallback = future[-1]
    return {
        "side": side,
        "status": OUTCOME_TIMEOUT,
        "hitAt": fallback["timestamp"],
        "barsHeld": len(future),
        "exitPrice": float(fallback["close"]),
        "targetPrice": target_price,
        "stopPrice": stop_price,
        "resolvedCandle": fallback,
    }


def first_directional_market_resolution(buy_resolution: dict[str, Any], sell_resolution: dict[str, Any]) -> dict[str, Any]:
    buy_done = buy_resolution["status"] != OUTCOME_TIMEOUT
    sell_done = sell_resolution["status"] != OUTCOME_TIMEOUT
    if buy_done and sell_done:
        return buy_resolution if int(buy_resolution["barsHeld"]) <= int(sell_resolution["barsHeld"]) else sell_resolution
    if buy_done:
        return buy_resolution
    if sell_done:
        return sell_resolution
    return buy_resolution


def market_outcome_from_directional_resolution(resolution: dict[str, Any]) -> str:
    if resolution["status"] == OUTCOME_TIMEOUT:
        return OUTCOME_TIMEOUT
    if resolution["side"] == DECISION_BUY:
        return OUTCOME_TARGET if resolution["status"] == "profit_target_hit_first" else OUTCOME_STOP
    return OUTCOME_STOP if resolution["status"] == "profit_target_hit_first" else OUTCOME_TARGET


def directional_resolution_value(
    resolution: dict[str, Any],
    entry: float,
    horizon_close: float,
    target_distance: float,
    stop_distance: float,
) -> float:
    if resolution["status"] == "profit_target_hit_first":
        return target_distance
    if resolution["status"] == "stop_loss_hit_first":
        return -stop_distance
    if resolution["side"] == DECISION_SELL:
        return entry - horizon_close
    return horizon_close - entry


def realized_side_values(outcome: str, entry: float, exit_price: float, target_distance: float, stop_distance: float) -> tuple[float, float]:
    if outcome == OUTCOME_TARGET:
        return target_distance, -stop_distance
    if outcome == OUTCOME_STOP:
        return -stop_distance, target_distance
    return exit_price - entry, entry - exit_price


def actual_class_name(outcome: str) -> str:
    if outcome == OUTCOME_TARGET:
        return "buy_success"
    if outcome == OUTCOME_STOP:
        return "sell_success"
    return "timeout_no_edge"


def executed_decision_correct(action: str, outcome: str) -> bool:
    if action == DECISION_BUY:
        return outcome == OUTCOME_TARGET
    if action == DECISION_SELL:
        return outcome == OUTCOME_STOP
    return outcome == OUTCOME_TIMEOUT


def candidate_direction_correct(action: str, outcome: str) -> bool | None:
    if action == DECISION_BUY:
        return outcome == OUTCOME_TARGET
    if action == DECISION_SELL:
        return outcome == OUTCOME_STOP
    return None


def realized_trade_result(action: str, outcome: str) -> str:
    if action == DECISION_NO_TRADE:
        return "skipped_timeout" if outcome == OUTCOME_TIMEOUT else "skipped_edge"
    if executed_decision_correct(action, outcome):
        return "win"
    if outcome == OUTCOME_TIMEOUT:
        return "timeout_exit"
    return "loss"


def realized_decision_value(action: str, buy_value: float, sell_value: float, costs: float) -> float:
    if action == DECISION_BUY:
        return buy_value - costs
    if action == DECISION_SELL:
        return sell_value - costs
    return 0.0


def candle_market_snapshot(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": str(candle.get("timestamp") or ""),
        "open": round(numeric(candle.get("open")), 4),
        "high": round(numeric(candle.get("high")), 4),
        "low": round(numeric(candle.get("low")), 4),
        "close": round(numeric(candle.get("close")), 4),
        "volume": round(numeric(candle.get("volume")), 4),
        "vwap": round(numeric(candle.get("vwap")), 4) if candle.get("vwap") is not None else None,
    }


def predicted_future_market_snapshot(
    latest: dict[str, Any],
    action: str,
    horizon_minutes: int,
    target_distance: float,
    stop_distance: float,
    *,
    future_price_prediction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = numeric(latest.get("close"))
    if action == DECISION_SELL:
        target_price = entry - target_distance
        stop_price = entry + stop_distance
    elif action == DECISION_BUY:
        target_price = entry + target_distance
        stop_price = entry - stop_distance
    else:
        target_price = None
        stop_price = None
    predicted_price = (future_price_prediction or {}).get("predictedPrice")
    predicted_change = (future_price_prediction or {}).get("predictedChangeDollars")
    return {
        "predictionTimestamp": str(latest.get("timestamp") or ""),
        "expectedAt": add_minutes_iso(str(latest.get("timestamp") or ""), horizon_minutes),
        "candidateAction": action,
        "entryPrice": round(entry, 4),
        "predictedPrice": round(numeric(predicted_price), 4) if predicted_price is not None else None,
        "predictedChangeDollars": round(numeric(predicted_change), 4) if predicted_change is not None else None,
        "predictedDirection": (future_price_prediction or {}).get("direction"),
        "targetPrice": round(target_price, 4) if target_price is not None else None,
        "stopPrice": round(stop_price, 4) if stop_price is not None else None,
        "targetDistance": round(target_distance, 4),
        "stopDistance": round(stop_distance, 4),
    }


def price_comparison_snapshot(latest: dict[str, Any], predicted_future: dict[str, Any]) -> dict[str, Any]:
    current_price = numeric(latest.get("close"))
    predicted_price = predicted_future.get("predictedPrice")
    predicted_change = numeric(predicted_price) - current_price if predicted_price is not None else None
    return {
        "predictionTimestamp": str(latest.get("timestamp") or ""),
        "expectedAt": predicted_future.get("expectedAt"),
        "actualCurrentPrice": round(current_price, 4),
        "futurePredictionPrice": round(numeric(predicted_price), 4) if predicted_price is not None else None,
        "futurePredictionChangeDollars": round(predicted_change, 4) if predicted_change is not None else None,
        "futurePredictionDirection": price_direction(predicted_change),
        "actualFuturePrice": None,
        "actualFutureChangeDollars": None,
        "actualFutureDirection": None,
        "predictionErrorDollars": None,
    }


def resolved_price_comparison_snapshot(record: dict[str, Any], horizon_candle: dict[str, Any], entry: float) -> dict[str, Any]:
    comparison = dict(record.get("priceComparison") or {})
    predicted_future = record.get("predictedFutureMarket") or {}
    predicted_price = comparison.get("futurePredictionPrice")
    if predicted_price is None and predicted_future.get("predictedPrice") is not None:
        predicted_price = numeric(predicted_future.get("predictedPrice"))
    actual_future_price = numeric(horizon_candle.get("close"))
    actual_change = actual_future_price - entry
    comparison.update(
        {
            "predictionTimestamp": record.get("predictionTimestamp"),
            "expectedAt": str(horizon_candle.get("timestamp") or ""),
            "actualCurrentPrice": round(entry, 4),
            "futurePredictionPrice": round(numeric(predicted_price), 4) if predicted_price is not None else None,
            "futurePredictionChangeDollars": (
                round(numeric(predicted_price) - entry, 4) if predicted_price is not None else None
            ),
            "futurePredictionDirection": price_direction(
                numeric(predicted_price) - entry if predicted_price is not None else None
            ),
            "actualFuturePrice": round(actual_future_price, 4),
            "actualFutureChangeDollars": round(actual_change, 4),
            "actualFutureDirection": price_direction(actual_change),
            "predictionErrorDollars": (
                round(actual_future_price - numeric(predicted_price), 4) if predicted_price is not None else None
            ),
        }
    )
    return comparison


def price_direction(change: float | None) -> str | None:
    if change is None:
        return None
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def add_minutes_iso(timestamp: str, minutes: int) -> str | None:
    parsed = parse_timestamp(str(timestamp))
    if not parsed:
        return None
    return (parsed + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def summarize_prediction_log(records: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [record for record in records if (record.get("actual") or {}).get("status") == "resolved"]
    pending = len(records) - len(resolved)
    executed = [
        record
        for record in resolved
        if ((record.get("prediction") or {}).get("decisionAction") in {DECISION_BUY, DECISION_SELL})
    ]
    no_trade = [
        record
        for record in resolved
        if ((record.get("prediction") or {}).get("decisionAction") == DECISION_NO_TRADE)
    ]
    wins = [record for record in executed if (record.get("actual") or {}).get("tradeResult") == "win"]
    realized_values = [numeric((record.get("actual") or {}).get("realizedDecisionValueDollars")) for record in executed]
    candidate_scored = [
        record for record in resolved if (record.get("actual") or {}).get("candidateDirectionCorrect") is not None
    ]
    candidate_hits = [
        record for record in candidate_scored if (record.get("actual") or {}).get("candidateDirectionCorrect") is True
    ]
    no_trade_timeouts = [
        record for record in no_trade if (record.get("actual") or {}).get("outcome") == OUTCOME_TIMEOUT
    ]
    return {
        "total": len(records),
        "resolved": len(resolved),
        "pending": pending,
        "executedTrades": len(executed),
        "executedWins": len(wins),
        "executedWinRate": round(len(wins) / len(executed), 4) if executed else None,
        "noTradeDecisions": len(no_trade),
        "noTradeTimeoutRate": round(len(no_trade_timeouts) / len(no_trade), 4) if no_trade else None,
        "candidateDirectionAccuracy": round(len(candidate_hits) / len(candidate_scored), 4) if candidate_scored else None,
        "averageRealizedDecisionValueDollars": round(mean(realized_values), 4) if realized_values else None,
        "cumulativeRealizedDecisionValueDollars": round(sum(realized_values), 4) if realized_values else 0.0,
        "actions": count_prediction_values(records, "decisionAction"),
        "actualOutcomes": count_actual_outcomes(resolved),
    }


def count_prediction_values(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str((record.get("prediction") or {}).get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def count_actual_outcomes(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str((record.get("actual") or {}).get("outcome") or "pending")
        counts[value] = counts.get(value, 0) + 1
    return counts


def prediction_safe_symbol(symbol: str) -> str:
    return "".join(character for character in symbol.upper() if character.isalnum() or character in {"-", "_"}) or "SPY"


def prediction_symbol_log_paths(symbol: str) -> list[Path]:
    safe_symbol = prediction_safe_symbol(symbol)
    paths_by_name: dict[str, Path] = {}
    for directory in (LEGACY_PREDICTION_LOG_DIR, PREDICTION_LOG_DIR):
        for path in directory.glob(f"{safe_symbol}_*.json"):
            paths_by_name[path.name] = path
    return list(paths_by_name.values())


def prediction_log_path(symbol: str, day: str) -> Path:
    return PREDICTION_LOG_DIR / f"{prediction_safe_symbol(symbol)}_{day}.json"


def existing_prediction_log_path(symbol: str, day: str) -> Path:
    preferred = prediction_log_path(symbol, day)
    if preferred.exists():
        return preferred
    legacy = LEGACY_PREDICTION_LOG_DIR / f"{prediction_safe_symbol(symbol)}_{day}.json"
    return legacy if legacy.exists() else preferred


def prediction_log_day(timestamp: str) -> str:
    parsed = parse_timestamp(str(timestamp))
    if parsed:
        return eastern_wall_clock(parsed).date().isoformat()
    return str(timestamp)[:10] or datetime.utcnow().date().isoformat()


def is_prediction_log_cadence(timestamp: str) -> bool:
    parsed = parse_timestamp(str(timestamp))
    if not parsed:
        return False
    local = eastern_wall_clock(parsed)
    return (
        local.minute % PREDICTION_LOG_INTERVAL_MINUTES == 0
        and local.second == 0
        and local.microsecond == 0
    )


def next_prediction_log_boundary(timestamp: str) -> str | None:
    parsed = parse_timestamp(str(timestamp))
    if not parsed:
        return None
    local = eastern_wall_clock(parsed).replace(second=0, microsecond=0)
    minutes_until = PREDICTION_LOG_INTERVAL_MINUTES - (local.minute % PREDICTION_LOG_INTERVAL_MINUTES)
    if minutes_until == PREDICTION_LOG_INTERVAL_MINUTES:
        minutes_until = 0
    boundary = local + timedelta(minutes=minutes_until)
    return boundary.isoformat()


def read_prediction_log_document(symbol: str, day: str) -> dict[str, Any]:
    path = existing_prediction_log_path(symbol, day)
    if not path.exists():
        return {"records": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"records": []}
    records = document.get("records")
    return {"records": records if isinstance(records, list) else []}


def write_prediction_log_document(symbol: str, day: str, records: list[dict[str, Any]]) -> None:
    path = prediction_log_path(symbol, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": 2,
        "ledgerName": FUTURE_MARKET_PREDICTION_LEDGER_NAME,
        "ledgerTitle": FUTURE_MARKET_PREDICTION_LEDGER_TITLE,
        "ledgerRule": FUTURE_MARKET_PREDICTION_LEDGER_RULE,
        "modelVersion": MODEL_VERSION,
        "symbol": symbol.upper(),
        "date": day,
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "intervalMinutes": PREDICTION_LOG_INTERVAL_MINUTES,
        "numberDecimals": LEDGER_NUMBER_DECIMAL_PLACES,
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "summary": ledger_number_safe(summarize_prediction_log(records)),
        "records": ledger_number_safe(records),
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def merge_prediction_log_record(existing: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.update(current)
    if (existing.get("actual") or {}).get("status") == "resolved":
        merged["actual"] = existing["actual"]
    return merged


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def ledger_number_safe(value: Any) -> Any:
    return round_ledger_numbers(json_safe(value))


def round_ledger_numbers(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): round_ledger_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_ledger_numbers(item) for item in value]
    if isinstance(value, tuple):
        return [round_ledger_numbers(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        rounded = round(value, LEDGER_NUMBER_DECIMAL_PLACES)
        return 0.0 if rounded == 0 else rounded
    return value


def extract_market_forecast_features(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close"] for row in candles]
    highs = [row["high"] for row in candles]
    lows = [row["low"] for row in candles]
    volumes = [row["volume"] for row in candles]
    latest = candles[-1]
    latest_close = max(latest["close"], 0.01)
    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    session_vwap = latest["vwap"] or anchored_vwap(candles)
    returns = [safe_return(closes[index], closes[index - 1]) for index in range(1, len(closes))]
    ranges = [(row["high"] - row["low"]) / max(row["close"], 0.01) for row in candles]
    atr_1m = average_true_range(candles, 14)
    atr_floor = max(atr_1m, latest_close * 0.0001, 0.01)
    adx_value = adx(candles, 14)
    volume_average_20 = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)
    range_average_20 = mean(ranges[-20:]) if len(ranges) >= 20 else mean(ranges)
    dollar_ranges = [row["high"] - row["low"] for row in candles]
    dollar_range_average_20 = mean(dollar_ranges[-20:]) if len(dollar_ranges) >= 20 else mean(dollar_ranges)
    recent_highs = highs[-6:]
    recent_lows = lows[-6:]
    timestamp = parse_timestamp(latest["timestamp"])
    rsi_value = relative_strength_index(closes, 14)
    bollinger_position = bollinger_band_position(closes, 20, 2)

    body = latest["close"] - latest["open"]
    candle_range = max(latest["high"] - latest["low"], 0.01)
    upper_wick = latest["high"] - max(latest["open"], latest["close"])
    lower_wick = min(latest["open"], latest["close"]) - latest["low"]

    price_features = {
        "return_1m": safe_window_return(closes, 1),
        "return_2m": safe_window_return(closes, 2),
        "return_3m": safe_window_return(closes, 3),
        "return_5m": safe_window_return(closes, 5),
        "return_1m_atr": atr_normalized_move(closes, 1, atr_floor),
        "return_3m_atr": atr_normalized_move(closes, 3, atr_floor),
        "return_5m_atr": atr_normalized_move(closes, 5, atr_floor),
        "candle_body_pct": body / latest_close,
        "candle_body_atr": body / atr_floor,
        "upper_wick_pct": upper_wick / latest_close,
        "upper_wick_atr": upper_wick / atr_floor,
        "lower_wick_pct": lower_wick / latest_close,
        "lower_wick_atr": lower_wick / atr_floor,
        "range_pct": candle_range / latest_close,
        "candle_range_atr": candle_range / atr_floor,
        "distance_from_vwap": (latest_close - session_vwap) / latest_close,
        "distance_from_vwap_atr": (latest_close - session_vwap) / atr_floor,
        "distance_from_ema_9": (latest_close - ema9[-1]) / latest_close,
        "distance_from_ema_9_atr": (latest_close - ema9[-1]) / atr_floor,
        "distance_from_ema_20": (latest_close - ema20[-1]) / latest_close,
        "distance_from_ema_20_atr": (latest_close - ema20[-1]) / atr_floor,
        "distance_from_ema_50": (latest_close - ema50[-1]) / latest_close,
        "distance_from_ema_50_atr": (latest_close - ema50[-1]) / atr_floor,
    }
    volume_features = {
        "volume_spike_ratio": latest["volume"] / max(volume_average_20, 1),
        "volume_vs_20_bar_average": latest["volume"] / max(volume_average_20, 1),
        "relative_volume": latest["volume"] / max(volume_average_20, 1),
        "volume_confirmation": latest["volume"] >= volume_average_20 and abs(body) >= (0.25 * atr_floor),
        "buy_sell_volume_imbalance": None,
    }
    volatility_features = {
        "atr_1m": atr_1m,
        "atr_pct": atr_1m / latest_close,
        "realized_volatility": pstdev(returns[-20:]) if len(returns) >= 2 else 0,
        "range_expansion": ranges[-1] / max(range_average_20, 0.000001),
        "range_compression": range_average_20 / max(ranges[-1], 0.000001),
        "current_range_vs_average": candle_range / max(dollar_range_average_20, 0.000001),
        "current_range_atr": candle_range / atr_floor,
        "spread": None,
    }
    trend_features = {
        "ema_9_slope": slope(ema9[-6:]) / latest_close,
        "ema_20_slope": slope(ema20[-6:]) / latest_close,
        "vwap_slope": vwap_slope(candles[-12:]) / latest_close,
        "adx": adx_value,
        "higher_high": len(recent_highs) >= 4 and recent_highs[-1] > max(recent_highs[:-1]),
        "higher_low": len(recent_lows) >= 4 and recent_lows[-1] > min(recent_lows[:-1]),
    }
    mean_reversion_features = {
        "distance_from_vwap_atr": price_features["distance_from_vwap_atr"],
        "distance_from_ema_20_atr": price_features["distance_from_ema_20_atr"],
        "rsi": rsi_value,
        "rsi_deviation": (rsi_value - 50) / 50,
        "bollinger_position": bollinger_position,
        "bollinger_deviation": bollinger_position - 0.5,
    }
    time_features = time_context_features(timestamp)
    regime_features = {
        "trending": adx_value >= 20 and abs(trend_features["ema_20_slope"]) > 0.00002,
        "range_bound": adx_value < 18,
        "high_volatility": volatility_features["realized_volatility"] > 0.0009 or volatility_features["range_expansion"] > 1.3,
        "low_volatility": volatility_features["realized_volatility"] < 0.00035,
        "above_vwap": latest_close >= session_vwap,
        "time_of_day_minutes": timestamp.hour * 60 + timestamp.minute if timestamp else None,
    }
    regime_profile = detect_market_regime(
        trend_features=trend_features,
        volatility_features=volatility_features,
        price_features=price_features,
        timestamp=timestamp,
    )
    regime_features.update(regime_profile_features(regime_profile))
    algorithm_features = algorithm_signal_features(
        candles,
        atr_value=atr_floor,
        regime_profile=regime_profile,
        rsi_value=rsi_value,
        adx_value=adx_value,
    )
    return {
        "price": round_nested(price_features),
        "volume": round_nested(volume_features),
        "volatility": round_nested(volatility_features),
        "trend": round_nested(trend_features),
        "mean_reversion": round_nested(mean_reversion_features),
        "algorithm": round_nested(algorithm_features),
        "regime": regime_features,
        "time": round_nested(time_features),
    }


def attach_microstructure_features(features: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    micro = microstructure_feature_values(row)
    atr_value = max(float(features["volatility"].get("atr_1m") or 0), 0.01)
    features["microstructure"] = micro
    features["volume"]["buy_sell_volume_imbalance"] = micro["buy_sell_imbalance"] if micro["has_microstructure"] else None
    features["volatility"]["spread"] = micro["avg_spread"] if micro["has_microstructure"] else None
    features["microstructure"]["avg_spread_atr"] = micro["avg_spread"] / atr_value if micro["has_microstructure"] else 0.0
    features["microstructure"]["max_spread_atr"] = micro["max_spread"] / atr_value if micro["has_microstructure"] else 0.0
    features["microstructure"]["quote_imbalance"] = quote_imbalance(micro["avg_bid_size"], micro["avg_ask_size"])
    return features


def atr_normalized_move(values: list[float], window: int, atr_value: float) -> float:
    if len(values) <= window:
        return 0.0
    return (values[-1] - values[-1 - window]) / max(atr_value, 0.01)


def relative_strength_index(values: list[float], period: int) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for index in range(len(values) - period, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    average_gain = mean(gains) if gains else 0.0
    average_loss = mean(losses) if losses else 0.0
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def bollinger_band_position(values: list[float], period: int, deviations: float) -> float:
    if len(values) < period:
        return 0.5
    window = values[-period:]
    center = mean(window)
    sigma = pstdev(window) if len(window) > 1 else 0.0
    band_width = max(sigma * deviations * 2, 0.000001)
    return clamp((values[-1] - (center - (sigma * deviations))) / band_width, -0.5, 1.5)


def time_context_features(timestamp: datetime | None) -> dict[str, Any]:
    if timestamp is None:
        return {
            "minute_from_market_open": 0.0,
            "minute_from_market_open_norm": 0.0,
            "day_of_week": 0.0,
            "day_of_week_sin": 0.0,
            "day_of_week_cos": 1.0,
            "session_opening": False,
            "session_midday": False,
            "session_power_hour": False,
        }
    local_timestamp = eastern_wall_clock(timestamp)
    minute_from_open = (local_timestamp.hour * 60 + local_timestamp.minute) - (9 * 60 + 30)
    day_of_week = float(local_timestamp.weekday())
    day_radians = (day_of_week / 5) * 2 * math.pi
    session = session_regime(timestamp)
    return {
        "minute_from_market_open": float(minute_from_open),
        "minute_from_market_open_norm": clamp(minute_from_open / 390, -1.0, 2.0),
        "day_of_week": day_of_week,
        "day_of_week_sin": math.sin(day_radians),
        "day_of_week_cos": math.cos(day_radians),
        "session_opening": session == "opening_session",
        "session_midday": session == "midday_session",
        "session_power_hour": session == "power_hour_session",
    }


def quote_imbalance(avg_bid_size: float, avg_ask_size: float) -> float:
    total = avg_bid_size + avg_ask_size
    return (avg_bid_size - avg_ask_size) / total if total > 0 else 0.0


def algorithm_signal_features(
    candles: list[dict[str, Any]],
    *,
    atr_value: float,
    regime_profile: dict[str, Any],
    rsi_value: float,
    adx_value: float,
) -> dict[str, Any]:
    votes = [
        algorithm_rsi_mean_reversion(candles, rsi_value),
        algorithm_breakout(candles, atr_value),
        algorithm_macd(candles, atr_value),
    ]
    reversal_vote = algorithm_reversal(candles, atr_value)
    family_scores = algorithm_family_scores(
        rsi_vote=votes[0],
        breakout_vote=votes[1],
        macd_vote=votes[2],
        reversal_vote=reversal_vote,
        regime_profile=regime_profile,
        adx_value=adx_value,
    )
    weighted = weighted_algorithm_scores(votes)
    buy_score = weighted["buy"]
    sell_score = weighted["sell"]
    hold_score = weighted["hold"]
    winner_score = max(buy_score, sell_score, hold_score)
    sorted_scores = sorted([buy_score, sell_score, hold_score], reverse=True)
    winner_margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) >= 2 else 0.0
    disagreement = 1.0 - winner_margin
    trend = str(regime_profile.get("trend") or "")
    rsi_vote, breakout_vote, macd_vote = votes
    return {
        "algorithm_1_signal": signal_value(rsi_vote["signal"]),
        "algorithm_1_confidence": rsi_vote["confidence"],
        "algorithm_2_signal": signal_value(breakout_vote["signal"]),
        "algorithm_2_confidence": breakout_vote["confidence"],
        "algorithm_3_signal": signal_value(macd_vote["signal"]),
        "algorithm_3_confidence": macd_vote["confidence"],
        "rsi_mean_reversion_signal": signal_value(rsi_vote["signal"]),
        "rsi_mean_reversion_confidence": rsi_vote["confidence"],
        "breakout_signal": signal_value(breakout_vote["signal"]),
        "breakout_confidence": breakout_vote["confidence"],
        "macd_signal": signal_value(macd_vote["signal"]),
        "macd_confidence": macd_vote["confidence"],
        "reversal_signal": signal_value(reversal_vote["signal"]),
        "reversal_confidence": reversal_vote["confidence"],
        **family_scores,
        "weighted_buy_score": buy_score,
        "weighted_sell_score": sell_score,
        "weighted_hold_score": hold_score,
        "buy_minus_sell_score": buy_score - sell_score,
        "winner_score_margin": winner_margin,
        "algorithm_disagreement": disagreement,
        "buy_vote_count": sum(1 for vote in votes if vote["signal"] == "Buy"),
        "sell_vote_count": sum(1 for vote in votes if vote["signal"] == "Sell"),
        "hold_vote_count": sum(1 for vote in votes if vote["signal"] == "Hold"),
        "all_algorithms_disagree": len({vote["signal"] for vote in votes}) == 3,
        "breakout_volume_confirmation_agree": breakout_vote["signal"] == "Buy" and breakout_vote["confidence"] >= 0.55,
        "rsi_buy_in_downtrend": rsi_vote["signal"] == "Buy" and trend in {"strong_downtrend", "weak_downtrend"},
        "rsi_sell_in_uptrend": rsi_vote["signal"] == "Sell" and trend in {"strong_uptrend", "weak_uptrend"},
        "mean_reversion_sideways_alignment": trend == "sideways" and rsi_vote["signal"] != "Hold",
        "trend_confirmation_alignment": breakout_vote["signal"] == macd_vote["signal"] and breakout_vote["signal"] != "Hold" and adx_value >= 20,
    }


def algorithm_rsi_mean_reversion(candles: list[dict[str, Any]], rsi_value: float) -> dict[str, Any]:
    if len(candles) <= 14:
        return {"signal": "Hold", "confidence": 0.0}
    if rsi_value <= 30:
        return {"signal": "Buy", "confidence": clamp((30 - rsi_value) / 30, 0.15, 1.0)}
    if rsi_value >= 70:
        return {"signal": "Sell", "confidence": clamp((rsi_value - 70) / 30, 0.15, 1.0)}
    return {"signal": "Hold", "confidence": clamp(1 - abs(rsi_value - 50) / 20, 0.0, 0.5)}


def algorithm_breakout(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    if len(candles) < 21:
        return {"signal": "Hold", "confidence": 0.0}
    latest = candles[-1]
    prior = candles[-21:-1]
    prior_high = max(float(candle["high"]) for candle in prior)
    prior_low = min(float(candle["low"]) for candle in prior)
    latest_close = float(latest["close"])
    if latest_close > prior_high:
        return {"signal": "Buy", "confidence": clamp((latest_close - prior_high) / max(atr_value, 0.01), 0.15, 1.0)}
    if latest_close < prior_low:
        return {"signal": "Sell", "confidence": clamp((prior_low - latest_close) / max(atr_value, 0.01), 0.15, 1.0)}
    range_width = max(prior_high - prior_low, 0.01)
    range_position = (latest_close - prior_low) / range_width
    return {"signal": "Hold", "confidence": clamp(1 - abs(range_position - 0.5) * 2, 0.0, 0.75)}


def algorithm_macd(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles]
    macd = macd_snapshot(closes)
    if macd is None:
        return {"signal": "Hold", "confidence": 0.0}
    confidence = clamp(abs(macd["histogram"]) / max(atr_value, 0.01), 0.05, 1.0)
    if macd["macd"] > macd["signal"] and macd["histogram"] > 0:
        return {"signal": "Buy", "confidence": confidence}
    if macd["macd"] < macd["signal"] and macd["histogram"] < 0:
        return {"signal": "Sell", "confidence": confidence}
    return {"signal": "Hold", "confidence": clamp(1 - confidence, 0.0, 0.5)}


def algorithm_reversal(candles: list[dict[str, Any]], atr_value: float) -> dict[str, Any]:
    if len(candles) < 21:
        return {"signal": "Hold", "confidence": 0.0}
    latest = candles[-1]
    prior = candles[-21:-1]
    prior_high = max(float(candle["high"]) for candle in prior)
    prior_low = min(float(candle["low"]) for candle in prior)
    close = float(latest["close"])
    failed_high = float(latest["high"]) > prior_high and close < prior_high
    failed_low = float(latest["low"]) < prior_low and close > prior_low
    if failed_high:
        confidence = clamp((float(latest["high"]) - close) / max(atr_value, 0.01), 0.15, 1.0)
        return {"signal": "Sell", "confidence": confidence}
    if failed_low:
        confidence = clamp((close - float(latest["low"])) / max(atr_value, 0.01), 0.15, 1.0)
        return {"signal": "Buy", "confidence": confidence}
    return {"signal": "Hold", "confidence": 0.0}


def algorithm_family_scores(
    *,
    rsi_vote: dict[str, Any],
    breakout_vote: dict[str, Any],
    macd_vote: dict[str, Any],
    reversal_vote: dict[str, Any],
    regime_profile: dict[str, Any],
    adx_value: float,
) -> dict[str, float]:
    scores = {
        "trend_buy_score": 0.0,
        "trend_sell_score": 0.0,
        "breakout_buy_score": 0.0,
        "breakout_sell_score": 0.0,
        "mean_reversion_buy_score": 0.0,
        "mean_reversion_sell_score": 0.0,
        "reversal_buy_score": 0.0,
        "reversal_sell_score": 0.0,
        "confirmation_score": 0.0,
        "regime_score": 0.0,
    }

    def add_vote(prefix: str, vote: dict[str, Any]) -> None:
        signal = str(vote.get("signal") or "Hold")
        confidence = float(vote.get("confidence") or 0.0)
        if signal == "Buy":
            scores[f"{prefix}_buy_score"] += confidence
        elif signal == "Sell":
            scores[f"{prefix}_sell_score"] += confidence

    add_vote("trend", macd_vote)
    add_vote("breakout", breakout_vote)
    add_vote("mean_reversion", rsi_vote)
    add_vote("reversal", reversal_vote)

    signed_votes = [
        signal_value(vote["signal"]) * float(vote.get("confidence") or 0.0)
        for vote in [rsi_vote, breakout_vote, macd_vote, reversal_vote]
        if str(vote.get("signal") or "Hold") != "Hold"
    ]
    confirmation = (sum(signed_votes) / len(signed_votes)) if signed_votes else 0.0
    if breakout_vote["signal"] == macd_vote["signal"] and breakout_vote["signal"] != "Hold" and adx_value >= 20:
        confirmation += 0.25 * signal_value(str(breakout_vote["signal"]))
    scores["confirmation_score"] = clamp(confirmation, -1.0, 1.0)

    trend = str(regime_profile.get("trend") or "")
    if trend == "strong_uptrend":
        scores["regime_score"] = 0.8
    elif trend == "weak_uptrend":
        scores["regime_score"] = 0.45
    elif trend == "strong_downtrend":
        scores["regime_score"] = -0.8
    elif trend == "weak_downtrend":
        scores["regime_score"] = -0.45

    return {key: round(value, 6) for key, value in scores.items()}


def weighted_algorithm_scores(votes: list[dict[str, Any]]) -> dict[str, float]:
    scores = {"buy": 0.0, "sell": 0.0, "hold": 0.0}
    if not votes:
        return {"buy": 0.0, "sell": 0.0, "hold": 1.0}
    for vote in votes:
        confidence = float(vote.get("confidence") or 0.0)
        signal = str(vote.get("signal") or "Hold")
        if signal == "Buy":
            scores["buy"] += confidence
            scores["hold"] += 1 - confidence
        elif signal == "Sell":
            scores["sell"] += confidence
            scores["hold"] += 1 - confidence
        else:
            scores["hold"] += max(confidence, 0.5)
    total = sum(scores.values()) or 1
    return {key: value / total for key, value in scores.items()}


def signal_value(signal: str) -> float:
    if signal == "Buy":
        return 1.0
    if signal == "Sell":
        return -1.0
    return 0.0


def macd_snapshot(values: list[float]) -> dict[str, float] | None:
    if len(values) < 35:
        return None
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    macd_line = [fast - slow for fast, slow in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "histogram": macd_line[-1] - signal_line[-1],
    }


def detect_market_regime(
    *,
    trend_features: dict[str, Any],
    volatility_features: dict[str, Any],
    price_features: dict[str, Any],
    timestamp: datetime | None,
) -> dict[str, Any]:
    trend_slope = float(trend_features["ema_20_slope"])
    vwap_slope_value = float(trend_features["vwap_slope"])
    adx_value = float(trend_features["adx"])
    above_vwap = float(price_features["distance_from_vwap"]) >= 0
    high_volatility = bool(volatility_features["realized_volatility"] > 0.0009 or volatility_features["range_expansion"] > 1.3)
    low_volatility = bool(volatility_features["realized_volatility"] < 0.00035)

    if adx_value >= 25 and trend_slope > 0.00003 and vwap_slope_value >= 0 and above_vwap:
        trend = "strong_uptrend"
    elif trend_slope > 0.00001 and above_vwap:
        trend = "weak_uptrend"
    elif adx_value >= 25 and trend_slope < -0.00003 and vwap_slope_value <= 0 and not above_vwap:
        trend = "strong_downtrend"
    elif trend_slope < -0.00001 and not above_vwap:
        trend = "weak_downtrend"
    else:
        trend = "sideways"

    if high_volatility:
        volatility = "high_volatility"
    elif low_volatility:
        volatility = "low_volatility"
    else:
        volatility = "normal_volatility"

    session = session_regime(timestamp)
    threshold_adjustment = 0.0
    position_size_multiplier = 1.0
    notes: list[str] = []
    if volatility == "high_volatility":
        threshold_adjustment += HIGH_VOLATILITY_THRESHOLD_ADJUSTMENT
        position_size_multiplier *= HIGH_VOLATILITY_POSITION_SIZE_MULTIPLIER
        notes.append("High volatility: require higher confidence and reduce size")
    if trend == "sideways":
        threshold_adjustment += SIDEWAYS_THRESHOLD_ADJUSTMENT
        position_size_multiplier *= SIDEWAYS_POSITION_SIZE_MULTIPLIER
        notes.append("Sideways regime: directional trend edges need stronger confirmation")
    if volatility == "low_volatility":
        notes.append("Low volatility: quiet tape increases timeout/no-trade risk")
    if session == "opening_session":
        notes.append("Opening session: breakout signals can work but noise is elevated")
    if session == "midday_session":
        notes.append("Midday session: reduce chase behavior and prefer mean reversion")
    if session == "power_hour_session":
        notes.append("Power hour: late-session directional flows can dominate")

    return {
        "trend": trend,
        "volatility": volatility,
        "session": session,
        "allowedLong": trend not in {"strong_downtrend", "weak_downtrend"},
        "allowedShort": trend not in {"strong_uptrend", "weak_uptrend"},
        "thresholdAdjustment": round(threshold_adjustment, 4),
        "positionSizeMultiplier": round(position_size_multiplier, 4),
        "notes": notes,
    }


def regime_profile_features(profile: dict[str, Any]) -> dict[str, Any]:
    trend = str(profile.get("trend") or "sideways")
    volatility = str(profile.get("volatility") or "normal_volatility")
    session = str(profile.get("session") or "unknown_session")
    return {
        "trend_strong_uptrend": trend == "strong_uptrend",
        "trend_weak_uptrend": trend == "weak_uptrend",
        "trend_strong_downtrend": trend == "strong_downtrend",
        "trend_weak_downtrend": trend == "weak_downtrend",
        "trend_sideways": trend == "sideways",
        "volatility_low": volatility == "low_volatility",
        "volatility_normal": volatility == "normal_volatility",
        "volatility_high": volatility == "high_volatility",
        "session_opening": session == "opening_session",
        "session_midday": session == "midday_session",
        "session_power_hour": session == "power_hour_session",
        "session_other": session == "other_session",
        "regime_threshold_adjustment": float(profile.get("thresholdAdjustment") or 0.0),
        "regime_position_size_multiplier": float(profile.get("positionSizeMultiplier") or 1.0),
    }


def session_regime(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "unknown_session"
    timestamp = eastern_wall_clock(timestamp)
    minutes = timestamp.hour * 60 + timestamp.minute
    opening_windows = [(9 * 60 + 30, 10 * 60 + 30)]
    midday_windows = [(11 * 60, 14 * 60)]
    power_hour_windows = [(15 * 60, 16 * 60)]
    if any(start <= minutes < end for start, end in opening_windows):
        return "opening_session"
    if any(start <= minutes < end for start, end in midday_windows):
        return "midday_session"
    if any(start <= minutes <= end for start, end in power_hour_windows):
        return "power_hour_session"
    return "other_session"


def eastern_wall_clock(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp
    return timestamp.astimezone(UTC).replace(tzinfo=None) + timedelta(hours=eastern_utc_offset_hours(timestamp))


def eastern_utc_offset_hours(timestamp: datetime) -> int:
    utc_timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
    year = utc_timestamp.year
    dst_start = nth_weekday_of_month(year, 3, 6, 2).replace(hour=7)
    dst_end = nth_weekday_of_month(year, 11, 6, 1).replace(hour=6)
    return -4 if dst_start <= utc_timestamp < dst_end else -5


def nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> datetime:
    cursor = datetime(year, month, 1)
    days_until_weekday = (weekday - cursor.weekday()) % 7
    return cursor + timedelta(days=days_until_weekday + ((occurrence - 1) * 7))


def market_regime_profile(features: dict[str, Any]) -> dict[str, Any]:
    regime = features.get("regime") or {}
    trend = "sideways"
    if regime.get("trend_strong_uptrend"):
        trend = "strong_uptrend"
    elif regime.get("trend_weak_uptrend"):
        trend = "weak_uptrend"
    elif regime.get("trend_strong_downtrend"):
        trend = "strong_downtrend"
    elif regime.get("trend_weak_downtrend"):
        trend = "weak_downtrend"

    volatility = "normal_volatility"
    if regime.get("volatility_high"):
        volatility = "high_volatility"
    elif regime.get("volatility_low"):
        volatility = "low_volatility"

    session = "other_session"
    if regime.get("session_opening"):
        session = "opening_session"
    elif regime.get("session_midday"):
        session = "midday_session"
    elif regime.get("session_power_hour"):
        session = "power_hour_session"

    return {
        "trend": trend,
        "volatility": volatility,
        "session": session,
        "allowedLong": trend not in {"strong_downtrend", "weak_downtrend"},
        "allowedShort": trend not in {"strong_uptrend", "weak_uptrend"},
        "thresholdAdjustment": round(float(regime.get("regime_threshold_adjustment") or 0.0), 4),
        "positionSizeMultiplier": round(float(regime.get("regime_position_size_multiplier") or 1.0), 4),
        "notes": regime_notes(trend, volatility, session),
    }


def regime_notes(trend: str, volatility: str, session: str) -> list[str]:
    notes: list[str] = []
    if trend == "sideways":
        notes.append("Sideways regime: directional trend edges need stronger confirmation")
    if volatility == "high_volatility":
        notes.append("High volatility: require higher confidence and reduce size")
    if volatility == "low_volatility":
        notes.append("Low volatility: quiet tape increases timeout/no-trade risk")
    if session == "opening_session":
        notes.append("Opening session: breakout signals can work but noise is elevated")
    elif session == "midday_session":
        notes.append("Midday session: reduce chase behavior and prefer mean reversion")
    elif session == "power_hour_session":
        notes.append("Power hour: late-session directional flows can dominate")
    return notes


def microstructure_feature_values(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "has_microstructure": False,
            "avg_spread": 0.0,
            "avg_spread_pct": 0.0,
            "min_spread": 0.0,
            "max_spread": 0.0,
            "quote_count": 0.0,
            "avg_bid_size": 0.0,
            "avg_ask_size": 0.0,
            "trade_count": 0.0,
            "trade_volume": 0.0,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "buy_sell_imbalance": 0.0,
        }
    return {
        "has_microstructure": True,
        "avg_spread": numeric(row.get("avg_spread")),
        "avg_spread_pct": numeric(row.get("avg_spread_pct")),
        "min_spread": numeric(row.get("min_spread")),
        "max_spread": numeric(row.get("max_spread")),
        "quote_count": numeric(row.get("quote_count")),
        "avg_bid_size": numeric(row.get("avg_bid_size")),
        "avg_ask_size": numeric(row.get("avg_ask_size")),
        "trade_count": numeric(row.get("trade_count")),
        "trade_volume": numeric(row.get("trade_volume")),
        "buy_volume": numeric(row.get("buy_volume")),
        "sell_volume": numeric(row.get("sell_volume")),
        "buy_sell_imbalance": numeric(row.get("buy_sell_imbalance")),
    }


def load_microstructure_rows_for_candles(symbol: str, feed: str, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timestamps = [str(row.get("timestamp") or "") for row in candles if row.get("timestamp")]
    dates = sorted({timestamp[:10] for timestamp in timestamps if len(timestamp) >= 10})
    rows: list[dict[str, Any]] = []
    for day in dates:
        path = MICROSTRUCTURE_DIR / symbol.upper() / feed / day / "one_minute_microstructure.jsonl"
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(rows, key=lambda row: str(row.get("timestamp") or ""))


def latest_microstructure_for_candle(candle: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    candle_timestamp = str(candle.get("timestamp") or "")
    exact = {str(row.get("timestamp") or ""): row for row in rows}
    if candle_timestamp in exact:
        return exact[candle_timestamp]
    candidates = [row for row in rows if str(row.get("timestamp") or "") <= candle_timestamp]
    return candidates[-1] if candidates else None


def fallback_probabilities(features: dict[str, Any]) -> dict[str, float]:
    price = features["price"]
    volume = features["volume"]
    volatility = features["volatility"]
    trend = features["trend"]
    regime = features["regime"]
    score = 0.0
    score += clamp(price["return_1m"] * 150, -0.35, 0.35)
    score += clamp(price["return_5m"] * 90, -0.45, 0.45)
    score += clamp(price["distance_from_vwap"] * 45, -0.35, 0.35)
    score += clamp(price["distance_from_ema_9"] * 55, -0.35, 0.35)
    score += clamp(trend["ema_9_slope"] * 700, -0.25, 0.25)
    score += clamp(trend["vwap_slope"] * 500, -0.2, 0.2)
    score += 0.08 if trend["higher_high"] and trend["higher_low"] else 0
    score += 0.06 if volume["volume_spike_ratio"] >= 1.2 else 0
    score -= 0.08 if volatility["range_expansion"] > 1.8 else 0
    score -= 0.08 if regime["range_bound"] and abs(price["distance_from_vwap"]) < 0.0004 else 0
    score -= 0.05 if regime["low_volatility"] else 0
    target_score = score
    stop_score = -score + clamp(volatility["range_expansion"] - 1.0, -0.25, 0.35)
    timeout_score = 0.25
    timeout_score += 0.35 if regime["range_bound"] else 0
    timeout_score += 0.2 if regime["low_volatility"] else 0
    timeout_score -= 0.2 if volume["volume_spike_ratio"] >= 1.4 else 0
    return softmax_scores({OUTCOME_STOP: stop_score, OUTCOME_TIMEOUT: timeout_score, OUTCOME_TARGET: target_score})


def expected_success_move(features: dict[str, Any], latest_close: float) -> float:
    atr_value = features["volatility"]["atr_1m"]
    realized = features["volatility"]["realized_volatility"] * latest_close * math.sqrt(FORECAST_HORIZON_MINUTES)
    return max(0.05, atr_value, realized)


def volatility_adjusted_barriers(features: dict[str, Any], latest_close: float, *, artifact: dict[str, Any] | None = None) -> dict[str, float]:
    label_config = (artifact or {}).get("label") or {}
    configured_fixed_target = numeric(label_config.get("profitTargetDollars"))
    fixed_target = max(configured_fixed_target, DEFAULT_PROFIT_TARGET_DOLLARS)
    fixed_stop = numeric(label_config.get("stopLossDollars"))
    min_target_pct = numeric(label_config.get("minTargetPct")) if configured_fixed_target >= DEFAULT_PROFIT_TARGET_DOLLARS else DEFAULT_MIN_TARGET_PCT
    min_stop_pct = numeric(label_config.get("minStopPct")) or DEFAULT_MIN_STOP_PCT
    target_multiplier = numeric(label_config.get("targetAtrMultiplier")) or DEFAULT_TARGET_ATR_MULTIPLIER
    stop_multiplier = numeric(label_config.get("stopAtrMultiplier")) or DEFAULT_STOP_ATR_MULTIPLIER
    atr_value = float(features["volatility"]["atr_1m"])
    realized = float(features["volatility"]["realized_volatility"]) * latest_close * math.sqrt(FORECAST_HORIZON_MINUTES)
    atr_5m = max(atr_value * math.sqrt(FORECAST_HORIZON_MINUTES), realized, 0.01)
    return {
        "targetDistance": max(fixed_target, latest_close * min_target_pct, atr_5m * target_multiplier),
        "stopDistance": max(fixed_stop, latest_close * min_stop_pct, atr_5m * stop_multiplier),
        "minTargetPct": min_target_pct,
        "minStopPct": min_stop_pct,
        "targetAtrMultiplier": target_multiplier,
        "stopAtrMultiplier": stop_multiplier,
        "fixedTargetDollars": fixed_target,
        "fixedStopDollars": fixed_stop,
        "atr5m": atr_5m,
    }


def forecast_future_price_prediction(
    features: dict[str, Any],
    latest_close: float,
    *,
    probabilities: dict[str, float],
    barriers: dict[str, float],
    market_regime: dict[str, Any],
) -> dict[str, Any]:
    algorithm = features.get("algorithm") or {}
    trend = features.get("trend") or {}
    mean_reversion = features.get("mean_reversion") or {}
    volatility = features.get("volatility") or {}
    regime = features.get("regime") or {}

    probability_component = clamp(
        float(probabilities.get(OUTCOME_TARGET) or 0) - float(probabilities.get(OUTCOME_STOP) or 0),
        -1.0,
        1.0,
    )
    algorithm_component = clamp(float(algorithm.get("weighted_buy_score") or 0) - float(algorithm.get("weighted_sell_score") or 0), -1.0, 1.0)
    trend_component = clamp(
        (
            (float(trend.get("ema_9_slope") or 0) * 650)
            + (float(trend.get("ema_20_slope") or 0) * 900)
            + (float(trend.get("vwap_slope") or 0) * 600)
        ),
        -1.0,
        1.0,
    )
    if trend.get("higher_high"):
        trend_component += 0.08
    if trend.get("higher_low"):
        trend_component += 0.06
    trend_component = clamp(trend_component, -1.0, 1.0)

    mean_reversion_component = clamp(
        (
            -float(mean_reversion.get("distance_from_vwap_atr") or 0) * 0.12
            -float(mean_reversion.get("rsi_deviation") or 0) * 0.18
            -float(mean_reversion.get("bollinger_deviation") or 0) * 0.16
        ),
        -1.0,
        1.0,
    )
    if not regime.get("range_bound"):
        mean_reversion_component *= 0.45

    regime_trend = str(market_regime.get("trend") or "")
    regime_component = {
        "strong_uptrend": 0.28,
        "weak_uptrend": 0.14,
        "strong_downtrend": -0.28,
        "weak_downtrend": -0.14,
    }.get(regime_trend, 0.0)

    session = str(market_regime.get("session") or "")
    session_multiplier = 0.8 if session == "opening_session" else 0.9 if session == "midday_session" else 1.0
    volatility_multiplier = 0.72 if market_regime.get("volatility") == "high_volatility" else 0.55 if market_regime.get("volatility") == "low_volatility" else 0.85
    timeout_probability = float(probabilities.get(OUTCOME_TIMEOUT) or 0)
    confidence_multiplier = clamp(1.0 - (timeout_probability * 0.55), 0.35, 1.0)

    directional_score = clamp(
        (probability_component * 0.34)
        + (algorithm_component * 0.24)
        + (trend_component * 0.22)
        + (mean_reversion_component * 0.12)
        + (regime_component * 0.08),
        -1.0,
        1.0,
    )
    atr_5m = max(float(barriers.get("atr5m") or 0), float(volatility.get("atr_1m") or 0), latest_close * 0.0002, 0.01)
    move_scale = atr_5m * session_multiplier * volatility_multiplier * confidence_multiplier
    predicted_change = clamp(directional_score * move_scale, -atr_5m, atr_5m)
    predicted_price = max(0.01, latest_close + predicted_change)
    return {
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "predictedPrice": round(predicted_price, 4),
        "predictedChangeDollars": round(predicted_change, 4),
        "predictedReturnPct": round(safe_return(predicted_price, latest_close), 6),
        "direction": price_direction(predicted_change),
        "directionalScore": round(directional_score, 4),
        "moveScale": round(move_scale, 4),
        "components": {
            "probability": round(probability_component, 4),
            "algorithmStrategies": round(algorithm_component, 4),
            "trendIndicators": round(trend_component, 4),
            "meanReversionIndicators": round(mean_reversion_component, 4),
            "regimeSession": round(regime_component, 4),
            "timeoutProbability": round(timeout_probability, 4),
            "sessionMultiplier": round(session_multiplier, 4),
            "volatilityMultiplier": round(volatility_multiplier, 4),
            "confidenceMultiplier": round(confidence_multiplier, 4),
        },
        "basis": "5-minute expected close from probabilities, trend indicators, strategy scores, regime, volatility, and session.",
    }


def no_edge_future_price_prediction(latest_close: float, reason: str) -> dict[str, Any]:
    latest_close = max(numeric(latest_close), 0.01)
    return {
        "horizonMinutes": FORECAST_HORIZON_MINUTES,
        "predictedPrice": round(latest_close, 4),
        "predictedChangeDollars": 0.0,
        "predictedReturnPct": 0.0,
        "direction": "flat",
        "directionalScore": 0.0,
        "moveScale": 0.0,
        "components": {
            "probability": 0.0,
            "algorithmStrategies": 0.0,
            "trendIndicators": 0.0,
            "meanReversionIndicators": 0.0,
            "regimeSession": 0.0,
            "timeoutProbability": 1.0,
            "sessionMultiplier": 0.0,
            "volatilityMultiplier": 0.0,
            "confidenceMultiplier": 0.0,
        },
        "basis": f"5-minute neutral expected close. {reason}.",
    }


def regime_allows_forecast(features: dict[str, Any], market_regime: dict[str, Any]) -> bool:
    regime = features["regime"]
    volatility = features["volatility"]
    return (
        not (regime["range_bound"] and regime["low_volatility"])
        and volatility["range_expansion"] < 2.5
        and float(market_regime.get("positionSizeMultiplier") or 0) > 0
    )


def forecast_probability_threshold(artifact: dict[str, Any] | None) -> float:
    if not artifact:
        return DEFAULT_SUCCESS_THRESHOLD
    try:
        return max(DEFAULT_SUCCESS_THRESHOLD, float(artifact.get("threshold") or DEFAULT_SUCCESS_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_SUCCESS_THRESHOLD


def forecast_trade_decision(
    probabilities: dict[str, float],
    *,
    buy_expected_value: float,
    sell_expected_value: float,
    regime_allows: bool,
    market_regime: dict[str, Any],
    uncertainty: dict[str, Any],
    features: dict[str, Any],
    base_threshold: float,
) -> dict[str, Any]:
    buy_probability = probabilities[OUTCOME_TARGET]
    sell_probability = probabilities[OUTCOME_STOP]
    timeout_probability = probabilities[OUTCOME_TIMEOUT]
    if buy_probability >= sell_probability:
        candidate_action = DECISION_BUY
        confidence = buy_probability
        expected_value = buy_expected_value
    else:
        candidate_action = DECISION_SELL
        confidence = sell_probability
        expected_value = sell_expected_value

    edge_gap = abs(buy_probability - sell_probability)
    minimum_confidence = base_threshold + float(market_regime.get("thresholdAdjustment") or 0.0)
    minimum_edge_gap = DEFAULT_MIN_EDGE_GAP + (0.03 if market_regime.get("trend") == "sideways" else 0.0)
    spread_atr = float((features.get("microstructure") or {}).get("avg_spread_atr") or 0.0)
    reasons: list[str] = []
    if candidate_action == DECISION_BUY and not market_regime.get("allowedLong", True):
        reasons.append(f"Regime {market_regime.get('trend')} blocks long/buy predictions")
    if candidate_action == DECISION_SELL and not market_regime.get("allowedShort", True):
        reasons.append(f"Regime {market_regime.get('trend')} blocks short/sell predictions")
    if confidence < minimum_confidence:
        reasons.append(f"Top side confidence {confidence:.1%} is below regime-adjusted {minimum_confidence:.0%}")
    if edge_gap < minimum_edge_gap:
        reasons.append(f"Buy/sell edge gap {edge_gap:.1%} is below regime-adjusted {minimum_edge_gap:.0%}")
    model_disagreement = uncertainty.get("modelDisagreement")
    if model_disagreement is not None and float(model_disagreement) > DEFAULT_MAX_MODEL_DISAGREEMENT:
        reasons.append(f"Model disagreement {float(model_disagreement):.1%} is above {DEFAULT_MAX_MODEL_DISAGREEMENT:.0%}")
    if timeout_probability >= confidence:
        reasons.append("Timeout/no-edge probability is the strongest class")
    if expected_value <= 0:
        reasons.append(f"Expected value {expected_value:+.2f}/share is not positive")
    if spread_atr > DEFAULT_MAX_SPREAD_ATR:
        reasons.append(f"Spread/ATR {spread_atr:.1%} is above {DEFAULT_MAX_SPREAD_ATR:.0%}")
    if not regime_allows:
        reasons.append("Regime filter blocks forecast edge")

    action = DECISION_NO_TRADE if reasons else candidate_action
    return {
        "action": action,
        "candidateAction": candidate_action,
        "confidence": round(confidence, 4),
        "edgeGap": round(edge_gap, 4),
        "minimumConfidence": round(minimum_confidence, 4),
        "minimumEdgeGap": round(minimum_edge_gap, 4),
        "modelDisagreement": round(float(model_disagreement), 4) if model_disagreement is not None else None,
        "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
        "spreadAtr": round(spread_atr, 4),
        "maximumSpreadAtr": DEFAULT_MAX_SPREAD_ATR,
        "expectedValue": expected_value if action != DECISION_NO_TRADE else max(buy_expected_value, sell_expected_value),
        "positionSizeMultiplier": float(market_regime.get("positionSizeMultiplier") or 0.0) if action != DECISION_NO_TRADE else 0.0,
        "reasons": reasons or [f"{candidate_action.title()} edge passed confidence, gap, expectancy, and regime gates"],
    }


def forecast_regime(features: dict[str, Any]) -> dict[str, str]:
    regime = features["regime"]
    trend = "trending" if regime["trending"] else "range-bound" if regime["range_bound"] else "mixed"
    volatility = "high" if regime["high_volatility"] else "low" if regime["low_volatility"] else "normal"
    vwap = "above VWAP" if regime["above_vwap"] else "below VWAP"
    minutes = regime["time_of_day_minutes"]
    if minutes is None:
        time_of_day = "unknown"
    elif minutes < 10 * 60 + 30:
        time_of_day = "opening"
    elif minutes < 12 * 60:
        time_of_day = "morning"
    elif minutes < 14 * 60:
        time_of_day = "midday"
    else:
        time_of_day = "afternoon"
    return {"trend": trend, "volatility": volatility, "vwap": vwap, "timeOfDay": time_of_day}


def forecast_drivers(features: dict[str, Any], probabilities: dict[str, float], decision: dict[str, Any], regime_allows: bool) -> list[str]:
    price = features["price"]
    volume = features["volume"]
    trend = features["trend"]
    algorithm = features.get("algorithm") or {}
    market_regime = market_regime_profile(features)
    drivers = [
        f"Decision {str(decision['action']).replace('_', ' ')}",
        f"Regime {str(market_regime['trend']).replace('_', ' ')}, {str(market_regime['volatility']).replace('_', ' ')}, {str(market_regime['session']).replace('_', ' ')}",
        f"Algo scores B/S/H {float(algorithm.get('weighted_buy_score') or 0):.2f}/{float(algorithm.get('weighted_sell_score') or 0):.2f}/{float(algorithm.get('weighted_hold_score') or 0):.2f}",
        f"P(buy success) {probabilities[OUTCOME_TARGET]:.1%}",
        f"P(sell success) {probabilities[OUTCOME_STOP]:.1%}",
        f"P(timeout/no edge) {probabilities[OUTCOME_TIMEOUT]:.1%}",
        f"Edge gap {float(decision['edgeGap'] or 0):.1%}",
        f"Model disagreement {float(decision.get('modelDisagreement') or 0):.1%}",
        f"Expected value {float(decision['expectedValue']):+.2f}/share after costs",
        f"VWAP distance {price['distance_from_vwap']:+.3%}",
        f"EMA9 slope {trend['ema_9_slope']:+.3%}",
        f"Volume {volume['volume_vs_20_bar_average']:.2f}x 20-bar average",
    ]
    if not regime_allows:
        drivers.append("Regime filter blocks forecast edge")
    if decision.get("action") == DECISION_NO_TRADE:
        drivers.extend(str(reason) for reason in decision.get("reasons", [])[:2])
    return drivers


def algorithm_signal_summary(features: dict[str, Any]) -> dict[str, Any]:
    algorithm = features.get("algorithm") or {}
    return {
        "rsiMeanReversion": {
            "signal": signal_label(float(algorithm.get("rsi_mean_reversion_signal") or 0)),
            "confidence": algorithm.get("rsi_mean_reversion_confidence"),
        },
        "breakout": {
            "signal": signal_label(float(algorithm.get("breakout_signal") or 0)),
            "confidence": algorithm.get("breakout_confidence"),
        },
        "macd": {
            "signal": signal_label(float(algorithm.get("macd_signal") or 0)),
            "confidence": algorithm.get("macd_confidence"),
        },
        "weightedScores": {
            "buy": algorithm.get("weighted_buy_score"),
            "sell": algorithm.get("weighted_sell_score"),
            "hold": algorithm.get("weighted_hold_score"),
            "buyMinusSell": algorithm.get("buy_minus_sell_score"),
            "winnerMargin": algorithm.get("winner_score_margin"),
        },
        "familyScores": {
            "trend_buy_score": algorithm.get("trend_buy_score"),
            "trend_sell_score": algorithm.get("trend_sell_score"),
            "breakout_buy_score": algorithm.get("breakout_buy_score"),
            "breakout_sell_score": algorithm.get("breakout_sell_score"),
            "mean_reversion_buy_score": algorithm.get("mean_reversion_buy_score"),
            "mean_reversion_sell_score": algorithm.get("mean_reversion_sell_score"),
            "reversal_buy_score": algorithm.get("reversal_buy_score"),
            "reversal_sell_score": algorithm.get("reversal_sell_score"),
            "confirmation_score": algorithm.get("confirmation_score"),
            "regime_score": algorithm.get("regime_score"),
        },
        "disagreement": algorithm.get("algorithm_disagreement"),
    }


def signal_label(value: float) -> str:
    if value > 0:
        return "Buy"
    if value < 0:
        return "Sell"
    return "Hold"


def market_forecast_artifact_path(symbol: str) -> Path:
    return MODEL_ARTIFACT_DIR / f"{MODEL_VERSION}_{symbol.upper()}.json"


def load_market_forecast_artifact(symbol: str) -> dict[str, Any] | None:
    path = market_forecast_artifact_path(symbol)
    if not path.exists():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if artifact.get("version") != MODEL_VERSION:
        return None
    model_kind = str(artifact.get("modelKind") or "")
    if model_kind == "xgboost":
        model_file = artifact.get("modelFile")
        feature_names = artifact.get("featureNames")
        if not model_file or not isinstance(feature_names, list):
            return None
        if not Path(str(model_file)).exists():
            return None
        return artifact
    if not isinstance(artifact.get("weights"), dict) and not isinstance(artifact.get("weightsByClass"), dict):
        return None
    return artifact


def flatten_forecast_features(features: dict[str, Any]) -> dict[str, float]:
    flattened: dict[str, float] = {"bias": 1.0}
    for group_name, group_values in features.items():
        if not isinstance(group_values, dict):
            continue
        for key, value in group_values.items():
            feature_key = f"{group_name}.{key}"
            if feature_key in SKIPPED_RAW_FEATURES:
                continue
            if isinstance(value, bool):
                flattened[feature_key] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                flattened[feature_key] = float(value)
    minutes = flattened.pop("regime.time_of_day_minutes", None)
    if minutes is not None:
        radians = (minutes / 1440) * 2 * math.pi
        flattened["regime.time_sin"] = math.sin(radians)
        flattened["regime.time_cos"] = math.cos(radians)
    return flattened


def model_probability(features: dict[str, Any], artifact: dict[str, Any]) -> float:
    return model_probabilities(features, artifact)[OUTCOME_TARGET]


def fallback_ensemble_probabilities(features: dict[str, Any]) -> dict[str, Any]:
    probabilities = fallback_probabilities(features)
    return uncertainty_summary([{"name": "fallback", "probabilities": probabilities}])


def ensemble_probabilities(features: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    members = [{"name": str(artifact.get("modelKind") or "primary"), "probabilities": model_probabilities(features, artifact)}]
    for member in artifact.get("uncertaintyEnsemble", {}).get("members", []) or []:
        probabilities = ensemble_member_probabilities(features, member)
        if probabilities:
            members.append({"name": str(member.get("name") or member.get("kind") or "member"), "probabilities": probabilities})
    return uncertainty_summary(members)


def uncertainty_summary(members: list[dict[str, Any]]) -> dict[str, Any]:
    if not members:
        probabilities = {OUTCOME_STOP: 1 / 3, OUTCOME_TIMEOUT: 1 / 3, OUTCOME_TARGET: 1 / 3}
        return {
            "probabilities": probabilities,
            "modelCount": 0,
            "modelDisagreement": None,
            "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
            "members": [],
        }
    averaged = {
        outcome: sum(float(member["probabilities"][outcome]) for member in members) / len(members)
        for outcome in OUTCOME_ORDER
    }
    side_disagreements = [
        pstdev([float(member["probabilities"][OUTCOME_TARGET]) for member in members]) if len(members) > 1 else 0.0,
        pstdev([float(member["probabilities"][OUTCOME_STOP]) for member in members]) if len(members) > 1 else 0.0,
    ]
    return {
        "probabilities": averaged,
        "modelCount": len(members),
        "modelDisagreement": round(max(side_disagreements), 4),
        "maximumModelDisagreement": DEFAULT_MAX_MODEL_DISAGREEMENT,
        "members": [
            {
                "name": member["name"],
                "buy": round(float(member["probabilities"][OUTCOME_TARGET]), 4),
                "sell": round(float(member["probabilities"][OUTCOME_STOP]), 4),
                "timeout": round(float(member["probabilities"][OUTCOME_TIMEOUT]), 4),
            }
            for member in members
        ],
    }


def ensemble_member_probabilities(features: dict[str, Any], member: dict[str, Any]) -> dict[str, float] | None:
    if member.get("kind") != "sparse-softmax-ensemble":
        return None
    raw = softmax_member_probabilities(features, member)
    return apply_probability_calibration(raw, member.get("calibration"))


def softmax_member_probabilities(features: dict[str, Any], member: dict[str, Any]) -> dict[str, float]:
    means = member.get("featureMeans") or {}
    scales = member.get("featureScales") or {}
    weights_by_class = member.get("weightsByClass") or {}
    intercepts = member.get("intercepts") or {}
    feature_names = [str(name) for name in member.get("featureNames") or []]
    model_features = flatten_forecast_features(features)
    scores: dict[str, float] = {}
    for outcome in OUTCOME_ORDER:
        weights = weights_by_class.get(outcome) or {}
        score = float(intercepts.get(outcome) or 0)
        for key in feature_names:
            value = float(model_features.get(key, 0.0))
            scale = float(scales.get(key) or 1)
            normalized = (value - float(means.get(key) or 0)) / (scale if scale > 0 else 1)
            score += float(weights.get(key) or 0) * normalized
        scores[outcome] = score
    return softmax_scores(scores)


def model_probabilities(features: dict[str, Any], artifact: dict[str, Any]) -> dict[str, float]:
    if artifact.get("modelKind") == "xgboost":
        return apply_probability_calibration(xgboost_model_probabilities(features, artifact), artifact.get("calibration"))
    if artifact.get("weightsByClass"):
        return apply_probability_calibration(softmax_model_probabilities(features, artifact), artifact.get("calibration"))
    return apply_probability_calibration(binary_model_probabilities(features, artifact), artifact.get("calibration"))


def binary_model_probabilities(features: dict[str, Any], artifact: dict[str, Any]) -> dict[str, float]:
    weights = artifact.get("weights") or {}
    means = artifact.get("featureMeans") or {}
    scales = artifact.get("featureScales") or {}
    model_features = flatten_forecast_features(features)
    z = float(artifact.get("intercept") or 0)
    for key, value in model_features.items():
        scale = float(scales.get(key) or 1)
        normalized = (value - float(means.get(key) or 0)) / (scale if scale > 0 else 1)
        z += float(weights.get(key) or 0) * normalized
    target_probability = clamp(1 / (1 + math.exp(-clamp(z, -30, 30))), 0.01, 0.99)
    remaining = 1 - target_probability
    return {
        OUTCOME_STOP: remaining * 0.5,
        OUTCOME_TIMEOUT: remaining * 0.5,
        OUTCOME_TARGET: target_probability,
    }


def softmax_model_probabilities(features: dict[str, Any], artifact: dict[str, Any]) -> dict[str, float]:
    means = artifact.get("featureMeans") or {}
    scales = artifact.get("featureScales") or {}
    weights_by_class = artifact.get("weightsByClass") or {}
    intercepts = artifact.get("intercepts") or {}
    model_features = flatten_forecast_features(features)
    scores: dict[str, float] = {}
    for outcome in OUTCOME_ORDER:
        weights = weights_by_class.get(outcome) or {}
        score = float(intercepts.get(outcome) or 0)
        for key, value in model_features.items():
            scale = float(scales.get(key) or 1)
            normalized = (value - float(means.get(key) or 0)) / (scale if scale > 0 else 1)
            score += float(weights.get(key) or 0) * normalized
        scores[outcome] = score
    return softmax_scores(scores)


def xgboost_model_probabilities(features: dict[str, Any], artifact: dict[str, Any]) -> dict[str, float]:
    try:
        import xgboost as xgb
    except ImportError:
        return fallback_probabilities(features)
    feature_names = [str(name) for name in artifact.get("featureNames") or []]
    flattened = flatten_forecast_features(features)
    values = [[float(flattened.get(name, 0.0)) for name in feature_names]]
    booster = xgb.Booster()
    booster.load_model(str(artifact["modelFile"]))
    matrix = xgb.DMatrix(values, feature_names=feature_names)
    raw = booster.predict(matrix)[0]
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        probabilities = {OUTCOME_ORDER[index]: clamp(float(raw[index]), 0.0, 1.0) for index in range(3)}
        total = sum(probabilities.values())
        if total > 0:
            return {name: value / total for name, value in probabilities.items()}
    probability = clamp(float(raw), 0.01, 0.99)
    remaining = 1 - probability
    return {OUTCOME_STOP: remaining * 0.5, OUTCOME_TIMEOUT: remaining * 0.5, OUTCOME_TARGET: probability}


def softmax_scores(scores: dict[str, float]) -> dict[str, float]:
    max_score = max(scores.values()) if scores else 0
    exp_scores = {name: math.exp(clamp(score - max_score, -30, 30)) for name, score in scores.items()}
    total = sum(exp_scores.values()) or 1
    return {name: exp_scores.get(name, 0) / total for name in OUTCOME_ORDER}


def apply_probability_calibration(probabilities: dict[str, float], calibration: Any) -> dict[str, float]:
    if not isinstance(calibration, dict) or calibration.get("method") != "per_class_platt_sigmoid":
        return probabilities
    classes = calibration.get("classes") or {}
    calibrated: dict[str, float] = {}
    for outcome in OUTCOME_ORDER:
        params = classes.get(outcome) or {}
        slope = float(params.get("slope", 1.0))
        intercept = float(params.get("intercept", 0.0))
        z = (slope * probability_logit(clamp_probability(float(probabilities[outcome])))) + intercept
        calibrated[outcome] = 1 / (1 + math.exp(-clamp(z, -30, 30)))
    total = sum(calibrated.values()) or 1
    return {outcome: calibrated[outcome] / total for outcome in OUTCOME_ORDER}


def clamp_probability(value: float) -> float:
    return max(0.000001, min(0.999999, value))


def probability_logit(value: float) -> float:
    value = clamp_probability(value)
    return math.log(value / (1 - value))


def model_runtime_status(symbol: str = "SPY") -> dict[str, Any]:
    libraries = {
        "lightgbm": importlib.util.find_spec("lightgbm") is not None,
        "xgboost": importlib.util.find_spec("xgboost") is not None,
        "catboost": importlib.util.find_spec("catboost") is not None,
    }
    installed = [name for name, available in libraries.items() if available]
    artifact = load_market_forecast_artifact(symbol)
    if artifact:
        metrics = artifact.get("metrics") or {}
        return {
            "status": "ready",
            "kind": str(artifact.get("modelKind") or "sparse-softmax-baseline"),
            "version": MODEL_VERSION,
            "trainedAt": artifact.get("trainedAt"),
            "trainingRows": artifact.get("trainingRows"),
            "testRows": artifact.get("testRows"),
            "accuracy": metrics.get("accuracy"),
            "auc": metrics.get("auc"),
            "brier": metrics.get("brier"),
            "calibration": (artifact.get("calibration") or {}).get("method"),
            "uncertaintyMembers": 1 + len(((artifact.get("uncertaintyEnsemble") or {}).get("members") or [])),
            "libraryCandidates": libraries,
            "installedLibraries": installed,
            "message": "Trained isolated market forecast artifact is available.",
        }
    return {
        "status": "model_artifact_missing",
        "kind": "fallback-baseline",
        "version": MODEL_VERSION,
        "calibration": None,
        "uncertaintyMembers": 1,
        "libraryCandidates": libraries,
        "installedLibraries": installed,
        "message": "No trained LightGBM/XGBoost/CatBoost artifact is available yet; using isolated heuristic baseline.",
    }


def missing_runtime_inputs(symbol: str = "SPY") -> list[str]:
    missing: list[str] = []
    if not load_market_forecast_artifact(symbol):
        missing.insert(0, "trained LightGBM/XGBoost/CatBoost model artifact")
    if not any(importlib.util.find_spec(name) is not None for name in ("lightgbm", "xgboost", "catboost")):
        missing.append("LightGBM/XGBoost/CatBoost Python package")
    return missing


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    result: list[float] = []
    current = values[0]
    for value in values:
        current = (value * alpha) + (current * (1 - alpha))
        result.append(current)
    return result


def average_true_range(candles: list[dict[str, Any]], period: int) -> float:
    ranges: list[float] = []
    for index, candle in enumerate(candles[-(period + 1):]):
        previous_close = candles[max(0, len(candles) - (period + 1) + index - 1)]["close"] if index else candle["close"]
        ranges.append(max(candle["high"] - candle["low"], abs(candle["high"] - previous_close), abs(candle["low"] - previous_close)))
    return mean(ranges) if ranges else 0


def adx(candles: list[dict[str, Any]], period: int) -> float:
    if len(candles) < period + 2:
        return 0
    dx_values: list[float] = []
    for index in range(len(candles) - period, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        true_range = max(current["high"] - current["low"], abs(current["high"] - previous["close"]), abs(current["low"] - previous["close"]))
        if true_range <= 0:
            continue
        plus_di = 100 * plus_dm / true_range
        minus_di = 100 * minus_dm / true_range
        denominator = plus_di + minus_di
        if denominator > 0:
            dx_values.append(100 * abs(plus_di - minus_di) / denominator)
    return mean(dx_values) if dx_values else 0


def anchored_vwap(candles: list[dict[str, Any]]) -> float:
    total_value = 0.0
    total_volume = 0.0
    for candle in candles:
        typical = (candle["high"] + candle["low"] + candle["close"]) / 3
        total_value += typical * candle["volume"]
        total_volume += candle["volume"]
    return total_value / total_volume if total_volume else candles[-1]["close"]


def vwap_slope(candles: list[dict[str, Any]]) -> float:
    if len(candles) < 2:
        return 0
    series = []
    for index in range(2, len(candles) + 1):
        series.append(anchored_vwap(candles[:index]))
    return slope(series[-6:])


def slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0
    return (values[-1] - values[0]) / (len(values) - 1)


def safe_return(current: float, previous: float) -> float:
    return (current - previous) / previous if previous else 0


def safe_window_return(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 0
    return safe_return(values[-1], values[-1 - window])


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def numeric(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def round_nested(values: dict[str, Any]) -> dict[str, Any]:
    rounded: dict[str, Any] = {}
    for key, value in values.items():
        rounded[key] = round(value, 6) if isinstance(value, float) else value
    return rounded
